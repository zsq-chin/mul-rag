"""知识治理模型：分级分类元数据 + 文档版本历史。"""

from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from server.models import Base


class KnowledgeGovernance(Base):
    """知识文档治理元数据（按 db_id + file_id 唯一）。

    confidentiality: public / internal / restricted
    tags: JSON 字符串数组（如 '["标准", "安全"]'）
    usage_count 只能由服务端在预览/下载时更新。
    """
    __tablename__ = "knowledge_governance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    db_id = Column(String, nullable=False, index=True)
    file_id = Column(String, nullable=False, index=True)
    domain = Column(String(100), nullable=True)  # 专业领域
    knowledge_type = Column(String(50), nullable=True)  # 报告/论文/设计图/日志/标准/其他
    confidentiality = Column(String(20), nullable=False, default="internal")
    tags = Column(Text, nullable=True)  # JSON 字符串数组
    download_allowed = Column(Integer, nullable=False, default=1)  # 1 允许 / 0 禁止
    owner_department = Column(String(100), nullable=True)  # 责任部门
    source_updated_at = Column(DateTime, nullable=True)  # 来源更新时间
    usage_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint("db_id", "file_id", name="uq_governance_db_file"),)


class KnowledgeDocumentVersion(Base):
    """知识文档版本快照（元数据 + 源文件校验值）。

    版本号在 (db_id, file_id) 内单调递增，并发创建不重号。
    只记录源文件副本/校验值，不触发重建索引。
    """
    __tablename__ = "knowledge_document_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    db_id = Column(String, nullable=False, index=True)
    file_id = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)  # 单调递增版本号
    sha256 = Column(String(64), nullable=True)  # 源文件 SHA-256
    file_size = Column(Integer, nullable=True)
    metadata_snapshot = Column(Text, nullable=True)  # 治理元数据 JSON 快照
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    note = Column(String(255), nullable=True)

    __table_args__ = (UniqueConstraint("db_id", "file_id", "version", name="uq_version_db_file_ver"),)
