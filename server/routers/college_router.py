from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import  Optional
from server.db_manager_college import db_manager_college
from server.models_college.college_models import Users, GuideRecords

college = APIRouter(prefix="/k80")

def get_db_session():
    with db_manager_college.get_session_context() as session:
        yield session

@college.get("/users")
def get_users(
    username: Optional[str] = None,
    role: Optional[str] = None,
    db: Session = Depends(get_db_session),
):
    """查询用户列表"""
    query = db.query(Users)
    
    if username:
        query = query.filter(Users.username.contains(username))
    if role:
        query = query.filter(Users.role == role)
        
    return query.order_by(Users.id).all()

@college.get("/users/{user_id}")
def get_user(
    user_id: int,
    db: Session = Depends(get_db_session),
):
    """根据ID查询用户详情"""
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user

@college.get("/guide_records")
def get_guide_records(
    guide_id: Optional[str] = None,
    user_id: Optional[int] = None,
    db: Session = Depends(get_db_session),
):
    """查询指南记录列表"""
    query = db.query(GuideRecords)
    
    if guide_id:
        query = query.filter(GuideRecords.guide_id.contains(guide_id))
    if user_id:
        query = query.filter(GuideRecords.user_id == user_id)
        
    return query.order_by(GuideRecords.id).all()

@college.get("/guide_records/{record_id}")
def get_guide_record(
    record_id: int,
    db: Session = Depends(get_db_session),
):
    """根据ID查询指南记录详情"""
    record = db.query(GuideRecords).filter(GuideRecords.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="指南记录不存在")
    return record

@college.get("/user_guide_records")
def get_user_guide_records(
    user_id: int,
    db: Session = Depends(get_db_session),
):
    """查询用户的所有指南记录"""
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
        
    return db.query(GuideRecords).filter(
        GuideRecords.user_id == user_id
    ).order_by(GuideRecords.updatetime.desc()).all()
