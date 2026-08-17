"""知识字典数据模型（knowledge dictionary）。

依据 docs/superpowers/specs/2026-08-16-knowledge-dictionary-generation-design.md §6：
关系数据库（SQLite，经 db_manager）是字典、版本、条目、证据与任务的唯一事实来源；
Milvus 只是可重建的派生索引。

六张表：
- knowledge_dictionaries           字典主记录（软删除）
- knowledge_dictionary_versions    版本（draft/reviewing/published/withdrawn + 索引状态）
- knowledge_dictionary_sources     来源快照（三种来源统一建模）
- knowledge_dictionary_entries     条目（审核状态、置信度、内容哈希、向量 id）
- knowledge_dictionary_evidences   证据（必须可定位回来源快照）
- knowledge_dictionary_jobs        持久化任务（租约、心跳、检查点、取消/重试/中断恢复）
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from server.models import Base

# ---------------------------------------------------------------------------
# 状态枚举（与设计文档一致）
# ---------------------------------------------------------------------------

DICTIONARY_STATUSES = ("draft", "published", "withdrawn")
VERSION_STATUSES = ("draft", "reviewing", "published", "withdrawn")
VERSION_INDEX_STATUSES = ("pending", "embedding", "indexed", "verified", "ready", "failed")
SOURCE_TYPES = ("knowledge_base_file", "knowledge_base", "upload")
ENTRY_REVIEW_STATUSES = ("pending", "approved", "rejected", "conflict")
ENTRY_INDEX_STATUSES = ("pending", "indexed", "deleted")
JOB_TYPES = ("generate", "index", "import_seed", "export")
JOB_STATUSES = ("queued", "running", "cancelling", "cancelled", "completed", "failed", "interrupted")

# 受控数据类型枚举（设计文档 §6.4：未知类型保留为 string 候选并标记待审核）
DATA_TYPES = ("string", "number", "integer", "boolean", "date", "datetime", "enum", "range", "text")


class KnowledgeDictionary(Base):
    """字典主记录。名称在未删除字典中唯一；活动版本必须已发布且索引 ready。"""

    __tablename__ = "knowledge_dictionaries"
    __table_args__ = (
        # 软删除 + 名称唯一：仅约束未删除行（SQLite 支持部分索引）
        Index(
            "uq_kd_name_active",
            "name",
            unique=True,
            sqlite_where=text("is_deleted = 0"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    domain = Column(String(255), nullable=True)  # 专业领域
    status = Column(String(20), nullable=False, default="draft", index=True)
    active_version_id = Column(Integer, ForeignKey("knowledge_dictionary_versions.id"), nullable=True)
    is_deleted = Column(Integer, nullable=False, default=0, index=True)
    seed_meta = Column(JSON, nullable=True)  # 种子迁移元数据（importer_version/source_hash 等）
    created_by = Column(Integer, nullable=True, index=True)
    updated_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    versions = relationship(
        "KnowledgeDictionaryVersion",
        back_populates="dictionary",
        order_by="KnowledgeDictionaryVersion.version_no",
        cascade="all, delete-orphan",
        foreign_keys="KnowledgeDictionaryVersion.dictionary_id",
    )


class KnowledgeDictionaryVersion(Base):
    """字典版本：内容不可变；变更通过创建新草稿版本进行。"""

    __tablename__ = "knowledge_dictionary_versions"
    __table_args__ = (
        UniqueConstraint("dictionary_id", "version_no", name="uq_kd_version_no"),
        Index("ix_kd_version_dict_status", "dictionary_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dictionary_id = Column(
        Integer,
        ForeignKey("knowledge_dictionaries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_no = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="draft")
    source_snapshot_hash = Column(String(64), nullable=True)  # 来源快照哈希（发布前比对）
    generation_config = Column(JSON, nullable=True)  # 脱敏后的模型 ID、提示词/规则版本
    embedding_config_hash = Column(String(64), nullable=True)
    index_status = Column(String(20), nullable=False, default="pending", index=True)
    entry_count = Column(Integer, nullable=False, default=0)
    pending_count = Column(Integer, nullable=False, default=0)
    conflict_count = Column(Integer, nullable=False, default=0)
    vector_count = Column(Integer, nullable=False, default=0)
    created_by = Column(Integer, nullable=True)
    published_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    published_at = Column(DateTime, nullable=True)

    dictionary = relationship(
        "KnowledgeDictionary",
        back_populates="versions",
        foreign_keys=[dictionary_id],
    )
    sources = relationship(
        "KnowledgeDictionarySource",
        back_populates="version",
        cascade="all, delete-orphan",
    )
    entries = relationship(
        "KnowledgeDictionaryEntry",
        back_populates="version",
        cascade="all, delete-orphan",
    )


class KnowledgeDictionarySource(Base):
    """来源快照：整个知识库来源为每个文件建立一行，支持变更检测与精确溯源。"""

    __tablename__ = "knowledge_dictionary_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version_id = Column(
        Integer,
        ForeignKey("knowledge_dictionary_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type = Column(String(30), nullable=False)  # knowledge_base_file / knowledge_base / upload
    knowledge_base_id = Column(String(255), nullable=True)
    file_id = Column(String(255), nullable=True)
    file_name = Column(String(512), nullable=True)
    storage_ref = Column(String(512), nullable=True)  # 受控存储标识，不保存任意绝对路径
    content_hash = Column(String(64), nullable=True)
    parser_version = Column(String(40), nullable=True)
    snapshot_metadata = Column(JSON, nullable=True)

    version = relationship("KnowledgeDictionaryVersion", back_populates="sources")
    evidences = relationship(
        "KnowledgeDictionaryEvidence", back_populates="source", cascade="all, delete-orphan"
    )


class KnowledgeDictionaryEntry(Base):
    """字典条目。standard_name、definition 与至少一条有效证据是发布必填。"""

    __tablename__ = "knowledge_dictionary_entries"
    __table_args__ = (
        Index("ix_kd_entry_version_review", "version_id", "review_status"),
        Index("ix_kd_entry_version_name", "version_id", "normalized_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    version_id = Column(
        Integer,
        ForeignKey("knowledge_dictionary_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String(255), nullable=True)
    standard_name = Column(String(255), nullable=False)
    normalized_name = Column(String(255), nullable=False, index=True)
    definition = Column(Text, nullable=False)
    unit = Column(String(100), nullable=True)
    normalized_unit = Column(String(100), nullable=True)
    data_type = Column(String(20), nullable=False, default="string")
    synonyms = Column(JSON, nullable=True)  # JSON 数组
    value_rule = Column(Text, nullable=True)  # 可读说明和可选结构化约束
    review_status = Column(String(20), nullable=False, default="pending", index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    review_note = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=True)
    vector_id = Column(String(128), nullable=True)
    index_status = Column(String(20), nullable=False, default="pending")
    merged_from = Column(JSON, nullable=True)  # 人工合并保留的原条目 ID 与快照
    created_by = Column(Integer, nullable=True)
    reviewed_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    reviewed_at = Column(DateTime, nullable=True)

    version = relationship("KnowledgeDictionaryVersion", back_populates="entries")
    evidences = relationship(
        "KnowledgeDictionaryEvidence", back_populates="entry", cascade="all, delete-orphan"
    )


class KnowledgeDictionaryEvidence(Base):
    """来源证据：必须能定位到来源快照（页码/工作表/单元格/节点 ID/偏移量）。"""

    __tablename__ = "knowledge_dictionary_evidences"
    __table_args__ = (Index("ix_kd_evidence_entry", "entry_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    entry_id = Column(
        Integer,
        ForeignKey("knowledge_dictionary_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id = Column(
        Integer,
        ForeignKey("knowledge_dictionary_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    node_id = Column(String(128), nullable=True)  # KnowledgeNode.id 或适配器节点 ID
    field_path = Column(String(255), nullable=True)  # 证据支持的字段（standard_name/definition/unit/...）
    quote = Column(Text, nullable=False)
    page_no = Column(String(50), nullable=True)
    sheet_name = Column(String(255), nullable=True)
    cell_range = Column(String(100), nullable=True)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    inferred = Column(Integer, nullable=False, default=0)  # 1=模型推断，待审核标记
    locator_metadata = Column(JSON, nullable=True)
    evidence_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=func.now())

    entry = relationship("KnowledgeDictionaryEntry", back_populates="evidences")
    source = relationship("KnowledgeDictionarySource", back_populates="evidences")


class KnowledgeDictionaryJob(Base):
    """持久化任务：租约、心跳、检查点、取消与中断恢复。

    API 进程只创建任务；dictionary-worker 通过租约领取工作。
    任务配置（input_config）绝不含 API Key。
    """

    __tablename__ = "knowledge_dictionary_jobs"
    __table_args__ = (
        Index("ix_kd_job_status", "status"),
        Index("ix_kd_job_version", "version_id"),
        Index("ix_kd_job_lease", "job_type", "status", "lease_expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_type = Column(String(20), nullable=False)  # generate / index / import_seed / export
    dictionary_id = Column(Integer, ForeignKey("knowledge_dictionaries.id"), nullable=True, index=True)
    version_id = Column(Integer, ForeignKey("knowledge_dictionary_versions.id"), nullable=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    stage = Column(String(50), nullable=True)
    progress = Column(Float, nullable=False, default=0.0)
    input_config = Column(JSON, nullable=True)  # 脱敏后的配置快照
    checkpoint = Column(JSON, nullable=True)  # 可恢复检查点
    processed_files = Column(Integer, nullable=False, default=0)
    processed_chunks = Column(Integer, nullable=False, default=0)
    candidate_count = Column(Integer, nullable=False, default=0)
    merged_count = Column(Integer, nullable=False, default=0)
    conflict_count = Column(Integer, nullable=False, default=0)
    pending_count = Column(Integer, nullable=False, default=0)
    rejected_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    error_summary = Column(Text, nullable=True)  # 脱敏错误摘要
    lease_owner = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    requested_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
