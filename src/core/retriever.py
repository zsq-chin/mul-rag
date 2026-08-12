import json
import re
import hashlib
import threading
import time
import traceback

from src import config, knowledge_base, graph_base
from src.models.rerank_model import get_reranker
from src.utils.logging_config import logger
from src.models import select_model
from src.core.operators import HyDEOperator
from server.utils.multimodal_remote import format_multimodal_context, search_multimodal_remote
from src.core.graph_retrieval import (
    normalize_entities,
    rank_unique_relations,
    format_graph_context,
    MAX_ENTITIES,
    MAX_HOPS,
    MAX_RELATIONS,
)


def _coerce_int(value, default, minimum, maximum):
    """Coerce a value to an integer clamped to [minimum, maximum].

    Returns *default* when the value is not a valid integer or falls below
    *minimum*.  Values above *maximum* are clamped.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if v < minimum:
        return default
    return min(v, maximum)


def _coerce_float(value, default, minimum, maximum):
    """Coerce a value to a float clamped to [minimum, maximum].

    Returns *default* when the value is not a valid float, is NaN, or is
    infinite.  Values below *minimum* return *default*; values above
    *maximum* are clamped.
    """
    try:
        v = float(value)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
    except (TypeError, ValueError):
        return default
    if v < minimum:
        return default
    return min(v, maximum)


class _MMSearchCache:
    """多模态检索结果的短 TTL 缓存（C2.2）。

    缓存键必须包含用户可见权限、`kbId`、`fileId` 与检索参数；命中缓存不
    消耗远端预算、不发网络请求。多线程（ThreadPoolExecutor 并发会话）下用
    锁保护。条目短 TTL 自动过期，避免跨会话/跨权限串数据。
    """

    _lock = threading.Lock()
    _entries: dict = {}

    @classmethod
    def key_for(cls, meta, query, **params):
        permission = str(meta.get("user_id") or meta.get("permission") or "")
        kb = str(meta.get("multimodal_kb_id") or meta.get("multimodal_kb_name") or "")
        file = str(meta.get("multimodal_file_id") or meta.get("file_id") or "")
        identity = "|".join(
            [permission, kb, file, str(query or "").strip()]
            + [f"{k}={v}" for k, v in sorted(params.items())]
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @classmethod
    def get(cls, key, now=None):
        now = time.monotonic() if now is None else now
        with cls._lock:
            entry = cls._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at < now:
                cls._entries.pop(key, None)
                return None
            return payload

    @classmethod
    def put(cls, key, payload, ttl, now=None):
        now = time.monotonic() if now is None else now
        with cls._lock:
            cls._entries[key] = (now + ttl, payload)

    @classmethod
    def clear(cls):
        with cls._lock:
            cls._entries.clear()


def _retrieval_cancel_signal(meta):
    """从 meta 提取用户取消信号：`threading.Event` 或无参可调用；未提供返回 None。

    路由层在客户端断开/用户取消时可通过该 seam 置位取消信号，使检索器停止
    继续发起远端调用（C2.5「用户取消」状态）。
    """
    if not isinstance(meta, dict):
        return None
    return meta.get("_retrieval_cancelled")


def _signal_is_set(signal) -> bool:
    try:
        if callable(signal):
            return bool(signal())
        return bool(signal.is_set())
    except Exception:
        return False


class Retriever:

    def __init__(self):
        self._load_models()

    def _load_models(self):

        if config.enable_web_search:
            # from src.utils.web_search import WebSearcher
            from src.utils.web_search_bocha import WebSearcher
            self.web_searcher = WebSearcher()

    def retrieval(self, query, history, meta, progress_cb=None):
        refs = {"query": query, "history": history, "meta": meta}
        refs["model_name"] = config.model_name

        # 多轮检索：由模型多次生成检索子问题，逐个子问题检索并合并结果
        if meta.get("retrieval_mode") == "multi_round":
            return self.multi_round_retrieval(query, history, refs, progress_cb)

        refs["entities"] = self.reco_entities(query, history, refs)
        refs["knowledge_base"] = self.query_knowledgebase(query, history, refs)
        refs["graph_base"] = self.query_graph(query, history, refs)
        refs["web_search"] = self.query_web(query, history, refs)
        refs["multimodal_knowledge_base"] = self.query_multimodal_knowledgebase(query, history, refs)

        return refs

    def restart(self):
        """所有需要重启的模型"""
        self._load_models()

    def construct_query(self, query, refs, meta):
        logger.debug(f"{refs=}")
        if not refs or len(refs) == 0:
            refs = {}

        external_parts = []

        # 解析知识库的结果
        kb_res = refs.get("knowledge_base", {}).get("results", [])
        if kb_res:
            kb_text = "\n".join(f"{r['id']}: {r['entity']['text']}" for r in kb_res)
            external_parts.extend(["知识库信息:", kb_text])

        # 解析图数据库的结果 – prefer pre-formatted context from query_graph
        graph_refs = refs.get("graph_base", {})
        graph_context = graph_refs.get("context", "")
        if graph_context:
            external_parts.extend(["图数据库信息:", graph_context])

        # 解析网络搜索的结果
        web_res = refs.get("web_search", {}).get("results", [])
        if web_res:
            web_text = "\n".join(f"{r['title']}: {r['content']}" for r in web_res)
            external_parts.extend(["网络搜索信息:", web_text])

        multimodal_refs = refs.get("multimodal_knowledge_base", {})
        multimodal_res = multimodal_refs.get("results", [])
        if multimodal_res:
            mm_context_max_items = _coerce_int(config.get("multi_query_mm_context_max_items"), 5, 1, 20)
            mm_context_max_chars = _coerce_int(config.get("multi_query_mm_context_max_chars"), 6000, 500, 100000)
            multimodal_text = format_multimodal_context(
                multimodal_res, max_items=mm_context_max_items, max_chars=mm_context_max_chars
            )
            if multimodal_text:
                external_parts.extend(["多模态知识库信息:", multimodal_text])
        elif meta.get("use_multimodal_kb") and multimodal_refs.get("message"):
            external_parts.extend(["多模态知识库状态:", multimodal_refs["message"]])

        # 构造查询：未启用任何检索时保持原样返回（普通聊天回归，P1-2）；
        # 已启用检索但证据为空时才注入“无证据”模板，要求模型明确说明证据不足而非编造
        from src.utils.prompts import build_chat_prompt
        external = "\n\n".join(external_parts) if external_parts else ""
        query = build_chat_prompt(query, external, meta, params=meta.get("isItemRequest"))
        logger.info(f"-------------RAG-final-prompt---------- {str(query)}")
        return query

    def query_classification(self, query):
        """判断是否需要查询
        - 对于完全基于用户给定信息的任务，称之为"足够""sufficient"，不需要检索；
        - 否则，称之为"不足""insufficient"，可能需要检索，
        """
        raise NotImplementedError

    def query_graph(self, query, history, refs):
        if not (refs["meta"].get("use_graph") and config.enable_knowledge_graph):
            return {"results": {"nodes": [], "edges": []}}

        try:
            threshold = _coerce_float(
                config.get("graph_similarity_threshold"), 0.5, 0.0, 1.0
            )
            hops = _coerce_int(config.get("graph_hops"), 2, 1, MAX_HOPS)
            max_entities = _coerce_int(
                config.get("graph_max_entities"), 5, 1, MAX_ENTITIES
            )
            max_relations = _coerce_int(
                config.get("graph_max_relations"), 10, 1, MAX_RELATIONS
            )
            context_chars = _coerce_int(
                config.get("graph_context_max_chars"), 2000, 100, 100000
            )

            entities = normalize_entities(refs.get("entities", []), max_entities)

            if not entities:
                raw_rows = graph_base.query_node(
                    query,
                    threshold=threshold,
                    hops=hops,
                    max_entities=max_entities,
                    max_relations=max_relations,
                )
            else:
                raw_rows = []
                remaining_budget = max_relations
                for entity in entities:
                    if not entity or remaining_budget <= 0:
                        continue
                    rows = graph_base.query_node(
                        entity,
                        threshold=threshold,
                        hops=hops,
                        max_entities=max_entities,
                        max_relations=remaining_budget,
                    )
                    if rows:
                        raw_rows.extend(rows)
                        remaining_budget -= len(rows)

            ranked = rank_unique_relations(raw_rows, max_relations)
            context = format_graph_context(ranked, max_chars=context_chars)
            graph_result = graph_base.format_query_result_to_graph(ranked)

            return {"results": graph_result, "context": context}
        except Exception as e:
            logger.error(f"Graph query error: {str(e)}")
            return {
                "error": "graph_query_failed",
                "message": str(e),
                "results": {"nodes": [], "edges": []},
                "context": "",
            }


    def query_knowledgebase(self, query, history, refs):
        """查询知识库"""

        response = {
            "results": [],
            "all_results": [],
            "rw_query": query,
            "message": "",
        }

        meta = refs["meta"]

        db_id = meta.get("db_id")
        if not db_id or not config.enable_knowledge_base:
            response["message"] = "知识库未启用、或未指定知识库、或知识库不存在"
            return response

        rw_query = self.rewrite_query(query, history, refs)

        logger.debug(f"{meta=}")
        query_result = knowledge_base.query(query_text=rw_query,
                                            db_id=db_id,
                                            distance_threshold=meta.get("distanceThreshold", 0.5),
                                            rerank_threshold=meta.get("rerankThreshold", 0.1),
                                            max_query_count=meta.get("maxQueryCount", 10),
                                            top_k=meta.get("topK", 5))

        response["results"] = query_result["results"]
        response["all_results"] = query_result["all_results"]
        response["rw_query"] = rw_query

        return response

    def query_web(self, query, history, refs):
        """查询网络"""

        if not (refs["meta"].get("use_web") or not config.enable_web_search):
            return {"results": [], "message": "Web search is disabled"}

        try:
            search_results = self.web_searcher.search(query, max_results=6)
        except Exception as e:
            logger.error(f"Web search error: {str(e)}")
            return {"results": [], "message": "Web search error"}

        return {"results": search_results}

    def query_multimodal_knowledgebase(self, query, history, refs):
        meta = refs["meta"]
        response = {
            "results": [],
            "message": "",
            "kb_id": meta.get("multimodal_kb_id"),
            "kb_name": meta.get("multimodal_kb_name") or meta.get("multimodal_kb_id"),
            "file_id": None,
            "status": "",
        }

        if not meta.get("use_multimodal_kb"):
            return response

        try:
            result = search_multimodal_remote(query, meta)
            response.update(result)
            # C2.4：限制总条数、单条文本长度与图片数
            response["results"] = self._limit_multimodal_results(
                response.get("results") or [],
                max_items=_coerce_int(config.get("multi_query_mm_max_items"), 6, 1, 50),
                max_text_chars=_coerce_int(config.get("multi_query_mm_max_text_chars"), 600, 50, 10000),
                max_images=_coerce_int(config.get("multi_query_mm_max_images"), 3, 0, 20),
            )
        except Exception as e:
            logger.error(f"Multimodal knowledge base search error: {e}, {traceback.format_exc()}")
            response["message"] = f"多模态知识库检索失败: {e}"
            response["status"] = "error"

        return response

    # ==== 多轮检索 (MultiRound / MultiQuery) ====

    def _emit_progress(self, progress_cb, message):
        """发送多轮检索进度消息，回调失败不影响主流程。"""
        if progress_cb is not None:
            try:
                progress_cb(message)
            except Exception:
                pass
        logger.info(message)

    def _select_chat_model(self):
        # 优先使用用户选定的模型（由 chat_router 在调用时注入），否则退回全局默认模型
        if getattr(self, "_chat_model", None) is not None:
            return self._chat_model
        return select_model(model_provider=config.model_provider, model_name=config.model_name)

    def _parse_query_list(self, text):
        """解析模型输出的子问题列表：兼容 JSON 数组 / JSON 对象 / 带说明文字的 JSON / 逐行文本。"""
        text = (text or "").strip()
        if not text:
            return []

        def _clean(items):
            return [str(x).strip() for x in items if str(x).strip()]

        # 去掉 ```json ... ``` 围栏
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        # 1) 直接 JSON 解析（含空数组，需显式返回 []，不能落到逐行兜底）
        data = None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, list):
            return _clean(data)
        if isinstance(data, dict):
            for key in ("questions", "queries", "sub_questions", "sub_queries", "results"):
                if isinstance(data.get(key), list):
                    return _clean(data[key])
            return _clean([v for v in data.values() if isinstance(v, str)])

        # 2) 模型常附带前后说明文字，先截取最外层 [..] / {..} 再解析
        for start, end in (("[", "]"), ("{", "}")):
            i, j = text.find(start), text.rfind(end)
            if i != -1 and j > i:
                candidate = text[i : j + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, list):
                        return _clean(data)
                    if isinstance(data, dict):
                        for key in ("questions", "queries", "sub_questions", "sub_queries", "results"):
                            if isinstance(data.get(key), list):
                                return _clean(data[key])
                        return _clean([v for v in data.values() if isinstance(v, str)])
                except (json.JSONDecodeError, TypeError):
                    continue

        # 3) 逐行解析兜底：去除序号 / 列表符 / 引号，跳过 JSON 结构片段行
        lines = []
        for raw in text.splitlines():
            line = raw.strip().lstrip("-*•").strip()
            if not line:
                continue
            if re.match(r"^[\[{\"'“]", line) or line.endswith(("]", "}", ",", '"')):
                continue
            line = re.sub(r"^\d+[.)、:：]\s*", "", line)
            line = line.strip().strip('"').strip("'")
            if line and line not in lines:
                lines.append(line)
        return lines

    def generate_sub_queries(self, query, history, meta, count):
        """第1轮：由模型把用户问题改写成 count 个检索子问题。"""
        from src.utils.prompts import multi_query_generation_prompt
        model = self._select_chat_model()
        history_questions = [entry["content"] for entry in history if entry.get("role") == "user"] if history else []
        prompt = multi_query_generation_prompt.format(
            question=query,
            history=json.dumps(history_questions[-5:], ensure_ascii=False),
            count=count,
        )
        try:
            response = model.predict(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            questions = self._parse_query_list(text)
        except Exception as e:
            logger.error(f"多轮检索：子问题生成失败: {e}")
            questions = []
        if not questions:
            questions = [query]
        return [q for q in questions if q][:count]

    @staticmethod
    def _result_text(r):
        """从普通知识库结果（entity.text）或多模态结果（text）中提取文本。"""
        if not isinstance(r, dict):
            return ""
        text = r.get("text")
        if text:
            return str(text)
        entity = r.get("entity")
        if isinstance(entity, dict) and entity.get("text"):
            return str(entity["text"])
        return ""

    def _parse_assessment(self, text):
        """解析模型输出的价值评估 JSON 对象，容错围栏/前后说明文字。"""
        text = (text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return {}

    def assess_results(self, question, results, meta):
        """让模型评估检索到的内容是否有价值、是否需要继续检索。

        返回 ``{"has_value", "need_more", "next_keywords", "reason"}``。
        """
        from src.utils.prompts import multi_query_assessment_prompt
        model = self._select_chat_model()
        snippets = "\n".join(
            f"- {self._result_text(r)[:150]}"
            for r in results[:10] if self._result_text(r)
        ) or "（无检索结果）"
        prompt = multi_query_assessment_prompt.format(question=question, results=snippets)
        try:
            response = model.predict(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            data = self._parse_assessment(text)
        except Exception as e:
            logger.error(f"多轮检索：内容价值评估失败: {e}")
            data = {}
        next_keywords = data.get("next_keywords") or []
        if not isinstance(next_keywords, list):
            next_keywords = [str(next_keywords)]
        return {
            "has_value": bool(data.get("has_value", False)),
            "need_more": bool(data.get("need_more", True)),
            "next_keywords": [str(k) for k in next_keywords if str(k).strip()][:8],
            "reason": str(data.get("reason") or "").strip(),
        }

    def generate_next_queries(self, query, results_snippets, assessment, existing_queries, meta, count):
        """后续轮次：根据评估反馈生成下一轮检索查询，补充召回。"""
        from src.utils.prompts import multi_query_refine_prompt
        model = self._select_chat_model()
        prompt = multi_query_refine_prompt.format(
            question=query,
            results=results_snippets or "（无）",
            assessment=json.dumps(assessment, ensure_ascii=False),
            previous="\n".join(existing_queries) or "（无）",
            count=count,
        )
        try:
            response = model.predict(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            questions = self._parse_query_list(text)
        except Exception as e:
            logger.error(f"多轮检索：下一轮查询生成失败: {e}")
            questions = []
        return [q for q in questions if q and q not in existing_queries][:count]


    @staticmethod
    def _dedupe_results(results):
        """按 id 与文本签名去重，保持原始顺序。"""
        seen_ids = set()
        seen_sigs = set()
        out = []
        for r in results:
            rid = r.get("id")
            if rid is not None:
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
            entity = r.get("entity") or {}
            text = entity.get("text") or ""
            if text:
                sig = hashlib.sha1(text.strip().encode("utf-8", "ignore")).hexdigest()
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
            out.append(r)
        return out

    @staticmethod
    def _dedupe_multimodal(results):
        """按 (fileId, page, text) 对多模态检索结果去重，保持原始顺序。"""
        seen = set()
        out = []
        for r in results:
            if not isinstance(r, dict):
                continue
            text = str(r.get("text") or "").strip()
            key = (
                str(r.get("fileId") or r.get("file_id") or ""),
                str(r.get("page") or ""),
                text,
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out

    @staticmethod
    def _normalize_sub_query_key(query):
        """规范化子问题用于近似去重（C2.2）：去首尾空白、合并空白、去尾部问号标点。"""
        if not query:
            return ""
        text = str(query).strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[？?！!。．.，,]+$", "", text)
        return text

    @staticmethod
    def _limit_multimodal_results(results, max_items=6, max_text_chars=600, max_images=3):
        """限制合并后的多模态结果规模（C2.4）：总条数、单条文本长度与图片数。

        按输入顺序保留前 ``max_items`` 条；单条文本超长截断并加省略号；
        单条图片超过 ``max_images`` 张时截断。
        """
        out = []
        for item in results:
            if not isinstance(item, dict) or len(out) >= max_items:
                continue
            limited = dict(item)
            text = str(limited.get("text") or "")
            if len(text) > max_text_chars:
                limited["text"] = text[: max_text_chars - 1].rstrip() + "…"
            images = limited.get("images")
            if isinstance(images, list) and len(images) > max_images:
                limited["images"] = images[:max_images]
            out.append(limited)
        return out

    @staticmethod
    def _aggregate_mm_status(statuses, has_results, budget_hit, deadline_hit, user_cancelled):
        """多模态检索总体状态聚合（C2.5）。

        优先级：用户取消 > 达到预算 > 达到 deadline > 有结果(ok) > 远端失败(error)
        > 未选择知识库(no_kb_selected) > 检索为空(empty) > 未启用("")。
        远端失败/检索为空/达到预算/用户取消必须呈现为不同状态，不能都伪装成空结果。
        """
        if user_cancelled:
            return "user_cancelled"
        if budget_hit:
            return "budget_reached"
        if deadline_hit:
            return "deadline_reached"
        if has_results:
            return "ok"
        for state in ("error", "no_kb_selected", "empty"):
            if state in statuses:
                return state
        return ""

    @staticmethod
    def _mm_state_message(status, mm_meta, budget_used, budget):
        """多模态状态对应的展示文案；预算/deadline/取消用服务端文案，其余保留远端消息。"""
        if status == "budget_reached":
            return f"已达本次远端检索预算（{budget_used}/{budget}），已停止继续扩展检索"
        if status == "deadline_reached":
            return "已达整轮检索 deadline，已停止继续扩展检索"
        if status == "user_cancelled":
            return "检索已被取消"
        if status == "empty" and not (mm_meta.get("message") or ""):
            return "未检索到结果"
        return mm_meta.get("message") or ""

    def _kb_query(self, sub_query, meta):
        """针对单个子问题执行一次知识库检索。

        返回 ``(results, all_results)``：前者是经过距离/重排过滤后的结果，
        后者是原始未过滤的候选集（用于对外暴露 all_results，与快速检索语义一致）。
        """
        db_id = meta.get("db_id")
        if not db_id or not config.enable_knowledge_base:
            return [], []
        top_k = _coerce_int(meta.get("topK"), 5, 1, 50)
        recall_top_k = max(top_k * 2, 10)
        max_query_count = _coerce_int(meta.get("maxQueryCount"), 10, 1, 1000)
        distance_threshold = _coerce_float(meta.get("distanceThreshold"), 0.5, -1.0, 1.0)
        rerank_threshold = _coerce_float(meta.get("rerankThreshold"), 0.1, -1.0, 1.0)
        query_result = knowledge_base.query(
            query_text=sub_query,
            db_id=db_id,
            distance_threshold=distance_threshold,
            rerank_threshold=rerank_threshold,
            max_query_count=max(max_query_count, recall_top_k),
            top_k=recall_top_k,
        )
        return query_result.get("results", []), query_result.get("all_results", [])

    def _final_rerank(self, query, results, meta):
        """对合并后的结果用检索所用查询做一次整体重排。

        - 重排器开启时按 rerank_score 降序并过滤阈值；
        - 重排器关闭时按已有 distance 降序，避免首个子问题的结果垄断 top_k。
        """
        if not results:
            return results
        if not config.enable_reranker:
            results = sorted(
                results, key=lambda x: x.get("distance", -1) or -1, reverse=True
            )
            return results
        try:
            reranker = get_reranker()
            scored = [(i, r) for i, r in enumerate(results)
                      if r.get("entity") and r["entity"].get("text")]
            if not scored:
                return results
            idxs = [i for i, _ in scored]
            texts = [r["entity"]["text"] for _, r in scored]
            scores = reranker.compute_score([query, texts], normalize=False)
            for pos, i in enumerate(idxs):
                if pos < len(scores):
                    results[i]["rerank_score"] = scores[pos]
            results.sort(key=lambda x: x.get("rerank_score", -1), reverse=True)
            threshold = _coerce_float(meta.get("rerankThreshold"), 0.1, -1.0, 1.0)
            results = [r for r in results if r.get("rerank_score", -1) > threshold]
        except Exception as e:
            logger.error(f"多轮检索：最终重排失败: {e}")
        return results

    def multi_round_retrieval(self, query, history, refs, progress_cb=None):
        """
        多轮检索：模型驱动的「检索 → 内容价值评估 → 再检索」循环。

        - 第1轮：改写+生成 count 个面向向量检索的查询，并始终保留改写后的查询本身参与检索；
        - 每轮检索后，模型评估检索到的内容是否有价值、是否需要继续；
          「有足够价值」或「模型认为继续检索无意义」则停止；
        - 第一轮一个结果都没检索到时，强制继续多检索几轮（不会因为首轮无果就放弃）；
        - 后续轮次根据评估反馈（含建议的关键词）生成新的检索查询；
        - 单个查询检索失败只跳过该查询，不中断整体流程；
        - 普通向量知识库与（远程）多模态知识库都会用每个查询各自检索并合并；
        - 最后用改写查询对合并结果整体重排，取 top_k。
        """
        meta = refs["meta"]

        # 图/网络检索仍用原始问题执行一次
        refs["entities"] = self.reco_entities(query, history, refs)
        refs["graph_base"] = self.query_graph(query, history, refs)
        refs["web_search"] = self.query_web(query, history, refs)

        # 多模态知识库（远程）：先以原始问题检索一次作为基线，后续每个查询也会检索。
        # C2：每次问答设置远端检索总预算与整轮 deadline，相同/近似子问题规范化
        # 去重，结果短 TTL 缓存，避免查询扩展无限放大远端调用。
        use_mm = bool(meta.get("use_multimodal_kb"))
        multimodal_merged: list = []
        mm_meta: dict = {}
        mm_status = ""

        mm_budget = _coerce_int(config.get("multi_query_multimodal_budget"), 3, 1, 10)
        mm_deadline_seconds = _coerce_float(config.get("multi_query_deadline_seconds"), 30.0, 0.0, 300.0)
        mm_cache_ttl = _coerce_float(config.get("multi_query_mm_cache_ttl_seconds"), 5.0, 0.0, 60.0)
        mm_max_items = _coerce_int(config.get("multi_query_mm_max_items"), 6, 1, 50)
        mm_max_text_chars = _coerce_int(config.get("multi_query_mm_max_text_chars"), 600, 50, 10000)
        mm_max_images = _coerce_int(config.get("multi_query_mm_max_images"), 3, 0, 20)

        mm_budget_used = 0
        mm_budget_hit = False
        mm_deadline_hit = False
        mm_user_cancelled = False
        mm_statuses: list = []

        def _mm_search(q, *, deadline):
            """在预算 / deadline / 短 TTL 缓存约束下执行一次多模态远端检索。

            返回 ``(result, consumed, stopped)``：result 为远端结果 dict 或
            None（被预算 / deadline / 用户取消阻止）；consumed 表示是否实际
            消耗了一次远端预算；stopped 为阻止原因（"budget"/"deadline"/
            "user_cancelled"/None）。
            """
            nonlocal mm_budget_used, mm_budget_hit, mm_deadline_hit, mm_user_cancelled
            if _signal_is_set(_retrieval_cancel_signal(meta)):
                mm_user_cancelled = True
                return None, False, "user_cancelled"
            top_k = _coerce_int(meta.get("topK"), 5, 1, 50)
            cache_key = _MMSearchCache.key_for(meta, q, top_k=top_k)
            cached = _MMSearchCache.get(cache_key)
            if cached is not None:
                return cached, False, None
            if mm_budget_used >= mm_budget:
                mm_budget_hit = True
                return None, False, "budget"
            if deadline is not None and time.monotonic() >= deadline:
                mm_deadline_hit = True
                return None, False, "deadline"
            try:
                mm_res = search_multimodal_remote(q, meta)
            except Exception as e:
                logger.error(f"多模态知识库检索失败: {e}")
                mm_res = {"results": [], "message": f"多模态知识库检索失败: {e}", "status": "error"}
            mm_budget_used += 1
            # 错误/未选库结果不缓存，避免把瞬时失败或权限态在 TTL 内当成确定结果
            if mm_cache_ttl > 0 and mm_res.get("status") not in ("error", "no_kb_selected"):
                _MMSearchCache.put(cache_key, mm_res, mm_cache_ttl)
            return mm_res, True, None

        deadline: float | None = None
        if use_mm:
            base_mm, _, _ = _mm_search(query, deadline=None)
            if base_mm:
                multimodal_merged.extend(base_mm.get("results", []))
                if base_mm.get("status"):
                    mm_statuses.append(base_mm["status"])
            mm_meta = {k: (base_mm or {}).get(k) for k in ("kb_id", "kb_name", "file_id", "message", "status")}
            # 整轮 deadline 在基线检索完成后开始计时，保证基线始终执行；
            # 0 表示立即过期（只允许基线检索）
            deadline = (
                time.monotonic() - 1e-6
                if mm_deadline_seconds <= 0
                else time.monotonic() + mm_deadline_seconds
            )

        rw_query = self.rewrite_query(query, history, refs)

        if (not config.enable_knowledge_base or not meta.get("db_id")) and not use_mm:
            refs["knowledge_base"] = {
                "results": [],
                "all_results": [],
                "rw_query": rw_query,
                "message": "知识库未启用、或未指定知识库、或知识库不存在",
            }
            refs["multimodal_knowledge_base"] = {
                "results": [],
                "message": "",
                "kb_id": None,
                "kb_name": None,
                "file_id": None,
                "status": "",
            }
            refs["multi_round"] = {
                "mode": "multi_round",
                "sub_queries": [],
                "rounds": [],
                "total_rounds": 0,
                "final_recall": 0,
                "assessment": {},
            }
            self._emit_progress(progress_cb, "未配置任何知识库检索源（普通知识库或多模态知识库），跳过子问题检索")
            return refs

        max_rounds = _coerce_int(config.get("multi_query_max_rounds"), 2, 1, 5)
        query_count = _coerce_int(config.get("multi_query_count"), 3, 1, 8)
        top_k = _coerce_int(meta.get("topK"), 5, 1, 50)

        seen_queries = set()
        seen_normalized = set()
        sub_queries = []
        merged = []
        all_raw = []
        round_log = []
        assessment: dict = {}

        def _combined():
            """普通向量知识库 + 多模态的合并结果（用于评估与摘要）。"""
            combined = self._dedupe_results(merged)
            if use_mm:
                combined = combined + self._dedupe_multimodal(multimodal_merged)
            return combined

        def _recall_count():
            return len(self._dedupe_results(merged)) + (
                len(self._dedupe_multimodal(multimodal_merged)) if use_mm else 0
            )

        def _snippets():
            return "\n".join(
                f"- {self._result_text(r)[:150]}" for r in _combined()[:8] if self._result_text(r)
            ) or "（无检索结果）"

        def _assess():
            """让模型评估当前已检索内容的价值，返回评估字典。"""
            result = self.assess_results(rw_query, _combined(), meta)
            # 守卫：没有任何检索结果时，即使模型误判 has_value，也强制继续多检索几轮
            if _recall_count() == 0:
                result["has_value"] = False
                result["need_more"] = True
            return result

        def _emit_assessment(a):
            verdict = "检索到有价值内容" if a.get("has_value") else "未检索到足够有价值的内容"
            self._emit_progress(progress_cb, f"  评估：{verdict}（{a.get('reason') or '无说明'}）")
            if a.get("next_keywords"):
                self._emit_progress(progress_cb, f"  建议下一轮检索关键词：{'、'.join(a['next_keywords'])}")

        def _retrieve(query_list):
            """检索一组查询：单个查询失败只跳过该查询，累积已检索到的结果。

            同时检索普通向量知识库与（远程）多模态知识库。多模态部分受
            预算 / deadline / 用户取消约束（C2）：一旦停止扩展，普通向量
            库仍继续按其余子问题检索。
            """
            mm_stop_reported = False

            for sq in query_list:
                try:
                    results, raw = self._kb_query(sq, meta)
                    if results:
                        merged.extend(results)
                    if raw:
                        all_raw.extend(raw)
                except Exception as e:
                    logger.error(f"多轮检索：查询检索失败（{sq}）: {e}")
                    self._emit_progress(progress_cb, f"  查询「{sq}」检索失败，已跳过")
                if use_mm:
                    mm_res, _, stopped = _mm_search(sq, deadline=deadline)
                    if mm_res is None:
                        if stopped and not mm_stop_reported:
                            mm_stop_reported = True
                            if stopped == "user_cancelled":
                                self._emit_progress(progress_cb, "  检索已被取消，停止多模态扩展")
                            elif stopped == "budget":
                                self._emit_progress(progress_cb, f"  已达到远端检索预算（{mm_budget_used}/{mm_budget}），停止多模态扩展")
                            elif stopped == "deadline":
                                self._emit_progress(progress_cb, "  已达到整轮检索 deadline，停止多模态扩展")
                        continue
                    if mm_res.get("status"):
                        mm_statuses.append(mm_res["status"])
                    multimodal_merged.extend(mm_res.get("results", []))

        self._emit_progress(progress_cb, f"多轮检索启动：最多 {max_rounds} 轮，每轮由模型生成 {query_count} 个检索查询")

        # ---- 第1轮：改写后的查询 + 模型生成的检索查询 ----
        self._emit_progress(progress_cb, f"第1轮：模型生成 {query_count} 个检索查询")
        generated = self.generate_sub_queries(rw_query, history, meta, query_count)
        round1_queries = []
        for sq in [rw_query] + list(generated):
            norm_key = self._normalize_sub_query_key(sq)
            if not sq or sq in seen_queries or norm_key in seen_normalized:
                continue
            seen_queries.add(sq)
            seen_normalized.add(norm_key)
            round1_queries.append(sq)
            sub_queries.append(sq)
            self._emit_progress(progress_cb, f"  检索查询：{sq}")
        _retrieve(round1_queries)
        merged = self._dedupe_results(merged)
        all_raw = self._dedupe_results(all_raw)
        recall = _recall_count()
        assessment = _assess()
        round_log.append({
            "round": 1,
            "queries": round1_queries,
            "recall": recall,
            "has_value": assessment["has_value"],
            "need_more": assessment["need_more"],
            "next_keywords": assessment["next_keywords"],
            "reason": assessment["reason"],
        })
        self._emit_progress(progress_cb, f"第1轮检索完成：{len(round1_queries)} 个查询，命中 {recall} 条")
        _emit_assessment(assessment)

        # ---- 后续轮次：模型评估决定是否继续检索 ----
        for round_no in range(2, max_rounds + 1):
            if assessment.get("has_value") or not assessment.get("need_more"):
                self._emit_progress(progress_cb, "模型评估认为内容已足够，停止多轮检索")
                break
            self._emit_progress(
                progress_cb,
                f"第{round_no}轮：未检索到足够有价值的内容，根据评估继续检索",
            )
            refined = self.generate_next_queries(
                rw_query, _snippets(), assessment, sub_queries, meta, query_count
            )
            round_queries = []
            for sq in refined:
                norm_key = self._normalize_sub_query_key(sq)
                if not sq or sq in seen_queries or norm_key in seen_normalized:
                    continue
                seen_queries.add(sq)
                seen_normalized.add(norm_key)
                round_queries.append(sq)
                sub_queries.append(sq)
                self._emit_progress(progress_cb, f"  第{round_no}轮 检索查询：{sq}")
            if not round_queries:
                self._emit_progress(progress_cb, "模型未生成新的检索查询，停止多轮检索")
                break
            _retrieve(round_queries)
            merged = self._dedupe_results(merged)
            all_raw = self._dedupe_results(all_raw)
            recall = _recall_count()
            assessment = _assess()
            round_log.append({
                "round": round_no,
                "queries": round_queries,
                "recall": recall,
                "has_value": assessment["has_value"],
                "need_more": assessment["need_more"],
                "next_keywords": assessment["next_keywords"],
                "reason": assessment["reason"],
            })
            self._emit_progress(
                progress_cb,
                f"第{round_no}轮检索完成：新增 {len(round_queries)} 个查询，累计命中 {recall} 条",
            )
            _emit_assessment(assessment)

        # ---- 整体重排 + 取 top_k（与检索实际所用的改写查询保持一致）----
        merged = self._final_rerank(rw_query, merged, meta)
        final = merged[:top_k]

        refs["knowledge_base"] = {
            "results": final,
            "all_results": all_raw,
            "rw_query": rw_query,
            "message": "",
        }
        if use_mm:
            mm_deduped = self._limit_multimodal_results(
                self._dedupe_multimodal(multimodal_merged),
                max_items=mm_max_items,
                max_text_chars=mm_max_text_chars,
                max_images=mm_max_images,
            )
            mm_status = self._aggregate_mm_status(
                statuses=mm_statuses,
                has_results=bool(mm_deduped),
                budget_hit=mm_budget_hit,
                deadline_hit=mm_deadline_hit,
                user_cancelled=mm_user_cancelled,
            )
            refs["multimodal_knowledge_base"] = {
                **mm_meta,
                "results": mm_deduped,
                "status": mm_status,
                "message": self._mm_state_message(mm_status, mm_meta, mm_budget_used, mm_budget),
                "budget_used": mm_budget_used,
                "budget_limit": mm_budget,
            }
        else:
            refs["multimodal_knowledge_base"] = {
                "results": [],
                "message": "",
                "kb_id": None,
                "kb_name": None,
                "file_id": None,
                "status": "",
            }
        refs["multi_round"] = {
            "mode": "multi_round",
            "sub_queries": sub_queries,
            "rounds": round_log,
            "total_rounds": len(round_log),
            "final_recall": len(final),
            "assessment": assessment,
            "mm_status": mm_status,
            "mm_budget_used": mm_budget_used if use_mm else 0,
            "mm_budget_limit": mm_budget if use_mm else 0,
        }
        verdict = "，模型认为内容足够" if assessment.get("has_value") else "，模型未确认检索到足够有价值的内容"
        self._emit_progress(
            progress_cb,
            f"多轮检索完成：共 {len(sub_queries)} 个查询 / {len(round_log)} 轮，最终上下文 {len(final)} 条{verdict}",
        )
        return refs

    def rewrite_query(self, query, history, refs):
        """重写查询"""
        model_provider = config.model_provider
        model_name = config.model_name
        model = select_model(model_provider=model_provider, model_name=model_name)
        if refs["meta"].get("mode") == "search":  # 比如检索测试中，是否开启重写查询，不同与全局配置，如果是搜索模式，就使用 meta 的配置，否则就使用全局的配置
            rewrite_query_span = refs["meta"].get("use_rewrite_query", "off")
        else:
            rewrite_query_span = config.use_rewrite_query

        if rewrite_query_span == "off":
            return query

        from src.utils.prompts import rewritten_query_prompt_template2 as rw_template
        # 只提取用户的输入
        history_query = [entry["content"] for entry in history if entry["role"] == "user"] if history else []
        rewritten_query_prompt = rw_template.format(history=history_query, query=query)
        rewritten_query = model.predict(rewritten_query_prompt).content

        if rewrite_query_span == "hyde":
            res = HyDEOperator.call(model_callable=model.predict, query=query, context_str=history_query)
            rewritten_query = res.content

        return rewritten_query

    def reco_entities(self, query, history, refs):
        """识别句子中的实体"""
        query = refs.get("rewritten_query", query)
        model_provider = config.model_provider
        model_name = config.model_name
        model = select_model(model_provider=model_provider, model_name=model_name)

        entities = []
        if refs["meta"].get("use_graph"):
            from src.utils.prompts import entity_extraction_prompt_template as entity_template
            # from src.utils.prompts import keywords_prompt_template as entity_templat|e

            entity_extraction_prompt = entity_template.format(text=query)
            entities = model.predict(entity_extraction_prompt).content.split("<->")
            # entities = [entity for entity in entities if all(char.isalnum() or char in "汉字" for char in entity)]

        return entities

    def __call__(self, query, history, meta, progress_cb=None, chat_model=None):
        # 注入用户选定的模型，供多轮检索的子问题生成使用（与回答模型保持一致）
        self._chat_model = chat_model
        try:
            refs = self.retrieval(query, history, meta, progress_cb)
            query = self.construct_query(query, refs, meta)
            return query, refs
        finally:
            self._chat_model = None
