# server/routers/statistics_router.py

from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from server.db_manager import db_manager
from server.models.statistics_model import Question, Discussion, HelpRequest
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

# --- 辅助函数：初始化数据 ---
def seed_initial_data(db: Session):
    """如果没有数据，则写入一些初始模拟数据"""
    if db.query(Question).count() == 0:
        logger.info("Initializing statistics sample data...")
        mock_questions = [
            { "title": '水力压裂的最优泵入速率是多少？', "count": 245, "category": '技术', "description": '在不同地层条件下，如何确定水力压裂的最优泵入速率？' },
            { "title": '压裂液配方选择的关键因素', "count": 189, "category": '工程', "description": '如何根据地层特性选择合适的压裂液配方？' },
            { "title": '支撑剂粒度对压裂效果的影响', "count": 156, "category": '技术', "description": '不同支撑剂粒度如何影响压裂后的产能？' },
            { "title": '压裂污水处理与回收技术', "count": 134, "category": '环保', "description": '如何高效处理和回收压裂过程中产生的污水？' },
            { "title": '压裂诱导裂缝方向控制', "count": 127, "category": '技术', "description": '如何有效控制压裂过程中诱导裂缝的扩展方向？' },
            { "title": '多段塞式压裂设计方案', "count": 98, "category": '工程', "description": '多段塞式压裂的具体施工参数如何优化？' },
        ]
        
        for item in mock_questions:
            q = Question(
                title=item["title"],
                count=item["count"],
                category=item["category"],
                description=item["description"],
                last_asked=datetime.now()
            )
            db.add(q)
        
        # 添加一条示例评论
        db.flush() # 获取ID
        first_q = db.query(Question).first()
        if first_q:
            d = Discussion(question_id=first_q.id, content="我们团队最近也遇到这个问题，可以考虑使用图分析的并行化方案。", author_name="张三")
            db.add(d)
            
        db.commit()

# --- API Endpoints ---

# 1. 获取热门问题列表
@router.get("/top-questions")
def get_top_questions(limit: int = 10, db: Session = Depends(get_db)):
    # 检查并初始化数据
    seed_initial_data(db)
    
    questions = db.query(Question).order_by(desc(Question.count)).limit(limit).all()
    
    result = []
    for q in questions:
        # 统计关联数量
        disc_count = db.query(func.count(Discussion.id)).filter(Discussion.question_id == q.id).scalar()
        help_count = db.query(func.count(HelpRequest.id)).filter(HelpRequest.question_id == q.id).scalar()
        
        result.append({
            "id": q.id,
            "title": q.title,
            "count": q.count,
            "category": q.category,
            "lastAsked": q.last_asked.strftime("%Y-%m-%d") if q.last_asked else "",
            "discussionCount": disc_count,
            "helpCount": help_count,
            "description": q.description
        })
        
    return {"status": "success", "data": result}

# 2. 获取问题的讨论列表
@router.get("/questions/{question_id}/discussions")
def get_question_discussions(question_id: int, db: Session = Depends(get_db)):
    discussions = db.query(Discussion).filter(Discussion.question_id == question_id).order_by(Discussion.created_at).all()
    
    data = []
    for d in discussions:
        data.append({
            "id": d.id,
            "author": d.author_name,
            "avatar": d.avatar or "",
            "time": d.created_at.strftime("%Y-%m-%d %H:%M"),
            "content": d.content
        })
        
    return {"status": "success", "data": data}

# 3. 发布讨论评论
@router.post("/questions/{question_id}/discussions")
def create_discussion(question_id: int, discussion: DiscussionCreate, db: Session = Depends(get_db)):
    # 验证问题是否存在
    q = db.query(Question).filter(Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
        
    new_discussion = Discussion(
        question_id=question_id,
        content=discussion.content,
        author_name="当前用户", # 这里可以对接真实用户系统
        created_at=datetime.now()
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
            "time": new_discussion.created_at.strftime("%Y-%m-%d %H:%M")
        }
    }

# 4. 发布求助
@router.post("/help-requests")
def create_help_request(request: HelpRequestCreate, db: Session = Depends(get_db)):
    q = db.query(Question).filter(Question.id == request.questionId).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
        
    new_help = HelpRequest(
        question_id=request.questionId,
        title=request.title,
        description=request.description,
        email=request.email,
        created_at=datetime.now()
    )
    db.add(new_help)
    db.commit()
    db.refresh(new_help)
    
    return {
        "status": "success",
        "message": "求助发布成功",
        "data": {
            "id": new_help.id,
            "createdAt": new_help.created_at.strftime("%Y-%m-%d %H:%M")
        }
    }