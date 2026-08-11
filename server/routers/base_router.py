from fastapi import Request, Body, Depends, HTTPException, Query
from fastapi import APIRouter
from sqlalchemy.orm import Session

from src import config, retriever, knowledge_base, graph_base
from server.db_manager import db_manager
from server.services import config_service, audit_service
from server.utils.auth_middleware import get_superadmin_user
from server.models.user_model import User


base = APIRouter()


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _client_ip(request: Request):
    return request.client.host if request.client else None


@base.get("/")
async def route_index():
    return {"message": "You Got It!"}

@base.get("/health")
async def health_check():
    """简单的健康检查接口"""
    return {"status": "ok", "message": "服务正常运行"}

# Depends = “告诉 FastAPI：在调用我之前，先帮我执行这个依赖函数，并把结果传给我”。
@base.get("/config")
def get_config(current_user: User = Depends(get_superadmin_user)):
    # 统一脱敏：任何配置响应都不返回真实 API Key（custom_models 仅返回 has_api_key/key_hint）
    return config_service.sanitize_config_snapshot(config.dump_config())


def _apply_and_respond(db, items, user_id, operator, description, request, action):
    """执行白名单校验的配置更新；失败时记录 failed 审计并抛 400。"""
    try:
        result = config_service.apply_update(db, config, items, operator=operator, description=description)
    except config_service.ConfigError as e:
        audit_service.record(
            action,
            user_id=user_id,
            resource_type="config",
            status="failed",
            detail={"reason": str(e)[:200], "count": len(items)},
            ip=_client_ip(request),
        )
        raise HTTPException(status_code=e.status_code, detail=str(e))
    audit_service.record(
        action,
        user_id=user_id,
        resource_type="config",
        resource_id=result["change_id"],
        detail={"count": len(result["changed_keys"]), "reason": (description or "")[:200]},
        ip=_client_ip(request),
    )
    # 保持原有返回形态（前端 setConfig 直接消费该字典），追加变更元信息
    snapshot = config_service.sanitize_config_snapshot(config.dump_config())
    snapshot["change_id"] = result["change_id"]
    snapshot["changed_keys"] = result["changed_keys"]
    snapshot["restart_components"] = result["restart_components"]
    return snapshot


@base.post("/config")
async def update_config(
    key = Body(...),
    value = Body(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user)
) -> dict:
    return _apply_and_respond(
        db, {key: value}, current_user.id, current_user.username, None, request, "config.update"
    )

@base.post("/config/update")
async def update_config_item(
    items: dict = Body(...),
    description: str = Body(None),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user)
) -> dict:
    return _apply_and_respond(
        db, items, current_user.id, current_user.username, description, request, "config.update"
    )


@base.get("/config/history")
async def list_config_history(
    operator: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    data = config_service.list_history(db, operator=operator, page=page, page_size=page_size)
    return {"status": "success", "data": data, "message": ""}


@base.get("/config/history/{change_id}")
async def get_config_history(
    change_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    change = config_service.get_history(db, change_id)
    if change is None:
        raise HTTPException(status_code=404, detail="配置变更记录不存在")
    return {"status": "success", "data": change, "message": ""}


@base.post("/config/history/{change_id}/rollback")
async def rollback_config(
    change_id: int,
    description: str = Body(None),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    try:
        result = config_service.rollback(
            db, config, change_id, operator=current_user.username, description=description
        )
    except config_service.ConfigError as e:
        audit_service.record(
            "config.rollback",
            user_id=current_user.id,
            resource_type="config",
            resource_id=change_id,
            status="failed",
            detail={"reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        raise HTTPException(status_code=e.status_code, detail=str(e))
    audit_service.record(
        "config.rollback",
        user_id=current_user.id,
        resource_type="config",
        resource_id=result["change_id"],
        detail={"count": len(result["rolled_back_keys"]), "reason": (description or "")[:200]},
        ip=_client_ip(request),
    )
    snapshot = config_service.sanitize_config_snapshot(config.dump_config())
    snapshot["change_id"] = result["change_id"]
    snapshot["rolled_back_keys"] = result["rolled_back_keys"]
    snapshot["restart_components"] = result["restart_components"]
    return snapshot

@base.post("/restart")
async def restart(current_user: User = Depends(get_superadmin_user)):
    knowledge_base.restart()
    graph_base.start()
    retriever.restart()
    return {"message": "Restarted!"}

@base.get("/log")
def get_log(current_user: User = Depends(get_superadmin_user)):
    from src.utils.logging_config import LOG_FILE
    from collections import deque

    with open(LOG_FILE) as f:
        last_lines = deque(f, maxlen=1000)

    log = ''.join(last_lines)
    return {"log": log, "message": "success", "log_file": LOG_FILE}
