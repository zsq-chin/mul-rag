"""知识字典 API 路由（设计文档 §13）。

统一前缀 /api/knowledge-dictionaries。所有写接口独立鉴权；
普通用户只能查看/检索已发布活动版本。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.models.user_model import User
from server.schemas.knowledge_dictionary import (
    BatchReviewRequest,
    DictionaryCreate,
    DictionaryUpdate,
    EntryCreate,
    EntryUpdate,
    GenerateRequest,
    MergeRequest,
    ReviewRequest,
    SearchRequest,
)
from server.services.audit_service import AuditService
from server.utils.auth_middleware import get_required_user

from server.services.knowledge_dictionary import (
    export_service,
    jobs as job_service,
    permissions as perms,
    service as dict_service,
    source_adapters,
    vector_indexer,
)
from server.services.knowledge_dictionary.errors import DictionaryError
from server.services.knowledge_dictionary.seed_import import create_seed_import_job

router = APIRouter(prefix="/knowledge-dictionaries", tags=["KnowledgeDictionary"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _http(exc: DictionaryError) -> HTTPException:
    """业务错误统一结构（§14.1）。"""
    return HTTPException(status_code=exc.status_code, detail=exc.to_dict())


def _ip(request: Optional[Request]) -> Optional[str]:
    return request.client.host if request and request.client else None


def _audit(action: str, user: User, resource_id: Any = None, status: str = "success", detail: Optional[Dict[str, Any]] = None, request: Optional[Request] = None) -> None:
    AuditService.record(
        action,
        user_id=getattr(user, "id", None),
        resource_type="knowledge_dictionary",
        resource_id=resource_id,
        status=status,
        detail=detail,
        ip=_ip(request),
    )


def _ok(data: Any, message: str = "") -> Dict[str, Any]:
    return {"status": "success", "data": data, "message": message}


def _guard_manager(user: User) -> None:
    """直接调用的管理门卫（把业务错误映射为统一 HTTP 结构）。"""
    try:
        perms.ensure_manager(user)
    except DictionaryError as exc:
        raise _http(exc)


# ---------------------------------------------------------------------------
# 字典与版本（§13.1）
# ---------------------------------------------------------------------------


@router.get("")
async def list_dictionaries_api(
    keyword: str = Query("", max_length=200),
    status: str = Query("", max_length=20),
    domain: str = Query("", max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    data = dict_service.list_dictionaries(db, user, keyword=keyword, status=status, domain=domain, page=page, page_size=page_size)
    return _ok(data)


@router.post("")
async def create_dictionary_api(
    payload: DictionaryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.create_dictionary(db, user, name=payload.name, description=payload.description or "", domain=payload.domain or "")
    except DictionaryError as exc:
        _audit("dictionary.create", user, status="failed", detail={"name": payload.name, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.create", user, resource_id=data["id"], detail={"name": payload.name}, request=request)
    return _ok(data, "字典已创建")


# ---------------------------------------------------------------------------
# 来源选择列表（生成向导步骤一；§4.3：admin/superadmin 可用）
# 注意：静态路径必须声明在 /{dictionary_id} 之前，避免被路径参数吞掉。
# ---------------------------------------------------------------------------


@router.get("/sources/knowledge-bases")
async def list_source_kbs(db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    _guard_manager(user)
    return _ok(source_adapters.list_kbs_for_source(db))


@router.get("/sources/knowledge-bases/{db_id}/files")
async def list_source_kb_files(
    db_id: str,
    keyword: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    _guard_manager(user)
    try:
        data = source_adapters.list_kb_files_for_source(db, db_id, keyword=keyword, page=page, page_size=page_size)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.get("/{dictionary_id}")
async def get_dictionary_api(dictionary_id: int, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = dict_service.get_dictionary_detail(db, user, dictionary_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.patch("/{dictionary_id}")
async def update_dictionary_api(
    dictionary_id: int,
    payload: DictionaryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.update_dictionary(
            db, user, dictionary_id, name=payload.name or "", description=payload.description or "", domain=payload.domain or ""
        )
    except DictionaryError as exc:
        _audit("dictionary.update", user, resource_id=dictionary_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.update", user, resource_id=dictionary_id, request=request)
    return _ok(data, "字典已更新")


@router.delete("/{dictionary_id}")
async def delete_dictionary_api(dictionary_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = dict_service.delete_dictionary(db, user, dictionary_id)
    except DictionaryError as exc:
        _audit("dictionary.delete", user, resource_id=dictionary_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.delete", user, resource_id=dictionary_id, request=request)
    return _ok(data, "字典已删除")


@router.get("/{dictionary_id}/versions")
async def list_versions_api(dictionary_id: int, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = dict_service.list_versions(db, user, dictionary_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.get("/{dictionary_id}/versions/{version_id}")
async def get_version_api(dictionary_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = dict_service.get_version_detail(db, user, dictionary_id, version_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.post("/{dictionary_id}/versions/{version_id}/publish")
async def publish_version_api(
    dictionary_id: int, version_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)
):
    try:
        data = dict_service.publish_version(db, user, dictionary_id, version_id)
    except DictionaryError as exc:
        _audit("dictionary.version.publish", user, resource_id=version_id, status="failed", detail={"dictionary_id": dictionary_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.version.publish", user, resource_id=version_id, detail={"dictionary_id": dictionary_id}, request=request)
    return _ok(data, "版本已发布")


@router.post("/{dictionary_id}/versions/{version_id}/withdraw")
async def withdraw_version_api(
    dictionary_id: int, version_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)
):
    try:
        data = dict_service.withdraw_version(db, user, dictionary_id, version_id)
    except DictionaryError as exc:
        _audit("dictionary.version.withdraw", user, resource_id=version_id, status="failed", detail={"dictionary_id": dictionary_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.version.withdraw", user, resource_id=version_id, detail={"dictionary_id": dictionary_id}, request=request)
    return _ok(data, "版本已撤回")


# ---------------------------------------------------------------------------
# 上传来源（§7.3）
# ---------------------------------------------------------------------------


@router.post("/upload")
async def upload_dictionary_source(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    """上传临时来源文件，返回受控上传令牌 storage_ref（不写路径、不写知识库）。"""
    _guard_manager(user)
    max_bytes = source_adapters._max_upload_bytes()
    content = bytearray()
    while True:
        chunk = await file.read(1024 * 256)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes + 1024 * 1024:
            from server.services.knowledge_dictionary.errors import PayloadTooLarge

            _audit("dictionary.upload", user, status="failed", detail={"filename": file.filename, "reason": "too_large"}, request=request)
            raise _http(PayloadTooLarge("文件超过大小上限"))
    try:
        meta = source_adapters.save_upload_file(file.filename or "upload", bytes(content))
    except DictionaryError as exc:
        _audit("dictionary.upload", user, status="failed", detail={"filename": file.filename, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit(
        "dictionary.upload",
        user,
        detail={"filename": meta["file_name"], "size_bytes": meta["size_bytes"], "extension": meta["extension"]},
        request=request,
    )
    return _ok(meta, "文件已上传，可作为生成来源")


# ---------------------------------------------------------------------------
# 生成与任务（§13.2）
# ---------------------------------------------------------------------------


@router.post("/generate", status_code=202)
async def generate_dictionary_api(
    payload: GenerateRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = job_service.create_generate_job(db, user, payload.model_dump())
    except DictionaryError as exc:
        _audit("dictionary.job.create", user, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit(
        "dictionary.job.create",
        user,
        resource_id=data["id"],
        detail={"dictionary_id": data["dictionary_id"], "version_id": data["version_id"], "job_type": "generate"},
        request=request,
    )
    return _ok(data, "生成任务已创建")


@router.get("/jobs/{job_id}")
async def get_job_api(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = job_service.get_job(db, user, job_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job_api(job_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = job_service.cancel_job(db, user, job_id)
    except DictionaryError as exc:
        _audit("dictionary.job.cancel", user, resource_id=job_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.job.cancel", user, resource_id=job_id, request=request)
    return _ok(data, "任务取消请求已受理")


@router.post("/jobs/{job_id}/retry")
async def retry_job_api(job_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = job_service.retry_job(db, user, job_id)
    except DictionaryError as exc:
        _audit("dictionary.job.retry", user, resource_id=job_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.job.retry", user, resource_id=job_id, request=request)
    return _ok(data, "任务已重新排队")


@router.post("/seed-import", status_code=202)
async def seed_import_api(request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    """发起 XinJiang 种子迁移任务（幂等，§9）。"""
    try:
        data = create_seed_import_job(db, user)
    except DictionaryError as exc:
        _audit("dictionary.seed.import", user, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.seed.import", user, resource_id=data["id"], request=request)
    return _ok(data, "种子迁移任务已创建")


# ---------------------------------------------------------------------------
# 条目与审核（§13.3）
# ---------------------------------------------------------------------------


@router.get("/{dictionary_id}/versions/{version_id}/entries")
async def list_entries_api(
    dictionary_id: int,
    version_id: int,
    category: str = Query("", max_length=100),
    review_status: str = Query("", max_length=20),
    keyword: str = Query("", max_length=200),
    source_file: str = Query("", max_length=200),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    missing_fields: bool = Query(False),
    conflict_only: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.list_entries(
            db,
            user,
            dictionary_id,
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
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.post("/{dictionary_id}/versions/{version_id}/entries")
async def create_entry_api(
    dictionary_id: int,
    version_id: int,
    payload: EntryCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.create_entry(db, user, dictionary_id, version_id, payload.model_dump(exclude_none=True))
    except DictionaryError as exc:
        _audit("dictionary.entry.create", user, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.create", user, resource_id=data["id"], detail={"version_id": version_id, "standard_name": data["standard_name"]}, request=request)
    return _ok(data, "条目已创建")


@router.patch("/{dictionary_id}/versions/{version_id}/entries/{entry_id}")
async def update_entry_api(
    dictionary_id: int,
    version_id: int,
    entry_id: int,
    payload: EntryUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.update_entry(db, user, dictionary_id, version_id, entry_id, payload.model_dump(exclude_none=True))
    except DictionaryError as exc:
        _audit("dictionary.entry.update", user, resource_id=entry_id, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.update", user, resource_id=entry_id, detail={"version_id": version_id}, request=request)
    return _ok(data, "条目已更新")


@router.delete("/{dictionary_id}/versions/{version_id}/entries/{entry_id}")
async def delete_entry_api(
    dictionary_id: int,
    version_id: int,
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.delete_entry(db, user, dictionary_id, version_id, entry_id)
    except DictionaryError as exc:
        _audit("dictionary.entry.delete", user, resource_id=entry_id, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.delete", user, resource_id=entry_id, detail={"version_id": version_id}, request=request)
    return _ok(data, "条目已删除")


@router.get("/{dictionary_id}/versions/{version_id}/entries/{entry_id}/evidences")
async def get_entry_evidences_api(
    dictionary_id: int,
    version_id: int,
    entry_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.get_entry_evidences(db, user, dictionary_id, version_id, entry_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.post("/{dictionary_id}/versions/{version_id}/entries/batch-review")
async def batch_review_api(
    dictionary_id: int,
    version_id: int,
    payload: BatchReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.batch_review(
            db,
            user,
            dictionary_id,
            version_id,
            items=[item.model_dump() for item in payload.items],
            concurrency_token=payload.concurrency_token,
            allow_low_confidence=payload.allow_low_confidence,
        )
    except DictionaryError as exc:
        _audit("dictionary.entry.review", user, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.review", user, detail={"version_id": version_id, "succeeded": data["succeeded"]}, request=request)
    return _ok(data)


@router.post("/{dictionary_id}/versions/{version_id}/entries/{entry_id}/review")
async def review_entry_api(
    dictionary_id: int,
    version_id: int,
    entry_id: int,
    payload: ReviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.review_entry(db, user, dictionary_id, version_id, entry_id, payload.model_dump())
    except DictionaryError as exc:
        _audit("dictionary.entry.review", user, resource_id=entry_id, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.review", user, resource_id=entry_id, detail={"version_id": version_id, "review_status": data["review_status"]}, request=request)
    return _ok(data, "审核已完成")


@router.post("/{dictionary_id}/versions/{version_id}/entries/merge")
async def merge_entries_api(
    dictionary_id: int,
    version_id: int,
    payload: MergeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.merge_entries(
            db,
            user,
            dictionary_id,
            version_id,
            keep_entry_id=payload.keep_entry_id,
            merge_entry_ids=payload.merge_entry_ids,
            review_note=payload.review_note or "",
        )
    except DictionaryError as exc:
        _audit("dictionary.entry.merge", user, status="failed", detail={"version_id": version_id, "reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.entry.merge", user, resource_id=data["id"], detail={"version_id": version_id, "merged": len(payload.merge_entry_ids)}, request=request)
    return _ok(data, "条目已合并")


@router.get("/{dictionary_id}/versions/{version_id}/entries/merge-suggestions")
async def merge_suggestions_api(
    dictionary_id: int,
    version_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        data = dict_service.merge_suggestions(db, user, dictionary_id, version_id, limit=limit)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


# ---------------------------------------------------------------------------
# 向量索引与检索（§13.4）
# ---------------------------------------------------------------------------


@router.post("/{dictionary_id}/versions/{version_id}/index", status_code=202)
async def build_index_api(
    dictionary_id: int, version_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_required_user)
):
    try:
        data = job_service.create_index_job(db, user, dictionary_id, version_id)
    except DictionaryError as exc:
        _audit("dictionary.index.build", user, resource_id=version_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.index.build", user, resource_id=version_id, detail={"job_id": data["id"]}, request=request)
    return _ok(data, "索引任务已创建")


@router.get("/{dictionary_id}/versions/{version_id}/index-status")
async def index_status_api(dictionary_id: int, version_id: int, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = vector_indexer.version_index_status(db, dictionary_id, version_id)
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


@router.post("/search")
async def search_api(payload: SearchRequest, db: Session = Depends(get_db), user: User = Depends(get_required_user)):
    try:
        data = vector_indexer.search_entries(
            db,
            user,
            query=payload.query,
            dictionary_ids=payload.dictionary_ids,
            top_k=payload.top_k,
            version_id=payload.version_id,
            include_draft=payload.include_draft,
        )
    except DictionaryError as exc:
        raise _http(exc)
    return _ok(data)


# ---------------------------------------------------------------------------
# 导出（§13.5）
# ---------------------------------------------------------------------------


@router.get("/{dictionary_id}/versions/{version_id}/export")
async def export_api(
    dictionary_id: int,
    version_id: int,
    format: str = Query("xlsx", pattern="^(xlsx|csv|json)$"),
    include_rejected: bool = Query(False),
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_required_user),
):
    try:
        content, media_type, filename = export_service.export_version(
            db, user, dictionary_id, version_id, fmt=format, include_rejected=include_rejected
        )
    except DictionaryError as exc:
        _audit("dictionary.export", user, resource_id=version_id, status="failed", detail={"reason": str(exc)[:200]}, request=request)
        raise _http(exc)
    _audit("dictionary.export", user, resource_id=version_id, detail={"format": format}, request=request)
    headers = {"Content-Disposition": export_service.export_content_disposition(filename)}
    return Response(content=content, media_type=media_type, headers=headers)
