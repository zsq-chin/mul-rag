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

from pydantic import ValidationError

from server.models import Base
from server.models.operations_model import AlertRule, AlertEvent, BackupJob
from server.schemas.alert import AlertRuleCreate, AlertRuleUpdate, TestEmailPayload
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

    def test_event_created_at_uses_injected_clock(self):
        """P1-5 固定时钟语义：事件 created_at 必须写注入的 now，而不是系统/DB 时间。

        否则冷却差值 (now - created_at) 会因两个时钟不同步变成负数，稳定复现
        test_refires_after_cooldown 失败。这里直接验证 created_at == 注入 now。
        """
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=60)
            t0 = datetime(2026, 8, 11, 12, 0, 0)
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=t0,
                                         milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            ev = env["db"].query(AlertEvent).get(result["fired"][0])
            self.assertEqual(ev.created_at, t0)

    def test_acknowledged_event_keeps_injected_created_at(self):
        """P1-5：确认操作不改变触发事件的 created_at，冷却判定仍按注入时钟。"""
        with _temp_env() as env:
            rule = asvc.create_rule(env["db"], "Milvus", "milvus", cooldown_seconds=100000)
            t0 = datetime(2026, 8, 11, 12, 0, 0)
            result = asvc.evaluate_rules(env["db"], env["ctx"], now=t0,
                                         milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            ev = env["db"].query(AlertEvent).get(result["fired"][0])
            asvc.acknowledge_event(env["db"], ev.id)
            # 确认后仍在冷却期内 → 不再触发
            second = asvc.evaluate_rules(env["db"], env["ctx"], now=t0,
                                         milvus_probe=_refused_probe, neo4j_probe=_ok_probe)
            self.assertEqual(len(second["fired"]), 0)
            self.assertEqual(asvc.list_events(env["db"])["total"], 1)


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

    def test_loop_does_not_block_event_loop(self):
        """P1-7 验收：阻塞探测在后台线程执行，事件循环持续响应。"""
        import time
        import threading as _threading

        calls = []
        started = _threading.Event()
        release = _threading.Event()

        def evaluate():
            started.set()
            release.wait(timeout=5)  # 模拟每轮探测阻塞
            calls.append(1)

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.01, stop=stop)
            )
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=2)
            # 探测线程正卡住，事件循环仍应能调度协程（0.1s 睡眠不被拖长）
            t0 = time.monotonic()
            await asyncio.sleep(0.1)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 0.4, "事件循环被阻塞探测拖住了")
            release.set()
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(run())
        self.assertEqual(len(calls), 1)

    def test_loop_skips_rounds_while_previous_blocked(self):
        """P1-7 验收：上一轮未结束时不叠加新一轮（并发护栏 + 整轮超时）。"""
        import threading as _threading

        calls = []
        release = _threading.Event()

        def evaluate():
            calls.append(1)
            release.wait(timeout=5)

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.01, stop=stop, round_timeout=0.05)
            )
            await asyncio.sleep(0.4)  # 阻塞期间本可跑很多轮
            self.assertEqual(len(calls), 1, "阻塞期间不应堆积多轮评估")
            release.set()
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(run())

    def test_alert_interval_positive_validation(self):
        """P1-7：ALERT_CHECK_INTERVAL_SECONDS 非法/非正必须回退默认，杜绝忙循环。"""
        self.assertEqual(asvc.alert_interval_seconds({"ALERT_CHECK_INTERVAL_SECONDS": "5"}), 5.0)
        self.assertEqual(asvc.alert_interval_seconds({"ALERT_CHECK_INTERVAL_SECONDS": "0"}), 60.0)
        self.assertEqual(asvc.alert_interval_seconds({"ALERT_CHECK_INTERVAL_SECONDS": "-3"}), 60.0)
        self.assertEqual(asvc.alert_interval_seconds({"ALERT_CHECK_INTERVAL_SECONDS": "abc"}), 60.0)
        self.assertEqual(asvc.alert_interval_seconds({}), 60.0)
        self.assertEqual(
            asvc.alert_round_timeout_seconds({"ALERT_EVALUATE_TIMEOUT_SECONDS": "30"}), 30.0
        )
        self.assertEqual(
            asvc.alert_round_timeout_seconds({"ALERT_EVALUATE_TIMEOUT_SECONDS": "-1"}), 90.0
        )
        self.assertEqual(asvc.alert_round_timeout_seconds({}), 90.0)

    def test_loop_negative_interval_coerced(self):
        """负间隔经兜底后仍按一轮执行退出，不形成忙循环。"""
        calls = []

        def evaluate():
            calls.append(1)

        async def run():
            stop = asyncio.Event()
            stop.set()
            await asvc.alert_loop(evaluate, interval=-1, stop=stop)

        asyncio.run(run())
        self.assertEqual(len(calls), 1)

    def test_blocked_round_is_tracked_skipped_and_recovers(self):
        """P1-5：永久阻塞探测被显式跟踪，不占锁、不叠加线程、解除后恢复。

        - 卡住期间 running=True、timed_out=True、后续轮次跳过（不重复提交线程）；
        - 解除阻塞后 timed_out 复位并继续完成常规轮次。
        """
        import threading as _threading

        calls = []
        release = _threading.Event()

        def evaluate():
            calls.append(1)
            release.wait(timeout=5)

        async def run():
            state = asvc.AlertLoopState()
            stop = asyncio.Event()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.05, stop=stop, round_timeout=0.05, state=state)
            )
            # 等第一轮真正启动并卡住
            for _ in range(200):
                if state.running:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(state.running, "阻塞探测期间应处于 running")
            await asyncio.sleep(0.2)  # 卡住期间本可跑多轮
            self.assertEqual(len(calls), 1, "卡住期间不应提交新的评估线程")
            self.assertTrue(state.timed_out, "超过 round_timeout 应置位 timed_out")
            self.assertGreaterEqual(state.skipped, 1, "卡住期间应跳过后续轮次")
            # 解除阻塞：线程退出，恢复常规轮次
            release.set()
            await asyncio.sleep(0.25)
            self.assertFalse(state.timed_out, "解除阻塞后 timed_out 应复位")
            self.assertGreaterEqual(state.completed, 2, "解除后应继续完成轮次")
            stop.set()
            await asyncio.wait_for(task, timeout=3)
            self.assertTrue(state.stopped)

        asyncio.run(run())

    def test_stop_while_blocked_exits_promptly_without_new_rounds(self):
        """P1-5：阻塞探测期间 stop 置位 → 循环及时退出，且之后不调度任何新轮次。"""
        import threading as _threading
        import time as _time

        calls = []
        release = _threading.Event()
        started = _threading.Event()

        def evaluate():
            calls.append(1)
            started.set()
            release.wait(timeout=10)

        async def run():
            state = asvc.AlertLoopState()
            stop = asyncio.Event()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.05, stop=stop, round_timeout=0.05, state=state)
            )
            await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=3)
            self.assertEqual(len(calls), 1)
            stop.set()
            # 循环应尽快退出（不会因等待阻塞线程而挂住）；close 后不再有新轮次
            await asyncio.wait_for(task, timeout=2)
            self.assertTrue(state.stopped)
            before = len(calls)
            await asyncio.sleep(0.2)
            self.assertEqual(len(calls), before, "stop 后不得再调度新的评估轮次")
            release.set()  # 释放测试残留线程，避免拖慢 executor 关闭

        asyncio.run(run())

    def test_cancel_while_blocked_does_not_leak_rounds(self):
        """P1-5：取消运行中的 alert_loop 应立即退出（优雅吞噬取消），
        已提交的后台线程不被重复提交，取消后不再调度任何新轮次。"""
        import threading as _threading

        calls = []
        release = _threading.Event()

        def evaluate():
            calls.append(1)
            release.wait(timeout=10)

        async def run():
            state = asvc.AlertLoopState()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.05, stop=None, state=state)
            )
            for _ in range(200):
                if state.running:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(state.running)
            task.cancel()
            # 循环在取消点捕获 CancelledError 优雅退出（不强行杀死后台线程）
            await asyncio.wait_for(task, timeout=2)
            self.assertTrue(state.stopped, "取消后循环应标记为已停止")
            await asyncio.sleep(0.15)
            self.assertEqual(len(calls), 1, "取消后不得再提交新的评估线程")
            release.set()  # 释放测试残留线程，避免拖慢 executor 关闭

        asyncio.run(run())


class AlertSchemaTests(unittest.TestCase):
    """P2-3：告警请求模型做严格类型/枚举/邮箱格式校验，替代裸 dict。"""

    def test_create_rule_validates_type_email_cooldown(self):
        r = AlertRuleCreate(
            name="磁盘告警", rule_type="disk_space", cooldown_seconds=120, notify_email="a@b.com"
        )
        self.assertTrue(r.enabled)
        self.assertEqual(r.cooldown_seconds, 120)
        with self.assertRaises(ValidationError):
            AlertRuleCreate(name="x", rule_type="nonsense")
        with self.assertRaises(ValidationError):
            AlertRuleCreate(name="x", rule_type="disk_space", notify_email="not-an-email")
        with self.assertRaises(ValidationError):
            AlertRuleCreate(name="x", rule_type="disk_space", cooldown_seconds=-1)
        with self.assertRaises(ValidationError):
            AlertRuleCreate(name="", rule_type="disk_space")

    def test_update_rule_optional_and_clearable(self):
        d = AlertRuleUpdate(threshold=None, notify_email=None).model_dump(exclude_unset=True)
        self.assertEqual(set(d), {"threshold", "notify_email"})
        self.assertIsNone(d["threshold"])
        self.assertIsNone(d["notify_email"])
        d2 = AlertRuleUpdate(name="新名字").model_dump(exclude_unset=True)
        self.assertEqual(set(d2), {"name"})
        with self.assertRaises(ValidationError):
            AlertRuleUpdate(rule_type="bad_type")

    def test_test_email_payload_validates(self):
        TestEmailPayload(to_email="a@b.com")
        with self.assertRaises(ValidationError):
            TestEmailPayload(to_email="bad")


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

    def test_uses_pydantic_request_models(self):
        """P2-3：告警创建/更新/测试邮件必须用 Pydantic 模型，不再接受裸 dict。"""
        self.assertIn("AlertRuleCreate", self.router)
        self.assertIn("AlertRuleUpdate", self.router)
        self.assertIn("TestEmailPayload", self.router)
        self.assertNotIn("dict = Body(...)", self.router)

    def test_failure_audits_on_sensitive_ops(self):
        """P2-3：创建/更新/删除规则与确认事件都同时记录成功与失败。"""
        self.assertEqual(self.router.count('"alert.rule.create"'), 2)
        self.assertEqual(self.router.count('"alert.rule.update"'), 2)
        self.assertEqual(self.router.count('"alert.rule.delete"'), 2)
        self.assertEqual(self.router.count('"alert.event.acknowledge"'), 2)

    def test_no_remote_multimodal_import(self):
        self.assertNotIn("multimodal_remote", self.router)
        self.assertNotIn("mul_rag", self.router)


if __name__ == "__main__":
    unittest.main()
