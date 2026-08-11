"""系统运维模型：配置历史、备份任务、告警规则与告警事件。"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from server.models import Base


class ConfigChangeHistory(Base):
    """系统配置修改历史（修改前/后快照、操作人、说明）。"""
    __tablename__ = "config_change_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_key = Column(String(255), nullable=False, index=True)
    before_value = Column(Text, nullable=True)
    after_value = Column(Text, nullable=True)
    operator = Column(String(100), nullable=True)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())


class BackupJob(Base):
    """备份任务记录。"""
    __tablename__ = "backup_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False)  # 相对 saves/backups 的路径，不出现在 API 响应
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)
    manifest_version = Column(String(20), nullable=False, default="1")
    status = Column(String(20), nullable=False, default="completed")  # completed / failed
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    verified_at = Column(DateTime, nullable=True)
    note = Column(String(255), nullable=True)


class AlertRule(Base):
    """告警规则。"""
    __tablename__ = "alert_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    # disk_space / sqlite_check / milvus / neo4j / gpu_mem / backup_fail
    rule_type = Column(String(50), nullable=False)
    enabled = Column(Integer, nullable=False, default=1)
    threshold = Column(String(50), nullable=True)  # 灵活阈值，字符串存储
    cooldown_seconds = Column(Integer, nullable=False, default=3600)
    notify_email = Column(String(255), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AlertEvent(Base):
    """告警事件（触发/恢复/确认状态）。"""
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(50), nullable=False)
    severity = Column(String(20), nullable=False, default="warning")
    status = Column(String(20), nullable=False, default="firing")  # firing / resolved / acknowledged
    message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    resolved_at = Column(DateTime, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
