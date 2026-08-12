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

_ABSENT = object()
_saved_modules = {}
_stub_names = []
_modules_snapshot: set = set()


def _make_module(name, **attrs):
    """Create a stub module and register it under ``name`` in sys.modules.

    P2-2: prior state is saved so the module-level loader can restore the
    global table afterwards (see ``_restore_modules``). Without this, the
    plain ``server`` / ``server.utils`` stubs leak into later test modules and
    break their namespace imports ("server is not a package") in discover
    order -- a class of order-dependent error the review forbids.
    """
    if name not in _stub_names:
        _stub_names.append(name)
    if name not in _saved_modules:
        _saved_modules[name] = sys.modules.get(name, _ABSENT)
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _restore_modules():
    """Remove only the src.*/server.* entries this module created.

    ``_modules_snapshot`` holds the sys.modules keys as they were when
    setUpModule began. Any src/server entry absent from that snapshot was
    created by this module's stubbing or by loading the real retriever /
    graph_retrieval modules, so it is safe to drop. Entries that already
    existed (e.g. real modules other test files imported during collection,
    like ``server.services.model_credentials``) are left untouched -- an
    aggressive "delete every server.* not in _saved_modules" sweep would
    clobber them and break later tests (order-dependent failure).
    """
    for name in list(sys.modules):
        if name not in _modules_snapshot and name.startswith(("src", "server")):
            del sys.modules[name]
    for name in _stub_names:
        prev = _saved_modules.get(name, _ABSENT)
        if prev is _ABSENT:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev


class _StubConfig(dict):
    """Mimics SimpleConfig: attribute read/write goes through dict."""

    def __setattr__(self, key, value):
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def setUpModule():
    """Install stubbed ``src``/``server`` packages, then load the real
    ``graph_retrieval.py`` and ``retriever.py`` against them.

    P2-2: the stubs live only for the duration of this module's tests
    (``tearDownModule`` restores the global module table). The review forbids
    permanently faking public package names at import phase -- the plain
    ``server`` / ``server.utils`` stubs previously leaked into later test
    modules and broke their namespace imports in discover order.
    """
    global _logger, _mm_remote, _cfg, _kb, _src_core, _src_models
    global _helpers_mod, _retriever_mod, Retriever

    _saved_modules.clear()
    _stub_names.clear()
    _modules_snapshot.clear()
    _modules_snapshot.update(sys.modules)

    _logger = MagicMock()
    _make_module("src.utils.logging_config", logger=_logger)
    _make_module("src.utils", logger=_logger)
    _make_module("src.models.rerank_model", get_reranker=MagicMock())
    _make_module("src.core.operators", HyDEOperator=MagicMock())
    _mm_remote = _make_module(
        "server.utils.multimodal_remote",
        format_multimodal_context=MagicMock(return_value=""),
        search_multimodal_remote=MagicMock(return_value={"results": [], "message": ""}),
    )

    # Stubbed prompts: only the multi-round templates we lazy-import are needed.
    _make_module(
        "src.utils.prompts",
        multi_query_generation_prompt="GEN question={question} history={history} count={count}",
        multi_query_assessment_prompt="ASSESS question={question} results={results}",
        multi_query_refine_prompt="REFINE question={question} results={results} assessment={assessment} previous={previous} count={count}",
        knowbase_qa_template="CONTEXT:\n{external}\nQUERY:{query}",
        build_qa_prompt=lambda query, external, params=None, is_item_request=False: external,
        RETRIEVAL_META_KEYS=("db_id", "use_graph", "use_web", "use_multimodal_kb"),
        retrieval_mode_enabled=lambda meta: (
            isinstance(meta, dict)
            and any(meta.get(k) for k in ("db_id", "use_graph", "use_web", "use_multimodal_kb"))
        ),
        build_chat_prompt=lambda query, external, meta, params=None: external,
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
    _make_module(
        "server.utils.multimodal_ops",
        content_version=types.SimpleNamespace(current=0),
        record_query_expansion=MagicMock(),
    )
    _make_module("server", )
    _make_module("server.utils", multimodal_ops=sys.modules["server.utils.multimodal_ops"])

    # Load the real graph_retrieval helpers (pure, no side effects).
    _helpers_path = _PROJECT_ROOT / "src" / "core" / "graph_retrieval.py"
    _spec_helpers = importlib.util.spec_from_file_location("src.core.graph_retrieval", _helpers_path)
    _helpers_mod = importlib.util.module_from_spec(_spec_helpers)
    for _n in ("src.core.graph_retrieval", "src.core.retriever"):
        if _n not in _stub_names:
            _stub_names.append(_n)
        _saved_modules.setdefault(_n, sys.modules.get(_n, _ABSENT))
    sys.modules["src.core.graph_retrieval"] = _helpers_mod
    _spec_helpers.loader.exec_module(_helpers_mod)

    # Load the real retriever module against the stubs.
    _retriever_path = _PROJECT_ROOT / "src" / "core" / "retriever.py"
    _spec = importlib.util.spec_from_file_location("src.core.retriever", _retriever_path)
    _retriever_mod = importlib.util.module_from_spec(_spec)
    sys.modules["src.core.retriever"] = _retriever_mod
    _spec.loader.exec_module(_retriever_mod)

    Retriever = _retriever_mod.Retriever


def tearDownModule():
    """Remove every stub this module installed so later test modules in the
    same process see the real packages (or their absence)."""
    _restore_modules()


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


def _mm_res(i, text, file_id="f1", page=1):
    return {
        "id": i,
        "rank": i,
        "fileId": file_id,
        "fileName": f"{file_id}.pdf",
        "page": page,
        "score": 0.9,
        "contentType": "text",
        "images": [],
        "text": text,
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


def _assess_ok(reason="检索到足够内容"):
    return ("ASSESS", json.dumps({"has_value": True, "need_more": False, "next_keywords": [], "reason": reason}))


def _assess_more(reason="内容不足，需继续检索", keywords=None):
    return (
        "ASSESS",
        json.dumps({
            "has_value": False,
            "need_more": True,
            "next_keywords": keywords or ["补充关键词"],
            "reason": reason,
        }),
    )


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

            with patch.object(_retriever_mod, "get_reranker", side_effect=_fake_get_reranker):
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
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"])), _assess_ok()])
        r, kb = _make_retriever(_FakeKB(), model)
        out = r.generate_sub_queries("原始问题", [{"role": "user", "content": "历史"}], _meta(), 3)
        self.assertEqual(out, ["q1", "q2", "q3"])

    def test_falls_back_to_original_on_model_error(self):
        r, kb = _make_retriever(_FakeKB(), _RaisingModel())
        out = r.generate_sub_queries("原始问题", [], _meta(), 3)
        self.assertEqual(out, ["原始问题"])


class SubQueryNormalizeTests(unittest.TestCase):
    def test_normalize_strips_whitespace_and_trailing_punctuation(self):
        r = Retriever()
        key = r._normalize_sub_query_key("  井身结构设计的关键内容？ ")
        self.assertEqual(key, "井身结构设计的关键内容")

    def test_near_duplicate_keys_equal(self):
        r = Retriever()
        self.assertEqual(
            r._normalize_sub_query_key("压裂施工参数有哪些？"),
            r._normalize_sub_query_key("压裂施工参数有哪些"),
        )


class MultimodalLimitTests(unittest.TestCase):
    def test_limit_caps_total_count(self):
        r = Retriever()
        results = [{"fileId": f"f{i}", "page": i, "text": "t"} for i in range(10)]
        out = r._limit_multimodal_results(results, max_items=3, max_text_chars=100, max_images=5)
        self.assertEqual(len(out), 3)

    def test_limit_truncates_text_and_caps_images(self):
        r = Retriever()
        results = [
            {
                "fileId": "f1",
                "page": 1,
                "text": "x" * 100,
                "images": [{"path": f"i{n}"} for n in range(5)],
            }
        ]
        out = r._limit_multimodal_results(results, max_items=10, max_text_chars=10, max_images=2)
        self.assertLessEqual(len(out[0]["text"]), 11)  # 截断 + 省略号
        self.assertEqual(len(out[0]["images"]), 2)


class MultiRoundRetrievalTests(unittest.TestCase):
    def setUp(self):
        # C2 短 TTL 缓存是模块级单例，必须在每个用例前清空，避免跨用例串数据
        _retriever_mod._MMSearchCache.clear()

    def test_stops_after_round_one_when_recall_sufficient(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"])), _assess_ok()])
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
            _assess_more(),
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
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"])), _assess_ok()])
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
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3", "q4"])), _assess_ok()])
        kb = _FakeKB(default_results=[_res(1), _res(2), _res(3), _res(4), _res(5), _res(6), _res(7)])
        r, kb = _make_retriever(kb, model)

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=2)},
        )
        self.assertEqual(len(refs["knowledge_base"]["results"]), 2)
        self.assertEqual(refs["multi_round"]["final_recall"], 2)
        self.assertEqual(refs["multi_round"]["mode"], "multi_round")

    def test_single_subquery_failure_does_not_abort(self):
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3"])), _assess_ok()])
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

    def test_first_round_empty_forces_more_rounds(self):
        """第一轮一个结果都没检索到，即使模型误判 has_value，也要强制继续多检索几轮。"""
        model = _ScriptedModel([
            ("GEN", json.dumps(["q1"])),
            _assess_ok("误判：认为内容足够"),  # 模型误判，但 recall=0 守卫会强制继续
            ("REFINE", json.dumps(["r1"])),
        ])
        kb = _FakeKB(default_results=[])  # 始终检索不到
        r, kb = _make_retriever(kb, model)

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(topK=5)},
        )
        self.assertEqual(refs["multi_round"]["rounds"][0]["recall"], 0)
        # recall=0 强制多检索：至少进入第2轮
        self.assertGreaterEqual(refs["multi_round"]["total_rounds"], 2)
        # 第1轮 rw_query+q1，第2轮 r1
        self.assertEqual(len(kb.calls), 3)

    def test_assess_results_parses_model_output(self):
        model = _ScriptedModel([_assess_more("缺少参数", ["压裂液类型"])])
        r, kb = _make_retriever(_FakeKB(), model)
        result = r.assess_results("问题", [_res(1), _mm_res(2, "mm")], _meta())
        self.assertFalse(result["has_value"])
        self.assertTrue(result["need_more"])
        self.assertEqual(result["next_keywords"], ["压裂液类型"])
        self.assertIn("缺少参数", result["reason"])

    def test_multimodal_subqueries_retrieve_remote_kb(self):
        """多轮模式下每个子问题都应调用远程多模态知识库检索并合并去重。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")], "q1": [_res(1, "a")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            mapping = {
                "原始问题": [_mm_res(1, "mm-orig")],
                "q1": [_mm_res(2, "mm-q1")],
                "q2": [_mm_res(2, "mm-q1")],  # 与 q1 文本重复，验证去重
            }
            return {
                "results": mapping.get(query, []),
                "message": "",
                "kb_id": "kb",
                "kb_name": "mm",
                "file_id": None,
                "base_url": "http://remote",
                "status": "ok",
            }

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        # 改写查询本身 + 每个生成的子问题都触发了一次远程多模态检索
        self.assertEqual(set(mm_calls), {"原始问题", "q1", "q2"})
        results = refs["multimodal_knowledge_base"]["results"]
        self.assertEqual([x["text"] for x in results], ["mm-orig", "mm-q1"])
        self.assertEqual(refs["multimodal_knowledge_base"]["kb_id"], "kb")

    def test_multimodal_failure_does_not_abort(self):
        """远程多模态检索失败只跳过该子问题，不影响其它子问题与整体流程。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")], "q1": [_res(1, "a")]})
        r, kb = _make_retriever(kb, model)

        def _flaky_mm(query, meta=None):
            if query == "q1":
                raise ConnectionError("remote down")
            return {"results": [_mm_res(1, f"mm-{query}")], "message": "", "kb_id": "kb", "kb_name": "mm"}

        _mm_remote.search_multimodal_remote.side_effect = _flaky_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        texts = [x["text"] for x in refs["multimodal_knowledge_base"]["results"]]
        self.assertIn("mm-原始问题", texts)
        self.assertIn("mm-q2", texts)
        self.assertNotIn("mm-q1", texts)

    def test_multimodal_budget_caps_remote_calls(self):
        """每次问答的远端检索总预算限制远端调用次数，超出时停止扩展并标记状态。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2", "q3", "q4"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")], "q1": [_res(1, "a")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            return {"results": [_mm_res(1, f"mm-{query}")], "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok"}

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm
        _cfg["multi_query_multimodal_budget"] = 2
        try:
            refs = r.multi_round_retrieval(
                "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
            )
        finally:
            _cfg.pop("multi_query_multimodal_budget", None)

        # 预算=2：基线 1 次 + 扩展最多 1 次；达到预算后停止扩展
        self.assertLessEqual(len(mm_calls), 2)
        self.assertEqual(refs["multimodal_knowledge_base"]["status"], "budget_reached")
        self.assertEqual(refs["multimodal_knowledge_base"]["budget_limit"], 2)
        self.assertLessEqual(refs["multimodal_knowledge_base"]["budget_used"], 2)

    def test_multimodal_degraded_keeps_chat_with_clear_message(self):
        """J.4：远端自动降级（熔断/暂不可用）时普通聊天继续工作并收到明确提示。

        降级快速失败不消耗远端预算；整体状态聚合为 degraded（而非伪装成空/普通失败），
        消息明确；普通知识库检索不受影响。
        """
        model = _ScriptedModel([("GEN", json.dumps(["q1"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")], "q1": [_res(1, "a")]})
        r, kb = _make_retriever(kb, model)

        def _degraded_mm(query, meta=None):
            return {
                "results": [],
                "message": "多模态远端暂不可用（已自动降级，稍后自动恢复）",
                "kb_id": "kb", "kb_name": "mm", "status": "degraded",
            }

        _mm_remote.search_multimodal_remote.side_effect = _degraded_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        mm = refs["multimodal_knowledge_base"]
        self.assertEqual(mm["status"], "degraded")
        self.assertIn("已自动降级", mm.get("message") or "")
        self.assertEqual(mm["budget_used"], 0, "降级快速失败不消耗远端预算")
        # 普通知识库检索照常工作（聊天继续）
        self.assertTrue(refs["knowledge_base"]["results"])

    def test_multimodal_near_duplicate_subqueries_deduped(self):
        """近似重复的子问题（仅尾部标点/空白不同）只触发一次远端检索。"""
        model = _ScriptedModel([
            ("GEN", json.dumps(["井身结构设计的关键内容", "井身结构设计的关键内容？"])),
            _assess_ok(),
        ])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            return {"results": [_mm_res(1, f"mm-{query}")], "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok"}

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        # 改写查询本身 + 规范化去重后的唯一子问题
        self.assertEqual(set(mm_calls), {"原始问题", "井身结构设计的关键内容"})

    def test_multimodal_deadline_stops_expansion(self):
        """整轮检索 deadline=0 时，基线检索后立即停止扩展，状态为 deadline_reached。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            return {"results": [_mm_res(1, f"mm-{query}")], "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok"}

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm
        _cfg["multi_query_deadline_seconds"] = 0
        try:
            refs = r.multi_round_retrieval(
                "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
            )
        finally:
            _cfg.pop("multi_query_deadline_seconds", None)

        self.assertEqual(len(mm_calls), 1)
        self.assertEqual(refs["multimodal_knowledge_base"]["status"], "deadline_reached")

    def test_multimodal_no_kb_selected_state_propagates(self):
        """未选择知识库返回独立状态 no_kb_selected，不伪装成空结果。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        def _no_kb_mm(query, meta=None):
            return {"results": [], "message": "未选择多模态知识库", "kb_id": None, "kb_name": None, "status": "no_kb_selected"}

        _mm_remote.search_multimodal_remote.side_effect = _no_kb_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        self.assertEqual(refs["multimodal_knowledge_base"]["status"], "no_kb_selected")
        self.assertEqual(refs["multimodal_knowledge_base"]["results"], [])

    def test_multimodal_empty_is_distinct_state(self):
        """检索为空返回独立状态 empty，不与远端失败混淆。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        def _empty_mm(query, meta=None):
            return {"results": [], "message": "未检索到结果", "kb_id": "kb", "kb_name": "mm", "status": "empty"}

        _mm_remote.search_multimodal_remote.side_effect = _empty_mm

        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
        )
        self.assertEqual(refs["multimodal_knowledge_base"]["status"], "empty")
        self.assertEqual(refs["multimodal_knowledge_base"]["results"], [])

    def test_multimodal_merged_results_limited(self):
        """合并结果限制总条数与单条图片数（服务端可配置）。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1", "q2"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")], "q1": [_res(1, "a")]})
        r, kb = _make_retriever(kb, model)

        def _big_mm(query, meta=None):
            return {
                "results": [
                    {
                        "id": i, "rank": i, "fileId": "f1", "fileName": "f1.pdf", "page": i,
                        "score": 0.9, "contentType": "text", "text": f"text-{query}-{i}",
                        "images": [{"path": f"i{n}"} for n in range(5)],
                    }
                    for i in range(5)
                ],
                "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok",
            }

        _mm_remote.search_multimodal_remote.side_effect = _big_mm
        _cfg["multi_query_mm_max_items"] = 2
        _cfg["multi_query_mm_max_images"] = 1
        try:
            refs = r.multi_round_retrieval(
                "原始问题", [], {"query": "原始问题", "history": [], "meta": _meta(use_multimodal_kb=True, topK=10)},
            )
        finally:
            _cfg.pop("multi_query_mm_max_items", None)
            _cfg.pop("multi_query_mm_max_images", None)

        results = refs["multimodal_knowledge_base"]["results"]
        self.assertLessEqual(len(results), 2)
        for item in results:
            self.assertLessEqual(len(item["images"]), 1)

    def test_multimodal_cache_hits_skip_network(self):
        """同一会话短时间重复问题命中短 TTL 缓存，不再消耗远端预算。"""
        model = _ScriptedModel([("GEN", json.dumps(["q1"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            return {"results": [_mm_res(1, "cached-text")], "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok"}

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm

        meta = _meta(use_multimodal_kb=True, topK=10)
        r.multi_round_retrieval("原始问题", [], {"query": "原始问题", "history": [], "meta": meta})
        first_count = len(mm_calls)
        self.assertGreater(first_count, 0)
        r.multi_round_retrieval("原始问题", [], {"query": "原始问题", "history": [], "meta": meta})
        # 第二次进入：基线/子问题全部命中缓存，不发网络请求
        self.assertEqual(len(mm_calls), first_count)

    def test_multimodal_user_cancel_is_distinct_state(self):
        """用户取消返回独立状态 user_cancelled，不再发起远端调用。"""
        import threading

        model = _ScriptedModel([("GEN", json.dumps(["q1"])), _assess_ok()])
        kb = _FakeKB(results_by_query={"原始问题": [_res(0, "orig")]})
        r, kb = _make_retriever(kb, model)

        mm_calls = []

        def _fake_mm(query, meta=None):
            mm_calls.append(query)
            return {"results": [_mm_res(1, "x")], "message": "", "kb_id": "kb", "kb_name": "mm", "status": "ok"}

        _mm_remote.search_multimodal_remote.side_effect = _fake_mm

        cancel = threading.Event()
        cancel.set()
        meta = _meta(use_multimodal_kb=True, topK=10)
        meta["_retrieval_cancelled"] = cancel
        refs = r.multi_round_retrieval(
            "原始问题", [], {"query": "原始问题", "history": [], "meta": meta},
        )
        self.assertEqual(len(mm_calls), 0)
        self.assertEqual(refs["multimodal_knowledge_base"]["status"], "user_cancelled")


if __name__ == "__main__":
    unittest.main()
