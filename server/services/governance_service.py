"""知识治理业务逻辑（会话注入，纯服务层，便于单元测试）。

覆盖：知识文档分级分类元数据、列表/详情、受控下载、元数据导出与 sync。

安全约束：
- 本模块不得 import `src` / `server.db_manager`（保证本地可测试，不触发 Milvus）。
- 预览/列表/导出响应绝不返回 `KnowledgeFile.path`。
- 下载路径必须解析到 DATA_ROOT 内（防 `..`、绝对路径逃逸、软链接逃逸、URL）。
- 伪造的 db_id/file_id 走数据库精确匹配，查不到即 404。
- usage_count 只能由服务端更新（预览/下载时自增）。
"""

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode
from server.models.governance_model import KnowledgeDocumentVersion, KnowledgeGovernance

logger = logging.getLogger("sage.governance")

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
# 受控数据根目录：默认项目 saves/data。测试可覆盖 DATA_ROOT / _PROJECT_ROOT。
DATA_ROOT = os.environ.get(
    "SAGE_GOVERNANCE_DATA_ROOT", os.path.join(_PROJECT_ROOT, "saves", "data")
)

VALID_CONFIDENTIALITY = frozenset({"public", "internal", "restricted"})
VALID_KNOWLEDGE_TYPES = frozenset(
    {"报告", "论文", "设计图", "日志", "标准", "其他"}
)
MAX_TAGS = 20
MAX_TAG_LEN = 50

# PATCH 允许更新的治理字段白名单
EDITABLE_FIELDS = frozenset(
    {
        "domain",
        "knowledge_type",
        "confidentiality",
        "tags",
        "download_allowed",
        "owner_department",
        "source_updated_at",
    }
)

_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
    "md": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
}

# XLSX/CSV 单元格公式注入防护：以这些字符开头的字符串会被转义
_FORMULA_PREFIXES = ("=", "+", "-", "@")


class GovernanceError(Exception):
    """治理业务错误，message 直接透传给用户。"""

    status_code = 400


class GovernanceNotFound(GovernanceError):
    status_code = 404


class GovernanceForbidden(GovernanceError):
    status_code = 403


# --- 内部辅助 ---


def _safe_id(value) -> str:
    """拒绝路径分隔符、`.`/`..` 等非法资源标识。"""
    s = str(value or "").strip()
    if not s or s in (".", "..") or "/" in s or "\\" in s or "\x00" in s:
        raise GovernanceError("非法的资源标识")
    return s


def _clean_str(value, max_len) -> str | None:
    s = str(value or "").strip()
    if not s:
        return None
    return s[:max_len]


def _resolve_download_path(candidate) -> str | None:
    """把 KnowledgeFile.path 安全解析到 DATA_ROOT 内的真实文件。

    返回绝对路径；任何越界/不存在/URL/软链接逃逸都返回 None。
    """
    if not candidate or not isinstance(candidate, str):
        return None
    scheme = candidate.split(":", 1)[0].strip().lower()
    if scheme in ("http", "https", "ftp", "file", "data", "s3", "gs"):
        return None
    if not os.path.isabs(candidate):
        candidate = os.path.join(_PROJECT_ROOT, candidate)
    full = os.path.normcase(os.path.realpath(candidate))
    root = os.path.normcase(os.path.realpath(DATA_ROOT))
    # 必须严格位于 root 内（不允许等于 root，必须是子路径）
    if not full.startswith(root + os.sep):
        return None
    if not os.path.isfile(full):
        return None
    return full


def _get_or_create_governance(session, db_id, file_id, source_updated_at=None):
    row = (
        session.query(KnowledgeGovernance)
        .filter_by(db_id=db_id, file_id=file_id)
        .first()
    )
    if row is None:
        row = KnowledgeGovernance(
            db_id=db_id,
            file_id=file_id,
            confidentiality="internal",
            download_allowed=1,
            source_updated_at=source_updated_at,
        )
        session.add(row)
        session.flush()
    return row


def _serialize_document(file_row, gov_row, node_count=None):
    """文档 + 治理元数据。绝不返回 path / 绝对路径。"""
    if node_count is None:
        node_count = (
            len(file_row.nodes) if getattr(file_row, "nodes", None) is not None else 0
        )
    tags = []
    if gov_row is not None and gov_row.tags:
        try:
            parsed = json.loads(gov_row.tags)
            if isinstance(parsed, list):
                tags = [str(t) for t in parsed]
        except (ValueError, TypeError):
            tags = []
    return {
        "db_id": file_row.database_id,
        "file_id": file_row.file_id,
        "filename": file_row.filename,
        "file_type": file_row.file_type,
        "status": file_row.status,
        "node_count": node_count,
        "created_at": file_row.created_at.isoformat() if file_row.created_at else None,
        "domain": gov_row.domain if gov_row else None,
        "knowledge_type": gov_row.knowledge_type if gov_row else None,
        "confidentiality": gov_row.confidentiality if gov_row else "internal",
        "tags": tags,
        "download_allowed": bool(gov_row.download_allowed) if gov_row else True,
        "owner_department": gov_row.owner_department if gov_row else None,
        "source_updated_at": (
            gov_row.source_updated_at.isoformat()
            if gov_row and gov_row.source_updated_at
            else None
        ),
        "usage_count": gov_row.usage_count if gov_row else 0,
    }


def _sanitize_cell(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _node_counts(session, db_id, file_ids):
    if not file_ids:
        return {}
    rows = (
        session.query(KnowledgeNode.file_id, func.count(KnowledgeNode.id))
        .join(KnowledgeFile, KnowledgeFile.file_id == KnowledgeNode.file_id)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeNode.file_id.in_(file_ids))
        .group_by(KnowledgeNode.file_id)
        .all()
    )
    return {fid: cnt for fid, cnt in rows}


# --- 对外接口 ---


def list_documents(
    session: Session,
    db_id: str,
    keyword: str = "",
    knowledge_type: str = "",
    confidentiality: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """分页返回知识库文档及治理元数据（过滤在 SQL 中完成）。"""
    db_id = _safe_id(db_id)
    db_row = (
        session.query(KnowledgeDatabase).filter(KnowledgeDatabase.db_id == db_id).first()
    )
    if db_row is None:
        raise GovernanceNotFound("知识库不存在")

    q = session.query(KnowledgeFile).outerjoin(
        KnowledgeGovernance,
        and_(
            KnowledgeGovernance.db_id == KnowledgeFile.database_id,
            KnowledgeGovernance.file_id == KnowledgeFile.file_id,
        ),
    )
    q = q.filter(KnowledgeFile.database_id == db_id)
    if keyword:
        q = q.filter(KnowledgeFile.filename.ilike(f"%{keyword}%"))
    if knowledge_type:
        q = q.filter(KnowledgeGovernance.knowledge_type == knowledge_type)
    if confidentiality:
        q = q.filter(KnowledgeGovernance.confidentiality == confidentiality)

    total = q.count()
    rows = (
        q.order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    file_ids = [r.file_id for r in rows]
    gov_map = {
        g.file_id: g
        for g in session.query(KnowledgeGovernance)
        .filter(KnowledgeGovernance.db_id == db_id)
        .all()
    }
    counts = _node_counts(session, db_id, file_ids)
    items = [_serialize_document(r, gov_map.get(r.file_id), counts.get(r.file_id, 0)) for r in rows]
    return {"items": items, "page": page, "page_size": page_size, "total": total}


def get_document(session: Session, db_id: str, file_id: str, increment_usage: bool = True) -> dict:
    """返回单个文档详情（预览）。预览计入使用次数。"""
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    gov = _get_or_create_governance(session, db_id, file_id, source_updated_at=file_row.created_at)
    if increment_usage:
        gov.usage_count = (gov.usage_count or 0) + 1
    session.commit()
    session.refresh(gov)
    return _serialize_document(file_row, gov, None)


def update_governance(session: Session, db_id: str, file_id: str, payload: dict) -> dict:
    """更新治理字段；缺失的记录自动补建默认值。"""
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    if not isinstance(payload, dict):
        raise GovernanceError("请求体必须是对象")
    unknown = set(payload) - EDITABLE_FIELDS
    if unknown:
        raise GovernanceError(f"不支持的治理字段: {sorted(unknown)}")

    gov = _get_or_create_governance(session, db_id, file_id, source_updated_at=file_row.created_at)

    if "domain" in payload:
        gov.domain = _clean_str(payload["domain"], 100)
    if "knowledge_type" in payload:
        kt = _clean_str(payload["knowledge_type"], 50)
        if kt is not None and kt not in VALID_KNOWLEDGE_TYPES:
            raise GovernanceError(
                f"knowledge_type 只能是 {sorted(VALID_KNOWLEDGE_TYPES)}"
            )
        gov.knowledge_type = kt
    if "confidentiality" in payload:
        conf = payload["confidentiality"]
        if conf not in VALID_CONFIDENTIALITY:
            raise GovernanceError(
                f"confidentiality 只能是 {sorted(VALID_CONFIDENTIALITY)}"
            )
        gov.confidentiality = conf
    if "tags" in payload:
        tags = payload["tags"] or []
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise GovernanceError("tags 必须是字符串数组")
        cleaned = [t.strip()[:MAX_TAG_LEN] for t in tags if t.strip()]
        gov.tags = json.dumps(cleaned[:MAX_TAGS], ensure_ascii=False)
    if "download_allowed" in payload:
        gov.download_allowed = 1 if payload["download_allowed"] else 0
    if "owner_department" in payload:
        gov.owner_department = _clean_str(payload["owner_department"], 100)
    if "source_updated_at" in payload:
        gov.source_updated_at = payload["source_updated_at"]

    session.commit()
    session.refresh(gov)
    return _serialize_document(file_row, gov, None)


def sync_governance(session: Session, db_id: str) -> dict:
    """只读现有知识库/文件信息，为缺失记录补建治理元数据，不解析不重建索引。"""
    db_id = _safe_id(db_id)
    db_row = (
        session.query(KnowledgeDatabase).filter(KnowledgeDatabase.db_id == db_id).first()
    )
    if db_row is None:
        raise GovernanceNotFound("知识库不存在")
    files = (
        session.query(KnowledgeFile).filter(KnowledgeFile.database_id == db_id).all()
    )
    created = 0
    updated = 0
    for f in files:
        gov = (
            session.query(KnowledgeGovernance)
            .filter_by(db_id=db_id, file_id=f.file_id)
            .first()
        )
        if gov is None:
            session.add(
                KnowledgeGovernance(
                    db_id=db_id,
                    file_id=f.file_id,
                    confidentiality="internal",
                    download_allowed=1,
                    source_updated_at=f.created_at,
                )
            )
            created += 1
        elif gov.source_updated_at is None:
            gov.source_updated_at = f.created_at
            updated += 1
    session.commit()
    return {"total": len(files), "created": created, "updated": updated}


def _all_documents(session: Session, db_id: str):
    db_id = _safe_id(db_id)
    rows = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id)
        .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id)
        .all()
    )
    gov_map = {
        g.file_id: g
        for g in session.query(KnowledgeGovernance)
        .filter(KnowledgeGovernance.db_id == db_id)
        .all()
    }
    counts = _node_counts(session, db_id, [r.file_id for r in rows])
    return [_serialize_document(r, gov_map.get(r.file_id), counts.get(r.file_id, 0)) for r in rows]


def export_json(session: Session, db_id: str) -> dict:
    """导出治理元数据 JSON（不含 path/正文/向量/秘密）。"""
    items = _all_documents(session, db_id)
    return {"items": items, "page": 1, "page_size": len(items) or 20, "total": len(items)}


def export_xlsx_bytes(session: Session, db_id: str) -> bytes:
    """导出治理元数据 XLSX 字节流（仅元数据，含公式注入防护）。"""
    from io import BytesIO

    from openpyxl import Workbook

    items = _all_documents(session, db_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "governance"
    headers = [
        "file_id",
        "filename",
        "file_type",
        "status",
        "node_count",
        "created_at",
        "domain",
        "knowledge_type",
        "confidentiality",
        "tags",
        "download_allowed",
        "owner_department",
        "source_updated_at",
        "usage_count",
    ]
    ws.append(headers)
    for it in items:
        ws.append(
            [
                _sanitize_cell(it["file_id"]),
                _sanitize_cell(it["filename"]),
                _sanitize_cell(it["file_type"]),
                _sanitize_cell(it["status"]),
                it["node_count"],
                it["created_at"] or "",
                _sanitize_cell(it["domain"]),
                _sanitize_cell(it["knowledge_type"]),
                _sanitize_cell(it["confidentiality"]),
                _sanitize_cell(",".join(it["tags"])),
                "是" if it["download_allowed"] else "否",
                _sanitize_cell(it["owner_department"]),
                it["source_updated_at"] or "",
                it["usage_count"],
            ]
        )
    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def media_type_for(extension: str) -> str:
    return _MEDIA_TYPES.get((extension or "").lower(), "application/octet-stream")


def _authorize_download(gov, user):
    """下载权限矩阵：restricted 仅 superadmin；download_allowed=0 一律拒绝。"""
    conf = gov.confidentiality or "internal"
    if conf not in VALID_CONFIDENTIALITY:
        conf = "internal"
    role = getattr(user, "role", "") or ""
    if conf == "restricted" and role != "superadmin":
        raise GovernanceForbidden("restricted 文档仅超级管理员可下载")
    if not gov.download_allowed:
        raise GovernanceForbidden("该文档已禁止下载")
    return conf


def _increment_usage(session, gov):
    gov.usage_count = (gov.usage_count or 0) + 1
    session.commit()


def resolve_download(session: Session, db_id: str, file_id: str, user) -> dict:
    """下载前鉴权与路径解析。

    规则：
    - restricted 仅 superadmin 可下载。
    - download_allowed=0 时任何人（含 superadmin）都不可下载。
    - 其余类别的已登录用户可下载。
    返回 abs_path / filename / size_bytes / extension；任何拒绝抛 403/404。
    """
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    gov = _get_or_create_governance(session, db_id, file_id, source_updated_at=file_row.created_at)
    _authorize_download(gov, user)

    abs_path = _resolve_download_path(file_row.path)
    if abs_path is None:
        raise GovernanceNotFound("源文件不存在或不可访问")

    _increment_usage(session, gov)

    filename = file_row.filename or f"file_{file_row.file_id}"
    return {
        "abs_path": abs_path,
        "filename": filename,
        "size_bytes": os.path.getsize(abs_path),
        "extension": os.path.splitext(filename)[1].lstrip(".").lower(),
        "db_id": db_id,
        "file_id": file_id,
    }


# --- 版本快照 ---


def _versions_root() -> str:
    return os.path.join(DATA_ROOT, "knowledge_versions")


def _version_dir(db_id: str, file_id: str, version: int) -> str:
    return os.path.join(_versions_root(), db_id, file_id, str(version))


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _version_meta(version_row) -> dict:
    """解析版本记录 metadata_snapshot 为 dict（损坏/非法一律当空处理）。"""
    if version_row is None or not getattr(version_row, "metadata_snapshot", None):
        return {}
    try:
        parsed = json.loads(version_row.metadata_snapshot)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass
    return {}


def _serialize_version(row: KnowledgeDocumentVersion) -> dict:
    meta = _version_meta(row)
    return {
        "version": row.version,
        "sha256": row.sha256 or "",
        "file_size": row.file_size,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "note": row.note,
        "deduplicated": bool(meta.get("deduplicated")),
    }


def list_versions(session: Session, db_id: str, file_id: str) -> dict:
    """按版本倒序返回该文档的版本历史。"""
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    rows = (
        session.query(KnowledgeDocumentVersion)
        .filter(
            KnowledgeDocumentVersion.db_id == db_id,
            KnowledgeDocumentVersion.file_id == file_id,
        )
        .order_by(KnowledgeDocumentVersion.version.desc())
        .all()
    )
    return {"items": [_serialize_version(r) for r in rows], "total": len(rows)}


def _blobs_root() -> str:
    """内容寻址 blob 根目录：knowledge_versions/_blobs/{sha[:2]}/{sha}/file。"""
    return os.path.join(_versions_root(), "_blobs")


def _blob_path(sha256: str) -> str:
    """按 SHA-256 内容寻址的 blob 路径：同内容同路径，天然去重、绝不互删。"""
    return os.path.join(_blobs_root(), sha256[:2], sha256, "file")


def _staging_root() -> str:
    """请求级唯一暂存目录：并发请求绝不共享，也绝不会被其它请求删除。

    P1-3：临时资源必须具有请求级唯一名称；失败清理只动自己的目录，
    绝不能删除其它请求/其它版本的内容。
    """
    return os.path.join(_blobs_root(), "_staging", "{}.{}".format(os.getpid(), os.urandom(8).hex()))


def _stage_version_tmp(source: str, sha256: str, size: int):
    """把源文件复制到请求级唯一暂存目录并校验 SHA/大小。

    返回 (staging_dir, tmp_path)。调用方（create_snapshot）随后先原子发布 blob、
    再提交版本记录；因此本函数只负责暂存校验，不负责发布。
    任何一步失败都清理本请求的暂存目录并抛错。
    """
    staging = _staging_root()
    tmp = os.path.join(staging, "file")
    try:
        os.makedirs(staging, exist_ok=True)
        shutil.copy2(source, tmp)
        if _sha256_file(tmp) != sha256 or os.path.getsize(tmp) != size:
            raise GovernanceError("版本文件校验不一致")
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, GovernanceError):
            raise
        logger.warning("版本源文件复制失败: %s", exc)
        raise GovernanceError("版本源文件复制失败")
    return staging, tmp


def _publish_version_blob(tmp: str, sha256: str) -> None:
    """把暂存文件原子发布到内容寻址 blob 路径。

    os.replace 原子改名：并发请求即使写到同一 sha 路径，内容也完全一致，
    最后写入者幂等覆盖，不会产生半文件。
    """
    final = _blob_path(sha256)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    os.replace(tmp, final)


def _referenced_blob_shas(session: Session) -> set:
    """所有版本记录 metadata_snapshot 中引用的 blob_sha 集合（H2 GC 依据）。

    只认格式合法（64 位十六进制）的 blob_sha；旧版按版本目录落盘的
    _version_dir 方案不使用 _blobs 内容寻址路径，不受本 GC 影响。
    """
    shas = set()
    for row in session.query(KnowledgeDocumentVersion).all():
        try:
            meta = json.loads(row.metadata_snapshot or "{}")
        except (TypeError, ValueError):
            continue
        blob_sha = meta.get("blob_sha")
        if isinstance(blob_sha, str) and len(blob_sha) == 64:
            shas.add(blob_sha)
    return shas


def gc_unreferenced_blobs(session: Session, min_age_seconds: float = 3600) -> dict:
    """定期/手动 GC：只删除「超过安全时间且无任何版本引用」的 blob。

    H2.5 语义：
    - 只删 _blobs/{sha[:2]}/{sha}/file，绝不触碰 _staging 暂存目录；
    - 只删 mtime 早于 (now - min_age_seconds) 的无引用 blob，保证并发创建中
      （blob 刚发布、版本记录尚未提交）的 blob 不会被误删；
    - 同内容并发写同一 sha 路径且内容一致，删除幂等安全；
    - 旧版 _version_dir 方案的文件不在 _blobs 下，天然不受影响。

    返回 {"removed": n, "retained": m}。
    """
    referenced = _referenced_blob_shas(session)
    blobs_root = _blobs_root()
    removed = 0
    retained = 0
    cutoff = time.time() - min_age_seconds
    if not os.path.isdir(blobs_root):
        return {"removed": 0, "retained": 0}
    for prefix in os.listdir(blobs_root):
        if prefix == "_staging" or len(prefix) != 2:
            continue
        prefix_dir = os.path.join(blobs_root, prefix)
        for sha in os.listdir(prefix_dir):
            blob_file = os.path.join(prefix_dir, sha, "file")
            if not os.path.isfile(blob_file):
                continue
            try:
                mtime = os.path.getmtime(blob_file)
            except OSError:
                retained += 1
                continue
            if sha in referenced or mtime >= cutoff:
                retained += 1
                continue
            try:
                os.remove(blob_file)
                removed += 1
                for empty_dir in (os.path.dirname(blob_file), prefix_dir):
                    try:
                        os.rmdir(empty_dir)
                    except OSError:
                        pass
            except OSError as exc:
                logger.warning("blob GC 删除失败 %s: %s", blob_file, exc)
                retained += 1
    logger.info(
        "blob GC 完成: removed=%d retained=%d referenced=%d",
        removed, retained, len(referenced),
    )
    return {"removed": removed, "retained": retained}


def _next_version_number(session: Session, db_id: str, file_id: str) -> int:
    """计算下一个版本号（latest + 1）。并发冲突由唯一约束 + 调用方重试兜底。"""
    latest = (
        session.query(func.max(KnowledgeDocumentVersion.version))
        .filter(
            KnowledgeDocumentVersion.db_id == db_id,
            KnowledgeDocumentVersion.file_id == file_id,
        )
        .scalar()
        or 0
    )
    return latest + 1


def create_snapshot(
    session: Session,
    db_id: str,
    file_id: str,
    creator: str = "",
    note: str = "",
) -> dict:
    """创建源文件版本快照（P1-3 内容寻址重构）。

    - 只复制本机源文件到受控版本目录，不修改 Milvus/知识节点/索引。
    - 版本文件按 SHA-256 内容寻址：同一内容只存一份 blob，版本记录只引用
      blob_sha；同内容并发创建写同一个路径且内容一致，互不删除。
    - H2 blob-first 顺序：暂存（请求级唯一目录校验）→ 原子发布 blob
      （os.replace 到内容寻址路径）→ 提交引用该 blob 的版本记录。
      发布失败时版本记录尚未写入（不依赖第二次 DB 提交删除）；提交失败只留下
      无引用的孤儿 blob（可由 gc_unreferenced_blobs 清理），绝不出现版本记录
      存在但文件不存在（浏览器可见断链）。
    - 去重复用时必须确认既有 blob 内容校验一致，损坏的 blob 绝不能被引用。
    - 所有数据库异常都回滚并清理本请求创建的暂存资源，绝不删除其它请求的内容。
    - 版本号单调递增；并发创建通过唯一约束 + 重试保证不重号。
    """
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    source = _resolve_download_path(file_row.path)
    if source is None:
        raise GovernanceNotFound("源文件不存在或不可访问")

    gov = _get_or_create_governance(session, db_id, file_id, source_updated_at=file_row.created_at)
    sha256 = _sha256_file(source)
    size = os.path.getsize(source)

    # 内容寻址去重：blob 存在且校验一致才复用；损坏的既有 blob 直接拒绝
    final_blob = _blob_path(sha256)
    reused = os.path.isfile(final_blob)
    if reused:
        try:
            if _sha256_file(final_blob) != sha256:
                raise GovernanceError("去重引用的既有版本文件已损坏，无法创建快照")
        except OSError:
            raise GovernanceError("去重引用的既有版本文件已损坏，无法创建快照")

    metadata_snapshot = _serialize_document(file_row, gov, None)
    snapshot_payload = {
        "governance": metadata_snapshot,
        "blob_sha": sha256,
        "deduplicated": bool(reused),
    }

    staging = None
    tmp = None
    row = None
    try:
        # H2 blob-first：新内容先在请求级唯一暂存目录校验，然后原子发布 blob，
        # 最后才提交引用该 blob 的版本记录。
        #  - 发布失败：版本记录尚未写入，直接抛错，不需要第二次 DB 提交去删除；
        #  - 提交失败：blob 已发布，成为无引用的孤儿 blob，由 gc_unreferenced_blobs
        #    清理，绝不出现版本记录存在但文件不存在（浏览器可见断链）。
        if not reused:
            staging, tmp = _stage_version_tmp(source, sha256, size)
            try:
                _publish_version_blob(tmp, sha256)
            except Exception as exc:
                logger.warning("版本 blob 发布失败 sha=%s: %s", sha256, exc)
                raise GovernanceError("版本文件发布失败")
        for _attempt in range(6):
            version = _next_version_number(session, db_id, file_id)
            row = KnowledgeDocumentVersion(
                db_id=db_id,
                file_id=file_id,
                version=version,
                sha256=sha256,
                file_size=size,
                metadata_snapshot=json.dumps(snapshot_payload, ensure_ascii=False, default=str),
                created_by=(creator or "")[:100],
                created_at=None,
                note=(note or "").strip()[:255],
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # 版本号冲突：回滚重试。blob 已发布且内容寻址一致，重试复用即可。
                session.rollback()
                row = None
                continue
            break
        else:
            raise GovernanceError("版本号冲突，请重试")
        staging = None
        tmp = None
    except Exception as exc:
        # 发布失败：版本记录尚未写入，回滚（无操作）后抛错即可（H2.3）。
        # 提交失败：孤儿 blob 已发布，交给 GC 清理（H2.2），不回滚不删除其它内容。
        try:
            session.rollback()
        except Exception:
            pass
        if isinstance(exc, GovernanceError):
            raise
        logger.warning("版本记录提交失败，已回滚；孤儿 blob 交由 GC 清理")
        raise
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)

    session.refresh(row)
    return _serialize_version(row)


def _resolve_version_blob(session: Session, db_id: str, file_id: str, version_row) -> str | None:
    """解析版本文件的受控路径。

    P1-3 新方案：按 metadata_snapshot 里的 blob_sha 内容寻址（路径 = sha，
    天然受控）。兼容旧版 blob_version 去重链（老数据仍可下载），
    任何情况都拒绝越界读取。
    """
    meta = _version_meta(version_row)
    blob_sha = meta.get("blob_sha")
    if isinstance(blob_sha, str) and len(blob_sha) == 64:
        candidate = _blob_path(blob_sha)
        full = os.path.normcase(os.path.realpath(candidate))
        root = os.path.normcase(os.path.realpath(_versions_root()))
        if full.startswith(root + os.sep) and os.path.isfile(full):
            return full
        return None
    # 旧版 blob_version 链（向后兼容）
    cur = version_row
    seen = set()
    for _ in range(64):
        meta = _version_meta(cur)
        target = meta.get("blob_version")
        if target is None:
            break
        if target in seen:
            return None
        seen.add(target)
        nxt = (
            session.query(KnowledgeDocumentVersion)
            .filter(
                KnowledgeDocumentVersion.db_id == db_id,
                KnowledgeDocumentVersion.file_id == file_id,
                KnowledgeDocumentVersion.version == int(target),
            )
            .first()
        )
        if nxt is None:
            return None
        cur = nxt
    candidate = os.path.join(_version_dir(db_id, file_id, cur.version), "file")
    full = os.path.normcase(os.path.realpath(candidate))
    root = os.path.normcase(os.path.realpath(_versions_root()))
    if not full.startswith(root + os.sep):
        return None
    if not os.path.isfile(full):
        return None
    return full


def resolve_version_download(
    session: Session, db_id: str, file_id: str, version: int, user
) -> dict:
    """版本受控下载：权限同文档下载；只从受控版本目录读取。"""
    db_id = _safe_id(db_id)
    file_id = _safe_id(file_id)
    file_row = (
        session.query(KnowledgeFile)
        .filter(KnowledgeFile.database_id == db_id, KnowledgeFile.file_id == file_id)
        .first()
    )
    if file_row is None:
        raise GovernanceNotFound("文档不存在")
    version_row = (
        session.query(KnowledgeDocumentVersion)
        .filter(
            KnowledgeDocumentVersion.db_id == db_id,
            KnowledgeDocumentVersion.file_id == file_id,
            KnowledgeDocumentVersion.version == version,
        )
        .first()
    )
    if version_row is None:
        raise GovernanceNotFound("版本不存在")

    gov = _get_or_create_governance(session, db_id, file_id, source_updated_at=file_row.created_at)
    _authorize_download(gov, user)

    abs_path = _resolve_version_blob(session, db_id, file_id, version_row)
    if abs_path is None:
        raise GovernanceNotFound("版本文件不存在或不可访问")

    _increment_usage(session, gov)

    filename = file_row.filename or f"file_{file_row.file_id}"
    return {
        "abs_path": abs_path,
        "filename": filename,
        "size_bytes": os.path.getsize(abs_path),
        "extension": os.path.splitext(filename)[1].lstrip(".").lower(),
        "db_id": db_id,
        "file_id": file_id,
        "version": version,
    }
