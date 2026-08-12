"""阶段 J：多模态可观测性、独立限流、短时熔断、缓存与统一错误码。

对应 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §11 的七项：
- J.1  每接口请求数 / 成功率 / 状态码 / 超时 / p50/p95/p99 / 响应字节数 / 当前并发；
- J.2  查询扩展记录每次问答实际远端调用数、去重前后结果数、达到预算次数（不含问题文本）；
- J.3  检索 / 图片 / 管理三类独立并发上限，队列满快速失败；
- J.4  连续上游失败短时熔断 → 降级，定期半开探测；普通聊天继续工作并收到明确提示；
- J.5  知识库列表短 TTL 缓存 + 管理变更主动失效；搜索结果缓存按权限/库/文件/版本；
- J.6  供告警评估的运行健康汇总（不可达 / 错误率 / p95 / 超时 / 图片字节 / 池耗尽 / 预算耗尽）；
- J.7  统一错误码 + trace ID（浏览器友好，堆栈只进服务日志）。

设计约束：
- 纯策略/状态模块：不 import `src` / `db_manager`，不触发 Milvus/Neo4j 等初始化；
  测试可离线直接导入。
- 所有时钟/阈值可用注入时钟覆盖（clock / now），保证无网络离线单测。
- 熔断/指标写入走锁，读快照线程安全；内存占用有上界（延迟窗口 + 事件环形缓冲）。

多模态代理（server/routers/multimodal_proxy_router.py）与同步检索客户端
（server/utils/multimodal_remote.py）调用本模块记录结果并读取熔断/门控；
监控服务（server/services/monitoring_service.py）读取快照上报与告警评估。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque

from fastapi import HTTPException

from server.services.concurrency import BoundedGate

# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _percentile(sorted_values: list[float], pct: float) -> float:
    """线性插值百分位；空列表返回 0。"""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = k - lo
    if hi == lo:
        return round(sorted_values[lo], 1)
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac, 1)


def _status_bucket(code):
    if code is None:
        return "none"
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return "other"


# ---------------------------------------------------------------------------
# J.1 每接口指标（含事件环形缓冲，供 J.6 窗口评估）
# ---------------------------------------------------------------------------

_MAX_LATENCY_SAMPLES = 500
_EVENT_RING_MAX = 2000


class MultimodalMetrics:
    """每路由多模态请求指标（J.1）。

    - 累计计数 + 延迟窗口（p50/p95/p99，窗口上界 _MAX_LATENCY_SAMPLES）；
    - 事件环形缓冲（最近 _EVENT_RING_MAX 条），供 J.6 按时间窗口评估
      错误率 / p95 / 超时数 / 图片字节 / 池耗尽；
    - 线程安全：代理 async 事件循环、同步检索线程、监控读取并发访问。
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._counts = {}
        self._successes = {}
        self._timeouts = {}
        self._status_buckets = {}
        self._bytes_total = {}
        self._latencies = {}
        self._in_flight = {}
        self._peak_in_flight = {}
        self._ring = deque(maxlen=_EVENT_RING_MAX)

    # -- 生命周期 -----------------------------------------------------------

    def begin(self, route: str) -> None:
        with self._lock:
            self._in_flight[route] = self._in_flight.get(route, 0) + 1
            peak = self._peak_in_flight.get(route, 0)
            if self._in_flight[route] > peak:
                self._peak_in_flight[route] = self._in_flight[route]

    def end(self, route: str) -> None:
        with self._lock:
            if self._in_flight.get(route, 0) > 0:
                self._in_flight[route] -= 1

    def add_bytes(self, route: str, n: int) -> None:
        with self._lock:
            self._bytes_total[route] = self._bytes_total.get(route, 0) + int(n)

    def record(
        self,
        route: str,
        *,
        duration_ms: float,
        ok: bool,
        timeout: bool = False,
        status_code=None,
        bytes_total: int = 0,
        upstream: bool = True,
        pool_exhausted: bool = False,
    ) -> None:
        """记录一次请求结果。

        - upstream=True 时参与熔断判定（ok→成功，否则→失败）；
        - pool_exhausted 标记 J.3 并发上限满的快速失败（供 J.6 池耗尽告警）。
        """
        with self._lock:
            self._counts[route] = self._counts.get(route, 0) + 1
            if ok:
                self._successes[route] = self._successes.get(route, 0) + 1
            if timeout:
                self._timeouts[route] = self._timeouts.get(route, 0) + 1
            if status_code is not None:
                bucket = _status_bucket(status_code)
                self._status_buckets.setdefault(route, {})
                self._status_buckets[route][bucket] = self._status_buckets[route].get(bucket, 0) + 1
            if bytes_total:
                self._bytes_total[route] = self._bytes_total.get(route, 0) + int(bytes_total)
            lat = self._latencies.setdefault(route, [])
            lat.append(float(duration_ms))
            if len(lat) > _MAX_LATENCY_SAMPLES:
                del lat[: len(lat) - _MAX_LATENCY_SAMPLES]
            self._ring.append(
                {
                    "route": route,
                    "ts": self._clock(),
                    "ok": bool(ok),
                    "timeout": bool(timeout),
                    "status_code": status_code,
                    "bytes_total": int(bytes_total),
                    "duration_ms": float(duration_ms),
                    "upstream": bool(upstream),
                    "pool_exhausted": bool(pool_exhausted),
                }
            )

    # -- 快照 ---------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            routes = sorted(
                set(self._counts)
                | set(self._successes)
                | set(self._timeouts)
                | set(self._status_buckets)
                | set(self._bytes_total)
                | set(self._latencies)
                | set(self._in_flight)
                | set(self._peak_in_flight)
            )
            out = {}
            for route in routes:
                count = self._counts.get(route, 0)
                lat = sorted(self._latencies.get(route, []))
                out[route] = {
                    "count": count,
                    "success": self._successes.get(route, 0),
                    "success_rate": round(self._successes.get(route, 0) / count, 4) if count else 0.0,
                    "timeouts": self._timeouts.get(route, 0),
                    "status": dict(self._status_buckets.get(route, {})),
                    "bytes": self._bytes_total.get(route, 0),
                    "latency_ms": {
                        "p50": _percentile(lat, 50),
                        "p95": _percentile(lat, 95),
                        "p99": _percentile(lat, 99),
                        "max": lat[-1] if lat else 0.0,
                    },
                    "in_flight": self._in_flight.get(route, 0),
                    "peak_in_flight": self._peak_in_flight.get(route, 0),
                }
            return out

    def window_summary(self, now=None, window_seconds=600.0) -> dict:
        """最近 window_seconds 秒的窗口统计（J.6 告警评估输入）。"""
        now = self._clock() if now is None else now
        with self._lock:
            events = [e for e in self._ring if now - e["ts"] <= window_seconds]
        total = len(events)
        if total == 0:
            return {
                "window_seconds": window_seconds,
                "count": 0,
                "success_rate": 0.0,
                "error_rate": 0.0,
                "upstream_error_rate": 0.0,
                "timeouts": 0,
                "p95_ms": 0.0,
                "image_bytes": 0,
                "pool_exhausted": 0,
                "upstream_errors": 0,
            }
        durations = sorted(e["duration_ms"] for e in events)
        upstream_total = sum(1 for e in events if e["upstream"])
        upstream_ok = sum(1 for e in events if e["upstream"] and e["ok"])
        return {
            "window_seconds": window_seconds,
            "count": total,
            "success_rate": round(sum(1 for e in events if e["ok"]) / total, 4) if total else 0.0,
            "error_rate": round(1 - sum(1 for e in events if e["ok"]) / total, 4) if total else 0.0,
            "upstream_error_rate": round(1 - upstream_ok / upstream_total, 4) if upstream_total else 0.0,
            "timeouts": sum(1 for e in events if e["timeout"]),
            "p95_ms": _percentile(durations, 95),
            "image_bytes": sum(e["bytes_total"] for e in events),
            "pool_exhausted": sum(1 for e in events if e["pool_exhausted"]),
            "upstream_errors": sum(1 for e in events if not e["ok"] and e["upstream"]),
        }


# ---------------------------------------------------------------------------
# J.4 短时熔断
# ---------------------------------------------------------------------------


class BreakerState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class MultimodalCircuitBreaker:
    """短时熔断：连续上游失败 → OPEN（请求快速失败降级），定期半开探测。

    - CLOSED：正常；每次上游失败 consecutive_failures += 1；
    - OPEN：consecutive_failures 达到 failure_threshold 后进入；请求快速失败；
      recovery_timeout 后放行一次半开探测；
    - HALF_OPEN：探测请求成功达到 half_open_successes 次 → 恢复 CLOSED；
      探测失败 → 立即回到 OPEN。
    """

    def __init__(
        self,
        failure_threshold=5,
        recovery_timeout=30.0,
        half_open_successes=1,
        clock=time.monotonic,
    ):
        self._failure_threshold = max(1, int(failure_threshold))
        self._recovery_timeout = max(0.1, float(recovery_timeout))
        self._half_open_successes = max(1, int(half_open_successes))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_count = 0

    # -- 查询/判定 ----------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def should_allow(self) -> bool:
        """当前请求是否放行（OPEN 下按 recovery_timeout 定期放行半开探测）。"""
        with self._lock:
            if self._state == BreakerState.CLOSED:
                return True
            if self._state == BreakerState.HALF_OPEN:
                return self._half_open_count < self._half_open_successes
            if self._opened_at is not None and (self._clock() - self._opened_at) >= self._recovery_timeout:
                self._state = BreakerState.HALF_OPEN
                self._half_open_count = 0
                return True
            return False

    # -- 结果反馈 -----------------------------------------------------------

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if self._state == BreakerState.HALF_OPEN:
                self._half_open_count += 1
                if self._half_open_count >= self._half_open_successes:
                    self._state = BreakerState.CLOSED
                    self._opened_at = None
                    self._half_open_count = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()
                self._half_open_count = 0
            elif self._state == BreakerState.CLOSED and self._consecutive_failures >= self._failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._state = BreakerState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None
            self._half_open_count = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "consecutive_failures": self._consecutive_failures,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "opened_at": self._opened_at,
            }


# ---------------------------------------------------------------------------
# J.3 三类独立并发上限
# ---------------------------------------------------------------------------

CATEGORY_RETRIEVAL = "retrieval"
CATEGORY_IMAGE = "image"
CATEGORY_MANAGE = "manage"

# 检索类：喂给问答/检索页的读接口（KB 列表、搜索、状态等）。
_RETRIEVAL_ROUTES = {
    ("POST", "index/search"),
    ("POST", "query"),
    ("GET", "health"),
    ("GET", "kb/list"),
    ("GET", "kb/files"),
    ("GET", "pdf/status"),
    ("GET", "index/chunks"),
    ("GET", "index/chunks/stats"),
    ("GET", "extraction/status"),
    ("GET", "extraction/check_filename"),
    ("GET", "preprocess/methods"),
}

# 图片/内容类：大流量慢传输（缩略图/原图/页图/表格/CSV 等）。
_IMAGE_ROUTES = {
    ("GET", "kb/images"),
    ("GET", "kb/file/dataframe"),
    ("GET", "kb/file/content"),
    ("GET", "file-manager/wells"),
    ("GET", "pdf/images_list"),
    ("GET", "pdf/image_summaries"),
    ("GET", "pdf/chunk"),
    ("GET", "kb/file/original"),
    ("GET", "pdf/images"),
    ("GET", "pdf/page"),
    ("GET", "extraction/image"),
    ("GET", "extraction/content"),
    ("GET", "preprocess/workbench/dataframe"),
    ("GET", "preprocess/report"),
    ("GET", "preprocess/dataframe"),
    ("GET", "preprocess/workbench/download"),
    ("GET", "preprocess/workbench/artifact/download"),
}


def route_category(method: str, path: str) -> str:
    """把白名单路由映射到 J.3 三类之一；未知路由兜底为管理类。"""
    key = (str(method).upper(), path)
    if key in _RETRIEVAL_ROUTES:
        return CATEGORY_RETRIEVAL
    if key in _IMAGE_ROUTES:
        return CATEGORY_IMAGE
    return CATEGORY_MANAGE


def _category_gate(name: str, default_limit: int, default_acquire: float) -> BoundedGate:
    return BoundedGate(
        name,
        limit=_env_int(f"MM_{name.upper()}_CONCURRENCY", default_limit),
        acquire_timeout=_env_float(f"MM_{name.upper()}_ACQUIRE_TIMEOUT", default_acquire),
    )


# 三类独立并发上限（J.3）：队列满在短超时内快速失败，不让慢图片占满检索连接。
multimodal_retrieval_gate = _category_gate("mm_retrieval", 6, 0.3)
multimodal_image_gate = _category_gate("mm_image", 4, 0.2)
multimodal_manage_gate = _category_gate("mm_manage", 2, 0.2)

_CATEGORY_GATES = {
    CATEGORY_RETRIEVAL: multimodal_retrieval_gate,
    CATEGORY_IMAGE: multimodal_image_gate,
    CATEGORY_MANAGE: multimodal_manage_gate,
}


def category_gate(category: str) -> BoundedGate:
    return _CATEGORY_GATES.get(category, multimodal_manage_gate)


def concurrency_snapshot() -> dict:
    return {
        name: {"limit": gate.limit, "in_use": gate.in_use}
        for name, gate in (
            (CATEGORY_RETRIEVAL, multimodal_retrieval_gate),
            (CATEGORY_IMAGE, multimodal_image_gate),
            (CATEGORY_MANAGE, multimodal_manage_gate),
        )
    }


# ---------------------------------------------------------------------------
# J.5 知识库列表 TTL 缓存 + 内容版本 + 搜索结果缓存
# ---------------------------------------------------------------------------


class TtlCache:
    """线程安全短 TTL 内存缓存（KB 列表 / 搜索结果共用）。"""

    def __init__(self, ttl=30.0, clock=time.monotonic, max_entries=512):
        self._ttl = float(ttl)
        self._clock = clock
        self._max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._entries = {}

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if self._clock() >= expires_at:
                self._entries.pop(key, None)
                return None
            return payload

    def put(self, key, payload):
        with self._lock:
            if len(self._entries) >= self._max_entries:
                # 简单 FCFS 淘汰最旧条目，保证内存上界
                try:
                    oldest = min(self._entries, key=lambda k: self._entries[k][0])
                    self._entries.pop(oldest, None)
                except ValueError:
                    pass
            self._entries[key] = (self._clock() + self._ttl, payload)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)


class ContentVersion:
    """管理变更版本号：任何管理写操作成功后自增，使旧缓存键失效。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._version = 0

    def bump(self) -> int:
        with self._lock:
            self._version += 1
            return self._version

    @property
    def current(self) -> int:
        with self._lock:
            return self._version

    def reset(self):
        with self._lock:
            self._version = 0


# 模块级单例
mm_metrics = MultimodalMetrics()
mm_breaker = MultimodalCircuitBreaker()
content_version = ContentVersion()
# 知识库列表：短 TTL（默认 30s），管理变更后主动失效（J.5）
kb_list_cache = TtlCache(ttl=_env_float("MM_KB_LIST_CACHE_TTL_SECONDS", 30.0))
# 搜索结果：短 TTL（默认 15s），键含 权限/库/文件/版本（J.5）
mm_search_cache = TtlCache(ttl=_env_float("MM_SEARCH_CACHE_TTL_SECONDS", 15.0))

_SEARCH_ROUTES = {("POST", "index/search"), ("POST", "query")}


def is_search_route(method: str, path: str) -> bool:
    return (str(method).upper(), path) in _SEARCH_ROUTES


def search_cache_key(method: str, path: str, permission: str, body_bytes) -> str | None:
    """搜索结果缓存键：权限 + 库 ID + 文件 ID + k + 内容版本 + 查询哈希（J.5）。

    query 原文只用于哈希，不落地明文；请求体不可解析/无查询时不缓存。
    """
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    query = str(payload.get("query") or "").strip()
    if not query:
        return None
    identity = "|".join(
        [
            str(method).upper(),
            path,
            str(permission),
            str(payload.get("kbId") or ""),
            str(payload.get("fileId") or ""),
            str(payload.get("k") or ""),
            str(content_version.current),
            query,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def invalidate_on_manage_change() -> None:
    """管理写操作成功后调用：清 KB 列表缓存、清搜索缓存、自增内容版本（J.5）。"""
    kb_list_cache.clear()
    mm_search_cache.clear()
    content_version.bump()


# ---------------------------------------------------------------------------
# J.2 查询扩展指标（每次问答，不含问题文本）
# ---------------------------------------------------------------------------

_MAX_QUERY_EXPANSION_ENTRIES = 500


class QueryExpansionMetrics:
    """查询扩展每次问答的指标记录（J.2）。

    记录实际远端调用数、去重前后结果数、达到预算次数、轮数；**绝不记录
    完整问题文本**，键中只出现无意义序号。
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries = deque(maxlen=_MAX_QUERY_EXPANSION_ENTRIES)

    def record(
        self,
        *,
        remote_calls: int,
        before_dedup: int,
        after_dedup: int,
        budget_reached: bool,
        rounds: int,
    ) -> None:
        with self._lock:
            self._entries.append(
                {
                    "ts": self._clock(),
                    "remote_calls": int(remote_calls),
                    "before_dedup": int(before_dedup),
                    "after_dedup": int(after_dedup),
                    "budget_reached": bool(budget_reached),
                    "rounds": int(rounds),
                }
            )

    def summary(self, now=None, window_seconds=3600.0) -> dict:
        now = self._clock() if now is None else now
        with self._lock:
            entries = [e for e in self._entries if now - e["ts"] <= window_seconds]
        if not entries:
            return {
                "window_seconds": window_seconds,
                "count": 0,
                "remote_calls": 0,
                "budget_reached": 0,
                "avg_before_dedup": 0,
                "avg_after_dedup": 0,
            }
        return {
            "window_seconds": window_seconds,
            "count": len(entries),
            "remote_calls": sum(e["remote_calls"] for e in entries),
            "budget_reached": sum(1 for e in entries if e["budget_reached"]),
            "avg_before_dedup": round(sum(e["before_dedup"] for e in entries) / len(entries), 1),
            "avg_after_dedup": round(sum(e["after_dedup"] for e in entries) / len(entries), 1),
        }


query_expansion_metrics = QueryExpansionMetrics()


def record_query_expansion(
    *,
    remote_calls: int,
    before_dedup: int,
    after_dedup: int,
    budget_reached: bool,
    rounds: int,
) -> None:
    """供检索器在每次问答的多模态扩展结束后调用（J.2）。"""
    query_expansion_metrics.record(
        remote_calls=remote_calls,
        before_dedup=before_dedup,
        after_dedup=after_dedup,
        budget_reached=budget_reached,
        rounds=rounds,
    )


# ---------------------------------------------------------------------------
# J.7 统一错误码
# ---------------------------------------------------------------------------

MM_ERROR_CODES = {
    "bad_request": "MM_BAD_REQUEST",
    "forbidden": "MM_FORBIDDEN",
    "not_found": "MM_NOT_FOUND",
    "request_too_large": "MM_REQUEST_TOO_LARGE",
    "not_configured": "MM_NOT_CONFIGURED",
    "gate_busy": "MM_GATE_BUSY",
    "degraded": "MM_DEGRADED",
    "timeout": "MM_TIMEOUT",
    "upstream": "MM_UPSTREAM_ERROR",
    "upstream_rate_limited": "MM_UPSTREAM_RATE_LIMITED",
    "response_too_large": "MM_RESPONSE_TOO_LARGE",
    "bad_response": "MM_BAD_RESPONSE",
}


def code_for_status(status: int) -> str:
    """HTTP 状态 → 统一错误码（未显式指定时的兜底映射）。"""
    if status == 400 or status == 422:
        return MM_ERROR_CODES["bad_request"]
    if status == 403:
        return MM_ERROR_CODES["forbidden"]
    if status == 404:
        return MM_ERROR_CODES["not_found"]
    if status == 413:
        return MM_ERROR_CODES["request_too_large"]
    if status == 429:
        return MM_ERROR_CODES["upstream_rate_limited"]
    if status == 503:
        return MM_ERROR_CODES["not_configured"]
    return MM_ERROR_CODES["upstream"]


def mm_error(status_code: int, message: str, code: str, trace_id: str, headers=None) -> HTTPException:
    """构造带统一错误码与 trace ID 的 HTTPException（J.7）。

    detail 保持浏览器可读的字符串（含 code 与 trace 前缀），堆栈只进服务日志。
    """
    exc = HTTPException(
        status_code=status_code,
        detail=f"{message}（{code}·trace={str(trace_id)[:8]}）",
        headers=headers,
    )
    exc.code = code
    exc.trace_id = trace_id
    return exc


# ---------------------------------------------------------------------------
# 对外统一入口
# ---------------------------------------------------------------------------

# J.6 告警窗口（秒）
_ALERT_WINDOW_SECONDS = _env_float("MM_ALERT_WINDOW_SECONDS", 600.0)


def should_allow_request() -> bool:
    """代理/检索客户端在发起上游请求前调用：熔断 OPEN 时返回 False（J.4）。"""
    return mm_breaker.should_allow()


def upstream_business_error(status: int) -> bool:
    """上游 4xx 业务错误（非 429/503）视为“上游可达/健康”，熔断判定为成功。

    429/503（上游过载/显式不可用）与 5xx/超时/传输错误才计入熔断失败。
    """
    return 400 <= status < 500 and status not in (429, 503)


def record_route_result(
    route: str,
    *,
    duration_ms: float,
    ok: bool,
    timeout: bool = False,
    status_code=None,
    bytes_total: int = 0,
    upstream: bool = True,
    pool_exhausted: bool = False,
    upstream_ok: bool | None = None,
) -> None:
    """记录一次请求结果并反馈熔断（upstream 触达才算熔断失败）。

    ``upstream_ok``：None（默认）时按 ``ok`` 判定熔断；显式给出时
    （上游触达且为 4xx 业务错误）用其判定熔断——业务错误不熔断，
    5xx/429/503/超时/传输错误才熔断。
    """
    mm_metrics.record(
        route,
        duration_ms=duration_ms,
        ok=ok,
        timeout=timeout,
        status_code=status_code,
        bytes_total=bytes_total,
        upstream=upstream,
        pool_exhausted=pool_exhausted,
    )
    if upstream:
        breaker_ok = ok if upstream_ok is None else upstream_ok
        if breaker_ok:
            mm_breaker.record_success()
        else:
            mm_breaker.record_failure()


def degraded_summary(now=None, window_seconds=None) -> dict:
    """J.6 供告警评估：熔断状态 + 最近窗口指标 + 并发 + 预算耗尽汇总。"""
    if window_seconds is None:
        window_seconds = _ALERT_WINDOW_SECONDS
    return {
        "breaker_state": mm_breaker.state,
        "breaker_consecutive_failures": mm_breaker.consecutive_failures,
        "reachability": None,  # 由监控服务注入（网络探针结果）
        "reachability_detail": "",
        **mm_metrics.window_summary(now=now, window_seconds=window_seconds),
        "concurrency": concurrency_snapshot(),
        "query_expansion": query_expansion_metrics.summary(now=now, window_seconds=window_seconds),
    }


def snapshot_observability() -> dict:
    """可观测性总快照（运维/依赖页展示，纯内存读取，无网络）。"""
    return {
        "metrics": mm_metrics.snapshot(),
        "breaker": mm_breaker.snapshot(),
        "concurrency": concurrency_snapshot(),
        "query_expansion": query_expansion_metrics.summary(),
        "caches": {
            "kb_list_entries": kb_list_cache.size(),
            "search_cache_entries": mm_search_cache.size(),
            "content_version": content_version.current,
        },
        "alarm_window_seconds": _ALERT_WINDOW_SECONDS,
    }


def reset_observability() -> None:
    """测试隔离：清空全部运行时状态（指标/熔断/缓存/版本/查询扩展）。"""
    with mm_metrics._lock:
        mm_metrics._counts.clear()
        mm_metrics._successes.clear()
        mm_metrics._timeouts.clear()
        mm_metrics._status_buckets.clear()
        mm_metrics._bytes_total.clear()
        mm_metrics._latencies.clear()
        mm_metrics._in_flight.clear()
        mm_metrics._peak_in_flight.clear()
        mm_metrics._ring.clear()
    mm_breaker.reset()
    kb_list_cache.clear()
    mm_search_cache.clear()
    content_version.reset()
    with query_expansion_metrics._lock:
        query_expansion_metrics._entries.clear()
