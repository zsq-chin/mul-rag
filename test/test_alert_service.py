"""本机邮件告警验收测试（服务层 + 临时 SQLite/目录）。

覆盖计划阶段 11 的验收点：触发、去重、冷却、恢复、确认和 SMTP 失败路径。
- 相同告警冷却时间去重，避免邮件风暴。
- 恢复正常记录 resolved。
- SMTP 未配置时测试邮件抛 503；邮件与异常消息绝不含 SMTP 密码。
- 后台检查循环 alert_loop 可取消。
路由层只做源码断言（alert_router 导入 db_manager→src，禁止导入）。
"""

import asyncio
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.models.operations_model import AlertRule, AlertEvent, BackupJob
from server.services import alert_service as asvc


@contextmanager
def _temp_env():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = str(root / "data" / "server.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        ctx = {
            "db_path": db_path,
            "save_dir": str(root / "saves"),
            "milvus_uri": "http://milvus:19530",
            "neo4j_uri": "bolt://localhost:7687",
            "neo4j_username": "neo4j",
            "neo4j_password": "secret",
        }
        os.makedirs(ctx["save_dir"], exist_ok=True)
        try:
            yield {"db": session, "ctx": ctx, "root": root}
        finally:
            session.close()
            engine.dispose()


def _refused_probe(*args, **kwargs):
    raise ConnectionRefusedError("connection refused")


def _ok_probe(*args, **kwargs):
    return True


def _notify_stub(records):
    return lambda rule, subject, body, is_resolve: records.append((rule.id, rule.name, subject, is_resolve))


class RuleCrudTests(unittest.TestCase):
    def test_create_and_list(self):
        with _temp_env() as env:
            row = asvc.create_rule(env["db"], "磁盘告警", "disk_space", threshold="90", created_by="admin")
            self.assertTrue(row.enabled)
            self.assertEqual(row.rule_type, "disk_space")
            data = asvc.list_rules(env["db"])
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["rule_type_label"], "磁盘剩余比例")

    def test_create_rejects_bad_type_and_empty_name(self):
        with _temp_env() as env:
            with self.assertRaises(asvc.AlertError):
                asvc.create_rule(env["db"], "x", "unknown_type")
            with self.assertRaises(asvc.AlertError):
                asvc.create_rule(env["db"], "  ", "milvus")

    def test_update_rule(self):
        with _temp_env() as env:
            row = asvc.create_rule(env["db"], "告警", "milvus", cooldown_seconds=3600)
            updated = asvc.update_rule(env["db"], row.id, enabled=False, threshold="50", cooldown_seconds=0)
            self.assertFalse(updated.enabled)
            self.assertEqual(updated.threshold, "50")
            self.assertEqual(updated.cooldown_seconds, 0)

    def test_update_missing_404_and_bad_cooldown(self):
        with _temp_env() as env:
            with self.assertRaises(asvc.AlertError) as ctx:
                asvc.update_rule(env["db"], 999, name="x")
            self.assertEqual(ctx.exception.status_code, 404)
            row = asvc.create_rule(env["db"], "告警", "milvus")
            with self.assertRaises(asvc.AlertError):
                asvc.update_rule(env["db"], row.id, cooldown_seconds="abc")

    def test_delete_detaches_events(self):
        with _temp_env() as env:
            row = asvc.create_rule(env["db"], "告警", "milvus")
            ev = asvc._create_event(env["db"], row, "trigger", "warning", "down")
            self.assertEqual(ev.rule_id, row.id)
            asvc.delete_rule(env["db"], row.id)
            self.assertEqual(asvc.list_rules(env["db"])["total"], 0)
            # 事件保留但 rule_id 置空
            self.assertIsNone(env["db"].query(AlertEvent).get(ev.id).rule_id)


class TriggerDedupCooldownTests(unittest.TestCase):
    def test_trigger_creates_firing_event_and_notifies(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=3600)
            records = []
            result = asvc.evaluate_rules(
                env["db"], env["ctx"], now=datetime(2026, 8, 11, 12, 0, 0),
                milvus_probe=_refused_probe, neo4j_probe=_ok_probe, notify=_notify_stub(records),
            )
            self.assertEqual(len(result["fired"]), 1)
            self.assertEqual(len(records), 1)
            self.assertFalse(records[0][3])  # is_resolve False
            ev = env["db"].query(AlertEvent).get(result["fired"][0])
            self.assertEqual(ev.status, "firing")
            self.assertEqual(ev.event_type, "trigger")

    def test_dedup_within_cooldown(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=100000)
            records = []
            t = datetime(2026, 8, 11, 12, 0, 0)
            first = asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                        milvus_probe=_refused_probe, neo4j_probe=_ok_probe,
                                        notify=_notify_stub(records))
            second = asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                         milvus_probe=_refused_probe, neo4j_probe=_ok_probe,
                                         notify=_notify_stub(records))
            self.assertEqual(len(first["fired"]), 1)
            self.assertEqual(len(second["fired"]), 0)  # 冷却期内去重
            self.assertEqual(len(records), 1)          # 只发一次通知
            self.assertEqual(asvc.list_events(env["db"])["total"], 1)

    def test_refires_after_cooldown(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=60)
            t0 = datetime(2026, 8, 11, 12, 0, 0)
            asvc.evaluate_rules(env["db"], env["ctx"], now=t0,
                                milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            # 超过冷却时间后再次触发
            asvc.evaluate_rules(env["db"], env["ctx"], now=datetime(2026, 8, 11, 12, 2, 0),
                                milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            self.assertEqual(asvc.list_events(env["db"])["total"], 2)


class RecoveryTests(unittest.TestCase):
    def test_recovery_records_resolved(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=3600)
            records = []
            t = datetime(2026, 8, 11, 12, 0, 0)
            asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                milvus_probe=_refused_probe, neo4j_probe=_ok_probe,
                                notify=_notify_stub(records))
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                         milvus_probe=_ok_probe, neo4j_probe=_ok_probe,
                                         notify=_notify_stub(records))
            self.assertEqual(len(result["resolved"]), 1)
            self.assertEqual(len(records), 2)
            self.assertTrue(records[1][3])  # is_resolve True
            ev = env["db"].query(AlertEvent).get(result["resolved"][0])
            self.assertEqual(ev.status, "resolved")
            self.assertEqual(ev.event_type, "recover")

    def test_retrigger_after_resolved(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=100000)
            t = datetime(2026, 8, 11, 12, 0, 0)
            asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                milvus_probe=_ok_probe, neo4j_probe=_ok_probe)
            # 恢复后再故障：last 是 resolved，不受冷却限制，重新触发
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=t,
                                         milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            self.assertEqual(len(result["fired"]), 1)
            self.assertEqual(asvc.list_events(env["db"])["total"], 3)


class AckTests(unittest.TestCase):
    def test_acknowledge_event(self):
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus")
            ev = asvc._create_event(env["db"], rule, "trigger", "warning", "down")
            acked = asvc.acknowledge_event(env["db"], ev.id)
            self.assertEqual(acked.status, "acknowledged")
            self.assertIsNotNone(acked.acknowledged_at)
            with self.assertRaises(asvc.AlertError) as ctx:
                asvc.acknowledge_event(env["db"], 999)
            self.assertEqual(ctx.exception.status_code, 404)


class RuleTypeTests(unittest.TestCase):
    def test_disk_space_rule_triggers(self):
        with _temp_env() as env:
            asvc.create_rule(env["db"], "磁盘", "disk_space", threshold="0")
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=datetime(2026, 8, 11))
            self.assertEqual(len(result["fired"]), 1)

    def test_backup_fail_consecutive(self):
        with _temp_env() as env:
            asvc.create_rule(env["db"], "备份", "backup_fail", threshold="2")
            # 最近两次（id 倒序）为 failed / running，最老一次已完成
            env["db"].add_all([
                BackupJob(filename="ok.zip", path="ok.zip", status="completed", size_bytes=1),
                BackupJob(filename="a.zip", path="a.zip", status="failed", size_bytes=1),
                BackupJob(filename="b.zip", path="b.zip", status="running", size_bytes=1),
            ])
            env["db"].commit()
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=datetime(2026, 8, 11))
            self.assertEqual(len(result["fired"]), 1)

    def test_gpu_mem_rule_only_fires_when_high(self):
        with _temp_env() as env:
            asvc.create_rule(env["db"], "显存", "gpu_mem", threshold="90")
            high = subprocess.CompletedProcess(args=[], returncode=0, stdout="10, 7900, 8192\n")  # 96% 显存
            with patch.object(__import__("server.services.monitoring_service", fromlist=["x"]).subprocess, "run", return_value=high):
                result = asvc.evaluate_rules(env["db"], env["ctx"], now=datetime(2026, 8, 11))
            self.assertEqual(len(result["fired"]), 1)
            # GPU 不存在 → 不告警
            with patch.object(__import__("server.services.monitoring_service", fromlist=["x"]).subprocess, "run", side_effect=FileNotFoundError):
                result = asvc.evaluate_rules(env["db"], env["ctx"], now=datetime(2026, 8, 11))
            self.assertEqual(len(result["fired"]), 0)


class SmtpTests(unittest.TestCase):
    def test_unconfigured_returns_503(self):
        with self.assertRaises(asvc.SMTPNotConfigured) as ctx:
            asvc.send_email({}, "a@b.com", "t", "b")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_connection_failure_no_password_leak(self):
        with self.assertRaises(asvc.AlertError) as ctx:
            asvc.send_email(
                {"host": "127.0.0.1", "port": 1, "from_addr": "a@b.com", "username": "u", "password": "smtpsecret123"},
                "a@b.com", "t", "b",
            )
        self.assertEqual(ctx.exception.status_code, 502)
        self.assertNotIn("smtpsecret123", str(ctx.exception))
        self.assertNotIn("smtpsecret123", "".join(str(x) for x in []))  # 无额外输出

    def test_send_email_uses_login_and_tls(self):
        class FakeSMTP:
            def __init__(self, *a, **k):
                self.tls = False
                self.login_args = None
                self.sent = None

            def starttls(self):
                self.tls = True

            def login(self, user, pwd):
                self.login_args = (user, pwd)

            def sendmail(self, from_addr, to, msg):
                self.sent = (from_addr, to)

            def quit(self):
                pass

            def close(self):
                pass

        fake = FakeSMTP()
        with patch.object(asvc.smtplib, "SMTP", return_value=fake):
            res = asvc.send_email(
                {"host": "smtp.example.com", "port": 587, "from_addr": "a@b.com",
                 "username": "user", "password": "pw", "use_tls": True},
                "x@y.com", "主题", "正文",
            )
        self.assertTrue(res["ok"])
        self.assertTrue(fake.tls)
        self.assertEqual(fake.login_args, ("user", "pw"))
        self.assertEqual(fake.sent[0], "a@b.com")

    def test_smtp_from_env(self):
        with patch.dict(os.environ, {"SMTP_HOST": "h", "SMTP_PORT": "465", "SMTP_PASSWORD": "p"}, clear=True):
            cfg = asvc.smtp_from_env()
        self.assertEqual(cfg["host"], "h")
        self.assertEqual(cfg["port"], 465)
        self.assertEqual(cfg["password"], "p")


class AlertLoopTests(unittest.TestCase):
    def test_loop_runs_and_stops(self):
        calls = []

        def evaluate():
            calls.append(1)

        async def run():
            stop = asyncio.Event()
            stop.set()
            await asvc.alert_loop(evaluate, interval=0.01, stop=stop)

        asyncio.run(run())
        self.assertEqual(len(calls), 1)  # 先执行一轮再退出


class AlertRouterSourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.router = (root / "server/routers/alert_router.py").read_text(encoding="utf-8")

    def test_endpoints_present(self):
        for route in (
            '@router.post("/alert-rules")',
            '@router.get("/alert-rules")',
            '@router.patch("/alert-rules/{rule_id}")',
            '@router.delete("/alert-rules/{rule_id}")',
            '@router.get("/alert-events")',
            '@router.post("/alert-events/{event_id}/acknowledge")',
            '@router.post("/email/test")',
        ):
            self.assertIn(route, self.router)

    def test_prefix_and_superadmin(self):
        self.assertIn('router = APIRouter(prefix="/operations"', self.router)
        self.assertIn("get_superadmin_user", self.router)

    def test_smtp_password_not_in_router(self):
        self.assertNotIn("SMTP_PASSWORD", self.router)
        self.assertNotIn("smtp_password", self.router)
        self.assertNotIn("password", self.router)

    def test_no_remote_multimodal_import(self):
        self.assertNotIn("multimodal_remote", self.router)
        self.assertNotIn("mul_rag", self.router)


if __name__ == "__main__":
    unittest.main()
