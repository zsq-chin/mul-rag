"""阶段 H3：本机功能回归验收测试。

覆盖 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §9 H3 六项。
既有覆盖项引用对应测试，本文件只补新增守卫（H3.1 穷举扫描、H3.5 行为/关闭守卫、
H3.6 报告分类存在性守卫）：

- H3.1 本机功能模块不得为了显示状态而调用远端多模态接口：
    新增「穷举扫描」守卫——server/routers + server/services 下任何模块出现
    multimodal / mul_rag 引用，必须属于下列文档允许清单，否则失败：
      * routers/__init__.py                —— 路由注册中心（必须 include 多模态代理）
      * routers/multimodal_proxy_router.py —— 多模态白名单代理本身
      * routers/chat_router.py             —— 聊天多模态 KB/图片接口（多模态消费方）
      * services/http_clients.py           —— 多模态 HTTP 客户端（共享连接池）
      * services/monitoring_service.py     —— I3.3 指定状态上报：依赖页上报远端多模态
                                              healthy/degraded/down，且从本地聚合剔除，
                                              远端故障不翻转本地状态。
    另用 AST 校验 monitoring_service 的多模态引用只出现在
    _default_multimodal_probe / check_multimodal / dependencies，
    绝不出现在规则评估等本机告警路径。

- H3.2 备份恢复继续保证数据库、配置、日志和知识文件全目标回滚：
    由 test_backup_service.BackupRestoreRollbackTests 覆盖
    （test_config_replace_failure_rolls_back_all_four /
     test_log_copy_failure_rolls_back_all_four /
     test_kb_partial_apply_failure_rolls_back_all_four）。

- H3.3 API Key 与其他秘密在配置、历史、备份与日志中脱敏：
    由 test_config_history.ConfigSanitizerTests / test_history_never_contains_api_keys /
    test_rollback_custom_models_preserves_real_api_key、test_secret_cleanup、
    test_audit_api、test_backup_service::test_manifest_and_config_snapshot_sanitized 覆盖。

- H3.4 上传和图谱导入只接受受控目录内的裸 file ID：
    由 test_upload_service.BareFileIdResolverTests（绝对路径 / 盘符 / UNC / `..` /
    软链接 / 目录一律拒绝）与 test_graph_router_h1（file_path 穿越 / 绝对路径 400）覆盖。

- H3.5 告警探测有真实 I/O 超时，应用关闭不遗留阻塞线程：
    本文件新增：
      * SMTP 探测必须传秒级数值 timeout（行为断言，抓 smtplib.SMTP kwargs）；
      * alert_loop 停止后不再提交新线程（行为断言）；
      * app_lifespan 关闭路径置位 alert_stop、有限等待收尾、超时取消
        （源码守卫，沿用 test_concurrency 读取 main.py 的既有模式）。
    check_sqlite(timeout=2.0) / check_gpu(timeout=3.0) / 循环不阻塞事件循环、
    阻塞期间不堆积轮次 由 test_monitoring_service / test_alert_service 覆盖。

- H3.6 全量测试报告区分 pass/fail/error/skip/environment-missing：
    由 scripts/run_backend_tests.py（P2-2 入口）实现；本文件做存在性守卫。

不依赖数据库 / Milvus / docker；alert_service 不导入 src / db_manager，可安全导入。
"""

import ast
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from server.services import alert_service as asvc

ROOT = Path(__file__).resolve().parents[1]

# H3.1：远端多模态引用的文档允许清单（相对仓库根路径）
ALLOWED_MULTIMODAL_REF_FILES = {
    "server/routers/__init__.py",
    "server/routers/multimodal_proxy_router.py",
    "server/routers/chat_router.py",
    "server/services/http_clients.py",
    "server/services/monitoring_service.py",
    "server/services/alert_service.py",  # J.6：多模态运行健康告警规则（经 monitoring 评估）
}

# H3.1：穷举扫描根目录（本机功能模块所在的引用面）
SCAN_ROOTS = (
    ROOT / "server" / "routers",
    ROOT / "server" / "services",
)


def _functions_referencing(source: str, needle: str) -> list[str]:
    """返回源文件中正文含 needle（小写比较）的函数名列表。"""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            segment = ast.get_source_segment(source, node) or ""
            if needle in segment.lower():
                hits.append(node.name)
    return hits


class H31LocalFeatureIsolationTests(unittest.TestCase):
    """H3.1：本机功能模块不得调用远端多模态来显示状态。"""

    def test_exhaustive_no_local_module_references_remote_multimodal(self):
        """除文档允许清单外，任何 router/service 模块不得出现 multimodal/mul_rag 引用。"""
        violations = []
        for root in SCAN_ROOTS:
            for py in sorted(root.rglob("*.py")):
                rel = py.relative_to(ROOT).as_posix()
                if rel in ALLOWED_MULTIMODAL_REF_FILES:
                    continue
                src = py.read_text(encoding="utf-8", errors="ignore").lower()
                if "multimodal" in src or "mul_rag" in src:
                    violations.append(rel)
        self.assertEqual(
            violations,
            [],
            "本机功能模块不得引用远端多模态（如需新增请先说明理由并加入允许清单）:\n"
            + "\n".join(violations),
        )

    def test_monitoring_multimodal_refs_confined_to_status_reporting(self):
        """monitoring_service 的多模态引用只能出现在 I3.3 状态上报函数内。"""
        src = (ROOT / "server" / "services" / "monitoring_service.py").read_text(
            encoding="utf-8"
        )
        allowed = {
            "_default_multimodal_probe",
            "check_multimodal",
            "check_multimodal_observability",  # J.6：运行健康汇总（供依赖页与告警评估）
            "dependencies",
        }
        hits = set(_functions_referencing(src, "multimodal"))
        unexpected = sorted(hits - allowed)
        self.assertEqual(
            unexpected,
            [],
            "monitoring_service 中多模态引用不得进入本机告警/评估路径: "
            + ", ".join(unexpected),
        )
        self.assertTrue(
            hits, "monitoring_service 必须保留 I3.3 的远端多模态状态上报"
        )


class H35AlertTimeoutAndShutdownTests(unittest.TestCase):
    """H3.5：告警探测真实 I/O 超时，应用关闭不遗留阻塞线程。"""

    def test_smtp_probe_has_real_io_timeout(self):
        """SMTP 探测必须向 smtplib.SMTP 传入秒级数值超时。"""
        captured = {}

        class FakeSMTP:
            def starttls(self):
                pass

            def login(self, *args):
                pass

            def sendmail(self, *args):
                pass

            def quit(self):
                pass

            def close(self):
                pass

        def _factory(*args, **kwargs):
            captured["kwargs"] = kwargs
            return FakeSMTP()

        with patch.object(asvc.smtplib, "SMTP", side_effect=_factory):
            asvc.send_email(
                {
                    "host": "smtp.example.com",
                    "port": 587,
                    "from_addr": "a@b.com",
                    "username": "u",
                    "password": "pw",
                },
                "x@y.com",
                "主题",
                "正文",
            )
        timeout = captured["kwargs"].get("timeout")
        self.assertIsInstance(
            timeout, (int, float), "SMTP 探测必须携带数值 I/O 超时"
        )
        self.assertGreater(
            timeout, 0, "SMTP 探测超时必须为正数（真实 I/O 超时，非阻塞等待）"
        )

    def test_alert_loop_does_not_submit_new_rounds_after_stop(self):
        """stop 置位（关闭路径）后不再提交新线程，不遗留阻塞告警线程。"""
        import threading as _threading

        calls = []
        release = _threading.Event()

        def evaluate():
            calls.append(1)
            release.wait(timeout=5)

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(
                asvc.alert_loop(evaluate, interval=0.01, stop=stop)
            )
            await asyncio.sleep(0.05)  # 第一轮已提交并阻塞
            stop.set()  # 关闭路径：停止调度新轮次
            release.set()  # 放行当前阻塞轮
            await asyncio.wait_for(task, timeout=3)  # 循环应退出
            n_after_exit = len(calls)
            await asyncio.sleep(0.1)  # 等若干个 interval，确认无新轮次
            self.assertEqual(
                len(calls),
                n_after_exit,
                "关闭后不得再提交新的告警评估线程",
            )

        asyncio.run(run())
        self.assertGreaterEqual(len(calls), 1, "关闭前应至少执行过一轮")

    def test_app_lifespan_shutdown_stops_alert_task(self):
        """应用关闭必须置位 alert_stop、有限等待收尾、超时取消，不遗留阻塞线程。

        沿用 test_concurrency 读取 main.py 的既有源码守卫模式。
        """
        src = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
        self.assertIn("alert_stop.set()", src, "关闭时必须通知告警循环停止")
        self.assertIn(
            "wait_for(alert_task, timeout=5)", src, "必须有限等待告警任务收尾"
        )
        self.assertIn(
            "alert_task.cancel()", src, "等待超时后必须取消告警任务"
        )
        self.assertIn("shutdown_runtime()", src, "最后必须执行运行时关闭")


class H36ReportTaxonomyTests(unittest.TestCase):
    """H3.6：全量测试报告区分 pass/fail/error/skip/environment-missing。"""

    def test_backend_runner_distinguishes_report_categories(self):
        """P2-2 入口必须能分类 PASS/FAIL/ERROR/env-missing/product/SKIP。"""
        src = (ROOT / "scripts" / "run_backend_tests.py").read_text(
            encoding="utf-8"
        )
        for marker in ("env-missing", "product", "PASS", "FAIL", "ERROR", "SKIP"):
            self.assertIn(marker, src, f"报告分类缺失分类器 '{marker}'")
        self.assertIn('"skips"', src, "报告必须统计 skip 数量")
        self.assertIn(
            "return 0 if len(product_modules) == 0 else 1",
            src,
            "无产品失败时退出码为 0",
        )


if __name__ == "__main__":
    unittest.main()
