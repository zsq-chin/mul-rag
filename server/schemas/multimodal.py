"""远端多模态 RAG 接口的显式 Pydantic DTO（阶段 A2 契约冻结）。

本模块把远端 OpenAPI v1.0.0（saves/remote_openapi.json，md5
5397c63053743950c0e9ccbc3d3951f0）实际观察到的响应形状固化为 DTO。
SAGE 只依赖这里声明的字段解析远端；字段缺失 / 类型变化 / 接口版本变化时，
调用方应明确报「远端接口版本不兼容」，而不是静默返回空结果。

设计约定：
- 未知字段一律忽略（extra="ignore"），保证远端新增字段不会导致本机解析失败；
- 必需字段缺失或类型错误会抛出 pydantic.ValidationError，由上层捕获并报告不兼容；
- top_k（k）限制为 1..20（阶段 B1 的输入校验要求在请求侧同样生效）。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RemoteBase(BaseModel):
    """远端响应基类：忽略未知字段，向前兼容。"""

    model_config = ConfigDict(extra="ignore")


class RemoteHealth(RemoteBase):
    """GET /api/v1/health -> {"ok": true, "version": "1.0.0"}。"""

    ok: bool = False
    version: str = ""


class RemoteKbItem(RemoteBase):
    """GET /api/v1/kb/list 的单个知识库条目。

    embedModel / vectorStoreType / kbName 远端可能返回 null（如尚未配置
    向量模型的知识库），一律按可选处理，回落空串。
    """

    kbId: str = Field(..., min_length=1)
    kbName: Optional[str] = None
    vectorStoreType: Optional[str] = None
    embedModel: Optional[str] = None
    fileCount: int = 0
    createdAt: Optional[int] = None


class RemoteKbListResponse(RemoteBase):
    """GET /api/v1/kb/list 响应：{"kbs": [...]}。"""

    kbs: list[RemoteKbItem] = Field(default_factory=list)


class RemoteSearchRequest(RemoteBase):
    """POST /api/v1/index/search 请求体。

    远端契约要求 kbId、query 必填；fileId 可选；k 默认 5。
    B1 要求 top_k 限制在 1..20：这里在请求侧强制，超界直接拒绝。
    """

    kbId: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    fileId: Optional[str] = None
    k: int = Field(5, ge=1, le=20)

    @field_validator("k", mode="before")
    @classmethod
    def _coerce_none_k(cls, v: Any) -> Any:
        # 显式传入 null 时回落默认值，而不是校验失败
        return 5 if v is None else v


class RemoteSearchResult(RemoteBase):
    """POST /api/v1/index/search 的单个命中条目。

    source 是 JSON 字符串（含 kb_id / page / type / image_path / caption 等），
    SAGE 解析时按 string 接收，避免与远端 schema 变化耦合。
    """

    id: Optional[Any] = None
    score: Optional[float] = None
    entity_key: Optional[str] = None
    source: Optional[str] = None
    chunk_text: Optional[str] = None
    fileName: Optional[str] = None
    fileId: Optional[str] = None
    page: Optional[Any] = None


class RemoteSearchResponse(RemoteBase):
    """POST /api/v1/index/search 响应：{"ok": bool, "results": [...]}。"""

    ok: bool = False
    message: Optional[str] = None
    results: list[RemoteSearchResult] = Field(default_factory=list)


class RemoteImageItem(RemoteBase):
    """GET /api/v1/kb/images 的单个图片目录条目（轻量元数据）。"""

    img_name: str = ""
    summary: str = ""
    page_num: Optional[int] = None
    source_page_num: Optional[int] = None
    original_img_name: str = ""
    fileId: Optional[str] = None
    fileName: str = ""


class RemoteImagePage(RemoteBase):
    """GET /api/v1/kb/images 响应：真实服务端分页。

    固定形状 items/page/pageSize/total；pageSize 最大 100、默认 24。
    """

    items: list[RemoteImageItem] = Field(default_factory=list)
    page: int = 1
    pageSize: int = 24
    total: int = 0

    @field_validator("page", "pageSize", "total", mode="before")
    @classmethod
    def _coerce_ints(cls, v: Any) -> Any:
        if v is None:
            return 0
        return v
