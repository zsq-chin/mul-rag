"""Tests for graph retrieval helpers and integration (Task 12).

Covers:
  1. normalize_entities: trim, blank removal, order-preserving dedup, bound
  2. rank_unique_relations: numeric score desc, dedup by (source,relation,target),
     deterministic tie-break, bounded, stable ref_id assignment,
     invalid/NaN scores, incomplete triples
  3. format_graph_context: [G#] lines, descriptions, character cap, no partial refs
  4. Graph retrieval gate: use_graph AND enable_knowledge_graph, independent of KB
  5. Empty-entity one-call fallback using original_query
  6. Configured args and hard caps (20 entities, 3 hops, 100 relations)
  7. Structured error degradation on graph exception
  8. Stable IDs shared by context and sidebar
  9. Graph start/is_running gate independent of enable_knowledge_base
 10. jsonl_file_add_entity uses detected encoding (source inspection)
 11. format_query_result_to_graph structured + legacy rows
 12. Config graph params with safe defaults
"""

import importlib.util
import inspect
import os
import re
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Build minimal stub modules so that:
#   - src/__init__ (real Milvus/Neo4j init) is NEVER imported
#   - retriever.py and graphbase.py load in <0.1s via importlib
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sentinel values that must NOT appear as hardcoded defaults in production code.
FORBIDDEN_SECRETS = ["CUPer123456", "0123456789", "defaultkey", "app_password", "minioadmin"]

# -- tiny stubs for heavy transitive dependencies --------------------------

_stub_torch = types.ModuleType("torch")
_stub_torch.cuda = MagicMock()
_stub_torch.cuda.is_available.return_value = False

_stub_neo4j = types.ModuleType("neo4j")
_stub_neo4j_gd = types.ModuleType("neo4j.GraphDatabase")
_stub_neo4j_gd.driver = MagicMock()
_stub_neo4j.GraphDatabase = _stub_neo4j_gd

_stub_chardet = types.ModuleType("chardet")
_stub_chardet.detect = MagicMock(return_value={"encoding": "utf-8"})

_stub_requests = types.ModuleType("requests")

_stub_rerank = types.ModuleType("src.models.rerank_model")
_stub_rerank.get_reranker = MagicMock()
# select_model will be added to _stub_src_models below (after it is created).

_stub_log_cfg = types.ModuleType("src.utils.logging_config")
_stub_logger = MagicMock()
_stub_log_cfg.logger = _stub_logger

_stub_operators = types.ModuleType("src.core.operators")
_stub_operators.HyDEOperator = MagicMock()

_stub_mm_remote = types.ModuleType("server.utils.multimodal_remote")
_stub_mm_remote.format_multimodal_context = MagicMock(return_value="")
_stub_mm_remote.search_multimodal_remote = MagicMock(return_value={})

_stub_mm_ops = types.ModuleType("server.utils.multimodal_ops")
_stub_mm_ops.content_version = types.SimpleNamespace(current=0)
_stub_mm_ops.should_allow_request = MagicMock(return_value=True)
_stub_mm_ops.record_query_expansion = MagicMock()

# -- src.config stub (SimpleConfig-like: dict + attr access) ---------------

class _StubConfig(dict):
    """Mimics SimpleConfig: attribute read/write goes through dict."""

    def __setattr__(self, key, value):
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

_stub_config_mod = types.ModuleType("src.config")
_stub_config_mod.Config = type("Config", (_StubConfig,), {})

# -- src.utils stub package ------------------------------------------------

_stub_src_utils = types.ModuleType("src.utils")
_stub_src_utils.__path__ = []
_stub_src_utils.logger = _stub_logger

_stub_src_utils_prompts = types.ModuleType("src.utils.prompts")
# construct_query 委托 build_chat_prompt 构建提示词；stub 需镜像真实模块的接口
_stub_src_utils_prompts.NO_EVIDENCE_MARKER = "（未检索到有效参考资料）"
_stub_src_utils_prompts.knowbase_qa_template = "CONTEXT:\n{external}\nQUERY:{query}"
_stub_src_utils_prompts.knowbase_itemGen_template = "{external}\n{params}"
_stub_src_utils_prompts.RETRIEVAL_META_KEYS = ("db_id", "use_graph", "use_web", "use_multimodal_kb")


def _stub_retrieval_mode_enabled(meta):
    if not isinstance(meta, dict):
        return False
    return any(meta.get(k) for k in _stub_src_utils_prompts.RETRIEVAL_META_KEYS)


def _stub_build_qa_prompt(query, external, params=None, is_item_request=False):
    if is_item_request:
        return _stub_src_utils_prompts.knowbase_itemGen_template.format(
            external=external or "", params=params
        )
    if not external or not str(external).strip():
        external = _stub_src_utils_prompts.NO_EVIDENCE_MARKER
    return _stub_src_utils_prompts.knowbase_qa_template.format(external=external, query=query)


def _stub_build_chat_prompt(query, external, meta, params=None):
    if isinstance(meta, dict) and meta.get("isItemRequest"):
        return _stub_build_qa_prompt(query, external, params=params, is_item_request=True)
    if not _stub_retrieval_mode_enabled(meta):
        return query
    return _stub_build_qa_prompt(query, external, params=params)


_stub_src_utils_prompts.build_qa_prompt = _stub_build_qa_prompt
_stub_src_utils_prompts.retrieval_mode_enabled = _stub_retrieval_mode_enabled
_stub_src_utils_prompts.build_chat_prompt = _stub_build_chat_prompt

# -- src stub (bare; never triggers real __init__) -------------------------

_stub_src = types.ModuleType("src")
_stub_src.__path__ = [str(_PROJECT_ROOT / "src")]
# retriever.py line 3: ``from src import config, knowledge_base, graph_base``
# These must be real attributes on the module object.
_stub_src.config = _stub_config_mod.Config()
_stub_src.knowledge_base = MagicMock()
_stub_src.graph_base = MagicMock()

# -- src.core stub package -------------------------------------------------

_stub_src_core = types.ModuleType("src.core")
_stub_src_core.__path__ = [str(_PROJECT_ROOT / "src" / "core")]

# -- src.models stub package ------------------------------------------------

_stub_src_models = types.ModuleType("src.models")
_stub_src_models.__path__ = []
_stub_src_models.select_model = MagicMock()

# -- server stubs -----------------------------------------------------------

_stub_server = types.ModuleType("server")
_stub_server.__path__ = []
_stub_server_utils = types.ModuleType("server.utils")
_stub_server_utils.__path__ = []

# ---------------------------------------------------------------------------
# Load graph_retrieval.py (pure helpers, no side effects)
# ---------------------------------------------------------------------------

_helpers_path = _PROJECT_ROOT / "src" / "core" / "graph_retrieval.py"
_spec_helpers = importlib.util.spec_from_file_location(
    "src.core.graph_retrieval", _helpers_path
)
_helpers_mod = importlib.util.module_from_spec(_spec_helpers)
sys.modules["src.core.graph_retrieval"] = _helpers_mod
_spec_helpers.loader.exec_module(_helpers_mod)

normalize_entities = _helpers_mod.normalize_entities
rank_unique_relations = _helpers_mod.rank_unique_relations
format_graph_context = _helpers_mod.format_graph_context

# ---------------------------------------------------------------------------
# Load retriever.py in isolation
# ---------------------------------------------------------------------------

_saved_modules = {}


def _register_stub(name, mod):
    """Save existing module (if any) and register the stub."""
    if name in sys.modules:
        _saved_modules[name] = sys.modules[name]
    sys.modules[name] = mod


def _restore_modules():
    """Restore original module state.

    Every name in ``_stub_names`` is either restored to its pre-existing
    value or removed entirely -- including entries like ``torch``,
    ``neo4j``, ``chardet``, and ``requests`` that do *not* start with
    ``src`` or ``server``.  Side-effect ``src``/``server`` sub-module
    imports are also cleaned up.
    """
    # 1. Remove side-effect imports of src/server submodules.
    for name in list(sys.modules):
        if name not in _saved_modules and name.startswith(("src", "server")):
            del sys.modules[name]
    # 2. Restore or remove every name we explicitly stubbed.
    for name in _stub_names:
        if name in _saved_modules:
            sys.modules[name] = _saved_modules[name]
        else:
            sys.modules.pop(name, None)


_retriever_path = _PROJECT_ROOT / "src" / "core" / "retriever.py"
_stub_names = [
    "src", "src.core", "src.config", "src.utils", "src.utils.logging_config",
    "src.utils.prompts", "src.models", "src.models.rerank_model",
    "src.core.operators", "server", "server.utils", "server.utils.multimodal_remote",
    "server.utils.multimodal_ops",
    "torch", "neo4j", "neo4j.GraphDatabase", "chardet", "requests",
]

_stub_map = {
    "src": _stub_src,
    "src.core": _stub_src_core,
    "src.config": _stub_config_mod,
    "src.utils": _stub_src_utils,
    "src.utils.logging_config": _stub_log_cfg,
    "src.utils.prompts": _stub_src_utils_prompts,
    "src.models": _stub_src_models,
    "src.models.rerank_model": _stub_rerank,
    "src.core.operators": _stub_operators,
    "server": _stub_server,
    "server.utils": _stub_server_utils,
    "server.utils.multimodal_remote": _stub_mm_remote,
    "server.utils.multimodal_ops": _stub_mm_ops,
    "torch": _stub_torch,
    "neo4j": _stub_neo4j,
    "neo4j.GraphDatabase": _stub_neo4j_gd,
    "chardet": _stub_chardet,
    "requests": _stub_requests,
}

for _name in _stub_names:
    _register_stub(_name, _stub_map[_name])

_spec_retriever = importlib.util.spec_from_file_location(
    "src.core.retriever", _retriever_path
)
_retriever_mod = importlib.util.module_from_spec(_spec_retriever)
sys.modules["src.core.retriever"] = _retriever_mod
_spec_retriever.loader.exec_module(_retriever_mod)

# Keep live references so tests can patch directly (no string-based decorators).
# The production module attributes ``config`` and ``graph_base`` are stubs;
# tests replace them with per-test mocks via patch.object(_retriever_mod, ...).
Retriever = _retriever_mod.Retriever
_retriever_config = _retriever_mod.config
_retriever_graph_base = _retriever_mod.graph_base

# ---------------------------------------------------------------------------
# Load graphbase.py in isolation
# ---------------------------------------------------------------------------

_graphbase_path = _PROJECT_ROOT / "src" / "core" / "graphbase.py"
_spec_graphbase = importlib.util.spec_from_file_location(
    "src.core.graphbase", _graphbase_path
)
_graphbase_mod = importlib.util.module_from_spec(_spec_graphbase)
sys.modules["src.core.graphbase"] = _graphbase_mod
_spec_graphbase.loader.exec_module(_graphbase_mod)

GraphDatabase = _graphbase_mod.GraphDatabase
_gb_config = _graphbase_mod.config
_gb_GD = _graphbase_mod.GD

# The loaded target modules retain their stub references. Restore the global
# module table so this test file cannot affect later tests in discovery order.
_restore_modules()

# =========================================================================
# 1. normalize_entities
# =========================================================================


class TestNormalizeEntities(unittest.TestCase):

    def test_trim_and_remove_blanks(self):
        self.assertEqual(
            normalize_entities([" 井筒 ", " ", "", "套管"], 10),
            ["井筒", "套管"],
        )

    def test_preserve_first_seen_order_and_dedup(self):
        result = normalize_entities(
            ["套管", "井筒", "套管", "水泥"], 10
        )
        self.assertEqual(result, ["套管", "井筒", "水泥"])

    def test_respects_bound(self):
        result = normalize_entities(["A", "B", "C", "D"], 2)
        self.assertEqual(result, ["A", "B"])

    def test_empty_input_returns_empty(self):
        self.assertEqual(normalize_entities([], 10), [])

    def test_all_blank_returns_empty(self):
        self.assertEqual(normalize_entities(["", "  ", None], 10), [])

    def test_bound_zero_returns_empty(self):
        self.assertEqual(normalize_entities(["A", "B"], 0), [])

    def test_none_items_skipped(self):
        self.assertEqual(normalize_entities([None, "A", None, "B"], 10), ["A", "B"])


# =========================================================================
# 2. rank_unique_relations
# =========================================================================


class TestRankUniqueRelations(unittest.TestCase):

    def _rows(self):
        return [
            {
                "source": "井筒", "target": "套管",
                "relation": "包含", "score": 0.7,
                "source_desc": "井筒描述",
                "target_desc": "套管描述",
                "relation_desc": "包含关系描述",
            },
            {
                "source": "井筒", "target": "套管",
                "relation": "包含", "score": 0.9,
                "source_desc": "井筒描述",
                "target_desc": "套管描述",
                "relation_desc": "包含关系描述",
            },
            {
                "source": "水泥", "target": "套管",
                "relation": "固结", "score": 0.8,
                "source_desc": "水泥描述",
                "target_desc": "套管描述",
                "relation_desc": "固结关系",
            },
        ]

    def test_dedup_keeps_highest_score(self):
        ranked = rank_unique_relations(self._rows(), 10)
        contains = [r for r in ranked if r["relation"] == "包含"]
        self.assertEqual(len(contains), 1)
        self.assertEqual(contains[0]["score"], 0.9)

    def test_sorted_by_score_descending(self):
        ranked = rank_unique_relations(self._rows(), 10)
        scores = [r["score"] for r in ranked]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_stable_ref_ids_assigned(self):
        ranked = rank_unique_relations(self._rows(), 10)
        ref_ids = [r["ref_id"] for r in ranked]
        self.assertEqual(ref_ids, ["G1", "G2"])

    def test_respects_bound(self):
        rows = [
            {"source": f"S{i}", "target": f"T{i}", "relation": f"R{i}",
             "score": 1.0 - i * 0.1}
            for i in range(5)
        ]
        ranked = rank_unique_relations(rows, 2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["ref_id"], "G1")
        self.assertEqual(ranked[1]["ref_id"], "G2")

    def test_deterministic_tie_break_by_source_then_relation_then_target(self):
        rows = [
            {"source": "B", "target": "X", "relation": "R1", "score": 0.5},
            {"source": "A", "target": "X", "relation": "R1", "score": 0.5},
        ]
        ranked = rank_unique_relations(rows, 10)
        self.assertEqual(ranked[0]["source"], "A")
        self.assertEqual(ranked[1]["source"], "B")

    def test_descriptions_preserved(self):
        rows = [
            {"source": "S", "target": "T", "relation": "R", "score": 1.0,
             "source_desc": "sd", "target_desc": "td", "relation_desc": "rd"},
        ]
        ranked = rank_unique_relations(rows, 10)
        self.assertEqual(ranked[0]["source_desc"], "sd")
        self.assertEqual(ranked[0]["target_desc"], "td")
        self.assertEqual(ranked[0]["relation_desc"], "rd")

    def test_sidebar_metadata_is_preserved(self):
        rows = [{
            "source": "S",
            "source_id": "source-1",
            "source_properties": {"name": "S", "description": "sd"},
            "target": "T",
            "target_id": "target-1",
            "target_properties": {"name": "T", "description": "td"},
            "relation": "R",
            "relation_desc": "rd",
            "score": 0.9,
        }]
        ranked = rank_unique_relations(rows, 10)
        self.assertIn("source_id", ranked[0])
        self.assertIn("target_id", ranked[0])
        self.assertIn("source_properties", ranked[0])
        self.assertIn("target_properties", ranked[0])
        self.assertEqual(ranked[0]["source_id"], "source-1")
        self.assertEqual(ranked[0]["target_id"], "target-1")
        self.assertEqual(ranked[0]["source_properties"]["description"], "sd")
        self.assertEqual(ranked[0]["target_properties"]["description"], "td")

    def test_missing_descriptions_default_empty(self):
        rows = [{"source": "S", "target": "T", "relation": "R", "score": 1.0}]
        ranked = rank_unique_relations(rows, 10)
        self.assertEqual(ranked[0]["source_desc"], "")
        self.assertEqual(ranked[0]["target_desc"], "")
        self.assertEqual(ranked[0]["relation_desc"], "")

    def test_empty_rows_returns_empty(self):
        self.assertEqual(rank_unique_relations([], 10), [])

    def test_invalid_nan_scores_handled(self):
        """Rows with None or NaN scores should not crash and should sort safely."""
        import math
        rows = [
            {"source": "A", "target": "B", "relation": "R", "score": None},
            {"source": "C", "target": "D", "relation": "R", "score": 0.5},
        ]
        try:
            ranked = rank_unique_relations(rows, 10)
        except Exception as exc:
            self.fail(f"invalid scores must be handled without raising: {exc}")
        self.assertTrue(len(ranked) >= 1)
        # NaN scores must not appear as NaN in output (should be coerced or filtered)
        for r in ranked:
            self.assertFalse(
                math.isnan(r["score"]),
                f"NaN score leaked through: {r}",
            )

    def test_incomplete_triple_fields_are_dropped(self):
        """Rows missing source, target, or relation cannot form references."""
        rows = [
            {"score": 1.0},  # all triple fields missing
        ]
        ranked = rank_unique_relations(rows, 10)
        self.assertEqual(ranked, [])


# =========================================================================
# 3. format_graph_context
# =========================================================================


class TestFormatGraphContext(unittest.TestCase):

    def test_contains_ref_id_source_relation_target(self):
        ranked = [
            {"ref_id": "G1", "source": "井筒", "target": "套管",
             "relation": "包含", "score": 0.9,
             "source_desc": "", "target_desc": "", "relation_desc": ""},
        ]
        text = format_graph_context(ranked)
        self.assertIn("[G1]", text)
        self.assertIn("井筒", text)
        self.assertIn("套管", text)
        self.assertIn("包含", text)
        self.assertIn("--包含-->", text)

    def test_descriptions_included_when_present(self):
        ranked = [
            {"ref_id": "G1", "source": "S", "target": "T", "relation": "R",
             "score": 1.0, "source_desc": "sdesc", "target_desc": "tdesc",
             "relation_desc": "rdesc"},
        ]
        text = format_graph_context(ranked)
        self.assertIn("sdesc", text)
        self.assertIn("tdesc", text)
        self.assertIn("rdesc", text)

    def test_character_cap_enforced(self):
        ranked = [
            {"ref_id": f"G{i}", "source": f"S{i}", "target": f"T{i}",
             "relation": f"R{i}很长的关系名称"
             "增加字符数",
             "score": 1.0 - i * 0.01,
             "source_desc": "很长的源描述信息",
             "target_desc": "很长的目标描述信息",
             "relation_desc": "很长的关系描述"}
            for i in range(1, 21)
        ]
        cap = 200
        text = format_graph_context(ranked, max_chars=cap)
        self.assertLessEqual(len(text), cap)

    def test_cap_does_not_produce_partial_refs(self):
        ranked = [
            {"ref_id": f"G{i}", "source": f"S{i}", "target": f"T{i}",
             "relation": f"R{i}", "score": 1.0 - i * 0.01,
             "source_desc": "d", "target_desc": "d", "relation_desc": "d"}
            for i in range(1, 11)
        ]
        text = format_graph_context(ranked, max_chars=150)
        refs_in_text = re.findall(r'\[G\d+\]', text)
        for ref in refs_in_text:
            for line in text.split('\n'):
                if ref in line:
                    self.assertIn('--', line)
                    self.assertIn('-->', line)

    def test_empty_ranked_returns_empty_string(self):
        self.assertEqual(format_graph_context([]), "")

    def test_whole_line_char_cap_no_partial_lines(self):
        """Character cap must not cut in the middle of a line."""
        ranked = [
            {"ref_id": f"G{i}", "source": f"S{i}",
             "target": f"T{i}", "relation": f"R{i}",
             "score": 1.0 - i * 0.01,
             "source_desc": "", "target_desc": "", "relation_desc": ""}
            for i in range(1, 11)
        ]
        text = format_graph_context(ranked, max_chars=80)
        for line in text.split('\n'):
            if line:
                self.assertIn('[', line)
                self.assertIn(']', line)
                self.assertIn('-->', line)


# =========================================================================
# 4-7. Retriever integration (mocks at Neo4j/model boundary)
# =========================================================================


class TestGraphRetrieverIntegration(unittest.TestCase):
    """Integration tests that exercise the Retriever.graph path with mocked
    Neo4j and model calls.  Uses real helper code from graph_retrieval."""

    def setUp(self):
        self._orig_cfg = getattr(_retriever_mod, "config", None)
        self._orig_gb = getattr(_retriever_mod, "graph_base", None)

    def tearDown(self):
        _retriever_mod.config = self._orig_cfg
        _retriever_mod.graph_base = self._orig_gb

    def _make_retriever(self, mock_cfg, mock_gb):
        _retriever_mod.config = mock_cfg
        _retriever_mod.graph_base = mock_gb
        r = Retriever.__new__(Retriever)
        return r

    def _base_cfg(self, **overrides):
        cfg = _StubConfig({
            "enable_knowledge_graph": False,
            "enable_knowledge_base": False,
            "enable_web_search": False,
            "graph_similarity_threshold": 0.5,
            "graph_hops": 2,
            "graph_max_entities": 5,
            "graph_max_relations": 10,
            "graph_context_max_chars": 2000,
        })
        for k, v in overrides.items():
            cfg[k] = v
        return cfg

    def _query_kwargs(self, graph_base):
        self.assertGreater(
            graph_base.query_node.call_count,
            0,
            "graph retrieval must call graph_base.query_node",
        )
        return graph_base.query_node.call_args.kwargs

    def test_gate_requires_both_use_graph_and_enable_knowledge_graph(self):
        """use_graph=True + enable_knowledge_graph=False => no graph query."""
        cfg = self._base_cfg(enable_knowledge_graph=False)
        gb = MagicMock()
        r = self._make_retriever(cfg, gb)
        r.reco_entities = MagicMock(return_value=["井筒"])
        refs = {
            "meta": {"use_graph": True},
            "entities": ["井筒"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        gb.query_node.assert_not_called()

    def test_gate_independent_of_enable_knowledge_base(self):
        """Graph gate does NOT depend on enable_knowledge_base."""
        cfg = self._base_cfg(enable_knowledge_graph=True,
                             enable_knowledge_base=False)
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        r.reco_entities = MagicMock(return_value=["井筒"])
        refs = {
            "meta": {"use_graph": True},
            "entities": ["井筒"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        gb.query_node.assert_called()

    def test_empty_entities_fallback_queries_once_with_original_query(self):
        """When entity extraction yields empty list, query graph_base with
        original query exactly once."""
        cfg = self._base_cfg(enable_knowledge_graph=True,
                             enable_knowledge_base=False)
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": [],
            "query": "井身结构设计",
        }
        r.query_graph("井身结构设计", [], refs)
        gb.query_node.assert_called_once()
        call_args = gb.query_node.call_args
        self.assertEqual(call_args[0][0], "井身结构设计")

    def test_graph_exception_returns_structured_error(self):
        """Graph exceptions produce refs.graph_base.error and continue."""
        cfg = self._base_cfg(enable_knowledge_graph=True,
                             enable_knowledge_base=False)
        gb = MagicMock()
        gb.query_node.side_effect = RuntimeError("Neo4j down")
        r = self._make_retriever(cfg, gb)
        r.reco_entities = MagicMock(return_value=["井筒"])
        refs = {
            "meta": {"use_graph": True},
            "entities": ["井筒"],
            "query": "test",
        }
        result = r.query_graph("test", [], refs)
        # Should degrade to a structured error dict, not raise.
        self.assertIn("error", result)
        self.assertEqual(result["error"], "graph_query_failed")
        self.assertIn("message", result)
        self.assertEqual(result["results"], {"nodes": [], "edges": []})

    def test_construct_query_uses_prepared_graph_context(self):
        cfg = self._base_cfg(enable_knowledge_graph=True)
        r = self._make_retriever(cfg, MagicMock())
        prompt_mod = types.ModuleType("src.utils.prompts")
        prompt_mod.knowbase_qa_template = "CONTEXT:\n{external}\nQUERY:{query}"
        prompt_mod.knowbase_itemGen_template = "{external}\n{params}"
        prompt_mod.RETRIEVAL_META_KEYS = ("db_id", "use_graph", "use_web", "use_multimodal_kb")

        def _pm_qa(query, external, params=None, is_item_request=False):
            if is_item_request:
                return prompt_mod.knowbase_itemGen_template.format(external=external, params=params)
            return prompt_mod.knowbase_qa_template.format(external=external, query=query)

        def _pm_chat(query, external, meta, params=None):
            if isinstance(meta, dict) and meta.get("isItemRequest"):
                return _pm_qa(query, external, params=params, is_item_request=True)
            if not (isinstance(meta, dict) and any(meta.get(k) for k in prompt_mod.RETRIEVAL_META_KEYS)):
                return query
            return _pm_qa(query, external, params=params)

        prompt_mod.build_qa_prompt = _pm_qa
        prompt_mod.build_chat_prompt = _pm_chat
        refs = {
            "knowledge_base": {"results": []},
            "graph_base": {
                "context": "[G1] well --contains--> casing",
                "results": {
                    "nodes": [{"id": "s", "name": "well"}],
                    "edges": [{
                        "source_name": "WRONG_SOURCE",
                        "target_name": "WRONG_TARGET",
                        "type": "WRONG_RELATION",
                    }],
                },
            },
            "web_search": {"results": []},
            "multimodal_knowledge_base": {"results": []},
        }
        with patch.dict(sys.modules, {"src.utils.prompts": prompt_mod}):
            result = r.construct_query("question", refs, {"use_graph": True})
        self.assertIn("[G1] well --contains--> casing", result)
        self.assertNotIn("WRONG_SOURCE", result)

    def test_context_and_sidebar_share_ranked_reference_ids(self):
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
        )
        gb = MagicMock()
        gb.query_node.return_value = [{
            "source": "well",
            "source_id": "source-1",
            "source_properties": {"name": "well"},
            "target": "casing",
            "target_id": "target-1",
            "target_properties": {"name": "casing"},
            "relation": "contains",
            "relation_desc": "",
            "score": 0.91,
        }]
        formatter = GraphDatabase.__new__(GraphDatabase)
        gb.format_query_result_to_graph.side_effect = (
            formatter.format_query_result_to_graph
        )
        r = self._make_retriever(cfg, gb)
        refs = {"meta": {"use_graph": True}, "entities": ["well"]}
        result = r.query_graph("question", [], refs)
        edge = result["results"]["edges"][0]
        self.assertIn(f"[{edge['ref_id']}]", result["context"])
        self.assertEqual(edge["source_id"], "source-1")
        self.assertEqual(edge["target_id"], "target-1")

    def test_passes_configured_args_to_query_node(self):
        """Configured threshold, hops, max_entities are passed to query_node."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_similarity_threshold=0.6,
            graph_hops=3,
            graph_max_entities=10,
        )
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        call_kwargs = self._query_kwargs(gb)
        self.assertAlmostEqual(call_kwargs["threshold"], 0.6)
        self.assertEqual(call_kwargs["hops"], 3)
        self.assertEqual(call_kwargs["max_entities"], 10)

    def test_hard_cap_entities_20(self):
        """graph_max_entities is clamped to hard max of 20."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_max_entities=50,
        )
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        call_kwargs = self._query_kwargs(gb)
        self.assertLessEqual(call_kwargs["max_entities"], 20)

    def test_hard_cap_hops_3(self):
        """graph_hops is clamped to hard max of 3."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_hops=10,
        )
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        call_kwargs = self._query_kwargs(gb)
        self.assertLessEqual(call_kwargs["hops"], 3)

    def test_hard_cap_max_relations_100(self):
        """graph_max_relations is clamped to hard max of 100."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_max_relations=500,
        )
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        call_kwargs = self._query_kwargs(gb)
        self.assertIn("max_relations", call_kwargs)
        self.assertLessEqual(call_kwargs["max_relations"], 100)

    def test_multiple_entities_share_one_relation_budget(self):
        """Graph lookups stop once the request-wide relation budget is used."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_max_relations=2,
        )
        gb = MagicMock()

        def query_node(entity, **kwargs):
            return [{
                "source": entity,
                "source_id": f"source-{entity}",
                "source_properties": {"name": entity},
                "target": f"target-{entity}",
                "target_id": f"target-{entity}",
                "target_properties": {"name": f"target-{entity}"},
                "relation": "related_to",
                "score": 0.9,
            }]

        gb.query_node.side_effect = query_node
        formatter = GraphDatabase.__new__(GraphDatabase)
        gb.format_query_result_to_graph.side_effect = (
            formatter.format_query_result_to_graph
        )
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A", "B", "C"],
            "query": "test",
        }

        result = r.query_graph("test", [], refs)

        self.assertEqual(gb.query_node.call_count, 2)
        self.assertEqual(
            [call.kwargs["max_relations"] for call in gb.query_node.call_args_list],
            [2, 1],
        )
        self.assertEqual(len(result["results"]["edges"]), 2)
        self.assertEqual(len(result["context"].splitlines()), 2)

    def test_invalid_persisted_config_falls_back_to_defaults(self):
        """Invalid config values should fall back to safe defaults."""
        cfg = self._base_cfg(
            enable_knowledge_graph=True,
            enable_knowledge_base=False,
            graph_similarity_threshold="not_a_number",
            graph_hops=-1,
            graph_max_entities=0,
        )
        gb = MagicMock()
        gb.query_node.return_value = []
        gb.format_query_result_to_graph.return_value = {"nodes": [], "edges": []}
        r = self._make_retriever(cfg, gb)
        refs = {
            "meta": {"use_graph": True},
            "entities": ["A"],
            "query": "test",
        }
        r.query_graph("test", [], refs)
        call_kwargs = self._query_kwargs(gb)
        # threshold should be a valid float >= 0
        self.assertIsInstance(call_kwargs["threshold"], (int, float))
        self.assertGreaterEqual(call_kwargs["threshold"], 0)
        # hops should be >= 1
        self.assertGreaterEqual(call_kwargs["hops"], 1)
        # max_entities should be >= 1
        self.assertGreaterEqual(call_kwargs["max_entities"], 1)


# =========================================================================
# 8. Graph start / is_running gate independent of KB
# =========================================================================


class TestGraphStartGate(unittest.TestCase):

    def setUp(self):
        self._orig_cfg = getattr(_graphbase_mod, "config", None)
        self._orig_gd = getattr(_graphbase_mod, "GD", None)
        self._orig_logger = getattr(_graphbase_mod, "logger", None)

    def tearDown(self):
        _graphbase_mod.config = self._orig_cfg
        _graphbase_mod.GD = self._orig_gd
        _graphbase_mod.logger = self._orig_logger

    def test_start_does_not_require_enable_knowledge_base(self):
        """GraphDatabase.start should work when enable_knowledge_graph=True
        even if enable_knowledge_base=False."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_driver = MagicMock()
        mock_gd.driver.return_value = mock_driver
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.files = []
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp/test_sage/knowledge_graph/neo4j"
        with patch.dict(os.environ, {
            "NEO4J_USERNAME": "neo4j",
            "NEO4J_PASSWORD": "test_pw",
        }):
            gdb.start()

        mock_gd.driver.assert_called_once()
        self.assertEqual(gdb.status, "open")

    def test_is_running_does_not_require_enable_knowledge_base(self):
        """is_running should return True when graph is enabled and connected,
        regardless of enable_knowledge_base."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        _graphbase_mod.config = cfg

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = MagicMock()
        gdb.files = []
        gdb.status = "open"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp"

        self.assertTrue(gdb.is_running())

    def test_start_failure_is_safe_and_does_not_log_credentials(self):
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_gd.driver.side_effect = RuntimeError("connection refused")
        mock_logger = MagicMock()
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd
        _graphbase_mod.logger = mock_logger

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp"
        try:
            gdb.start()
        except Exception as exc:
            self.fail(f"start failure must be handled without raising: {exc}")
        self.assertEqual(gdb.status, "closed")
        logged = " ".join(
            str(value)
            for call in mock_logger.error.call_args_list
            for value in call.args
        )
        self.assertNotIn("0123456789", logged)

    def test_start_skips_driver_when_username_missing(self):
        """start() must not call GD.driver when NEO4J_USERNAME is absent."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_logger = MagicMock()
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd
        _graphbase_mod.logger = mock_logger

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.files = []
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp/test_sage/knowledge_graph/neo4j"
        env = {"NEO4J_PASSWORD": "pw123"}
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("NEO4J_USERNAME", "NEO4J_PASSWORD")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            gdb.start()
        mock_gd.driver.assert_not_called()
        self.assertEqual(gdb.status, "closed")

    def test_start_skips_driver_when_password_missing(self):
        """start() must not call GD.driver when NEO4J_PASSWORD is absent."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_logger = MagicMock()
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd
        _graphbase_mod.logger = mock_logger

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.files = []
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp/test_sage/knowledge_graph/neo4j"
        env = {"NEO4J_USERNAME": "neo4j"}
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("NEO4J_USERNAME", "NEO4J_PASSWORD")}
        clean.update(env)
        with patch.dict(os.environ, clean, clear=True):
            gdb.start()
        mock_gd.driver.assert_not_called()
        self.assertEqual(gdb.status, "closed")

    def test_start_skips_driver_when_both_missing(self):
        """start() must not call GD.driver when both creds are absent."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_logger = MagicMock()
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd
        _graphbase_mod.logger = mock_logger

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.files = []
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp/test_sage/knowledge_graph/neo4j"
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("NEO4J_USERNAME", "NEO4J_PASSWORD")}
        with patch.dict(os.environ, clean, clear=True):
            gdb.start()
        mock_gd.driver.assert_not_called()
        self.assertEqual(gdb.status, "closed")

    def test_missing_creds_log_excludes_secret_values(self):
        """Error log on missing creds must not contain any credential value."""
        cfg = _StubConfig({
            "enable_knowledge_graph": True,
            "enable_knowledge_base": False,
        })
        mock_gd = MagicMock()
        mock_logger = MagicMock()
        _graphbase_mod.config = cfg
        _graphbase_mod.GD = mock_gd
        _graphbase_mod.logger = mock_logger

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = None
        gdb.files = []
        gdb.status = "closed"
        gdb.kgdb_name = "neo4j"
        gdb.embed_model_name = None
        gdb.work_dir = "/tmp/test_sage/knowledge_graph/neo4j"
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("NEO4J_USERNAME", "NEO4J_PASSWORD")}
        with patch.dict(os.environ, clean, clear=True):
            gdb.start()
        all_log = " ".join(
            str(a)
            for call in (mock_logger.error.call_args_list +
                         mock_logger.warning.call_args_list +
                         mock_logger.info.call_args_list)
            for a in call.args
        )
        for secret in FORBIDDEN_SECRETS:
            self.assertNotIn(secret, all_log,
                             f"Log output contains forbidden secret '{secret}'")


class TestStructuredQueryNode(unittest.TestCase):

    def _node(self, element_id, name, description):
        node = MagicMock()
        node.element_id = element_id
        node._properties = {
            "name": name,
            "description": description,
            "embedding": [1.0],
            "entityEmbeddings": [2.0],
        }
        return node

    def test_query_node_attaches_similarity_score_and_sanitized_properties(self):
        source = self._node("source-1", "well", "source description")
        target = self._node("target-1", "casing", "target description")
        relation = MagicMock()
        relation.element_id = "relation-1"
        relation.type = "RELATION"
        relation._properties = {
            "type": "contains",
            "description": "relation description",
        }
        relation.nodes = [source, target]

        session = MagicMock()
        session.execute_read.return_value = [["well", 0.91]]
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = driver
        gdb.status = "open"
        gdb.kgdb_name = "neo4j"
        gdb.is_running = MagicMock(return_value=True)
        gdb.use_database = MagicMock()
        gdb.query_specific_entity = MagicMock(
            return_value=[[source, [relation], target]]
        )

        rows = gdb.query_node("well", threshold=0.5, hops=2, max_entities=5)
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], dict)
        row = rows[0]
        self.assertEqual(row["source_id"], "source-1")
        self.assertEqual(row["target_id"], "target-1")
        self.assertEqual(row["relation"], "contains")
        self.assertEqual(row["relation_desc"], "relation description")
        self.assertEqual(row["score"], 0.91)
        self.assertNotIn("embedding", row["source_properties"])
        self.assertNotIn("entityEmbeddings", row["target_properties"])

    def test_query_node_shares_relation_budget_across_qualified_entities(self):
        session = MagicMock()
        session.execute_read.return_value = [["A", 0.91], ["B", 0.90]]
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = driver
        gdb.status = "open"
        gdb.kgdb_name = "neo4j"
        gdb.is_running = MagicMock(return_value=True)
        gdb.use_database = MagicMock()
        gdb.query_specific_entity = MagicMock(side_effect=[
            [{
                "source": "A",
                "target": "A1",
                "relation": "related_to",
                "score": 0.91,
            }],
            [{
                "source": "B",
                "target": "B1",
                "relation": "related_to",
                "score": 0.90,
            }],
        ])

        rows = gdb.query_node(
            "question",
            threshold=0.5,
            hops=2,
            max_entities=5,
            max_relations=1,
        )

        self.assertEqual(len(rows), 1)
        gdb.query_specific_entity.assert_called_once()
        self.assertEqual(
            gdb.query_specific_entity.call_args.kwargs["limit"], 1
        )

    def test_multihop_rows_use_each_relationships_actual_endpoints(self):
        first = self._node("n1", "A", "first")
        middle = self._node("n2", "B", "middle")
        last = self._node("n3", "C", "last")

        rel_one = MagicMock()
        rel_one.element_id = "r1"
        rel_one.type = "RELATION"
        rel_one._properties = {"type": "R1"}
        rel_one.nodes = [first, middle]

        rel_two = MagicMock()
        rel_two.element_id = "r2"
        rel_two.type = "RELATION"
        rel_two._properties = {"type": "R2"}
        rel_two.nodes = [middle, last]

        rows = GraphDatabase._legacy_row_to_structured(
            [first, [rel_one, rel_two], last], 0.9
        )
        endpoints = [
            (row["source_id"], row["target_id"])
            for row in rows
        ]
        self.assertEqual(endpoints, [("n1", "n2"), ("n2", "n3")])

    def test_query_specific_entity_does_not_mutate_driver_nodes(self):
        source = self._node("source-1", "well", "source")
        target = self._node("target-1", "casing", "target")
        relation = MagicMock()
        relation.nodes = [source, target]
        relation._properties = {"type": "contains"}
        result = MagicMock()
        result.__bool__.return_value = True
        result.values.return_value = [[source, [relation], target]]
        tx = MagicMock()
        tx.run.return_value = result
        session = MagicMock()
        session.execute_read.side_effect = lambda callback, *args: callback(tx, *args)
        driver = MagicMock()
        driver.session.return_value.__enter__.return_value = session

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.driver = driver
        gdb.status = "open"
        gdb.kgdb_name = "neo4j"
        gdb.use_database = MagicMock()

        gdb.query_specific_entity("well", hops=2)
        self.assertEqual(source._properties["embedding"], [1.0])
        self.assertEqual(source._properties["entityEmbeddings"], [2.0])
        self.assertEqual(target._properties["embedding"], [1.0])
        self.assertEqual(target._properties["entityEmbeddings"], [2.0])


# =========================================================================
# 9. jsonl_file_add_entity uses detected encoding
# =========================================================================


class TestJsonlFileEncoding(unittest.TestCase):

    def test_detected_encoding_is_used_in_open(self):
        """The actual open() call must use the encoding detected by chardet,
        not hardcoded 'gbk'."""
        source = inspect.getsource(GraphDatabase.jsonl_file_add_entity)
        self.assertNotIn(
            "encoding='gbk'", source,
            "jsonl_file_add_entity still uses hardcoded 'gbk' encoding",
        )

    def test_failure_restores_graph_status(self):
        import asyncio
        import tempfile

        gdb = GraphDatabase.__new__(GraphDatabase)
        gdb.status = "open"
        gdb.use_database = MagicMock()
        gdb.txt_add_vector_entity = AsyncMock(
            side_effect=RuntimeError("injected import failure")
        )
        gdb.save_graph_info = MagicMock()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding="utf-8", delete=False
        ) as handle:
            handle.write("h,r,t\nA,R,B\n")
            path = handle.name
        try:
            with self.assertRaisesRegex(RuntimeError, "injected import failure"):
                asyncio.run(gdb.jsonl_file_add_entity(path))
            self.assertEqual(gdb.status, "open")
        finally:
            os.unlink(path)


# =========================================================================
# 10. format_query_result_to_graph structured + legacy rows
# =========================================================================


class TestFormatQueryResultCompat(unittest.TestCase):

    def _make_node(self, element_id, name):
        node = MagicMock()
        node.element_id = element_id
        node._properties = {"name": name}
        return node

    def _make_relationship(self, element_id, rel_type, source_node, target_node):
        rel = MagicMock()
        rel.element_id = element_id
        rel.type = rel_type
        rel._properties = {"type": rel_type}
        rel.nodes = [source_node, target_node]
        return rel

    def test_legacy_format_with_list_relationships(self):
        """Legacy format: [node, [rel1, rel2], node] works."""
        gdb = GraphDatabase.__new__(GraphDatabase)

        n1 = self._make_node("id1", "井筒")
        n2 = self._make_node("id2", "套管")
        rel = self._make_relationship("rid1", "包含", n1, n2)

        results = [[n1, [rel], n2]]
        formatted = gdb.format_query_result_to_graph(results)

        self.assertEqual(len(formatted["nodes"]), 2)
        self.assertEqual(len(formatted["edges"]), 1)
        self.assertEqual(formatted["edges"][0]["source_name"], "井筒")
        self.assertEqual(formatted["edges"][0]["target_name"], "套管")
        self.assertEqual(formatted["edges"][0]["type"], "包含")

    def test_structured_row_format(self):
        """Structured relation dictionaries retain stable reference data."""
        gdb = GraphDatabase.__new__(GraphDatabase)
        results = [{
            "source": "cement",
            "source_id": "s1",
            "source_properties": {"name": "cement", "description": "source"},
            "target": "casing",
            "target_id": "s2",
            "target_properties": {"name": "casing", "description": "target"},
            "relation": "bonds",
            "relation_desc": "relation description",
            "score": 0.91,
            "ref_id": "G1",
        }]
        try:
            formatted = gdb.format_query_result_to_graph(results)
        except Exception as exc:
            self.fail(f"structured rows must format without raising: {exc}")

        self.assertEqual(len(formatted["nodes"]), 2)
        node_names = {n["name"] for n in formatted["nodes"]}
        self.assertEqual(node_names, {"cement", "casing"})
        self.assertEqual(formatted["edges"][0]["ref_id"], "G1")
        self.assertEqual(formatted["edges"][0]["score"], 0.91)


# =========================================================================
# 11. Config graph params with hard caps and invalid value defense
# =========================================================================


class TestGraphConfigParams(unittest.TestCase):

    @staticmethod
    def _config_source():
        return (_PROJECT_ROOT / "src" / "config" / "__init__.py").read_text(
            encoding="utf-8"
        )

    def test_config_has_graph_params(self):
        """Config should define graph retrieval parameters."""
        source = self._config_source()
        for key in ('graph_similarity_threshold', 'graph_hops',
                     'graph_max_entities', 'graph_max_relations',
                     'graph_context_max_chars'):
            self.assertIn(
                f'self.add_item("{key}"',
                source,
                f"Config missing '{key}'",
            )

    def test_config_default_values(self):
        source = self._config_source()
        expected = {
            "graph_similarity_threshold": "0.5",
            "graph_hops": "2",
            "graph_max_entities": "5",
            "graph_max_relations": "10",
            "graph_context_max_chars": "2000",
        }
        for key, default in expected.items():
            self.assertIn(
                f'self.add_item("{key}", default={default}',
                source,
            )


# =========================================================================
# 12. Stable IDs shared between context and sidebar
# =========================================================================


class TestStableIdsShared(unittest.TestCase):

    def test_ranked_ref_ids_match_context_output(self):
        """ref_ids from rank_unique_relations appear in format_graph_context."""
        rows = [
            {"source": "井筒", "target": "套管",
             "relation": "包含", "score": 0.9,
             "source_desc": "", "target_desc": "", "relation_desc": ""},
            {"source": "水泥", "target": "套管",
             "relation": "固结", "score": 0.8,
             "source_desc": "", "target_desc": "", "relation_desc": ""},
        ]
        ranked = rank_unique_relations(rows, 10)
        text = format_graph_context(ranked)
        for r in ranked:
            self.assertIn(f"[{r['ref_id']}]", text)


# =========================================================================
# 13. Module cleanup regression
# =========================================================================


class TestModuleCleanup(unittest.TestCase):
    """Verify _restore_modules() removes all stubs, including non-src names."""

    def test_torch_neo4j_chardet_requests_not_left_in_sys_modules(self):
        """After _restore_modules(), fake torch/neo4j/chardet/requests
        entries must not linger in sys.modules -- they pollute later tests."""
        for name, stub in _stub_map.items():
            if name.startswith(("src", "server")):
                continue
            actual = sys.modules.get(name)
            if actual is None:
                continue
            # If the stub object we created is still the one in sys.modules,
            # the cleanup failed.
            self.assertIsNot(
                actual,
                stub,
                f"{name!r} still references the test stub after _restore_modules()",
            )


if __name__ == "__main__":
    unittest.main()
