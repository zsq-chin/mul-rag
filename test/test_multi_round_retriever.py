"""Tests for multi-round (MultiQuery) retrieval in src/core/retriever.py.

The multi-round mode asks the model to generate several retrieval sub-questions,
searches the knowledge base for each one, merges results, and re-runs another
model generation round when recall is low (the "generate questions multiple
times" strategy from the WeKnora reference).

These tests load retriever.py through a stubbed `src` package so the real
Milvus / Neo4j initialisation in src/__init__.py is never triggered.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Minimal stub modules (mirrors test_graph_retrieval.py)
# ---------------------------------------------------------------------------

def _make_module(name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


class _StubConfig(dict):
    """Mimics SimpleConfig: attribute read/write goes through dict."""

    def __setattr__(self, key, value):
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


_logger = MagicMock()
_make_module("src.utils.logging_config", logger=_logger)
_make_module("src.utils", logger=_logger)
_make_module("src.models.rerank_model", get_reranker=MagicMock())
_make_module("src.core.operators", HyDEOperator=MagicMock())
_make_module(
    "server.utils.multimodal_remote",
    format_multimodal_context=MagicMock(return_value=""),
    search_multimodal_remote=MagicMock(return_value={"results": [], "message": ""}),
)

# Stubbed prompts: only the multi-round templates we lazy-import are needed.
_make_module(
    "src.utils.prompts",
    multi_query_generation_prompt="GEN question={question} history={history} count={count}",
    multi_query_refine_prompt="REFINE question={question} results={results} previous={previous} count={count}",
)

_config_mod = _make_module("src.config", Config=_StubConfig)
_cfg = _config_mod.Config()
_cfg.update({
    "enable_web_search": False,
    "enable_reranker": False,
    "enable_knowledge_base": True,
    "enable_knowledge_graph": False,
    "use_rewrite_query": "off",
    "model_provider": "test",
    "model_name": "test-model",
    "multi_query_max_rounds": 2,
    "multi_query_count": 3,
    "multi_query_recall_min": 3,
})

_kb = MagicMock()
_src_core = _make_module("src.core")
_src_core.__path__ = [str(_PROJECT_ROOT / "src" / "core")]
_src_models = _make_module("src.models", select_model=MagicMock())
_make_module("src", config=_cfg, knowledge_base=_kb, graph_base=MagicMock())
_make_module("server", )
_make_module("server.utils")

# Load the real graph_retrieval helpers (pure, no side effects).
_helpers_path = _PROJECT_ROOT / "src" / "core" / "graph_retrieval.py"
_spec_helpers = importlib.util.spec_from_file_location("src.core.graph_retrieval", _helpers_path)
_helpers_mod = importlib.util.module_from_spec(_spec_helpers)
sys.modules["src.core.graph_retrieval"] = _helpers_mod
_spec_helpers.loader.exec_module(_helpers_mod)

# Load the real retriever module against the stubs.
_retriever_path = _PROJECT_ROOT / "src" / "core" / "retriever.py"
_spec = importlib.util.spec_from_file_location("src.core.retriever", _retriever_path)
_retriever_mod = importlib.util.module_from_spec(_spec)
sys.modules["src.core.retriever"] = _retriever_mod
_spec.loader.exec_module(_retriever_mod)

Retriever = _retriever_mod.Retriever


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _ScriptedModel:
    """Returns a fixed answer per prompt-marker, or raises on unexpected input."""

    def __init__(self, answers, fallback=""):
        self.answers = list(answers)
        self.fallback = fallback
        self.calls = []

    def predict(self, prompt, **kwargs):
        self.calls.append(prompt)
        for marker, text in self.answers:
            if marker in prompt:
                return types.SimpleNamespace(content=text)
        if self.fallback:
            return types.SimpleNamespace(content=self.fallback)
        raise AssertionError(f"Unexpected prompt: {prompt[:120]!r}")


class _RaisingModel:
    def predict(self, prompt, **kwargs):
        raise RuntimeError("model unavailable")


class _FakeKB:
    def __init__(self, default_results=None, results_by_query=None):
        self.default_results = default_results or []
        self.results_by_query = results_by_query or {}
        self.calls = []

    def query(self, query_text, db_id, **kwargs):
        self.calls.append((query_text, db_id, kwargs))
        res = self.results_by_query.get(query_text, self.default_results)
        return {"results": res, "all_results": res}


class _FlakyKB(_FakeKB):
    """对指定子问题抛异常的 KB，用于验证单子问题失败不中断整体流程。"""

    def __init__(self, fail_on=(), default_results=None, results_by_query=None):
        super().__init__(default_results, results_by_query)
        self.fail_on = set(fail_on)

    def query(self, query_text, db_id, **kwargs):
        if query_text in self.fail_on:
            raise ConnectionError("milvus down")
        return super().query(query_text, db_id, **kwargs)


def _res(i, text=None, file_id="f1"):
    return {
        "id": i,
        "distance": 0.9,
        "entity": {"text": text if text is not None else f"chunk {i}", "file_id": file_id},
        "file": {"file_id": file_id, "filename": f"{file_id}.docx", "file_type": "docx"},
    }


def _make_retriever(kb, model):
    kb_instance = kb if isinstance(kb, _FakeKB) else _FakeKB()
    _kb.query = MagicMock(side_effect=kb_instance.query)
    # Configure the SAME MagicMock object the module bound at import time.
    _src_models.select_model.return_value = model
    r = Retriever()
    # Avoid the web-search guard touching a missing web_searcher when disabled.
    r.query_web = MagicMock(return_value={"results": [], "message": "Web search is disabled"})
    return r, kb_instance


def _meta(**overrides):
    base = {
        "db_id": "kb_x",
        "topK": 3,
        "use_graph": False,
        "use_web": False,
        "use_multimodal_kb": False,
        "retrieval_mode": "multi_round",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class ParseQueryListTests(unittest.TestCase):
    def test_json_array(self):
        r = Retriever()
        out = r._parse_query_list('["问题一", "问题二"]')
        self.assertEqual(out, ["问题一", "问题二"])

    def test_json_object_with_questions_key(self):
        r = Retriever()
        out = r._parse_query_list('{"questions": ["a", "b"]}')
        self.assertEqual(out, ["a", "b"])

    def test_code_fence_wrapped(self):
        r = Retriever()
        out = r._parse_query_list('```json\n["x", "y"]\n```')
        self.assertEqual(out, ["x", "y"])

    def test_line_fallback_with_numbering(self):
        r = Retriever()
        out = r._parse_query_list("1. 第一个\n- 第二个\n* 第三个")
        self.assertEqual(out, ["第一个", "第二个", "第三个"])

    def test_empty(self):
        r = Retriever()
        self.assertEqual(r._parse_query_list(""), [])
        self.assertEqual(r._parse_query_list("   "), [])

    def test_json_with_trailing_prose(self):
        r = Retriever()
        out = r._parse_query_list('["问题一", "问题二"]\n以下是说明')
        self.assertEqual(out, ["问题一", "问题二"])

    def test_json_with_leading_prose(self):
        r = Retriever()
        out = r._parse_query_list('以下是子问题：\n["a", "b"]')
        self.assertEqual(out, ["a", "b"])

    def test_empty_json_array_returns_empty(self):
        r = Retriever()
        self.assertEqual(r._parse_query_list("[]"), [])

    def test_json_array_with_trailing_punctuation(self):
        r = Retriever()
        out = r._parse_query_list('["a", "b"]。')
        self.assertEqual(out, ["a", "b"])


class DedupeTests(unittest.TestCase):
    def test_dedupe_by_id(self):
        r = Retriever()
        res = [_res(1), _res(1), _res(2)]
        out = r._dedupe_results(res)
        self.assertEqual([x["id"] for x in out], [1, 2])

    def test_dedupe_by_text_signature(self):
        r = Retriever()
        res = [_res(1, "same"), _res(2, "same"), _res(3, "other")]
        out = r._dedupe_results(res)
        self.assertEqual([x["id"] for x in out], [1, 3])


class FinalRerankTests(unittest.TestCase):
    def test_disabled_rerank_sorts_by_distance(self):
        r = Retriever()
        _cfg["enable_reranker"] = False
        try:
            results = [
                _res(1),  # distance 0.9
                {"id": 2, "distance": 0.5, "entity": {"text": "c2"}, "file": {}},
                {"id": 3, "distance": 0.99, "entity": {"text": "c3"}, "file": {}},
            ]
            out = r._final_rerank("q", results, _meta())
        finally:
            _cfg["enable_reranker"] = True
        self.assertEqual([x["id"] for x in out], [3, 1, 2])

    def test_enabled_rerank_score_alignment_with_empty_text(self):
        r = Retriever()
        _cfg["enable_reranker"] = True
        try:
            calls = {}

            def _fake_get_reranker():
                class _FakeReranker:
                    def compute_score(self, pair, normalize=False):
                        # 3 texts -> scores; entity-less result must not shift alignment
                        calls["pair"] = pair
                        return [0.9, 0.5, 0.7]

                return _FakeReranker()

            with patch("src.core.retriever.get_reranker", side_effect=_fake_get_reranker):
                results = [
                    {"id": 1, "distance": 0.9, "entity": {"text": "t1"}, "file": {}},
                    {"id": 2, "distance": 0.5, "entity": {}, "file": {}},  # 无 text
                    {"id": 3, "distance": 0.8, "entity": {"text": "t3"}, "file": {}},
                    {"id": 4, "distance": 0.7, "entity": {"text": "t4"}, "file": {}},
                ]
                out = r._final_rerank("q", results, _meta())
        finally:
            _cfg["enable_reranker"] = False
        # 无 text 的结果不参与打分，但不应导致后续结果得分错位；阈值 0.1 下该结果被过滤
        scores = {x["id"]: x.get("rerank_score") for x in out}
        self.assertEqual(scores[1], 0.9)
        self.assertEqual(scores[3], 0.5)
        self.assertEqual(scores[4], 0.7)
        self.assertNotIn(2, scores)


class GenerateSubQueriesTests(unittest.TestCase):
    def test_parses_model_output(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"]))])
        r, kb = _make_retriever(_FakeKB(), model)
        out = r.generate_sub_queries("原始问题", [{"role": "user", "content": "历史"}], _meta(), 3)
        self.assertEqual(out, ["q1", "q2", "q3"])

    def test_falls_back_to_original_on_model_error(self):
        r, kb = _make_retriever(_FakeKB(), _RaisingModel())
        out = r.generate_sub_queries("原始问题", [], _meta(), 3)
        self.assertEqual(out, ["原始问题"])


class MultiRoundRetrievalTests(unittest.TestCase):
    def test_stops_after_round_one_when_recall_sufficient(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"]))])
        kb = _FakeKB(default_results=[_res(1), _res(2), _res(3), _res(4), _res(5)])
        r, kb = _make_retriever(kb, model)

        progress = []
        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=3)},
            progress_cb=progress.append,
        )

        # rw_query + 3 generated sub-queries, each searched once; recall >= 3 so no round 2.
        self.assertEqual(len(kb.calls), 4)
        self.assertEqual(refs["multi_round"]["rounds"][0]["round"], 1)
        self.assertEqual(refs["multi_round"]["total_rounds"], 1)
        self.assertLessEqual(len(refs["knowledge_base"]["results"]), 3)
        self.assertTrue(any("第1轮检索完成" in m for m in progress))
        # 改写后的查询本身必须始终参与检索
        self.assertIn("原始问题", [q for q, _, _ in kb.calls])

    def test_expands_when_recall_low(self):
        # Always one deduped chunk -> round 1 recall too low -> round 2 refine.
        model = _ScriptedModel([
            ("GEN", json.dumps(["q1", "q2", "q3"])),
            ("REFINE", json.dumps(["r1", "r2"])),
        ])
        kb = _FakeKB(default_results=[_res(1, "同一段内容")])
        r, kb = _make_retriever(kb, model)

        progress = []
        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=5)},
            progress_cb=progress.append,
        )

        # rw_query + 3 (round 1) + 2 (round 2) sub-queries.
        self.assertEqual(len(kb.calls), 6)
        self.assertEqual(refs["multi_round"]["sub_queries"], ["原始问题", "q1", "q2", "q3", "r1", "r2"])
        self.assertEqual(refs["multi_round"]["total_rounds"], 2)
        self.assertEqual(refs["multi_round"]["rounds"][-1]["round"], 2)
        self.assertTrue(any("第2轮" in m for m in progress))

    def test_aggregates_results_across_subqueries_and_dedupes(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"]))])
        kb = _FakeKB(results_by_query={
            "原始问题": [_res(0, "Z")],
            "q1": [_res(1, "A"), _res(2, "B")],
            "q2": [_res(2, "B"), _res(3, "C")],
        })
        r, kb = _make_retriever(kb, model)

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=10)},
        )
        merged = refs["knowledge_base"]["all_results"]
        ids = sorted(x["id"] for x in merged)
        self.assertEqual(ids, [0, 1, 2, 3])

    def test_final_results_capped_at_topk_and_round_log_shape(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3", "q4"]))])
        kb = _FakeKB(default_results=[_res(1), _res(2), _res(3), _res(4), _res(5), _res(6), _res(7)])
        r, kb = _make_retriever(kb, model)

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=2)},
        )
        self.assertEqual(len(refs["knowledge_base"]["results"]), 2)
        self.assertEqual(refs["multi_round"]["final_recall"], 2)
        self.assertEqual(refs["multi_round"]["mode"], "multi_round")

    def test_single_subquery_failure_does_not_abort(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"]))])
        kb = _FlakyKB(
            fail_on=["q2"],
            results_by_query={"原始问题": [_res(9, "orig")], "q1": [_res(1, "one")]},
        )
        r, kb = _make_retriever(kb, model)

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=10)},
        )
        ids = sorted(x["id"] for x in refs["knowledge_base"]["results"])
        # q2 检索失败被跳过，其余子问题的结果保留
        self.assertEqual(ids, [1, 9])

    def test_short_circuits_when_kb_disabled(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1"]))])
        r, kb = _make_retriever(_FakeKB(), model)
        _cfg["enable_knowledge_base"] = False
        try:
            refs = r.multi_round_retrieval(
                "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta()},
            )
        finally:
            _cfg["enable_knowledge_base"] = True
        self.assertIn("知识库未启用", refs["knowledge_base"]["message"])
        self.assertEqual(refs["multi_round"]["sub_queries"], [])
        self.assertEqual(len(kb.calls), 0)


if __name__ == "__main__":
    unittest.main()
