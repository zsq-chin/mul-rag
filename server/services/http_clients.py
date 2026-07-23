import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx


GRAPH_WORKER_URL = os.getenv("GRAPH_WORKER_URL", "http://graphrag-worker:8111")

_multimodal_client: httpx.AsyncClient | None = None
_graph_worker_client: httpx.AsyncClient | None = None


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


def get_graph_worker_client() -> httpx.AsyncClient:
    """Return a shared async client for proxying to the graphrag worker."""
    global _graph_worker_client
    if _graph_worker_client is None or _graph_worker_client.is_closed:
        timeout = httpx.Timeout(
            connect=_env_float("GRAPH_WORKER_CONNECT_TIMEOUT", 5.0),
            read=_env_float("GRAPH_WORKER_READ_TIMEOUT", 30.0),
            write=_env_float("GRAPH_WORKER_WRITE_TIMEOUT", 10.0),
            pool=_env_float("GRAPH_WORKER_POOL_TIMEOUT", 5.0),
        )
        limits = httpx.Limits(
            max_connections=_env_int("GRAPH_WORKER_MAX_CONNECTIONS", 10),
            max_keepalive_connections=_env_int("GRAPH_WORKER_MAX_KEEPALIVE", 5),
        )
        _graph_worker_client = httpx.AsyncClient(
            base_url=GRAPH_WORKER_URL,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        )
    return _graph_worker_client


async def close_graph_worker_client() -> None:
    global _graph_worker_client
    if _graph_worker_client is not None and not _graph_worker_client.is_closed:
        await _graph_worker_client.aclose()
    _graph_worker_client = None


_tianshu_client: httpx.AsyncClient | None = None

TIANSHU_API_BASE = os.getenv("TIANSHU_API_BASE", "http://tianshu-backend:8000/api/v1")


def get_tianshu_client() -> httpx.AsyncClient:
    """Return a dedicated async client for the Tianshu backend."""
    global _tianshu_client
    if _tianshu_client is None or _tianshu_client.is_closed:
        timeout = httpx.Timeout(
            connect=_env_float("TIANSHU_CONNECT_TIMEOUT", 10.0),
            read=_env_float("TIANSHU_READ_TIMEOUT", 60.0),
            write=10.0,
            pool=5.0,
        )
        limits = httpx.Limits(
            max_connections=10,
            max_keepalive_connections=5,
        )
        _tianshu_client = httpx.AsyncClient(
            base_url=TIANSHU_API_BASE,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,
        )
    return _tianshu_client


async def close_tianshu_client() -> None:
    global _tianshu_client
    if _tianshu_client is not None and not _tianshu_client.is_closed:
        await _tianshu_client.aclose()
    _tianshu_client = None


@asynccontextmanager
async def multimodal_client_lifespan(_app: Any) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await close_multimodal_client()
        await close_graph_worker_client()
        await close_tianshu_client()
