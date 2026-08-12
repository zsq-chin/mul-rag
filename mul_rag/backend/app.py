import io
from fastapi import FastAPI, UploadFile, File, Query, Body, Form, Request
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio, time, os, random, string, json, re
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional

from fastapi import BackgroundTasks
from services.pdf_service import (
    save_upload,
    run_full_parse_pipeline,
    original_pdf_path,
    dir_original_pages,
    dir_parsed_pages,
    markdown_output,
    get_image_summaries,
    save_image_summaries,
    kb_dir,
    kb_files_dir,
    workdir,
    read_kb_metadata,
    write_kb_metadata,
    delete_workdir,
)
from services.excel_service import process_excel_file
from services.index_service import build_faiss_index, search_faiss, is_indexed, delete_index
from fastapi.responses import StreamingResponse, JSONResponse
from services.rag_service import retrieve, answer_stream, clear_history
from services.extraction import (
    save_uploaded_file_for_extraction,
    parse_file_content,
    locate_relevant_content,
    llm_extract,
    export_data,
)

app = FastAPI(
    title="多模态RAG系统API",
    version="1.0.0",
    description="多模态RAG系统开发实战后端API。"
)

# 允许前端本地联调
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 课堂演示方便，生产请收紧
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
# 使用绝对路径，确保不同cwd下都能定位到同一数据目录
DATA_ROOT = Path(__file__).resolve().parent / "data"
RESERVED_DATA_DIRS = {"__pycache__"}

# ---------------- 内存态存储（教学Mock） ----------------
# parsing_jobs["kbId:fileId"] = {status, progress}
parsing_jobs: Dict[str, Dict[str, Any]] = {}
# extraction_jobs["jobId"] = {status, progress, error, ...}
EXTRACTION_JOBS: Dict[str, Dict[str, Any]] = {}
citations: Dict[str, Dict[str, Any]] = {}   # citationId -> { fileId, page, snippet, bbox, previewUrl }

# ---------------- 工具函数 ----------------
def rid(prefix: str) -> str:
    return f"{prefix}_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

def now_ts() -> int:
    return int(time.time())

def err(code: str, message: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message}, "requestId": rid("req"), "ts": now_ts()}

def _slugify_kb_name(name: str) -> str:
    # 允许中文，仅移除文件系统非法字符: \ / : * ? " < > |
    # 并且将空白字符替换为下划线
    slug = re.sub(r'[\\/:*?"<>|]', "", name.strip())
    slug = re.sub(r'\s+', "_", slug)
    return slug

def _allocate_kb_id(preferred: Optional[str] = None) -> str:
    base = None
    if preferred:
        slug = _slugify_kb_name(preferred)
        if slug and slug not in RESERVED_DATA_DIRS:
            base = slug
    if not base:
        base = rid("kb")

    candidate = base
    counter = 1
    while (DATA_ROOT / candidate).exists() or candidate in RESERVED_DATA_DIRS:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _allocate_file_id(kb_id: str, filename: str) -> str:
    base = _slugify_kb_name(Path(filename).stem) or rid("file")
    candidate = base
    counter = 1
    root = kb_files_dir(kb_id)
    while (root / candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate

# ---------------- Pydantic 模型（契约） ----------------
class ChatRequest(BaseModel):
    message: str
    sessionId: Optional[str] = None
    kbId: Optional[str] = None
    fileId: Optional[str] = None

# ---------------- Health ----------------
@app.get(f"{API_PREFIX}/health", tags=["Health"])
async def health():
    return {"ok": True, "version": "1.0.0"}

# ---------------- Chat（SSE，POST 返回 event-stream） ----------------
class ChatRequest(BaseModel):
    message: str
    sessionId: Optional[str] = None
    pdfFileId: Optional[str] = None

@app.post(f"{API_PREFIX}/chat", tags=["Chat"])
async def chat_stream(req: ChatRequest):
    """
    SSE 事件：token | citation | done | error
    """
    async def gen():
        try:
            question = (req.message or "").strip()
            session_id = (req.sessionId or "default").strip()  # 默认单会话
            kb_id = (req.kbId or "").strip()
            file_id = (req.fileId or "").strip() or None

            if not kb_id:
                raise HTTPException(status_code=400, detail="KB_ID_REQUIRED")

            citations, context_text = [], ""
            branch = "no_context"
            
            # 尝试检索（支持全局或指定文件）
            try:
                citations, context_text = await retrieve(question, kb_id, file_id)
                branch = "with_context" if context_text else "no_context"
            except FileNotFoundError:
                branch = "no_context"
            except Exception as e:
                print(f"Retrieve error: {e}")
                branch = "no_context"

            # 先推送引用（若有）
            if branch == "with_context" and citations:
                for c in citations:
                    yield "event: citation\n"
                    yield f"data: {json.dumps(c)}\n\n"

            # 再推送 token 流（内部会写入历史）
            async for evt in answer_stream(
                question=question,
                citations=citations,
                context_text=context_text,
                branch=branch,
                session_id=session_id
            ):
                if evt["type"] == "token":
                    yield "event: token\n"
                    # 注意：这里确保 data 是合法 JSON 字符串
                    text = evt["data"].replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
                    yield f'data: {{"text":"{text}"}}\n\n'
                elif evt["type"] == "citation":
                    yield "event: citation\n"
                    yield f"data: {json.dumps(evt['data'])}\n\n"
                elif evt["type"] == "done":
                    used = "true" if evt["data"].get("used_retrieval") else "false"
                    yield "event: done\n"
                    yield f"data: {{\"used_retrieval\": {used}}}\n\n"

        except Exception as e:
            yield "event: error\n"
            esc = str(e).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
            yield f'data: {{"message":"{esc}"}}\n\n'

    headers = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

# ---------------- Chat: 清除对话 ----------------
class ClearChatRequest(BaseModel):
    sessionId: Optional[str] = None

@app.post(f"{API_PREFIX}/chat/clear", tags=["Chat"])
async def chat_clear(req: ClearChatRequest):
    sid = (req.sessionId or "default").strip()
    clear_history(sid)
    return {"ok": True, "sessionId": sid, "cleared": True}


# ---------------- KB: 新建知识库 ----------------
class CreateKBRequest(BaseModel):
    kbName: str
    embedModel: Optional[str] = None
    vectorStoreType: Optional[str] = None

class CreateKBResponse(BaseModel):
    kbId: str
    kbName: str

@app.post(f"{API_PREFIX}/kb/create", tags=["KB"], response_model=CreateKBResponse)
async def kb_create(payload: Optional[CreateKBRequest] = Body(default=None)):
    """创建一个新的知识库（名称必填，可指定向量模型与向量库类型）"""
    if not payload or not (payload.kbName or "").strip():
        raise HTTPException(status_code=400, detail="KB_NAME_REQUIRED")

    desired_name = payload.kbName.strip()
    kb_id = _allocate_kb_id(desired_name)
    kb_display_name = desired_name

    # 初始化该知识库的目录/索引/元数据
    kb_dir(kb_id)
    kb_files_dir(kb_id)
    try:
        from services.index_service import init_db, index_dir

        conn = init_db(kb_id)
        conn.close()
        index_dir(kb_id)
    except Exception as e:
        print(f"[kb_create] init storage failed: {e}")

    meta = write_kb_metadata(
        kb_id,
        {
            "name": kb_display_name,
            "createdAt": now_ts(),
            "embedModel": payload.embedModel,
            "vectorStoreType": payload.vectorStoreType or "faiss",
        },
    )
    return {"kbId": kb_id, "kbName": meta.get("name", kb_display_name)}

class DeleteKBRequest(BaseModel):
    kbId: str

@app.post(f"{API_PREFIX}/kb/delete", tags=["KB"])
async def kb_delete(req: DeleteKBRequest):
    """删除整个知识库"""
    import shutil
    kb_id = (req.kbId or "").strip()
    if not kb_id:
        raise HTTPException(status_code=400, detail="KB_ID_REQUIRED")
    
    kb_path = DATA_ROOT / kb_id
    if kb_path.exists() and kb_path.is_dir():
        # 安全检查：防止删除保留目录
        if kb_id in RESERVED_DATA_DIRS:
             raise HTTPException(status_code=400, detail="CANNOT_DELETE_RESERVED_DIR")
        try:
            shutil.rmtree(str(kb_path))
            return {"ok": True, "kbId": kb_id}
        except Exception as e:
            return JSONResponse(err("DELETE_KB_ERROR", f"Failed to delete KB: {e}"), status_code=500)
    else:
        return {"ok": True, "message": "KB not found or already deleted"}

class DeleteFileRequest(BaseModel):
    kbId: str
    fileId: str
    deleteIndex: bool = True
    deleteFile: bool = False

@app.post(f"{API_PREFIX}/kb/file/delete", tags=["KB"])
async def kb_file_delete(req: DeleteFileRequest):
    """删除文件或索引"""
    kb_id = (req.kbId or "").strip()
    file_id = (req.fileId or "").strip()
    if not kb_id or not file_id:
        raise HTTPException(status_code=400, detail="KB_ID_AND_FILE_ID_REQUIRED")

    # 1. 删除索引 (如果请求)
    if req.deleteIndex:
        out = delete_index(kb_id, file_id=file_id)
        if not out.get("ok"):
            return JSONResponse(err(out.get("error", "DELETE_INDEX_ERROR"), "删除索引失败"), status_code=500)
            
    # 2. 删除文件 (如果请求)
    if req.deleteFile:
        if not delete_workdir(kb_id, file_id):
            return JSONResponse(err("DELETE_FILE_ERROR", "删除文件失败"), status_code=500)
            
    return {"ok": True}

@app.get(f"{API_PREFIX}/kb/list", tags=["KB"])
async def kb_list():
    """列出所有已存在的知识库"""
    if not DATA_ROOT.exists():
        return {"kbs": []}

    kbs: List[Dict[str, Any]] = []
    for p in DATA_ROOT.iterdir():
        if not p.is_dir() or p.name in RESERVED_DATA_DIRS:
            continue

        meta = read_kb_metadata(p.name)
        kb_name = meta.get("name") or p.name
        created_at = meta.get("createdAt")
        embed_model = meta.get("embedModel")
        vs_type = meta.get("vectorStoreType") or "faiss"

        file_count = 0
        try:
            file_count = len([x for x in (p / "files").iterdir() if x.is_dir()]) if (p / "files").exists() else 0
        except Exception:
            file_count = 0

        kbs.append(
            {
                "kbId": p.name,
                "kbName": kb_name,
                "vectorStoreType": vs_type,
                "embedModel": embed_model,
                "fileCount": file_count,
                "createdAt": created_at,
            }
        )

    kbs.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return {"kbs": kbs}


@app.get(f"{API_PREFIX}/kb/files", tags=["KB"])
async def kb_files(kbId: str = Query(...)):
    """列出某个知识库下的所有文件"""
    kb_id = (kbId or "").strip()
    if not kb_id:
        raise HTTPException(status_code=400, detail="KB_ID_REQUIRED")
    root = DATA_ROOT / kb_id / "files"
    if not root.exists():
        return {"kbId": kb_id, "files": []}

    items = []
    for fdir in root.iterdir():
        if not fdir.is_dir():
            continue
        file_id = fdir.name
        # 找主文件
        main_file = None
        for pattern in ["*.pdf", "*.xlsx", "*.xls", "*.csv"]:
            matches = sorted(fdir.glob(pattern))
            if matches:
                main_file = matches[0]
                break
        file_name = main_file.name if main_file else None
        file_type = "unknown"
        if main_file:
            suf = main_file.suffix.lower()
            if suf == ".pdf":
                file_type = "pdf"
            elif suf in [".xlsx", ".xls", ".csv"]:
                file_type = "excel"

        has_md = markdown_output(kb_id, file_id).exists()
        page_count = 0
        try:
            page_dir = dir_original_pages(kb_id, file_id)
            if page_dir.exists():
                page_count = len(list(page_dir.glob("page-*.png")))
        except Exception:
            page_count = 0

        items.append(
            {
                "fileId": file_id,
                "fileName": file_name,
                "type": file_type,
                "hasParsed": has_md or (file_type == "excel" and main_file is not None),
                "pageCount": page_count,
                "isIndexed": is_indexed(kb_id, file_id=file_id),
            }
        )

    return {"kbId": kb_id, "files": items}

@app.get(f"{API_PREFIX}/kb/images", tags=["KB"])
async def kb_images_page(
    kbId: str = Query(...),
    page: int = Query(1, ge=1),
    pageSize: int = Query(24, ge=1, le=100),
):
    """获取知识库图片目录：服务端分页（数据源层切片，只返回当前页）。

    阶段 D1：分页、稳定排序、轻量元数据都发生在数据源所在进程内；响应固定为
    `items/page/pageSize/total`，pageSize 最大 100、默认 24。SAGE 侧不得再对
    全量列表本地切片（“假分页”）。
    """
    from services.image_catalog import paginate_kb_images

    kb_id = (kbId or "").strip()
    if not kb_id:
        return JSONResponse(err("PARAM_ERROR", "缺少知识库 ID"), status_code=400)
    return paginate_kb_images(DATA_ROOT, kb_id, page=page, page_size=pageSize)

@app.get(f"{API_PREFIX}/kb/file/dataframe", tags=["KB"])
async def kb_file_dataframe(kbId: str = Query(...), fileId: str = Query(...)):
    """获取 Excel/CSV 文件的 JSON 数据"""
    from services.pdf_service import original_pdf_path
    import pandas as pd
    
    file_path = original_pdf_path(kbId, fileId)
    if file_path.exists() and file_path.suffix.lower() in ['.xlsx', '.xls', '.csv']:
        try:
            sheets_data = {}
            # 定义清理函数：处理 NaN 和 Inf，使其符合 JSON 标准
            def clean_df(dframe):
                # 1. 将 Inf 替换为 NaN
                dframe = dframe.replace([float('inf'), float('-inf')], float('nan'))
                # 2. 将 NaN 替换为 None (JSON null)
                # 必须先转换为 object 类型，否则 None 可能会被强制转回 NaN (在 float 列中)
                dframe = dframe.astype(object).where(pd.notnull(dframe), None)
                return dframe

            # 定义表头修复函数
            def fix_header(dframe, fname):
                if dframe.empty:
                    return dframe
                
                header_row_idx = 0
                # 策略0: 检查第一行是否为文件名
                filename_no_ext = os.path.splitext(os.path.basename(fname))[0]
                first_row_text = dframe.iloc[0].astype(str).str.cat(sep=' ')
                
                if filename_no_ext in first_row_text and len(dframe) > 1:
                    header_row_idx = 1
                else:
                    # 策略1: 扫描前 10 行寻找有效列最多的行
                    max_valid_cols = 0
                    scan_rows = min(10, len(dframe))
                    for i in range(scan_rows):
                        row = dframe.iloc[i]
                        valid_count = row.count()
                        if valid_count > max_valid_cols:
                            max_valid_cols = valid_count
                            header_row_idx = i
                
                # 重设表头
                new_header = []
                row_vals = dframe.iloc[header_row_idx]
                for idx, val in enumerate(row_vals):
                    if pd.isna(val) or str(val).strip() == '':
                        new_header.append(f"Column_{idx}")
                    else:
                        # 修复：去除列名中的换行符
                        clean_val = str(val).strip().replace('\n', ' ').replace('\r', '')
                        new_header.append(clean_val)
                
                dframe.columns = new_header
                dframe = dframe.iloc[header_row_idx+1:].reset_index(drop=True)
                
                # 统一清理所有列名
                dframe.columns = [str(c).strip().replace('\n', ' ').replace('\r', '') for c in dframe.columns]
                
                return dframe

            if file_path.suffix.lower() == '.csv':
                df = pd.read_csv(file_path, header=None)
                df = fix_header(df, str(file_path))
                df = clean_df(df)
                sheets_data["Sheet1"] = df.to_dict(orient="records")
            else:
                dfs = pd.read_excel(file_path, sheet_name=None, header=None)
                for sheet_name, df in dfs.items():
                    df = fix_header(df, str(file_path))
                    df = clean_df(df)
                    sheets_data[sheet_name] = df.to_dict(orient="records")
            
            # 返回记录列表
            return {"fileId": fileId, "sheets": sheets_data}
        except Exception as e:
             return JSONResponse(err("READ_ERROR", f"无法读取文件: {str(e)}"), status_code=500)
    
    return JSONResponse(err("INVALID_FILE", "不是有效的表格文件"), status_code=400)

@app.get(f"{API_PREFIX}/kb/file/content", tags=["KB"])
async def kb_file_content(kbId: str = Query(...), fileId: str = Query(...)):
    """获取指定文件的 Markdown 内容"""
    from services.pdf_service import markdown_output, original_pdf_path
    import pandas as pd

    md_path = markdown_output(kbId, fileId)
    if md_path.exists():
        content = md_path.read_text(encoding="utf-8")
        return {"fileId": fileId, "content": content}
    
    # Check for Excel/CSV
    file_path = original_pdf_path(kbId, fileId)
    if file_path.exists() and file_path.suffix.lower() in ['.xlsx', '.xls', '.csv']:
        try:
            if file_path.suffix.lower() == '.csv':
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            
            # Return first 50 rows as Markdown table
            content = df.head(50).to_markdown(index=False)
            return {"fileId": fileId, "content": f"# Preview (First 50 rows)\n\n{content}"}
        except Exception as e:
             return JSONResponse(err("READ_ERROR", f"无法读取文件: {str(e)}"), status_code=500)

    return JSONResponse(err("FILE_NOT_FOUND", "文件内容不存在"), status_code=404)

# ---------------- PDF: 上传（支持指定 kbId） ----------------

@app.post(f"{API_PREFIX}/pdf/upload", tags=["PDF"])
async def pdf_upload(
    file: UploadFile = File(...), 
    kbId: str = Form(...),
    fileId: Optional[str] = Form(None),
    replace: Optional[bool] = True
):
    if not file:
        return JSONResponse(err("NO_FILE", "缺少文件"), status_code=400)
    
    kb_id = (kbId or "").strip()
    if not kb_id:
        return JSONResponse(err("KB_ID_REQUIRED", "缺少kbId"), status_code=400)

    fid = (fileId or "").strip() if fileId else _allocate_file_id(kb_id, file.filename)
    saved = save_upload(kb_id, fid, await file.read(), file.filename)
    parsing_jobs[f"{kb_id}:{fid}"] = {"status": "idle", "progress": 0}
    citations.clear()
    return saved

# ---------------- PDF: 触发解析 ----------------
@app.post(f"{API_PREFIX}/pdf/parse", tags=["PDF"])
async def pdf_parse(payload: Dict[str, Any] = Body(...), bg: BackgroundTasks = None):
    kb_id = (payload.get("kbId") or "").strip()
    file_id = (payload.get("fileId") or "").strip()
    method = payload.get("method", "original") # 默认使用 original

    if not kb_id or not file_id:
        return JSONResponse(err("BAD_REQUEST", "kbId 与 fileId 必填"), status_code=400)
    
    # 检查文件是否存在
    from services.pdf_service import original_pdf_path
    file_path = original_pdf_path(kb_id, file_id)
    if not file_path.exists():
        return JSONResponse(err("FILE_NOT_FOUND", "未找到该文件"), status_code=400)

    # 更新 current_pdf 以便 status 接口能返回进度
    parsing_jobs[f"{kb_id}:{file_id}"] = {"status": "parsing", "progress": 5}

    def _job():
        try:
            # 20 → 60 → 100 三阶段进度示意
            parsing_jobs[f"{kb_id}:{file_id}"]["progress"] = 20
            
            def _update_prog(p):
                parsing_jobs[f"{kb_id}:{file_id}"]["progress"] = p

            # Check file type
            # file_path 已经在上面获取了
            if file_path.suffix.lower() in ['.xlsx', '.xls', '.csv']:
                # Excel/CSV files don't need OCR/Parsing
                pass
            else:
                run_full_parse_pipeline(kb_id, file_id, method=method, progress_callback=_update_prog)   # 真解析
                
            parsing_jobs[f"{kb_id}:{file_id}"] = {"status": "ready", "progress": 100}
        except Exception as e:
            parsing_jobs[f"{kb_id}:{file_id}"] = {"status": "error", "progress": 0}
            print("Parse error:", e)

    if bg is not None:
        bg.add_task(_job)
    else:
        _job()

    return {"jobId": rid("j")}

# ---------------- PDF: 状态 ----------------
@app.get(f"{API_PREFIX}/pdf/status", tags=["PDF"])
async def pdf_status(kbId: str = Query(...), fileId: str = Query(...)):
    # 优先返回内存中的正在进行的任务状态
    key = f"{kbId}:{fileId}"
    job = parsing_jobs.get(key)
    if job and job.get("status") == "parsing":
        return {"status": "parsing", "progress": job.get("progress", 0)}
    
    # 否则检查磁盘
    from services.pdf_service import markdown_output, original_pdf_path
    
    # Check for Excel/CSV
    file_path = original_pdf_path(kbId, fileId)
    if file_path.exists() and file_path.suffix.lower() in ['.xlsx', '.xls', '.csv']:
        return {"status": "ready", "progress": 100}

    if markdown_output(kbId, fileId).exists():
        return {"status": "ready", "progress": 100}
        
    return {"status": "idle", "progress": 0}

# ---------------- PDF: 页面图 ----------------
@app.get(f"{API_PREFIX}/pdf/page", tags=["PDF"])
async def pdf_page(
    kbId: str = Query(...),
    fileId: str = Query(...),
    page: int = Query(..., ge=1),
    type: str = Query(..., regex="^(original|parsed)$")
):
    # 移除对 current_pdf 的强校验，允许访问历史文件
    # if not current_pdf["fileId"] or current_pdf["fileId"] != fileId:
    #     return JSONResponse(status_code=404, content=None)

    # if current_pdf["status"] != "ready" and type == "parsed":
    #     return JSONResponse(status_code=204, content=None)

    base = dir_original_pages(kbId, fileId) if type == "original" else dir_parsed_pages(kbId, fileId)
    img = base / f"page-{page:04d}.png"
    if not img.exists():
        return JSONResponse(err("PAGE_NOT_FOUND", "页面不存在或未渲染"), status_code=404)
    return FileResponse(str(img), media_type="image/png")

# ---------------- PDF: 图片文件 ----------------
@app.get(f"{API_PREFIX}/pdf/images", tags=["PDF"])
async def pdf_images(
    request: Request,
    kbId: str = Query(...),
    fileId: str = Query(...),
    imagePath: str = Query(...),
    thumb: int = Query(0, ge=0, le=1),
):
    """获取 PDF 解析后的图片文件；`thumb=1` 返回 320px 缓存缩略图。

    阶段 D1/D2：
    - 图片名 / kbId / fileId 在 image_catalog 内严格校验（拒绝穿越、绝对路径、
      盘符、UNC、NUL、URL 编码分隔符），不向外部暴露远端文件系统路径；
    - 缩略图按需生成并缓存到 `<file>/thumbs/`，源图更新后自动重新生成；
    - ETag 基于被服务文件 stat 指纹，支持 If-None-Match → 304，合理 Cache-Control。
    """
    from services.image_catalog import make_image_etag, resolve_image_for_serve

    kb_id = (kbId or "").strip()
    file_id = (fileId or "").strip()
    path, is_thumb = resolve_image_for_serve(
        DATA_ROOT, kb_id, file_id, imagePath, thumb=bool(thumb)
    )
    if path is None:
        return JSONResponse(err("IMAGE_NOT_FOUND", "图片文件不存在"), status_code=404)

    etag = make_image_etag(path, thumb=is_thumb)
    cache_control = "private, max-age=3600"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return FileResponse(
        str(path),
        media_type="image/jpeg" if is_thumb else "image/png",
        headers={"ETag": etag, "Cache-Control": cache_control},
    )

@app.get(f"{API_PREFIX}/pdf/images_list", tags=["PDF"])
async def pdf_images_list(kbId: str = Query(...), fileId: str = Query(...)):
    """获取PDF解析出的所有图片文件名列表"""
    # 移除对 current_pdf 的强校验
    # if not current_pdf["fileId"] or current_pdf["fileId"] != fileId:
    #     return JSONResponse(status_code=404, content={"images": []})
    
    # if current_pdf["status"] != "ready":
    #     return JSONResponse(status_code=204, content={"images": []})

    from services.pdf_service import images_dir
    img_dir = images_dir(kbId, fileId)
    if not img_dir.exists():
        return {"images": []}
        
    images = sorted([f.name for f in img_dir.glob("*.png")])
    return {"images": images}

# ---------------- PDF: 获取图片摘要 ----------------
@app.get(f"{API_PREFIX}/pdf/image_summaries", tags=["PDF"])
async def pdf_image_summaries(kbId: str = Query(...), fileId: str = Query(...)):
    summaries = get_image_summaries(kbId, fileId)
    return {"summaries": summaries}

class UpdateSummariesRequest(BaseModel):
    kbId: str
    fileId: str
    summaries: List[Dict[str, Any]]

# ---------------- KB: 全局图片管理 ----------------
@app.get(f"{API_PREFIX}/kb/images/all", tags=["KB"])
async def kb_images_all(kbId: str = Query(...)):
    """获取指定目录下所有文件的图片摘要列表"""
    kb_id = (kbId or "").strip()
    files_root = DATA_ROOT / kb_id / "files"
    if not files_root.exists():
        return {"images": []}
    
    all_images = []
    
    # 遍历所有文件目录
    for fdir in files_root.iterdir():
        if not fdir.is_dir(): continue
        file_id = fdir.name
        
        # 获取文件名 (尝试找主文件)
        file_name = file_id
        for pattern in ["*.pdf", "*.xlsx", "*.xls", "*.csv"]:
            matches = sorted(fdir.glob(pattern))
            if matches:
                 file_name = matches[0].name
                 break

        # 读取 image_summaries.json
        sum_path = fdir / "image_summaries.json"
        if sum_path.exists():
            try:
                data = json.loads(sum_path.read_text(encoding="utf-8"))
                for item in data:
                    all_images.append({
                        "kbId": kb_id,
                        "fileId": file_id,
                        "fileName": file_name,
                        "img_name": item.get("img_name"),
                        "summary": item.get("summary"),
                        "page_num": item.get("page_num")
                    })
            except:
                pass
                
    return {"images": all_images}

class UpdateSingleImageRequest(BaseModel):
    kbId: str
    fileId: str
    img_name: str
    summary: str

@app.post(f"{API_PREFIX}/kb/image/update", tags=["KB"])
async def kb_image_update(req: UpdateSingleImageRequest):
    """更新单张图片的描述并重建所属文件的索引"""
    from services.pdf_service import get_image_summaries, save_image_summaries
    
    # 1. 获取现有摘要列表
    current_summaries = get_image_summaries(req.kbId, req.fileId)
    if not current_summaries:
        return JSONResponse(err("NOT_FOUND", "该文件没有图片摘要记录"), status_code=404)
        
    # 2. 更新指定图片
    found = False
    for item in current_summaries:
        if item.get("img_name") == req.img_name:
            item["summary"] = req.summary
            found = True
            break
            
    if not found:
        return JSONResponse(err("IMG_NOT_FOUND", "未找到指定图片"), status_code=404)
        
    # 3. 保存回磁盘
    if not save_image_summaries(req.kbId, req.fileId, current_summaries):
        return JSONResponse(err("SAVE_FAILED", "保存失败"), status_code=500)
        
    # 4. 重建索引
    try:
        build_faiss_index(req.kbId, req.fileId)
    except Exception as e:
        return JSONResponse(err("INDEX_FAILED", f"索引重建失败: {e}"), status_code=500)
        
    return {"ok": True}

# ---------------- PDF: 更新图片摘要并重建索引 ----------------
@app.post(f"{API_PREFIX}/pdf/image_summaries/update", tags=["PDF"])
async def pdf_update_summaries(req: UpdateSummariesRequest):
    # 1. 保存新的摘要
    if not save_image_summaries(req.kbId, req.fileId, req.summaries):
        return JSONResponse(err("SAVE_FAILED", "保存摘要失败"), status_code=500)
    
    # 2. 重建索引
    try:
        build_faiss_index(req.kbId, req.fileId)
    except Exception as e:
        return JSONResponse(err("INDEX_FAILED", f"重建索引失败: {e}"), status_code=500)
        
    return {"ok": True}

# ---------------- PDF: 引用片段 ----------------
@app.get(f"{API_PREFIX}/pdf/chunk", tags=["PDF"])
async def pdf_chunk(citationId: str = Query(...)):
    ref = citations.get(citationId)
    if not ref:
        return JSONResponse(err("NOT_FOUND", "无该引用"), status_code=404)
    return ref

class BuildIndexRequest(BaseModel):
    kbId: str
    fileId: str

class SearchRequest(BaseModel):
    kbId: str
    fileId: Optional[str] = None
    query: str
    k: Optional[int] = 5

@app.post(f"{API_PREFIX}/index/build", tags=["Index"])
async def index_build(req: BuildIndexRequest):
    # 移除对 current_pdf 的强校验
    # if not current_pdf["fileId"] or current_pdf["fileId"] != req.fileId:
    #     raise HTTPException(status_code=400, detail="FILE_NOT_FOUND_OR_NOT_CURRENT")
    # if current_pdf["status"] != "ready":
    #     raise HTTPException(status_code=409, detail="NEED_PARSE_FIRST")
    
    # Check if it's an Excel file
    file_path = original_pdf_path(req.kbId, req.fileId)
    if file_path.suffix.lower() in ['.xlsx', '.xls', '.csv']:
        count = process_excel_file(str(file_path), req.kbId, req.fileId)
        return {"ok": True, "chunks": count}

    # 检查文件是否存在
    from services.pdf_service import markdown_output
    if not markdown_output(req.kbId, req.fileId).exists():
        raise HTTPException(status_code=404, detail="MARKDOWN_NOT_FOUND")

    out = build_faiss_index(req.kbId, req.fileId)
    if not out.get("ok"):
        return JSONResponse(err(out.get("error", "INDEX_BUILD_ERROR"), "索引构建失败"), status_code=500)
    return {"ok": True, "chunks": out["chunks"]}

@app.post(f"{API_PREFIX}/index/search", tags=["Index"])
async def index_search(req: SearchRequest):
    out = search_faiss(req.kbId, req.query, req.k or 5, file_id=req.fileId)
    if not out.get("ok"):
        code = out.get("error", "INDEX_NOT_FOUND")
        return JSONResponse(err(code, "请先构建索引"), status_code=400)
    return out

@app.post(f"{API_PREFIX}/index/delete", tags=["Index"])
async def index_delete(req: BuildIndexRequest):
    """删除指定文件的索引"""
    out = delete_index(req.kbId, file_id=req.fileId)
    if not out.get("ok"):
        return JSONResponse(err(out.get("error", "DELETE_ERROR"), "删除索引失败"), status_code=500)
    return out
# ---------------- Knowledge Extraction ----------------

@app.post(f"{API_PREFIX}/extraction/extract", tags=["Extraction"])
async def run_extraction(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    instruction: str = Form(""),
    kb_id: str = Form(""),
    output_format: str = Form("excel"),
    custom_filename: str = Form(""),
    parse_method: str = Form("original"),
    model_name: str = Form("qwen2.5:latest")
):
    try:
        # 1. Save file synchronously to ensure it's on disk
        file_bytes = await file.read()
        saved_path = save_uploaded_file_for_extraction(file_bytes, file.filename, kb_id)
        
        # 2. Create Job
        job_id = rid("ext")
        EXTRACTION_JOBS[job_id] = {
            "status": "pending", 
            "progress": 0,
            "filename": file.filename,
            "created_at": now_ts(),
            "parse_method": parse_method,
            "kb_id": kb_id,            # Stored for later updates
            "output_format": output_format, # Stored for later updates
            "model_name": model_name
        }

        # 3. Define Worker
        def _extraction_worker(jid: str, fpath: str, instr: str, kbid: str, fmt: str, cname: str, p_method: str, m_name: str):
            job = EXTRACTION_JOBS[jid]
            try:
                # -- Stage 1: Parsing --
                job["status"] = "parsing"
                job["progress"] = 10
                
                text = parse_file_content(fpath, method=p_method)
                if text.startswith("Error"):
                     raise Exception(text)
                
                # Save parsed content for inspection
                parsed_path = Path(fpath).parent / "parsed_input.md"
                with open(parsed_path, "w", encoding="utf-8") as f:
                    f.write(text)
                job["parsed_file_path"] = str(parsed_path)
                
                job["progress"] = 40

                # -- Stage 2: Locating --
                job["status"] = "locating"
                job["progress"] = 50
                relevant_text, relevant_pages = locate_relevant_content(text, instr)
                
                # Store locating result for inspection
                job["relevant_context"] = relevant_text
                job["relevant_pages"] = relevant_pages

                # -- Stage 3: Extraction --
                job["status"] = "extracting"
                job["progress"] = 60
                data = llm_extract(relevant_text, instr, model_name=m_name)
                job["progress"] = 90
                
                # -- Stage 4: Exporting --
                job["status"] = "exporting"
                start_ts = int(time.time())
                ext_map = {"excel": "xlsx", "csv": "csv", "json": "json"}
                ext = ext_map.get(fmt.lower(), "txt")
                
                if cname and cname.strip():
                    safe_custom_name = re.sub(r'[\\/:*?"<>|]', "", cname.strip())
                    if not safe_custom_name.lower().endswith(f".{ext}"):
                         safe_custom_name += f".{ext}"
                    out_filename = safe_custom_name
                else:
                    out_filename = f"extracted_{start_ts}.{ext}"

                out_dir = Path(fpath).parent 
                out_path = out_dir / out_filename
                
                export_data(data, fmt, str(out_path))

                # -- Stage 5: KB Integration --
                new_fid = _allocate_file_id(kbid, out_filename)
                kb_file_dir = workdir(kbid, new_fid)
                kb_file_dir.mkdir(parents=True, exist_ok=True)
                kb_file_path = kb_file_dir / out_filename
                
                import shutil
                shutil.copy2(out_path, kb_file_path)
                
                # Success
                job["status"] = "completed"
                job["progress"] = 100
                job["data"] = data
                job["result_filepath"] = str(out_path)
                job["kb_file_id"] = new_fid

            except Exception as e:
                import traceback
                traceback.print_exc()
                job["status"] = "failed"
                job["error"] = str(e)

        # 4. Launch Background Task
        background_tasks.add_task(
            _extraction_worker, 
            job_id, saved_path, instruction, kb_id, output_format, custom_filename, parse_method, model_name
        )

        return {"jobId": job_id, "status": "pending"}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content=err("START_FAIL", str(e)))

@app.get(f"{API_PREFIX}/extraction/status", tags=["Extraction"])
async def get_extraction_status(jobId: str = Query(...)):
    job = EXTRACTION_JOBS.get(jobId)
    if not job:
        return JSONResponse(status_code=404, content=err("JOB_NOT_FOUND", "任务不存在"))
    return job

@app.get(f"{API_PREFIX}/extraction/content", tags=["Extraction"])
async def get_extraction_content(jobId: str = Query(...)):
    """获取解析后的中间内容（Markdown/Text）用于检查"""
    job = EXTRACTION_JOBS.get(jobId)
    if not job:
         return JSONResponse(status_code=404, content=err("JOB_NOT_FOUND", "任务不存在"))
    
    parsed_path = job.get("parsed_file_path")
    if not parsed_path or not os.path.exists(parsed_path):
         return JSONResponse(status_code=404, content=err("CONTENT_NOT_READY", "解析内容尚未生成或不存在"))
         
    try:
        content = Path(parsed_path).read_text(encoding="utf-8")
        return {"jobId": jobId, "content": content}
    except Exception as e:
        return JSONResponse(status_code=500, content=err("READ_ERROR", str(e)))


class UpdateExtractionResultRequest(BaseModel):
    jobId: str
    data: List[Dict[str, Any]]

@app.post(f"{API_PREFIX}/extraction/update_result", tags=["Extraction"])
async def update_extraction_result(req: UpdateExtractionResultRequest):
    """
    Manually update the extracted data and overwrite the file in Knowledge Base.
    """
    job = EXTRACTION_JOBS.get(req.jobId)
    if not job:
        return JSONResponse(err("JOB_NOT_FOUND", "任务不存在或已过期"), status_code=404)
    
    # 1. Update in-memory job data
    job["data"] = req.data
    
    # 2. Key info validation
    kb_id = job.get("kb_id")
    kb_file_id = job.get("kb_file_id")
    output_format = job.get("output_format", "excel")
    
    if not kb_id or not kb_file_id:
        return JSONResponse(err("INVALID_JOB_STATE", "任务未完成或缺少KB信息"), status_code=400)

    # 3. Locate the KB file directory
    kb_file_dir = workdir(kb_id, kb_file_id)
    if not kb_file_dir.exists():
         return JSONResponse(err("KB_FILE_NOT_FOUND", "知识库文件目录不存在"), status_code=404)
    
    # Find the data file in the directory
    result_filepath = job.get("result_filepath")
    target_file = None
    
    if result_filepath:
        # Best case: we know the exact name
        target_file = kb_file_dir / os.path.basename(result_filepath)
    
    if not target_file or not target_file.exists():
        # Fallback: Search for likely candidates
        for f in kb_file_dir.iterdir():
            if f.suffix.lower() in ['.xlsx', '.csv'] and f.name.startswith("extracted_"):
                target_file = f
                break
    
    if not target_file:
         # Create a new name if necessary
         ext = "xlsx" if output_format.lower() == "excel" else "csv"
         target_file = kb_file_dir / f"extracted_updated.{ext}"

    # 4. Overwrite file
    try:
        export_data(req.data, output_format, str(target_file))
        
        # Also update the temp file if it exists, for 'download' button consistency
        if result_filepath and os.path.exists(result_filepath):
             export_data(req.data, output_format, result_filepath)
             
        return {"ok": True, "updated_path": str(target_file)}
    except Exception as e:
        return JSONResponse(err("UPDATE_FAILED", str(e)), status_code=500)

@app.get(f"{API_PREFIX}/extraction/image", tags=["Extraction"])
async def get_extraction_image_preview(jobId: str = Query(...), page: int = Query(...)):
    """
    Get the preview image of a specific page for an extraction job.
    Only available if parsing method was 'olmocr' (images cached) or can be rendered on fly for 'original'.
    """
    job = EXTRACTION_JOBS.get(jobId)
    if not job:
        return JSONResponse(err("JOB_NOT_FOUND", "任务不存在"), status_code=404)
    
    parsed_path = job.get("parsed_file_path")
    if not parsed_path:
        return JSONResponse(err("NOT_READY", "解析尚未完成"), status_code=404)
    
    # Task base directory
    task_dir = Path(parsed_path).parent
    
    # Case 1: Look for Cached Image (olmocr)
    # Stored in task_dir/pages/page_{i}.png
    img_path_cached = task_dir / "pages" / f"page_{page}.png"
    if img_path_cached.exists():
        return FileResponse(img_path_cached, media_type="image/png")
    
    # Case 2: Render on fly (original)
    filename = job.get("filename")
    # Need to find the source file in task_dir. It was saved as sanitize_filename(filename)
    # But wait, save_uploaded_file_for_extraction returns the full path.
    # But job info might not have the full path easily? 
    # Actually _extraction_worker has 'fpath' (saved_path).
    # 'parsed_file_path' is alongside it. 
    # Let's search for the PDF in the task_dir.
    
    try:
        source_pdf = None
        for f in task_dir.glob("*.pdf"):
            source_pdf = f
            break
        
        if source_pdf and source_pdf.exists():
            import fitz
            doc = fitz.open(source_pdf)
            # page arg is 1-based, fitz is 0-based
            if 0 < page <= len(doc):
                pix = doc[page-1].get_pixmap(matrix=fitz.Matrix(2, 2)) # 2x zoom for preview
                img_bytes = pix.tobytes("png")
                return StreamingResponse(
                    io.BytesIO(img_bytes), 
                    media_type="image/png"
                )
    except Exception as e:
        logger.error(f"Render preview failed: {e}")
        
    return JSONResponse(err("IMG_NOT_FOUND", "无法生成预览图"), status_code=404)

