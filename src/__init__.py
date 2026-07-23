import atexit
import os
from dotenv import load_dotenv

load_dotenv("src/.env")

from concurrent.futures import ThreadPoolExecutor  # noqa: E402


def _parse_blocking_workers() -> int:
    """Parse BLOCKING_WORKERS from env, falling back to 8 on any bad value."""
    raw = os.environ.get("BLOCKING_WORKERS", "8")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return 8
    return val if val > 0 else 8


BLOCKING_WORKERS = _parse_blocking_workers()
executor = ThreadPoolExecutor(
    max_workers=BLOCKING_WORKERS,
    thread_name_prefix="sage-blocking",
)

_shutdown_done = False


def shutdown_runtime() -> None:
    """Shut down the blocking thread-pool and graph base gracefully.

    Idempotent — safe to call from both lifespan and atexit.
    """
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    try:
        if "graph_base" in globals():
            graph_base.close()
    except Exception:
        pass
    executor.shutdown(wait=True, cancel_futures=True)


atexit.register(shutdown_runtime)

from src.config import Config  # noqa: E402
config = Config()

from src.core import KnowledgeBase  # noqa: E402
knowledge_base = KnowledgeBase()

from src.core import GraphDatabase  # noqa: E402
graph_base = GraphDatabase()

from src.core.retriever import Retriever  # noqa: E402
retriever = Retriever()
