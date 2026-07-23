"""Concurrency primitives for the platform."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from fastapi import HTTPException


def _env_int(name: str, default: int) -> int:
    """Return a positive integer from *name*, falling back to *default*."""
    try:
        value = int(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _env_timeout(name: str, default: float) -> float:
    """Return a positive timeout from *name*, falling back to *default*."""
    try:
        value = float(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


class BoundedGate:
    """Async-context-managed concurrency gate backed by an asyncio.BoundedSemaphore.

    Parameters
    ----------
    name:
        Human-readable gate identifier (used in error details; must not be blank).
    limit:
        Maximum number of concurrent holders (must be a positive integer).
    acquire_timeout:
        Seconds to wait for a slot before raising a 503 (must be a positive float).
    """

    def __init__(
        self,
        name: str,
        *,
        limit: int,
        acquire_timeout: float,
    ) -> None:
        # -- validate inputs ---------------------------------------------------
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(acquire_timeout, (int, float)) or acquire_timeout <= 0:
            raise ValueError("acquire_timeout must be a positive number")

        self._name = name.strip()
        self._limit = int(limit)
        self._semaphore = asyncio.BoundedSemaphore(limit)
        self._acquire_timeout = float(acquire_timeout)
        self._in_use: int = 0

    # -- public helpers --------------------------------------------------------

    @property
    def in_use(self) -> int:
        """Number of currently held slots."""
        return self._in_use

    @property
    def limit(self) -> int:
        """Maximum concurrent holders configured for this gate."""
        return self._limit

    # -- async-context-manager -------------------------------------------------

    async def __aenter__(self) -> "BoundedGate":
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self._acquire_timeout,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503,
                detail=f"gate '{self._name}' is busy",
                headers={"Retry-After": "2"},
            )
        self._in_use += 1
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> None:
        self._in_use -= 1
        self._semaphore.release()


# ---------------------------------------------------------------------------
# Module-level singletons -- configured from environment variables with
# safe fallbacks when the variable is unset or malformed.
# ---------------------------------------------------------------------------

_DEFAULT_ACQUIRE_TIMEOUT: float = _env_timeout("CONCURRENCY_ACQUIRE_TIMEOUT", 30.0)

chat_gate = BoundedGate(
    "chat",
    limit=_env_int("CHAT_CONCURRENCY", 2),
    acquire_timeout=_DEFAULT_ACQUIRE_TIMEOUT,
)

retrieval_gate = BoundedGate(
    "retrieval",
    limit=_env_int("RETRIEVAL_CONCURRENCY", 4),
    acquire_timeout=_DEFAULT_ACQUIRE_TIMEOUT,
)

graph_import_gate = BoundedGate(
    "graph_import",
    limit=_env_int("GRAPH_IMPORT_CONCURRENCY", 1),
    acquire_timeout=_DEFAULT_ACQUIRE_TIMEOUT,
)

upstream_proxy_gate = BoundedGate(
    "upstream_proxy",
    limit=_env_int("UPSTREAM_PROXY_CONCURRENCY", 16),
    acquire_timeout=_DEFAULT_ACQUIRE_TIMEOUT,
)
