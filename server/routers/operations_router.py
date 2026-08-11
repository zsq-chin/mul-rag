# server/routers/operations_router.py
"""运维接口：本机备份、校验、预检、恢复与删除。

全部仅 superadmin 可访问。备份目录 saves/backups/。
数据库路径/配置/日志均来自 src 与 db_manager（本路由不做 Milvus 相关初始化）。
"""

import os

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from src import config
from src.utils.logging_config import LOG_FILE
from server.db_manager import db_manager
from server.services import backup_service, config_service, audit_service
from server.utils.auth_middleware import get_superadmin_user

router = APIRouter(prefix="/operations", tags=["Operations"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _client_ip(request: Request):
    return request.client.host if request.client else None


def _backup_dir():
    return os.path.join(config.save_dir, "backups")


def _kb_root():
    return os.path.join(config.save_dir, "knowledge")


def _restore_targets():
    return {
        "server.db": db_manager.db_path,
        "config.json": config.filename,
        "logs/app.log": LOG_FILE,
    }


@router.post("/backups")
async def create_backup(
    request: Request,
    include_kb: bool = Body(False),
    include_logs: bool = Body(True),
    note: str = Body(None),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    snapshot = config_service.sanitize_config_snapshot(
        config.dump_config(), drop_internal=True
    )
    try:
        row = backup_service.create_backup(
            db,
            _backup_dir(),
            db_manager.db_path,
            snapshot,
            log_path=LOG_FILE if include_logs else None,
            include_logs=include_logs,
            include_kb=include_kb,
            kb_roots=[_kb_root()] if include_kb else (),
            created_by=superadmin.username,
            note=note,
        )
    except backup_service.BackupError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    audit_service.record(
        "backup.create",
        user_id=superadmin.id,
        resource_type="backup",
        resource_id=row.id,
        detail={"backup_id": row.id, "size_bytes": row.size_bytes},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": backup_service._serialize_backup(row), "message": ""}


@router.get("/backups")
async def list_backups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    return {"status": "success", "data": backup_service.list_backups(db, page=page, page_size=page_size), "message": ""}


@router.get("/backups/{backup_id}")
async def get_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    row = backup_service.get_backup(db, backup_id)
    if row is None:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    return {"status": "success", "data": backup_service._serialize_backup(row), "message": ""}


@router.get("/backups/{backup_id}/download")
async def download_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        row, path = backup_service._resolve_zip_path(db, backup_id, _backup_dir())
    except backup_service.BackupError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return FileResponse(path, filename=row.filename, media_type="application/zip")


@router.post("/backups/{backup_id}/verify")
async def verify_backup(
    backup_id: int,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = backup_service.verify_backup(db, backup_id, _backup_dir())
    except backup_service.BackupError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "success", "data": data, "message": ""}


@router.post("/backups/{backup_id}/restore/preview")
async def preview_restore(
    backup_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = backup_service.preview_restore(
            db, backup_id, _backup_dir(), _restore_targets(), kb_target_root=_kb_root()
        )
    except backup_service.BackupError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "success", "data": data, "message": ""}


@router.post("/backups/{backup_id}/restore")
async def restore_backup(
    backup_id: int,
    request: Request,
    token: str = Body(...),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    snapshot = config_service.sanitize_config_snapshot(
        config.dump_config(), drop_internal=True
    )
    try:
        data = backup_service.restore_backup(
            db,
            backup_id,
            token,
            _backup_dir(),
            db_manager.db_path,
            config.filename,
            log_target=LOG_FILE,
            kb_target_root=_kb_root(),
            config_snapshot=snapshot,
            log_path=LOG_FILE,
            kb_roots=[_kb_root()],
            created_by=superadmin.username,
        )
    except backup_service.BackupError as e:
        audit_service.record(
            "backup.restore",
            user_id=superadmin.id,
            resource_type="backup",
            resource_id=backup_id,
            status="failed",
            detail={"backup_id": backup_id, "reason": str(e)[:200]},
            ip=_client_ip(request),
        )
        raise HTTPException(status_code=e.status_code, detail=str(e))
    audit_service.record(
        "backup.restore",
        user_id=superadmin.id,
        resource_type="backup",
        resource_id=backup_id,
        detail={"backup_id": backup_id, "restore_point_id": data["restore_point_id"]},
        ip=_client_ip(request),
    )
    return {"status": "success", "data": data, "message": ""}


@router.delete("/backups/{backup_id}")
async def delete_backup(
    backup_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = backup_service.delete_backup(db, backup_id, _backup_dir())
    except backup_service.BackupError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "success", "data": data, "message": ""}
