"""知识字典核心测试（设计文档 §15.1）：标准化、权限矩阵、字典/版本生命周期、
条目审核、合并、发布门禁与导出。

仅依赖临时 SQLite 与纯函数，不加载 src / Milvus / 模型。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
import server.models.kb_models  # noqa: F401  # 注册 knowledge_* 表
import server.models.user_model  # noqa: F401
from server.models.knowledge_dictionary_models import (  # noqa: F401
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionaryJob,
    KnowledgeDictionarySource,
    KnowledgeDictionaryVersion,
)

from server.services.knowledge_dictionary import (
    export_service,
    normalizer,
    permissions,
    service as svc,
)
from server.services.knowledge_dictionary.errors import (
    Conflict,
    DictionaryError,
    Forbidden,
    NotFound,
    PublishBlocked,
    ValidationError,
)


class User:
    def __init__(self, user_id=1, role="admin"):
        self.id = user_id
        self.role = role
        self.username = f"u{user_id}"


@contextmanager
def _env():
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'server.db'}")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            yield {"db": db, "root": Path(tmp)}
        finally:
            db.close()
            engine.dispose()


def _make_dictionary(db, name="测试字典", role="admin", user_id=1):
    return svc.create_dictionary(db, User(user_id, role), name=name, domain="石油工程")


def _draft_version(db, dictionary_id):
    return (
        db.query(KnowledgeDictionaryVersion)
        .filter(
            KnowledgeDictionaryVersion.dictionary_id == dictionary_id,
            KnowledgeDictionaryVersion.status.in_(["draft", "reviewing"]),
        )
        .first()
    )


def _add_entry(db, version_id, dictionary_id=1, name="孔隙度", definition="岩石中孔隙体积占比", unit="%", data_type="number", **extra):
    payload = {
        "standard_name": name,
        "definition": definition,
        "unit": unit,
        "data_type": data_type,
        "evidences": [{"quote": name, "field_path": "standard_name"}],
    }
    payload.update(extra)
    return svc.create_entry(db, User(1, "admin"), dictionary_id, version_id, payload)


class NormalizerTest(unittest.TestCase):
    def test_normalize_name_full_width_and_punct(self):
        # 全角转半角 + NFKC + 尾部标点剥离
        self.assertEqual(normalizer.normalize_name("  孔隙度（%）  "), "孔隙度(%")
        self.assertEqual(normalizer.normalize_name("Ａ测深(m)"), "a测深(m")
        self.assertEqual(normalizer.normalize_name("大地坐标X："), "大地坐标x")

    def test_normalize_unit_aliases(self):
        self.assertEqual(normalizer.normalize_unit("(m³)"), "m3")
        self.assertEqual(normalizer.normalize_unit("MPa"), "mpa")
        self.assertEqual(normalizer.normalize_unit("（%）"), "pct")
        self.assertEqual(normalizer.normalize_unit("吨/天"), "t/d")
        self.assertEqual(normalizer.normalize_unit(""), "")

    def test_data_type_mapping(self):
        self.assertEqual(normalizer.map_data_type("float"), "number")
        self.assertEqual(normalizer.map_data_type("整数"), "integer")
        self.assertEqual(normalizer.map_data_type("unknown-type"), "string")

    def test_dedupe_key_and_content_hash(self):
        a = {"standard_name": "孔隙度", "data_type": "number", "unit": "%"}
        b = {"standard_name": "孔隙度", "data_type": "number", "unit": "（%）"}
        self.assertEqual(normalizer.dedupe_key(a), normalizer.dedupe_key(b))
        h1 = normalizer.content_hash(a)
        self.assertEqual(len(h1), 64)
        self.assertEqual(h1, normalizer.content_hash(a))

    def test_confidence_bands(self):
        self.assertGreaterEqual(normalizer.compute_confidence({"has_definition": True, "explicit_unit": True, "explicit_type": True, "seed_hit": True, "multi_source": True, "complete": True}), 0.85)
        self.assertEqual(normalizer.confidence_band(0.9), "high")
        self.assertEqual(normalizer.confidence_band(0.7), "review")
        self.assertEqual(normalizer.confidence_band(0.3), "low")

    def test_units_compatible(self):
        self.assertTrue(normalizer.are_units_compatible("m³", "m3"))
        self.assertTrue(normalizer.are_units_compatible("", "m"))
        self.assertFalse(normalizer.are_units_compatible("m", "t"))


class PermissionTest(unittest.TestCase):
    def test_roles(self):
        self.assertTrue(permissions.is_manager(User(role="admin")))
        self.assertTrue(permissions.is_manager(User(role="superadmin")))
        self.assertFalse(permissions.is_manager(User(role="user")))
        with self.assertRaises(Forbidden):
            permissions.ensure_manager(User(role="user"))

    def test_read_version_rules(self):
        with self.assertRaises(Forbidden):
            permissions.ensure_can_read_version(User(role="user"), "draft", "draft", False)
        with self.assertRaises(Forbidden):
            permissions.ensure_can_read_version(User(role="user"), "published", "published", False)
        # 普通用户可读已发布活动版本
        permissions.ensure_can_read_version(User(role="user"), "published", "published", True)
        # 管理员可读任何状态
        permissions.ensure_can_read_version(User(role="admin"), "draft", "draft", False)


class DictionaryLifecycleTest(unittest.TestCase):
    def test_create_and_name_uniqueness(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            self.assertEqual(data["status"], "draft")
            with self.assertRaises(Conflict):
                svc.create_dictionary(db, User(1, "admin"), name="测试字典")
            # 普通用户禁止创建
            with self.assertRaises(Forbidden):
                svc.create_dictionary(db, User(2, "user"), name="用户字典")

    def test_user_cannot_see_draft(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            with self.assertRaises(Forbidden):
                svc.get_dictionary_detail(db, User(2, "user"), data["id"])
            svc.get_dictionary_detail(db, User(2, "admin"), data["id"])

    def test_delete_requires_withdraw(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            # 草稿可直接软删除
            svc.delete_dictionary(db, User(1, "admin"), data["id"])
            deleted = db.query(KnowledgeDictionary).filter(KnowledgeDictionary.id == data["id"]).first()
            self.assertEqual(deleted.is_deleted, 1)
            # 已发布（活动版本）必须先撤回
            data2 = _make_dictionary(db, name="字典2")
            v = _draft_version(db, data2["id"])
            _add_entry(db, v.id, dictionary_id=data2["id"])
            d = db.query(KnowledgeDictionary).get(data2["id"])
            d.status = "published"
            d.active_version_id = v.id
            v.status = "published"
            db.commit()
            with self.assertRaises(Conflict):
                svc.delete_dictionary(db, User(1, "admin"), data2["id"])
            # 撤回后可删除
            svc.withdraw_version(db, User(1, "admin"), data2["id"], v.id)
            svc.delete_dictionary(db, User(1, "admin"), data2["id"])


class EntryReviewTest(unittest.TestCase):
    def _published_setup(self):
        env = _env()
        ctx = env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)
        db = ctx["db"]
        data = _make_dictionary(db)
        v = _draft_version(db, data["id"])
        return db, data, v

    def test_entry_crud_draft_only(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            entry = _add_entry(db, v.id)
            self.assertEqual(entry["review_status"], "pending")
            self.assertEqual(entry["normalized_name"], "孔隙度")
            # 编辑
            updated = svc.update_entry(db, User(1, "admin"), data["id"], v.id, entry["id"], {"definition": "更新后的定义"})
            self.assertEqual(updated["definition"], "更新后的定义")
            # 删除
            svc.delete_entry(db, User(1, "admin"), data["id"], v.id, entry["id"])
            out = svc.list_entries(db, User(1, "admin"), data["id"], v.id, page=1, page_size=10)
            self.assertEqual(out["total"], 0)

    def test_review_requires_evidence(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            # 无证据条目不能通过
            entry = svc.create_entry(
                db, User(1, "admin"), data["id"], v.id,
                {"standard_name": "无证据", "definition": "定义"},
            )
            with self.assertRaises(ValidationError):
                svc.review_entry(db, User(1, "admin"), data["id"], v.id, entry["id"], {"action": "approve"})
            # 有证据可通过
            entry2 = _add_entry(db, v.id, name="有证据")
            svc.review_entry(db, User(1, "admin"), data["id"], v.id, entry2["id"], {"action": "approve"})
            reloaded = db.query(KnowledgeDictionaryEntry).get(entry2["id"])
            self.assertEqual(reloaded.review_status, "approved")

    def test_batch_review_low_confidence_gate(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            low = _add_entry(db, v.id, name="低置信", confidence=0.3)
            result = svc.batch_review(
                db, User(1, "admin"), data["id"], v.id,
                items=[{"entry_id": low["id"], "action": "approve"}],
            )
            self.assertFalse(result["results"][0]["ok"])
            self.assertIn("低置信", result["results"][0]["reason"])
            # 显式允许后可过
            result2 = svc.batch_review(
                db, User(1, "admin"), data["id"], v.id,
                items=[{"entry_id": low["id"], "action": "approve"}],
                allow_low_confidence=True,
            )
            self.assertTrue(result2["results"][0]["ok"])

    def test_batch_review_concurrency_token(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            entry = _add_entry(db, v.id)
            token = v.updated_at.isoformat() if v.updated_at else v.created_at.isoformat()
            # 用错 token → 409
            with self.assertRaises(Conflict):
                svc.batch_review(
                    db, User(1, "admin"), data["id"], v.id,
                    items=[{"entry_id": entry["id"], "action": "reject"}],
                    concurrency_token="WRONG",
                )
            svc.batch_review(
                db, User(1, "admin"), data["id"], v.id,
                items=[{"entry_id": entry["id"], "action": "reject"}],
                concurrency_token=token,
            )
            reloaded = db.query(KnowledgeDictionaryEntry).get(entry["id"])
            self.assertEqual(reloaded.review_status, "rejected")

    def test_version_advances_to_reviewing_on_first_approval(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            self.assertEqual(v.status, "draft")
            entry = _add_entry(db, v.id)
            svc.review_entry(db, User(1, "admin"), data["id"], v.id, entry["id"], {"action": "approve"})
            v = _draft_version(db, data["id"])
            self.assertEqual(v.status, "reviewing")

    def test_merge_conflict_and_success(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            a = _add_entry(db, v.id, name="孔隙度", unit="%")
            b = _add_entry(db, v.id, name="孔隙度", unit="m")  # 单位冲突
            with self.assertRaises(Conflict):
                svc.merge_entries(db, User(1, "admin"), data["id"], v.id, keep_entry_id=a["id"], merge_entry_ids=[b["id"]])
            c = _add_entry(db, v.id, name="孔隙度", unit="%", definition="另一来源的定义")
            merged = svc.merge_entries(db, User(1, "admin"), data["id"], v.id, keep_entry_id=a["id"], merge_entry_ids=[c["id"]])
            self.assertIsNotNone(merged["merged_from"])
            evidences = svc.get_entry_evidences(db, User(1, "admin"), data["id"], v.id, a["id"])
            self.assertEqual(evidences["total"], 2)  # 证据转移

    def test_published_version_immutable(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            v.status = "published"
            db.commit()
            with self.assertRaises(Conflict):
                svc.create_entry(db, User(1, "admin"), data["id"], v.id, {"standard_name": "x", "definition": "y"})


class PublishGateTest(unittest.TestCase):
    def _setup(self):
        ctx = _env()
        env = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        db = env["db"]
        data = _make_dictionary(db)
        v = _draft_version(db, data["id"])
        return db, data, v

    def test_publish_blocked_by_pending_entries(self):
        db, data, v = self._setup()
        _add_entry(db, v.id)
        with self.assertRaises(PublishBlocked) as ctx:
            svc.publish_version(db, User(1, "admin"), data["id"], v.id)
        self.assertIn("待审核", str(ctx.exception))

    def test_publish_blocked_by_index_status(self):
        db, data, v = self._setup()
        entry = _add_entry(db, v.id)
        svc.review_entry(db, User(1, "admin"), data["id"], v.id, entry["id"], {"action": "approve"})
        with self.assertRaises(PublishBlocked):
            svc.publish_version(db, User(1, "admin"), data["id"], v.id)

    def test_publish_success_and_withdraw(self):
        db, data, v = self._setup()
        entry = _add_entry(db, v.id)
        svc.review_entry(db, User(1, "admin"), data["id"], v.id, entry["id"], {"action": "approve"})
        v.status = "reviewing"
        v.index_status = "ready"
        v.vector_count = 1
        v.embedding_config_hash = None  # 未配置哈希时不校验
        db.commit()
        # 索引任务不阻塞：模拟无任务状态
        published = svc.publish_version(db, User(1, "admin"), data["id"], v.id)
        self.assertEqual(published["status"], "published")
        d = db.query(KnowledgeDictionary).get(data["id"])
        self.assertEqual(d.status, "published")
        self.assertEqual(d.active_version_id, v.id)
        # 幂等发布
        again = svc.publish_version(db, User(1, "admin"), data["id"], v.id)
        self.assertEqual(again["status"], "published")
        # 撤回
        withdrawn = svc.withdraw_version(db, User(1, "admin"), data["id"], v.id)
        self.assertEqual(withdrawn["status"], "withdrawn")
        d = db.query(KnowledgeDictionary).get(data["id"])
        self.assertIsNone(d.active_version_id)
        # 普通用户不能撤回
        with self.assertRaises(Forbidden):
            svc.withdraw_version(db, User(2, "user"), data["id"], v.id)

    def test_publish_idempotent_requires_admin(self):
        db, data, v = self._setup()
        with self.assertRaises(Forbidden):
            svc.publish_version(db, User(2, "user"), data["id"], v.id)


class ExportTest(unittest.TestCase):
    def test_csv_formula_injection_protection(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            _add_entry(db, v.id, definition="=1+1")
            _add_entry(db, v.id, name="@SUM", definition="普通定义")
            content, media_type, filename = export_service.export_version(db, User(1, "admin"), data["id"], v.id, fmt="csv")
            text = content.decode("utf-8-sig")
            self.assertIn("'=1+1", text)
            self.assertIn("'@SUM", text)
            self.assertEqual(media_type, "text/csv")
            self.assertIn("测试字典-V1", filename)

    def test_xlsx_three_sheets(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            _add_entry(db, v.id)
            content, media_type, _ = export_service.export_version(db, User(1, "admin"), data["id"], v.id, fmt="xlsx")
            import io

            import openpyxl

            wb = openpyxl.load_workbook(io.BytesIO(content))
            self.assertEqual(wb.sheetnames, ["字典条目", "来源证据", "版本信息"])

    def test_export_requires_manager(self):
        with _env() as env:
            db = env["db"]
            data = _make_dictionary(db)
            v = _draft_version(db, data["id"])
            with self.assertRaises(Forbidden):
                export_service.export_version(db, User(2, "user"), data["id"], v.id, fmt="csv")


if __name__ == "__main__":
    unittest.main()
