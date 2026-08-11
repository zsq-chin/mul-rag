"""知识治理请求/响应模型。"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class GovernanceUpdate(BaseModel):
    """治理字段编辑请求。全部可选，服务端只更新出现的字段。"""

    domain: Optional[str] = Field(default=None, max_length=100, description="专业领域")
    knowledge_type: Optional[str] = Field(
        default=None, max_length=50, description="报告/论文/设计图/日志/标准/其他"
    )
    confidentiality: Optional[Literal["public", "internal", "restricted"]] = None
    tags: Optional[List[str]] = None  # 字符串数组
    download_allowed: Optional[bool] = None  # 是否允许下载
    owner_department: Optional[str] = Field(default=None, max_length=100, description="责任部门")
    source_updated_at: Optional[datetime] = None  # 来源更新时间
