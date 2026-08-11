"""回答反馈请求/响应模型。"""

from typing import Optional

from pydantic import BaseModel, Field


class FeedbackCreate(BaseModel):
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    rating: str = Field(..., description="up | down")
    reason: Optional[str] = Field(default=None, max_length=255)
    comment: Optional[str] = Field(default=None, max_length=2000)


class FeedbackRead(BaseModel):
    id: int
    message_id: str
    conversation_id: Optional[str]
    rating: str
    reason: Optional[str]
    comment: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
