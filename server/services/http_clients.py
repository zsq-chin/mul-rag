import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx


_multimodal_client: httpx.AsyncClient | None = None


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except (TypeError, ValueError):
        return default


def get_multimodal_client() -> httpx.AsyncClient:
    global _multimodal_client
    if _multimodal_client is None or _multimodal_client.is_closed:
        timeout = httpx.Timeout(
            connect=_env_float("MULTIMODAL_HTTP_CONNECT_TIMEOUT", 10.0),
            read=_env_float(
                "MULTIMODAL_HTTP_READ_TIMEOUT",
                _env_float("MULTIMODAL_KB_PROXY_TIMEOUT", 600.0),
            ),
            write=_env_float("MULTIMODAL_HTTP_WRITE_TIMEOUT", 60.0),
            pool=_env_float("MULTIMODAL_HTTP_POOL_TIMEOUT", 10.0),
        )
        limits = httpx.Limits(
            max_connections=_env_int("MULTIMODAL_HTTP_MAX_CONNECTIONS", 40),
            max_keepalive_connections=_env_int("MULTIMODAL_HTTP_MAX_KEEPALIVE", 20),
            keepalive_expiry=_env_float("MULTIMODAL_HTTP_KEEPALIVE_EXPIRY", 30.0),
        )
        _multimodal_client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        )
    return _multimodal_client


async def close_multimodal_client() -> None:
    global _multimodal_client
    if _multimodal_client is not None and not _multimodal_client.is_closed:
        await _multimodal_client.aclose()
    _multimodal_client = None


@asynccontextmanager
async def multimodal_client_lifespan(_app: Any) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_multimodal_client()
