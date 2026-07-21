# server/models/statistics_model.py

from sqlalchemy import Column, Integer, Text, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from server.models import Base
from datetime import datetime

class Question(Base):
    """问题表"""
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True) # 技术, 工程, 环保 等
    count = Column(Integer, default=0) # 提问/查看次数
    last_asked = Column(DateTime, default=datetime.now)

    # 关联关系
    discussions = relationship("Discussion", back_populates="question", cascade="all, delete-orphan")
    help_requests = relationship("HelpRequest", back_populates="question", cascade="all, delete-orphan")

class Discussion(Base):
    """讨论/评论表"""
    __tablename__ = "discussions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(Integer, ForeignKey('questions.id'), nullable=False)
    content = Column(Text, nullable=False)
    author_name = Column(String(100), default="匿名用户") # 简化处理，暂存用户名
    avatar = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.now)

    question = relationship("Question", back_populates="discussions")

class HelpRequest(Base):
    """专家求助表"""
    __tablename__ = "help_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question_id = Column(Integer, ForeignKey('questions.id'), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    email = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.now)

    question = relationship("Question", back_populates="help_requests")
