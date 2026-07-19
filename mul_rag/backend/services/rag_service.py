# services/rag_service.py
from __future__ import annotations
import os, asyncio, textwrap
from typing import List, Dict, Any, Tuple, AsyncGenerator
from typing_extensions import TypedDict

from dotenv import load_dotenv
load_dotenv(override=True)

from langchain_ollama import ChatOllama, OllamaEmbeddings
# from langchain_community.vectorstores import FAISS
import sqlite3
import json
from services.vector_store import get_vector_store
from services.pdf_service import read_kb_metadata
from collections import defaultdict
from pathlib import Path
try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None
try:
    from rank_bm25 import BM25Okapi
    import jieba
except ImportError:
    BM25Okapi = None
    jieba = None

# 存储结构：sessions[session_id] = [{"role":"user|assistant","content":"..."}...]
_sessions: dict[str, list[dict]] = defaultdict(list)

def get_history(session_id: str) -> list[dict]:
    return _sessions.get(session_id, [])

def append_history(session_id: str, role: str, content: str) -> None:
    _sessions[session_id].append({"role": role, "content": content})

def clear_history(session_id: str) -> None:
    _sessions.pop(session_id, None)

# ---------------- 配置 ----------------
MODEL_NAME = "qwen2.5:latest"
TEMPERATURE = 0

EMBED_MODEL = "nomic-embed-text:latest"  # default; may be overridden per KB by meta.json
RERANK_MODEL_NAME = "BAAI/bge-reranker-v2-m3" # 或者是 "BAAI/bge-reranker-base"
K = 10 # 增加召回数量，避免因切片过细导致信息不全
# FAISS Cosine Similarity: 越接近1越相似
SCORE_TAU_TOP1 = 0.75
SCORE_TAU_MEAN3 = 0.70

_reranker_model = None
def _get_reranker():
    global _reranker_model
    if _reranker_model is None and CrossEncoder is not None:
        print(f"Loading Reranker model: {RERANK_MODEL_NAME} ...")
        try:
            _reranker_model = CrossEncoder(RERANK_MODEL_NAME, max_length=512)
            print("Reranker loaded.")
        except Exception as e:
            print(f"Failed to load Reranker: {e}")
    return _reranker_model

# ---------------- BM25 (Hybrid Search) ----------------
# 每个知识库一个 BM25 缓存，避免不同 KB 相互污染
_bm25_cache: dict[str, dict] = {}

def _tokenize_zh(text: str) -> List[str]:
    if jieba:
        return jieba.lcut(text)
    return text.split()

def _db_file_for_kb(kb_id: str) -> Path:
    return Path("data") / kb_id / "metadata.db"


def _ensure_bm25(kb_id: str):
    if BM25Okapi is None:
        # 允许系统在未安装时退化为纯向量检索
        return

    db_file = _db_file_for_kb(kb_id)
    if not db_file.exists():
        return

    mtime = db_file.stat().st_mtime
    cached = _bm25_cache.get(kb_id)
    if cached and cached.get("mtime") == mtime and cached.get("model") is not None:
        return

    print(f"Building BM25 index for kb={kb_id} ...")
    try:
        conn = _get_db_connection(kb_id)
        cur = conn.cursor()
        cur.execute("SELECT id, entity_key, source, chunk_text FROM embeddings")
        rows = cur.fetchall()
        conn.close()

        if not rows:
            _bm25_cache[kb_id] = {"model": None, "data": [], "mtime": mtime}
            return

        bm25_data = []
        corpus = []
        for r in rows:
            bm25_data.append(
                {
                    "id": r[0],
                    "entity_key": r[1],
                    "source": r[2],
                    "chunk_text": r[3],
                }
            )
            # 优先使用 embedding_text (纯摘要) 进行分词
            text_to_tokenize = r[3]
            try:
                # source 可能是 JSON(来自 PDF/Markdown) 或普通字符串(来自 Excel)
                meta = json.loads(r[2]) if isinstance(r[2], str) else None
                if isinstance(meta, dict) and "embedding_text" in meta:
                    text_to_tokenize = meta["embedding_text"]
            except Exception:
                pass
            corpus.append(_tokenize_zh(text_to_tokenize))

        model = BM25Okapi(corpus)
        _bm25_cache[kb_id] = {"model": model, "data": bm25_data, "mtime": mtime}
        print(f"BM25 index built with {len(corpus)} documents.")
    except Exception as e:
        print(f"Failed to build BM25: {e}")
        _bm25_cache[kb_id] = {"model": None, "data": [], "mtime": mtime}

def _bm25_search(query: str, top_k: int = 10, kb_id: str = None, file_id: str = None) -> List[Dict]:
    if not kb_id:
        return []
    _ensure_bm25(kb_id)
    cached = _bm25_cache.get(kb_id) or {}
    bm25_model = cached.get("model")
    bm25_data = cached.get("data") or []
    if bm25_model is None:
        return []
    
    tokenized_query = _tokenize_zh(query)
    # 获取所有文档的分数
    scores = bm25_model.get_scores(tokenized_query)
    
    results = []
    # 遍历所有文档分数 (性能瓶颈点，数据量大时需优化)
    for i, score in enumerate(scores):
        if score > 0:
            item = bm25_data[i]
            # 过滤 file_id
            if file_id and item['entity_key'] != file_id:
                continue
                
            results.append({
                'id': item['id'],
                'score': float(score), # BM25 score
                'entity_key': item['entity_key'],
                'source': item['source'],
                'chunk_text': item['chunk_text']
            })
    
    # 按分数降序
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]

def _rrf_fusion(list1: List[Dict], list2: List[Dict], k=60) -> List[Dict]:
    """Reciprocal Rank Fusion"""
    scores = defaultdict(float)
    items = {}
    
    # 处理列表1 (Vector)
    for rank, item in enumerate(list1):
        doc_id = item['id']
        scores[doc_id] += 1 / (k + rank + 1)
        items[doc_id] = item
        
    # 处理列表2 (BM25)
    for rank, item in enumerate(list2):
        doc_id = item['id']
        scores[doc_id] += 1 / (k + rank + 1)
        if doc_id not in items:
            items[doc_id] = item
            
    # 按 RRF 分数排序
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    
    fused_results = []
    for doc_id in sorted_ids:
        item = items[doc_id]
        # 注意：这里的分数变成了 RRF 分数，不再是余弦相似度或 BM25 分数
        item['score'] = scores[doc_id] 
        fused_results.append(item)
        
    return fused_results



SYSTEM_INSTRUCTION = (
    "你是多模态 PDF 检索 RAG 聊天机器人，可以围绕多模态文档进行解析、检索和问答。\n"
    "请优先使用当前上传并已解析/索引的课程资料来回答问题；若未检索到相关内容，则基于通识知识作答，"
    "并**明确说明未找到匹配的课程片段**。\n"
    "当检索到的上下文中包含与答案直接相关的图片时，请在回答中一并给出这些图片的 Markdown 引用，"
    "例如：`![参考图1](图片URL)`。如果没有合适的图片，也就是如果没有检索到图片，或者用户只是让你介绍自己的功能，勿强行添加图片路径。绝不伪造图片或路径。"
)

GRADE_PROMPT = (
    "你是一个判定器，评估检索到的上下文是否有助于回答用户问题。\n"
    "上下文片段：\n{context}\n\n问题：{question}\n"
    "如果上下文对回答该问题有帮助，返回 'yes'；否则返回 'no'。"
)

ANSWER_WITH_CONTEXT = (
    "请使用提供的上下文回答用户的问题。\n\n"
    "问题：\n{question}\n\n上下文：\n{context}\n\n"
    "要求：使用 Markdown；表达简洁但完整；如需给出代码，请使用三引号代码块（```）。\n"
    "若上下文包含与答案直接相关的图片，请在相关段落后内联给出 1–3 张图片（Markdown 语法），"
    "作为一名助人为乐的助手，你需要仔细详细的感受用户的需求，并作出详细的回答。如果有图片，请在回答中给出图片的Markdown引用。"
)

ANSWER_NO_CONTEXT = (
    "当前未找到与课程资料直接相关的片段，将基于通识知识作答。\n"
    "问题：\n{question}"
)


# ---------------- 模型/向量函数 ----------------
def _get_llm():
    return ChatOllama(model=MODEL_NAME, temperature=TEMPERATURE, base_url="http://127.0.0.1:11434")

def _get_grader():
    return ChatOllama(model=MODEL_NAME, temperature=0, base_url="http://127.0.0.1:11434")

def _get_embeddings(kb_id: str | None = None):
    model = EMBED_MODEL
    if kb_id:
        try:
            meta = read_kb_metadata(kb_id)
            configured = (meta or {}).get("embedModel")
            if isinstance(configured, str) and configured.strip():
                model = configured.strip()
        except Exception:
            pass
    return OllamaEmbeddings(model=model, base_url="http://127.0.0.1:11436")

def _get_db_connection(kb_id: str):
    # 使用知识库内的数据库
    db_file = os.path.join("data", kb_id, "metadata.db")
    return sqlite3.connect(db_file)

def _load_store(kb_id: str):
    conn = _get_db_connection(kb_id)
    store = get_vector_store(kb_id, conn)
    if not store.load():
        conn.close()
        raise FileNotFoundError("Vector index not found; build index first.")
    return store, conn

def _score_ok(scores: List[float]) -> bool:
    if not scores:
        return False
    top1 = scores[0]
    mean3 = sum(scores[:3]) / min(3, len(scores))
    # Cosine Similarity: higher is better
    return (top1 >= SCORE_TAU_TOP1) or (mean3 >= SCORE_TAU_MEAN3)

# ---------------- 主流程：检索 + 判定 + 生成 ----------------
async def retrieve(question: str, kb_id: str, file_id: str | None = None) -> tuple[list[dict], str]:
    """
    返回 (citations, context_text)
    citations: [{citation_id, fileId, rank, page, snippet, score, previewUrl}]
    context_text: 供 LLM 使用的拼接上下文
    """
    if not kb_id:
        return [], ""
        
    try:
        store, conn = _load_store(kb_id)
    except Exception as e:
        print(f"[retrieve] Error loading store: {e}")
        return [], ""

    try:
        embed_model = _get_embeddings(kb_id)
        query_vector = embed_model.embed_query(question)
        
        initial_k = K * 5
        entity_keys = [file_id] if file_id else None

        # If the selected vector store supports unified hybrid search (e.g., Elasticsearch),
        # use it; otherwise fall back to (Vector + local BM25) with RRF fusion.
        fused_results = None
        if hasattr(store, "hybrid_search"):
            try:
                fused_results = store.hybrid_search(
                    query_text=question,
                    query_vector=query_vector,
                    top_k=initial_k,
                    entity_keys=entity_keys,
                )
            except Exception as e:
                print(f"[retrieve] ES hybrid_search failed, fallback to local BM25: {e}")
                fused_results = None

        if fused_results is None:
            # 1. 向量检索 (Vector Search) Top-N（单库内全局；可选按 file_id 过滤）
            vector_results = store.search(query_vector, top_k=initial_k, entity_keys=entity_keys)

            # 2. 关键词检索 (BM25 Search) Top-N
            bm25_results = _bm25_search(question, top_k=initial_k, kb_id=kb_id, file_id=file_id)

            # 3. 混合检索融合 (RRF Fusion)
            fused_results = _rrf_fusion(vector_results, bm25_results)
        
        # 4. 精排 (Reranker)
        results = fused_results  # 默认使用融合结果
        
        reranker = _get_reranker()
        if reranker and results:
            try:
                import math
                rerank_inputs = []
                # 只对 Top-N 进行重排序，避免计算量过大
                rerank_candidates = results[:initial_k] 
                
                for res in rerank_candidates:
                    try:
                        meta = json.loads(res['source'])
                        # 优先使用专门的 embedding_text (纯摘要)，否则用 chunk_text
                        doc_text = meta.get('embedding_text') or res['chunk_text']
                    except:
                        doc_text = res['chunk_text']
                    rerank_inputs.append([question, doc_text])
                
                # 预测分数 (Logits)
                rerank_scores = reranker.predict(rerank_inputs)
                
                # 更新分数
                for i, res in enumerate(rerank_candidates):
                    logit = float(rerank_scores[i])
                    res['score'] = logit # 暂存 logit 用于排序
                
                # 排序
                rerank_candidates.sort(key=lambda x: x['score'], reverse=True)
                
                # 截取 Top-K
                results = rerank_candidates[:K]
                
                # 将 Logit 归一化到 0-1 (Sigmoid)，以便兼容后续阈值判定
                for res in results:
                    res['score'] = 1 / (1 + math.exp(-res['score']))
                    
            except Exception as e:
                print(f"Reranking failed: {e}")
                results = results[:K]
        else:
            results = results[:K]
        
        citations = []
        ctx_snippets = []
        scores = []
        
        for i, res in enumerate(results, start=1):
            snippet = res['chunk_text']
            score = res['score']
            
            try:
                meta = json.loads(res['source'])
            except:
                meta = {}
            
            # 从 metadata 中获取真实的 file_id (来源文件)
            real_file_id = meta.get("file_id") or file_id
            
            snippet_short = snippet.strip()
            if len(snippet_short) > 500:
                snippet_short = snippet_short[:500] + "..."
            
            page = meta.get("page") or meta.get("page_number") or 1
            
            preview_url = f"/api/v1/pdf/page?fileId={real_file_id}&page={page}&type=original"
            
            # 如果是图片类型的引用，修改 previewUrl 并处理 snippet 中的图片路径
            if meta.get("type") == "image" and meta.get("image_path"):
                img_name = meta["image_path"]
                preview_url = f"/api/v1/pdf/images?fileId={real_file_id}&imagePath={img_name}"
                # 替换 snippet 中的相对路径为绝对 API 路径，以便 LLM 输出正确的 Markdown
                # 这里使用相对路径 /api/v1/...，由前端负责拼接完整的 API Base URL
                full_img_url = f"/api/v1/pdf/images?fileId={real_file_id}&imagePath={img_name}"
                snippet = snippet.replace(f"./images/{img_name}", full_img_url)
                snippet_short = f"[Image: {img_name}] " + snippet_short

            citations.append({
                "citation_id": f"{real_file_id}-c{i}", # 使用真实的 file_id
                "fileId": real_file_id,
                "rank": i,
                "page": page,
                "snippet": snippet[:4000],
                "score": float(score),
                "previewUrl": preview_url,
            })
            ctx_snippets.append(f"[{i}] {snippet}") # 使用处理过的 snippet (包含完整URL)
            scores.append(float(score))
            
        context_text = "\n\n".join(ctx_snippets) if ctx_snippets else "(no hits)"

        # 规则 + LLM 复核
        ok_by_score = _score_ok(scores)
        if not ok_by_score:
            grader = _get_grader()
            grade_prompt = GRADE_PROMPT.format(context=context_text, question=question)
            decision = await grader.ainvoke([{"role": "user", "content": grade_prompt}])
            ok_by_llm = "yes" in (decision.content or "").lower()
        else:
            ok_by_llm = True

        branch = "with_context" if ok_by_llm else "no_context"
        return citations, context_text if branch == "with_context" else ""
        
    finally:
        conn.close()

async def answer_stream(
    question: str,
    citations: list[dict],
    context_text: str,
    branch: str,
    session_id: str | None = None
) -> AsyncGenerator[dict, None]:
    """
    以增量事件的形式产出：
      {"type":"citation", "data": {...}}
      {"type":"token", "data": "text chunk"}
      {"type":"done", "data": {"used_retrieval": bool}}
    同时：如果提供了 session_id，会把本轮问答写入内存历史。
    """
    # 先把 citations 全部发给前端（便于角标立刻出现）
    if branch == "with_context" and citations:
        for c in citations:
            yield {"type": "citation", "data": c}

    # 组装“历史 + 本轮提示”
    llm = _get_llm()
    history_msgs = get_history(session_id) if session_id else []

    if branch == "with_context" and context_text:
        user_prompt = ANSWER_WITH_CONTEXT.format(question=question, context=context_text)
    else:
        user_prompt = ANSWER_NO_CONTEXT.format(question=question)

    # 完整消息序列：system + 历史多轮 + 当前用户
    msgs = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    # 将历史逐条附加（保持 role: "user"/"assistant"）
    msgs.extend(history_msgs)
    # 当前用户问题
    msgs.append({"role": "user", "content": user_prompt})

    # 把最终生成的文本拼接出来用于写历史
    final_text_parts: list[str] = []

    # 优先使用流式
    try:
        async for chunk in llm.astream(msgs):
            delta = getattr(chunk, "content", None)
            if delta:
                final_text_parts.append(delta)
                yield {"type": "token", "data": delta}
    except Exception:
        # 回退：非流式整段生成
        resp = await llm.ainvoke(msgs)
        text = resp.content or ""
        final_text_parts.append(text)
        for i in range(0, len(text), 20):
            yield {"type": "token", "data": text[i:i+20]}
            await asyncio.sleep(0.005)

    # if branch == "with_context" and citations:
    #     imgs = []
    #     # 取前 2 张，避免过多（可按需改成 3）
    #     for c in citations[:2]:
    #         url = c.get("previewUrl")
    #         if url:
    #             # 生成 Markdown 图片行
    #             imgs.append(f"![参考页 {c.get('rank', '')}]({url})")
    #     if imgs:
    #         tail = "\n\n---\n**相关页面预览**\n\n" + "\n\n".join(imgs)
    #         # 作为一个额外 token 块发给前端
    #         yield {"type": "token", "data": tail}

    # 将本轮问答写入历史（仅在提供 session_id 时）
    if session_id:
        append_history(session_id, "user", question)
        append_history(session_id, "assistant", "".join(final_text_parts))

    yield {"type": "done", "data": {"used_retrieval": branch == "with_context"}}
