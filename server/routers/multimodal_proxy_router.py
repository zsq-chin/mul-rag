import asyncio
import traceback
from collections.abc import Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from server.models.user_model import User
from server.services.http_clients import get_multimodal_client
from server.services.concurrency import upstream_proxy_gate
from server.utils.auth_middleware import get_superadmin_user
from server.utils.multimodal_remote import (
    build_multimodal_remote_url,
    build_service_auth_headers,
    filter_multimodal_proxy_headers,
    format_redacted_upstream_error,
    get_multimodal_api_base,
    new_multimodal_trace_id,
    normalize_multimodal_image_page,
)
from src.utils.logging_config import logger

multimodal = APIRouter(prefix="/multimodal")

SAFE_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-range",
    "etag",
    "last-modified",
}


def _forward_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in SAFE_RESPONSE_HEADERS
    }


@multimodal.get("/kb/images")
async def get_paged_kb_images(
    request: Request,
    kbId: str,
    page: int = 1,
    pageSize: int = 24,
    current_user: User = Depends(get_superadmin_user),
):
    base_url = get_multimodal_api_base()
    if not base_url:
        raise HTTPException(status_code=503, detail="多模态知识库未配置")
    remote_url = build_multimodal_remote_url("kb/images", base_url)
    trace_id = new_multimodal_trace_id()
    headers = build_service_auth_headers(trace_id)
    client = get_multimodal_client()
    try:
        async with upstream_proxy_gate:
            response = await client.get(
                remote_url,
                params=list(request.query_params.multi_items()),
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(format_redacted_upstream_error(trace_id, "kb/images", None, 0.0, type(exc).__name__))
        raise HTTPException(status_code=502, detail=f"多模态图片目录加载失败（trace={trace_id[:8]}）") from exc

    return normalize_multimodal_image_page(payload, page=page, page_size=pageSize)


@multimodal.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_multimodal_request(
    path: str,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
):
    base_url = get_multimodal_api_base()
    if not base_url:
        raise HTTPException(status_code=503, detail="多模态知识库未配置")
    try:
        remote_url = build_multimodal_remote_url(path, base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = get_multimodal_client()
    has_body = request.method not in {"GET", "HEAD"} and request.headers.get("content-length") != "0"

    await upstream_proxy_gate.__aenter__()
    try:
        upstream_request = client.build_request(
            method=request.method,
            url=remote_url,
            params=list(request.query_params.multi_items()),
            content=request.stream() if has_body else None,
            headers=filter_multimodal_proxy_headers(dict(request.headers)),
        )
        response = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise
    except httpx.HTTPError as exc:
        await upstream_proxy_gate.__aexit__(type(exc), exc, exc.__traceback__)
        logger.error(f"Multimodal proxy error: {exc}, {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"多模态知识库代理请求失败: {exc}") from exc
    except Exception as exc:
        await upstream_proxy_gate.__aexit__(type(exc), exc, exc.__traceback__)
        logger.error(f"Multimodal proxy build error: {exc}, {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"多模态知识库代理请求构建失败: {exc}") from exc

    async def _stream():
        try:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            try:
                await response.aclose()
            finally:
                await upstream_proxy_gate.__aexit__(None, None, None)

    return StreamingResponse(
        _stream(),
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
        headers=_forward_response_headers(response.headers),
    )
