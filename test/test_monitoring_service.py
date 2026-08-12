"""本机系统监控验收测试（服务层 + 临时 SQLite/目录）。

覆盖计划阶段 10 的验收点：
- 依赖成功 / 超时 / 拒绝连接 / GPU 不存在四类场景均返回结构化状态。
- 每个检查项独立超时；单个依赖失败不能拖垮整个接口。
- GPU 不存在时返回 unavailable，不产生 500。
- I3.3：远端多模态依赖单独上报 healthy/degraded/down/unavailable，
  不计入本地聚合、不拖垮普通聊天流量。
路由层只做源码断言（monitoring_router 导入 src，禁止导入）。
"""

import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.models.operations_model import BackupJob, AlertEvent
from server.services import monitoring_service as ms


@contextmanager
def _temp_env():
    """临时目录 + SQLite 数据库 + 会话 + 监控服务 ctx。"""
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
            "backup_dir": str(root / "saves" / "backups"),
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


def _ok_probe(*args, **kwargs):
    return True


def _timeout_probe(*args, **kwargs):
    raise TimeoutError("deadline exceeded")


def _refused_probe(*args, **kwargs):
    raise ConnectionRefusedError("connection refused")


# 可解析出远端多模态 Base URL 的最小环境（配合注入 probe，不发起真实网络请求）。
_MM_BASE_ENV = {
    "MULTIMODAL_ENABLED": "true",
    "MULTIMODAL_MODE": "remote",
    "MULTIMODAL_KB_API_BASE": "https://mm.example.com/api/v1",
}


# ---- I3.3 远端多模态探针（可注入）----
def _mm_healthy_probe(*args, **kwargs):
    return (True, 200, {"ok": True})


def _mm_degraded_http_probe(*args, **kwargs):
    return (True, 502, {})


def _mm_degraded_payload_probe(*args, **kwargs):
    return (True, 200, {"ok": False})


def _mm_down_probe(*args, **kwargs):
    raise ConnectionError("connection refused")


class SqliteCheckTests(unittest.TestCase):
    def test_sqlite_ok_reports_read_write_and_size(self):
        with _temp_env() as env:
            res = ms.check_sqlite(env["ctx"]["db_path"])
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["read_ok"])
            self.assertTrue(res["write_ok"])
            self.assertGreaterEqual(res["size_bytes"], 0)

    def test_sqlite_missing_file(self):
        res = ms.check_sqlite("/nonexistent/nope.db")
        self.assertEqual(res["status"], "failed")
        self.assertIn("不存在", res["detail"])

    def test_sqlite_readonly_write_fails(self):
        from unittest.mock import Mock

        with _temp_env() as env:
            db_path = env["ctx"]["db_path"]
            read_result = Mock()
            read_result.fetchone.return_value = (1,)

            def fake_connect(*args, **kwargs):
                conn = Mock()
                conn.close = Mock()

                def guarded(sql, *params):
                    upper = sql.strip().upper()
                    if upper.startswith("BEGIN") or "CREATE TABLE" in sql:
                        raise sqlite3.OperationalError("attempt to write a readonly database")
                    return read_result

                conn.execute.side_effect = guarded
                return conn

            with patch.object(ms.sqlite3, "connect", side_effect=fake_connect):
                res = ms.check_sqlite(db_path)
            self.assertEqual(res["status"], "failed")
            self.assertTrue(res["read_ok"])
            self.assertFalse(res["write_ok"])


class DiskAndBackupDirTests(unittest.TestCase):
    def test_disk_ok_reports_usage(self):
        with _temp_env() as env:
            res = ms.check_disk(env["ctx"]["save_dir"])
            self.assertEqual(res["status"], "ok")
            self.assertGreater(res["total_bytes"], 0)
            self.assertIn("free_bytes", res)

    def test_disk_missing_path_failed(self):
        res = ms.check_disk("/nonexistent/dir")
        self.assertEqual(res["status"], "failed")

    def test_backup_dir_writable(self):
        with _temp_env() as env:
            res = ms.check_backup_dir(env["ctx"]["backup_dir"])
            self.assertEqual(res["status"], "ok")
            self.assertTrue(res["writable"])
            self.assertTrue(os.path.isdir(env["ctx"]["backup_dir"]))

    def test_backup_dir_unwritable(self):
        with _temp_env() as env:
            # 把备份目录指到只读文件上，makedirs 会失败
            blocker = str(env["root"] / "blocker")
            with open(blocker, "w") as f:
                f.write("x")
            res = ms.check_backup_dir(os.path.join(blocker, "sub"))
            self.assertEqual(res["status"], "failed")
            self.assertFalse(res["writable"])


class GpuCheckTests(unittest.TestCase):
    def test_gpu_ok_parses_metrics(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="45, 1234, 8192\n")
        with patch.object(ms.subprocess, "run", return_value=fake):
            res = ms.check_gpu()
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["available"])
        self.assertEqual(res["utilization_percent"], 45)
        self.assertEqual(res["vram_used_mb"], 1234)
        self.assertEqual(res["vram_total_mb"], 8192)

    def test_gpu_absent_returns_unavailable(self):
        with patch.object(ms.subprocess, "run", side_effect=FileNotFoundError):
            res = ms.check_gpu()
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(res["available"])

    def test_gpu_timeout(self):
        with patch.object(ms.subprocess, "run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 3)):
            res = ms.check_gpu()
        self.assertEqual(res["status"], "timeout")

    def test_gpu_nonzero_exit_returns_unavailable(self):
        fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="No devices")
        with patch.object(ms.subprocess, "run", return_value=fake):
            res = ms.check_gpu()
        self.assertEqual(res["status"], "unavailable")

    def test_gpu_unparseable_output_failed(self):
        fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="not,a,number\n")
        with patch.object(ms.subprocess, "run", return_value=fake):
            res = ms.check_gpu()
        self.assertEqual(res["status"], "failed")


class MilvusCheckTests(unittest.TestCase):
    def test_milvus_success(self):
        res = ms.check_milvus("http://milvus:19530", probe=_ok_probe)
        self.assertEqual(res["status"], "ok")

    def test_milvus_timeout(self):
        res = ms.check_milvus("http://milvus:19530", probe=_timeout_probe)
        self.assertEqual(res["status"], "timeout")

    def test_milvus_connection_refused(self):
        res = ms.check_milvus("http://milvus:19530", probe=_refused_probe)
        self.assertEqual(res["status"], "failed")
        self.assertIn("refused", res["detail"])


class Neo4jCheckTests(unittest.TestCase):
    def test_neo4j_success(self):
        res = ms.check_neo4j("bolt://localhost:7687", "neo4j", "pw", probe=_ok_probe)
        self.assertEqual(res["status"], "ok")

    def test_neo4j_unconfigured_unavailable(self):
        res = ms.check_neo4j("bolt://localhost:7687", None, None)
        self.assertEqual(res["status"], "unavailable")

    def test_neo4j_timeout(self):
        res = ms.check_neo4j("bolt://localhost:7687", "neo4j", "pw", probe=_timeout_probe)
        self.assertEqual(res["status"], "timeout")

    def test_neo4j_connection_refused(self):
        res = ms.check_neo4j("bolt://localhost:7687", "neo4j", "pw", probe=_refused_probe)
        self.assertEqual(res["status"], "failed")


class LastRecordTests(unittest.TestCase):
    def test_no_backup_unavailable(self):
        with _temp_env() as env:
            res = ms.last_backup(env["db"])
            self.assertEqual(res["status"], "unavailable")

    def test_last_backup_completed_ok(self):
        with _temp_env() as env:
            env["db"].add(BackupJob(filename="a.zip", path="a.zip", status="completed", size_bytes=1))
            env["db"].add(BackupJob(filename="b.zip", path="b.zip", status="failed", size_bytes=2))
            env["db"].commit()
            res = ms.last_backup(env["db"])
            self.assertEqual(res["status"], "failed")  # 最近一条是 failed
            self.assertEqual(res["filename"], "b.zip")

    def test_no_alert_unavailable(self):
        with _temp_env() as env:
            res = ms.last_alert(env["db"])
            self.assertEqual(res["status"], "unavailable")

    def test_last_alert_firing(self):
        with _temp_env() as env:
            env["db"].add(AlertEvent(rule_id=None, event_type="disk_space", severity="warning",
                                     status="firing", message="disk low"))
            env["db"].commit()
            res = ms.last_alert(env["db"])
            self.assertEqual(res["status"], "firing")
            self.assertEqual(res["severity"], "warning")


class AggregateTests(unittest.TestCase):
    def test_health_all_ok(self):
        with _temp_env() as env:
            res = ms.health(env["db"], env["ctx"])
            self.assertEqual(res["status"], "ok")
            self.assertEqual(set(res["checks"]), {"api", "sqlite", "disk", "backup_dir"})

    def test_dependencies_all_ok_with_probes(self):
        with _temp_env() as env, patch.dict(os.environ, _MM_BASE_ENV, clear=True):
            res = ms.dependencies(
                env["db"], env["ctx"],
                milvus_probe=_ok_probe, neo4j_probe=_ok_probe, multimodal_probe=_mm_healthy_probe,
            )
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["dependencies"]["milvus"]["status"], "ok")
            self.assertEqual(res["dependencies"]["neo4j"]["status"], "ok")
            self.assertEqual(res["dependencies"]["multimodal"]["status"], "healthy")

    def test_single_failure_degrades_without_breaking_others(self):
        with _temp_env() as env, patch.dict(os.environ, _MM_BASE_ENV, clear=True):
            no_gpu = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="No devices")
            with patch.object(ms.subprocess, "run", return_value=no_gpu):
                res = ms.dependencies(
                    env["db"], env["ctx"],
                    milvus_probe=_refused_probe, neo4j_probe=_ok_probe, multimodal_probe=_mm_healthy_probe,
                )
            self.assertEqual(res["status"], "degraded")
            self.assertEqual(res["dependencies"]["milvus"]["status"], "failed")
            self.assertEqual(res["dependencies"]["neo4j"]["status"], "ok")
            self.assertEqual(res["dependencies"]["gpu"]["status"], "unavailable")  # GPU 不存在不算 500
            self.assertEqual(res["dependencies"]["multimodal"]["status"], "healthy")

    def test_dependencies_reports_multimodal_when_not_configured(self):
        """I3.3：未配置远端多模态时上报 unavailable，不发网络请求、不计入本地聚合。"""
        with _temp_env() as env, patch.dict(os.environ, {}, clear=True):
            res = ms.dependencies(
                env["db"], env["ctx"], milvus_probe=_ok_probe, neo4j_probe=_ok_probe
            )
        self.assertEqual(res["dependencies"]["multimodal"]["status"], "unavailable")
        self.assertEqual(res["status"], "ok")  # 远端未启用不降级本地状态

    def test_metrics_shape(self):
        with _temp_env() as env:
            res = ms.metrics(env["db"], env["ctx"])
            self.assertIn("gpu", res["metrics"])
            self.assertIn("last_backup", res["metrics"])
            self.assertIn("last_alert", res["metrics"])


class MultimodalCheckTests(unittest.TestCase):
    """I3.3 远端多模态依赖状态：healthy / degraded / down / unavailable。"""

    def test_unavailable_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            res = ms.check_multimodal()
        self.assertEqual(res["status"], "unavailable")

    def test_unavailable_when_explicitly_disabled(self):
        with patch.dict(os.environ, {"MULTIMODAL_ENABLED": "false"}, clear=True):
            res = ms.check_multimodal()
        self.assertEqual(res["status"], "unavailable")

    def test_healthy_when_probe_ok(self):
        res = ms.check_multimodal(base_url="https://mm.example.com", probe=_mm_healthy_probe)
        self.assertEqual(res["status"], "healthy")

    def test_degraded_on_http_error(self):
        res = ms.check_multimodal(base_url="https://mm.example.com", probe=_mm_degraded_http_probe)
        self.assertEqual(res["status"], "degraded")
        self.assertIn("HTTP 502", res["detail"])

    def test_degraded_when_health_not_ok(self):
        res = ms.check_multimodal(base_url="https://mm.example.com", probe=_mm_degraded_payload_probe)
        self.assertEqual(res["status"], "degraded")

    def test_down_on_connection_error(self):
        res = ms.check_multimodal(base_url="https://mm.example.com", probe=_mm_down_probe)
        self.assertEqual(res["status"], "down")

    def test_down_does_not_flip_local_aggregate(self):
        """远端 down 只影响本项，本地聚合保持 ok（I3.3 不使聊天退出流量）。"""
        with _temp_env() as env, patch.dict(os.environ, _MM_BASE_ENV, clear=True):
            res = ms.dependencies(
                env["db"], env["ctx"],
                milvus_probe=_ok_probe, neo4j_probe=_ok_probe, multimodal_probe=_mm_down_probe,
            )
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["dependencies"]["multimodal"]["status"], "down")


class MonitoringRouterSourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.router = (root / "server/routers/monitoring_router.py").read_text(encoding="utf-8")

    def test_endpoints_present(self):
        for route in ('@router.get("/health")', '@router.get("/metrics")', '@router.get("/dependencies")'):
            self.assertIn(route, self.router)

    def test_endpoints_are_sync_def_not_async(self):
        """P1-7：阻塞探测必须交给 FastAPI 线程池，事件循环不被占用。"""
        self.assertIn("def operations_health(", self.router)
        self.assertIn("def operations_metrics(", self.router)
        self.assertIn("def operations_dependencies(", self.router)
        self.assertNotIn("async def operations_health", self.router)
        self.assertNotIn("async def operations_metrics", self.router)
        self.assertNotIn("async def operations_dependencies", self.router)

    def test_prefix_and_superadmin(self):
        self.assertIn('router = APIRouter(prefix="/operations"', self.router)
        self.assertIn("get_superadmin_user", self.router)

    def test_no_remote_multimodal_import(self):
        self.assertNotIn("multimodal_remote", self.router)
        self.assertNotIn("mul_rag", self.router)

    def test_no_secrets_in_router(self):
        self.assertNotIn("password =", self.router)
        self.assertNotIn("api_key", self.router)


if __name__ == "__main__":
    unittest.main()
