# server/routers/alert_router.py
"""邮件告警接口：规则 CRUD、告警事件、确认与 SMTP 测试。

全部仅 superadmin 可访问。SMTP 配置只来自环境变量（SMTP_*），
API 响应与日志绝不含 SMTP 密码；未配置时测试邮件返回 503。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.schemas.alert import AlertRuleCreate, AlertRuleUpdate, TestEmailPayload
from server.services import alert_service, audit_service
from server.utils.auth_middleware import get_superadmin_user

router = APIRouter(prefix="/operations", tags=["Operations"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _client_ip(request: Request):
    return request.client.host if request.client else None


def _raise(exc: alert_service.AlertError):
    raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/alert-rules")
async def create_alert_rule(
    request: Request,
    payload: AlertRuleCreate,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        row = alert_service.create_rule(
            db,
            name=payload.name,
            rule_type=payload.rule_type,
            enabled=payload.enabled,
            threshold=payload.threshold,
            cooldown_seconds=payload.cooldown_seconds,
            notify_email=payload.notify_email,
            created_by=superadmin.username,
        )
    except alert_service.AlertError as e:
        audit_service.record(
            "alert.rule.create",
            user_id=superadmin.id,
            resource_type="alert_rule",
            status="failed",
            detail={"rule_name": payload.name, "rule_type": payload.rule_type, "reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        _raise(e)
    audit_service.record(
        "alert.rule.create",
        user_id=superadmin.id,
        resource_type="alert_rule",
        resource_id=row.id,
        detail={"rule_name": row.name, "rule_type": row.rule_type},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": alert_service._serialize_rule(row), "message": ""}


@router.get("/alert-rules")
async def get_alert_rules(
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    return {"status": "success", "data": alert_service.list_rules(db), "message": ""}


@router.patch("/alert-rules/{rule_id}")
async def update_alert_rule(
    rule_id: int,
    request: Request,
    payload: AlertRuleUpdate,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        # exclude_unset 区分“未提交”与“明确清空（null）”
        row = alert_service.update_rule(db, rule_id, **payload.model_dump(exclude_unset=True))
    except alert_service.AlertError as e:
        audit_service.record(
            "alert.rule.update",
            user_id=superadmin.id,
            resource_type="alert_rule",
            resource_id=rule_id,
            status="failed",
            detail={"rule_id": rule_id, "reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        _raise(e)
    audit_service.record(
        "alert.rule.update",
        user_id=superadmin.id,
        resource_type="alert_rule",
        resource_id=row.id,
        detail={"rule_name": row.name, "rule_type": row.rule_type},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": alert_service._serialize_rule(row), "message": ""}


@router.delete("/alert-rules/{rule_id}")
async def delete_alert_rule(
    rule_id: int,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = alert_service.delete_rule(db, rule_id)
    except alert_service.AlertError as e:
        audit_service.record(
            "alert.rule.delete",
            user_id=superadmin.id,
            resource_type="alert_rule",
            resource_id=rule_id,
            status="failed",
            detail={"rule_id": rule_id, "reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        _raise(e)
    audit_service.record(
        "alert.rule.delete",
        user_id=superadmin.id,
        resource_type="alert_rule",
        resource_id=rule_id,
        detail={"deleted": True},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": data, "message": ""}


@router.get("/alert-events")
async def get_alert_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query(""),
    severity: str = Query(""),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    return {
        "status": "success",
        "data": alert_service.list_events(db, page=page, page_size=page_size, status=status, severity=severity),
        "message": "",
    }


@router.post("/alert-events/{event_id}/acknowledge")
async def acknowledge_alert_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        row = alert_service.acknowledge_event(db, event_id)
    except alert_service.AlertError as e:
        audit_service.record(
            "alert.event.acknowledge",
            user_id=superadmin.id,
            resource_type="alert_event",
            resource_id=event_id,
            status="failed",
            detail={"event_id": event_id, "reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        _raise(e)
    audit_service.record(
        "alert.event.acknowledge",
        user_id=superadmin.id,
        resource_type="alert_event",
        resource_id=row.id,
        detail={"status": "acknowledged"},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": alert_service._serialize_event(row), "message": ""}


@router.post("/email/test")
async def test_email(
    request: Request,
    payload: TestEmailPayload,
    superadmin=Depends(get_superadmin_user),
):
    to_email = payload.to_email.strip()
    try:
        cfg = alert_service.smtp_from_env()
        data = alert_service.send_email(cfg, to_email, "Sage 系统测试邮件", "这是一封来自 Sage 本机系统的测试邮件。")
    except alert_service.AlertError as e:
        audit_service.record(
            "alert.email.test",
            user_id=superadmin.id,
            resource_type="alert_email",
            status="failed",
            detail={"reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        _raise(e)
    audit_service.record(
        "alert.email.test",
        user_id=superadmin.id,
        resource_type="alert_email",
        detail={"status": "success"},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": data, "message": ""}
