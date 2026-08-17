"""知识字典 Repository：关系数据读写与事务边界（设计文档 §5/§6）。

规则：
- 只使用 SQLAlchemy Session，不直接触碰 Milvus；
- 返回序列化后的字典（不含 SQLAlchemy 内部状态）；
- 业务不变量（发布门禁、审核状态机等）在 service 层，本模块只做 CRUD 与查询。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from server.models.knowledge_dictionary_models import (
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionaryJob,
    KnowledgeDictionarySource,
    KnowledgeDictionaryVersion,
)

from .errors import NotFound


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def serialize_dictionary(d: KnowledgeDictionary, *, with_counts: bool = False) -> Dict[str, Any]:
    out = {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "domain": d.domain,
        "status": d.status,
        "active_version_id": d.active_version_id,
        "created_by": d.created_by,
        "updated_by": d.updated_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
    if with_counts:
        out["entry_count"] = getattr(d, "_entry_count", 0)
    return out


def serialize_version(v: KnowledgeDictionaryVersion, *, detail: bool = False) -> Dict[str, Any]:
    out = {
        "id": v.id,
        "dictionary_id": v.dictionary_id,
        "version_no": v.version_no,
        "status": v.status,
        "source_snapshot_hash": v.source_snapshot_hash,
        "generation_config": v.generation_config,
        "embedding_config_hash": v.embedding_config_hash,
        "index_status": v.index_status,
        "entry_count": v.entry_count,
        "pending_count": v.pending_count,
        "conflict_count": v.conflict_count,
        "vector_count": v.vector_count,
        "created_by": v.created_by,
        "published_by": v.published_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
        "published_at": v.published_at.isoformat() if v.published_at else None,
    }
    return out


def serialize_source(s: KnowledgeDictionarySource) -> Dict[str, Any]:
    return {
        "id": s.id,
        "version_id": s.version_id,
        "source_type": s.source_type,
        "knowledge_base_id": s.knowledge_base_id,
        "file_id": s.file_id,
        "file_name": s.file_name,
        "storage_ref": s.storage_ref,
        "content_hash": s.content_hash,
        "parser_version": s.parser_version,
        "snapshot_metadata": s.snapshot_metadata,
    }


def serialize_entry(e: KnowledgeDictionaryEntry, *, include_evidence: bool = False) -> Dict[str, Any]:
    out = {
        "id": e.id,
        "version_id": e.version_id,
        "category": e.category,
        "standard_name": e.standard_name,
        "normalized_name": e.normalized_name,
        "definition": e.definition,
        "unit": e.unit,
        "normalized_unit": e.normalized_unit,
        "data_type": e.data_type,
        "synonyms": _parse_json(e.synonyms, []),
        "value_rule": e.value_rule,
        "review_status": e.review_status,
        "confidence": round(float(e.confidence or 0.0), 4),
        "review_note": e.review_note,
        "index_status": e.index_status,
        "merged_from": _parse_json(e.merged_from, None),
        "created_by": e.created_by,
        "reviewed_by": e.reviewed_by,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        "reviewed_at": e.reviewed_at.isoformat() if e.reviewed_at else None,
    }
    if include_evidence:
        out["evidences"] = [serialize_evidence(ev) for ev in e.evidences]
    return out


def serialize_evidence(ev: KnowledgeDictionaryEvidence) -> Dict[str, Any]:
    return {
        "id": ev.id,
        "entry_id": ev.entry_id,
        "source_id": ev.source_id,
        "node_id": ev.node_id,
        "field_path": ev.field_path,
        "quote": ev.quote,
        "page_no": ev.page_no,
        "sheet_name": ev.sheet_name,
        "cell_range": ev.cell_range,
        "start_offset": ev.start_offset,
        "end_offset": ev.end_offset,
        "inferred": bool(ev.inferred),
        "locator_metadata": ev.locator_metadata,
        "evidence_hash": ev.evidence_hash,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


def serialize_job(j: KnowledgeDictionaryJob) -> Dict[str, Any]:
    return {
        "id": j.id,
        "job_type": j.job_type,
        "dictionary_id": j.dictionary_id,
        "version_id": j.version_id,
        "status": j.status,
        "stage": j.stage,
        "progress": round(float(j.progress or 0.0), 2),
        "input_config": j.input_config,
        "processed_files": j.processed_files,
        "processed_chunks": j.processed_chunks,
        "candidate_count": j.candidate_count,
        "merged_count": j.merged_count,
        "conflict_count": j.conflict_count,
        "pending_count": j.pending_count,
        "rejected_count": j.rejected_count,
        "failed_count": j.failed_count,
        "error_summary": j.error_summary,
        "requested_by": j.requested_by,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
    }


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def get_dictionary(db: Session, dictionary_id: int, *, include_deleted: bool = False) -> KnowledgeDictionary:
    q = db.query(KnowledgeDictionary).filter(KnowledgeDictionary.id == dictionary_id)
    if not include_deleted:
        q = q.filter(KnowledgeDictionary.is_deleted == 0)
    d = q.first()
    if d is None:
        raise NotFound(f"字典不存在: {dictionary_id}")
    return d


def get_version(db: Session, version_id: int) -> KnowledgeDictionaryVersion:
    v = db.query(KnowledgeDictionaryVersion).filter(KnowledgeDictionaryVersion.id == version_id).first()
    if v is None:
        raise NotFound(f"版本不存在: {version_id}")
    return v


def get_version_of_dictionary(db: Session, dictionary_id: int, version_id: int) -> KnowledgeDictionaryVersion:
    v = (
        db.query(KnowledgeDictionaryVersion)
        .filter(
            KnowledgeDictionaryVersion.id == version_id,
            KnowledgeDictionaryVersion.dictionary_id == dictionary_id,
        )
        .first()
    )
    if v is None:
        raise NotFound(f"版本不存在: {version_id}")
    return v


def get_entry(db: Session, entry_id: int, *, with_evidence: bool = False) -> KnowledgeDictionaryEntry:
    q = db.query(KnowledgeDictionaryEntry)
    if with_evidence:
        q = q.options(joinedload(KnowledgeDictionaryEntry.evidences))
    e = q.filter(KnowledgeDictionaryEntry.id == entry_id).first()
    if e is None:
        raise NotFound(f"条目不存在: {entry_id}")
    return e


def get_job(db: Session, job_id: int) -> KnowledgeDictionaryJob:
    j = db.query(KnowledgeDictionaryJob).filter(KnowledgeDictionaryJob.id == job_id).first()
    if j is None:
        raise NotFound(f"任务不存在: {job_id}")
    return j


def list_dictionaries(
    db: Session,
    *,
    keyword: str = "",
    status: str = "",
    domain: str = "",
    created_by: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[KnowledgeDictionary], int]:
    q = db.query(KnowledgeDictionary).filter(KnowledgeDictionary.is_deleted == 0)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(KnowledgeDictionary.name.like(like))
    if status:
        q = q.filter(KnowledgeDictionary.status == status)
    if domain:
        q = q.filter(KnowledgeDictionary.domain == domain)
    if created_by is not None:
        q = q.filter(KnowledgeDictionary.created_by == created_by)
    total = q.count()
    rows = q.order_by(KnowledgeDictionary.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def next_version_no(db: Session, dictionary_id: int) -> int:
    current = (
        db.query(func.max(KnowledgeDictionaryVersion.version_no))
        .filter(KnowledgeDictionaryVersion.dictionary_id == dictionary_id)
        .scalar()
    )
    return (current or 0) + 1


def create_next_draft_version(db: Session, user: Any, dictionary_id: int) -> KnowledgeDictionaryVersion:
    """为现有字典创建下一个草稿版本（generate 流程复用，§13.2）。

    业务不变量：同一字典至多存在一个非终态版本（draft/reviewing），
    避免条目归属歧义。
    """
    from .errors import Conflict

    existing = (
        db.query(KnowledgeDictionaryVersion)
        .filter(
            KnowledgeDictionaryVersion.dictionary_id == dictionary_id,
            KnowledgeDictionaryVersion.status.in_(("draft", "reviewing")),
        )
        .first()
    )
    if existing is not None:
        raise Conflict(f"字典已存在草稿/审核中的版本 V{existing.version_no}，请先完成或放弃该版本")
    version = KnowledgeDictionaryVersion(
        dictionary_id=dictionary_id,
        version_no=next_version_no(db, dictionary_id),
        status="draft",
        index_status="pending",
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    return version


def find_dictionary_by_name(db: Session, name: str, *, exclude_id: Optional[int] = None) -> Optional[KnowledgeDictionary]:
    q = db.query(KnowledgeDictionary).filter(
        KnowledgeDictionary.name == name,
        KnowledgeDictionary.is_deleted == 0,
    )
    if exclude_id is not None:
        q = q.filter(KnowledgeDictionary.id != exclude_id)
    return q.first()


def list_versions(db: Session, dictionary_id: int) -> List[KnowledgeDictionaryVersion]:
    return (
        db.query(KnowledgeDictionaryVersion)
        .filter(KnowledgeDictionaryVersion.dictionary_id == dictionary_id)
        .order_by(KnowledgeDictionaryVersion.version_no.desc())
        .all()
    )


def list_entries(
    db: Session,
    version_id: int,
    *,
    category: str = "",
    review_status: str = "",
    keyword: str = "",
    source_file: str = "",
    min_confidence: Optional[float] = None,
    missing_fields: bool = False,
    conflict_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[KnowledgeDictionaryEntry], int]:
    q = db.query(KnowledgeDictionaryEntry).filter(KnowledgeDictionaryEntry.version_id == version_id)
    if category:
        q = q.filter(KnowledgeDictionaryEntry.category == category)
    if review_status:
        q = q.filter(KnowledgeDictionaryEntry.review_status == review_status)
    if conflict_only:
        q = q.filter(KnowledgeDictionaryEntry.review_status == "conflict")
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(
            KnowledgeDictionaryEntry.standard_name.like(like)
            | KnowledgeDictionaryEntry.definition.like(like)
        )
    if min_confidence is not None:
        q = q.filter(KnowledgeDictionaryEntry.confidence >= min_confidence)
    if missing_fields:
        from sqlalchemy import or_

        q = q.filter(
            or_(
                KnowledgeDictionaryEntry.definition.is_(None),
                KnowledgeDictionaryEntry.definition == "",
                KnowledgeDictionaryEntry.standard_name.is_(None),
                KnowledgeDictionaryEntry.standard_name == "",
            )
        )
    if source_file:
        # 证据引用按来源文件筛选：子查询
        entry_ids = (
            db.query(KnowledgeDictionaryEvidence.entry_id)
            .join(KnowledgeDictionarySource, KnowledgeDictionaryEvidence.source_id == KnowledgeDictionarySource.id)
            .filter(KnowledgeDictionarySource.file_name.like(f"%{source_file}%"))
            .distinct()
            .subquery()
        )
        q = q.filter(KnowledgeDictionaryEntry.id.in_(entry_ids))
    total = q.count()
    rows = (
        q.order_by(KnowledgeDictionaryEntry.review_status.asc(), KnowledgeDictionaryEntry.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def count_entries_by_status(db: Session, version_id: int) -> Dict[str, int]:
    rows = (
        db.query(KnowledgeDictionaryEntry.review_status, func.count(KnowledgeDictionaryEntry.id))
        .filter(KnowledgeDictionaryEntry.version_id == version_id)
        .group_by(KnowledgeDictionaryEntry.review_status)
        .all()
    )
    return {status: int(count) for status, count in rows}


def refresh_version_counts(db: Session, version: KnowledgeDictionaryVersion) -> None:
    """按条目真实状态重算版本计数（条目数/待审核/冲突）。"""
    counts = count_entries_by_status(db, version.id)
    version.entry_count = sum(counts.values())
    version.pending_count = counts.get("pending", 0) + counts.get("conflict", 0)
    version.conflict_count = counts.get("conflict", 0)


def active_jobs_for_version(db: Session, version_id: int, job_types: Optional[List[str]] = None) -> List[KnowledgeDictionaryJob]:
    q = db.query(KnowledgeDictionaryJob).filter(
        KnowledgeDictionaryJob.version_id == version_id,
        KnowledgeDictionaryJob.status.in_(["queued", "running", "cancelling"]),
    )
    if job_types:
        q = q.filter(KnowledgeDictionaryJob.job_type.in_(job_types))
    return q.all()


def count_active_jobs(db: Session, job_type: str) -> int:
    return (
        db.query(func.count(KnowledgeDictionaryJob.id))
        .filter(
            KnowledgeDictionaryJob.job_type == job_type,
            KnowledgeDictionaryJob.status.in_(["queued", "running", "cancelling"]),
        )
        .scalar()
        or 0
    )
