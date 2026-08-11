"""系统配置历史、白名单、脱敏与回滚验收测试。

服务层 + 临时 SQLite（不依赖 Milvus/docker），config 用鸭子类型替身；
路由层只做源码断言（base_router 导入 src 会触发 Milvus，禁止导入）。

验收对照计划阶段 8：
- 未知键/秘密键/空请求返回 400（ConfigError）。
- 历史与回滚记录中绝不出现 API Key / 密码 / Token。
- 回滚只回滚该次变更涉及的非秘密字段，写新历史，不删旧历史，可连续回滚。
- 修改/回滚返回 restart_components，不自动重启。
"""

import json
import re
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

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

    def test_keeps_custom_models_intact_for_interactive_mode(self):
        cfg = _seed_config()
        out = sanitize_config_snapshot(cfg.dump_config(), redact_custom_models=False)
        self.assertEqual(out["custom_models"][0]["api_key"], "sk-secret-old")

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
