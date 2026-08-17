"""知识字典请求/响应模型（设计文档 §13）。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DictionaryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="字典名称")
    description: Optional[str] = Field(default=None, max_length=2000)
    domain: Optional[str] = Field(default=None, max_length=255)


class DictionaryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    domain: Optional[str] = Field(default=None, max_length=255)


class SourceSpec(BaseModel):
    """互斥来源结构：一次请求只允许一种来源（§13.2）。"""

    kind: Literal["kb_file", "kb", "upload"]
    db_id: Optional[str] = None
    file_id: Optional[str] = None
    storage_ref: Optional[str] = None
    file_name: Optional[str] = None

    @model_validator(mode="after")
    def _check_exclusive(self) -> "SourceSpec":
        if self.kind == "kb_file" and (not self.db_id or not self.file_id):
            raise ValueError("kb_file 来源需要 db_id 与 file_id")
        if self.kind == "kb" and not self.db_id:
            raise ValueError("kb 来源需要 db_id")
        if self.kind == "upload" and not self.storage_ref:
            raise ValueError("upload 来源需要 storage_ref")
        return self


class GenerateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    domain: Optional[str] = Field(default=None, max_length=255)
    dictionary_id: Optional[int] = None
    model_id: Optional[int] = None  # 用户已保存模型的 ID；密钥只在后端解密
    categories: Optional[List[str]] = None
    use_seed: bool = True
    duplicate_policy: Literal["merge"] = "merge"
    source: SourceSpec


class EvidenceIn(BaseModel):
    source_id: Optional[int] = None
    node_id: Optional[str] = Field(default=None, max_length=128)
    field_path: Optional[str] = Field(default=None, max_length=255)
    quote: str = Field(..., min_length=1, max_length=4000)
    page_no: Optional[str] = Field(default=None, max_length=50)
    sheet_name: Optional[str] = Field(default=None, max_length=255)
    cell_range: Optional[str] = Field(default=None, max_length=100)
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    inferred: bool = False
    locator_metadata: Optional[Dict[str, Any]] = None


class EntryCreate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=255)
    standard_name: str = Field(..., min_length=1, max_length=255)
    definition: str = Field(..., min_length=1, max_length=20000)
    unit: Optional[str] = Field(default=None, max_length=100)
    data_type: Optional[str] = Field(default=None, max_length=20)
    synonyms: Optional[List[str]] = None
    value_rule: Optional[str] = Field(default=None, max_length=4000)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_note: Optional[str] = Field(default=None, max_length=2000)
    evidences: Optional[List[EvidenceIn]] = None


class EntryUpdate(BaseModel):
    category: Optional[str] = Field(default=None, max_length=255)
    standard_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    definition: Optional[str] = Field(default=None, min_length=1, max_length=20000)
    unit: Optional[str] = Field(default=None, max_length=100)
    data_type: Optional[str] = Field(default=None, max_length=20)
    synonyms: Optional[List[str]] = None
    value_rule: Optional[str] = Field(default=None, max_length=4000)
    evidences: Optional[List[EvidenceIn]] = None


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "reset"]
    note: Optional[str] = Field(default=None, max_length=2000)


class BatchReviewItem(BaseModel):
    entry_id: int
    action: Literal["approve", "reject", "reset"]
    note: Optional[str] = Field(default=None, max_length=2000)


class BatchReviewRequest(BaseModel):
    items: List[BatchReviewItem] = Field(..., min_length=1, max_length=500)
    concurrency_token: Optional[str] = None
    allow_low_confidence: bool = False


class MergeRequest(BaseModel):
    keep_entry_id: int
    merge_entry_ids: List[int] = Field(..., min_length=1, max_length=200)
    review_note: Optional[str] = Field(default=None, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    dictionary_ids: Optional[List[int]] = None
    top_k: int = Field(default=5, ge=1, le=20)
    version_id: Optional[int] = None
    include_draft: bool = False
