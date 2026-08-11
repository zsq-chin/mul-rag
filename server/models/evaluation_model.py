"""问答测试集模型：测试集 + 测试用例。"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from server.models import Base


class EvaluationSuite(Base):
    """问答测试集。"""
    __tablename__ = "evaluation_suites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    cases = relationship("EvaluationCase", back_populates="suite", cascade="all, delete-orphan")


class EvaluationCase(Base):
    """测试用例。"""
    __tablename__ = "evaluation_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suite_id = Column(Integer, ForeignKey("evaluation_suites.id", ondelete="CASCADE"), nullable=False, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=True)  # 标准答案
    key_points = Column(Text, nullable=True)  # JSON 字符串数组
    kb_id = Column(String(100), nullable=True)  # 知识库标识
    category = Column(String(50), nullable=True)
    difficulty = Column(String(20), nullable=True)  # easy / medium / hard
    enabled = Column(Integer, nullable=False, default=1)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    suite = relationship("EvaluationSuite", back_populates="cases")
