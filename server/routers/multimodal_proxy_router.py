"""多模态远端管理代理：固定白名单 + 显式权限 + 上传/响应边界。

B3：删除 `@api_route('/{path:path}')` 全路径通用代理，改为按
`server.utils.multimodal_remote.MULTIMODAL_PROXY_WHITELIST` 显式注册每条接口。
每条路由显式定义权限、请求模型、超时、响应类型和大小限制；检索接口允许任意
已登录用户，其余管理接口仅超级管理员。非白名单路径由 FastAPI 直接 404。
"""

import asyncio
import json
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile

from server.models.user_model import User
from server.services.concurrency import upstream_proxy_gate
from server.services.http_clients import get_multimodal_client
from server.utils.auth_middleware import get_required_user, get_superadmin_user
from server.utils.multimodal_ops import (
    CATEGORY_MANAGE,
    MM_ERROR_CODES,
    category_gate,
    code_for_status,
    invalidate_on_manage_change,
    is_search_route,
    kb_list_cache,
    mm_error,
    mm_metrics,
    mm_search_cache,
    record_route_result,
    route_category,
    search_cache_key,
    should_allow_request,
    upstream_business_error,
)
from server.utils.multimodal_remote import (
    BODY_JSON,
    BODY_MULTIPART,
    BODY_NONE,
    MAX_JSON_BODY_BYTES,
    MAX_STREAM_BYTES,
    PERMISSION_READ,
    RESPONSE_STREAM,
    MultimodalConfigError,
    MultimodalPaginationError,
    MultimodalUploadError,
    accumulate_bounded_bytes,
    build_multimodal_remote_url,
    build_service_auth_headers,
    filter_multimodal_proxy_headers,
    filter_multimodal_response_headers,
    format_redacted_upstream_error,
    get_multimodal_api_base,
    map_upstream_proxy_status,
    new_multimodal_trace_id,
    normalize_multimodal_image_page,
    route_spec_for,
    validate_proxy_identifier_params,
    validate_stream_content_type,
    validate_upload_metadata,
    whitelisted_proxy_routes,
)
from src.utils.logging_config import logger

multimodal = APIRouter(prefix="/multimodal")

# JSON 响应后处理：kb/images 需要把远端分页/未分页目录统一为 items/page/pageSize/total。
_JSON_NORMALIZERS: dict[tuple[str, str], Any] = {
    ("GET", "kb/images"): normalize_multimodal_image_page,
}

# J.5：知识库列表走短 TTL 缓存的路由（管理变更后主动失效）
_KB_LIST_ROUTES = {"kb/list", "kb/files"}


def _route_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def _kb_list_cache_key(request: Request, spec: Any) -> str:
    params = "&".join(f"{k}={v}" for k, v in sorted(request.query_params.multi_items()))
    return f"{spec.method} {spec.path}?{params}"


class _Gates:
    """J.3 组合门控：先获取分类门（短超时快速失败），再获取全局代理门。

    分类门满时的 503 标记 ``_mm_pool_exhausted``，供 J.6 池耗尽告警统计；
    该 503 属于本机限流，不参与熔断（上游未触达）。
    """

    __slots__ = ("_gates",)

    def __init__(self, category: str):
        self._gates = (category_gate(category), upstream_proxy_gate)

    async def __aenter__(self):
        first = self._gates[0]
        try:
            await first.__aenter__()
        except HTTPException as exc:
            exc._mm_pool_exhausted = True
            raise
        try:
            await self._gates[1].__aenter__()
        except BaseException:
            await first.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        for gate in reversed(self._gates):
            await gate.__aexit__(exc_type, exc_val, exc_tb)


def _upstream_contact(exc: HTTPException, *, status_code: int) -> HTTPException:
    """标记异常确实触达了上游（熔断据此判定；上游 4xx 业务错误不算熔断失败）。"""
    exc._mm_upstream_touched = True
    exc._mm_upstream_status = status_code
    return exc


def _elapsed_ms(t0: float) -> float:
    return (time.monotonic() - t0) * 1000.0


def _int_query(request: Request, name: str, default: int) -> int:
    raw = request.query_params.get(name)
    try:
        return int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _upload_file_size(file_obj: Any) -> int:
    file_obj.seek(0, 2)
    size = file_obj.tell()
    return size


def _permission_dependencies(permission: str):
    if permission == PERMISSION_READ:
        return [Depends(get_required_user)]
    return [Depends(get_superadmin_user)]


def _raise_proxy_error(exc: BaseException, spec: Any, trace_id: str, t0: float) -> None:
    """把上游/构建异常统一映射为 HTTP 4xx/5xx（B3.7），日志只含脱敏字段。

    J.7：统一错误码 + trace ID，detail 浏览器可读，堆栈只进服务日志。
    """
    if isinstance(exc, HTTPException):
        # 未触达上游的本地错误（build_request 校验等），由边界统一附码
        raise exc
    if isinstance(exc, MultimodalUploadError):
        raise mm_error(
            exc.status_code, exc.message, MM_ERROR_CODES["bad_request"], trace_id
        ) from exc
    if isinstance(exc, MultimodalPaginationError):
        logger.error(
            format_redacted_upstream_error(trace_id, spec.path, 502, _elapsed_ms(t0), "PaginationContract")
        )
        raise _upstream_contact(
            mm_error(502, f"{exc}", MM_ERROR_CODES["upstream"], trace_id),
            status_code=502,
        ) from exc
    if isinstance(exc, httpx.TimeoutException):
        logger.error(format_redacted_upstream_error(trace_id, spec.path, None, _elapsed_ms(t0), type(exc).__name__))
        raised = mm_error(
            504, "多模态远端连接/读取超时", MM_ERROR_CODES["timeout"], trace_id
        )
        raised._mm_timeout = True
        raise _upstream_contact(raised, status_code=None) from exc
    logger.error(format_redacted_upstream_error(trace_id, spec.path, None, _elapsed_ms(t0), type(exc).__name__))
    raise _upstream_contact(
        mm_error(502, "多模态远端不可用", MM_ERROR_CODES["upstream"], trace_id),
        status_code=None,
    ) from exc


def _raise_mapped_upstream_error(response: httpx.Response, spec: Any, trace_id: str, t0: float) -> None:
    status = response.status_code
    mapped_status, mapped_message = map_upstream_proxy_status(status)
    logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "HTTPError"))
    headers = None
    if status in (429, 503):
        retry_after = response.headers.get("retry-after")
        if retry_after:
            headers = {"Retry-After": retry_after}
    code = (
        MM_ERROR_CODES["upstream_rate_limited"]
        if status in (429, 503)
        else MM_ERROR_CODES["upstream"]
    )
    raise _upstream_contact(
        mm_error(mapped_status, mapped_message, code, trace_id, headers=headers),
        status_code=status,
    )


async def _build_upstream_request(
    client: httpx.AsyncClient,
    request: Request,
    remote_url: str,
    spec: Any,
    headers: dict[str, str],
    timeout: httpx.Timeout,
    json_body_override: bytes | None = None,
) -> httpx.Request:
    """构造发往远端的请求：JSON 请求体限体积；multipart 流式解析并校验后重编码。

    httpx 0.28 的 send() 不接受 timeout，必须通过 build_request 注入每个请求。
    ``json_body_override`` 供 J.5 搜索结果缓存复用已读取的 JSON 请求体。
    """
    request_headers = filter_multimodal_proxy_headers(dict(request.headers))
    params = list(request.query_params.multi_items())
    try:
        validate_proxy_identifier_params(params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if spec.body == BODY_MULTIPART:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except (TypeError, ValueError):
                declared = None
            if declared is not None and declared > spec.max_total_bytes + 64 * 1024:
                raise HTTPException(status_code=413, detail="上传总大小超过限制")

        try:
            form = await request.form(
                max_files=spec.max_files,
                max_fields=64,
                max_part_size=spec.max_file_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是有效的 multipart/form-data") from exc

        file_metas: list[tuple[str | None, str | None, int]] = []
        data: dict[str, str] = {}
        files: list[tuple[str, tuple[str, Any, str]]] = []
        for key, value in form.multi_items():
            if isinstance(value, UploadFile):
                filename = value.filename or key
                size = _upload_file_size(value.file)
                value.file.seek(0)
                file_metas.append((filename, value.content_type, size))
                files.append((key, (filename, value.file, value.content_type or "application/octet-stream")))
            else:
                data[key] = str(value)
        validate_upload_metadata(file_metas, spec)

        request_headers.pop("content-type", None)
        request_headers.pop("content-length", None)
        return client.build_request(
            spec.method, remote_url, params=params, headers=request_headers,
            data=data, files=files, timeout=timeout,
        )

    if spec.body == BODY_JSON:
        if json_body_override is not None:
            body = json_body_override
        else:
            body = await accumulate_bounded_bytes(request.stream(), MAX_JSON_BODY_BYTES)
        if body is None:
            raise HTTPException(status_code=413, detail="多模态请求体超过体积限制")
        request_headers["content-type"] = "application/json"
        request_headers.pop("content-length", None)
        return client.build_request(
            spec.method, remote_url, params=params, headers=request_headers,
            content=body or b"{}", timeout=timeout,
        )

    return client.build_request(
        spec.method, remote_url, params=params, headers=request_headers, timeout=timeout,
    )


async def _proxy_json(
    request: Request,
    remote_url: str,
    spec: Any,
    headers: dict[str, str],
    trace_id: str,
    timeout: httpx.Timeout,
    json_body_override: bytes | None = None,
) -> Response:
    client = get_multimodal_client()
    t0 = time.monotonic()
    route = _route_key(spec.method, spec.path)
    try:
        async with _Gates(route_category(spec.method, spec.path)):
            upstream_request = await _build_upstream_request(
                client, request, remote_url, spec, headers, timeout,
                json_body_override=json_body_override,
            )
            response = await client.send(upstream_request, stream=True)
            try:
                if response.is_redirect:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "Redirect"))
                    raise _upstream_contact(
                        HTTPException(status_code=502, detail=f"多模态远端跳转已被拒绝（trace={trace_id[:8]}）"),
                        status_code=response.status_code,
                    )
                if response.status_code >= 400:
                    _raise_mapped_upstream_error(response, spec, trace_id, t0)

                body = await accumulate_bounded_bytes(response.aiter_bytes(), spec.max_response_bytes)
                if body is None:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "ResponseTooLarge"))
                    raise _upstream_contact(
                        HTTPException(status_code=502, detail=f"多模态远端响应超过体积限制（trace={trace_id[:8]}）"),
                        status_code=response.status_code,
                    )
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "NonJSONResponse"))
                    raise _upstream_contact(
                        HTTPException(status_code=502, detail=f"多模态远端返回非 JSON 响应（trace={trace_id[:8]}）"),
                        status_code=response.status_code,
                    ) from exc

                normalizer = _JSON_NORMALIZERS.get((spec.method, spec.path))
                if normalizer is not None:
                    payload = normalizer(
                        payload,
                        page=_int_query(request, "page", 1),
                        page_size=_int_query(request, "pageSize", 24),
                    )
                # J.1 响应字节数
                mm_metrics.add_bytes(route, len(body))
                # J.5 写入搜索结果/知识库列表缓存
                skey = getattr(request.state, "_mm_search_cache_key", None)
                if skey is not None:
                    mm_search_cache.put(skey, payload)
                kbkey = getattr(request.state, "_mm_kb_cache_key", None)
                if kbkey is not None:
                    kb_list_cache.put(kbkey, payload)
                return JSONResponse(content=payload, status_code=response.status_code)
            finally:
                await response.aclose()
    except HTTPException:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _raise_proxy_error(exc, spec, trace_id, t0)


async def _proxy_stream(
    request: Request,
    remote_url: str,
    spec: Any,
    headers: dict[str, str],
    trace_id: str,
    timeout: httpx.Timeout,
) -> Response:
    client = get_multimodal_client()
    t0 = time.monotonic()
    route = _route_key(spec.method, spec.path)
    gates = _Gates(route_category(spec.method, spec.path))
    await gates.__aenter__()  # 队列满抛 503 HTTPException（分类门标记 _mm_pool_exhausted）
    response: httpx.Response | None = None
    try:
        upstream_request = await _build_upstream_request(client, request, remote_url, spec, headers, timeout)
        response = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        await gates.__aexit__(None, None, None)
        raise
    except Exception as exc:
        await gates.__aexit__(type(exc), exc, exc.__traceback__)
        _raise_proxy_error(exc, spec, trace_id, t0)

    status = response.status_code
    if status == 304:
        headers_out = filter_multimodal_response_headers(response.headers)
        await response.aclose()
        await gates.__aexit__(None, None, None)
        return Response(status_code=304, headers=headers_out)

    if response.is_redirect or status >= 400:
        await response.aclose()
        await gates.__aexit__(None, None, None)
        if response.is_redirect:
            logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "Redirect"))
            raise _upstream_contact(
                HTTPException(status_code=502, detail=f"多模态远端跳转已被拒绝（trace={trace_id[:8]}）"),
                status_code=status,
            )
        _raise_mapped_upstream_error(response, spec, trace_id, t0)

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if not validate_stream_content_type(content_type):
        await response.aclose()
        await gates.__aexit__(None, None, None)
        logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "BadContentType"))
        raise _upstream_contact(
            HTTPException(status_code=502, detail=f"多模态远端返回非预期 Content-Type（trace={trace_id[:8]}）"),
            status_code=status,
        )

    declared_length: int | None = None
    raw_length = response.headers.get("content-length")
    if raw_length:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError):
            declared_length = None
    if declared_length is not None and declared_length > MAX_STREAM_BYTES:
        await response.aclose()
        await gates.__aexit__(None, None, None)
        logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "ResponseTooLarge"))
        raise _upstream_contact(
            HTTPException(status_code=502, detail=f"多模态远端响应超过体积限制（trace={trace_id[:8]}）"),
            status_code=status,
        )

    async def _stream():
        total = 0
        try:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    return
                mm_metrics.add_bytes(route, len(chunk))  # J.1 实际传输字节数
                yield chunk
        except (httpx.HTTPError, asyncio.CancelledError):
            # 客户端断开或上游读取失败：停止，不把断流当 500
            return
        finally:
            try:
                await response.aclose()
            finally:
                await gates.__aexit__(None, None, None)

    return StreamingResponse(
        _stream(),
        status_code=status,
        media_type=content_type or "application/octet-stream",
        headers=filter_multimodal_response_headers(response.headers),
    )


def _breaker_ok_for_upstream_status(status: int) -> bool:
    """上游 4xx 业务错误视为“上游可达/健康”（熔断成功）；429/503/5xx 视为失败。"""
    return upstream_business_error(status)


def _record_boundary_result(
    route: str,
    t0: float,
    *,
    exc: HTTPException | None = None,
    status_code: int | None = None,
) -> None:
    """J.1/J.4：在代理边界统一记录一次请求结果（含熔断反馈）。

    异常路径按 ``_mm_upstream_touched`` / ``_mm_upstream_status`` /
    ``_mm_timeout`` / ``_mm_pool_exhausted`` 判定是否计入熔断与池耗尽；
    本机限流/未配置 503 不计入熔断（上游未触达）。
    """
    if exc is not None:
        status_code = exc.status_code
        upstream = bool(getattr(exc, "_mm_upstream_touched", False))
        upstream_status = getattr(exc, "_mm_upstream_status", None)
        timeout = bool(getattr(exc, "_mm_timeout", False))
        pool = bool(getattr(exc, "_mm_pool_exhausted", False))
        ok = status_code is not None and 200 <= status_code < 300
        upstream_ok = None
        if upstream and upstream_status is not None:
            upstream_ok = _breaker_ok_for_upstream_status(upstream_status)
        record_route_result(
            route,
            duration_ms=_elapsed_ms(t0),
            ok=ok,
            timeout=timeout,
            status_code=upstream_status if upstream_status is not None else status_code,
            upstream=upstream,
            pool_exhausted=pool,
            upstream_ok=upstream_ok,
        )
        return
    ok = status_code is not None and 200 <= status_code < 300
    record_route_result(
        route,
        duration_ms=_elapsed_ms(t0),
        ok=ok,
        status_code=status_code,
        upstream=True,
        upstream_ok=ok,
    )


def _attach_code_trace(exc: HTTPException, trace_id: str) -> HTTPException:
    """J.7：给未带统一错误码的 HTTPException 附加 code + trace ID（detail 保持字符串）。"""
    if getattr(exc, "code", None):
        return exc
    if getattr(exc, "_mm_pool_exhausted", False):
        code = MM_ERROR_CODES["gate_busy"]
    else:
        code = code_for_status(exc.status_code)
    detail = exc.detail
    if isinstance(detail, str):
        detail = f"{detail}（{code}·trace={trace_id[:8]}）"
    new_exc = HTTPException(status_code=exc.status_code, detail=detail, headers=exc.headers)
    new_exc.code = code
    new_exc.trace_id = trace_id
    return new_exc


async def _proxy_whitelisted(request: Request, spec: Any) -> Response:
    route = _route_key(spec.method, spec.path)
    trace_id = new_multimodal_trace_id()

    base_url = get_multimodal_api_base()
    if not base_url:
        raise mm_error(503, "多模态知识库未配置", MM_ERROR_CODES["not_configured"], trace_id)

    # J.4 熔断：OPEN 时快速失败降级，浏览器收到明确提示；半开探测请求放行
    if not should_allow_request():
        raise mm_error(503, "多模态远端暂不可用（已自动降级，稍后自动恢复）", MM_ERROR_CODES["degraded"], trace_id)

    try:
        remote_url = build_multimodal_remote_url(spec.path, base_url)
    except MultimodalConfigError as exc:
        raise mm_error(503, str(exc), MM_ERROR_CODES["not_configured"], trace_id) from exc
    except ValueError as exc:
        raise mm_error(400, str(exc), MM_ERROR_CODES["bad_request"], trace_id) from exc

    headers = build_service_auth_headers(trace_id)
    timeout = httpx.Timeout(
        connect=min(10.0, spec.timeout_seconds),
        read=spec.timeout_seconds,
        write=spec.timeout_seconds,
        pool=10.0,
    )

    # J.5 知识库列表短 TTL 缓存（管理变更后主动失效）
    if spec.method == "GET" and spec.path in _KB_LIST_ROUTES:
        kk = _kb_list_cache_key(request, spec)
        cached = kb_list_cache.get(kk)
        if cached is not None:
            return JSONResponse(content=cached, status_code=200)
        request.state._mm_kb_cache_key = kk

    # J.5 搜索结果缓存：键含 权限/库/文件/版本（query 原文只进哈希）
    search_body: bytes | None = None
    if is_search_route(spec.method, spec.path):
        search_body = await accumulate_bounded_bytes(request.stream(), MAX_JSON_BODY_BYTES)
        if search_body is None:
            raise mm_error(413, "多模态请求体超过体积限制", MM_ERROR_CODES["request_too_large"], trace_id)
        skey = search_cache_key(spec.method, spec.path, spec.permission, search_body)
        if skey is not None:
            cached = mm_search_cache.get(skey)
            if cached is not None:
                return JSONResponse(content=cached, status_code=200)
            request.state._mm_search_cache_key = skey

    mm_metrics.begin(route)
    t0 = time.monotonic()
    try:
        if spec.response == RESPONSE_STREAM:
            result = await _proxy_stream(request, remote_url, spec, headers, trace_id, timeout)
        else:
            result = await _proxy_json(
                request, remote_url, spec, headers, trace_id, timeout,
                json_body_override=search_body,
            )
    except HTTPException as exc:
        _record_boundary_result(route, t0, exc=exc)
        raise _attach_code_trace(exc, trace_id)
    finally:
        mm_metrics.end(route)

    # 成功路径：记录指标 + 熔断成功；管理写操作成功后主动失效缓存（J.5）
    status_code = getattr(result, "status_code", 200)
    _record_boundary_result(route, t0, status_code=status_code)
    if route_category(spec.method, spec.path) == CATEGORY_MANAGE and status_code < 300:
        invalidate_on_manage_change()
    return result


def _make_proxy_handler(spec: Any):
    async def _handler(request: Request) -> Response:
        return await _proxy_whitelisted(request, spec)

    _handler.__name__ = f"multimodal_proxy_{spec.method.lower()}_{spec.path.replace('/', '_')}"
    return _handler


# 按白名单显式注册每条路由（B3.1）：不存在 /{path:path} 通配代理。
for _method, _path in whitelisted_proxy_routes():
    _spec = route_spec_for(_method, _path)
    multimodal.add_api_route(
        f"/{_path}",
        _make_proxy_handler(_spec),
        methods=[_method],
        name=f"multimodal_{_method.lower()}_{_path}",
        dependencies=_permission_dependencies(_spec.permission),
    )
