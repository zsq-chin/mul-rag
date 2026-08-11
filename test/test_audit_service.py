"""AuditService 单元测试（不依赖数据库 / Milvus / docker）。"""

import json
import unittest

from server.services.audit_service import AuditService, sanitize_detail


class FakeSession:
    def __init__(self, fail_commit=False):
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self._fail_commit = fail_commit

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        if self._fail_commit:
            raise RuntimeError("commit boom")
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class AuditServiceTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self._orig_factory = AuditService.session_factory
        AuditService.session_factory = lambda: self.session

    def tearDown(self):
        AuditService.session_factory = self._orig_factory

    def test_record_writes_sanitized_entry(self):
        AuditService.record(
            "feedback.submit",
            user_id=1,
            resource_type="message",
            resource_id="m-123",
            status="success",
            detail={"message_id": "m-123", "conversation_id": "c-1", "note": "外部字段应被丢弃"},
            ip="1.2.3.4",
        )
        self.assertTrue(self.session.committed)
        self.assertEqual(len(self.session.added), 1)
        entry = self.session.added[0]
        # 注意：不使用 isinstance(entry, OperationLog) —— discover 模式下其他测试模块
        # 会用 fake 替换 sys.modules["server"]，导致同一类名指向两份不同模块对象。
        self.assertEqual(entry.__class__.__name__, "OperationLog")
        self.assertEqual(entry.operation, "feedback.submit")
        self.assertEqual(entry.user_id, 1)
        self.assertEqual(entry.ip_address, "1.2.3.4")
        details = json.loads(entry.details)
        self.assertEqual(
            details,
            {
                "status": "success",
                "resource_type": "message",
                "resource_id": "m-123",
                "message_id": "m-123",
                "conversation_id": "c-1",
            },
        )

    def test_record_sanitizes_secrets(self):
        AuditService.record(
            "config.update",
            user_id=2,
            detail={
                "password": "hunter2",
                "api_key": "sk-xxx",
                "smtp_password": "smtp-secret",
                "jwt_secret": "jwt",
                "token": "abc",
                "file_id": "f-1",  # 白名单内，应保留
            },
        )
        entry = self.session.added[0]
        details = json.loads(entry.details)
        self.assertEqual(details.get("file_id"), "f-1")
        for secret in ("password", "api_key", "smtp_password", "jwt_secret", "token"):
            self.assertNotIn(secret, details)
        self.assertNotIn("hunter2", entry.details)

    def test_record_failure_never_raises(self):
        failing = FakeSession(fail_commit=True)
        AuditService.session_factory = lambda: failing
        # 不应抛出异常
        AuditService.record("backup.create", user_id=1, detail={"backup_id": 9})
        self.assertTrue(failing.rolled_back)
        self.assertTrue(failing.closed)

    def test_sanitize_detail_drops_non_dict_and_unknown(self):
        self.assertEqual(sanitize_detail(None), {})
        self.assertEqual(sanitize_detail("not a dict"), {})
        safe = sanitize_detail({"file_id": 3, "size_bytes": 10, "some_other": "x"})
        self.assertEqual(safe, {"file_id": 3, "size_bytes": 10})

    def test_sanitize_detail_serializes_non_scalar(self):
        safe = sanitize_detail({"file_id": {"nested": 1}})
        self.assertEqual(safe, {"file_id": "{'nested': 1}"})


if __name__ == "__main__":
    unittest.main()
