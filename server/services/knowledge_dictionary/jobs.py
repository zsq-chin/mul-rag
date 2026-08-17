"""知识字典任务服务（设计文档 §12）：持久化任务、租约、心跳、检查点、取消与重试。

API 进程只创建任务与查询状态；dictionary-worker 通过租约领取工作。
生成版本、来源快照和任务记录在同一数据库事务中创建。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from server.models.knowledge_dictionary_models import (
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionaryJob,
    KnowledgeDictionarySource,
    KnowledgeDictionaryVersion,
)

from . import repository as repo
from .errors import Conflict, JobConflict, NotFound, ValidationError
from .extractor import extract_candidates, validate_evidence_for_candidate
from .normalizer import compute_confidence, content_hash, dedupe_key, map_data_type, normalize_name, normalize_synonyms, normalize_unit
from .permissions import ensure_manager
from .source_adapters import (
    create_source_rows,
    iter_source_nodes,
    snapshot_kb,
    snapshot_kb_file,
    snapshot_upload,
)

# ---------------------------------------------------------------------------
# 运行参数（§12.2：生成/索引各 1 并发，与问答资源隔离）
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


GENERATE_CONCURRENCY = _env_int("DICTIONARY_GENERATE_CONCURRENCY", 1)
INDEX_CONCURRENCY = _env_int("DICTIONARY_INDEX_CONCURRENCY", 1)
LEASE_TTL_SECONDS = _env_int("DICTIONARY_LEASE_TTL_SECONDS", 120)
HEARTBEAT_INTERVAL_SECONDS = _env_int("DICTIONARY_HEARTBEAT_INTERVAL_SECONDS", 20)
NODE_BATCH_SIZE = _env_int("DICTIONARY_NODE_BATCH_SIZE", 8)
BATCH_FAILURE_RATIO = 0.5  # 超过该比例的批次失败则整个任务失败（§8.1.4）

_CONCURRENCY = {"generate": GENERATE_CONCURRENCY, "index": INDEX_CONCURRENCY, "import_seed": 1, "export": 1}

_ACTIVE_STATUSES = ("queued", "running", "cancelling")


def _now() -> datetime:
    """SQLite 存储 naive UTC 时间（与 func.now()/其他模型一致），租约比较必须同构。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sanitize_error(exc: BaseException) -> str:
    """脱敏错误摘要：只保留异常类别与简短信息，不含堆栈与密钥。"""
    text = str(exc).strip().replace("\n", " ")
    return f"{type(exc).__name__}: {text[:300]}"


# ---------------------------------------------------------------------------
# 任务创建（§13.2：三种来源互斥；任务配置不含 API Key）
# ---------------------------------------------------------------------------


def create_generate_job(db: Session, user: Any, spec: Dict[str, Any]) -> Dict[str, Any]:
    """创建生成任务：来源互斥校验、冻结来源快照、创建字典/版本与任务（同事务）。"""
    ensure_manager(user)
    source = spec.get("source") or {}
    kinds = [k for k, v in source.items() if v and k in ("kind",)]
    kind = source.get("kind")
    if kind not in ("kb_file", "kb", "upload"):
        raise ValidationError("必须且只能指定一种来源: kb_file / kb / upload")
    name = (spec.get("name") or "").strip()
    dictionary_id = spec.get("dictionary_id")
    if not dictionary_id and not name:
        raise ValidationError("新建字典时必须提供名称 name")

    # 快照冻结（§7.2）：先于任何写入完成，失败不产生半成品
    snapshots: List[Dict[str, Any]] = []
    if kind == "kb_file":
        if not source.get("db_id") or not source.get("file_id"):
            raise ValidationError("kb_file 来源需要 db_id 与 file_id")
        snapshots.append(snapshot_kb_file(db, str(source["db_id"]), str(source["file_id"])))
    elif kind == "kb":
        if not source.get("db_id"):
            raise ValidationError("kb 来源需要 db_id")
        snapshots = snapshot_kb(db, str(source["db_id"]))
    else:
        storage_ref = (source.get("storage_ref") or "").strip()
        if not storage_ref:
            raise ValidationError("upload 来源需要受控上传令牌 storage_ref")
        snapshots.append(
            snapshot_upload(
                {"storage_ref": storage_ref, "file_name": source.get("file_name") or storage_ref}
            )
        )

    # 事务：字典/版本/来源/任务一起创建
    if dictionary_id:
        dictionary = repo.get_dictionary(db, int(dictionary_id))
        version = repo.create_next_draft_version(db, user, dictionary.id)
        _ensure_no_active_job(db, version.id, ("generate",))
    else:
        if repo.find_dictionary_by_name(db, name) is not None:
            raise Conflict(f"字典名称已存在: {name}")
        dictionary = KnowledgeDictionary(
            name=name,
            description=(spec.get("description") or "").strip() or None,
            domain=(spec.get("domain") or "").strip() or None,
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
        )
        db.add(version)
        db.flush()

    snapshot_hash = create_source_rows(db, version, snapshots)
    generation_config = {
        "model_id": spec.get("model_id"),
        "categories": [str(c).strip() for c in (spec.get("categories") or []) if str(c).strip()][:50]
        or None,
        "use_seed": bool(spec.get("use_seed", True)),
        "duplicate_policy": spec.get("duplicate_policy") or "merge",
        "prompt_version": "dict-extract-v1",
        "rules_version": "dict-rules-v1",
        "source_kind": kind,
    }
    version.generation_config = generation_config
    job = KnowledgeDictionaryJob(
        job_type="generate",
        dictionary_id=dictionary.id,
        version_id=version.id,
        status="queued",
        stage="pending",
        progress=0.0,
        input_config=generation_config,
        checkpoint={
            "source_snapshot_hash": snapshot_hash,
            "source_index": 0,
            "node_offset": 0,
            "processed_sources": [],
        },
        requested_by=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return repo.serialize_job(job)


def _ensure_no_active_job(db: Session, version_id: Optional[int], job_types: tuple) -> None:
    if version_id is None:
        return  # 不绑定版本的任务（如 import_seed）不做版本级互斥
    count = (
        db.query(func.count(KnowledgeDictionaryJob.id))
        .filter(
            KnowledgeDictionaryJob.version_id == version_id,
            KnowledgeDictionaryJob.job_type.in_(job_types),
            KnowledgeDictionaryJob.status.in_(_ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )
    if count:
        raise JobConflict("该版本已有进行中的任务，请先取消或等待完成")


def create_index_job(db: Session, user: Any, dictionary_id: int, version_id: int) -> Dict[str, Any]:
    """创建（草稿）索引任务；生成完成后自动排入（§10.3）。"""
    ensure_manager(user)
    repo.get_dictionary(db, dictionary_id)
    version = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    if version.status in ("published", "withdrawn"):
        raise Conflict("已发布/已撤回版本不能重建索引，请创建新版本")
    _ensure_no_active_job(db, version.id, ("index",))
    job = KnowledgeDictionaryJob(
        job_type="index",
        dictionary_id=dictionary_id,
        version_id=version.id,
        status="queued",
        stage="pending",
        progress=0.0,
        input_config={"scope": "draft"},
        checkpoint={"phase": "start"},
        requested_by=user.id,
    )
    db.add(job)
    version.index_status = "pending"
    db.commit()
    db.refresh(job)
    return repo.serialize_job(job)


def queue_index_job_after_generation(db: Session, version_id: int) -> None:
    """生成任务完成后自动排入草稿索引任务（§10.3）。"""
    from .vector_indexer import vector_index_enabled

    if not vector_index_enabled():
        return
    existing = (
        db.query(func.count(KnowledgeDictionaryJob.id))
        .filter(
            KnowledgeDictionaryJob.version_id == version_id,
            KnowledgeDictionaryJob.job_type == "index",
            KnowledgeDictionaryJob.status.in_(_ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )
    if existing:
        return
    db.add(
        KnowledgeDictionaryJob(
            job_type="index",
            dictionary_id=None,
            version_id=version_id,
            status="queued",
            stage="pending",
            progress=0.0,
            input_config={"scope": "draft", "auto": True},
            checkpoint={"phase": "start"},
            requested_by=None,
        )
    )
    version = db.query(KnowledgeDictionaryVersion).filter(KnowledgeDictionaryVersion.id == version_id).first()
    if version is not None:
        version.index_status = "pending"
    db.commit()


# ---------------------------------------------------------------------------
# 任务查询 / 取消 / 重试（§13.2 / §12.1）
# ---------------------------------------------------------------------------


def get_job(db: Session, user: Any, job_id: int) -> Dict[str, Any]:
    job = repo.get_job(db, job_id)
    if not _user_can_view_job(user, job):
        from .errors import Forbidden

        raise Forbidden("无权查看该任务")
    return repo.serialize_job(job)


def _user_can_view_job(user: Any, job: KnowledgeDictionaryJob) -> bool:
    if user.role in ("admin", "superadmin"):
        return True
    return job.requested_by == user.id


def cancel_job(db: Session, user: Any, job_id: int) -> Dict[str, Any]:
    ensure_manager(user)
    job = repo.get_job(db, job_id)
    if job.status == "queued":
        job.status = "cancelled"
        job.finished_at = _now()
    elif job.status in ("running", "cancelling"):
        job.status = "cancelling"
    elif job.status in ("cancelled", "failed", "interrupted"):
        raise JobConflict(f"任务已处于终态: {job.status}")
    else:  # completed
        raise JobConflict("任务已完成，无法取消")
    db.commit()
    return repo.serialize_job(job)


def retry_job(db: Session, user: Any, job_id: int) -> Dict[str, Any]:
    """失败/取消/中断任务从最近检查点重试（§12.1）。"""
    ensure_manager(user)
    job = repo.get_job(db, job_id)
    if job.status not in ("failed", "cancelled", "interrupted"):
        raise JobConflict(f"任务状态 {job.status} 不允许重试")
    _ensure_no_active_job(db, job.version_id, (job.job_type,))
    job.status = "queued"
    job.error_summary = None
    job.finished_at = None
    job.heartbeat_at = None
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()
    return repo.serialize_job(job)


# ---------------------------------------------------------------------------
# 租约（worker 侧，§12.1）
# ---------------------------------------------------------------------------


def mark_expired_leases(db: Session) -> int:
    """租约过期的 running 任务标记为 interrupted（服务重启恢复）。"""
    now = _now()
    rows = (
        db.query(KnowledgeDictionaryJob)
        .filter(
            KnowledgeDictionaryJob.status == "running",
            KnowledgeDictionaryJob.lease_expires_at < now,
        )
        .all()
    )
    for job in rows:
        job.status = "interrupted"
        job.finished_at = now
    if rows:
        db.commit()
    return len(rows)


def claim_next_job(db: Session, worker_id: str, job_types: Optional[List[str]] = None) -> Optional[KnowledgeDictionaryJob]:
    """按类型并发限制领取一个 queued 任务；领取成功即持有租约。"""
    mark_expired_leases(db)
    types = job_types or ["generate", "index", "import_seed"]
    for job_type in types:
        limit = _CONCURRENCY.get(job_type, 1)
        running = (
            db.query(func.count(KnowledgeDictionaryJob.id))
            .filter(
                KnowledgeDictionaryJob.job_type == job_type,
                KnowledgeDictionaryJob.status.in_(("running", "cancelling")),
            )
            .scalar()
            or 0
        )
        if running >= limit:
            continue
        job = (
            db.query(KnowledgeDictionaryJob)
            .filter(KnowledgeDictionaryJob.job_type == job_type, KnowledgeDictionaryJob.status == "queued")
            .order_by(KnowledgeDictionaryJob.id)
            .first()
        )
        if job is None:
            continue
        now = _now()
        job.status = "running"
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        db.commit()
        db.refresh(job)
        return job
    return None


def heartbeat(
    db: Session,
    job_id: int,
    worker_id: str,
    *,
    stage: Optional[str] = None,
    progress: Optional[float] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
    **counts: Any,
) -> None:
    """续租并写入检查点（文件/节点批次/向量批次边界调用）。"""
    job = db.query(KnowledgeDictionaryJob).filter(KnowledgeDictionaryJob.id == job_id).first()
    if job is None or job.lease_owner != worker_id:
        return
    if job.status != "running":
        return
    now = _now()
    job.lease_expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)
    job.heartbeat_at = now
    if stage is not None:
        job.stage = stage
    if progress is not None:
        job.progress = max(0.0, min(float(progress), 100.0))
    if checkpoint is not None:
        job.checkpoint = checkpoint
    for key in (
        "processed_files",
        "processed_chunks",
        "candidate_count",
        "merged_count",
        "conflict_count",
        "pending_count",
        "rejected_count",
        "failed_count",
    ):
        if key in counts:
            setattr(job, key, int(counts[key]))
    db.commit()


def complete_job(db: Session, job_id: int, worker_id: str) -> None:
    job = _owned_job(db, job_id, worker_id)
    job.status = "completed"
    job.progress = 100.0
    job.finished_at = _now()
    job.lease_expires_at = None
    db.commit()


def fail_job(db: Session, job_id: int, worker_id: str, error: BaseException) -> None:
    job = _owned_job(db, job_id, worker_id)
    job.status = "failed"
    job.error_summary = _sanitize_error(error)
    job.finished_at = _now()
    job.lease_expires_at = None
    db.commit()


def _owned_job(db: Session, job_id: int, worker_id: str) -> KnowledgeDictionaryJob:
    job = db.query(KnowledgeDictionaryJob).filter(KnowledgeDictionaryJob.id == job_id).first()
    if job is None:
        raise NotFound(f"任务不存在: {job_id}")
    if job.lease_owner != worker_id:
        raise JobConflict("任务不属于当前 worker")
    return job


def cancel_flag(db: Session, job_id: int) -> bool:
    job = db.query(KnowledgeDictionaryJob).filter(KnowledgeDictionaryJob.id == job_id).first()
    return job is not None and job.status == "cancelling"


# ---------------------------------------------------------------------------
# 生成流水线（worker 执行；§8）
# ---------------------------------------------------------------------------


def run_job(db: Session, job: KnowledgeDictionaryJob, worker_id: str, deps: Optional[Dict[str, Any]] = None) -> None:
    """按任务类型执行；返回时任务必须处于终态或保持 running（租约仍有效）。"""
    deps = deps or {}
    try:
        if job.job_type == "generate":
            _run_generation(db, job, worker_id, deps)
        elif job.job_type == "index":
            _run_index(db, job, worker_id, deps)
        elif job.job_type == "import_seed":
            _run_import_seed(db, job, worker_id, deps)
        else:
            raise ValidationError(f"未知任务类型: {job.job_type}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        fail_job(db, job.id, worker_id, exc)


def _make_predict(db: Session, job: KnowledgeDictionaryJob, deps: Dict[str, Any]) -> Callable[[str], Any]:
    """构造模型调用函数：优先测试注入，否则按任务配置解析用户保存的模型。"""
    injected = deps.get("predict")
    if injected is not None:
        return injected
    config = job.input_config or {}
    model_id = config.get("model_id")
    if model_id:
        from server.models.user_model import User
        from server.services.model_credentials import resolve_model_for_user

        user = db.query(User).filter(User.id == job.requested_by).first()
        if user is None:
            raise ValidationError("任务发起人不存在，无法解析生成模型")
        model = resolve_model_for_user(db, user, {"user_model_id": int(model_id)})
        return lambda prompt: model.predict(prompt)
    from src.models import select_model

    model = select_model()
    return lambda prompt: model.predict(prompt)


def _run_generation(db: Session, job: KnowledgeDictionaryJob, worker_id: str, deps: Dict[str, Any]) -> None:
    version = db.query(KnowledgeDictionaryVersion).filter(KnowledgeDictionaryVersion.id == job.version_id).first()
    if version is None:
        raise NotFound(f"版本不存在: {job.version_id}")
    sources = (
        db.query(KnowledgeDictionarySource)
        .filter(KnowledgeDictionarySource.version_id == version.id)
        .order_by(KnowledgeDictionarySource.id)
        .all()
    )
    if not sources:
        raise ValidationError("版本没有任何来源快照")

    checkpoint = dict(job.checkpoint or {})
    source_index = int(checkpoint.get("source_index") or 0)
    node_offset = int(checkpoint.get("node_offset") or 0)
    processed_sources = list(checkpoint.get("processed_sources") or [])

    config = job.input_config or {}
    predict = _make_predict(db, job, deps)
    seed_names = deps.get("seed_names")
    if seed_names is None and config.get("use_seed"):
        from .seed_import import load_seed_names

        seed_names = load_seed_names()

    # 已存在的条目映射：确定性合并（§8.3.5）
    existing_entries = (
        db.query(KnowledgeDictionaryEntry)
        .filter(KnowledgeDictionaryEntry.version_id == version.id)
        .all()
    )
    entry_index: Dict[str, KnowledgeDictionaryEntry] = {dedupe_key(_entry_payload(e)): e for e in existing_entries}

    total_sources = len(sources)
    total_batches = 0
    failed_batches = 0
    last_batch_error = ""

    for idx in range(source_index, total_sources):
        source = sources[idx]
        nodes = list(iter_source_nodes(db, source))
        offset = node_offset if idx == source_index else 0
        batches = [nodes[i : i + NODE_BATCH_SIZE] for i in range(offset, len(nodes), NODE_BATCH_SIZE)]
        for batch in batches:
            if cancel_flag(db, job.id):
                job.status = "cancelled"
                job.finished_at = _now()
                job.lease_expires_at = None
                db.commit()
                return
            total_batches += 1
            try:
                _process_batch(db, version, source, batch, predict, seed_names, entry_index, config)
            except Exception as exc:  # 批次失败：记录并继续（§8.1.4）
                failed_batches += 1
                job.failed_count = int(job.failed_count or 0) + 1
                last_batch_error = _sanitize_error(exc)
                db.commit()  # 已提交的部分保留
            # 检查点：本批次完成后的节点偏移
            checkpoint.update(
                {
                    "source_index": idx,
                    "node_offset": offset + len(batch),
                    "processed_sources": processed_sources,
                }
            )
            counts = _version_counts(db, version)
            heartbeat(
                db,
                job.id,
                worker_id,
                stage=f"extract:{idx + 1}/{total_sources}",
                progress=round(min(99.0, 100.0 * (idx + 1) / total_sources), 2),
                checkpoint=checkpoint,
                processed_files=len(processed_sources) + 1,
                processed_chunks=total_batches,
                candidate_count=counts["candidate"],
                merged_count=counts["merged"],
                conflict_count=counts["conflict"],
                pending_count=counts["pending"],
                rejected_count=counts["rejected"],
                failed_count=job.failed_count,
            )
        processed_sources.append(source.id)
        checkpoint.update({"source_index": idx + 1, "node_offset": 0, "processed_sources": processed_sources})

    if total_batches and failed_batches / total_batches > BATCH_FAILURE_RATIO:
        raise ValidationError(
            f"批次失败比例超过 {BATCH_FAILURE_RATIO:.0%}，任务标记失败"
            + (f"；最近错误: {last_batch_error[:200]}" if last_batch_error else "")
        )

    repo.refresh_version_counts(db, version)
    db.commit()
    complete_job(db, job.id, worker_id)
    queue_index_job_after_generation(db, version.id)


def _entry_payload(e: KnowledgeDictionaryEntry) -> Dict[str, Any]:
    return {
        "standard_name": e.standard_name,
        "data_type": e.data_type,
        "unit": e.unit,
    }


def _process_batch(
    db: Session,
    version: KnowledgeDictionaryVersion,
    source: KnowledgeDictionarySource,
    batch: List[Dict[str, Any]],
    predict: Callable[[str], Any],
    seed_names: Optional[List[str]],
    entry_index: Dict[str, KnowledgeDictionaryEntry],
    config: Dict[str, Any],
) -> None:
    candidates = extract_candidates(
        batch, predict, category_hints=config.get("categories"), seed_names=seed_names
    )
    node_index = {node["node_id"]: node for node in batch}
    seed_set = set(seed_names or [])
    for candidate in candidates:
        evidences, signals = validate_evidence_for_candidate(candidate, node_index)
        if not evidences:
            continue  # 无有效引文直接丢弃并计入拒绝统计（§8.2）
        name = normalize_name(candidate["standard_name"])
        if not name:
            continue
        # 证据定位信息（页码/工作表/单元格）从来源节点回填，不信任模型输出（§6.5）
        for ev in evidences:
            node = node_index.get(ev["node_id"]) or {}
            ev.setdefault("page_no", node.get("page_no"))
            ev.setdefault("sheet_name", node.get("sheet_name"))
            ev.setdefault("cell_range", node.get("cell_range"))
        signals.update(
            {
                "seed_hit": name in seed_set,
                "multi_source": False,  # 精确合并后再计算
                "complete": bool(candidate.get("unit") and candidate.get("data_type")),
            }
        )
        confidence = compute_confidence(signals)
        candidate_norm = {
            "standard_name": candidate["standard_name"].strip(),
            "category": candidate.get("category"),
            "definition": candidate["definition"],
            "unit": candidate.get("unit"),
            "data_type": map_data_type(candidate.get("data_type")),
            "synonyms": normalize_synonyms(candidate.get("synonyms")),
            "value_rule": candidate.get("value_rule"),
            "confidence": confidence,
            "evidence": evidences,
            "signals": signals,
        }
        _merge_candidate_into_index(db, version, source, candidate_norm, entry_index)


def _merge_candidate_into_index(
    db: Session,
    version: KnowledgeDictionaryVersion,
    source: KnowledgeDictionarySource,
    candidate: Dict[str, Any],
    entry_index: Dict[str, KnowledgeDictionaryEntry],
) -> None:
    """确定性合并（§8.3）：同 key（名称+类型+单位）合并证据；单位/类型冲突进入 conflict。"""
    key = dedupe_key(candidate)
    existing = entry_index.get(key)
    if existing is not None:
        # 精确重复：合并同义词与证据（幂等）
        existing.synonyms = _merge_synonym_lists(existing.synonyms, candidate["synonyms"])
        _attach_evidences(db, existing, source, candidate["evidence"])
        existing.review_status = "pending"
        existing.index_status = "pending"
        existing.updated_at = _now()
        _rehash(existing)
        return
    # 同标准名但类型/单位冲突：进入 conflict，不自动合并（§8.3）
    conflict_row = _find_conflict(db, version.id, candidate)
    if conflict_row is not None:
        _attach_evidences(db, conflict_row, source, candidate["evidence"])
        conflict_row.review_status = "conflict"
        conflict_row.index_status = "pending"
        conflict_row.updated_at = _now()
        _rehash(conflict_row)
        entry_index[dedupe_key(_entry_payload(conflict_row))] = conflict_row
        return
    entry = KnowledgeDictionaryEntry(
        version_id=version.id,
        category=candidate.get("category"),
        standard_name=candidate["standard_name"],
        normalized_name=normalize_name(candidate["standard_name"]),
        definition=candidate["definition"],
        unit=candidate.get("unit"),
        normalized_unit=normalize_unit(candidate.get("unit")),
        data_type=candidate["data_type"],
        synonyms=normalize_synonyms(candidate.get("synonyms")),
        value_rule=candidate.get("value_rule"),
        review_status="pending",
        confidence=float(candidate.get("confidence") or 0.0),
        index_status="pending",
    )
    _rehash(entry)
    db.add(entry)
    db.flush()
    _attach_evidences(db, entry, source, candidate["evidence"])
    entry_index[key] = entry


def _find_conflict(
    db: Session, version_id: int, candidate: Dict[str, Any]
) -> Optional[KnowledgeDictionaryEntry]:
    """同 normalized_name 但类型或单位不兼容的条目：进入 conflict。"""
    name = normalize_name(candidate["standard_name"])
    rows = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == version_id,
            KnowledgeDictionaryEntry.normalized_name == name,
        )
        .all()
    )
    for row in rows:
        if row.data_type != candidate["data_type"] or not _units_compatible(row.normalized_unit, candidate["unit"]):
            return row
    return None


def _units_compatible(a: Optional[str], b: Optional[str]) -> bool:
    na, nb = normalize_unit(a), normalize_unit(b)
    if not na or not nb:
        return True
    return na == nb


def _attach_evidences(
    db: Session, entry: KnowledgeDictionaryEntry, source: KnowledgeDictionarySource, evidences: List[Dict[str, Any]]
) -> None:
    """附加证据（按 evidence_hash 幂等去重，重试不会重复写入）。"""
    existing_hashes = {
        ev.evidence_hash
        for ev in db.query(KnowledgeDictionaryEvidence)
        .filter(KnowledgeDictionaryEvidence.entry_id == entry.id)
        .all()
    }
    for ev in evidences:
        digest = _evidence_hash(ev["quote"])
        if digest in existing_hashes:
            continue
        db.add(
            KnowledgeDictionaryEvidence(
                entry_id=entry.id,
                source_id=source.id,
                node_id=ev.get("node_id"),
                field_path=ev.get("field_path"),
                quote=ev["quote"],
                page_no=ev.get("page_no"),
                sheet_name=ev.get("sheet_name"),
                cell_range=ev.get("cell_range"),
                inferred=1 if ev.get("inferred") else 0,
                evidence_hash=digest,
            )
        )
        existing_hashes.add(digest)
    db.flush()


def _evidence_hash(quote: str) -> str:
    import hashlib

    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def _merge_synonym_lists(current: Any, incoming: List[str]) -> List[str]:
    from .normalizer import merge_synonyms

    return merge_synonyms(current or [], incoming or [])


def _rehash(entry: KnowledgeDictionaryEntry) -> None:
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


def _version_counts(db: Session, version: KnowledgeDictionaryVersion) -> Dict[str, int]:
    """按当前会话统计条目状态计数（生成流水线进度展示）。"""
    rows = (
        db.query(
            KnowledgeDictionaryEntry.review_status,
            func.count(KnowledgeDictionaryEntry.id),
        )
        .filter(KnowledgeDictionaryEntry.version_id == version.id)
        .group_by(KnowledgeDictionaryEntry.review_status)
        .all()
    )
    counts = {status: int(count) for status, count in rows}
    pending = counts.get("pending", 0) + counts.get("conflict", 0)
    return {
        "candidate": sum(counts.values()),
        "merged": counts.get("pending", 0),
        "conflict": counts.get("conflict", 0),
        "pending": pending,
        "rejected": counts.get("rejected", 0),
    }


# ---------------------------------------------------------------------------
# 索引 / 种子任务（委托给对应模块）
# ---------------------------------------------------------------------------


def _run_index(db: Session, job: KnowledgeDictionaryJob, worker_id: str, deps: Dict[str, Any]) -> None:
    from .vector_indexer import reindex_version

    version = db.query(KnowledgeDictionaryVersion).filter(KnowledgeDictionaryVersion.id == job.version_id).first()
    if version is None:
        raise NotFound(f"版本不存在: {job.version_id}")
    embed = deps.get("embed")
    reindex_version(db, version, embed=embed, heartbeat=lambda **kw: heartbeat(db, job.id, worker_id, **kw))
    complete_job(db, job.id, worker_id)


def _run_import_seed(db: Session, job: KnowledgeDictionaryJob, worker_id: str, deps: Dict[str, Any]) -> None:
    from .seed_import import import_seed_for_job

    import_seed_for_job(db, job, worker_id, heartbeat=lambda **kw: heartbeat(db, job.id, worker_id, **kw))
    complete_job(db, job.id, worker_id)
