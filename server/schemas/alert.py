"""告警规则请求模型（P2-3：严格类型/枚举/邮件格式校验，替代裸 dict）。"""

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

# 与 server.services.alert_service.RULE_TYPES 保持一致
RuleType = Literal[
    "disk_space",
    "sqlite_check",
    "milvus",
    "neo4j",
    "gpu_mem",
    "backup_fail",
]

# 基础邮箱格式校验（不引入 email-validator 依赖）
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AlertRuleCreate(BaseModel):
    """创建告警规则。rule_type 必须属于白名单枚举。"""

    name: str = Field(..., min_length=1, max_length=255, description="规则名称")
    rule_type: RuleType
    enabled: bool = True
    threshold: Optional[Union[str, int, float]] = Field(
        default=None, description="阈值（字符串形式，如磁盘百分比）"
    )
    cooldown_seconds: int = Field(3600, ge=0, description="冷却时间（秒）")
    notify_email: Optional[str] = Field(
        default=None, max_length=255, pattern=_EMAIL_PATTERN, description="通知邮箱"
    )


class AlertRuleUpdate(BaseModel):
    """更新告警规则。全部可选；显式 null 可清空 threshold / notify_email。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    rule_type: Optional[RuleType] = None
    enabled: Optional[bool] = None
    threshold: Optional[Union[str, int, float]] = None
    cooldown_seconds: Optional[int] = Field(default=None, ge=0)
    notify_email: Optional[str] = Field(default=None, max_length=255, pattern=_EMAIL_PATTERN)


class TestEmailPayload(BaseModel):
    """SMTP 测试邮件收件人。"""

    to_email: str = Field(..., pattern=_EMAIL_PATTERN, description="测试收件人邮箱")
