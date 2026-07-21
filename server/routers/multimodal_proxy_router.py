import os
import traceback

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from server.models.user_model import User
from server.utils.auth_middleware import get_superadmin_user
from server.utils.multimodal_remote import (
    build_multimodal_remote_url,
    filter_multimodal_proxy_headers,
    get_multimodal_api_base,
)
from src.utils.logging_config import logger

multimodal = APIRouter(prefix="/multimodal")

SAFE_RESPONSE_HEADERS = {
    "cache-control",
    "content-disposition",
    "etag",
    "last-modified",
}


def _forward_response_headers(headers: requests.structures.CaseInsensitiveDict) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() in SAFE_RESPONSE_HEADERS
    }


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

    timeout = float(os.getenv("MULTIMODAL_KB_PROXY_TIMEOUT") or os.getenv("MULTIMODAL_KB_TIMEOUT") or 600)
    body = await request.body()

    try:
        response = requests.request(
            method=request.method,
            url=remote_url,
            params=list(request.query_params.multi_items()),
            data=body if body else None,
            headers=filter_multimodal_proxy_headers(dict(request.headers)),
            timeout=timeout,
            stream=True,
        )
    except Exception as exc:
        logger.error(f"Multimodal proxy error: {exc}, {traceback.format_exc()}")
        raise HTTPException(status_code=502, detail=f"多模态知识库代理请求失败: {exc}") from exc

    return StreamingResponse(
        response.iter_content(chunk_size=1024 * 64),
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
        headers=_forward_response_headers(response.headers),
        background=BackgroundTask(response.close),
    )
