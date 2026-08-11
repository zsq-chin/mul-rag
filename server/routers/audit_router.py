# server/routers/audit_router.py

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.services import audit_service
from server.utils.auth_middleware import get_superadmin_user

router = APIRouter(prefix="/audit", tags=["Audit"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _parse_dt(value, name: str):
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} 必须是合法时间字符串")


@router.get("/events")
async def list_audit_events(
    user: str = Query("", max_length=100),
    action: str = Query("", max_length=100),
    resource_type: str = Query("", max_length=100),
    status: str = Query("", max_length=20),
    start: str = Query(None, description="起始时间 ISO"),
    end: str = Query(None, description="结束时间 ISO"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    data = audit_service.list_events(
        db,
        user=user,
        action=action,
        resource_type=resource_type,
        status=status,
        start=_parse_dt(start, "start"),
        end=_parse_dt(end, "end"),
        page=page,
        page_size=page_size,
    )
    return {"status": "success", "data": data, "message": ""}


@router.get("/events/{event_id}")
async def get_audit_event(
    event_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    event = audit_service.get_event(db, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="审计事件不存在")
    return {"status": "success", "data": event, "message": ""}


@router.get("/actions")
async def list_audit_actions(
    superadmin=Depends(get_superadmin_user),
):
    return {"status": "success", "data": {"actions": audit_service.list_actions()}, "message": ""}
