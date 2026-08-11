"""审计服务：所有敏感操作写 operation_logs 表。

设计约束：
- 独立会话：审计写入失败只记录 warning，绝不影响业务主流程。
- 白名单脱敏：details 只允许写入 AUDIT_DETAIL_WHITELIST 中的字段，
  任何疑似密钥的键（password/token/secret/api_key 等）一律丢弃。
- 可测试性：session_factory 为类属性，测试可注入假会话工厂，
  未注入时延迟导入 db_manager（避免模块导入即触发 src/Milvus 初始化）。
"""

import json
import logging
import re

logger = logging.getLogger("sage.audit")
# 说明：使用标准 logging 而非 src.utils.logging_config.logger，
# 避免模块导入即触发 src/__init__（Milvus 连接），保证本地可测试性。
# 应用内已配置日志处理器，本 logger 自动继承。

# 允许写入审计详情的字段白名单
AUDIT_DETAIL_WHITELIST = frozenset({
    "resource_type",
    "resource_id",
    "status",
    "username",
    "role",
    "model_name",
    "model_base",
    "filename",
    "file_id",
    "db_id",
    "size_bytes",
    "extension",
    "conversation_id",
    "message_id",
    "suite_name",
    "suite_id",
    "case_count",
    "rule_name",
    "rule_type",
    "backup_id",
    "reason",
    "count",
    "rating",
    "confidentiality",
    "domain",
    "knowledge_type",
    "owner_department",
    "download_allowed",
    "version",
    "format",
})

# 命中即丢弃的密钥提示词（黑名单，双重保险）
_SECRET_KEY_HINTS = re.compile(
    r"password|passwd|secret|token|api_?key|apikey|smtp|jwt|private|master.?key|"
    r"milvus.?password|mysql.?password|credential|mms.?key|access.?key",
    re.IGNORECASE,
)


def sanitize_detail(detail):
    """将 details 字典脱敏为白名单内的字段。"""
    if not detail or not isinstance(detail, dict):
        return {}
    safe = {}
    for key, value in detail.items():
        if not isinstance(key, str):
            continue
        if key not in AUDIT_DETAIL_WHITELIST:
            continue
        if _SECRET_KEY_HINTS.search(key):
            continue
        # 值也必须可安全序列化
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


class AuditService:
    """审计服务（全静态/类方法，可直接复用）。"""

    # 测试可注入；默认延迟导入 db_manager.get_session
    session_factory = None

    @classmethod
    def _get_session(cls):
        if cls.session_factory is None:
            from server.db_manager import db_manager

            cls.session_factory = db_manager.get_session
        return cls.session_factory()

    @classmethod
    def record(
        cls,
        action,
        user_id=None,
        resource_type=None,
        resource_id=None,
        status="success",
        detail=None,
        ip=None,
    ):
        """写入一条审计日志。任何异常都只降级为 warning，不向外抛出。

        action: 审计动作码，如 feedback.submit / config.update / backup.create
        resource_type / resource_id: 资源标识
        status: success / failed
        detail: 脱敏后的详情字典
        ip: 客户端 IP
        """
        from server.models.user_model import OperationLog

        session = None
        try:
            session = cls._get_session()
            payload = sanitize_detail(detail)
            payload["status"] = status
            if resource_type:
                payload["resource_type"] = resource_type
            if resource_id is not None:
                payload["resource_id"] = str(resource_id)

            entry = OperationLog(
                user_id=user_id,
                operation=action,
                details=json.dumps(payload, ensure_ascii=False, default=str),
                ip_address=ip,
            )
            session.add(entry)
            session.commit()
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
            logger.warning("Audit record failed for action=%s", action)
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
