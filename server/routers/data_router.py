import os
import asyncio
import traceback
import fastapi
from distutils.file_util import copy_file
import subprocess
from email.quoprimime import unquote

from pydantic import BaseModel
from fastapi import Response
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Body, Form, Query
from fastapi.responses import FileResponse
from urllib.parse import unquote, quote

from starlette.responses import StreamingResponse

from src.utils import logger, hashstr
from src import executor, retriever, config, knowledge_base, graph_base
from server.utils.auth_middleware import get_admin_user
from server.models.user_model import User
from typing import List, Optional
from fastapi.responses import JSONResponse
from pathlib import Path
import time
import requests
import pandas as pd
from pathlib import Path as PathlibPath
import shutil
data = APIRouter(prefix="/data")
UPLOAD_DIR = Path("D:\shanhai\sage-master\sage-master\saves\data\graphragfile")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

@data.get("/")
async def get_databases(current_user: User = Depends(get_admin_user)):
    try:
        database = knowledge_base.get_databases()
    except Exception as e:
        logger.error(f"获取数据库列表失败 {e}, {traceback.format_exc()}")
        return {"message": f"获取数据库列表失败 {e}", "databases": []}
    return database

@data.post("/")
async def create_database(
    database_name: str = Body(...),
    description: str = Body(...),
    dimension: int | None = Body(None),
    current_user: User = Depends(get_admin_user)
):
    logger.debug(f"Create database {database_name}")
    try:
        existing_dbs_dict = knowledge_base.get_databases()  
        db_list = existing_dbs_dict.get("databases", [])  
        if any(db.get("name") == database_name for db in db_list):
            raise HTTPException(
                status_code=400,
                detail=f"数据库名 '{database_name}' 已存在"
            )
        database_info = knowledge_base.create_database(
            database_name,
            description,
            dimension=dimension
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
async def delete_database(db_id, current_user: User = Depends(get_admin_user)):
    logger.debug(f"Delete database {db_id}")
    knowledge_base.delete_database(db_id)
    return {"message": "删除成功"}

@data.post("/query-test")
async def query_test(query: str = Body(...), meta: dict = Body(...), current_user: User = Depends(get_admin_user)):
    logger.debug(f"Query test in {meta}: {query}")
    result = retriever.query_knowledgebase(query, history=None, refs={"meta": meta})
    return result

@data.post("/file-to-chunk")
async def file_to_chunk(db_id: str = Body(...), files: list[str] = Body(...), params: dict = Body(...), current_user: User = Depends(get_admin_user)):
    logger.debug(f"File to chunk for db_id {db_id}: {files} {params=}")
    try:
        processed_files = await knowledge_base.save_files_for_pending_indexing(db_id, files, params)
        return {"message": "Files processed and pending indexing", "files": processed_files, "status": "success"}
    except Exception as e:
        logger.error(f"Failed to process files for pending indexing: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to process files for pending indexing: {e}", "status": "failed"}

@data.post("/url-to-chunk")
async def url_to_chunk(db_id: str = Body(...), urls: list[str] = Body(...), params: dict = Body(...), current_user: User = Depends(get_admin_user)):
    logger.debug(f"Url to chunk for db_id {db_id}: {urls} {params=}")
    try:
        processed_urls = await knowledge_base.save_urls_for_pending_indexing(db_id, urls, params)
        return {"message": "URLs processed and pending indexing", "urls": processed_urls, "status": "success"}
    except Exception as e:
        logger.error(f"Failed to process URLs for pending indexing: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to process URLs for pending indexing: {e}", "status": "failed"}

@data.post("/add-by-file")
async def create_document_by_file(db_id: str = Body(...), files: list[str] = Body(...), current_user: User = Depends(get_admin_user)):
    raise ValueError("This method is deprecated. Use /file-to-chunk and /index-file instead.")

@data.post("/add-by-chunks")
async def add_by_chunks(db_id: str = Body(...), file_chunks: dict = Body(...), current_user: User = Depends(get_admin_user)):
    raise ValueError("This method is deprecated. Use /file-to-chunk and /index-file instead.")

@data.post("/index-file")
async def index_file(db_id: str = Body(...), file_id: str = Body(...), current_user: User = Depends(get_admin_user)):
    logger.debug(f"Indexing file_id {file_id} in db_id {db_id}")
    try:
        result = await knowledge_base.trigger_file_indexing(db_id, file_id)
        return {"message": f"File {file_id} indexing initiated", "details": result, "status": "success"}
    except Exception as e:
        logger.error(f"Failed to index file {file_id}: {e}, {traceback.format_exc()}")
        return {"message": f"Failed to index file {file_id}: {e}", "status": "failed"}

@data.get("/info")
async def get_database_info(db_id: str, current_user: User = Depends(get_admin_user)):
    # logger.debug(f"Get database {db_id} info")
    database = knowledge_base.get_database_info(db_id)
    if database is None:
        raise HTTPException(status_code=404, detail="Database not found")
    return database

@data.delete("/document")
async def delete_document(db_id: str = Body(...), file_id: str = Body(...), current_user: User = Depends(get_admin_user)):
    logger.debug(f"DELETE document {file_id} info in {db_id}")
    knowledge_base.delete_file(db_id, file_id)
    return {"message": "删除成功"}

@data.get("/document")
async def get_document_info(db_id: str, file_id: str, current_user: User = Depends(get_admin_user)):
    logger.debug(f"GET document {file_id} info in {db_id}")

    try:
        info = knowledge_base.get_file_info(db_id, file_id)
    except Exception as e:
        logger.error(f"Failed to get file info, {e}, {db_id=}, {file_id=}, {traceback.format_exc()}")
        info = {"message": "Failed to get file info", "status": "failed"}

    return info

@data.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db_id: str | None = Query(None),
    current_user: User = Depends(get_admin_user)
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No selected file")

    # 根据db_id获取上传路径，如果db_id为None则使用默认路径
    if db_id:
        upload_dir = knowledge_base.get_db_upload_path(db_id)
    else:
        upload_dir = os.path.join(config.save_dir, "data", "uploads")

    basename, ext = os.path.splitext(file.filename)
    filename = f"{basename}_{hashstr(basename, 4, with_salt=True)}{ext}".lower()
    file_path = os.path.join(upload_dir, filename)
    os.makedirs(upload_dir, exist_ok=True)

    with open(file_path, "wb") as buffer:
        buffer.write(await file.read())

    return {"message": "File successfully uploaded", "file_path": file_path, "db_id": db_id}

@data.get("/graph")
async def get_graph_info(current_user: User = Depends(get_admin_user)):
    graph_info = graph_base.get_graph_info()
    if graph_info is None:
        raise HTTPException(status_code=400, detail="图数据库获取出错")
    return graph_info

@data.post("/graph/index-nodes")
async def index_nodes(data: dict = Body(default={}), current_user: User = Depends(get_admin_user)):
    if not graph_base.is_running():
        raise HTTPException(status_code=400, detail="图数据库未启动")

    # 获取参数或使用默认值
    kgdb_name = data.get('kgdb_name', 'neo4j')

    # 调用GraphDatabase的add_embedding_to_nodes方法
    count = graph_base.add_embedding_to_nodes(kgdb_name=kgdb_name)

    return {"status": "success", "message": f"已成功为{count}个节点添加嵌入向量", "indexed_count": count}

@data.get("/graph/node")
async def get_graph_node(entity_name: str, current_user: User = Depends(get_admin_user)):
    result = graph_base.query_node(entity_name=entity_name)
    return {"result": graph_base.format_query_result_to_graph(result), "message": "success"}

@data.get("/graph/nodes")
async def get_graph_nodes(kgdb_name: str, num: int, current_user: User = Depends(get_admin_user)):
    if not config.enable_knowledge_graph:
        raise HTTPException(status_code=400, detail="Knowledge graph is not enabled")

    logger.debug(f"Get graph nodes in {kgdb_name} with {num} nodes")
    result = graph_base.get_sample_nodes(kgdb_name, num)
    return {"result": graph_base.format_general_results(result), "message": "success"}

@data.post("/graph/add-by-jsonl")
async def add_graph_entity(file_path: str = Body(...), kgdb_name: str | None = Body(None), current_user: User = Depends(get_admin_user)):
    if not config.enable_knowledge_graph:
        return {"message": "知识图谱未启用", "status": "failed"}

    if not file_path.endswith('.csv'):
        return {"message": "文件格式错误，请上传 csv 文件", "status": "failed"}

    try:
        await graph_base.jsonl_file_add_entity(file_path, kgdb_name)
        return {"message": "实体添加成功", "status": "success"}
    except Exception as e:
        logger.error(f"添加实体失败: {e}, {traceback.format_exc()}")
        return {"message": f"添加实体失败: {e}", "status": "failed"}
#处理文件
class FileHandleRequest(BaseModel):
    file_path: str
@data.post("/graph/handle")
async def graphfile_handle(request: FileHandleRequest):
    file_path = request.file_path
    '''首先进行文件处理'''
    EXTERNAL_API_URL = "http://host.docker.internal:8000/api/v1/tasks/submit"
    TASK_STATUS_URL = "http://host.docker.internal:8000/api/v1/tasks"  # 用于查询任务状态
    POLL_INTERVAL = 5  # 每隔 5 秒轮询一次任务状态
    TIMEOUT = 600  # 最长等待时间 600 秒
    print(file_path)
    ROOT_DIR = Path(__file__).resolve().parent.parent.parent   # 向上一级
    try:
        input_file = ROOT_DIR / file_path
        task_name = input_file.name  # 提取文件名作为任务名
        if not input_file.exists():
            print("❌ 文件不存在，无法提交")
            return {"message": "文件不存在，无法提交"}

        # 提交任务
        result = graph_base.file_Handle(input_file, EXTERNAL_API_URL)
        if not result or "task_id" not in result:
            print("❌ 文件提交失败，返回结果异常")
            return {"message": "文件提交失败", "detail": result}

        task_id = result["task_id"]
        print(f"✅ 文件提交成功，任务ID: {task_id}")

        # 开始轮询任务状态
        start_time = time.time()
        while True:
            # 查询任务状态
            resp = requests.get(f"{TASK_STATUS_URL}/{task_id}", timeout=30)
            resp.raise_for_status()
            status_data = resp.json()
            status = status_data.get("status", "").lower()

            if status == "completed":
                print("✅ 任务完成，返回结果给前端")
                # 复制 output 文件
                print(task_name)
                copied_file = graph_base.copy_output(task_name)
                print(str(copied_file))
                return {
                    "task_name": task_name,
                    "message": "文件处理完成",
                    "task_id": task_id,
                    "output_file": str(copied_file),
                    "result": status_data.get("result")  # 这里返回实际分析结果
                }
            elif status == "failed":
                print("❌ 任务处理失败")
                return {
                    "task_name": task_name,
                    "message": "文件处理失败",
                    "task_id": task_id,
                    "detail": status_data
                }

            # 超时处理
            if time.time() - start_time > TIMEOUT:
                return {
                    "task_name": task_name,
                    "status": "处理超时",
                    "task_id": task_id
                }

            # 等待下一次轮询
            #容易死机
            time.sleep(POLL_INTERVAL)

    except Exception as e:
        print(f"❌ 文件处理失败: {str(e)}")
        return {"message": f"文件处理失败: {str(e)}"}


@data.post("/graph/build_graph")
def api_build_graph():
    try:
        response = requests.post(
            "http://host.docker.internal:8111/build_graph",
            json={"clean_copypath": True}
        )

        if response.status_code != 200:
            return {
                "status": "failed",
                "detail": f"远程服务错误: {response.text}"
            }

        return {
            "status": "success",
            "detail": response.json()
        }

    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.post("/graph/build_drillgraph")
def api_build_drillgraph():
    try:
        response = requests.post(
            "http://host.docker.internal:8111/build_drillgraph",
            json={"clean_copypath": True}
        )

        if response.status_code != 200:
            return {
                "status": "failed",
                "detail": f"远程服务错误: {response.text}"
            }

        return {
            "status": "success",
            "detail": response.json()
        }

    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/get_file_list/{graph_type}")
def api_get_file_list(graph_type: str):
    try:

        response = requests.get(
            f"http://host.docker.internal:8111/get_file_list/{graph_type}"
        )
        print(response)
        return Response(
            response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )

    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.delete("/graph/delete_file/{graph_type}/{file_name}")
def api_delete_graph_file(
    graph_type: str = fastapi.Path(..., description="图谱类型 drill/ground", regex="^(drill|ground)$"),
    file_name: str = fastapi.Path(..., description="要删除的文件名")
):
    """
    删除指定图谱类型的文件（中间转发到内部服务）
    """
    try:
        # 对文件名进行 URL 编码
        from urllib.parse import quote
        encoded_file_name = quote(file_name, safe='')

        # 构建内部服务 URL
        target_url = f"http://host.docker.internal:8111/delete_file/{graph_type}/{encoded_file_name}"

        # 发起 DELETE 请求到内部服务
        response = requests.delete(target_url)

        # 如果返回不是 2xx，则抛出异常
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except:
                detail = response.text
            raise HTTPException(status_code=response.status_code, detail=detail)

        # 成功返回内部服务内容
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )

    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/get_downloadable_files/{graph_type}")
def api_get_downloadable_files(graph_type: str):
    try:

        response = requests.get(
            f"http://host.docker.internal:8111/get_downloadable_files/{graph_type}"
        )

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )

    except Exception as e:
        return {"status": "failed", "detail": str(e)}

@data.get("/graph/download_file/{graph_type}/{file_name}")
async def api_download_file(graph_type: str, file_name: str):
    try:
        print(f"接收到下载请求 - graph_type: {graph_type}, filename: {file_name}")

        # 验证 graph_type
        if graph_type not in ["ground", "drill"]:
            return {"status": "failed", "detail": "不支持的图表类型"}

        # 构建上游服务URL
        encoded_filename = quote(file_name, safe='')
        target_url = f"http://host.docker.internal:8111/download_file/{graph_type}/{encoded_filename}"


        # 请求上游服务 - 不要使用 stream=True
        response = requests.get(target_url)  # 移除了 stream=True

        # 处理错误响应
        if response.status_code != 200:
            error_msg = f"文件下载失败，状态码: {response.status_code}"
            if response.status_code in [404, 400]:
                try:
                    error_data = response.json()
                    error_msg = error_data.get('detail', error_msg)
                except:
                    pass
            return {"status": "failed", "detail": error_msg}

        # 获取文件信息
        file_size = len(response.content)
        content_type = response.headers.get('Content-Type', 'application/octet-stream')

        print(f"文件下载成功，大小: {file_size} bytes")
        print(f"文件内容预览: {response.text[:100]}")  # 调试：查看文件内容

        # 对文件名进行 RFC 5987 编码，支持中文
        encoded_file_name = quote(file_name, safe='')
        content_disposition = f"attachment; filename*=UTF-8''{encoded_file_name}"

        # 直接返回文件内容
        return Response(
            content=response.content,
            status_code=200,
            headers={
                'Content-Type': content_type,
                'Content-Disposition': content_disposition,
                'Content-Length': str(file_size),
                'Access-Control-Expose-Headers': 'Content-Disposition'
            },
            media_type=content_type
        )

    except Exception as e:
        print(f"异常: {str(e)}")
        return {"status": "failed", "detail": str(e)}


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
async def run_graphrag_index():
    """
    触发 graphrag 索引构建
    """
    cmd = [
        "docker", "exec",
        "graphrag-worker",
        "python", "-m", "graphrag.index",
        "--root", "./indexing"
    ]
    try:
        subprocess.run(cmd, check=True)
        return {"message": "GraphRAG 索引构建成功"}
    except subprocess.CalledProcessError as e:
        return {"error": f"执行失败: {e}"}

@data.post("/update")
async def update_database_info(
    db_id: str = Body(...),
    name: str = Body(...),
    description: str = Body(...),
    current_user: User = Depends(get_admin_user)
):
    logger.debug(f"Update database {db_id} info: {name}, {description}")
    try:
        database = knowledge_base.update_database(db_id, name, description)
        return {"message": "更新成功", "database": database}
    except Exception as e:
        logger.error(f"更新数据库失败 {e}, {traceback.format_exc()}")
        raise HTTPException(status_code=400, detail=f"更新数据库失败: {e}")

