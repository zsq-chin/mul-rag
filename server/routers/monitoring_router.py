# server/routers/monitoring_router.py
"""本机系统监控接口：/operations/health、/metrics、/dependencies。

全部仅 superadmin 可访问。每个依赖项独立超时、独立状态；GPU 不存在时返回
`unavailable`，依赖失败不产生 500。`/api/health` 保持轻量，详细检查只放这里。
"""

import os

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src import config
from server.db_manager import db_manager
from server.services import monitoring_service
from server.utils.auth_middleware import get_superadmin_user

router = APIRouter(prefix="/operations", tags=["Operations"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _ctx():
    """注入给监控服务的运行上下文（连接参数，全部来自本机配置/环境变量）。"""
    return {
        "db_path": db_manager.db_path,
        "save_dir": config.save_dir,
        "backup_dir": os.path.join(config.save_dir, "backups"),
        "milvus_uri": os.getenv("MILVUS_URI", config.get("milvus_uri", "http://milvus:19530")),
        "neo4j_uri": os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        "neo4j_username": os.environ.get("NEO4J_USERNAME"),
        "neo4j_password": os.environ.get("NEO4J_PASSWORD"),
    }


@router.get("/health")
def operations_health(
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    # 同步 def：探测交给 FastAPI 线程池，不阻塞事件循环
    return {"status": "success", "data": monitoring_service.health(db, _ctx()), "message": ""}


@router.get("/metrics")
def operations_metrics(
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    # 同步 def：nvidia-smi 等阻塞探测在 FastAPI 线程池执行，不阻塞事件循环
    return {"status": "success", "data": monitoring_service.metrics(db, _ctx()), "message": ""}


@router.get("/dependencies")
def operations_dependencies(
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    # 同步 def：Milvus/Neo4j/GPU 探测在 FastAPI 线程池执行，不阻塞事件循环
    return {"status": "success", "data": monitoring_service.dependencies(db, _ctx()), "message": ""}
