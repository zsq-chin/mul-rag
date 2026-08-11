"""问答测试集请求/响应模型。"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class EvaluationSuiteCreate(BaseModel):
    name: str = Field(..., max_length=255, description="测试集名称")
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)


class EvaluationSuiteUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=50)


class EvaluationCasePayload(BaseModel):
    """用例字段（创建时 question 必填；更新时全部可选）。"""

    question: Optional[str] = Field(default=None, description="问题")
    answer: Optional[str] = Field(default=None, description="标准答案")
    key_points: Optional[List[str]] = None  # 关键要点数组
    kb_id: Optional[str] = Field(default=None, max_length=100, description="知识库标识")
    category: Optional[str] = Field(default=None, max_length=50)
    difficulty: Optional[Literal["easy", "medium", "hard"]] = None
    enabled: Optional[bool] = None
    note: Optional[str] = Field(default=None, description="备注")
