from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta
from server.models import Base

class CASSession(Base):
    """CAS Session表 - 专门存储CAS登录的session token"""
    __tablename__ = "cas_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_token = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_token = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)

    # 与用户表的关联
    user = relationship("User", back_populates="cas_sessions")

    def __repr__(self):
        return f"<CASSession(id={self.id}, user_id={self.user_id}, expires_at={self.expires_at})>"

    def is_expired(self) -> bool:
        """检查session是否已过期"""
        return datetime.now() > self.expires_at

# 创建索引
Index('idx_cas_sessions_token', CASSession.session_token)
Index('idx_cas_sessions_expires', CASSession.expires_at)
Index('idx_cas_sessions_user', CASSession.user_id)
