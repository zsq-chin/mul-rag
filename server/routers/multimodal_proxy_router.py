import traceback
from collections.abc import Mapping

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from server.models.user_model import User
from server.services.http_clients import get_multimodal_client
from server.utils.auth_middleware import get_superadmin_user
from server.utils.multimodal_remote import (
    build_multimodal_remote_url,
    filter_multimodal_proxy_headers,
    get_multimodal_api_base,
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
    remote_url = build_multimodal_remote_url("kb/images", get_multimodal_api_base())
    client = get_multimodal_client()
    try:
        response = await client.get(remote_url, params=list(request.query_params.multi_items()))
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(f"Multimodal image catalog proxy error: {exc}, {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"多模态图片目录加载失败: {exc}") from exc

    return normalize_multimodal_image_page(payload, page=page, page_size=pageSize)


@multimodal.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_multimodal_request(
    path: str,
    request: Request,
    current_user: User = Depends(get_superadmin_user),
):
    try:
        remote_url = build_multimodal_remote_url(path, get_multimodal_api_base())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = get_multimodal_client()

    async def request_content():
        async for chunk in request.stream():
            yield chunk

    has_body = request.method not in {"GET", "HEAD"} and request.headers.get("content-length") != "0"
    try:
        upstream_request = client.build_request(
            method=request.method,
            url=remote_url,
            params=list(request.query_params.multi_items()),
            content=request_content() if has_body else None,
            headers=filter_multimodal_proxy_headers(dict(request.headers)),
        )
        response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        logger.error(f"Multimodal proxy error: {exc}, {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"多模态知识库代理请求失败: {exc}") from exc

    return StreamingResponse(
        response.aiter_bytes(chunk_size=1024 * 64),
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
        headers=_forward_response_headers(response.headers),
        background=BackgroundTask(response.aclose),
    )
