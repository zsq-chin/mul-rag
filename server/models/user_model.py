from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from server.models import Base

class User(Base):
    """用户模型"""
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True) # 本地用户ID或者CAS系统中的用户ID
    username = Column(String, nullable=True, unique=True, index=True) # 本地用户名或者CAS系统中的用户名
    password_hash = Column(String, nullable=True)  # 密码哈希，CAS用户可为空
    role = Column(String, nullable=False, default='user')  # 角色: superadmin, admin, user
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime, nullable=True)
    is_cas_user = Column(Boolean, default=False)  # 是否为CAS用户

    # 关联操作日志
    operation_logs = relationship("OperationLog", back_populates="user", passive_deletes=True)

    # 关联CAS Session
    cas_sessions = relationship("CASSession", back_populates="user", passive_deletes=True)

    model_credentials = relationship(
        "UserModelCredential",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self, include_password=False):
        result = {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "is_cas_user": self.is_cas_user,
        }
        if include_password:
            result["password_hash"] = self.password_hash
        return result

class OperationLog(Base):
    """操作日志模型"""
    __tablename__ = 'operation_logs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    operation = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String, nullable=True)
    timestamp = Column(DateTime, default=func.now())

    # 关联用户
    user = relationship("User", back_populates="operation_logs")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "operation": self.operation,
            "details": self.details,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }

# 在文件底部导入 ChatRecord 并添加关系
from server.models.chat_model import ChatRecord
User.chat_records = relationship("ChatRecord", back_populates="user", passive_deletes=True)

# 在文件底部导入 GuideRecord 并添加关系
from server.models.chat_model import GuideRecord
User.guide_records = relationship("GuideRecord", back_populates="user", passive_deletes=True)

# 在文件底部导入 WriterRecord 并添加关系
from server.models.chat_model import WriterRecord
User.writer_records = relationship("WriterRecord", back_populates="user", passive_deletes=True)

# 在文件底部导入 ItemRecord 并添加关系
from server.models.chat_model import ItemRecord
User.item_records = relationship("ItemRecord", back_populates="user", passive_deletes=True)

# 在文件底部导入 ExamPapersRecord 并添加关系
from server.models.chat_model import ExamPapersRecord
User.exam_papers_records = relationship("ExamPapersRecord", back_populates="user", passive_deletes=True)

# 在文件底部导入 CASSession 并添加关系
from server.models.cas_session_model import CASSession
User.cas_sessions = relationship("CASSession", back_populates="user", passive_deletes=True)

from server.models.user_model_credential import UserModelCredential
