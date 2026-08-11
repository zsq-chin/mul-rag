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

from sqlalchemy import func
from sqlalchemy.orm import Session

logger = logging.getLogger("sage.audit")

# 已知动作码词汇表（GET /api/audit/actions 返回）。
# 覆盖计划要求的全部动作码，外加本项目实际写入的扩展动作码。
KNOWN_ACTIONS = [
    "auth.login",
    "user.create",
    "user.update",
    "user.delete",
    "model.create",
    "model.update",
    "model.delete",
    "model.select",
    "knowledge.upload",
    "knowledge.delete",
    "knowledge.metadata.update",
    "knowledge.download",
    "knowledge.export",
    "knowledge.sync",
    "knowledge.version.snapshot",
    "feedback.upsert",
    "feedback.delete",
    "evaluation.suite.create",
    "evaluation.suite.update",
    "evaluation.suite.delete",
    "evaluation.case.create",
    "evaluation.case.update",
    "evaluation.case.delete",
    "evaluation.import",
    "config.update",
    "config.rollback",
    "backup.create",
    "backup.restore",
    "backup.verify",
    "backup.delete",
    "backup.download",
    "alert.rule.create",
    "alert.rule.update",
    "alert.rule.delete",
    "alert.event.acknowledge",
    "alert.email.test",
]
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
    "api_base",
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


# --- 只读查询（供 /api/audit 接口使用，db 由调用方注入） ---


def _parse_details(raw) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def serialize_event(row, username=None) -> dict:
    """把 OperationLog 行序列化为审计事件。details 内的 status/resource 提升到顶层。"""
    details = _parse_details(getattr(row, "details", None))
    return {
        "id": row.id,
        "user_id": row.user_id,
        "username": username,
        "action": row.operation,
        "status": details.get("status", "success"),
        "resource_type": details.get("resource_type"),
        "resource_id": details.get("resource_id"),
        "details": details,
        "ip_address": row.ip_address,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


def list_events(
    db: Session,
    user: str = "",
    action: str = "",
    resource_type: str = "",
    status: str = "",
    start=None,
    end=None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """筛选分页查询审计事件。user 按用户名模糊匹配，其余字段精确匹配。"""
    from server.models.user_model import OperationLog, User

    query = db.query(OperationLog, User.username).join(User, OperationLog.user_id == User.id)
    if user:
        query = query.filter(User.username.ilike(f"%{user}%"))
    if action:
        query = query.filter(OperationLog.operation == action)
    if resource_type:
        query = query.filter(func.json_extract(OperationLog.details, "$.resource_type") == resource_type)
    if status:
        query = query.filter(func.json_extract(OperationLog.details, "$.status") == status)
    if start is not None:
        query = query.filter(OperationLog.timestamp >= start)
    if end is not None:
        query = query.filter(OperationLog.timestamp <= end)

    total = query.count()
    rows = (
        query.order_by(OperationLog.timestamp.desc(), OperationLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [serialize_event(log, username) for log, username in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def get_event(db: Session, event_id: int):
    """按 ID 查询单条审计事件；不存在返回 None。"""
    from server.models.user_model import OperationLog, User

    row = (
        db.query(OperationLog, User.username)
        .join(User, OperationLog.user_id == User.id)
        .filter(OperationLog.id == event_id)
        .first()
    )
    if row is None:
        return None
    log, username = row
    return serialize_event(log, username)


def list_actions() -> list:
    """返回已知动作码词汇表。"""
    return list(KNOWN_ACTIONS)
