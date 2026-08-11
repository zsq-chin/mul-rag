"""统一操作审计验收测试（服务层 + 临时 SQLite，不依赖 Milvus/docker）。

覆盖：记录与读取、筛选（用户/动作/资源类型/状态/时间范围）、分页、
详情脱敏（白名单 + 密钥黑名单）、status/resource 提升到顶层、
动作码词汇表，以及 auth/user/model/data 四个路由的审计钩子。
"""

import datetime
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
import re

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import server.models.user_model  # noqa: F401
from server.models import Base
from server.models.user_model import OperationLog, User
from server.services import audit_service
from server.services.audit_service import (
    AuditService,
    list_events,
    get_event,
    list_actions,
    KNOWN_ACTIONS,
)


@contextmanager
def _temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        # 让 AuditService.record 使用独立会话写入同一个临时库
        AuditService.session_factory = Session
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
            AuditService.session_factory = None


def _seed_users(session):
    admin = User(username="sysadmin", role="superadmin", password_hash="x")
    normal = User(username="alice", role="user", password_hash="x")
    session.add_all([admin, normal])
    session.commit()
    session.refresh(admin)
    session.refresh(normal)
    return admin, normal


class AuditReadTests(unittest.TestCase):
    def test_record_and_list_with_username_join(self):
        with _temp_db() as session:
            admin, normal = _seed_users(session)
            AuditService.record("auth.login", user_id=admin.id, status="success",
                                detail={"username": "sysadmin", "role": "superadmin"}, ip="1.2.3.4")
            AuditService.record("user.create", user_id=admin.id, resource_type="user",
                                resource_id=10, detail={"username": "bob", "role": "user"}, ip="1.2.3.4")
            data = list_events(session, page_size=50)
            self.assertEqual(data["total"], 2)
            by_action = {e["action"]: e for e in data["items"]}
            self.assertEqual(by_action["auth.login"]["username"], "sysadmin")
            self.assertEqual(by_action["auth.login"]["status"], "success")
            self.assertEqual(by_action["user.create"]["resource_id"], "10")
            self.assertEqual(by_action["user.create"]["resource_type"], "user")

    def test_filters(self):
        with _temp_db() as session:
            admin, normal = _seed_users(session)
            AuditService.record("auth.login", user_id=admin.id, status="success",
                                detail={"username": "sysadmin"})
            AuditService.record("feedback.upsert", user_id=normal.id, status="success",
                                detail={"message_id": "m1"})
            AuditService.record("feedback.delete", user_id=normal.id, status="failed",
                                detail={"message_id": "m2", "reason": "not_found"})
            # 按动作
            self.assertEqual(list_events(session, action="feedback.upsert")["total"], 1)
            # 按状态
            self.assertEqual(list_events(session, status="failed")["total"], 1)
            # 按用户（用户名模糊）
            self.assertEqual(list_events(session, user="ali")["total"], 2)
            # 按资源类型
            self.assertEqual(list_events(session, resource_type="user")["total"], 0)

    def test_pagination_stable(self):
        with _temp_db() as session:
            admin, _ = _seed_users(session)
            for i in range(25):
                AuditService.record("config.update", user_id=admin.id,
                                    detail={"count": i})
            p1 = list_events(session, page=1, page_size=10)
            p3 = list_events(session, page=3, page_size=10)
            self.assertEqual(p1["total"], 25)
            self.assertEqual(len(p1["items"]), 10)
            self.assertEqual(len(p3["items"]), 5)
            ids1 = {e["id"] for e in p1["items"]}
            ids3 = {e["id"] for e in p3["items"]}
            self.assertTrue(ids1.isdisjoint(ids3))

    def test_time_range_filter(self):
        with _temp_db() as session:
            admin, _ = _seed_users(session)
            AuditService.record("auth.login", user_id=admin.id)
            now = datetime.datetime.now()
            all_ = list_events(session, start=now - datetime.timedelta(days=1),
                               end=now + datetime.timedelta(days=1))
            self.assertEqual(all_["total"], 1)
            none_ = list_events(session, start=now + datetime.timedelta(days=2))
            self.assertEqual(none_["total"], 0)

    def test_get_event(self):
        with _temp_db() as session:
            admin, _ = _seed_users(session)
            AuditService.record("backup.create", user_id=admin.id, resource_type="backup",
                                resource_id="bk_1", detail={"backup_id": "bk_1"})
            event_id = session.query(OperationLog).first().id
            event = get_event(session, event_id)
            self.assertEqual(event["action"], "backup.create")
            self.assertEqual(event["username"], "sysadmin")
            self.assertIsNone(get_event(session, 99999))

    def test_list_actions_covers_plan(self):
        actions = set(list_actions())
        required = {
            "auth.login", "user.create", "user.update", "user.delete",
            "model.create", "model.update", "model.delete", "model.select",
            "knowledge.upload", "knowledge.delete", "knowledge.metadata.update",
            "knowledge.download", "knowledge.export", "feedback.upsert",
            "feedback.delete", "evaluation.import", "config.update",
            "config.rollback", "backup.create", "backup.restore",
            "backup.verify", "backup.delete", "backup.download",
            "alert.rule.create", "alert.rule.update", "alert.rule.delete",
            "alert.event.acknowledge", "alert.email.test",
        }
        self.assertTrue(required.issubset(actions))
        self.assertEqual(len(actions), len(KNOWN_ACTIONS))

    def test_detail_whitelist_and_secret_stripping(self):
        with _temp_db() as session:
            admin, _ = _seed_users(session)
            AuditService.record(
                "model.create", user_id=admin.id, resource_type="user_model",
                detail={
                    "model_name": "gpt-x",
                    "api_base": "https://example.com/v1",
                    "api_key": "sk-secret-123",
                    "password": "p@ss",
                    "authorization": "Bearer abc",
                },
            )
            data = list_events(session)
            details = data["items"][0]["details"]
            self.assertEqual(details.get("model_name"), "gpt-x")
            self.assertEqual(details.get("api_base"), "https://example.com/v1")
            self.assertNotIn("api_key", details)
            self.assertNotIn("password", details)
            self.assertNotIn("authorization", details)
            raw = session.query(OperationLog).first().details
            self.assertNotIn("sk-secret-123", raw)

    def test_status_and_resource_promoted(self):
        with _temp_db() as session:
            admin, _ = _seed_users(session)
            AuditService.record("knowledge.download", user_id=admin.id,
                                resource_type="knowledge_file", resource_id="file_1",
                                status="failed", detail={"reason": "denied"})
            data = list_events(session)
            event = data["items"][0]
            self.assertEqual(event["status"], "failed")
            self.assertEqual(event["resource_type"], "knowledge_file")
            self.assertEqual(event["resource_id"], "file_1")


class AuditRouterSourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.audit_router = (root / "server/routers/audit_router.py").read_text(encoding="utf-8")
        self.auth_router = (root / "server/routers/auth_router.py").read_text(encoding="utf-8")
        self.model_router = (root / "server/routers/user_model_router.py").read_text(encoding="utf-8")
        self.data_router = (root / "server/routers/data_router.py").read_text(encoding="utf-8")

    def test_three_endpoints_all_superadmin_only(self):
        self.assertIn('@router.get("/events")', self.audit_router)
        self.assertIn('@router.get("/events/{event_id}")', self.audit_router)
        self.assertIn('@router.get("/actions")', self.audit_router)
        # 每个端点 + import 行
        self.assertEqual(self.audit_router.count("get_superadmin_user"), 4)

    def test_filters_and_pagination_params(self):
        self.assertIn("resource_type: str = Query", self.audit_router)
        self.assertIn("status: str = Query", self.audit_router)
        self.assertIn("start: str = Query", self.audit_router)
        self.assertIn("page_size: int = Query(20, ge=1, le=100)", self.audit_router)

    def test_no_secrets_in_router(self):
        self.assertNotIn("password", self.audit_router)
        self.assertNotIn("api_key", self.audit_router)
        self.assertNotIn("Authorization", self.audit_router)

    def test_auth_router_has_login_and_user_hooks(self):
        for code in ('"auth.login"', '"user.create"', '"user.update"', '"user.delete"'):
            self.assertIn(code, self.auth_router)

    def test_model_router_hooks_never_log_api_key(self):
        for code in ('"model.create"', '"model.update"', '"model.delete"', '"model.select"'):
            self.assertIn(code, self.model_router)
        # 每个 record 调用参数里都不得出现 api_key
        self.assertIsNone(
            re.search(r'AuditService\.record\([^)]*api_key', self.model_router, re.S)
        )
        self.assertIn('"model_name"', self.model_router)
        self.assertIn('"api_base"', self.model_router)

    def test_data_router_has_upload_and_delete_hooks(self):
        self.assertIn('"knowledge.upload"', self.data_router)
        self.assertIn('"knowledge.delete"', self.data_router)
        self.assertIn("AuditService", self.data_router)


if __name__ == "__main__":
    unittest.main()
