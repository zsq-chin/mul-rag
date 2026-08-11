"""问答反馈模型。"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from server.models import Base


class AnswerFeedback(Base):
    """回答反馈表：点赞/点踩真实入库。

    约束：
    - (user_id, message_id) 唯一，重复点击走 upsert，不产生重复记录。
    - 只保存消息标识与评价，不重复保存完整回答正文。
    - 删除用户时级联删除其反馈（外键开启 + CASCADE）。
    """
    __tablename__ = "answer_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id = Column(String(64), nullable=False, index=True)  # 消息ID
    conversation_id = Column(String(64), nullable=True, index=True)  # 会话ID
    rating = Column(String(8), nullable=False)  # up | down
    reason = Column(String(255), nullable=True)  # 点踩可选原因
    comment = Column(Text, nullable=True)  # 可选补充说明
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("user_id", "message_id", name="uq_feedback_user_message"),)
