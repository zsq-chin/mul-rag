# server/routers/statistics_router.py

import json
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.models.chat_model import ChatRecord
from server.models.statistics_model import Discussion, HelpRequest, Question
from server.models.thread_model import Thread
from server.models.user_model import User
from server.services.statistics_aggregation import (
    MOCK_SEED_TITLES,
    aggregate_records,
    build_daily_trend,
    top_users,
)
from server.utils.auth_middleware import get_superadmin_user
from src.utils.logging_config import logger

router = APIRouter(prefix="/statistics", tags=["Statistics"])


# --- 依赖项 ---
def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


# --- Pydantic Schemas (请求体验证) ---
class DiscussionCreate(BaseModel):
    content: str


class HelpRequestCreate(BaseModel):
    questionId: int
    title: str
    description: str
    email: str


# --- 辅助函数：读取原始对话记录 ---
def _chat_record_rows(db: Session) -> list[dict]:
    """把 chat_records 表转成聚合函数需要的 dict 列表（按保存时间倒序）。"""
    records = db.query(ChatRecord).order_by(ChatRecord.updatetime.desc()).all()
    return [
        {"content": r.content, "updatetime": r.updatetime, "user_id": r.user_id}
        for r in records
    ]


# --- API Endpoints ---

# 1. 统计数据总览（真实数据聚合）
@router.get("/overview")
def get_statistics_overview(
    days: int = 14,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """基于 chat_records / thread 的真实问答数据，返回统计面板所需的全部数据。"""
    rows = _chat_record_rows(db)
    agg = aggregate_records(rows)

    threads = db.query(Thread).filter(Thread.status == 1).all()
    agent_counter = Counter((t.agent_id or "未知智能体") for t in threads)

    users = db.query(User).all()
    users_by_id = {u.id: u for u in users}

    # 最近动态：最近保存的对话
    recent_activity = []
    for r in rows[:10]:
        user = users_by_id.get(r.user_id)
        title = ""
        try:
            conv = json.loads(r["content"]) if r["content"] else {}
            if isinstance(conv, dict):
                title = conv.get("title", "") or ""
        except (ValueError, TypeError):
            title = ""
        recent_activity.append(
            {
                "time": r["updatetime"].strftime("%Y-%m-%d %H:%M") if r["updatetime"] else "",
                "username": user.username if user and user.username else f"用户{r['user_id']}",
                "title": title,
            }
        )

    totals = agg["totals"]
    totals["threads"] = len(threads)
    totals["active_users"] = len({r["user_id"] for r in rows})

    return {
        "status": "success",
        "data": {
            "totals": totals,
            "daily_trend": build_daily_trend(agg, days=days),
            "agent_distribution": [
                {"name": name, "value": count}
                for name, count in agent_counter.most_common()
            ],
            "hot_questions": agg["hot_questions"],
            "top_users": top_users(agg, users_by_id),
            "recent_activity": recent_activity,
        },
    }


# 2. 把真实热门问题同步进 questions 表（供社区讨论/求助使用）
@router.post("/sync-questions")
def sync_questions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """聚合真实对话里的高频问题，按标题 upsert 到 questions 表；并清理早期演示数据。"""
    rows = _chat_record_rows(db)
    agg = aggregate_records(rows)
    counter = agg["hot_counter"]

    synced = 0
    updated = 0
    now = datetime.now()
    for title, count in counter.items():
        existing = db.query(Question).filter(Question.title == title).first()
        if existing:
            existing.count = count
            existing.last_asked = now
            updated += 1
        else:
            db.add(
                Question(
                    title=title,
                    count=count,
                    category="用户提问",
                    description="",
                    last_asked=now,
                )
            )
            synced += 1

    # 清理早期 seed 的演示数据（有关联的讨论/求助时不删）
    removed = 0
    for seed_title in MOCK_SEED_TITLES:
        row = db.query(Question).filter(Question.title == seed_title).first()
        if not row:
            continue
        has_link = (
            db.query(func.count(Discussion.id))
            .filter(Discussion.question_id == row.id)
            .scalar()
            or db.query(func.count(HelpRequest.id))
            .filter(HelpRequest.question_id == row.id)
            .scalar()
        )
        if not has_link:
            db.delete(row)
            removed += 1

    db.commit()
    total = db.query(Question).count()
    logger.info(
        "sync-questions done: synced=%d updated=%d removed=%d total=%d",
        synced,
        updated,
        removed,
        total,
    )
    return {
        "status": "success",
        "message": "热门问题已同步",
        "data": {"synced": synced, "updated": updated, "removed": removed, "total": total},
    }


# 3. 获取热门问题列表（社区板块数据源，由 sync-questions 填充）
@router.get("/top-questions")
def get_top_questions(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    questions = db.query(Question).order_by(desc(Question.count)).limit(limit).all()

    result = []
    for q in questions:
        # 统计关联数量
        disc_count = db.query(func.count(Discussion.id)).filter(Discussion.question_id == q.id).scalar()
        help_count = db.query(func.count(HelpRequest.id)).filter(HelpRequest.question_id == q.id).scalar()

        result.append(
            {
                "id": q.id,
                "title": q.title,
                "count": q.count,
                "category": q.category,
                "lastAsked": q.last_asked.strftime("%Y-%m-%d") if q.last_asked else "",
                "discussionCount": disc_count,
                "helpCount": help_count,
                "description": q.description,
            }
        )

    return {"status": "success", "data": result}


# 4. 获取问题的讨论列表
@router.get("/questions/{question_id}/discussions")
def get_question_discussions(
    question_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    discussions = (
        db.query(Discussion)
        .filter(Discussion.question_id == question_id)
        .order_by(Discussion.created_at)
        .all()
    )

    data = []
    for d in discussions:
        data.append(
            {
                "id": d.id,
                "author": d.author_name,
                "avatar": d.avatar or "",
                "time": d.created_at.strftime("%Y-%m-%d %H:%M"),
                "content": d.content,
            }
        )

    return {"status": "success", "data": data}


# 5. 发布讨论评论
@router.post("/questions/{question_id}/discussions")
def create_discussion(
    question_id: int,
    discussion: DiscussionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    # 验证问题是否存在
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    new_discussion = Discussion(
        question_id=question_id,
        content=discussion.content,
        author_name=current_user.username or "当前用户",
        created_at=datetime.now(),
    )
    db.add(new_discussion)
    db.commit()
    db.refresh(new_discussion)

    return {
        "status": "success",
        "message": "评论发布成功",
        "data": {
            "id": new_discussion.id,
            "content": new_discussion.content,
            "time": new_discussion.created_at.strftime("%Y-%m-%d %H:%M"),
        },
    }


# 6. 发布求助
@router.post("/help-requests")
def create_help_request(
    request: HelpRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    q = db.query(Question).filter(Question.id == request.questionId).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    new_help = HelpRequest(
        question_id=request.questionId,
        title=request.title,
        description=request.description,
        email=request.email,
        created_at=datetime.now(),
    )
    db.add(new_help)
    db.commit()
    db.refresh(new_help)

    return {
        "status": "success",
        "message": "求助发布成功",
        "data": {
            "id": new_help.id,
            "createdAt": new_help.created_at.strftime("%Y-%m-%d %H:%M"),
        },
    }
