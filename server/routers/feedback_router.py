# server/routers/feedback_router.py

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.schemas.feedback import FeedbackCreate
from server.services import feedback_service
from server.services.audit_service import AuditService
from server.utils.auth_middleware import get_required_user, get_superadmin_user

router = APIRouter(prefix="/feedback", tags=["Feedback"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _to_http(exc: feedback_service.FeedbackError):
    return HTTPException(status_code=getattr(exc, "status_code", 400), detail=str(exc))


@router.put("/messages/{message_id}")
async def put_feedback(
    message_id: str,
    payload: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """创建或更新当前用户对某条回答的评价（upsert）。"""
    try:
        feedback, created = feedback_service.upsert_feedback(
            db,
            user.id,
            message_id,
            conversation_id=payload.conversation_id,
            rating=payload.rating,
            reason=payload.reason,
            comment=payload.comment,
        )
    except feedback_service.FeedbackError as e:
        AuditService.record(
            "feedback.upsert",
            user_id=user.id,
            resource_type="message",
            resource_id=message_id,
            status="failed",
            detail={"message_id": message_id, "rating": payload.rating},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "feedback.upsert",
        user_id=user.id,
        resource_type="message",
        resource_id=message_id,
        status="success",
        detail={
            "message_id": message_id,
            "conversation_id": payload.conversation_id,
            "rating": payload.rating,
        },
        ip=request.client.host,
    )
    return {
        "status": "success",
        "data": feedback,
        "message": "已更新评价" if not created else "已记录评价",
    }


@router.get("/messages/{message_id}")
async def get_message_feedback(
    message_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """返回当前用户对某条消息的评价；暂无返回 data=null。"""
    try:
        feedback = feedback_service.get_user_feedback(db, user.id, message_id)
    except feedback_service.FeedbackError as e:
        raise _to_http(e)
    return {"status": "success", "data": feedback, "message": ""}


@router.delete("/messages/{message_id}")
async def delete_message_feedback(
    message_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """取消当前用户对某条消息的评价。"""
    try:
        deleted = feedback_service.delete_feedback(db, user.id, message_id)
    except feedback_service.FeedbackError as e:
        raise _to_http(e)
    AuditService.record(
        "feedback.delete",
        user_id=user.id,
        resource_type="message",
        resource_id=message_id,
        status="success",
        detail={"message_id": message_id},
        ip=request.client.host,
    )
    return {
        "status": "success",
        "data": {"deleted": deleted},
        "message": "已取消评价",
    }


@router.get("/mine")
async def list_mine_feedback(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """分页返回当前用户自己的全部反馈。"""
    data = feedback_service.list_mine(db, user.id, page, page_size)
    return {"status": "success", "data": data, "message": ""}


@router.get("/summary")
async def feedback_summary(
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    """全局反馈汇总（superadmin）。"""
    data = feedback_service.summarize(db)
    return {"status": "success", "data": data, "message": ""}
