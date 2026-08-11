# server/routers/evaluation_router.py

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from server.db_manager import db_manager
from server.schemas.evaluation import (
    EvaluationCasePayload,
    EvaluationSuiteCreate,
    EvaluationSuiteUpdate,
)
from server.services import evaluation_service
from server.services.audit_service import AuditService
from server.utils.auth_middleware import get_superadmin_user

router = APIRouter(prefix="/evaluation", tags=["Evaluation"])


def get_db():
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def _to_http(exc: evaluation_service.EvaluationError):
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _audit(action, user, suite_id, status, detail, ip):
    AuditService.record(
        action,
        user_id=user.id,
        resource_type="evaluation",
        resource_id=suite_id,
        status=status,
        detail=detail,
        ip=ip,
    )


# --- 测试集 ---


@router.post("/suites")
async def create_evaluation_suite(
    payload: EvaluationSuiteCreate,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.create_suite(
            db,
            payload.name,
            description=payload.description,
            category=payload.category,
            creator=superadmin.username or "",
        )
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    _audit("evaluation.suite.create", superadmin, data["id"], "success",
           {"suite_name": data["name"], "suite_id": data["id"]}, request.client.host)
    return {"status": "success", "data": data, "message": "测试集已创建"}


@router.get("/suites")
async def list_evaluation_suites(
    keyword: str = Query("", max_length=200),
    category: str = Query("", max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    data = evaluation_service.list_suites(db, keyword=keyword, category=category, page=page, page_size=page_size)
    return {"status": "success", "data": data, "message": ""}


@router.get("/suites/{suite_id}")
async def get_evaluation_suite(
    suite_id: int,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.get_suite(db, suite_id)
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    return {"status": "success", "data": data, "message": ""}


@router.patch("/suites/{suite_id}")
async def update_evaluation_suite(
    suite_id: int,
    payload: EvaluationSuiteUpdate,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.update_suite(db, suite_id, payload.model_dump(exclude_none=True))
    except evaluation_service.EvaluationError as e:
        _audit("evaluation.suite.update", superadmin, suite_id, "failed",
               {"suite_id": suite_id}, request.client.host)
        raise _to_http(e)
    _audit("evaluation.suite.update", superadmin, suite_id, "success",
           {"suite_name": data["name"], "suite_id": suite_id}, request.client.host)
    return {"status": "success", "data": data, "message": "测试集已更新"}


@router.delete("/suites/{suite_id}")
async def delete_evaluation_suite(
    suite_id: int,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        evaluation_service.delete_suite(db, suite_id)
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    _audit("evaluation.suite.delete", superadmin, suite_id, "success",
           {"suite_id": suite_id}, request.client.host)
    return {"status": "success", "data": {"deleted": True}, "message": "测试集已删除"}


# --- 用例 ---


@router.get("/suites/{suite_id}/cases")
async def list_evaluation_cases(
    suite_id: int,
    keyword: str = Query("", max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.list_cases(db, suite_id, keyword=keyword, page=page, page_size=page_size)
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    return {"status": "success", "data": data, "message": ""}


@router.post("/suites/{suite_id}/cases")
async def create_evaluation_case(
    suite_id: int,
    payload: EvaluationCasePayload,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.create_case(db, suite_id, payload.model_dump(exclude_none=True))
    except evaluation_service.EvaluationError as e:
        _audit("evaluation.case.create", superadmin, suite_id, "failed",
               {"suite_id": suite_id}, request.client.host)
        raise _to_http(e)
    _audit("evaluation.case.create", superadmin, suite_id, "success",
           {"suite_id": suite_id, "case_count": 1}, request.client.host)
    return {"status": "success", "data": data, "message": "用例已添加"}


@router.patch("/suites/{suite_id}/cases/{case_id}")
async def update_evaluation_case(
    suite_id: int,
    case_id: int,
    payload: EvaluationCasePayload,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        data = evaluation_service.update_case(db, suite_id, case_id, payload.model_dump(exclude_none=True))
    except evaluation_service.EvaluationError as e:
        _audit("evaluation.case.update", superadmin, suite_id, "failed",
               {"suite_id": suite_id, "case_id": case_id}, request.client.host)
        raise _to_http(e)
    _audit("evaluation.case.update", superadmin, suite_id, "success",
           {"suite_id": suite_id, "case_id": case_id}, request.client.host)
    return {"status": "success", "data": data, "message": "用例已更新"}


@router.delete("/suites/{suite_id}/cases/{case_id}")
async def delete_evaluation_case(
    suite_id: int,
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        evaluation_service.delete_case(db, suite_id, case_id)
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    _audit("evaluation.case.delete", superadmin, suite_id, "success",
           {"suite_id": suite_id, "case_id": case_id}, request.client.host)
    return {"status": "success", "data": {"deleted": True}, "message": "用例已删除"}


# --- 导入 / 导出 ---


@router.post("/suites/{suite_id}/import")
async def import_evaluation_cases(
    suite_id: int,
    request: Request,
    format: str = Query("json", pattern="^(json|csv)$"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    content = await file.read()
    try:
        data = evaluation_service.import_cases(db, suite_id, content, format)
    except evaluation_service.EvaluationError as e:
        _audit("evaluation.import", superadmin, suite_id, "failed",
               {"suite_id": suite_id, "format": format}, request.client.host)
        raise _to_http(e)
    _audit("evaluation.import", superadmin, suite_id, "success",
           {"suite_id": suite_id, "format": format, "count": data.get("imported", 0)},
           request.client.host)
    message = f"成功导入 {data['imported']} 条"
    if data["row_errors"]:
        message = f"导入失败：{len(data['row_errors'])} 行校验未通过"
    return {"status": "success", "data": data, "message": message}


@router.get("/suites/{suite_id}/export")
async def export_evaluation_cases(
    suite_id: int,
    format: str = Query("json", pattern="^(json|csv)$"),
    request: Request,
    db: Session = Depends(get_db),
    superadmin=Depends(get_superadmin_user),
):
    try:
        if format == "csv":
            content = evaluation_service.export_cases_csv(db, suite_id)
        else:
            content = evaluation_service.export_cases_json(db, suite_id)
    except evaluation_service.EvaluationError as e:
        raise _to_http(e)
    if format == "csv":
        filename = f"suite_{suite_id}_cases.csv"
        disposition = f"attachment; filename=\"{filename}\""
        return StreamingResponse(
            iter([content]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )
    return {"status": "success", "data": content, "message": ""}
