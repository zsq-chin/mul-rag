"""系统配置历史、白名单、脱敏与回滚验收测试。

服务层 + 临时 SQLite（不依赖 Milvus/docker），config 用鸭子类型替身；
路由层只做源码断言（base_router 导入 src 会触发 Milvus，禁止导入）。

验收对照计划阶段 8：
- 未知键/秘密键/空请求返回 400（ConfigError）。
- 历史与回滚记录中绝不出现 API Key / 密码 / Token。
- 回滚只回滚该次变更涉及的非秘密字段，写新历史，不删旧历史，可连续回滚。
- 修改/回滚返回 restart_components，不自动重启。
"""

import importlib.util
import json
import logging
import re
import tempfile
import threading
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.models.operations_model import ConfigChangeHistory
from server.services import config_service
from server.services.config_service import (
    ConfigError,
    ConfigChangeNotFound,
    MUTABLE_CONFIG_KEYS,
    sanitize_config_snapshot,
)


class FakeConfig(dict):
    """可序列化的配置替身，行为对齐 Config.dump_config()/save()。"""

    def dump_config(self):
        return dict(self)

    def save(self):
        pass


class FileFakeConfig(FakeConfig):
    """带真实临时文件落盘的配置替身（用于原子性/失败恢复测试）。"""

    def __init__(self, filename):
        super().__init__()
        self.filename = filename

    def save(self):
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, ensure_ascii=False, indent=2)


class _FailSaveConfig(FakeConfig):
    """save() 抛异常，模拟写盘失败。"""

    def save(self):
        raise OSError("simulated disk full")


class _CommitFailSession:
    """对真实 session 的薄包装：commit 时抛异常，模拟历史写入失败。"""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self):
        raise RuntimeError("simulated commit failure")


def _seed_config():
    cfg = FakeConfig()
    cfg["enable_reranker"] = False
    cfg["enable_knowledge_base"] = False
    cfg["model_name"] = "default-model"
    cfg["use_rewrite_query"] = "off"
    cfg["graph_hops"] = 2
    cfg["custom_models"] = [
        {"custom_id": "m1", "name": "model one", "api_key": "sk-secret-old", "api_base": "http://a"}
    ]
    cfg["_config_items"] = {"model_name": {"default": "default-model", "choices": []}}
    cfg["model_names"] = {"provider": {"models": [], "env": []}}
    return cfg


@contextmanager
def _temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(
            f"sqlite:///{Path(tmp) / 'test.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


class ConfigSanitizerTests(unittest.TestCase):
    def test_redacts_top_level_secret_keys(self):
        out = sanitize_config_snapshot({"api_key": "sk-x", "password": "p", "model_name": "m"})
        self.assertEqual(out["api_key"], "***")
        self.assertEqual(out["password"], "***")
        self.assertEqual(out["model_name"], "m")

    def test_redacts_custom_models_api_key_in_history_mode(self):
        cfg = _seed_config()
        out = sanitize_config_snapshot(cfg.dump_config(), drop_internal=True)
        self.assertNotIn("_config_items", out)
        self.assertNotIn("model_names", out)
        cm = out["custom_models"]
        self.assertEqual(cm[0]["api_key"], "***")
        self.assertEqual(cm[0]["api_base"], "http://a")

    def test_snapshot_never_leaks_real_api_key(self):
        """P1-1：任何配置响应都不返回真实 API Key，只给占位 + has_api_key/key_hint。"""
        cfg = _seed_config()
        out = sanitize_config_snapshot(cfg.dump_config())
        m = out["custom_models"][0]
        self.assertEqual(m["api_key"], "***")
        self.assertTrue(m["has_api_key"])
        self.assertEqual(m["key_hint"], "-old")  # sk-secret-old 末尾 4 位提示
        self.assertNotIn("sk-secret-old", json.dumps(out, ensure_ascii=False))

    def test_snapshot_default_and_drop_internal_both_redact(self):
        cfg = _seed_config()
        for kwargs in ({}, {"drop_internal": True}):
            out = sanitize_config_snapshot(cfg.dump_config(), **kwargs)
            self.assertNotIn("sk-secret-old", json.dumps(out, ensure_ascii=False))
            self.assertNotEqual(out["custom_models"][0]["api_key"], "sk-secret-old")

    def test_non_dict_returns_empty(self):
        self.assertEqual(sanitize_config_snapshot(None), {})


class ConfigApplyTests(unittest.TestCase):
    def test_unknown_key_rejected(self):
        with _temp_db() as db:
            cfg = _seed_config()
            with self.assertRaises(ConfigError) as ctx:
                config_service.apply_update(db, cfg, {"not_a_config_key": 1}, operator="admin")
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("白名单", str(ctx.exception))
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)

    def test_secret_key_rejected(self):
        with _temp_db() as db:
            cfg = _seed_config()
            for bad in ("password", "api_key", "smtp_password", "model_provider_token"):
                with self.assertRaises(ConfigError):
                    config_service.apply_update(db, cfg, {bad: "x"}, operator="admin")
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)

    def test_empty_items_rejected(self):
        with _temp_db() as db:
            with self.assertRaises(ConfigError):
                config_service.apply_update(db, _seed_config(), {}, operator="admin")

    def test_whitelist_accepts_mutable_keys(self):
        self.assertIn("enable_reranker", MUTABLE_CONFIG_KEYS)
        self.assertIn("model_name", MUTABLE_CONFIG_KEYS)
        self.assertIn("custom_models", MUTABLE_CONFIG_KEYS)
        self.assertIn("multi_query_max_rounds", MUTABLE_CONFIG_KEYS)
        # 系统自动管理、不可手动配置的键不在白名单
        self.assertNotIn("enable_web_search", MUTABLE_CONFIG_KEYS)
        self.assertNotIn("model_names", MUTABLE_CONFIG_KEYS)

    def test_apply_records_history_and_returns_meta(self):
        with _temp_db() as db:
            cfg = _seed_config()
            result = config_service.apply_update(
                db, cfg,
                {"enable_reranker": True, "graph_hops": 3},
                operator="sysadmin", description="tune rerank",
            )
            self.assertIn("change_id", result)
            self.assertIn("restart_components", result)
            self.assertEqual(result["changed_keys"], ["enable_reranker", "graph_hops"])
            self.assertIn("retriever", result["restart_components"])
            self.assertEqual(cfg["enable_reranker"], True)
            self.assertEqual(cfg["graph_hops"], 3)

            row = db.query(ConfigChangeHistory).first()
            self.assertEqual(row.operator, "sysadmin")
            self.assertEqual(row.description, "tune rerank")
            changes = json.loads(row.changes)
            by_key = {c["key"]: c for c in changes}
            self.assertEqual(by_key["enable_reranker"]["old"], False)
            self.assertEqual(by_key["enable_reranker"]["new"], True)
            before = json.loads(row.before_snapshot)
            after = json.loads(row.after_snapshot)
            self.assertEqual(before["enable_reranker"], False)
            self.assertEqual(after["enable_reranker"], True)
            self.assertNotIn("_config_items", before)
            self.assertNotIn("model_names", after)

    def test_history_never_contains_api_keys(self):
        with _temp_db() as db:
            cfg = _seed_config()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_key": "sk-secret-new", "api_base": "http://b"}
                ]},
                operator="sysadmin",
            )
            row = db.query(ConfigChangeHistory).first()
            blob = " ".join(filter(None, [row.changes, row.before_snapshot, row.after_snapshot]))
            self.assertNotIn("sk-secret-old", blob)
            self.assertNotIn("sk-secret-new", blob)
            self.assertIn("***", blob)


class CustomModelsMergeTests(unittest.TestCase):
    """P1-1：编辑旧模型未填 Key 时保留原密钥，绝不覆盖为占位值。"""

    def _seed(self):
        cfg = _seed_config()
        cfg["custom_models"] = [
            {"custom_id": "m1", "name": "model one", "api_key": "sk-secret-old", "api_base": "http://a"},
            {"custom_id": "m2", "name": "model two", "api_key": "sk-secret-new", "api_base": "http://c"},
        ]
        return cfg

    def test_blank_key_preserves_real_key(self):
        with _temp_db() as db:
            cfg = self._seed()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_key": "", "api_base": "http://b"},
                    {"custom_id": "m2", "name": "model two", "api_key": "sk-replaced", "api_base": "http://c"},
                ]},
                operator="admin",
            )
            by_id = {m["custom_id"]: m for m in cfg["custom_models"]}
            self.assertEqual(by_id["m1"]["api_key"], "sk-secret-old")  # 留空 → 保留
            self.assertEqual(by_id["m2"]["api_key"], "sk-replaced")    # 新填 → 替换
            self.assertNotIn("sk-secret-new", json.dumps(cfg.dump_config()))

    def test_placeholder_key_preserves_real_key(self):
        with _temp_db() as db:
            cfg = self._seed()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_key": "***", "api_base": "http://b"},
                ]},
                operator="admin",
            )
            self.assertEqual(cfg["custom_models"][0]["api_key"], "sk-secret-old")

    def test_new_custom_model_without_key_has_no_api_key(self):
        with _temp_db() as db:
            cfg = self._seed()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_key": "", "api_base": "http://a"},
                    {"custom_id": "m3", "name": "model three", "api_base": "http://d"},
                ]},
                operator="admin",
            )
            by_id = {m["custom_id"]: m for m in cfg["custom_models"]}
            self.assertEqual(by_id["m3"].get("api_key"), None)


class ConfigValueValidationTests(unittest.TestCase):
    """P2-1：类型与范围校验，整批拒绝，不写历史。"""

    def test_wrong_type_rejected(self):
        with _temp_db() as db:
            cfg = _seed_config()
            for bad in ({"graph_hops": "abc"}, {"enable_reranker": "not-a-bool"}):
                with self.assertRaises(ConfigError) as ctx:
                    config_service.apply_update(db, cfg, bad, operator="admin")
                self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)
            self.assertEqual(cfg["graph_hops"], 2)
            self.assertEqual(cfg["enable_reranker"], False)

    def test_out_of_range_rejected(self):
        with _temp_db() as db:
            cfg = _seed_config()
            for bad in ({"graph_hops": 100}, {"multi_query_count": 0}, {"graph_similarity_threshold": 1.5}):
                with self.assertRaises(ConfigError) as ctx:
                    config_service.apply_update(db, cfg, bad, operator="admin")
                self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)

    def test_numeric_string_coerced(self):
        with _temp_db() as db:
            cfg = _seed_config()
            result = config_service.apply_update(db, cfg, {"graph_hops": "3"}, operator="admin")
            self.assertEqual(cfg["graph_hops"], 3)
            self.assertIn("graph_hops", result["changed_keys"])

    def test_bool_variants_accepted(self):
        with _temp_db() as db:
            cfg = _seed_config()
            config_service.apply_update(db, cfg, {"enable_reranker": "true"}, operator="admin")
            self.assertIs(cfg["enable_reranker"], True)

    def test_str_choice_validation(self):
        with _temp_db() as db:
            cfg = _seed_config()
            with self.assertRaises(ConfigError):
                config_service.apply_update(db, cfg, {"use_rewrite_query": "bogus"}, operator="admin")
            config_service.apply_update(db, cfg, {"use_rewrite_query": "hyde"}, operator="admin")
            self.assertEqual(cfg["use_rewrite_query"], "hyde")


class ConfigAtomicityTests(unittest.TestCase):
    """P2-1：提交失败 / 写盘失败时恢复原配置与文件。"""

    def test_commit_failure_restores_config_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "base.json"
            cfg = FileFakeConfig(str(cfg_file))
            cfg.update(_seed_config())
            cfg.save()

            with _temp_db() as db:
                failing = _CommitFailSession(db)
                with self.assertRaises(ConfigError) as ctx:
                    config_service.apply_update(
                        failing, cfg,
                        {"enable_reranker": True, "graph_hops": 3},
                        operator="admin",
                    )
                self.assertEqual(ctx.exception.status_code, 400)
            # 内存与文件都被还原
            self.assertEqual(cfg["enable_reranker"], False)
            self.assertEqual(cfg["graph_hops"], 2)
            on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["enable_reranker"], False)
            self.assertEqual(on_disk["graph_hops"], 2)
            # 没有留下历史记录
            with _temp_db() as db2:
                self.assertEqual(db2.query(ConfigChangeHistory).count(), 0)

    def test_save_failure_mid_batch_restores_all_fields(self):
        with _temp_db() as db:
            cfg = _FailSaveConfig()
            cfg.update(_seed_config())
            with self.assertRaises(ConfigError):
                config_service.apply_update(
                    db, cfg,
                    {"enable_reranker": True, "graph_hops": 3, "model_name": "n"},
                    operator="admin",
                )
            # 前面已改字段一并恢复
            self.assertEqual(cfg["enable_reranker"], False)
            self.assertEqual(cfg["graph_hops"], 2)
            self.assertEqual(cfg["model_name"], "default-model")
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)


class ConfigHistoryTests(unittest.TestCase):
    def test_list_pagination_and_operator_filter(self):
        with _temp_db() as db:
            cfg = _seed_config()
            for i in range(5):
                config_service.apply_update(db, cfg, {"enable_reranker": bool(i % 2)},
                                            operator="admin" if i % 2 == 0 else "tester")
            data = config_service.list_history(db, page=1, page_size=2)
            self.assertEqual(data["total"], 5)
            self.assertEqual(len(data["items"]), 2)
            data = config_service.list_history(db, operator="tester", page=1, page_size=20)
            self.assertEqual(data["total"], 2)
            # 默认倒序：最新在最前
            self.assertGreater(data["items"][0]["id"], data["items"][1]["id"])

    def test_get_history(self):
        with _temp_db() as db:
            cfg = _seed_config()
            r = config_service.apply_update(db, cfg, {"model_name": "new"}, operator="admin")
            change = config_service.get_history(db, r["change_id"])
            self.assertIsNotNone(change)
            self.assertEqual(change["operator"], "admin")
            self.assertEqual(change["changes"][0]["key"], "model_name")
            self.assertIsNone(config_service.get_history(db, 99999))


class ConfigRollbackTests(unittest.TestCase):
    def test_rollback_restores_and_writes_new_record(self):
        with _temp_db() as db:
            cfg = _seed_config()
            r1 = config_service.apply_update(db, cfg, {"enable_reranker": True}, operator="admin")
            before_total = db.query(ConfigChangeHistory).count()
            rr = config_service.rollback(db, cfg, r1["change_id"], operator="sysadmin")
            self.assertEqual(cfg["enable_reranker"], False)
            self.assertEqual(rr["rolled_back_keys"], ["enable_reranker"])
            # 新历史写入，旧历史保留
            self.assertEqual(db.query(ConfigChangeHistory).count(), before_total + 1)
            new_row = db.query(ConfigChangeHistory).order_by(ConfigChangeHistory.id.desc()).first()
            self.assertIn("回滚", new_row.description)
            self.assertEqual(new_row.operator, "sysadmin")

    def test_rollback_custom_models_preserves_real_api_key(self):
        with _temp_db() as db:
            cfg = _seed_config()
            r1 = config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_key": "sk-current", "api_base": "http://b"},
                    {"custom_id": "m2", "name": "model two", "api_key": "sk-new", "api_base": "http://c"},
                ]},
                operator="admin",
            )
            config_service.rollback(db, cfg, r1["change_id"], operator="admin")
            restored = cfg["custom_models"]
            by_id = {m["custom_id"]: m for m in restored}
            # 结构回到旧状态（api_base 回退），但真实密钥保留，绝不被 "***" 覆盖
            self.assertEqual(by_id["m1"]["api_base"], "http://a")
            self.assertEqual(by_id["m1"]["api_key"], "sk-current")
            self.assertNotIn("m2", by_id)
            blob = json.dumps(cfg)
            self.assertNotIn("sk-new", blob)

    def test_rollback_is_chainable_and_idempotent(self):
        with _temp_db() as db:
            cfg = _seed_config()
            r1 = config_service.apply_update(db, cfg, {"enable_reranker": True}, operator="admin")
            rr = config_service.rollback(db, cfg, r1["change_id"], operator="admin")
            # 再次回滚同一条：已是旧值，回滚结果不改变
            rr2 = config_service.rollback(db, cfg, r1["change_id"], operator="admin")
            self.assertEqual(cfg["enable_reranker"], False)
            self.assertEqual(rr["rolled_back_keys"], rr2["rolled_back_keys"])
            # 向前滚（回滚回滚记录）也稳定
            config_service.rollback(db, cfg, rr["change_id"], operator="admin")
            self.assertEqual(cfg["enable_reranker"], True)

    def test_rollback_missing_change_returns_404(self):
        with _temp_db() as db:
            with self.assertRaises(ConfigChangeNotFound) as ctx:
                config_service.rollback(db, _seed_config(), 99999, operator="admin")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_rollback_never_touches_unknown_or_secret_changes(self):
        with _temp_db() as db:
            cfg = _seed_config()
            # 构造一条包含秘密键的记录（正常情况下不会被写入，这里直接手工插入验证防御）
            row = ConfigChangeHistory(
                operator="admin",
                changes=json.dumps([
                    {"key": "model_name", "old": "a", "new": "b"},
                    {"key": "api_key", "old": "sk-x", "new": "sk-y"},
                ]),
            )
            db.add(row)
            db.commit()
            rr = config_service.rollback(db, cfg, row.id, operator="admin")
            self.assertEqual(rr["rolled_back_keys"], ["model_name"])
            self.assertEqual(cfg["model_name"], "a")
            # 秘密键未被回滚，当前配置没有 sk-x
            self.assertNotIn("sk-x", json.dumps(cfg.dump_config()))


def _load_atomic_write_module():
    """加载 src/config/__init__.py 的 _atomic_write（不触发 src/__init__.py → Milvus）。

    src/config 依赖 `from src.utils.logging_config import logger`（绝对导入），
    因此用假 src 包 stub 掉 sys.modules，只加载该模块本身。
    """
    root = Path(__file__).resolve().parents[1]
    stub_src = types.ModuleType("src")
    stub_utils = types.ModuleType("src.utils")
    stub_logcfg = types.ModuleType("src.utils.logging_config")
    stub_logcfg.logger = logging.getLogger("test-stub")
    saved = {}
    for name, mod in (("src", stub_src), ("src.utils", stub_utils),
                      ("src.utils.logging_config", stub_logcfg)):
        saved[name] = __import__("sys").modules.get(name)
        __import__("sys").modules[name] = mod
    try:
        path = root / "src" / "config" / "__init__.py"
        spec = importlib.util.spec_from_file_location("src.config", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sysmod = __import__("sys").modules
        for name in ("src", "src.utils", "src.utils.logging_config"):
            if saved[name] is None:
                sysmod.pop(name, None)
            else:
                sysmod[name] = saved[name]


class CustomModelsSchemaTests(unittest.TestCase):
    """P2-1：custom_models 每项严格结构校验 + 剥离只读元数据。"""

    def test_item_schema_rejects_malformed_items(self):
        with _temp_db() as db:
            cfg = _seed_config()
            bad_values = [
                [{"name": "x", "api_base": "http://a"}],                          # 缺 custom_id
                [{"custom_id": "m9", "api_base": "http://a"}],                    # 缺 name
                [{"custom_id": "m9", "name": "x"}],                               # 缺 api_base
                [{"custom_id": "m9", "name": "x", "api_base": "ftp://a"}],        # 非 http(s)
                [{"custom_id": "m9", "name": "x" * 300, "api_base": "http://a"}],  # name 超长
                [{"custom_id": "m9", "name": "x", "api_base": "http://a" + "a" * 3000}],  # api_base 超长
                ["not-a-dict"],                                                   # 非对象项
            ]
            for bad in bad_values:
                with self.subTest(bad=bad):
                    with self.assertRaises(ConfigError) as ctx:
                        config_service.apply_update(db, cfg, {"custom_models": bad}, operator="admin")
                    self.assertEqual(ctx.exception.status_code, 400)
            # 全部拒绝，不写任何历史
            self.assertEqual(db.query(ConfigChangeHistory).count(), 0)

    def test_duplicate_custom_id_rejected(self):
        with _temp_db() as db:
            cfg = _seed_config()
            with self.assertRaises(ConfigError) as ctx:
                config_service.apply_update(
                    db, cfg,
                    {"custom_models": [
                        {"custom_id": "dup", "name": "a", "api_base": "http://a"},
                        {"custom_id": "dup", "name": "b", "api_base": "http://b"},
                    ]},
                    operator="admin",
                )
            self.assertIn("重复", str(ctx.exception))

    def test_readonly_metadata_stripped_before_persist(self):
        """前端回传的 has_api_key/key_hint 绝不写回配置文件（P2-1）。"""
        with _temp_db() as db:
            cfg = _seed_config()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "model one", "api_base": "http://b",
                     "has_api_key": True, "key_hint": "xxxx"},
                ]},
                operator="admin",
            )
            item = cfg["custom_models"][0]
            self.assertNotIn("has_api_key", item)
            self.assertNotIn("key_hint", item)
            self.assertEqual(item["api_base"], "http://b")
            self.assertEqual(item["custom_id"], "m1")

    def test_valid_item_accepted_and_normalized(self):
        with _temp_db() as db:
            cfg = _seed_config()
            config_service.apply_update(
                db, cfg,
                {"custom_models": [
                    {"custom_id": "m1", "name": "  model one  ", "api_base": "https://example.com/v1"},
                ]},
                operator="admin",
            )
            item = cfg["custom_models"][0]
            self.assertEqual(item["name"], "model one")       # 空白被裁剪
            self.assertEqual(item["api_base"], "https://example.com/v1")
            # 未填 key → 沿用当前真实密钥，绝不因校验被丢弃
            self.assertEqual(item["api_key"], "sk-secret-old")


class ConfigConcurrencyTests(unittest.TestCase):
    """P2-1：并发更新配置 → 进程内锁串行化，内存/文件不丢字段、不留临时文件。"""

    def _concurrent_apply(self, tmp):
        """共享引擎 + 每线程独立 session，两个线程并发改不同字段。"""
        engine = create_engine(
            f"sqlite:///{Path(tmp) / 'shared.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        cfg_file = Path(tmp) / "base.json"
        cfg = FileFakeConfig(str(cfg_file))
        cfg.update(_seed_config())
        cfg.save()
        results, errors = [], []

        def worker(key, value):
            try:
                session = sessionmaker(bind=engine)()
                try:
                    results.append(config_service.apply_update(session, cfg, {key: value}, operator=key))
                finally:
                    session.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=("enable_reranker", True)),
            threading.Thread(target=worker, args=("graph_hops", 7)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        return engine, cfg, cfg_file, results, errors

    def test_concurrent_updates_persist_both_fields_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine, cfg, cfg_file, results, errors = self._concurrent_apply(tmp)
            try:
                self.assertEqual(errors, [], "并发更新不得失败：{}".format(errors))
                self.assertEqual(len(results), 2)
                self.assertEqual(cfg["enable_reranker"], True)
                self.assertEqual(cfg["graph_hops"], 7)
                # 落盘文件同时保留两个字段，无字段丢失
                on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
                self.assertEqual(on_disk["enable_reranker"], True)
                self.assertEqual(on_disk["graph_hops"], 7)
                # 两条历史记录都写入
                session = sessionmaker(bind=engine)()
                try:
                    self.assertEqual(session.query(ConfigChangeHistory).count(), 2)
                finally:
                    session.close()
            finally:
                engine.dispose()  # 释放 SQLite 文件句柄，避免 Windows 清理失败

    def test_concurrent_updates_are_serialized_in_save(self):
        """无进程内锁时两个更新会同时进入 save（in_save 计数 > 1）；有锁则串行。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "base.json"
            cfg = FileFakeConfig(str(cfg_file))
            cfg.update(_seed_config())
            cfg.save()
            real_save = FileFakeConfig.save
            save_entered = threading.Event()
            release_save = threading.Event()
            in_save = {"n": 0}
            results, errors = [], []

            def sync_save(self):
                in_save["n"] += 1
                save_entered.set()
                release_save.wait(timeout=10)
                in_save["n"] -= 1
                return real_save(self)

            def worker(key, value):
                try:
                    with _temp_db() as db:
                        results.append(config_service.apply_update(db, cfg, {key: value}, operator=key))
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            with patch.object(FileFakeConfig, "save", sync_save):
                t1 = threading.Thread(target=worker, args=("enable_reranker", True))
                t2 = threading.Thread(target=worker, args=("graph_hops", 7))
                t1.start()
                self.assertTrue(save_entered.wait(timeout=10), "第一个更新未进入 save")
                t2.start()
                # 给第二个线程 1s 机会进入 save：若无锁会与第一个并发（in_save==2）
                deadline = time.time() + 1.0
                while time.time() < deadline and in_save["n"] < 2:
                    time.sleep(0.01)
                self.assertEqual(in_save["n"], 1, "并发更新必须串行：第二个线程不得同时进入 save")
                release_save.set()
                t1.join(timeout=15)
                t2.join(timeout=15)
            self.assertEqual(errors, [])
            self.assertEqual(cfg["enable_reranker"], True)
            self.assertEqual(cfg["graph_hops"], 7)

    def test_atomic_write_temp_uses_random_component_no_tmp_collision(self):
        """P2-1：_atomic_write 临时名含随机值，两线程并发写同一文件不再竞争同一临时文件。

        用 barrier 同步两个 os.replace：旧实现两线程共享同一个临时名，第二个
        replace 会因临时文件已被移走而抛 FileNotFoundError；新实现各自独立临时名，
        两个 replace 都成功，最终文件是两份完整内容之一且无残留。
        """
        module = _load_atomic_write_module()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            real_replace = module.os.replace
            barrier = threading.Barrier(2)
            replace_lock = threading.Lock()  # 串行化最终 replace，隔离 Windows 目标文件竞态

            def sync_replace(src, dst):
                # 两个线程都完成写临时文件、到达 replace 后才放行：
                # 旧实现共享同一临时名，后到的 replace 因临时文件已被移走而失败；
                # 新实现各自独立临时名，两个 replace 都成功。
                barrier.wait(timeout=5)
                with replace_lock:
                    return real_replace(src, dst)

            errors = []

            def writer(content):
                try:
                    module._atomic_write(str(path), content)
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

            with patch.object(module.os, "replace", side_effect=sync_replace):
                t1 = threading.Thread(target=writer, args=("content-A",))
                t2 = threading.Thread(target=writer, args=("content-B",))
                t1.start()
                t2.start()
                t1.join(timeout=10)
                t2.join(timeout=10)

            self.assertEqual(errors, [], "并发原子写不得抛异常：{}".format(errors))
            final = path.read_text(encoding="utf-8")
            self.assertIn(final, ("content-A", "content-B"))
            leftovers = [p.name for p in Path(tmp).iterdir() if ".tmp." in p.name]
            self.assertEqual(leftovers, [], "并发原子写不得残留临时文件")


class BaseRouterSourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.base_router = (root / "server/routers/base_router.py").read_text(encoding="utf-8")

    def test_history_endpoints_present(self):
        self.assertIn('@base.get("/config/history")', self.base_router)
        self.assertIn('@base.get("/config/history/{change_id}")', self.base_router)
        self.assertIn('@base.post("/config/history/{change_id}/rollback")', self.base_router)

    def test_get_config_uses_unified_sanitizer(self):
        self.assertIn("sanitize_config_snapshot", self.base_router)
        self.assertIn("config.dump_config()", self.base_router)

    def test_router_never_requests_custom_models_plaintext(self):
        # P1-1：路由层禁止以明文方式请求 custom_models（不再传 redact_custom_models）
        self.assertNotIn("redact_custom_models", self.base_router)

    def test_updates_go_through_apply_update_with_whitelist(self):
        self.assertIn("config_service.apply_update", self.base_router)
        self.assertIn('"config.update"', self.base_router)
        self.assertIn('"config.rollback"', self.base_router)

    def test_returns_restart_components_without_auto_restart(self):
        self.assertIn('"restart_components"', self.base_router)
        # 配置服务只返回需重启的组件，绝不触发自动重启
        root = Path(__file__).resolve().parents[1]
        service_src = (root / "server/services/config_service.py").read_text(encoding="utf-8")
        self.assertIn("def _restart_components_for", service_src)
        self.assertIsNone(re.search(r"restart\s*\(", service_src))

    def test_secret_keys_not_accepted_in_payloads(self):
        # 路由层把校验交给 config_service，不自己把密钥写进 config
        self.assertIn("config_service.ConfigError", self.base_router)
        self.assertIn("apply_update(db, config", self.base_router)

    def test_audit_hooks_for_failed_and_success(self):
        self.assertIn('status="failed"', self.base_router)
        self.assertIn("audit_service.record", self.base_router)
        self.assertIn('"config.update"', self.base_router)
        self.assertIn('"config.rollback"', self.base_router)


if __name__ == "__main__":
    unittest.main()
