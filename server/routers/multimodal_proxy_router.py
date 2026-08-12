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
    """把上游/构建异常统一映射为 HTTP 4xx/5xx（B3.7），日志只含脱敏字段。"""
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, MultimodalUploadError):
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if isinstance(exc, MultimodalPaginationError):
        logger.error(
            format_redacted_upstream_error(trace_id, spec.path, 502, _elapsed_ms(t0), "PaginationContract")
        )
        raise HTTPException(status_code=502, detail=f"{exc}（trace={trace_id[:8]}）") from exc
    if isinstance(exc, httpx.TimeoutException):
        logger.error(format_redacted_upstream_error(trace_id, spec.path, None, _elapsed_ms(t0), type(exc).__name__))
        raise HTTPException(status_code=504, detail=f"多模态远端连接/读取超时（trace={trace_id[:8]}）") from exc
    logger.error(format_redacted_upstream_error(trace_id, spec.path, None, _elapsed_ms(t0), type(exc).__name__))
    raise HTTPException(status_code=502, detail=f"多模态远端不可用（trace={trace_id[:8]}）") from exc


def _raise_mapped_upstream_error(response: httpx.Response, spec: Any, trace_id: str, t0: float) -> None:
    status = response.status_code
    mapped_status, mapped_message = map_upstream_proxy_status(status)
    logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "HTTPError"))
    headers = None
    if status in (429, 503):
        retry_after = response.headers.get("retry-after")
        if retry_after:
            headers = {"Retry-After": retry_after}
    raise HTTPException(
        status_code=mapped_status,
        detail=f"{mapped_message}（trace={trace_id[:8]}）",
        headers=headers,
    )


async def _build_upstream_request(
    client: httpx.AsyncClient,
    request: Request,
    remote_url: str,
    spec: Any,
    headers: dict[str, str],
    timeout: httpx.Timeout,
) -> httpx.Request:
    """构造发往远端的请求：JSON 请求体限体积；multipart 流式解析并校验后重编码。

    httpx 0.28 的 send() 不接受 timeout，必须通过 build_request 注入每个请求。
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
) -> Response:
    client = get_multimodal_client()
    t0 = time.monotonic()
    try:
        async with upstream_proxy_gate:
            upstream_request = await _build_upstream_request(client, request, remote_url, spec, headers, timeout)
            response = await client.send(upstream_request, stream=True)
            try:
                if response.is_redirect:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "Redirect"))
                    raise HTTPException(status_code=502, detail=f"多模态远端跳转已被拒绝（trace={trace_id[:8]}）")
                if response.status_code >= 400:
                    _raise_mapped_upstream_error(response, spec, trace_id, t0)

                body = await accumulate_bounded_bytes(response.aiter_bytes(), spec.max_response_bytes)
                if body is None:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "ResponseTooLarge"))
                    raise HTTPException(status_code=502, detail=f"多模态远端响应超过体积限制（trace={trace_id[:8]}）")
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    logger.error(format_redacted_upstream_error(trace_id, spec.path, response.status_code, _elapsed_ms(t0), "NonJSONResponse"))
                    raise HTTPException(status_code=502, detail=f"多模态远端返回非 JSON 响应（trace={trace_id[:8]}）") from exc

                normalizer = _JSON_NORMALIZERS.get((spec.method, spec.path))
                if normalizer is not None:
                    payload = normalizer(
                        payload,
                        page=_int_query(request, "page", 1),
                        page_size=_int_query(request, "pageSize", 24),
                    )
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
    await upstream_proxy_gate.__aenter__()  # 队列满时抛 503 HTTPException
    response: httpx.Response | None = None
    try:
        upstream_request = await _build_upstream_request(client, request, remote_url, spec, headers, timeout)
        response = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise
    except Exception as exc:
        await upstream_proxy_gate.__aexit__(type(exc), exc, exc.__traceback__)
        _raise_proxy_error(exc, spec, trace_id, t0)

    status = response.status_code
    if status == 304:
        headers_out = filter_multimodal_response_headers(response.headers)
        await response.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        return Response(status_code=304, headers=headers_out)

    if response.is_redirect or status >= 400:
        await response.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        if response.is_redirect:
            logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "Redirect"))
            raise HTTPException(status_code=502, detail=f"多模态远端跳转已被拒绝（trace={trace_id[:8]}）")
        _raise_mapped_upstream_error(response, spec, trace_id, t0)

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if not validate_stream_content_type(content_type):
        await response.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "BadContentType"))
        raise HTTPException(status_code=502, detail=f"多模态远端返回非预期 Content-Type（trace={trace_id[:8]}）")

    declared_length: int | None = None
    raw_length = response.headers.get("content-length")
    if raw_length:
        try:
            declared_length = int(raw_length)
        except (TypeError, ValueError):
            declared_length = None
    if declared_length is not None and declared_length > MAX_STREAM_BYTES:
        await response.aclose()
        await upstream_proxy_gate.__aexit__(None, None, None)
        logger.error(format_redacted_upstream_error(trace_id, spec.path, status, _elapsed_ms(t0), "ResponseTooLarge"))
        raise HTTPException(status_code=502, detail=f"多模态远端响应超过体积限制（trace={trace_id[:8]}）")

    async def _stream():
        total = 0
        try:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    return
                yield chunk
        except (httpx.HTTPError, asyncio.CancelledError):
            # 客户端断开或上游读取失败：停止，不把断流当 500
            return
        finally:
            try:
                await response.aclose()
            finally:
                await upstream_proxy_gate.__aexit__(None, None, None)

    return StreamingResponse(
        _stream(),
        status_code=status,
        media_type=content_type or "application/octet-stream",
        headers=filter_multimodal_response_headers(response.headers),
    )


async def _proxy_whitelisted(request: Request, spec: Any) -> Response:
    base_url = get_multimodal_api_base()
    if not base_url:
        raise HTTPException(status_code=503, detail="多模态知识库未配置")
    try:
        remote_url = build_multimodal_remote_url(spec.path, base_url)
    except MultimodalConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace_id = new_multimodal_trace_id()
    headers = build_service_auth_headers(trace_id)
    timeout = httpx.Timeout(
        connect=min(10.0, spec.timeout_seconds),
        read=spec.timeout_seconds,
        write=spec.timeout_seconds,
        pool=10.0,
    )

    if spec.response == RESPONSE_STREAM:
        return await _proxy_stream(request, remote_url, spec, headers, trace_id, timeout)
    return await _proxy_json(request, remote_url, spec, headers, trace_id, timeout)


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
