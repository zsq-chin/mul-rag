"""知识字典 worker 进程入口（设计文档 §5/§12）。

独立于 API 进程运行（同代码镜像）：
    uv run python server/dictionary_worker.py

- 通过数据库任务租约领取 queued 任务（generate / index / import_seed）；
- 崩溃或重启后，租约过期的 running 任务被标记为 interrupted，可从检查点重试；
- 取消请求在批次边界生效；
- 生成完成后自动排入草稿索引任务。
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import time

# 与 API 一致地加载 src/.env（绝对路径，不依赖 CWD）
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv

    load_dotenv(_env_path)

# 导入顺序与 API 进程（uvicorn server.main:app）保持一致：
# 必须先完整初始化 src（其内部会首次导入 server.db_manager），
# 再取 db_manager 单例；否则 server.db_manager -> src -> src.core.knowledgebase
# -> server.db_manager 形成部分初始化循环导入。
from src import config, shutdown_runtime  # noqa: E402,F401
from server.db_manager import db_manager  # noqa: E402
from server.services.knowledge_dictionary import jobs as job_service  # noqa: E402

import logging  # noqa: E402

logger = logging.getLogger("sage.dictionary-worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

WORKER_ID = os.environ.get("DICTIONARY_WORKER_ID") or f"dict-worker-{socket.gethostname()}-{os.getpid()}"
POLL_INTERVAL_SECONDS = float(os.environ.get("DICTIONARY_WORKER_POLL_SECONDS", "3") or 3)
JOB_TYPES = [t.strip() for t in (os.environ.get("DICTIONARY_WORKER_JOB_TYPES", "generate,index,import_seed") or "").split(",") if t.strip()]

_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True


def run_forever() -> None:
    logger.info("starting worker=%s types=%s", WORKER_ID, JOB_TYPES)
    session = db_manager.get_session()
    try:
        while not _stop:
            job = job_service.claim_next_job(session, WORKER_ID, job_types=JOB_TYPES)
            if job is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            logger.info("claimed job=%s type=%s", job.id, job.job_type)
            try:
                job_service.run_job(session, job, WORKER_ID)
                status = (
                    session.query(type(job)).filter(type(job).id == job.id).first()
                )
                logger.info("job=%s done status=%s", job.id, status.status if status else "unknown")
            except Exception as exc:  # noqa: BLE001 -- 单任务异常不得杀死 worker
                logger.error("job=%s crashed: %s: %s", job.id, type(exc).__name__, exc)
                session.rollback()
    finally:
        session.close()
    logger.info("stopped")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        run_forever()
        return 0
    finally:
        try:
            db_manager.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
