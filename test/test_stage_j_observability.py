"""阶段 J：多模态可观测性、独立限流、短时熔断、缓存与统一错误码验收测试。

覆盖 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §11 七项：
- J.1 每接口请求数 / 成功率 / 状态码 / 超时 / p50/p95/p99 / 响应字节数 / 当前并发；
- J.2 查询扩展记录每次问答实际远端调用数、去重前后结果数、达到预算次数（不含问题文本）；
- J.3 检索 / 图片 / 管理三类独立并发上限，队列满快速失败；
- J.4 连续上游失败短时熔断 → 降级，定期半开探测；普通聊天继续工作并收到明确提示；
- J.5 知识库列表短 TTL 缓存 + 管理变更主动失效；搜索结果缓存按权限/库/文件/版本；
- J.6 供告警评估的运行健康汇总（不可达 / 错误率 / p95 / 超时 / 图片字节 / 池耗尽 / 预算耗尽）；
- J.7 统一错误码 + trace ID（浏览器友好，堆栈只进服务日志）。

测试约束：
- 离线可执行：不 import `src` / `server.routers` / `retriever`（避免触发 Milvus 初始化）；
- 不发起网络请求：探针 / 时钟全部可注入；
- 熔断 / 指标 / 缓存的模块级单例在 setUp 复位，避免测试间污染。
"""

import asyncio
import inspect
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.services import alert_service as asvc
from server.services import monitoring_service as mon
from server.services.concurrency import BoundedGate
from server.utils import multimodal_ops as ops


class _FakeClock:
    """可推进的注入时钟（time.monotonic 同形）。"""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _notify_stub(records):
    def _notify(rule, subject, body, is_resolve):
        records.append((rule.id, subject, body, is_resolve))

    return _notify


class JStageTestCase(unittest.TestCase):
    def setUp(self):
        ops.reset_observability()


# ---------------------------------------------------------------------------
# J.1 每接口指标
# ---------------------------------------------------------------------------


class J1MetricsTests(JStageTestCase):
    def test_metrics_track_counts_success_status_bytes_timeouts(self):
        clock = _FakeClock()
        m = ops.MultimodalMetrics(clock=clock)
        m.record("GET kb/images", duration_ms=100.0, ok=True, status_code=200, bytes_total=1024, upstream=True)
        m.record("GET kb/images", duration_ms=2500.0, ok=False, status_code=504, timeout=True, upstream=True)
        m.record("GET kb/images", duration_ms=800.0, ok=False, status_code=502, upstream=True)
        row = m.snapshot()["GET kb/images"]
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["success"], 1)
        self.assertEqual(row["success_rate"], round(1 / 3, 4))
        self.assertEqual(row["timeouts"], 1)
        self.assertEqual(row["bytes"], 1024)
        self.assertEqual(row["status"], {"2xx": 1, "5xx": 2})

    def test_metrics_latency_percentiles(self):
        m = ops.MultimodalMetrics()
        for lat in (100.0, 800.0, 2500.0):
            m.record("GET kb/list", duration_ms=lat, ok=True, status_code=200)
        lat = m.snapshot()["GET kb/list"]["latency_ms"]
        # 排序 [100, 800, 2500]：p50=800；p95 线性插值 800+(2500-800)*0.9=2330
        self.assertEqual(lat["p50"], 800.0)
        self.assertEqual(lat["p95"], 2330.0)
        self.assertEqual(lat["max"], 2500.0)

    def test_in_flight_and_peak_tracking(self):
        m = ops.MultimodalMetrics()
        m.begin("GET kb/list")
        m.begin("GET kb/list")
        m.end("GET kb/list")
        self.assertEqual(m.snapshot()["GET kb/list"]["in_flight"], 1)
        self.assertEqual(m.snapshot()["GET kb/list"]["peak_in_flight"], 2)
        m.end("GET kb/list")
        self.assertEqual(m.snapshot()["GET kb/list"]["in_flight"], 0)

    def test_window_summary_error_rate_timeouts_p95_and_expiry(self):
        clock = _FakeClock()
        m = ops.MultimodalMetrics(clock=clock)
        for _ in range(4):
            m.record("POST index/search", duration_ms=100.0, ok=True, status_code=200, upstream=True)
        for _ in range(2):
            m.record("POST index/search", duration_ms=3000.0, ok=False, timeout=True, status_code=None, upstream=True)
        clock.advance(100)  # 事件仍落在 600s 窗口内
        s = m.window_summary(now=clock.now, window_seconds=600.0)
        self.assertEqual(s["count"], 6)
        self.assertEqual(s["success_rate"], round(4 / 6, 4))
        self.assertEqual(s["timeouts"], 2)
        self.assertEqual(s["upstream_error_rate"], round(2 / 6, 4))
        self.assertEqual(s["upstream_errors"], 2)
        # 排序 [100,100,100,100,3000,3000]：p95 落在 3000
        self.assertEqual(s["p95_ms"], 3000.0)
        # 时间推进使窗口变空
        clock.advance(700)
        empty = m.window_summary(now=clock.now, window_seconds=600.0)
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["image_bytes"], 0)


# ---------------------------------------------------------------------------
# J.2 查询扩展指标（不含问题文本）
# ---------------------------------------------------------------------------


class J2QueryExpansionTests(JStageTestCase):
    def test_query_expansion_records_counts_only(self):
        clock = _FakeClock()
        q = ops.QueryExpansionMetrics(clock=clock)
        q.record(remote_calls=3, before_dedup=12, after_dedup=7, budget_reached=True, rounds=2)
        q.record(remote_calls=1, before_dedup=4, after_dedup=4, budget_reached=False, rounds=1)
        s = q.summary(now=clock.now, window_seconds=3600.0)
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["remote_calls"], 4)
        self.assertEqual(s["budget_reached"], 1)
        self.assertEqual(s["avg_before_dedup"], 8.0)
        self.assertEqual(s["avg_after_dedup"], 5.5)

    def test_record_api_accepts_only_counts(self):
        """J.2：记录接口只有计数参数，结构上不可能存入问题文本。"""
        params = set(inspect.signature(ops.QueryExpansionMetrics.record).parameters)
        expected = {"self", "remote_calls", "before_dedup", "after_dedup", "budget_reached", "rounds"}
        self.assertEqual(params, expected)
        summary_keys = set(ops.QueryExpansionMetrics.summary.__annotations__) or set()
        # summary 输出键（空窗返回值）不含任何文本字段
        s = ops.QueryExpansionMetrics().summary()
        self.assertNotIn("query", s)
        self.assertNotIn("text", s)

    def test_record_query_expansion_module_entry(self):
        ops.record_query_expansion(remote_calls=2, before_dedup=6, after_dedup=3, budget_reached=False, rounds=1)
        s = ops.query_expansion_metrics.summary()
        self.assertEqual(s["count"], 1)
        self.assertEqual(s["remote_calls"], 2)


# ---------------------------------------------------------------------------
# J.3 三类独立并发上限
# ---------------------------------------------------------------------------


class J3CategoryGateTests(JStageTestCase):
    def test_route_category_mapping(self):
        self.assertEqual(ops.route_category("POST", "index/search"), ops.CATEGORY_RETRIEVAL)
        self.assertEqual(ops.route_category("post", "query"), ops.CATEGORY_RETRIEVAL)
        self.assertEqual(ops.route_category("GET", "kb/images"), ops.CATEGORY_IMAGE)
        self.assertEqual(ops.route_category("GET", "pdf/images"), ops.CATEGORY_IMAGE)
        self.assertEqual(ops.route_category("POST", "kb/upload"), ops.CATEGORY_MANAGE)  # 未知兜底管理

    def test_three_independent_category_gates(self):
        g1 = ops.category_gate(ops.CATEGORY_RETRIEVAL)
        g2 = ops.category_gate(ops.CATEGORY_IMAGE)
        g3 = ops.category_gate(ops.CATEGORY_MANAGE)
        self.assertIsNot(g1, g2)
        self.assertIsNot(g2, g3)
        self.assertIs(g1, ops.category_gate(ops.CATEGORY_RETRIEVAL))  # 同分类同实例
        snap = ops.concurrency_snapshot()
        self.assertEqual(
            set(snap),
            {ops.CATEGORY_RETRIEVAL, ops.CATEGORY_IMAGE, ops.CATEGORY_MANAGE},
        )
        for name, g in snap.items():
            self.assertGreaterEqual(g["limit"], 1)

    def test_gate_exhaustion_fast_fails_with_503(self):
        async def _run():
            g = BoundedGate("mm_manage_test", limit=2, acquire_timeout=0.05)
            await g.__aenter__()
            await g.__aenter__()
            try:
                await g.__aenter__()  # 第 3 个在短超时内快速失败
            except Exception as exc:  # noqa: BLE001
                return exc
            finally:
                await g.__aexit__(None, None, None)
                await g.__aexit__(None, None, None)
            return None

        exc = asyncio.run(_run())
        self.assertIsNotNone(exc, "队列满时应快速失败而非无限等待")
        self.assertEqual(getattr(exc, "status_code", None), 503)
        self.assertEqual(getattr(exc, "headers", {}).get("Retry-After"), "2")


# ---------------------------------------------------------------------------
# J.4 短时熔断
# ---------------------------------------------------------------------------


class J4BreakerTests(JStageTestCase):
    def test_breaker_opens_after_threshold_and_half_open_recovers(self):
        clock = _FakeClock()
        br = ops.MultimodalCircuitBreaker(
            failure_threshold=3, recovery_timeout=5.0, half_open_successes=1, clock=clock
        )
        self.assertTrue(br.should_allow())
        br.record_failure()
        br.record_failure()
        self.assertEqual(br.state, "closed")  # 未达阈值仍放行
        br.record_failure()
        self.assertEqual(br.state, "open")
        self.assertFalse(br.should_allow())  # OPEN 快速失败降级
        clock.advance(5.0)
        self.assertTrue(br.should_allow())  # recovery_timeout 后放行半开探测
        self.assertEqual(br.state, "half_open")
        br.record_success()  # 探测成功恢复
        self.assertEqual(br.state, "closed")

    def test_half_open_probe_failure_reopens(self):
        clock = _FakeClock()
        br = ops.MultimodalCircuitBreaker(
            failure_threshold=2, recovery_timeout=5.0, clock=clock
        )
        br.record_failure()
        br.record_failure()
        clock.advance(5.0)
        self.assertTrue(br.should_allow())
        br.record_failure()  # 半开探测失败 → 立即回 OPEN
        self.assertEqual(br.state, "open")
        self.assertFalse(br.should_allow())

    def test_should_allow_request_module_gate_tracks_breaker(self):
        self.assertTrue(ops.should_allow_request())
        for _ in range(5):
            ops.record_route_result(
                "POST index/search", duration_ms=100.0, ok=False, status_code=502, upstream=True
            )
        self.assertFalse(ops.should_allow_request(), "连续 5 次上游失败后应降级")
        ops.reset_observability()
        self.assertTrue(ops.should_allow_request())

    def test_business_4xx_does_not_trip_breaker(self):
        """J.4：上游 4xx 业务错误视为上游可达，不熔断；5xx/429/503 才熔断。"""
        for _ in range(5):
            ops.record_route_result(
                "POST index/search", duration_ms=100.0, ok=False, status_code=400,
                upstream=True, upstream_ok=ops.upstream_business_error(400),
            )
        self.assertTrue(ops.should_allow_request())
        self.assertEqual(ops.mm_breaker.state, "closed")
        ops.reset_observability()
        for _ in range(5):
            ops.record_route_result(
                "POST index/search", duration_ms=100.0, ok=False, status_code=502,
                upstream=True, upstream_ok=ops.upstream_business_error(502),
            )
        self.assertFalse(ops.should_allow_request())

    def test_degraded_error_message_has_code_and_trace(self):
        """J.4/J.7：降级响应浏览器可读，携带统一错误码与 trace。"""
        exc = ops.mm_error(503, "多模态远端暂不可用（已自动降级，稍后自动恢复）",
                           ops.MM_ERROR_CODES["degraded"], "abcd1234")
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.code, "MM_DEGRADED")
        self.assertIn("MM_DEGRADED", exc.detail)
        self.assertIn("trace=abcd1234", exc.detail)


# ---------------------------------------------------------------------------
# J.5 缓存与主动失效
# ---------------------------------------------------------------------------


class J5CacheTests(JStageTestCase):
    def test_ttl_cache_expiry_and_max_entries(self):
        clock = _FakeClock()
        c = ops.TtlCache(ttl=10.0, clock=clock, max_entries=2)
        c.put("a", 1)
        c.put("b", 2)
        clock.advance(5)  # now=1005
        self.assertEqual(c.get("a"), 1)
        c.put("c", 3)  # 满后淘汰最旧 a
        self.assertEqual(c.size(), 2)
        self.assertIsNone(c.get("a"))
        clock.advance(6)  # now=1011：a/b（到期 1010）过期，c（到期 1015）未过期
        self.assertIsNone(c.get("b"))
        self.assertEqual(c.get("c"), 3)
        clock.advance(5)  # now=1016：c 也超过 TTL
        self.assertIsNone(c.get("c"))

    def test_search_cache_key_identity_and_version(self):
        body = json.dumps({"query": "井身结构设计", "kbId": "kb1", "fileId": "f1", "k": 5}).encode("utf-8")
        k1 = ops.search_cache_key("POST", "index/search", "user1", body)
        self.assertEqual(k1, ops.search_cache_key("POST", "index/search", "user1", body))
        self.assertNotEqual(k1, ops.search_cache_key("POST", "index/search", "user2", body))
        # 键是查询哈希，不落地 query 明文
        self.assertEqual(len(k1), 64)
        self.assertNotIn("井身结构设计", k1)
        # 管理变更后版本自增 → 旧键失效
        k_before = ops.search_cache_key("POST", "query", "user1", body)
        ops.invalidate_on_manage_change()
        k_after = ops.search_cache_key("POST", "query", "user1", body)
        self.assertNotEqual(k_before, k_after)

    def test_search_cache_key_skips_unparseable_or_empty_query(self):
        self.assertIsNone(ops.search_cache_key("POST", "index/search", "u", b"not json"))
        self.assertIsNone(ops.search_cache_key("POST", "index/search", "u", b'{"query":"   "}'))

    def test_is_search_route(self):
        self.assertTrue(ops.is_search_route("POST", "index/search"))
        self.assertTrue(ops.is_search_route("post", "query"))
        self.assertFalse(ops.is_search_route("GET", "kb/list"))

    def test_invalidate_on_manage_change_clears_caches_and_bumps_version(self):
        ops.kb_list_cache.put("k", {"items": []})
        ops.mm_search_cache.put("s", {"results": []})
        v0 = ops.content_version.current
        ops.invalidate_on_manage_change()
        self.assertEqual(ops.kb_list_cache.size(), 0)
        self.assertEqual(ops.mm_search_cache.size(), 0)
        self.assertEqual(ops.content_version.current, v0 + 1)


# ---------------------------------------------------------------------------
# J.6 告警评估输入与规则
# ---------------------------------------------------------------------------


class J6MonitoringAlertTests(JStageTestCase):
    def _mm_probe(self, reachable=True, status=200, ok=True):
        def _probe(base_url, timeout=5.0):
            return (reachable, status, {"ok": ok} if ok else {})
        return _probe

    def test_check_multimodal_observability_states(self):
        self.assertEqual(
            mon.check_multimodal_observability(probe=self._mm_probe(), base_url=None)["status"],
            "unavailable",
        )

        def _down(base_url, timeout=5.0):
            raise ConnectionError("refused")

        r = mon.check_multimodal_observability(probe=_down, base_url="https://mm.example.com")
        self.assertEqual(r["status"], "down")
        self.assertEqual(r["reachability"], "down")

        r = mon.check_multimodal_observability(probe=self._mm_probe(), base_url="https://mm.example.com")
        self.assertEqual(r["status"], "healthy")
        self.assertEqual(r["reachability"], "healthy")

        r = mon.check_multimodal_observability(
            probe=self._mm_probe(status=500), base_url="https://mm.example.com"
        )
        self.assertEqual(r["status"], "degraded")

    def test_breaker_open_flips_observability_to_degraded(self):
        for _ in range(5):
            ops.record_route_result(
                "POST index/search", duration_ms=50.0, ok=False, status_code=502, upstream=True
            )
        r = mon.check_multimodal_observability(probe=self._mm_probe(), base_url="https://mm.example.com")
        self.assertEqual(r["breaker_state"], "open")
        self.assertEqual(r["status"], "degraded")

    def test_dependencies_excludes_multimodal_from_local_aggregate(self):
        """I3.3/J.6：远端多模态（含可观测性汇总）不计入本地聚合。"""
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        ctx = {
            "save_dir": tempfile.gettempdir(),
            "backup_dir": tempfile.gettempdir(),
        }

        def _down(base_url, timeout=5.0):
            raise ConnectionError("refused")

        try:
            with patch.object(mon, "check_sqlite", return_value={"status": "ok"}), \
                 patch.object(mon, "check_disk", return_value={"status": "ok", "used_percent": 20}), \
                 patch.object(mon, "check_backup_dir", return_value={"status": "ok", "writable": True}), \
                 patch.object(mon, "check_gpu", return_value={"status": "ok", "available": True}):
                result = mon.dependencies(
                    session, ctx, timeout=1.0, milvus_probe=lambda *a, **k: True,
                    neo4j_probe=lambda *a, **k: True, gpu_timeout=1.0,
                    multimodal_probe=_down,
                )
            self.assertIn("multimodal_observability", result["dependencies"])
            self.assertIn(
                result["dependencies"]["multimodal_observability"]["status"],
                ("down", "degraded", "unavailable", "healthy"),
            )
            # 远端多模态 down 不得把本地聚合拖成 degraded
            self.assertEqual(result["status"], "ok")
        finally:
            session.close()
            engine.dispose()

    def _fresh_db(self):
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        return engine, sessionmaker(bind=engine)()

    def test_all_mm_alert_rules_fire_on_degraded_window(self):
        """J.6：7 种多模态规则从同一运行健康汇总触发并写事件。"""
        ops.reset_observability()
        engine, session = self._fresh_db()
        ctx = {"save_dir": tempfile.gettempdir()}
        records = []
        # 让 check_multimodal 走到注入探针（healthy）而非“未配置”分支，
        # 以便熔断状态成为唯一降级信号
        saved_env = {
            k: os.environ.get(k)
            for k in ("MULTIMODAL_ENABLED", "MULTIMODAL_KB_API_BASE", "MULTIMODAL_MODE")
        }
        os.environ["MULTIMODAL_ENABLED"] = "true"
        os.environ["MULTIMODAL_KB_API_BASE"] = "https://mm.test.example/api/v1"
        thresholds = {
            "mm_down": None,
            "mm_error_rate": "20",
            "mm_p95": "1000",
            "mm_timeout": "2",
            "mm_image_bytes": "1000000",
            "mm_pool": "2",
            "mm_budget": "1",
        }
        rule_ids = {}
        try:
            for rt, th in thresholds.items():
                rule = asvc.create_rule(
                    session, name=f"j6-{rt}", rule_type=rt, enabled=True,
                    threshold=th, cooldown_seconds=0, notify_email=None,
                )
                rule_ids[rt] = rule.id
            # 制造窗口故障：5 次超时 + 池耗尽 + 大字节量 → 错误率 100%、熔断 OPEN
            for _ in range(5):
                ops.record_route_result(
                    "POST index/search", duration_ms=5000.0, ok=False, timeout=True,
                    status_code=None, upstream=True, pool_exhausted=True, bytes_total=500_000,
                )
            # 查询扩展预算耗尽 ×2
            for _ in range(2):
                ops.record_query_expansion(
                    remote_calls=3, before_dedup=5, after_dedup=2, budget_reached=True, rounds=2
                )

            result = asvc.evaluate_rules(
                session, ctx, now=datetime.now(),
                multimodal_probe=self._mm_probe(), notify=_notify_stub(records),
            )
            fired = set(result["fired"])
            self.assertEqual(len(fired), 7, f"应全部 7 条多模态规则触发: {sorted(fired)}")
            for rt, rid in rule_ids.items():
                self.assertIn(rid, fired, f"规则 {rt} 应在故障窗口触发")
            self.assertEqual(len(records), 7, "每条触发都应回调通知")
            # mm_down 事件消息含熔断提示
            ev = (
                session.query(asvc.AlertEvent)
                .filter_by(rule_id=rule_ids["mm_down"], event_type="trigger")
                .first()
            )
            self.assertIsNotNone(ev)
            self.assertIn("熔断", ev.message or "")
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            session.close()
            engine.dispose()
            ops.reset_observability()

    def test_mm_rules_registered(self):
        for rt in (
            "mm_down", "mm_error_rate", "mm_p95", "mm_timeout",
            "mm_image_bytes", "mm_pool", "mm_budget",
        ):
            self.assertIn(rt, asvc.RULE_TYPES)
            self.assertIn(rt, asvc.MM_RULE_TYPES)


# ---------------------------------------------------------------------------
# J.7 统一错误码
# ---------------------------------------------------------------------------


class J7ErrorCodeTests(JStageTestCase):
    def test_mm_error_detail_stays_browser_friendly_string(self):
        exc = ops.mm_error(503, "多模态远端暂不可用", ops.MM_ERROR_CODES["degraded"], "abc12345def")
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.code, "MM_DEGRADED")
        self.assertEqual(exc.trace_id, "abc12345def")
        self.assertIsInstance(exc.detail, str)
        self.assertIn("MM_DEGRADED", exc.detail)
        self.assertIn("trace=abc12345", exc.detail)  # 只暴露前 8 位

    def test_code_for_status_mapping(self):
        cases = {
            400: "MM_BAD_REQUEST",
            422: "MM_BAD_REQUEST",
            403: "MM_FORBIDDEN",
            404: "MM_NOT_FOUND",
            413: "MM_REQUEST_TOO_LARGE",
            429: "MM_UPSTREAM_RATE_LIMITED",
            503: "MM_NOT_CONFIGURED",
            502: "MM_UPSTREAM_ERROR",
        }
        for status, code in cases.items():
            self.assertEqual(ops.code_for_status(status), code)


if __name__ == "__main__":
    unittest.main()
