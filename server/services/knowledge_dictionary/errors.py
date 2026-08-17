"""知识字典统一错误模型（设计文档 §14.1）。

所有业务错误统一结构：
    {"error_code": "...", "message": "...", "trace_id": "...", "details": {}}

错误码稳定、可测试；trace_id 由路由层附加（不包含敏感信息）。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional


class DictionaryError(Exception):
    """知识字典业务错误基类。"""

    error_code: str = "DICTIONARY_ERROR"
    status_code: int = 400

    def __init__(
        self,
        message: str,
        *,
        error_code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "trace_id": uuid.uuid4().hex[:16],
            "details": self.details,
        }


class NotFound(DictionaryError):
    error_code = "DICTIONARY_NOT_FOUND"
    status_code = 404


class Forbidden(DictionaryError):
    error_code = "DICTIONARY_FORBIDDEN"
    status_code = 403


class Conflict(DictionaryError):
    error_code = "DICTIONARY_CONFLICT"
    status_code = 409


class ValidationError(DictionaryError):
    error_code = "DICTIONARY_VALIDATION_ERROR"
    status_code = 422


class PayloadTooLarge(DictionaryError):
    error_code = "DICTIONARY_UPLOAD_TOO_LARGE"
    status_code = 413


class UnsupportedMediaType(DictionaryError):
    error_code = "DICTIONARY_UNSUPPORTED_FILE"
    status_code = 415


class RateLimited(DictionaryError):
    error_code = "DICTIONARY_RATE_LIMITED"
    status_code = 429


class ServiceUnavailable(DictionaryError):
    error_code = "DICTIONARY_SERVICE_UNAVAILABLE"
    status_code = 503


class SourceChanged(Conflict):
    """来源快照相对提交时发生变化，阻止发布（§11.2）。"""

    error_code = "DICTIONARY_SOURCE_CHANGED"


class PublishBlocked(Conflict):
    """发布条件不满足（§11.2 任一阻断条件）。"""

    error_code = "DICTIONARY_PUBLISH_BLOCKED"


class InvalidSource(DictionaryError):
    """三种来源互斥或来源参数非法（§13.2 / §14.1 400）。"""

    error_code = "DICTIONARY_INVALID_SOURCE"
    status_code = 400


class JobConflict(Conflict):
    """重复活动任务 / 任务状态不允许该操作。"""

    error_code = "DICTIONARY_JOB_CONFLICT"


class ExtractionFailed(ValidationError):
    """候选抽取/结构化输出校验失败。"""

    error_code = "DICTIONARY_EXTRACTION_FAILED"
