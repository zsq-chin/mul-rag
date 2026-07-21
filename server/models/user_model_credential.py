from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from server.models import Base


class UserModelCredential(Base):
    __tablename__ = "user_model_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "display_name", name="uq_user_model_display_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name = Column(String(100), nullable=False)
    provider = Column(String(40), nullable=False, default="openai-compatible")
    model_name = Column(String(200), nullable=False)
    api_base = Column(String(500), nullable=False)
    encrypted_api_key = Column(Text, nullable=False)
    key_hint = Column(String(4), nullable=False, default="")
    key_version = Column(Integer, nullable=False, default=1)
    last_used_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="model_credentials")
