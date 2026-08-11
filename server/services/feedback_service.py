"""回答反馈业务逻辑（会话注入，纯服务层，便于单元测试）。

关键约束：
- (user_id, message_id) 唯一，重复点击走 upsert，不产生重复记录。
- rating 只能是 up / down。
- 服务端根据当前用户聊天记录校验 message_id 所有权，防止伪造他人反馈。
- 并发 upsert：唯一约束冲突时自动转为更新，不抛 IntegrityError。
"""

import json
from collections import Counter

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.models.chat_model import ChatRecord
from server.models.feedback_model import AnswerFeedback
from server.services.statistics_aggregation import ASSISTANT_ROLES, iter_conv_messages

VALID_RATINGS = {"up", "down"}
MAX_REASON_LEN = 255
MAX_COMMENT_LEN = 2000
MAX_SUMMARY_RECENT = 10


class FeedbackError(Exception):
    """反馈业务错误，message 直接透传给用户。"""

    status_code = 400


class MessageNotFoundError(FeedbackError):
    """消息不存在或不属于当前用户。"""

    status_code = 404


# --- 内部辅助 ---


def _owned_message_ids(session, user_id) -> set[str]:
    """返回该用户 chat_records 中出现过的全部 message id。"""
    ids: set[str] = set()
    rows = session.query(ChatRecord).filter(ChatRecord.user_id == user_id).all()
    for r in rows:
        try:
            data = json.loads(r.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        for msg in data.get("messages") or []:
            if isinstance(msg, dict) and msg.get("id"):
                ids.add(str(msg["id"]))
    return ids


def _serialize(row: AnswerFeedback) -> dict:
    return {
        "id": row.id,
        "message_id": row.message_id,
        "conversation_id": row.conversation_id,
        "rating": row.rating,
        "reason": row.reason,
        "comment": row.comment,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# --- 对外接口 ---


def upsert_feedback(
    session,
    user_id: int,
    message_id: str,
    conversation_id=None,
    rating: str = "",
    reason: str | None = None,
    comment: str | None = None,
):
    """创建或更新当前用户对某条回答的评价。返回 (feedback_dict, created: bool)。"""
    rating = (rating or "").strip().lower()
    if rating not in VALID_RATINGS:
        raise FeedbackError("rating 只能是 up 或 down")
    reason = (reason or "").strip() or None
    comment = (comment or "").strip() or None
    if reason and len(reason) > MAX_REASON_LEN:
        raise FeedbackError("reason 过长")
    if comment and len(comment) > MAX_COMMENT_LEN:
        raise FeedbackError("comment 过长")
    if conversation_id is not None:
        conversation_id = str(conversation_id).strip()[:64] or None

    # 所有权校验：message_id 必须属于当前用户
    if message_id not in _owned_message_ids(session, user_id):
        raise MessageNotFoundError("消息不存在或无权评价")

    existing = (
        session.query(AnswerFeedback)
        .filter_by(user_id=user_id, message_id=message_id)
        .first()
    )
    if existing is not None:
        existing.rating = rating
        existing.reason = reason
        existing.comment = comment
        if conversation_id:
            existing.conversation_id = conversation_id
        session.commit()
        session.refresh(existing)
        return _serialize(existing), False

    row = AnswerFeedback(
        user_id=user_id,
        message_id=message_id,
        conversation_id=conversation_id,
        rating=rating,
        reason=reason,
        comment=comment,
    )
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        # 并发 upsert：唯一约束冲突 → 回滚后转为更新
        session.rollback()
        existing = (
            session.query(AnswerFeedback)
            .filter_by(user_id=user_id, message_id=message_id)
            .first()
        )
        if existing is None:
            raise
        existing.rating = rating
        existing.reason = reason
        existing.comment = comment
        if conversation_id:
            existing.conversation_id = conversation_id
        session.commit()
        session.refresh(existing)
        return _serialize(existing), False
    session.refresh(row)
    return _serialize(row), True


def get_user_feedback(session, user_id: int, message_id: str):
    """返回当前用户对某条消息的评价；无评价返回 None；消息不属于用户抛 404。"""
    if message_id not in _owned_message_ids(session, user_id):
        raise MessageNotFoundError("消息不存在或无权评价")
    row = (
        session.query(AnswerFeedback)
        .filter_by(user_id=user_id, message_id=message_id)
        .first()
    )
    return _serialize(row) if row is not None else None


def delete_feedback(session, user_id: int, message_id: str) -> bool:
    """删除当前用户对某条消息的评价。返回是否真的删除。"""
    if message_id not in _owned_message_ids(session, user_id):
        raise MessageNotFoundError("消息不存在或无权评价")
    row = (
        session.query(AnswerFeedback)
        .filter_by(user_id=user_id, message_id=message_id)
        .first()
    )
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_mine(session, user_id: int, page: int = 1, page_size: int = 20) -> dict:
    """分页返回当前用户自己的反馈。"""
    base = session.query(AnswerFeedback).filter(AnswerFeedback.user_id == user_id)
    total = base.count()
    rows = (
        base.order_by(AnswerFeedback.created_at.desc(), AnswerFeedback.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "items": [_serialize(r) for r in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


def count_assistant_messages(session) -> int:
    """统计全部回答消息数（用于反馈覆盖率）。"""
    total = 0
    for r in session.query(ChatRecord).all():
        for role, _ in iter_conv_messages(r.content):
            if role in ASSISTANT_ROLES:
                total += 1
    return total


def summarize(session) -> dict:
    """superadmin 汇总：总数/点赞/点踩/满意度/覆盖率/点踩原因 Top10。"""
    total = session.query(func.count(AnswerFeedback.id)).scalar() or 0
    up = (
        session.query(func.count(AnswerFeedback.id))
        .filter(AnswerFeedback.rating == "up")
        .scalar()
        or 0
    )
    down = total - up
    satisfaction_rate = round(up / total, 4) if total else 0.0
    total_answers = count_assistant_messages(session)
    coverage_rate = round(total / total_answers, 4) if total_answers else 0.0

    down_rows = (
        session.query(AnswerFeedback.reason)
        .filter(AnswerFeedback.rating == "down")
        .all()
    )
    reason_counter: Counter = Counter()
    for (r,) in down_rows:
        if r:
            reason_counter[r] += 1
    down_reasons = [
        {"reason": r, "count": c} for r, c in reason_counter.most_common(10)
    ]

    recent = (
        session.query(AnswerFeedback)
        .order_by(AnswerFeedback.created_at.desc(), AnswerFeedback.id.desc())
        .limit(MAX_SUMMARY_RECENT)
        .all()
    )
    return {
        "total": total,
        "up": up,
        "down": down,
        "satisfaction_rate": satisfaction_rate,
        "coverage_rate": coverage_rate,
        "down_reasons": down_reasons,
        "recent": [_serialize(r) for r in recent],
    }
