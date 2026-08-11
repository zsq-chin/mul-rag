import os
import re
import asyncio
import functools
import traceback
import httpx

from pydantic import BaseModel
from fastapi import Response
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Body, Query, Header, Request
from urllib.parse import quote
from starlette.responses import StreamingResponse

from src.utils import logger
from src import executor, retriever, config, knowledge_base, graph_base
from server.services.audit_service import AuditService
from server.services.upload_service import (
    UploadError,
    resolve_upload_path,
    resolve_upload_paths,
    save_upload_stream_async,
)
from server.utils.auth_middleware import get_required_user, get_superadmin_user
from server.models.user_model import User
from server.services.graph_import import GraphImportService, internal_token_matches, resolve_import_artifact
from server.services.http_clients import get_graph_worker_client, get_tianshu_client
from server.services.concurrency import graph_import_gate, retrieval_gate, upstream_proxy_gate
from typing import Literal
from fastapi.responses import JSONResponse
from pathlib import Path
import pandas as pd
from pathlib import Path as PathlibPath
data = APIRouter(prefix="/data")
UPLOAD_DIR = Path(os.getenv("GRAPH_UPLOAD_DIR", os.path.join(config.save_dir, "data", "graphragfile")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# 无 db_id 的通用上传目录（graphrag 预处理等流程使用）
GENERAL_UPLOAD_DIR = os.path.join(config.save_dir, "data", "uploads")

TIANSHU_API_BASE = os.getenv("TIANSHU_API_BASE", "http://tianshu-backend:8000/api/v1")


async def _run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, functools.partial(func, *args, **kwargs))

@data.get("/")
async def get_databases(current_user: User = Depends(get_required_user)):
    try:
        async with retrieval_gate:
            database = await _run_blocking(knowledge_base.get_databases)
    except Exception as e:
        logger.error(f"获取数据库列表失败 {e}, {traceback.format_exc()}")
        return {"message": f"获取数据库列表失败 {e}", "databases": []}
    return database

@data.post("/")
async def create_database(
    database_name: str = Body(...),
    description: str = Body(...),
    dimension: int | None = Body(None),
    current_user: User = Depends(get_superadmin_user)
):
    logger.debug(f"Create database {database_name}")
    try:
        async with retrieval_gate:
            existing_dbs_dict = await _run_blocking(knowledge_base.get_databases)
        db_list = existing_dbs_dict.get("databases", [])
        if any(db.get("name") == database_name for db in db_list):
            raise HTTPException(
                status_code=400,
                detail=f"数据库名 '{database_name}' 已存在"
            )
        async with retrieval_gate:
            database_info = await _run_blocking(
                knowledge_base.create_database,
                database_name, description, dimension=dimension,
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建数据库失败 {e}, {traceback.format_exc()}")
        return {"message": f"创建数据库失败 {e}", "status": "failed"}
    return database_info


def convert_to_graph_format(input_csv_path: PathlibPath, output_csv_path: PathlibPath) -> dict:
    """
    将知识图谱CSV文件转换为图数据库上传格式

    Args:
        input_csv_path: 输入的CSV文件路径
        output_csv_path: 输出的CSV文件路径

    Returns:
        dict: 转换结果信息
    """
    try:
        # 检查输入文件是否存在
        if not input_csv_path.exists():
            return {"status": "error", "detail": f"输入文件不存在: {input_csv_path}"}

        # 读取CSV文件
        print(f"📖 正在读取文件: {input_csv_path}")
        df = pd.read_csv(input_csv_path)

        # 检查必需的列是否存在
        required_columns = ['source', 'target', 'description']
        missing_columns = [col for col in required_columns if col not in df.columns]

        if missing_columns:
            return {
                "status": "error",
                "detail": f"CSV文件中缺少必需的列: {missing_columns}",
                "available_columns": list(df.columns)
            }

        # 提取需要的列并重命名
        graph_df = df[['source', 'description', 'target']].copy()
        graph_df.columns = ['h', 'r', 't']  # 重命名为图数据库要求的格式

        # 确保输出目录存在
        output_csv_path.parent.mkdir(parents=True, exist_ok=True)

        # 保存为新的CSV文件
        graph_df.to_csv(output_csv_path, index=False, encoding='utf-8')

        print(f"✅ 已成功转换文件格式")
        print(f"📊 转换统计: {len(graph_df)} 条关系")
        print(f"💾 输出文件: {output_csv_path}")

        return {
            "status": "success",
            "detail": "文件格式转换成功",
            "input_file": str(input_csv_path),
            "output_file": str(output_csv_path),
            "relationship_count": len(graph_df),
            "sample_data": graph_df.head(3).to_dict('records')  # 返回前3条数据作为示例
        }

    except Exception as e:
        return {"status": "error", "detail": f"文件格式转换失败: {str(e)}"}


@data.delete("/")
async def delete_database(db_id, current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"Delete database {db_id}")
    async with retrieval_gate:
        await _run_blocking(knowledge_base.delete_database, db_id)
    return {"message": "删除成功"}

@data.post("/query-test")
async def query_test(query: str = Body(...), meta: dict = Body(...), current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"Query test in {meta}: {query}")
    async with retrieval_gate:
        result = await _run_blocking(
            retriever.query_knowledgebase, query, history=None, refs={"meta": meta},
        )
    return result

@data.post("/file-to-chunk")
async def file_to_chunk(db_id: str = Body(...), files: list[str] = Body(...), params: dict = Body(...), current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"File to chunk for db_id {db_id}: {files} {params=}")
    try:
        async with retrieval_gate:
            # 前端提交的是上传接口返回的 file_id（裸存储名），解析到该库的上传目录；
            # 兼容通用上传目录，但绝对路径/目录穿越一律由解析器拒绝
            upload_dir = await _run_blocking(knowledge_base.get_db_upload_path, db_id)
            resolved = resolve_upload_paths(upload_dir, files, general_dir=GENERAL_UPLOAD_DIR)
            processed_files = await knowledge_base.save_files_for_pending_indexing(db_id, resolved, params)
        return {"message": "Files processed and pending indexing", "files": processed_files, "status": "success"}
    except UploadError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except Exception as e:
        logger.error(f"Failed to process files for pending indexing: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to process files for pending indexing: {e}", "status": "failed"}

@data.post("/url-to-chunk")
async def url_to_chunk(db_id: str = Body(...), urls: list[str] = Body(...), params: dict = Body(...), current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"Url to chunk for db_id {db_id}: {urls} {params=}")
    try:
        async with retrieval_gate:
            processed_urls = await knowledge_base.save_urls_for_pending_indexing(db_id, urls, params)
        return {"message": "URLs processed and pending indexing", "urls": processed_urls, "status": "success"}
    except Exception as e:
        logger.error(f"Failed to process URLs for pending indexing: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to process URLs for pending indexing: {e}", "status": "failed"}

@data.post("/add-by-file")
async def create_document_by_file(db_id: str = Body(...), files: list[str] = Body(...), current_user: User = Depends(get_superadmin_user)):
    raise ValueError("This method is deprecated. Use /file-to-chunk and /index-file instead.")

@data.post("/add-by-chunks")
async def add_by_chunks(db_id: str = Body(...), file_chunks: dict = Body(...), current_user: User = Depends(get_superadmin_user)):
    raise ValueError("This method is deprecated. Use /file-to-chunk and /index-file instead.")

@data.post("/index-file")
async def index_file(db_id: str = Body(...), file_id: str = Body(...), current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"Indexing file_id {file_id} in db_id {db_id}")
    try:
        async with retrieval_gate:
            result = await knowledge_base.trigger_file_indexing(db_id, file_id)
        return {"message": f"File {file_id} indexing initiated", "details": result, "status": "success"}
    except Exception as e:
        logger.error(f"Failed to index file {file_id}: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to index file {file_id}: {e}", "status": "failed"}

@data.get("/info")
async def get_database_info(db_id: str, current_user: User = Depends(get_superadmin_user)):
    # logger.debug(f"Get database {db_id} info")
    async with retrieval_gate:
        database = await _run_blocking(knowledge_base.get_database_info, db_id)
    if database is None:
        raise HTTPException(status_code=404, detail="Database not found")
    return database

@data.delete("/document")
async def delete_document(db_id: str = Body(...), file_id: str = Body(...), request: Request = None, current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"DELETE document {file_id} info in {db_id}")
    async with retrieval_gate:
        await _run_blocking(knowledge_base.delete_file, db_id, file_id)
    AuditService.record(
        "knowledge.delete",
        user_id=current_user.id,
        resource_type="knowledge_file",
        resource_id=file_id,
        detail={"file_id": file_id, "db_id": db_id},
        ip=request.client.host if request and request.client else None,
    )
    return {"message": "删除成功"}

@data.get("/document")
async def get_document_info(db_id: str, file_id: str, current_user: User = Depends(get_superadmin_user)):
    logger.debug(f"GET document {file_id} info in {db_id}")

    try:
        async with retrieval_gate:
            info = await _run_blocking(knowledge_base.get_file_info, db_id, file_id)
    except Exception as e:
        logger.error(f"Failed to get file info, {e}, {db_id=}, {file_id=}, {traceback.format_exc()}")
        info = {"message": "Failed to get file info", "status": "failed"}

    return info

@data.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db_id: str | None = Query(None),
    request: Request = None,
    current_user: User = Depends(get_superadmin_user)
):
    ip = request.client.host if request and request.client else None
    try:
        # 根据db_id获取上传路径，如果db_id为None则使用默认路径
        if db_id:
            async with retrieval_gate:
                upload_dir = await _run_blocking(knowledge_base.get_db_upload_path, db_id)
        else:
            upload_dir = GENERAL_UPLOAD_DIR

        # 分块流式 + 白名单 + 大小上限 + 原子改名，全程不整体读入内存
        stored_name, size_bytes = await save_upload_stream_async(file, upload_dir)
    except UploadError as e:
        AuditService.record(
            "knowledge.upload",
            user_id=current_user.id,
            resource_type="knowledge_file",
            status="failed",
            detail={
                "filename": getattr(file, "filename", "") or "",
                "db_id": db_id,
                "reason": e.message[:200],
            },
            ip=ip,
        )
        raise HTTPException(status_code=e.status_code, detail=e.message)

    AuditService.record(
        "knowledge.upload",
        user_id=current_user.id,
        resource_type="knowledge_file",
        resource_id=stored_name,
        detail={"filename": getattr(file, "filename", "") or "", "db_id": db_id, "size_bytes": size_bytes},
        ip=ip,
    )
    # 只返回 file_id/文件名/大小，绝不返回服务器绝对路径
    return {
        "message": "File successfully uploaded",
        "file_id": stored_name,
        "filename": stored_name,
        "size_bytes": size_bytes,
        "db_id": db_id,
    }

@data.get("/graph")
async def get_graph_info(current_user: User = Depends(get_superadmin_user)):
    async with retrieval_gate:
        graph_info = await _run_blocking(graph_base.get_graph_info)
    if graph_info is None:
        raise HTTPException(status_code=400, detail="图数据库获取出错")
    return graph_info

@data.post("/graph/index-nodes")
async def index_nodes(data: dict = Body(default={}), current_user: User = Depends(get_superadmin_user)):
    # 获取参数或使用默认值
    kgdb_name = data.get('kgdb_name', 'neo4j')

    # 调用GraphDatabase的add_embedding_to_nodes方法
    async with graph_import_gate:
        if not await _run_blocking(graph_base.is_running):
            raise HTTPException(status_code=400, detail="图数据库未启动")
        count = await _run_blocking(graph_base.add_embedding_to_nodes, kgdb_name=kgdb_name)

    return {"status": "success", "message": f"已成功为{count}个节点添加嵌入向量", "indexed_count": count}

@data.get("/graph/node")
async def get_graph_node(entity_name: str, current_user: User = Depends(get_superadmin_user)):
    async with retrieval_gate:
        result = await _run_blocking(graph_base.query_node, entity_name=entity_name)
        formatted = await _run_blocking(graph_base.format_query_result_to_graph, result)
    return {"result": formatted, "message": "success"}

@data.get("/graph/nodes")
async def get_graph_nodes(kgdb_name: str, num: int, current_user: User = Depends(get_superadmin_user)):
    if not config.enable_knowledge_graph:
        raise HTTPException(status_code=400, detail="Knowledge graph is not enabled")

    logger.debug(f"Get graph nodes in {kgdb_name} with {num} nodes")
    async with retrieval_gate:
        result = await _run_blocking(graph_base.get_sample_nodes, kgdb_name, num)
        formatted = await _run_blocking(graph_base.format_general_results, result)
    return {"result": formatted, "message": "success"}

@data.post("/graph/add-by-jsonl")
async def add_graph_entity(file_path: str = Body(...), kgdb_name: str | None = Body(None), current_user: User = Depends(get_superadmin_user)):
    if not config.enable_knowledge_graph:
        return {"message": "知识图谱未启用", "status": "failed"}

    if not file_path.endswith('.csv'):
        return {"message": "文件格式错误，请上传 csv 文件", "status": "failed"}

    # 只接受通用上传目录内的裸 file_id；绝对路径/盘符/UNC/穿越一律由解析器拒绝
    try:
        resolved_path = resolve_upload_path(GENERAL_UPLOAD_DIR, file_path)
    except UploadError as e:
        return {"message": e.message, "status": "failed"}

    try:
        async with graph_import_gate:
            await graph_base.jsonl_file_add_entity(resolved_path, kgdb_name)
        return {"message": "实体添加成功", "status": "success"}
    except Exception as e:
        logger.error(f"添加实体失败: {e}, {traceback.format_exc()}")
        return {"message": f"添加实体失败: {e}", "status": "failed"}
#处理文件
class FileHandleRequest(BaseModel):
    file_path: str
@data.post("/graph/handle")
async def graphfile_handle(request: FileHandleRequest, current_user: User = Depends(get_superadmin_user)):
    """Submit a file to the external processing API and poll until completion."""
    file_path = request.file_path
    EXTERNAL_API_URL = f"{TIANSHU_API_BASE}/tasks/submit"
    POLL_INTERVAL = 5
    TIMEOUT = 600
    logger.debug(f"graphfile_handle: {file_path}")
    try:
        # 上传接口只回传 file_id（裸存储名），按通用上传目录安全解析；
        # 绝对路径/盘符/UNC/目录穿越一律由解析器拒绝
        input_file = Path(resolve_upload_path(GENERAL_UPLOAD_DIR, file_path))
        task_name = input_file.name

        loop = asyncio.get_running_loop()
        async with upstream_proxy_gate:
            result = await loop.run_in_executor(
                executor, functools.partial(graph_base.file_Handle, input_file, EXTERNAL_API_URL)
            )
        if not result or "task_id" not in result:
            return {"message": "文件提交失败", "detail": result}

        task_id = result["task_id"]
        logger.info(f"graphfile_handle: task {task_id} submitted")

        tianshu_client = get_tianshu_client()
        start_time = loop.time()
        while True:
            async with upstream_proxy_gate:
                resp = await tianshu_client.get(f"/tasks/{task_id}", timeout=30)
                resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status", "").lower()

            if status == "completed":
                copied_file = await loop.run_in_executor(
                    executor, functools.partial(graph_base.copy_output, task_name)
                )
                return {
                    "task_name": task_name,
                    "message": "文件处理完成",
                    "task_id": task_id,
                    "output_file": str(copied_file),
                    "result": status_data.get("result"),
                }
            elif status == "failed":
                return {
                    "task_name": task_name,
                    "message": "文件处理失败",
                    "task_id": task_id,
                    "detail": status_data,
                }

            if loop.time() - start_time > TIMEOUT:
                return {
                    "task_name": task_name,
                    "status": "处理超时",
                    "task_id": task_id,
                }

            await asyncio.sleep(POLL_INTERVAL)

    except Exception as e:
        logger.error(f"graphfile_handle failed: {e}")
        return {"message": f"文件处理失败: {str(e)}"}


@data.post("/graph/build_graph")
async def api_build_graph(current_user: User = Depends(get_superadmin_user)):
    try:
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.post("/build_graph", json={"clean_copypath": True})
        if resp.status_code != 200:
            return {"status": "failed", "detail": f"远程服务错误: {resp.text}"}
        return {"status": "success", "detail": resp.json()}
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.post("/graph/build_drillgraph")
async def api_build_drillgraph(current_user: User = Depends(get_superadmin_user)):
    try:
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.post("/build_drillgraph", json={"clean_copypath": True})
        if resp.status_code != 200:
            return {"status": "failed", "detail": f"远程服务错误: {resp.text}"}
        return {"status": "success", "detail": resp.json()}
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/get_file_list/{graph_type}")
async def api_get_file_list(graph_type: str, current_user: User = Depends(get_superadmin_user)):
    try:
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.get(f"/get_file_list/{graph_type}")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.delete("/graph/delete_file/{graph_type}/{file_name}")
async def api_delete_graph_file(
    graph_type: str,
    file_name: str,
    current_user: User = Depends(get_superadmin_user),
):
    """删除指定图谱类型的文件（中间转发到内部服务）"""
    try:
        encoded_file_name = quote(file_name, safe='')
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.delete(f"/delete_file/{graph_type}/{encoded_file_name}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise HTTPException(status_code=resp.status_code, detail=detail)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/get_downloadable_files/{graph_type}")
async def api_get_downloadable_files(graph_type: str, current_user: User = Depends(get_superadmin_user)):
    try:
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.get(f"/get_downloadable_files/{graph_type}")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/download_file/{graph_type}/{file_name}")
async def api_download_file(graph_type: str, file_name: str, current_user: User = Depends(get_superadmin_user)):
    """Stream a file download from the graph worker without buffering in memory."""
    if graph_type not in ("ground", "drill"):
        return {"status": "failed", "detail": "不支持的图表类型"}

    encoded_filename = quote(file_name, safe='')
    client = get_graph_worker_client()

    await upstream_proxy_gate.__aenter__()
    try:
        request = client.build_request("GET", f"/download_file/{graph_type}/{encoded_filename}")
        resp = await client.send(request, stream=True)
    except asyncio.CancelledError:
        await upstream_proxy_gate.__aexit__(None, None, None)
        raise
    except Exception as exc:
        await upstream_proxy_gate.__aexit__(type(exc), exc, exc.__traceback__)
        logger.error(f"Graph file download error: {exc}")
        return {"status": "failed", "detail": f"文件下载失败: {exc}"}

    if resp.status_code != 200:
        try:
            error_msg = f"文件下载失败，状态码: {resp.status_code}"
            if resp.status_code in (400, 404):
                try:
                    error_body = await resp.aread()
                    import json as _json
                    error_msg = _json.loads(error_body).get("detail", error_msg)
                except Exception:
                    pass
        finally:
            await resp.aclose()
            await upstream_proxy_gate.__aexit__(None, None, None)
        return {"status": "failed", "detail": error_msg}

    encoded_file_name = quote(file_name, safe='')
    content_disposition = f"attachment; filename*=UTF-8''{encoded_file_name}"

    async def _stream():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            try:
                await resp.aclose()
            finally:
                await upstream_proxy_gate.__aexit__(None, None, None)

    return StreamingResponse(
        _stream(),
        status_code=200,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={
            "Content-Disposition": content_disposition,
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


#     graph_type: str = Path(..., description="图谱类型", regex="^(drill|ground)$"),
#     file_name: str = Path(..., description="要下载的文件名"),
#     current_user: User = Depends(get_admin_user)
# ):
#     """下载指定类型的图谱文件"""
#     try:
#         # 直接在函数中定义路径
#         INDEX_ROOT = Path("/app/indexing")
#         DRILL_INDEX_ROOT = Path("/app/indexing_drill")
#         GROUND_DOWNLOAD_DIR = INDEX_ROOT / "ground_graph_fill"
#         DRILL_DOWNLOAD_DIR = DRILL_INDEX_ROOT / "drill_graph_fill"
#         # 根据图谱类型确定目标目录
#         if graph_type == "drill":
#             target_dir = DRILL_DOWNLOAD_DIR
#         else:  # ground
#             target_dir = GROUND_DOWNLOAD_DIR
#
#         file_to_download = target_dir / file_name
#
#         # 检查文件是否存在
#         if not file_to_download.exists():
#             raise HTTPException(
#                 status_code=404,
#                 detail=f"文件不存在: {file_to_download}"
#             )
#
#         # 检查是否是有效文件
#         if not file_to_download.is_file():
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"'{file_name}' 不是一个有效文件"
#             )
#
#         print(f"📥 准备下载文件: {file_to_download}")
#
#         # 返回文件响应
#         return FileResponse(
#             path=file_to_download,
#             filename=file_name,
#             media_type='application/octet-stream'
#         )
#
#     except HTTPException:
#         # 重新抛出已有的 HTTP 异常
#         raise
#     except Exception as e:
#         # 处理其他异常
#         raise HTTPException(
#             status_code=500,
#             detail=f"下载文件失败: {str(e)}"
#         ) from e

@data.post("/graph/run_graphrag")
async def run_graphrag_index(current_user: User = Depends(get_superadmin_user)):
    """Trigger a graphrag build via the graph worker job API."""
    try:
        client = get_graph_worker_client()
        async with upstream_proxy_gate:
            resp = await client.post("/jobs", json={"graph_type": "ground"})
        if resp.status_code == 202:
            body = resp.json()
            return {"message": "GraphRAG 索引构建任务已提交", "task_id": body.get("id"), "status": body.get("status")}
        body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"detail": resp.text}
        return {"error": body.get("detail", f"远程服务错误: {resp.status_code}")}
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Graph worker unreachable")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Graph worker timeout")
    except Exception as e:
        logger.error(f"run_graphrag_index failed: {e}")
        return {"error": f"执行失败: {e}"}

@data.post("/update")
async def update_database_info(
    db_id: str = Body(...),
    name: str = Body(...),
    description: str = Body(...),
    current_user: User = Depends(get_superadmin_user)
):
    logger.debug(f"Update database {db_id} info: {name}, {description}")
    try:
        async with retrieval_gate:
            database = await _run_blocking(knowledge_base.update_database, db_id, name, description)
        return {"message": "更新成功", "database": database}
    except Exception as e:
        logger.error(f"更新数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"更新数据库失败: {e}")


class InternalGraphImportRequest(BaseModel):
    task_id: str
    graph_type: Literal['ground', 'drill']
    artifact_path: str


@data.post('/graph/internal/import')
async def internal_import_graph_artifact(
    request: InternalGraphImportRequest,
    x_graph_internal_token: str | None = Header(default=None, alias='X-Graph-Internal-Token'),
):
    # 1. Validate internal token.
    if not internal_token_matches(x_graph_internal_token):
        raise HTTPException(status_code=401, detail='Invalid internal token')

    # 2. Resolve the import artifact path.
    try:
        resolved_path = resolve_import_artifact(request.graph_type, request.artifact_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # 3. Check graph database availability without leaking credentials.
    if not hasattr(graph_base, 'driver') or graph_base.driver is None:
        raise HTTPException(status_code=503, detail='Graph database unavailable')

    # 4. Run the import.
    try:
        async with graph_import_gate:
            stats = await _run_blocking(
                GraphImportService(graph_base).import_csv, resolved_path, request.graph_type
            )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"Graph import failed: {exc}")
        raise HTTPException(status_code=500, detail='Graph import failed')

    # 5. Return success response.
    return {
        'task_id': request.task_id,
        'graph_type': request.graph_type,
        'artifact_path': request.artifact_path,
        'status': 'success',
        'node_count': stats.node_count,
        'relationship_count': stats.relationship_count,
        'embedded_count': stats.embedded_count,
        'vector_index_ready': stats.vector_index_ready,
    }


# ---------------------------------------------------------------------------
# Graph job proxy -- forwards to the graphrag worker's durable job API.
# ---------------------------------------------------------------------------

_VALID_TASK_ID = re.compile(r'[0-9a-f]{32}')


def _validate_task_id(task_id: str) -> str:
    """Reject anything that is not exactly 32 lowercase hex characters.

    This matches the worker's real ID format and blocks traversal attempts
    (``..``, encoded slashes, uppercase, punctuation, wrong length).
    """
    if not _VALID_TASK_ID.fullmatch(task_id):
        raise HTTPException(
            status_code=422,
            detail="Invalid task_id: must be exactly 32 lowercase hexadecimal characters",
        )
    return task_id


class GraphJobCreateRequest(BaseModel):
    graph_type: Literal['ground', 'drill']


async def _proxy_graph_worker(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
) -> JSONResponse:
    """Forward a request to the graphrag worker and return a faithful proxy
    response.  Upstream 404/409/5xx are mapped to the same status codes; a
    success-shaped body is never returned on upstream failure."""
    client = get_graph_worker_client()
    try:
        async with upstream_proxy_gate:
            resp = await client.request(method, path, json=json_body)
    except httpx.ConnectError:
        raise HTTPException(status_code=502, detail="Graph worker unreachable")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Graph worker timeout")
    except httpx.HTTPError as exc:
        logger.error(f"Graph worker proxy error: {exc}")
        raise HTTPException(status_code=502, detail="Graph worker error")

    # Faithfully propagate upstream status and body.
    try:
        body = resp.json()
    except Exception:
        body = {"detail": resp.text}

    return JSONResponse(status_code=resp.status_code, content=body)


@data.post('/graph/jobs')
async def graph_job_submit(
    request: GraphJobCreateRequest,
    current_user: User = Depends(get_superadmin_user),
):
    """Submit a new graph build job via the worker."""
    return await _proxy_graph_worker('POST', '/jobs', json_body=request.model_dump())


@data.get('/graph/jobs/{task_id}')
async def graph_job_get(
    task_id: str,
    current_user: User = Depends(get_superadmin_user),
):
    """Get job status from the worker."""
    _validate_task_id(task_id)
    return await _proxy_graph_worker('GET', f'/jobs/{task_id}')


@data.post('/graph/jobs/{task_id}/cancel')
async def graph_job_cancel(
    task_id: str,
    current_user: User = Depends(get_superadmin_user),
):
    """Request cancellation of an active job."""
    _validate_task_id(task_id)
    return await _proxy_graph_worker('POST', f'/jobs/{task_id}/cancel')


@data.post('/graph/jobs/{task_id}/retry')
async def graph_job_retry(
    task_id: str,
    current_user: User = Depends(get_superadmin_user),
):
    """Retry a terminal (failed/cancelled/interrupted) job."""
    _validate_task_id(task_id)
    return await _proxy_graph_worker('POST', f'/jobs/{task_id}/retry')

