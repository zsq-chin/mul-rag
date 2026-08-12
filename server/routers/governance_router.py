# server/routers/governance_router.py

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.schemas.governance import GovernanceUpdate, VersionSnapshotCreate
from server.services import governance_service
from server.services.audit_service import AuditService
from server.utils.auth_middleware import get_required_user, get_superadmin_user

router = APIRouter(prefix="/governance", tags=["Governance"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _to_http(exc: governance_service.GovernanceError):
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _attachment_disposition(filename: str) -> str:
    """RFC 5987 安全编码 Content-Disposition 文件名（含中文）。"""
    fallback = "".join(c for c in filename if ord(c) < 128) or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _file_chunks(abs_path, chunk_size=1 << 16):
    with open(abs_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk


@router.get("/databases/{db_id}/documents")
async def list_governance_documents(
    db_id: str,
    keyword: str = Query("", max_length=200),
    knowledge_type: str = Query("", max_length=50),
    confidentiality: str = Query("", max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """分页返回文档治理元数据（任何已登录用户可预览）。"""
    try:
        data = governance_service.list_documents(
            db,
            db_id,
            keyword=keyword,
            knowledge_type=knowledge_type,
            confidentiality=confidentiality,
            page=page,
            page_size=page_size,
        )
    except governance_service.GovernanceError as e:
        raise _to_http(e)
    return {"status": "success", "data": data, "message": ""}


@router.get("/databases/{db_id}/documents/{file_id}")
async def get_governance_document(
    db_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """返回单个文档治理详情（预览），计入使用次数。"""
    try:
        data = governance_service.get_document(db, db_id, file_id)
    except governance_service.GovernanceError as e:
        raise _to_http(e)
    return {"status": "success", "data": data, "message": ""}


@router.patch("/databases/{db_id}/documents/{file_id}")
async def patch_governance_document(
    db_id: str,
    file_id: str,
    payload: GovernanceUpdate,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    """更新治理字段（superadmin）。"""
    try:
        # exclude_unset 区分“未提交”与“明确清空（null）”，允许清空可选字段
        data = governance_service.update_governance(
            db, db_id, file_id, payload.model_dump(exclude_unset=True)
        )
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.metadata.update",
            user_id=superadmin.id,
            resource_type="document",
            resource_id=file_id,
            status="failed",
            detail={"db_id": db_id, "file_id": file_id},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.metadata.update",
        user_id=superadmin.id,
        resource_type="document",
        resource_id=file_id,
        status="success",
        detail={
            "db_id": db_id,
            "file_id": file_id,
            "filename": data.get("filename"),
            "confidentiality": data.get("confidentiality"),
            "download_allowed": data.get("download_allowed"),
        },
        ip=request.client.host,
    )
    return {"status": "success", "data": data, "message": "治理信息已更新"}


@router.get("/databases/{db_id}/documents/{file_id}/download")
async def download_governance_document(
    db_id: str,
    file_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """受控下载：restricted 仅 superadmin；download_allowed=0 一律拒绝。"""
    try:
        info = governance_service.resolve_download(db, db_id, file_id, user)
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.download",
            user_id=getattr(user, "id", None),
            resource_type="document",
            resource_id=file_id,
            status="failed",
            detail={"db_id": db_id, "file_id": file_id, "reason": str(e)},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.download",
        user_id=user.id,
        resource_type="document",
        resource_id=file_id,
        status="success",
        detail={
            "db_id": info["db_id"],
            "file_id": info["file_id"],
            "filename": info["filename"],
            "size_bytes": info["size_bytes"],
            "extension": info["extension"],
        },
        ip=request.client.host,
    )
    return StreamingResponse(
        _file_chunks(info["abs_path"]),
        media_type=governance_service.media_type_for(info["extension"]),
        headers={
            "Content-Disposition": _attachment_disposition(info["filename"]),
            "Content-Length": str(info["size_bytes"]),
        },
    )


@router.get("/databases/{db_id}/documents/{file_id}/versions")
async def list_document_versions(
    db_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """返回文档版本历史（任何已登录用户可查看）。"""
    try:
        data = governance_service.list_versions(db, db_id, file_id)
    except governance_service.GovernanceError as e:
        raise _to_http(e)
    return {"status": "success", "data": data, "message": ""}


@router.post("/databases/{db_id}/documents/{file_id}/versions/snapshot")
async def create_document_version_snapshot(
    db_id: str,
    file_id: str,
    payload: VersionSnapshotCreate,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    """创建源文件版本快照（superadmin）。不重建索引。"""
    try:
        data = governance_service.create_snapshot(
            db,
            db_id,
            file_id,
            creator=superadmin.username or "",
            note=payload.note,
        )
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.version.snapshot",
            user_id=superadmin.id,
            resource_type="document",
            resource_id=file_id,
            status="failed",
            detail={"db_id": db_id, "file_id": file_id},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.version.snapshot",
        user_id=superadmin.id,
        resource_type="document",
        resource_id=file_id,
        status="success",
        detail={
            "db_id": db_id,
            "file_id": file_id,
            "version": data.get("version"),
            "size_bytes": data.get("file_size"),
        },
        ip=request.client.host,
    )
    return {"status": "success", "data": data, "message": "版本快照已创建"}


@router.post("/blobs/gc")
async def gc_version_blobs(
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
    min_age_seconds: float = Query(default=3600, ge=0),
):
    """手动清理无引用且超过安全时间的版本 blob（superadmin）。

    H2.5：只删除超过安全时间且没有任何版本引用的 blob；默认 1 小时保护期保证
    并发创建中的新 blob（刚发布、版本记录尚未提交）不被误删；请求级暂存目录
    绝不触碰。返回 {"removed": n, "retained": m}。
    """
    try:
        stats = governance_service.gc_unreferenced_blobs(db, min_age_seconds=min_age_seconds)
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.version.blob_gc",
            user_id=superadmin.id,
            resource_type="version_blob",
            resource_id="",
            status="failed",
            detail={"min_age_seconds": min_age_seconds},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.version.blob_gc",
        user_id=superadmin.id,
        resource_type="version_blob",
        resource_id="",
        status="success",
        detail={"min_age_seconds": min_age_seconds, **stats},
        ip=request.client.host,
    )
    return {"status": "success", "data": stats, "message": "版本 blob GC 完成"}


@router.get("/databases/{db_id}/documents/{file_id}/versions/{version}/download")
async def download_document_version(
    db_id: str,
    file_id: str,
    version: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(get_required_user),
):
    """版本受控下载：权限同文档下载，只从受控版本目录读取。"""
    try:
        info = governance_service.resolve_version_download(db, db_id, file_id, version, user)
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.download",
            user_id=getattr(user, "id", None),
            resource_type="document_version",
            resource_id=f"{file_id}:v{version}",
            status="failed",
            detail={"db_id": db_id, "file_id": file_id, "version": version, "reason": str(e)},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.download",
        user_id=user.id,
        resource_type="document_version",
        resource_id=f"{file_id}:v{version}",
        status="success",
        detail={
            "db_id": info["db_id"],
            "file_id": info["file_id"],
            "filename": info["filename"],
            "size_bytes": info["size_bytes"],
            "extension": info["extension"],
            "version": info["version"],
        },
        ip=request.client.host,
    )
    return StreamingResponse(
        _file_chunks(info["abs_path"]),
        media_type=governance_service.media_type_for(info["extension"]),
        headers={
            "Content-Disposition": _attachment_disposition(info["filename"]),
            "Content-Length": str(info["size_bytes"]),
        },
    )


@router.get("/databases/{db_id}/export")
async def export_governance_metadata(
    db_id: str,
    request: Request,
    format: str = Query("json", pattern="^(json|xlsx)$"),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    """导出治理元数据（json 或 xlsx），不导出正文/向量/秘密。"""
    try:
        if format == "xlsx":
            content = governance_service.export_xlsx_bytes(db, db_id)
        else:
            content = governance_service.export_json(db, db_id)
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.export",
            user_id=superadmin.id,
            resource_type="database",
            resource_id=db_id,
            status="failed",
            detail={"db_id": db_id, "format": format},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.export",
        user_id=superadmin.id,
        resource_type="database",
        resource_id=db_id,
        status="success",
        detail={"db_id": db_id, "format": format},
        ip=request.client.host,
    )
    if format == "xlsx":
        return StreamingResponse(
            iter([content]),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": _attachment_disposition(
                    f"governance_{db_id}.xlsx"
                )
            },
        )
    return {"status": "success", "data": content, "message": ""}


@router.post("/databases/{db_id}/sync")
async def sync_governance(
    db_id: str,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    """为缺失记录的文档补建治理元数据（不解析、不重建索引）。"""
    try:
        data = governance_service.sync_governance(db, db_id)
    except governance_service.GovernanceError as e:
        AuditService.record(
            "knowledge.sync",
            user_id=superadmin.id,
            resource_type="database",
            resource_id=db_id,
            status="failed",
            detail={"db_id": db_id},
            ip=request.client.host,
        )
        raise _to_http(e)
    AuditService.record(
        "knowledge.sync",
        user_id=superadmin.id,
        resource_type="database",
        resource_id=db_id,
        status="success",
        detail={"db_id": db_id, "count": data.get("created", 0)},
        ip=request.client.host,
    )
    return {
        "status": "success",
        "message": "治理元数据已同步",
        "data": data,
    }
