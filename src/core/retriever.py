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

    def retrieval(self, query, history, meta):
        refs = {"query": query, "history": history, "meta": meta}
        refs["model_name"] = config.model_name
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

    def __call__(self, query, history, meta):
        refs = self.retrieval(query, history, meta)
        query = self.construct_query(query, refs, meta)
        return query, refs
