from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from server.models import Base
from datetime import datetime
from pytz import timezone

class ChatRecord(Base):
    """聊天记录表"""
    __tablename__ = "chat_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conv_id = Column(Text, nullable=False)  # 会话ID
    content = Column(Text, nullable=False)  # 存储 JSON 字符串
    updatetime = Column(DateTime)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    user = relationship("User", back_populates="chat_records")

class GuideRecord(Base):
    """引导记录表"""
    __tablename__ = "guide_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guide_id = Column(Text, nullable=False)  # 引导ID
    content = Column(Text, nullable=False)  # 存储 JSON 字符串
    updatetime = Column(DateTime)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    user = relationship("User", back_populates="guide_records")


class WriterRecord(Base):
    """写作达人记录表"""
    __tablename__ = "writer_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conv_id = Column(Text, nullable=False)  # 会话ID
    content = Column(Text, nullable=False)  # 存储 JSON 字符串
    updatetime = Column(DateTime)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    user = relationship("User", back_populates="writer_records")


class ItemRecord(Base):
    """题目生成记录表"""
    __tablename__ = "item_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Text, nullable=False)  # 题目ID
    content = Column(Text, nullable=False)  # 存储 JSON 字符串
    structured_content = Column(Text, nullable=True)  # 结构化题目
    createdtime = Column(DateTime)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    user = relationship("User", back_populates="item_records")


class ExamPapersRecord(Base):
    """题目生成记录表"""
    __tablename__ = "exam_papers_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exam_paper_id = Column(Text, nullable=False)  # 试卷ID
    content = Column(Text, nullable=False)  # 存储 JSON 字符串
    submission_content = Column(Text, nullable=True)  # 存储 JSON 字符串
    createdtime = Column(DateTime)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    # 记录双向关系，user->exam_papers_records, exam_papers_record->user
    # 这样就能实现 双向访问：
    # 可以通过 ExamPapersRecords.user 拿到对应的用户对象
    # 可以通过 User.exam_papers_records 拿到该用户的所有试卷记录
    user = relationship("User", back_populates="exam_papers_records")