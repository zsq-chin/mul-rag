# services/index_service.py
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional
import os
from langchain_ollama import OllamaEmbeddings
from langchain_text_splitters import MarkdownHeaderTextSplitter
from langchain_core.documents import Document
#from langchain.docstore.document import Document
# from langchain_community.vectorstores import FAISS

from dotenv import load_dotenv
load_dotenv(override=True)

# 使用绝对路径，确保在任何CWD下都能找到正确的数据目录
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_ROOT = BACKEND_DIR / "data"


def kb_dir(kb_id: str) -> Path:
    p = DATA_ROOT / kb_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def file_workdir(kb_id: str, file_id: str) -> Path:
    p = kb_dir(kb_id) / "files" / file_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def markdown_path(kb_id: str, file_id: str) -> Path:
    return file_workdir(kb_id, file_id) / "output.md"


def index_dir(kb_id: str) -> Path:
    """返回某个知识库的索引目录。"""
    p = kb_dir(kb_id) / "index"
    p.mkdir(parents=True, exist_ok=True)
    return p

from services.pdf_service import read_kb_metadata


def load_embeddings(kb_id: Optional[str] = None) -> OllamaEmbeddings:
    model = "nomic-embed-text:latest"
    if kb_id:
        try:
            meta = read_kb_metadata(kb_id)
            configured = (meta or {}).get("embedModel")
            if isinstance(configured, str) and configured.strip():
                model = configured.strip()
        except Exception:
            pass
            
    # ★★★ 修改重点：读取环境变量，如果读不到再用默认值 ★★★
    # 这样 Docker 里的 OLLAMA_BASE_URL=http://host.docker.internal:11434 才会生效
    base_url = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    
    return OllamaEmbeddings(model=model, base_url=base_url)

import re

def split_markdown(md_text: str) -> List[Document]:
    # 预处理：修复标题层级
    # 1. 处理 olmocr 可能产生的无 # 号标题
    # 2. 处理 Unstructured 可能产生的 # 号层级不准问题
    
    lines = md_text.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        
        # 情况A: 已经是 Markdown 标题，调整层级
        if stripped.startswith('#'):
            # 匹配 "# 1.1.1" -> "### 1.1.1"
            if re.match(r'^#\s+\d+\.\d+\.\d+', stripped):
                line = re.sub(r'^#\s+', '### ', line)
            # 匹配 "# 1.1" -> "## 1.1"
            elif re.match(r'^#\s+\d+\.\d+', stripped):
                line = re.sub(r'^#\s+', '## ', line)
                
        # 情况B: 纯文本标题 (针对 olmocr)
        else:
            # 匹配 "1、基本信息" -> "# 1、基本信息"
            if re.match(r'^\d+、', stripped):
                line = '# ' + stripped
            # 匹配 "1.1 井号" -> "## 1.1 井号"
            elif re.match(r'^\d+\.\d+\s+', stripped):
                line = '## ' + stripped
            # 匹配 "1.1.1 邻井..." -> "### 1.1.1 邻井..."
            elif re.match(r'^\d+\.\d+\.\d+\s+', stripped):
                line = '### ' + stripped
                
        new_lines.append(line)
    
    md_text = '\n'.join(new_lines)

    headers_to_split_on = [
        ("#", "Header 1"),
        ("##", "Header 2"),
        ("###", "Header 3"),
    ]
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    docs = splitter.split_text(md_text)
    # 可加一点清洗
    cleaned: List[Document] = []
    for d in docs:
        txt = (d.page_content or "").strip()
        if not txt:
            continue
        # 限制太长的段落，避免向量化出错
        if len(txt) > 8000:
            txt = txt[:8000]
        cleaned.append(Document(page_content=txt, metadata=d.metadata))
    return cleaned

import sqlite3
import json
from services.vector_store import get_vector_store

def init_db(kb_id: str, clear: bool = False) -> sqlite3.Connection:
    """返回某个知识库的元数据 SQLite 连接。"""
    if not kb_id:
        raise ValueError("kb_id is required for per-KB metadata db")
    
    p = kb_dir(kb_id) / "metadata.db"
    if clear and p.exists():
        try:
            p.unlink()
        except Exception:
            pass

    print(f"[init_db] Connecting to DB: {p}")
    conn = sqlite3.connect(str(p))
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_key TEXT,
            source TEXT,
            chunk_text TEXT
        )
    ''')
    conn.commit()
    return conn

def build_faiss_index(kb_id: str, file_id: str) -> Dict[str, Any]:
    md_file = markdown_path(kb_id, file_id)
    if not md_file.exists():
        print(f"[build_faiss_index] Markdown not found: {md_file}")
        return {"ok": False, "error": "MARKDOWN_NOT_FOUND"}
    md_text = md_file.read_text(encoding="utf-8")

    docs = split_markdown(md_text)
    # 为普通文本添加 embedding_text (默认为原文)
    # 改进：将标题上下文回填到 page_content，确保 Embedding 和 LLM 都能看到标题信息
    for d in docs:
        headers = []
        if "Header 1" in d.metadata:
            headers.append(f"# {d.metadata['Header 1']}")
        if "Header 2" in d.metadata:
            headers.append(f"## {d.metadata['Header 2']}")
        if "Header 3" in d.metadata:
            headers.append(f"### {d.metadata['Header 3']}")
            
        if headers:
            header_text = "\n".join(headers)
            # 将标题拼接到内容前面
            d.page_content = f"{header_text}\n{d.page_content}"

        d.metadata["embedding_text"] = d.page_content
    
    # 加载图片摘要
    img_summary_path = file_workdir(kb_id, file_id) / "image_summaries.json"
    if img_summary_path.exists():
        try:
            img_data = json.loads(img_summary_path.read_text(encoding="utf-8"))
            for item in img_data:
                # 改进：分离嵌入内容和展示内容 (Multi-Vector 思想)
                
                # 1. Embedding Content (X): 仅使用摘要文本进行向量化，去除 Markdown 图片语法的噪声
                embedding_text = item.get('summary', '')
                if not embedding_text or not str(embedding_text).strip():
                    continue
                
                # 2. Display Content (Y): 检索后返回的内容，包含图片 Markdown 和摘要，供 LLM 上下文使用
                display_text = (
                    f"![Image](./images/{item.get('img_name', '')})\n"
                    f"Image Description: {item.get('summary', '')}"
                )
                
                meta = {
                    "source": f"Page {item.get('page_num', '?')} (Image)",
                    "page": item.get('page_num', 1),
                    "type": "image",
                    "image_path": item.get('img_name', ''),
                    "embedding_text": embedding_text # 存储用于嵌入的文本
                }
                meta.update({"Header 1": "Images", "Header 2": f"Image on Page {item.get('page_num', '?')}"})
                docs.append(Document(page_content=display_text, metadata=meta))
        except Exception as e:
            print(f"Error loading image summaries: {e}")

    # 再次过滤，确保没有空文本进入 embedding，避免 Ollama 500 错误
    valid_docs = []
    for d in docs:
        txt = d.metadata.get("embedding_text", d.page_content)
        if txt and str(txt).strip():
            valid_docs.append(d)
    docs = valid_docs

    if not docs:
        return {"ok": False, "error": "EMPTY_MD"}

    # Embed
    embeddings_model = load_embeddings(kb_id)
    # 使用专门的 embedding_text 进行向量化，而不是 page_content
    texts_to_embed = [d.metadata.get("embedding_text", d.page_content) for d in docs]
    
    vectors = []
    # 修改：分批处理以避免 Ollama CUDA OOM (显存溢出)
    # 再次降低：将 batch_size 设为 1，这是最慢但最稳妥的方式，防止显存不足
    batch_size = 1
    total_docs = len(texts_to_embed)
    print(f"[Embed] Starting embedding for {total_docs} chunks, batch_size={batch_size}")

    import time
    
    try:
        for i in range(0, total_docs, batch_size):
            batch = texts_to_embed[i : i + batch_size]
            try:
                # 这里的 embed_documents 会向 Ollama 发起 HTTP 请求
                batch_vectors = embeddings_model.embed_documents(batch)
                vectors.extend(batch_vectors)
            except Exception as e:
                print(f"[Embed Warning] Batch {i//batch_size} failed: {e}. Retrying in 2s...")
                time.sleep(2)
                # 简单的重试一次
                batch_vectors = embeddings_model.embed_documents(batch)
                vectors.extend(batch_vectors)

            # 每处理几个就打印一下，每处理一个稍微停顿一下让显存喘口气（可选）
            # time.sleep(0.1) 
            
            if (i + 1) % 10 == 0:
                 print(f"[Embed] Progress: {min(i + batch_size, total_docs)}/{total_docs}")

    except Exception as e:
        print(f"[Embed Error] Failed to embed documents: {e}")
        # 如果是因为模型不存在 (通常也是 500 或 404)，提示用户
        print(f"Check if model exists in Ollama. Try running: ollama pull nomic-embed-text")
        raise e
    
    # Prepare metadata
    metas = []
    for d in docs:
        # 注入 file_id 到 metadata 中，以便区分来源
        d.metadata["file_id"] = file_id
        meta_json = json.dumps(d.metadata, ensure_ascii=False)
        # entity_key 用于在同一 KB 内区分不同文件
        metas.append((file_id, meta_json, d.page_content))
    
    # Initialize DB and VectorStore (Per-KB)
    conn = init_db(kb_id)
    store = get_vector_store(kb_id, conn)
    # best-effort load existing index/collection
    try:
        store.load()
    except Exception:
        pass
    store.add_embeddings(vectors, metas)
    
    return {"ok": True, "chunks": len(docs)}

def search_faiss(kb_id: str, query: str, k: int = 3, file_id: Optional[str] = None):
    # 搜索指定知识库；可选按 file_id 过滤
    p_db = kb_dir(kb_id) / "metadata.db"
    if not p_db.exists():
        print(f"[search_faiss] DB not found at: {p_db.absolute()} (kb_id='{kb_id}')")
        # 尝试列出该知识库目录下的文件，排查是否文件名有异
        if p_db.parent.exists():
            print(f"[search_faiss] Dir content: {list(p_db.parent.iterdir())}")
        else:
            print(f"[search_faiss] Parent dir does not exist: {p_db.parent}")
        return {"ok": False, "error": "DB_NOT_FOUND"}

    conn = sqlite3.connect(str(p_db))
    store = get_vector_store(kb_id, conn)
    if not store.load():
        print(f"[search_faiss] Index load failed. Path: {store.index_path if hasattr(store, 'index_path') else 'Unknown'}")
        return {"ok": False, "error": "INDEX_NOT_FOUND"}
    
    embeddings_model = load_embeddings(kb_id)
    query_vector = embeddings_model.embed_query(query)
    
    entity_keys = [file_id] if file_id else None
    results = store.search(query_vector, top_k=k, entity_keys=entity_keys)
    return {"ok": True, "results": results}

def delete_index(kb_id: str, file_id: Optional[str] = None) -> Dict[str, Any]:
    """删除知识库索引：不传 file_id 则清空整个 KB；传 file_id 则仅删除该文件。"""
    p_db = kb_dir(kb_id) / "metadata.db"
    if not p_db.exists():
        # 如果 DB 不存在，说明根本没有索引，视为删除成功（幂等）
        return {"ok": True, "removed": 0}

    conn = sqlite3.connect(str(p_db))
    store = get_vector_store(kb_id, conn)
    if not store.load():
        # 索引未加载（可能是新空索引），视为成功
        return {"ok": True, "removed": 0}
    
    # 1. 从 SQLite 查找要删除的向量 ID
    cur = conn.cursor()
    if file_id:
        cur.execute("SELECT id FROM embeddings WHERE entity_key=?", (file_id,))
    else:
        cur.execute("SELECT id FROM embeddings")
    rows = cur.fetchall()
    ids_to_remove = [r[0] for r in rows]
    
    if not ids_to_remove:
        return {"ok": True, "removed": 0, "message": "No index found for this file"}
        
    # 2. 从 FAISS 移除
    removed_count = store.remove_by_ids(ids_to_remove)
    
    # 3. 从 SQLite 移除
    if file_id:
        cur.execute("DELETE FROM embeddings WHERE entity_key=?", (file_id,))
    else:
        cur.execute("DELETE FROM embeddings")
    conn.commit()
    conn.close()
    
    return {"ok": True, "removed": removed_count}

def is_indexed(kb_id: str, file_id: Optional[str] = None) -> bool:
    """检查指定知识库/文件是否已构建索引"""
    p_db = kb_dir(kb_id) / "metadata.db"
    if not p_db.exists():
        return False
        
    try:
        conn = sqlite3.connect(str(p_db))
        cur = conn.cursor()
        # 检查是否存在任意记录 / 指定文件记录
        if file_id:
            cur.execute("SELECT 1 FROM embeddings WHERE entity_key=? LIMIT 1", (file_id,))
        else:
            cur.execute("SELECT 1 FROM embeddings LIMIT 1")
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False
