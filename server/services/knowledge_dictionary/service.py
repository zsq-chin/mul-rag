"""知识字典核心 Service：版本、审核、发布、撤回与业务不变量（设计文档 §5/§11）。

依赖关系：service -> repository + normalizer + permissions；
Milvus/模型/文件解析均通过其他模块（vector_indexer / source_adapters）惰性接入，
保证本模块可在无 Milvus、无模型的环境下独立单元测试。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from server.models.knowledge_dictionary_models import (
    DATA_TYPES,
    ENTRY_REVIEW_STATUSES,
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionarySource,
    KnowledgeDictionaryVersion,
)

from . import repository as repo
from .errors import Conflict, Forbidden, NotFound, PublishBlocked, SourceChanged, ValidationError
from .normalizer import (
    are_units_compatible,
    content_hash,
    map_data_type,
    merge_synonyms,
    normalize_name,
    normalize_synonyms,
    normalize_unit,
)
from .permissions import ensure_can_read_version, ensure_manager, is_manager

# ---------------------------------------------------------------------------
# 版本/状态机辅助
# ---------------------------------------------------------------------------

_MUTABLE_VERSION_STATUSES = frozenset({"draft", "reviewing"})
_TERMINAL_VERSION_STATUSES = frozenset({"published", "withdrawn"})


def _ensure_mutable_version(version: KnowledgeDictionaryVersion) -> None:
    if version.status not in _MUTABLE_VERSION_STATUSES:
        raise Conflict(f"版本状态为 {version.status}，条目已不可修改；如需修订请创建新版本")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# 字典 CRUD
# ---------------------------------------------------------------------------


def list_dictionaries(
    db: Session,
    user: Any,
    *,
    keyword: str = "",
    status: str = "",
    domain: str = "",
    created_by: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    rows, total = repo.list_dictionaries(
        db,
        keyword=keyword,
        status=status,
        domain=domain,
        created_by=created_by,
        page=page,
        page_size=page_size,
    )
    items = []
    for d in rows:
        item = repo.serialize_dictionary(d)
        active = (
            db.query(KnowledgeDictionaryVersion)
            .filter(KnowledgeDictionaryVersion.id == d.active_version_id)
            .first()
            if d.active_version_id
            else None
        )
        if active is not None:
            item["active_version"] = {
                "version_no": active.version_no,
                "status": active.status,
                "index_status": active.index_status,
                "entry_count": active.entry_count,
                "published_at": active.published_at.isoformat() if active.published_at else None,
            }
        item["source_types"] = _source_type_summary(db, d.id)
        # 普通用户只能看到已发布字典
        if not is_manager(user) and d.status != "published":
            item["versions_unavailable"] = True
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _source_type_summary(db: Session, dictionary_id: int) -> List[str]:
    rows = (
        db.query(KnowledgeDictionarySource.source_type)
        .join(
            KnowledgeDictionaryVersion,
            KnowledgeDictionarySource.version_id == KnowledgeDictionaryVersion.id,
        )
        .filter(KnowledgeDictionaryVersion.dictionary_id == dictionary_id)
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows if r[0]})


def get_dictionary_detail(db: Session, user: Any, dictionary_id: int) -> Dict[str, Any]:
    d = repo.get_dictionary(db, dictionary_id)
    if not is_manager(user) and d.status != "published":
        raise Forbidden("普通用户只能查看已发布的活动版本")
    out = repo.serialize_dictionary(d)
    versions = repo.list_versions(db, d.id)
    active = d.active_version_id
    visible = []
    for v in versions:
        try:
            ensure_can_read_version(user, v.status, d.status, v.id == active)
        except Forbidden:
            visible.append({"id": v.id, "version_no": v.version_no, "status": v.status})
            continue
        visible.append(repo.serialize_version(v))
    out["versions"] = visible
    return out


def create_dictionary(
    db: Session, user: Any, *, name: str, description: str = "", domain: str = ""
) -> Dict[str, Any]:
    ensure_manager(user)
    name = (name or "").strip()
    if not name:
        raise ValidationError("字典名称不能为空")
    if len(name) > 255:
        raise ValidationError("字典名称过长（最多 255 字符）")
    if repo.find_dictionary_by_name(db, name) is not None:
        raise Conflict(f"字典名称已存在: {name}")
    dictionary = KnowledgeDictionary(
        name=name,
        description=(description or "").strip() or None,
        domain=(domain or "").strip() or None,
        status="draft",
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(dictionary)
    db.flush()
    version = KnowledgeDictionaryVersion(
        dictionary_id=dictionary.id,
        version_no=1,
        status="draft",
        index_status="pending",
        created_by=user.id,
        generation_config={"kind": "manual"},
    )
    db.add(version)
    db.commit()
    return repo.serialize_dictionary(db.query(KnowledgeDictionary).get(dictionary.id))


def update_dictionary(
    db: Session, user: Any, dictionary_id: int, *, name: str = "", description: str = "", domain: str = ""
) -> Dict[str, Any]:
    ensure_manager(user)
    d = repo.get_dictionary(db, dictionary_id)
    if name and name != d.name:
        if len(name) > 255:
            raise ValidationError("字典名称过长（最多 255 字符）")
        if repo.find_dictionary_by_name(db, name, exclude_id=d.id) is not None:
            raise Conflict(f"字典名称已存在: {name}")
        d.name = name
    if description:
        d.description = description
    if domain:
        d.domain = domain
    d.updated_by = user.id
    db.commit()
    return repo.serialize_dictionary(d)


def delete_dictionary(db: Session, user: Any, dictionary_id: int) -> Dict[str, Any]:
    """软删除：活动字典必须先撤回才能删除（设计文档 §4.2）。"""
    ensure_manager(user)
    d = repo.get_dictionary(db, dictionary_id)
    if d.status == "published" or d.active_version_id is not None:
        raise Conflict("活动字典必须先撤回才能删除")
    from server.models.knowledge_dictionary_models import KnowledgeDictionaryJob

    running = (
        db.query(KnowledgeDictionaryJob)
        .filter(
            KnowledgeDictionaryJob.dictionary_id == dictionary_id,
            KnowledgeDictionaryJob.status.in_(["queued", "running", "cancelling"]),
        )
        .count()
    )
    if running:
        raise Conflict("存在进行中的任务，请先取消或等待完成后再删除")
    d.is_deleted = 1
    d.updated_by = user.id
    db.commit()
    return {"deleted": True, "id": dictionary_id}


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------


def list_versions(db: Session, user: Any, dictionary_id: int) -> Dict[str, Any]:
    d = repo.get_dictionary(db, dictionary_id)
    items = []
    active = d.active_version_id
    for v in repo.list_versions(db, dictionary_id):
        try:
            ensure_can_read_version(user, v.status, d.status, v.id == active)
        except Forbidden:
            if not is_manager(user):
                continue
        items.append(repo.serialize_version(v))
    return {"items": items}


def get_version_detail(db: Session, user: Any, dictionary_id: int, version_id: int) -> Dict[str, Any]:
    d = repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    ensure_can_read_version(user, v.status, d.status, v.id == d.active_version_id)
    out = repo.serialize_version(v)
    out["sources"] = [
        repo.serialize_source(s)
        for s in db.query(KnowledgeDictionarySource)
        .filter(KnowledgeDictionarySource.version_id == v.id)
        .all()
    ]
    return out


# ---------------------------------------------------------------------------
# 发布 / 撤回（§11）
# ---------------------------------------------------------------------------


def _verify_source_snapshot(db: Session, version: KnowledgeDictionaryVersion) -> None:
    """发布前比对来源哈希：发生变化则阻止发布（§7.2 / §11.2）。

    哈希算法必须与创建任务时（source_adapters.create_source_rows / _snapshot_digest）
    完全一致：按来源排序后对 [source_type, knowledge_base_id, file_id, storage_ref,
    content_hash(当前值)] 列表做 sha256。两边结构不同会导致哈希永不相等、
    发布永远被误判为"来源已变化"。
    """
    if not version.source_snapshot_hash:
        return
    sources = (
        db.query(KnowledgeDictionarySource)
        .filter(KnowledgeDictionarySource.version_id == version.id)
        .order_by(KnowledgeDictionarySource.id)
        .all()
    )
    if not sources:
        return
    # 延迟导入适配器，避免无 Milvus/文件环境下的重量级依赖
    from .source_adapters import _current_content_hash, _snapshot_digest

    items = []
    changed = False
    for s in sources:
        current = _current_content_hash(db, s)
        if current != s.content_hash:
            changed = True
        items.append(
            {
                "source_type": s.source_type,
                "knowledge_base_id": s.knowledge_base_id,
                "file_id": s.file_id,
                "storage_ref": s.storage_ref,
                "content_hash": current,
            }
        )
    # 与创建时同构：按稳定键排序后再计算摘要
    items.sort(key=lambda item: (item.get("source_type") or "", item.get("file_id") or item.get("storage_ref") or ""))
    digest = _snapshot_digest(items)
    if digest != version.source_snapshot_hash or changed:
        raise SourceChanged("来源文件已变化，请重新生成后再发布")


def _verify_entry_evidence(db: Session, entry: KnowledgeDictionaryEntry) -> bool:
    count = (
        db.query(KnowledgeDictionaryEvidence)
        .filter(KnowledgeDictionaryEvidence.entry_id == entry.id)
        .count()
    )
    return count >= 1


def publish_version(db: Session, user: Any, dictionary_id: int, version_id: int) -> Dict[str, Any]:
    """发布门禁（§11.2）：任一条件不满足则 409，事务内原子更新活动版本。"""
    ensure_manager(user)
    d = repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    if v.status == "published":
        # 幂等：重复发布已发布版本直接返回成功
        return repo.serialize_version(v)

    from server.models.knowledge_dictionary_models import KnowledgeDictionaryJob

    blocked: List[str] = []

    if v.status not in ("draft", "reviewing"):
        blocked.append("版本状态不允许发布")

    # 1) 仍有 pending / conflict 条目
    pending = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == v.id,
            KnowledgeDictionaryEntry.review_status.in_(["pending", "conflict"]),
        )
        .count()
    )
    if pending:
        blocked.append(f"仍有 {pending} 条待审核或冲突条目未处理")

    # 2) 通过条目缺少标准名称、定义或有效证据
    approved = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == v.id,
            KnowledgeDictionaryEntry.review_status == "approved",
        )
        .all()
    )
    for e in approved:
        if not (e.standard_name or "").strip() or not (e.definition or "").strip():
            blocked.append(f"条目「{e.standard_name or e.id}」缺少标准名称或定义")
            break
        if not _verify_entry_evidence(db, e):
            blocked.append(f"条目「{e.standard_name or e.id}」缺少有效来源证据")
            break

    # 3) 来源快照变化
    try:
        _verify_source_snapshot(db, v)
    except SourceChanged as exc:
        blocked.append(str(exc))

    # 4) 索引状态不是 ready
    if v.index_status != "ready":
        blocked.append(f"索引状态为 {v.index_status}，需要 ready")

    # 5) 数据库条目计数与 Milvus 向量计数不一致
    if len(approved) != v.vector_count:
        blocked.append(f"数据库批准条目数({len(approved)})与向量数({v.vector_count})不一致")

    # 6) embedding 配置不一致
    if v.embedding_config_hash:
        from .vector_indexer import current_embedding_config_hash

        if v.embedding_config_hash != current_embedding_config_hash():
            blocked.append("当前 embedding 配置与索引配置不一致")

    # 7) 生成/索引任务仍在运行、取消中或失败
    bad_jobs = (
        db.query(KnowledgeDictionaryJob)
        .filter(
            KnowledgeDictionaryJob.version_id == v.id,
            KnowledgeDictionaryJob.job_type.in_(["generate", "index"]),
            KnowledgeDictionaryJob.status.in_(["queued", "running", "cancelling", "failed"]),
        )
        .count()
    )
    if bad_jobs:
        blocked.append("存在进行中、取消中或失败的任务")

    if blocked:
        raise PublishBlocked("发布条件不满足：" + "；".join(blocked), details={"reasons": blocked})

    # 事务内更新：旧活动版本撤回 + 目标版本发布 + 字典活动指针
    now = _now()
    v.status = "published"
    v.published_by = user.id
    v.published_at = now
    d.status = "published"
    d.active_version_id = v.id
    d.updated_by = user.id
    db.commit()
    return repo.serialize_version(v)


def withdraw_version(db: Session, user: Any, dictionary_id: int, version_id: int) -> Dict[str, Any]:
    """撤回只取消活动版本，不删除历史数据和审计证据（§11.1）。"""
    ensure_manager(user)
    d = repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    if d.active_version_id != v.id:
        raise Conflict("只能撤回当前活动版本")
    v.status = "withdrawn"
    d.status = "withdrawn"
    d.active_version_id = None
    d.updated_by = user.id
    db.commit()
    return repo.serialize_version(v)


# ---------------------------------------------------------------------------
# 条目 CRUD（仅草稿/审核中版本，§13.3）
# ---------------------------------------------------------------------------

_ENTRY_FIELDS = ("category", "standard_name", "definition", "unit", "data_type", "synonyms", "value_rule")


def _apply_entry_payload(entry: KnowledgeDictionaryEntry, payload: Dict[str, Any]) -> None:
    for field in _ENTRY_FIELDS:
        if field in payload and payload[field] is not None:
            setattr(entry, field, payload[field])
    if "synonyms" in payload:
        entry.synonyms = normalize_synonyms(payload.get("synonyms"))
    if "data_type" in payload and payload.get("data_type") is not None:
        entry.data_type = map_data_type(payload["data_type"])


def _refresh_entry(entry: KnowledgeDictionaryEntry, *, user: Any) -> None:
    """内容变更后重算规范化字段、内容哈希并把向量置为待索引。"""
    entry.normalized_name = normalize_name(entry.standard_name or "")
    entry.normalized_unit = normalize_unit(entry.unit)
    entry.data_type = map_data_type(entry.data_type)
    entry.synonyms = normalize_synonyms(entry.synonyms)
    entry.content_hash = content_hash(
        {
            "category": entry.category,
            "standard_name": entry.standard_name,
            "definition": entry.definition,
            "unit": entry.unit,
            "data_type": entry.data_type,
            "synonyms": entry.synonyms,
            "value_rule": entry.value_rule,
        }
    )
    entry.index_status = "pending"
    entry.updated_at = _now()


def _upsert_evidences(db: Session, entry: KnowledgeDictionaryEntry, version_id: int, evidences: List[Dict[str, Any]]) -> None:
    """重建条目证据（手工创建/编辑路径）。source_id 必须属于当前版本。"""
    valid_source_ids = {
        s.id
        for s in db.query(KnowledgeDictionarySource)
        .filter(KnowledgeDictionarySource.version_id == version_id)
        .all()
    }
    for ev in evidences:
        quote = str(ev.get("quote") or "").strip()
        if not quote:
            raise ValidationError("证据原文(quote)不能为空")
        source_id = ev.get("source_id")
        if source_id is not None and int(source_id) not in valid_source_ids:
            raise ValidationError(f"证据引用了不属于该版本的来源: {source_id}")
        db.add(
            KnowledgeDictionaryEvidence(
                entry_id=entry.id,
                source_id=source_id,
                node_id=ev.get("node_id"),
                field_path=ev.get("field_path"),
                quote=quote,
                page_no=ev.get("page_no"),
                sheet_name=ev.get("sheet_name"),
                cell_range=ev.get("cell_range"),
                start_offset=ev.get("start_offset"),
                end_offset=ev.get("end_offset"),
                inferred=1 if ev.get("inferred") else 0,
                locator_metadata=ev.get("locator_metadata"),
                evidence_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest()[:32],
            )
        )


def list_entries(
    db: Session,
    user: Any,
    dictionary_id: int,
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
) -> Dict[str, Any]:
    d = repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    ensure_can_read_version(user, v.status, d.status, v.id == d.active_version_id)
    if review_status and review_status not in ENTRY_REVIEW_STATUSES:
        raise ValidationError(f"非法审核状态: {review_status}")
    rows, total = repo.list_entries(
        db,
        version_id,
        category=category,
        review_status=review_status,
        keyword=keyword,
        source_file=source_file,
        min_confidence=min_confidence,
        missing_fields=missing_fields,
        conflict_only=conflict_only,
        page=page,
        page_size=page_size,
    )
    show_internal = is_manager(user)
    items = []
    for e in rows:
        item = repo.serialize_entry(e)
        if not show_internal:
            # 普通用户不展示内部置信度与审核备注（§4.6）
            item.pop("confidence", None)
            item.pop("review_note", None)
            item.pop("reviewed_by", None)
            item.pop("reviewed_at", None)
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def create_entry(
    db: Session, user: Any, dictionary_id: int, version_id: int, payload: Dict[str, Any]
) -> Dict[str, Any]:
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    standard_name = (payload.get("standard_name") or "").strip()
    definition = (payload.get("definition") or "").strip()
    if not standard_name or not definition:
        raise ValidationError("标准名称与定义为必填字段")
    entry = KnowledgeDictionaryEntry(
        version_id=v.id,
        created_by=user.id,
        review_status="pending",
        # 手工条目由管理员权威录入，默认高置信；生成管线条目使用模型信号计算的置信度
        confidence=payload.get("confidence") if payload.get("confidence") is not None else 1.0,
        review_note=payload.get("review_note"),
    )
    _apply_entry_payload(entry, payload)
    entry.standard_name = standard_name
    entry.definition = definition
    _refresh_entry(entry, user=user)
    db.add(entry)
    db.flush()
    evidences = payload.get("evidences") or []
    if evidences:
        _upsert_evidences(db, entry, v.id, evidences)
    repo.refresh_version_counts(db, v)
    db.commit()
    return repo.serialize_entry(entry, include_evidence=True)


def update_entry(
    db: Session, user: Any, dictionary_id: int, version_id: int, entry_id: int, payload: Dict[str, Any]
) -> Dict[str, Any]:
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    entry = repo.get_entry(db, entry_id, with_evidence=True)
    if entry.version_id != v.id:
        raise NotFound(f"条目不存在于该版本: {entry_id}")
    if "review_status" in payload and payload["review_status"] is not None:
        raise ValidationError("请使用审核接口修改审核状态")
    _apply_entry_payload(entry, payload)
    if not (entry.standard_name or "").strip() or not (entry.definition or "").strip():
        raise ValidationError("标准名称与定义为必填字段")
    _refresh_entry(entry, user=user)
    if "evidences" in payload and payload["evidences"] is not None:
        db.query(KnowledgeDictionaryEvidence).filter(
            KnowledgeDictionaryEvidence.entry_id == entry.id
        ).delete(synchronize_session=False)
        _upsert_evidences(db, entry, v.id, payload["evidences"])
    # 编辑后回到待审核，避免"改过的通过条目"静默保留通过状态
    if entry.review_status in ("approved", "rejected"):
        entry.review_status = "pending"
        entry.review_note = None
        entry.reviewed_by = None
        entry.reviewed_at = None
    repo.refresh_version_counts(db, v)
    db.commit()
    return repo.serialize_entry(entry, include_evidence=True)


def delete_entry(db: Session, user: Any, dictionary_id: int, version_id: int, entry_id: int) -> Dict[str, Any]:
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    entry = repo.get_entry(db, entry_id)
    if entry.version_id != v.id:
        raise NotFound(f"条目不存在于该版本: {entry_id}")
    db.delete(entry)
    repo.refresh_version_counts(db, v)
    db.commit()
    return {"deleted": True, "id": entry_id}


def get_entry_evidences(
    db: Session, user: Any, dictionary_id: int, version_id: int, entry_id: int
) -> Dict[str, Any]:
    d = repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    ensure_can_read_version(user, v.status, d.status, v.id == d.active_version_id)
    entry = repo.get_entry(db, entry_id, with_evidence=True)
    if entry.version_id != v.id:
        raise NotFound(f"条目不存在于该版本: {entry_id}")
    evidences = [
        repo.serialize_evidence(ev)
        for ev in sorted(entry.evidences, key=lambda x: x.id)
    ]
    # 附上来源文件名，便于前端展示证据抽屉
    source_ids = {ev.source_id for ev in entry.evidences if ev.source_id}
    source_names: Dict[int, str] = {}
    if source_ids:
        for s in (
            db.query(KnowledgeDictionarySource)
            .filter(KnowledgeDictionarySource.id.in_(source_ids))
            .all()
        ):
            source_names[s.id] = s.file_name or ""
    for item in evidences:
        item["source_file_name"] = source_names.get(item["source_id"], "")
    return {"items": evidences, "total": len(evidences)}


# ---------------------------------------------------------------------------
# 审核（§4.5 / §13.3）
# ---------------------------------------------------------------------------


def _review_one(
    db: Session, v: KnowledgeDictionaryVersion, entry: KnowledgeDictionaryEntry, action: str, note: str, user: Any
) -> None:
    if action not in ("approve", "reject", "reset"):
        raise ValidationError(f"非法审核动作: {action}")
    if action == "approve":
        if not (entry.standard_name or "").strip() or not (entry.definition or "").strip():
            raise ValidationError(f"条目「{entry.standard_name or entry.id}」缺少标准名称或定义，不能通过")
        if not _verify_entry_evidence(db, entry):
            raise ValidationError(f"条目「{entry.standard_name or entry.id}」缺少来源证据，不能通过")
        entry.review_status = "approved"
    elif action == "reject":
        entry.review_status = "rejected"
    else:  # reset -> pending
        entry.review_status = "pending"
    entry.review_note = (note or "").strip() or None
    entry.reviewed_by = user.id
    entry.reviewed_at = _now()
    entry.index_status = "pending"


def _advance_version_status(db: Session, v: KnowledgeDictionaryVersion) -> None:
    """状态机推进（§11.1）：草稿版本出现首个通过条目后进入 reviewing。"""
    if v.status == "draft":
        approved = (
            db.query(KnowledgeDictionaryEntry)
            .filter(
                KnowledgeDictionaryEntry.version_id == v.id,
                KnowledgeDictionaryEntry.review_status == "approved",
            )
            .count()
        )
        if approved:
            v.status = "reviewing"


def review_entry(
    db: Session, user: Any, dictionary_id: int, version_id: int, entry_id: int, payload: Dict[str, Any]
) -> Dict[str, Any]:
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    entry = repo.get_entry(db, entry_id, with_evidence=True)
    if entry.version_id != v.id:
        raise NotFound(f"条目不存在于该版本: {entry_id}")
    _review_one(db, v, entry, payload.get("action", ""), payload.get("note", ""), user)
    _advance_version_status(db, v)
    repo.refresh_version_counts(db, v)
    db.commit()
    return repo.serialize_entry(entry, include_evidence=False)


def batch_review(
    db: Session,
    user: Any,
    dictionary_id: int,
    version_id: int,
    *,
    items: List[Dict[str, Any]],
    concurrency_token: Optional[str] = None,
    allow_low_confidence: bool = False,
) -> Dict[str, Any]:
    """批量审核：接收明确条目 ID 列表与版本并发令牌（§13.3）。

    批量通过前仍要校验来源证据和必填字段；低置信（<0.60）条目不允许批量直接通过（§8.4）。
    """
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    if concurrency_token is not None:
        expected = concurrency_token
        actual = v.updated_at.isoformat() if v.updated_at else ""
        if not actual:
            actual = (v.created_at.isoformat() if v.created_at else "")
        if expected != actual:
            raise Conflict("版本已被其他操作修改，请刷新后重试")
    if not items:
        raise ValidationError("批量审核至少需要一个条目")
    if len(items) > 500:
        raise ValidationError("单次批量审核最多 500 条")
    results = []
    for item in items:
        entry = repo.get_entry(db, int(item.get("entry_id") or 0), with_evidence=True)
        if entry.version_id != v.id:
            results.append({"entry_id": item.get("entry_id"), "ok": False, "reason": "条目不属于该版本"})
            continue
        action = item.get("action")
        if action == "approve" and not allow_low_confidence:
            confidence = float(entry.confidence or 0.0)
            if confidence < 0.60:
                results.append(
                    {
                        "entry_id": entry.id,
                        "ok": False,
                        "reason": f"低置信条目({confidence:.2f})不允许批量直接通过，请逐条审核",
                    }
                )
                continue
        try:
            _review_one(db, v, entry, action, item.get("note", ""), user)
            results.append({"entry_id": entry.id, "ok": True, "review_status": entry.review_status})
        except ValidationError as exc:
            results.append({"entry_id": entry.id, "ok": False, "reason": str(exc)})
    _advance_version_status(db, v)
    repo.refresh_version_counts(db, v)
    db.commit()
    succeeded = sum(1 for r in results if r.get("ok"))
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


# ---------------------------------------------------------------------------
# 合并（§4.5：语义相似条目只给出建议，用户确认后才合并；§8.3 冲突约束）
# ---------------------------------------------------------------------------


def merge_entries(
    db: Session,
    user: Any,
    dictionary_id: int,
    version_id: int,
    *,
    keep_entry_id: int,
    merge_entry_ids: List[int],
    review_note: str = "",
) -> Dict[str, Any]:
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    _ensure_mutable_version(v)
    if not merge_entry_ids:
        raise ValidationError("至少需要一个待合并条目")
    keep = repo.get_entry(db, keep_entry_id, with_evidence=True)
    if keep.version_id != v.id:
        raise NotFound("保留条目不属于该版本")
    merged: List[KnowledgeDictionaryEntry] = []
    for entry_id in merge_entry_ids:
        e = repo.get_entry(db, entry_id, with_evidence=True)
        if e.version_id != v.id:
            raise NotFound(f"待合并条目不属于该版本: {entry_id}")
        if e.id == keep.id:
            continue
        # 类型必须兼容；单位不一致或不可换算时禁止合并（§8.3）
        if map_data_type(e.data_type) != map_data_type(keep.data_type):
            raise Conflict(f"条目「{e.standard_name}」与「{keep.standard_name}」数据类型冲突，不能合并")
        if not are_units_compatible(e.unit, keep.unit):
            raise Conflict(f"条目「{e.standard_name}」与「{keep.standard_name}」单位冲突，不能合并")
        merged.append(e)
    if not merged:
        raise ValidationError("没有可合并的条目")
    history = keep.merged_from or []
    for e in merged:
        snapshot = {
            "id": e.id,
            "standard_name": e.standard_name,
            "definition": (e.definition or "")[:500],
            "merged_at": _now().isoformat(),
        }
        history.append(snapshot)
        # 证据转移：先把证据从被合并条目集合中移出（防止 delete-orphan 级联删除），
        # 再挂到保留条目集合
        moved = list(e.evidences)
        e.evidences = []
        for ev in moved:
            ev.entry_id = keep.id
            keep.evidences.append(ev)
        db.delete(e)
    keep.merged_from = history
    keep.synonyms = merge_synonyms(keep.synonyms or [], *(e.synonyms or [] for e in merged))
    keep.review_note = (review_note or "").strip() or keep.review_note
    keep.review_status = "pending"
    keep.index_status = "pending"
    _refresh_entry(keep, user=user)
    repo.refresh_version_counts(db, v)
    db.commit()
    return repo.serialize_entry(keep, include_evidence=True)


def merge_suggestions(db: Session, user: Any, dictionary_id: int, version_id: int, *, limit: int = 10) -> Dict[str, Any]:
    """语义相似条目的合并建议（仅在用户确认后才会执行合并）。"""
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    v = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    entries = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == v.id,
            KnowledgeDictionaryEntry.review_status.in_(["pending", "approved", "conflict"]),
        )
        .all()
    )
    if len(entries) < 2:
        return {"items": []}
    from .vector_indexer import embed_texts

    texts = [f"{e.standard_name} {e.definition or ''}"[:800] for e in entries]
    vectors = embed_texts(texts)
    suggestions = []
    import math

    def _cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    seen_pairs = set()
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            sim = _cos(vectors[i], vectors[j])
            if sim >= 0.92:
                pair = tuple(sorted((entries[i].id, entries[j].id)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                suggestions.append(
                    {
                        "entry_a": {"id": entries[i].id, "standard_name": entries[i].standard_name},
                        "entry_b": {"id": entries[j].id, "standard_name": entries[j].standard_name},
                        "similarity": round(sim, 4),
                    }
                )
            if len(suggestions) >= limit:
                return {"items": suggestions}
    return {"items": suggestions}
