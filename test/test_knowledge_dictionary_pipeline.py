"""知识字典流水线测试（设计文档 §15.1/§15.2）：候选抽取与证据校验、
任务租约/心跳/取消/重试/中断、三种来源适配器、生成端到端（fake predict）与
XinJiang 种子幂等迁移。

不依赖 Milvus / 真实模型：predict 与 embed 均可注入。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
import server.models.kb_models  # noqa: F401
import server.models.user_model  # noqa: F401
from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode
from server.models.knowledge_dictionary_models import (  # noqa: F401
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionaryJob,
    KnowledgeDictionarySource,
    KnowledgeDictionaryVersion,
)

from server.services.knowledge_dictionary import (
    extractor,
    jobs as job_service,
    seed_import,
    service as svc,
    source_adapters,
)
from server.services.knowledge_dictionary.errors import (
    Conflict,
    DictionaryError,
    ExtractionFailed,
    InvalidSource,
    PayloadTooLarge,
    UnsupportedMediaType,
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


def _fake_predict(response_candidates):
    def predict(prompt):
        class Resp:
            content = json.dumps(response_candidates, ensure_ascii=False)

        return Resp()

    return predict


def _candidate(name="孔隙度", definition="岩石中孔隙体积占比", unit="%", data_type="number", quote=None):
    return {
        "category": "基础数据",
        "standard_name": name,
        "definition": definition,
        "unit": unit,
        "data_type": data_type,
        "synonyms": [],
        "value_rule": None,
        "evidence": [{"node_id": "node-1", "quote": quote if quote is not None else f"{name}是描述储层的重要参数", "field_path": "definition"}],
        "inferred": [],
    }


_NODES = [
    {"node_id": "node-1", "text": "孔隙度是描述储层的重要参数，单位为%。", "page_no": "1", "sheet_name": None, "cell_range": None, "metadata": {}},
    {"node_id": "node-2", "text": "渗透率单位为mD。", "page_no": "1", "sheet_name": None, "cell_range": None, "metadata": {}},
]


class ExtractorTest(unittest.TestCase):
    def test_valid_candidates_passthrough(self):
        cand = _candidate()
        cand["evidence"][0]["quote"] = "孔隙度是描述储层的重要参数"
        out = extractor.extract_candidates(_NODES, _fake_predict([cand]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["standard_name"], "孔隙度")

    def test_invalid_json_repair(self):
        broken = '```json\n[{"standard_name":"孔隙度","definition":"定义","evidence":[{"node_id":"node-1","quote":"孔隙度是描述储层的重要参数"}]}'
        out = extractor._repair_json(broken)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 1)

    def test_candidate_missing_required_field_rejected(self):
        bad = _candidate()
        bad.pop("definition")
        with self.assertRaises(ExtractionFailed):
            extractor.extract_candidates(_NODES, _fake_predict([bad]))

    def test_candidate_without_evidence_rejected(self):
        bad = _candidate()
        bad["evidence"] = []
        with self.assertRaises(ExtractionFailed):
            extractor.extract_candidates(_NODES, _fake_predict([bad]))

    def test_quote_must_match_node(self):
        cand = _candidate()
        cand["evidence"][0]["quote"] = "这段文字不在文档中"
        out = extractor.extract_candidates(_NODES, _fake_predict([cand]))
        self.assertEqual(len(out), 1)
        evidences, signals = extractor.validate_evidence_for_candidate(out[0], {n["node_id"]: n for n in _NODES})
        self.assertEqual(len(evidences), 0)  # 引文无法匹配 → 无证据
        self.assertFalse(signals["has_definition"])

    def test_quote_normalization_matches(self):
        cand = _candidate()
        cand["evidence"][0]["quote"] = "孔隙度 是描述储层的 重要参数，单位为%"
        out = extractor.extract_candidates(_NODES, _fake_predict([cand]))
        evidences, signals = extractor.validate_evidence_for_candidate(out[0], {n["node_id"]: n for n in _NODES})
        self.assertEqual(len(evidences), 1)
        self.assertTrue(signals["has_definition"])

    def test_inferred_fields_marked(self):
        cand = _candidate()
        cand["evidence"][0]["quote"] = "孔隙度是描述储层的重要参数"
        cand["evidence"][0]["field_path"] = "unit"
        cand["inferred"] = ["unit"]
        out = extractor.extract_candidates(_NODES, _fake_predict([cand]))
        evidences, signals = extractor.validate_evidence_for_candidate(out[0], {n["node_id"]: n for n in _NODES})
        self.assertTrue(any(ev["inferred"] for ev in evidences))
        self.assertTrue(signals["inferred_penalty"])

    def test_seed_names_accepts_set(self):
        # 回归：load_seed_names() 返回 set，直接传入不得报 'set' object is not subscriptable
        cand = _candidate()
        cand["evidence"][0]["quote"] = "孔隙度是描述储层的重要参数"
        out = extractor.extract_candidates(
            _NODES, _fake_predict([cand]), seed_names={"孔隙度", "渗透率"}
        )
        self.assertEqual(len(out), 1)

    def test_unknown_data_type_coerced(self):
        cand = _candidate(data_type="weird-type")
        out = extractor.extract_candidates(_NODES, _fake_predict([cand]))
        self.assertEqual(out[0]["data_type"], "string")


class SourceAdapterTest(unittest.TestCase):
    def test_upload_extension_and_signature_validation(self):
        with self.assertRaises(UnsupportedMediaType):
            source_adapters.save_upload_file("x.exe", b"PK\x03\x04")
        with self.assertRaises(UnsupportedMediaType):
            source_adapters.save_upload_file("x.pdf", b"not a pdf")
        with self.assertRaises(PayloadTooLarge):
            source_adapters.save_upload_file("x.txt", b"a" * (101 * 1024 * 1024))

    def test_upload_save_and_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DICTIONARY_UPLOAD_ROOT"] = tmp
            try:
                meta = source_adapters.save_upload_file("资料.txt", "孔隙度=10%".encode("utf-8"))
                self.assertIn("storage_ref", meta)
                path = source_adapters.resolve_upload_path(meta["storage_ref"])
                self.assertTrue(path.is_file())
                with self.assertRaises((InvalidSource, DictionaryError)):
                    source_adapters.resolve_upload_path("../../etc/passwd")
                with self.assertRaises((InvalidSource, DictionaryError)):
                    source_adapters.resolve_upload_path("C:\\Windows\\win.ini")
            finally:
                os.environ.pop("DICTIONARY_UPLOAD_ROOT", None)

    def test_upload_node_stream_txt_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DICTIONARY_UPLOAD_ROOT"] = tmp
            try:
                meta = source_adapters.save_upload_file("data.csv", "a,b\n1,2\n3,4\n".encode("utf-8"))
                from server.models.knowledge_dictionary_models import KnowledgeDictionarySource

                source = KnowledgeDictionarySource(source_type="upload", storage_ref=meta["storage_ref"], file_name="data.csv")
                nodes = list(source_adapters.iter_upload_nodes(source))
                self.assertGreater(len(nodes), 0)
                self.assertTrue(any("1" in n["text"] for n in nodes))
            finally:
                os.environ.pop("DICTIONARY_UPLOAD_ROOT", None)

    def test_kb_file_snapshot_and_changed_detection(self):
        with _env() as env:
            db = env["db"]
            kb = KnowledgeDatabase(db_id="kb1", name="库1")
            f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
            db.add_all([kb, f])
            db.commit()
            n = KnowledgeNode(file_id="f1", text="孔隙度是描述储层的重要参数", hash="h1")
            db.add(n)
            db.commit()
            snap = source_adapters.snapshot_kb_file(db, "kb1", "f1")
            self.assertEqual(snap["file_name"], "a.pdf")
            self.assertEqual(snap["node_count"], 1)
            # 未变化
            source = KnowledgeDictionarySource(
                source_type="knowledge_base_file",
                knowledge_base_id="kb1",
                file_id="f1",
                content_hash=snap["content_hash"],
                snapshot_metadata=snap["snapshot_metadata"],
            )
            status = source_adapters.current_source_hashes(db, [source])
            self.assertFalse(status["any_changed"])
            # 节点变化后
            n.text = "改了"
            db.commit()
            status = source_adapters.current_source_hashes(db, [source])
            self.assertTrue(status["any_changed"])
            # 空节点文件拒绝
            f2 = KnowledgeFile(file_id="f2", database_id="kb1", filename="b.pdf", path="p", file_type="pdf", status="done")
            db.add(f2)
            db.commit()
            with self.assertRaises(InvalidSource):
                source_adapters.snapshot_kb_file(db, "kb1", "f2")


class JobLifecycleTest(unittest.TestCase):
    def test_claim_lease_heartbeat_cancel_retry_interrupt(self):
        with _env() as env:
            db = env["db"]
            # 直接构造任务
            job = KnowledgeDictionaryJob(job_type="index", status="queued", stage="pending", progress=0.0)
            db.add(job)
            db.commit()
            claimed = job_service.claim_next_job(db, "worker-1", job_types=["index"])
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.status, "running")
            self.assertEqual(claimed.lease_owner, "worker-1")
            # 同类型并发=1：第二个 queued 任务不能被领取
            job2 = KnowledgeDictionaryJob(job_type="index", status="queued")
            db.add(job2)
            db.commit()
            self.assertIsNone(job_service.claim_next_job(db, "worker-2", job_types=["index"]))
            # 心跳续租 + 检查点
            job_service.heartbeat(db, job.id, "worker-1", stage="embedding", progress=50.0, checkpoint={"phase": "x"})
            reloaded = db.query(KnowledgeDictionaryJob).get(job.id)
            self.assertEqual(reloaded.stage, "embedding")
            self.assertGreater(reloaded.lease_expires_at, datetime.now(timezone.utc).replace(tzinfo=None))
            # 取消
            job_service.cancel_job(db, User(1, "admin"), job.id)
            reloaded = db.query(KnowledgeDictionaryJob).get(job.id)
            self.assertEqual(reloaded.status, "cancelling")
            # 模拟取消完成
            reloaded.status = "cancelled"
            reloaded.finished_at = datetime.now(timezone.utc)
            db.commit()
            # 重试
            retried = job_service.retry_job(db, User(1, "admin"), job.id)
            self.assertEqual(retried["status"], "queued")
            # 过期租约 → interrupted
            claimed2 = job_service.claim_next_job(db, "worker-3", job_types=["index"])
            self.assertIsNotNone(claimed2)
            claimed2.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            marked = job_service.mark_expired_leases(db)
            self.assertGreaterEqual(marked, 1)
            reloaded = db.query(KnowledgeDictionaryJob).get(claimed2.id)
            self.assertEqual(reloaded.status, "interrupted")

    def test_user_cannot_view_others_job(self):
        with _env() as env:
            db = env["db"]
            job = KnowledgeDictionaryJob(job_type="index", status="queued", requested_by=1)
            db.add(job)
            db.commit()
            from server.services.knowledge_dictionary.errors import Forbidden

            with self.assertRaises(Forbidden):
                job_service.get_job(db, User(2, "user"), job.id)
            job_service.get_job(db, User(2, "admin"), job.id)


class GenerationPipelineTest(unittest.TestCase):
    def test_generate_end_to_end_with_fake_predict(self):
        with _env() as env:
            db = env["db"]
            kb = KnowledgeDatabase(db_id="kb1", name="库1")
            f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
            db.add_all([kb, f])
            db.commit()
            for i, text in enumerate(["孔隙度是描述储层的重要参数，单位为%。", "渗透率单位为mD。"]):
                db.add(KnowledgeNode(file_id="f1", text=text, hash=f"h{i}"))
            db.commit()

            spec = {
                "name": "压裂字典",
                "description": "测试",
                "domain": "石油工程",
                "model_id": None,
                "categories": ["基础数据"],
                "use_seed": False,
                "duplicate_policy": "merge",
                "source": {"kind": "kb_file", "db_id": "kb1", "file_id": "f1"},
            }
            created = job_service.create_generate_job(db, User(1, "admin"), spec)
            job = db.query(KnowledgeDictionaryJob).get(created["id"])
            self.assertEqual(job.status, "queued")
            self.assertEqual(job.version_id, created["version_id"])

            # 同一字典存在草稿版本时重复创建任务 → 冲突
            with self.assertRaises(Conflict):
                job_service.create_generate_job(
                    db, User(1, "admin"),
                    {**spec, "name": None, "dictionary_id": created["dictionary_id"]},
                )

            # 来源互斥
            with self.assertRaises(ValidationError):
                job_service.create_generate_job(
                    db, User(1, "admin"),
                    {**spec, "name": "x2", "source": {"kind": "kb_file", "db_id": None, "file_id": None}},
                )

            # 领取并执行（fake predict + 关闭自动索引）
            claimed = job_service.claim_next_job(db, "worker-1", job_types=["generate"])
            self.assertIsNotNone(claimed)

            node_ids = [n.id for n in db.query(KnowledgeNode).order_by(KnowledgeNode.id).all()]

            def candidate(name, definition, unit, dtype, node_id, quote):
                return {
                    "category": "基础数据",
                    "standard_name": name,
                    "definition": definition,
                    "unit": unit,
                    "data_type": dtype,
                    "synonyms": [],
                    "value_rule": None,
                    "evidence": [{"node_id": str(node_id), "quote": quote, "field_path": "definition"}],
                    "inferred": [],
                }

            def predict(prompt):
                class Resp:
                    content = json.dumps(
                        [
                            candidate("孔隙度", "岩石中孔隙体积占比", "%", "number", node_ids[0], "孔隙度是描述储层的重要参数"),
                            candidate("渗透率", "流体渗透能力", "mD", "number", node_ids[1], "渗透率单位为mD"),
                            candidate("孔隙度", "重复候选定义", "%", "number", node_ids[0], "孔隙度是描述储层的重要参数，单位为%"),
                            candidate("渗透率", "冲突定义", "t", "number", node_ids[1], "渗透率单位为mD"),
                        ],
                        ensure_ascii=False,
                    )

                return Resp()

            job_service.run_job(db, claimed, "worker-1", deps={"predict": predict})
            version = db.query(KnowledgeDictionaryVersion).get(created["version_id"])
            self.assertEqual(version.entry_count, 2)  # 孔隙度（合并）+ 渗透率
            conflict = (
                db.query(KnowledgeDictionaryEntry)
                .filter(
                    KnowledgeDictionaryEntry.version_id == version.id,
                    KnowledgeDictionaryEntry.review_status == "conflict",
                )
                .count()
            )
            self.assertEqual(conflict, 1)  # 渗透率 mD 与 t 冲突
            # 证据不重复
            merged = (
                db.query(KnowledgeDictionaryEntry)
                .filter(
                    KnowledgeDictionaryEntry.version_id == version.id,
                    KnowledgeDictionaryEntry.normalized_name == "孔隙度",
                    KnowledgeDictionaryEntry.review_status == "pending",
                )
                .first()
            )
            ev_count = db.query(KnowledgeDictionaryEvidence).filter(KnowledgeDictionaryEvidence.entry_id == merged.id).count()
            self.assertEqual(ev_count, 2)  # 两次候选各一条证据
            job_reloaded = db.query(KnowledgeDictionaryJob).get(claimed.id)
            self.assertEqual(job_reloaded.status, "completed")
            # 无 Milvus 环境：自动索引任务仍应排入（由 vector_index_enabled 控制），此处只验证生成完成
            self.assertGreaterEqual(job_reloaded.progress, 99)

    def test_generation_cancel_at_batch_boundary(self):
        with _env() as env:
            db = env["db"]
            kb = KnowledgeDatabase(db_id="kb1", name="库1")
            f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
            db.add_all([kb, f])
            db.commit()
            db.add(KnowledgeNode(file_id="f1", text="孔隙度是描述储层的重要参数，单位为%。", hash="h1"))
            db.commit()
            spec = {
                "name": "压裂字典",
                "use_seed": False,
                "source": {"kind": "kb_file", "db_id": "kb1", "file_id": "f1"},
            }
            created = job_service.create_generate_job(db, User(1, "admin"), spec)
            claimed = job_service.claim_next_job(db, "worker-1", job_types=["generate"])
            # 领取后请求取消 → worker 在批次边界看到 cancelling
            job_service.cancel_job(db, User(1, "admin"), claimed.id)

            def predict(prompt):
                raise AssertionError("取消后不应再调用模型")

            job_service.run_job(db, claimed, "worker-1", deps={"predict": predict})
            reloaded = db.query(KnowledgeDictionaryJob).get(claimed.id)
            self.assertEqual(reloaded.status, "cancelled")

    def test_batch_failure_ratio_fails_job(self):
        with _env() as env:
            db = env["db"]
            kb = KnowledgeDatabase(db_id="kb1", name="库1")
            f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
            db.add_all([kb, f])
            db.commit()
            for i in range(3):
                db.add(KnowledgeNode(file_id="f1", text=f"第{i}个节点文本。", hash=f"h{i}"))
            db.commit()
            spec = {"name": "压裂字典", "use_seed": False, "source": {"kind": "kb_file", "db_id": "kb1", "file_id": "f1"}}
            created = job_service.create_generate_job(db, User(1, "admin"), spec)
            claimed = job_service.claim_next_job(db, "worker-1", job_types=["generate"])

            def predict(prompt):
                raise RuntimeError("模型超时")

            job_service.run_job(db, claimed, "worker-1", deps={"predict": predict})
            reloaded = db.query(KnowledgeDictionaryJob).get(claimed.id)
            self.assertEqual(reloaded.status, "failed")
            self.assertIn("RuntimeError", reloaded.error_summary or "")
            self.assertNotIn("模型超时" * 3, reloaded.error_summary or "")


class PublishSourceSnapshotTest(unittest.TestCase):
    """回归：创建任务与发布校验的快照哈希算法必须一致（曾出现两边结构不同导致发布永远被阻止）。"""

    def _prepare(self, env, db, name="压裂字典"):
        from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode
        from server.models.knowledge_dictionary_models import KnowledgeDictionaryJob

        kb = KnowledgeDatabase(db_id="kb1", name="库1")
        f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
        db.add_all([kb, f])
        db.commit()
        db.add(KnowledgeNode(file_id="f1", text="孔隙度是描述储层的重要参数，单位为%。", hash="h1"))
        db.commit()
        spec = {"name": name, "use_seed": False, "source": {"kind": "kb_file", "db_id": "kb1", "file_id": "f1"}}
        created = job_service.create_generate_job(db, User(1, "admin"), spec)
        version = db.query(KnowledgeDictionaryVersion).get(created["version_id"])
        # 生成任务完成（避免门禁 7）
        job = db.query(KnowledgeDictionaryJob).get(created["id"])
        job.status = "completed"
        db.commit()
        return created, version

    def _make_publishable(self, db, dictionary_id, version):
        from server.models.knowledge_dictionary_models import KnowledgeDictionaryEntry

        entry = svc.create_entry(
            db, User(1, "admin"), dictionary_id, version.id,
            {"standard_name": "孔隙度", "definition": "岩石中孔隙体积占比", "unit": "%",
             "data_type": "number", "evidences": [{"quote": "孔隙度", "field_path": "standard_name"}]},
        )
        svc.review_entry(db, User(1, "admin"), dictionary_id, version.id, entry["id"], {"action": "approve"})
        version = db.query(KnowledgeDictionaryVersion).get(version.id)
        version.index_status = "ready"
        version.vector_count = 1
        db.commit()
        return version

    def test_publish_ok_when_snapshot_unchanged(self):
        with _env() as env:
            db = env["db"]
            created, version = self._prepare(env, db)
            version = self._make_publishable(db, created["dictionary_id"], version)
            published = svc.publish_version(db, User(1, "admin"), created["dictionary_id"], version.id)
            self.assertEqual(published["status"], "published")

    def test_publish_blocked_when_source_changed(self):
        with _env() as env:
            db = env["db"]
            created, version = self._prepare(env, db, name="字典B")
            version = self._make_publishable(db, created["dictionary_id"], version)
            from server.models.kb_models import KnowledgeNode

            node = db.query(KnowledgeNode).first()
            node.text = "内容已改变"
            db.commit()
            with self.assertRaises(Exception) as ctx:
                svc.publish_version(db, User(1, "admin"), created["dictionary_id"], version.id)
            self.assertEqual(ctx.exception.error_code, "DICTIONARY_PUBLISH_BLOCKED")
            self.assertIn("来源文件已变化", ctx.exception.message)


class SeedImportTest(unittest.TestCase):
    def test_parse_seed_xlsx(self):
        columns = seed_import.parse_seed_xlsx()
        self.assertGreater(len(columns), 50)
        names = {c["name"] for c in columns}
        self.assertIn("井号", names)
        self.assertIn("孔隙度", names)

    def test_seed_import_idempotent(self):
        with _env() as env:
            db = env["db"]
            first = seed_import.import_seed_sync(db, User(1, "admin"))
            self.assertTrue(first["created"])
            self.assertGreater(first["entry_count"], 50)
            dictionary = db.query(KnowledgeDictionary).get(first["dictionary_id"])
            self.assertEqual(dictionary.name, "压裂知识字典 V1")
            version = db.query(KnowledgeDictionaryVersion).get(first["version_id"])
            self.assertEqual(version.entry_count, first["entry_count"])
            # 相同哈希再次执行：不创建新字典/条目
            second = seed_import.import_seed_sync(db, User(1, "admin"))
            self.assertFalse(second["created"])
            self.assertEqual(second["dictionary_id"], first["dictionary_id"])
            total = db.query(KnowledgeDictionaryEntry).filter(KnowledgeDictionaryEntry.version_id == version.id).count()
            self.assertEqual(total, first["entry_count"])
            # 每个条目都有证据
            no_evidence = 0
            for e in db.query(KnowledgeDictionaryEntry).filter(KnowledgeDictionaryEntry.version_id == version.id).all():
                if db.query(KnowledgeDictionaryEvidence).filter(KnowledgeDictionaryEvidence.entry_id == e.id).count() == 0:
                    no_evidence += 1
            self.assertEqual(no_evidence, 0)

    def test_seed_names_loaded(self):
        names = seed_import.load_seed_names()
        self.assertGreater(len(names), 50)
        self.assertIn("孔隙度", names)

    def test_seed_import_requires_manager(self):
        with _env() as env:
            db = env["db"]
            from server.services.knowledge_dictionary.errors import Forbidden

            with self.assertRaises(Forbidden):
                seed_import.import_seed_sync(db, User(2, "user"))


if __name__ == "__main__":
    unittest.main()
