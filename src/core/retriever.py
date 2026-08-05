import json
import re
import hashlib
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
            return query

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
            multimodal_text = format_multimodal_context(multimodal_res)
            if multimodal_text:
                external_parts.extend(["多模态知识库信息:", multimodal_text])
        elif meta.get("use_multimodal_kb") and multimodal_refs.get("message"):
            external_parts.extend(["多模态知识库状态:", multimodal_refs["message"]])

        # 构造查询
        from src.utils.prompts import knowbase_qa_template
        from src.utils.prompts import knowbase_itemGen_template
        if external_parts and len(external_parts) > 0:
            external = "\n\n".join(external_parts)
            # 题目生成的请求单独处理
            if meta.get("isItemRequest"):
                query = knowbase_itemGen_template.format(external=external, params=meta.get("isItemRequest"))
            else:
                query = knowbase_qa_template.format(external=external, query=query)
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
            "base_url": None,
            "status": "",
        }

        if not meta.get("use_multimodal_kb"):
            return response

        try:
            response.update(search_multimodal_remote(query, meta))
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

    def generate_refined_queries(self, query, history, partial_results, existing_queries, meta, count):
        """后续轮次：根据已有部分结果生成新的检索子问题，补充召回。"""
        from src.utils.prompts import multi_query_refine_prompt
        model = self._select_chat_model()
        snippets = "\n".join(
            f"- {(r.get('entity') or {}).get('text', '')[:120]}"
            for r in partial_results[:8] if r.get("entity")
        )
        prompt = multi_query_refine_prompt.format(
            question=query,
            results=snippets or "（无）",
            previous="\n".join(existing_queries) or "（无）",
            count=count,
        )
        try:
            response = model.predict(prompt)
            text = response.content if hasattr(response, "content") else str(response)
            questions = self._parse_query_list(text)
        except Exception as e:
            logger.error(f"多轮检索：扩展子问题生成失败: {e}")
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
        多轮检索：由模型多次生成检索子问题，逐个子问题检索并合并结果。

        - 第1轮：改写+生成 count 个子问题，并始终保留改写后的查询本身参与检索；
        - 后续轮次：若合并命中数低于阈值，由模型依据部分结果生成新的子问题再次检索；
        - 单个子问题检索失败只跳过该子问题，不中断整体流程；
        - 普通向量知识库与（远程）多模态知识库都会用每个子问题各自检索并合并；
        - 最后用检索实际所用的改写查询对合并结果整体重排，取 top_k。
        """
        meta = refs["meta"]

        # 图/网络检索仍用原始问题执行一次
        refs["entities"] = self.reco_entities(query, history, refs)
        refs["graph_base"] = self.query_graph(query, history, refs)
        refs["web_search"] = self.query_web(query, history, refs)

        # 多模态知识库（远程）：先以原始问题检索一次作为基线，后续每个子问题也会检索
        use_mm = bool(meta.get("use_multimodal_kb"))
        multimodal_merged: list = []
        mm_meta: dict = {}
        if use_mm:
            try:
                base_mm = search_multimodal_remote(query, meta)
            except Exception as e:
                logger.error(f"多模态知识库检索失败: {e}")
                base_mm = {"results": [], "message": f"多模态知识库检索失败: {e}"}
            multimodal_merged.extend(base_mm.get("results", []))
            mm_meta = {k: base_mm.get(k) for k in ("kb_id", "kb_name", "file_id", "base_url", "message", "status")}

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
                "base_url": None,
                "status": "",
            }
            refs["multi_round"] = {
                "mode": "multi_round",
                "sub_queries": [],
                "rounds": [],
                "total_rounds": 0,
                "final_recall": 0,
            }
            self._emit_progress(progress_cb, "未配置任何知识库检索源（普通知识库或多模态知识库），跳过子问题检索")
            return refs

        max_rounds = _coerce_int(config.get("multi_query_max_rounds"), 2, 1, 5)
        query_count = _coerce_int(config.get("multi_query_count"), 3, 1, 8)
        recall_min = _coerce_int(config.get("multi_query_recall_min"), 3, 1, 50)
        top_k = _coerce_int(meta.get("topK"), 5, 1, 50)

        seen_queries = set()
        sub_queries = []
        merged = []
        all_raw = []
        round_log = []

        def _recall_count():
            """普通向量知识库与多模态知识库的总命中数（均已去重）。"""
            mm_count = len(self._dedupe_multimodal(multimodal_merged)) if use_mm else 0
            return len(merged) + mm_count

        def _retrieve(query_list):
            """检索一组子问题：单个子问题失败只跳过该问题，累积已检索到的结果。

            同时检索普通向量知识库与（远程）多模态知识库。
            """
            for sq in query_list:
                try:
                    results, raw = self._kb_query(sq, meta)
                    if results:
                        merged.extend(results)
                    if raw:
                        all_raw.extend(raw)
                except Exception as e:
                    logger.error(f"多轮检索：子问题检索失败（{sq}）: {e}")
                    self._emit_progress(progress_cb, f"  子问题「{sq}」检索失败，已跳过")
                if use_mm:
                    try:
                        mm_res = search_multimodal_remote(sq, meta)
                    except Exception as e:
                        logger.error(f"多轮检索：多模态检索失败（{sq}）: {e}")
                        self._emit_progress(progress_cb, f"  子问题「{sq}」多模态检索失败，已跳过")
                        continue
                    multimodal_merged.extend(mm_res.get("results", []))

        self._emit_progress(progress_cb, f"多轮检索启动：最多 {max_rounds} 轮，每轮由模型生成 {query_count} 个子问题")

        # ---- 第1轮：改写后的查询 + 模型生成的子问题 ----
        self._emit_progress(progress_cb, f"第1轮：模型生成 {query_count} 个检索子问题")
        generated = self.generate_sub_queries(rw_query, history, meta, query_count)
        round1_queries = []
        for sq in [rw_query] + list(generated):
            if not sq or sq in seen_queries:
                continue
            seen_queries.add(sq)
            round1_queries.append(sq)
            sub_queries.append(sq)
            self._emit_progress(progress_cb, f"  检索子问题：{sq}")
        _retrieve(round1_queries)
        merged = self._dedupe_results(merged)
        all_raw = self._dedupe_results(all_raw)
        recall = _recall_count()
        round_log.append({"round": 1, "queries": round1_queries, "recall": recall})
        self._emit_progress(progress_cb, f"第1轮检索完成：{len(round1_queries)} 个子问题，命中 {recall} 条")

        # ---- 后续轮次：召回不足时由模型依据部分结果生成新的子问题 ----
        for round_no in range(2, max_rounds + 1):
            if recall >= max(top_k, recall_min):
                break
            self._emit_progress(
                progress_cb,
                f"命中 {recall} 条，低于阈值 {max(top_k, recall_min)}，进入第{round_no}轮扩展检索",
            )
            refined = self.generate_refined_queries(
                rw_query, history, merged, sub_queries, meta, query_count
            )
            round_queries = []
            for sq in refined:
                if not sq or sq in seen_queries:
                    continue
                seen_queries.add(sq)
                round_queries.append(sq)
                sub_queries.append(sq)
                self._emit_progress(progress_cb, f"  第{round_no}轮 检索子问题：{sq}")
            if not round_queries:
                self._emit_progress(progress_cb, "模型未生成新的子问题，停止多轮检索")
                break
            _retrieve(round_queries)
            merged = self._dedupe_results(merged)
            all_raw = self._dedupe_results(all_raw)
            recall = _recall_count()
            round_log.append({"round": round_no, "queries": round_queries, "recall": recall})
            self._emit_progress(
                progress_cb,
                f"第{round_no}轮检索完成：新增 {len(round_queries)} 个子问题，累计命中 {recall} 条",
            )

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
            refs["multimodal_knowledge_base"] = {
                **mm_meta,
                "results": self._dedupe_multimodal(multimodal_merged),
                "message": mm_meta.get("message") or "",
            }
        else:
            refs["multimodal_knowledge_base"] = {
                "results": [],
                "message": "",
                "kb_id": None,
                "kb_name": None,
                "file_id": None,
                "base_url": None,
                "status": "",
            }
        refs["multi_round"] = {
            "mode": "multi_round",
            "sub_queries": sub_queries,
            "rounds": round_log,
            "total_rounds": len(round_log),
            "final_recall": len(final),
        }
        self._emit_progress(
            progress_cb,
            f"多轮检索完成：共 {len(sub_queries)} 个子问题 / {len(round_log)} 轮，最终上下文 {len(final)} 条",
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
