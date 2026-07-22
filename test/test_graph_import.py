"""Tests for GraphImportService -- CSV import into Neo4j-like graph DB.

Exercises every contract in Task 10B:
  1. Read UTF-8 CSV with required headers h,r,t; reject missing headers.
  2. Trim h/r/t, drop rows where any required field is blank, exact-dedupe,
     deterministic-sort before batching.
  3. Parameterized Cypher: malicious relation in params only, never interpolated.
  4. Honor batch_size; pass kgdb_name unchanged to every write.
  5. Post-import: add_embedding_to_nodes with namespace isolation,
     ensure_entity_vector_index, query namespace counts.
  6. Return counts from graph (post-MERGE totals), embedding return, index state.
  7. Empty normalized input: no writes, predictable namespace counts/index.
  8. MERGE idempotence: same CSV twice -> unchanged counts.
  9. Integration test skipped when NEO4J_URI unavailable.
"""

import ast
import csv
import importlib
import importlib.util
import io
import os
import sys
import tempfile
import types
import unittest
import unittest.mock
import uuid
from unittest.mock import patch
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# -- Production imports (will fail until implementation exists) ---------------
from server.services.graph_import import (
    GraphImportService,
    ImportStats,
    internal_token_matches,
    resolve_import_artifact,
)


# ============================================================================
# Fakes
# ============================================================================


# A deliberately nasty relation string to prove parameterization, not interpolation.
MALICIOUS_REL = "Robert'); DROP TABLE Relationships;--"


class FakeTransaction:
    """Records every ``run()`` call for later assertion.

    Shares node/relationship sets with the owning :class:`FakeDriver` so that
    MERGE state persists across sessions and across multiple ``import_csv``
    calls.  ``get_namespace_counts`` therefore reads cumulative state.
    """

    def __init__(
        self,
        shared_nodes: Set[Tuple[str, str]],
        shared_rels: Set[Tuple[str, str, str, str, str]],
    ) -> None:
        self.queries: List[str] = []
        self.params: List[Dict[str, Any]] = []
        self._nodes = shared_nodes   # (name, kgdb_name)
        self._rels = shared_rels     # (type, kgdb, src, tgt, desc)

    def run(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> None:
        self.queries.append(query)
        params = parameters or {}
        self.params.append(params)

        # Simulate MERGE idempotence: track unique nodes and relationships.
        rows = params.get("rows", [])
        kgdb = params.get("kgdb_name", "")
        for row in rows:
            h = row.get("h", "")
            t = row.get("t", "")
            r = row.get("r", "")
            self._nodes.add((h, kgdb))
            self._nodes.add((t, kgdb))
            # Key includes all properties SET on the relationship.
            self._rels.add((r, kgdb, h, t, r))

    def node_count(self, kgdb_name: str) -> int:
        return sum(1 for _, k in self._nodes if k == kgdb_name)

    def rel_count(self, kgdb_name: str) -> int:
        return sum(1 for _, k, _, _, _ in self._rels if k == kgdb_name)


class FakeSession:
    """Context-managed session that yields a :class:`FakeTransaction`."""

    def __init__(
        self,
        shared_nodes: Set[Tuple[str, str]],
        shared_rels: Set[Tuple[str, str, str, str, str]],
    ) -> None:
        self.txn = FakeTransaction(shared_nodes, shared_rels)

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def begin_transaction(self) -> FakeTransaction:
        return self.txn

    # Some drivers use session.run directly.
    def run(self, query: str, parameters: Optional[Dict[str, Any]] = None) -> None:
        self.txn.run(query, parameters)


class FakeDriver:
    """Looks like a Neo4j driver; yields :class:`FakeSession` instances that
    all share the same node/relationship state so MERGE idempotence works
    across sessions and across multiple ``import_csv`` calls."""

    def __init__(self) -> None:
        self.sessions: List[FakeSession] = []
        # Shared mutable state -- every session/transaction reads & writes here.
        self._shared_nodes: Set[Tuple[str, str]] = set()
        self._shared_rels: Set[Tuple[str, str, str, str, str]] = set()

    @contextmanager
    def session(self):  # type: ignore[override]
        s = FakeSession(self._shared_nodes, self._shared_rels)
        self.sessions.append(s)
        yield s


class FakeGraphDB:
    """Simulates the graph database abstraction consumed by the service.

    Tracks:
      - node / relationship counts (post-MERGE, namespace-scoped)
      - calls to ``add_embedding_to_nodes`` and ``ensure_entity_vector_index``
    """

    def __init__(self) -> None:
        self.driver = FakeDriver()
        # Embedded entity tracking
        self.embedding_calls: List[Dict[str, Any]] = []
        self.embedding_return_count: int = 0
        self.ensure_vector_index_calls: int = 0
        self.namespace_count_calls: int = 0

    # -- post-import bookkeeping ---------------------------------------------

    def add_embedding_to_nodes(
        self,
        node_names: List[str],
        kgdb_name: str,
        namespace: str,
    ) -> int:
        self.embedding_calls.append(
            {"node_names": node_names, "kgdb_name": kgdb_name, "namespace": namespace}
        )
        return self.embedding_return_count

    def ensure_entity_vector_index(self) -> bool:
        """Return True indicating the index was verified/created."""
        self.ensure_vector_index_calls += 1
        return True

    def get_namespace_counts(self, kgdb_name: str) -> Dict[str, int]:
        """Return post-MERGE totals from the shared cumulative state.

        Reads from the driver's shared node/relationship sets so that counts
        persist across sessions and across multiple ``import_csv`` calls.
        """
        self.namespace_count_calls += 1
        nodes = self.driver._shared_nodes
        rels = self.driver._shared_rels
        return {
            "node_count": sum(1 for _, k in nodes if k == kgdb_name),
            "relationship_count": sum(1 for _, k, _, _, _ in rels if k == kgdb_name),
        }


# ============================================================================
# Helpers
# ============================================================================


def _write_csv(path: Path, rows: List[List[str]]) -> None:
    """Write *rows* (first row is header) as UTF-8 CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")


# ============================================================================
# Tests
# ============================================================================


class GraphImportServiceTests(unittest.TestCase):
    """Unit tests with faked graph database."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp = self._tmpdir.name
        self.csv_path = Path(self.tmp) / "relationships.csv"
        self.graph_db = FakeGraphDB()
        self.graph_db.graph_db_name = "test_graph"
        self.embedding_return_count = 42
        self.graph_db.embedding_return_count = self.embedding_return_count

    # ------------------------------------------------------------------
    # Requirement 1: UTF-8 CSV with h,r,t headers
    # ------------------------------------------------------------------

    def test_reads_utf8_csv_with_exact_headers(self) -> None:
        """Valid CSV with h,r,t headers is accepted and returns ImportStats."""
        _write_csv(self.csv_path, [["h", "r", "t"], ["Alice", "knows", "Bob"]])

        svc = GraphImportService(self.graph_db)
        result = svc.import_csv(self.csv_path, "my_graph")

        self.assertIsInstance(result, ImportStats)
        self.assertIsInstance(result.node_count, int)
        self.assertIsInstance(result.relationship_count, int)
        self.assertIsInstance(result.embedded_count, int)
        self.assertIsInstance(result.vector_index_ready, bool)

    def test_rejects_csv_missing_h_header(self) -> None:
        """CSV without 'h' header raises ValueError naming 'h'."""
        _write_csv(self.csv_path, [["x", "r", "t"], ["a", "b", "c"]])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError) as ctx:
            svc.import_csv(self.csv_path, "g")
        self.assertIn("h", str(ctx.exception))

    def test_rejects_csv_missing_r_header(self) -> None:
        """CSV without 'r' header raises ValueError naming 'r'."""
        _write_csv(self.csv_path, [["h", "x", "t"], ["a", "b", "c"]])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError) as ctx:
            svc.import_csv(self.csv_path, "g")
        self.assertIn("r", str(ctx.exception))

    def test_rejects_csv_missing_t_header(self) -> None:
        """CSV without 't' header raises ValueError naming 't'."""
        _write_csv(self.csv_path, [["h", "r", "x"], ["a", "b", "c"]])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError) as ctx:
            svc.import_csv(self.csv_path, "g")
        self.assertIn("t", str(ctx.exception))

    def test_rejects_csv_with_extra_headers_only(self) -> None:
        """CSV with extra headers but none of h,r,t raises ValueError."""
        _write_csv(self.csv_path, [["a", "b", "c"], ["1", "2", "3"]])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError):
            svc.import_csv(self.csv_path, "g")

    def test_rejects_csv_with_extra_header_after_required(self) -> None:
        """CSV headers h,r,t,extra must raise ValueError mentioning
        unexpected or exact headers."""
        _write_csv(self.csv_path, [
            ["h", "r", "t", "extra"],
            ["Alice", "knows", "Bob", "x"],
        ])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError) as ctx:
            svc.import_csv(self.csv_path, "g")
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "unexpected" in msg or "exact" in msg or "extra" in msg,
            f"Error message should mention unexpected/exact/extra headers, got: {ctx.exception}",
        )

    def test_rejects_csv_with_wrong_header_order(self) -> None:
        """CSV headers r,h,t (wrong order) must raise ValueError mentioning
        order or exact headers."""
        _write_csv(self.csv_path, [
            ["r", "h", "t"],
            ["knows", "Alice", "Bob"],
        ])

        svc = GraphImportService(self.graph_db)
        with self.assertRaises(ValueError) as ctx:
            svc.import_csv(self.csv_path, "g")
        msg = str(ctx.exception).lower()
        self.assertTrue(
            "order" in msg or "exact" in msg or "unexpected" in msg,
            f"Error message should mention order/exact headers, got: {ctx.exception}",
        )

    # ------------------------------------------------------------------
    # Requirement 2: trim, drop blanks, dedupe, sort
    # ------------------------------------------------------------------

    def test_trims_whitespace_from_fields(self) -> None:
        """Leading/trailing whitespace in h/r/t is stripped."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["  Alice  ", "  knows  ", "  Bob  "],
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        self.assertEqual(len(sessions), 1)
        rows = sessions[0].txn.params[0]["rows"]
        self.assertEqual(rows[0], {"h": "Alice", "r": "knows", "t": "Bob"})

    def test_drops_rows_where_any_required_field_is_blank(self) -> None:
        """Rows where any required field (h, r, or t) is blank after trimming
        are excluded.  This covers fully blank rows, partially blank rows
        (blank h only, blank r only, blank t only), and whitespace-only
        variants of each."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", "knows", "Bob"],       # valid
            ["", "", ""],                     # all blank
            ["  ", "  ", "  "],               # all whitespace
            ["", "knows", "Bob"],             # blank h
            ["Alice", "", "Bob"],             # blank r
            ["Alice", "knows", ""],           # blank t
            ["  ", "knows", "Bob"],           # whitespace-only h
            ["Alice", "  ", "Bob"],           # whitespace-only r
            ["Alice", "knows", "  "],         # whitespace-only t
            ["Carol", "likes", "Dave"],       # valid
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        all_rows = []
        for call in sessions[0].txn.params:
            all_rows.extend(call["rows"])
        self.assertEqual(len(all_rows), 2)
        names = {(r["h"], r["t"]) for r in all_rows}
        self.assertIn(("Alice", "Bob"), names)
        self.assertIn(("Carol", "Dave"), names)

    def test_exact_deduplication(self) -> None:
        """Identical rows (after trim) appear only once."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", "knows", "Bob"],
            ["Alice", "knows", "Bob"],
            ["Alice", "knows", "Bob"],
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        total_rows = sum(
            len(call["rows"]) for call in sessions[0].txn.params
        )
        self.assertEqual(total_rows, 1)

    def test_deterministic_sort(self) -> None:
        """Rows are sorted deterministically (by h, r, t) regardless of input order."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Zoe", "knows", "Alice"],
            ["Alice", "likes", "Bob"],
            ["Alice", "knows", "Bob"],
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        all_rows = []
        for call in sessions[0].txn.params:
            all_rows.extend(call["rows"])

        triples = [(r["h"], r["r"], r["t"]) for r in all_rows]
        self.assertEqual(triples, sorted(triples))

    # ------------------------------------------------------------------
    # Requirement 3: parameterized Cypher, no interpolation
    # ------------------------------------------------------------------

    def test_uses_parameterized_cypher_query(self) -> None:
        """Cypher uses UNWIND $rows, MERGE, and SET with parameterized values."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", "knows", "Bob"],
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "my_graph")

        sessions = self.graph_db.driver.sessions
        self.assertEqual(len(sessions), 1)
        query = sessions[0].txn.queries[0]

        # Structural checks on the Cypher template.
        self.assertIn("UNWIND $rows AS row", query)
        self.assertIn("MERGE (h:Entity {name: row.h, kgdb_name: $kgdb_name})", query)
        self.assertIn("MERGE (t:Entity {name: row.t, kgdb_name: $kgdb_name})", query)
        self.assertIn("MERGE (h)-[r:RELATION {type: row.r, kgdb_name: $kgdb_name}]->(t)", query)
        self.assertIn("SET r.description = row.r", query)
        self.assertIn("r.updated_at = datetime()", query)

        # Parameters carry the data, not the query text.
        params = sessions[0].txn.params[0]
        self.assertEqual(params["kgdb_name"], "my_graph")
        self.assertEqual(params["rows"][0]["h"], "Alice")
        self.assertEqual(params["rows"][0]["r"], "knows")
        self.assertEqual(params["rows"][0]["t"], "Bob")

    def test_malicious_relation_in_params_only_never_interpolated(self) -> None:
        """A relation string that contains SQL/Cypher injection payload must
        appear only in parameters, never in the query text itself."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", MALICIOUS_REL, "Bob"],
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        query = sessions[0].txn.queries[0]

        # The malicious string must NOT appear in the Cypher template.
        self.assertNotIn(MALICIOUS_REL, query)

        # But it MUST appear in the parameter values.
        params = sessions[0].txn.params[0]
        rel_values = [row["r"] for row in params["rows"]]
        self.assertIn(MALICIOUS_REL, rel_values)

    # ------------------------------------------------------------------
    # Requirement 4: batch_size honored, kgdb_name passed unchanged
    # ------------------------------------------------------------------

    def test_batch_size_controls_rows_per_call(self) -> None:
        """With batch_size=2 and 5 unique rows, expect 3 run() calls
        (2+2+1)."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["A", "r1", "B"],
            ["C", "r2", "D"],
            ["E", "r3", "F"],
            ["G", "r4", "H"],
            ["I", "r5", "J"],
        ])

        svc = GraphImportService(self.graph_db, batch_size=2)
        svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        txn = sessions[0].txn
        self.assertEqual(len(txn.queries), 3)

        batch_sizes = [len(p["rows"]) for p in txn.params]
        self.assertEqual(batch_sizes, [2, 2, 1])

    def test_kgdb_name_unchanged_in_every_batch(self) -> None:
        """kgdb_name is passed verbatim to every run() call."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["A", "r1", "B"],
            ["C", "r2", "D"],
        ])

        svc = GraphImportService(self.graph_db, batch_size=1)
        svc.import_csv(self.csv_path, "my_namespace")

        sessions = self.graph_db.driver.sessions
        for params in sessions[0].txn.params:
            self.assertEqual(params["kgdb_name"], "my_namespace")

    # ------------------------------------------------------------------
    # Requirement 5: post-import pipeline
    # ------------------------------------------------------------------

    def test_calls_add_embedding_to_nodes_with_sorted_unique_entities(self) -> None:
        """After writes, add_embedding_to_nodes receives sorted unique
        entity names (union of h and t) with namespace isolation."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Charlie", "knows", "Alice"],
            ["Alice", "likes", "Bob"],
            ["Bob", "knows", "Charlie"],  # duplicates Charlie and Alice
        ])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "kg1")

        self.assertEqual(len(self.graph_db.embedding_calls), 1)
        call = self.graph_db.embedding_calls[0]

        # Sorted unique entity names.
        expected = sorted({"Alice", "Bob", "Charlie"})
        self.assertEqual(call["node_names"], expected)

        # Namespace isolation: kgdb_name == namespace.
        self.assertEqual(call["kgdb_name"], "kg1")
        self.assertEqual(call["namespace"], "kg1")

    def test_calls_ensure_entity_vector_index_after_embeddings(self) -> None:
        """ensure_entity_vector_index is called exactly once per import."""
        _write_csv(self.csv_path, [["h", "r", "t"], ["A", "r", "B"]])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        self.assertEqual(self.graph_db.ensure_vector_index_calls, 1)

    def test_queries_namespace_counts_after_index(self) -> None:
        """get_namespace_counts is called with the kgdb_name after writes."""
        _write_csv(self.csv_path, [["h", "r", "t"], ["A", "r", "B"]])

        svc = GraphImportService(self.graph_db)
        svc.import_csv(self.csv_path, "g")

        self.assertGreaterEqual(self.graph_db.namespace_count_calls, 1)

    # ------------------------------------------------------------------
    # Requirement 6: returned stats reflect graph state
    # ------------------------------------------------------------------

    def test_import_stats_reflect_graph_counts_and_embedding(self) -> None:
        """ImportStats fields come from post-MERGE graph counts and the
        embedding return value."""
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", "knows", "Bob"],
        ])

        self.graph_db.embedding_return_count = 7
        svc = GraphImportService(self.graph_db)
        result = svc.import_csv(self.csv_path, "g")

        # Node count: post-MERGE namespace count (2: Alice and Bob).
        self.assertEqual(result.node_count, 2)
        # Relationship count: post-MERGE namespace count (1).
        self.assertEqual(result.relationship_count, 1)
        # Embedded count: from add_embedding_to_nodes return.
        self.assertEqual(result.embedded_count, 7)
        # Vector index ready: from ensure_entity_vector_index return value.
        self.assertTrue(result.vector_index_ready)

    # ------------------------------------------------------------------
    # Requirement 7: empty normalized input
    # ------------------------------------------------------------------

    def test_empty_normalized_input_no_writes_predictable_counts(self) -> None:
        """When every row is blank after normalization, no Cypher is executed
        but namespace counts and index state are still queried and returned.

        Expected: node_count=0, relationship_count=0, embedded_count=0,
        vector_index_ready=True (ensure_entity_vector_index still called).
        """
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["", "", ""],
            ["  ", "  ", "  "],
        ])

        svc = GraphImportService(self.graph_db)
        result = svc.import_csv(self.csv_path, "g")

        # No Cypher should have been executed.
        sessions = self.graph_db.driver.sessions
        txn = sessions[0].txn
        self.assertEqual(len(txn.queries), 0)

        # Post-import pipeline still runs.
        self.assertEqual(len(self.graph_db.embedding_calls), 1)
        self.assertEqual(self.graph_db.embedding_calls[0]["node_names"], [])
        self.assertEqual(self.graph_db.ensure_vector_index_calls, 1)

        # Predictable stats.
        self.assertEqual(result.node_count, 0)
        self.assertEqual(result.relationship_count, 0)
        self.assertEqual(result.embedded_count, 0)
        self.assertTrue(result.vector_index_ready)

    def test_header_only_csv_no_writes(self) -> None:
        """A CSV with only the header row (no data rows) produces no writes."""
        _write_csv(self.csv_path, [["h", "r", "t"]])

        svc = GraphImportService(self.graph_db)
        result = svc.import_csv(self.csv_path, "g")

        sessions = self.graph_db.driver.sessions
        self.assertEqual(len(sessions[0].txn.queries), 0)
        self.assertEqual(result.node_count, 0)
        self.assertEqual(result.relationship_count, 0)

    # ------------------------------------------------------------------
    # Requirement 8: MERGE idempotence
    # ------------------------------------------------------------------

    def test_importing_same_csv_twice_leaves_counts_unchanged(self) -> None:
        """Simulate MERGE idempotence: importing the same CSV twice must not
        increase node or relationship counts beyond the first import.

        Shared state across sessions proves that the second import sees
        the cumulative totals and does not grow them.
        """
        _write_csv(self.csv_path, [
            ["h", "r", "t"],
            ["Alice", "knows", "Bob"],
            ["Bob", "likes", "Carol"],
        ])

        svc = GraphImportService(self.graph_db)

        result1 = svc.import_csv(self.csv_path, "g")
        result2 = svc.import_csv(self.csv_path, "g")

        # After two identical imports, counts must be the same.
        self.assertEqual(result1.node_count, result2.node_count)
        self.assertEqual(result1.relationship_count, result2.relationship_count)
        # Specifically, 3 unique nodes and 2 relationships.
        self.assertEqual(result2.node_count, 3)
        self.assertEqual(result2.relationship_count, 2)

    # ------------------------------------------------------------------
    # Requirement 9: integration test (skipped without NEO4J_URI)
    # ------------------------------------------------------------------


@unittest.skipUnless(
    os.environ.get("NEO4J_URI"),
    "NEO4J_URI not set -- skipping Neo4j integration test",
)
class GraphImportIntegrationTests(unittest.TestCase):
    """Integration tests for a real Neo4j instance.

    Builds a minimal local wrapper around a real neo4j driver that exposes
    the same interface as :class:`FakeGraphDB`.  Uses a UUID namespace to
    avoid collisions.  Imports the same CSV twice to verify MERGE
    idempotence and cleanup removes all data in that namespace.
    """

    def test_import_round_trip_with_real_neo4j(self) -> None:
        """Import a small CSV into a real Neo4j and verify counts.

        Wraps a real driver to expose: driver, add_embedding_to_nodes
        (returning 0 -- no model dependency), ensure_entity_vector_index
        (returning True), and get_namespace_counts via parameterized Cypher.
        """
        # Lazy import so the skip is evaluated before import-time errors.
        from neo4j import GraphDatabase  # type: ignore[import-not-found]

        uri = os.environ["NEO4J_URI"]
        user = os.environ.get("NEO4J_USERNAME") or os.environ.get(
            "NEO4J_USER", "neo4j"
        )
        password = os.environ.get("NEO4J_PASSWORD", "neo4j")
        namespace = f"test_{uuid.uuid4().hex[:8]}"

        real_driver = GraphDatabase.driver(uri, auth=(user, password))

        # -- Minimal local wrapper matching FakeGraphDB's interface ----------
        class _RealGraphDB:
            """Real-driver wrapper for integration testing."""

            def __init__(self, drv: Any) -> None:
                self.driver = drv

            @contextmanager
            def session(self):  # type: ignore[override]
                with self.driver.session() as s:
                    yield s

            def add_embedding_to_nodes(
                self,
                node_names: List[str],
                kgdb_name: str,
                namespace: str,
            ) -> int:
                return 0  # no model dependency

            def ensure_entity_vector_index(self) -> bool:
                return True  # verified for merge/idempotence integration

            def get_namespace_counts(self, kgdb_name: str) -> Dict[str, int]:
                """Read counts via parameterized Cypher."""
                with self.driver.session() as session:
                    nc = session.run(
                        "MATCH (n:Entity {kgdb_name: $kg}) "
                        "RETURN count(n) AS cnt",
                        {"kg": kgdb_name},
                    ).single()["cnt"]
                    rc = session.run(
                        "MATCH ()-[r:RELATION {kgdb_name: $kg}]->() "
                        "RETURN count(r) AS cnt",
                        {"kg": kgdb_name},
                    ).single()["cnt"]
                return {"node_count": nc, "relationship_count": rc}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                csv_path = Path(tmp) / "integration.csv"
                _write_csv(csv_path, [
                    ["h", "r", "t"],
                    ["IntAlice", "knows", "IntBob"],
                    ["IntBob", "likes", "IntCarol"],
                ])

                graph_db = _RealGraphDB(real_driver)
                svc = GraphImportService(graph_db, batch_size=100)

                # First import: 3 nodes, 2 relationships.
                result1 = svc.import_csv(csv_path, namespace)
                self.assertEqual(result1.node_count, 3)
                self.assertEqual(result1.relationship_count, 2)
                self.assertTrue(result1.vector_index_ready)

                # Second import (idempotence): same counts, no growth.
                result2 = svc.import_csv(csv_path, namespace)
                self.assertEqual(result2.node_count, 3)
                self.assertEqual(result2.relationship_count, 2)
                self.assertTrue(result2.vector_index_ready)
        finally:
            # Cleanup: remove all data in the test namespace.
            with real_driver.session() as session:
                session.run(
                    "MATCH (n:Entity {kgdb_name: $kg}) DETACH DELETE n",
                    {"kg": namespace},
                )
            real_driver.close()


# ============================================================================
# Module-loading helper for GraphDatabase (avoids src/__init__ + Milvus startup)
# ============================================================================


def _load_graphbase_module():
    """Load ``src.core.graphbase`` by file path, stubbing heavy imports.

    Normal import of ``src.core.graphbase`` triggers ``src/__init__`` which
    boots Milvus, loads models, and connects to Neo4j.  This helper instead:

    1. Inserts minimal stub modules for ``src``, ``src.config``, ``src.utils``,
       ``src.core``, ``neo4j``, ``torch``, ``requests``, and ``chardet``.
    2. Loads ``graphbase.py`` directly via ``importlib.util.spec_from_file_location``.
    3. Returns the loaded module.

    Callers **must** restore ``sys.modules`` afterward.
    """
    import importlib.util

    src_root = Path(__file__).resolve().parent.parent / "src"
    graphbase_path = src_root / "core" / "graphbase.py"

    # -- Stub logger with no-op methods ---------------------------------------
    stub_logger = types.ModuleType("src.utils.logging_config")
    stub_logger.logger = type("NullLogger", (), {
        "debug": staticmethod(lambda *a, **kw: None),
        "info": staticmethod(lambda *a, **kw: None),
        "warning": staticmethod(lambda *a, **kw: None),
        "error": staticmethod(lambda *a, **kw: None),
    })()

    # -- Stub src.config -------------------------------------------------------
    stub_config_mod = types.ModuleType("src.config")

    class _StubConfig:
        save_dir = tempfile.gettempdir()
        enable_knowledge_graph = False
        enable_knowledge_base = False
        embed_model = "test_model"
        embed_model_names = {"test_model": {"name": "test", "dimension": 3}}

    stub_config_mod.Config = _StubConfig
    stub_config_mod.config = _StubConfig()

    # -- Stub src.utils --------------------------------------------------------
    stub_utils = types.ModuleType("src.utils")
    stub_utils.logger = stub_logger.logger
    stub_utils.__path__ = []  # mark as package

    # -- Stub src (package) ----------------------------------------------------
    stub_src = types.ModuleType("src")
    stub_src.__path__ = [str(src_root)]
    stub_src.config = stub_config_mod.config

    # -- Stub src.core (package) -----------------------------------------------
    stub_core = types.ModuleType("src.core")
    stub_core.__path__ = [str(src_root / "core")]

    # -- Stub neo4j ------------------------------------------------------------
    stub_neo4j = types.ModuleType("neo4j")

    class _StubGD:
        @staticmethod
        def driver(*a, **kw):
            return None

    stub_neo4j.GraphDatabase = _StubGD

    # -- Stub torch / requests / chardet --------------------------------------
    stub_torch = types.ModuleType("torch")
    stub_requests = types.ModuleType("requests")
    stub_chardet = types.ModuleType("chardet")

    # -- Install stubs into sys.modules ----------------------------------------
    saved: Dict[str, Any] = {}
    stubs = {
        "src": stub_src,
        "src.config": stub_config_mod,
        "src.utils": stub_utils,
        "src.utils.logging_config": stub_logger,
        "src.core": stub_core,
        "neo4j": stub_neo4j,
        "torch": stub_torch,
        "requests": stub_requests,
        "chardet": stub_chardet,
    }
    for key in stubs:
        if key in sys.modules:
            saved[key] = sys.modules[key]
        sys.modules[key] = stubs[key]

    try:
        spec = importlib.util.spec_from_file_location(
            "src.core.graphbase", str(graphbase_path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(
                f"Cannot create spec for {graphbase_path}"
            )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, saved
    finally:
        # Restore original sys.modules entries.
        for key in stubs:
            if key in saved:
                sys.modules[key] = saved[key]
            else:
                sys.modules.pop(key, None)


# ============================================================================
# Fakes for GraphBaseNamespaceTests
# ============================================================================


class _FakeRecord:
    """Minimal record wrapper for ``tx.run(...)`` result iteration."""

    def __init__(self, **fields: Any) -> None:
        self._fields = fields

    def __getitem__(self, key: str) -> Any:
        return self._fields[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._fields.get(key, default)


class _FakeResult:
    """Minimal result object returned by ``tx.run(...)``."""

    def __init__(self, records: Optional[List[_FakeRecord]] = None) -> None:
        self._records = records or []

    def single(self) -> Optional[_FakeRecord]:
        return self._records[0] if self._records else None

    def __iter__(self):
        return iter(self._records)


class _FakeNSQueryTx:
    """Fake transaction that records queries and returns configurable results.

    ``result_map`` maps a substring of the query to a list of ``_FakeRecord``
    objects.  The *first* matching entry wins (checked in insertion order).
    """

    def __init__(
        self,
        result_map: Optional[Dict[str, List[_FakeRecord]]] = None,
    ) -> None:
        self.queries: List[str] = []
        self.params_list: List[Dict[str, Any]] = []
        self._result_map: Dict[str, List[_FakeRecord]] = (
            result_map if result_map is not None else {}
        )

    def run(self, query: str, **params: Any) -> _FakeResult:
        """Record the query/params and return a pre-configured result."""
        # Also accept a positional dict (Neo4j driver style).
        if not params and isinstance(query, str):
            # Allow (query, parameters_dict) positional call style.
            pass
        self.queries.append(query)
        self.params_list.append(params)
        for key, records in self._result_map.items():
            if key in query:
                return _FakeResult(records)
        return _FakeResult()


class _FakeNSSession:
    """Context-managed session yielding a ``_FakeNSQueryTx``."""

    def __init__(self, tx: _FakeNSQueryTx) -> None:
        self._tx = tx

    def __enter__(self) -> "_FakeNSSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        pass

    def begin_transaction(self) -> _FakeNSQueryTx:
        return self._tx

    def execute_write(self, fn: Any, *args: Any) -> Any:
        return fn(self._tx, *args)

    def execute_read(self, fn: Any, *args: Any) -> Any:
        return fn(self._tx, *args)


class _FakeNSDriver:
    """Driver whose ``session()`` context manager yields a single fake session."""

    def __init__(self, tx: _FakeNSQueryTx) -> None:
        self._tx = tx

    @contextmanager
    def session(self, **kw: Any):  # type: ignore[override]
        yield _FakeNSSession(self._tx)


# ============================================================================
# GraphBaseNamespaceTests -- TDD red tests for namespace adapter on GraphDatabase
# ============================================================================


class GraphBaseNamespaceTests(unittest.TestCase):
    """Red TDD tests for the namespace adapter on ``src.core.graphbase.GraphDatabase``.

    These methods do **not** exist on the production class yet.  Every test
    exercises a contract the production code must eventually satisfy.

    ``GraphDatabase.__new__`` is used to create bare instances (no
    ``__init__``), and a ``_FakeNSDriver`` is injected to capture/verify
    queries.
    """

    # -- module loading (once per class) --------------------------------------

    @classmethod
    def setUpClass(cls) -> None:
        cls._mod, cls._saved_modules = _load_graphbase_module()
        cls.GraphDatabase = cls._mod.GraphDatabase

    @classmethod
    def tearDownClass(cls) -> None:
        # Restore any sys.modules entries that _load_graphbase_module saved.
        pass  # cleanup is handled inside _load_graphbase_module's finally

    def _make_db(self, tx: _FakeNSQueryTx) -> Any:
        """Return a bare ``GraphDatabase`` with a fake driver and session."""
        db = self.GraphDatabase.__new__(self.GraphDatabase)
        db.driver = _FakeNSDriver(tx)
        db.kgdb_name = "neo4j"
        return db

    # ------------------------------------------------------------------
    # 1. set_embedding uses parameterized MATCH with name AND kgdb_name
    # ------------------------------------------------------------------

    def test_set_embedding_parameterized_match_name_and_namespace(self) -> None:
        """set_embedding(tx, entity_name, embedding, namespace='ground')
        must use a parameterized MATCH containing both ``name: $name`` and
        ``kgdb_name: $namespace``; the namespace value must appear in
        params, never interpolated into the query text."""
        tx = _FakeNSQueryTx()
        db = self._make_db(tx)
        emb = [0.1, 0.2, 0.3]

        db.set_embedding(tx, "Alice", emb, namespace="ground")

        self.assertEqual(len(tx.queries), 1)
        query = tx.queries[0]
        params = tx.params_list[0]

        # Must match both name and namespace.
        self.assertIn("name: $name", query)
        self.assertIn("kgdb_name: $namespace", query)

        # Namespace is a parameter -- never interpolated.
        self.assertNotIn("ground", query)
        self.assertEqual(params["name"], "Alice")
        self.assertEqual(params["namespace"], "ground")
        self.assertEqual(params["embedding"], emb)

    def test_set_embedding_legacy_call_without_namespace_supported(self) -> None:
        """Legacy call ``set_embedding(tx, entity_name, embedding)`` with
        no namespace must still work (default namespace)."""
        tx = _FakeNSQueryTx()
        db = self._make_db(tx)
        emb = [0.5]

        # Default namespace (no explicit argument).
        db.set_embedding(tx, "Bob", emb)

        self.assertEqual(len(tx.queries), 1)
        params = tx.params_list[0]
        self.assertEqual(params["name"], "Bob")
        self.assertEqual(params["embedding"], emb)

    # ------------------------------------------------------------------
    # 2. add_embedding_to_nodes: skip already-indexed, namespace aware
    # ------------------------------------------------------------------

    def test_add_embedding_skips_node_already_with_embedding(self) -> None:
        """add_embedding_to_nodes must filter provided names to only those
        missing an embedding in the given namespace and embed only those.

        With ``node_names=['B', 'A']`` where 'B' already has an embedding
        in namespace 'ground', only 'A' receives a write.

        Calls match the service pattern: ``kgdb_name='ground'``,
        ``namespace='ground'`` while ``db.kgdb_name='neo4j'``."""
        # FakeResult for the "without embedding" query returns only A.
        without_emb_records = [_FakeRecord(name="A")]
        tx = _FakeNSQueryTx(result_map={
            "n.embedding IS NULL": without_emb_records,
        })
        db = self._make_db(tx)
        db.get_embedding = lambda name: [0.1, 0.2, 0.3]  # fake embedder

        # Spy on use_database to record calls.
        use_db_calls: List[str] = []
        def _spy_use_database(name: str) -> None:
            use_db_calls.append(name)
        db.use_database = _spy_use_database

        # Call exactly as GraphImportService does.
        db.add_embedding_to_nodes(
            node_names=["B", "A"],
            kgdb_name="ground",
            namespace="ground",
        )

        write_queries = [q for q in tx.queries if "setNodeVectorProperty" in q]

        # Only one write -- 'A' (B is already embedded).
        self.assertEqual(
            len(write_queries), 1,
            f"Expected exactly 1 write for 'A', got {len(write_queries)}: "
            f"{write_queries}",
        )

        # The write uses parameterized match with namespace.
        self.assertIn("name: $name", write_queries[0])
        self.assertIn("kgdb_name: $namespace", write_queries[0])

        # Namespace parameter is passed.
        write_params = [
            p for q, p in zip(tx.queries, tx.params_list)
            if "setNodeVectorProperty" in q
        ]
        self.assertEqual(write_params[0]["namespace"], "ground")

        # use_database must use the physical DB name, not the namespace.
        self.assertEqual(use_db_calls, ["neo4j"])

    def test_add_embedding_uses_physical_db_name(self) -> None:
        """add_embedding_to_nodes must use ``self.kgdb_name`` ('neo4j') as
        the physical database for ``use_database``, NOT the namespace.

        Call exactly as GraphImportService does: ``kgdb_name='ground'``,
        ``namespace='ground'`` while ``db.kgdb_name='neo4j'``.  The spy on
        ``use_database`` proves only the physical DB name is used."""
        without_emb_records = [_FakeRecord(name="X")]
        tx = _FakeNSQueryTx(result_map={
            "n.embedding IS NULL": without_emb_records,
        })
        db = self._make_db(tx)
        db.kgdb_name = "neo4j"
        db.get_embedding = lambda name: [0.0]

        # Spy on use_database to record every call.
        use_db_calls: List[str] = []
        def _spy_use_database(name: str) -> None:
            use_db_calls.append(name)
        db.use_database = _spy_use_database

        # Call exactly as GraphImportService does.
        db.add_embedding_to_nodes(
            node_names=["X"],
            kgdb_name="ground",
            namespace="ground",
        )

        # use_database must be called with the physical DB name, not the namespace.
        self.assertEqual(use_db_calls, ["neo4j"])

        # At least one query was issued (proof it didn't crash).
        self.assertGreater(len(tx.queries), 0)

    # ------------------------------------------------------------------
    # 3. get_namespace_counts uses parameterized namespace
    # ------------------------------------------------------------------

    def test_get_namespace_counts_returns_parameterized_counts(self) -> None:
        """get_namespace_counts('ground') must return only node and
        relationship counts for that namespace via parameterized Cypher.
        The namespace value must appear in params, never in query text."""
        tx = _FakeNSQueryTx(result_map={
            "count(n)": [_FakeRecord(cnt=5)],
            "count(r)": [_FakeRecord(cnt=3)],
        })
        db = self._make_db(tx)

        counts = db.get_namespace_counts("ground")

        self.assertIsInstance(counts, dict)
        self.assertIn("node_count", counts)
        self.assertIn("relationship_count", counts)
        self.assertEqual(counts["node_count"], 5)
        self.assertEqual(counts["relationship_count"], 3)

        # Every query must be parameterized -- 'ground' must not appear
        # in the query text itself.
        for q in tx.queries:
            self.assertNotIn(
                "ground", q,
                f"Namespace must be a parameter, not interpolated: {q}",
            )

        # Every query must carry the namespace as a parameter.
        for p in tx.params_list:
            self.assertIn(
                "namespace", p,
                f"Parameter dict missing 'namespace': {p}",
            )
            self.assertEqual(p["namespace"], "ground")

    # ------------------------------------------------------------------
    # 4. ensure_entity_vector_index
    # ------------------------------------------------------------------

    def test_ensure_entity_vector_index_creates_with_correct_config(self) -> None:
        """ensure_entity_vector_index(dimension=3) must create a fixed-
        name index ``entityEmbeddings`` over ``(n:Entity).embedding``
        with ``IF NOT EXISTS``, verify it is ONLINE, and return True.

        The index name must be a constant (not derived from arguments),
        and dimension must be passed as a parameter (never interpolated).

        Neo4j 5.26+ uses ``CALL db.awaitIndex(indexName, timeoutSeconds)``
        rather than ``db.index.awaitEventuallyOnlineIndex``."""
        # No existing index.
        tx = _FakeNSQueryTx(result_map={
            "SHOW INDEXES": [],
            "db.awaitIndex": [_FakeRecord()],
        })
        db = self._make_db(tx)

        result = db.ensure_entity_vector_index(dimension=3)

        self.assertTrue(result)

        create_queries = [
            q for q in tx.queries
            if "CREATE VECTOR INDEX" in q.upper()
        ]
        self.assertEqual(
            len(create_queries), 1,
            f"Expected exactly 1 CREATE VECTOR INDEX, got: {tx.queries}",
        )
        create_q = create_queries[0]

        # Fixed constant index name.
        self.assertIn("entityEmbeddings", create_q)
        # IF NOT EXISTS idempotency guard.
        self.assertIn("IF NOT EXISTS", create_q)
        # Dimension validated positive (passed as parameter, not interpolated).
        self.assertNotIn("3", create_q)
        dim_params = [
            p for p in tx.params_list if "dimension" in p
        ]
        self.assertEqual(len(dim_params), 1)
        self.assertEqual(dim_params[0]["dimension"], 3)

        # Await/verify the index is online via Neo4j 5.26+ procedure.
        await_queries = [
            q for q in tx.queries
            if "db.awaitIndex" in q
        ]
        self.assertEqual(
            len(await_queries), 1,
            "Must call db.awaitIndex(indexName, timeoutSeconds) after creation",
        )
        # The await call must reference the constant index name.
        self.assertIn("entityEmbeddings", await_queries[0])

    def test_ensure_entity_vector_index_rejects_nonpositive_dimension(self) -> None:
        """Dimension must be validated as strictly positive."""
        tx = _FakeNSQueryTx()
        db = self._make_db(tx)

        with self.assertRaises(ValueError):
            db.ensure_entity_vector_index(dimension=0)

        with self.assertRaises(ValueError):
            db.ensure_entity_vector_index(dimension=-1)

        # No queries should have been issued for invalid dimensions.
        self.assertEqual(len(tx.queries), 0)

    def test_ensure_entity_vector_index_no_duplicate_create_for_online_index(
        self,
    ) -> None:
        """When the index already exists and is ONLINE, the method must
        NOT issue a duplicate CREATE VECTOR INDEX query."""
        # Simulate an existing ONLINE index.
        tx = _FakeNSQueryTx(result_map={
            "SHOW INDEXES": [_FakeRecord(
                name="entityEmbeddings",
                state="ONLINE",
            )],
        })
        db = self._make_db(tx)

        result = db.ensure_entity_vector_index(dimension=3)

        self.assertTrue(result)

        create_queries = [
            q for q in tx.queries
            if "CREATE VECTOR INDEX" in q.upper()
        ]
        self.assertEqual(
            len(create_queries), 0,
            f"Must not CREATE when index already ONLINE: {tx.queries}",
        )


# ============================================================================
# ImportSecurityHelpersTests -- TDD red tests for internal security helpers
# ============================================================================


class ImportSecurityHelpersTests(unittest.TestCase):
    """Red TDD tests for ``internal_token_matches`` and ``resolve_import_artifact``.

    Every test exercises a contract the production code must satisfy.
    A missing import at the module level causes a real ImportError failure.
    """

    # ------------------------------------------------------------------
    # Token helper: internal_token_matches
    # ------------------------------------------------------------------

    def test_token_correct_returns_true(self) -> None:
        """Matching non-empty provided and expected tokens return True."""
        self.assertTrue(internal_token_matches("s3cret", "s3cret"))

    def test_token_wrong_returns_false(self) -> None:
        """Non-matching non-empty tokens return False."""
        self.assertFalse(internal_token_matches("s3cret", "wrong"))

    def test_token_provided_none_returns_false(self) -> None:
        """None provided token returns False regardless of expected."""
        self.assertFalse(internal_token_matches(None, "s3cret"))

    def test_token_expected_none_falls_back_to_env(self) -> None:
        """When expected is None the helper reads GRAPH_INTERNAL_TOKEN."""
        with patch.dict(os.environ, {"GRAPH_INTERNAL_TOKEN": "env_tok"}):
            self.assertTrue(internal_token_matches("env_tok"))

    def test_token_env_missing_returns_false(self) -> None:
        """When expected is None and GRAPH_INTERNAL_TOKEN is unset, return
        False even if provided is non-empty."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GRAPH_INTERNAL_TOKEN", None)
            self.assertFalse(internal_token_matches("anything"))

    def test_token_empty_provided_returns_false(self) -> None:
        """Empty-string provided token returns False."""
        self.assertFalse(internal_token_matches("", "s3cret"))

    def test_token_empty_expected_returns_false(self) -> None:
        """Empty-string expected token returns False."""
        self.assertFalse(internal_token_matches("s3cret", ""))

    def test_token_both_empty_returns_false(self) -> None:
        """Both empty strings returns False."""
        self.assertFalse(internal_token_matches("", ""))

    def test_token_uses_secrets_compare_digest(self) -> None:
        """The helper must call ``secrets.compare_digest`` to prevent
        timing attacks.  Patching it to always return True proves use."""
        with patch(
            "secrets.compare_digest", return_value=True
        ) as mock_cmp:
            result = internal_token_matches("wrong", "also_wrong")
            self.assertTrue(result)
            mock_cmp.assert_called_once()

    def test_token_never_logs_or_returns_token_value(self) -> None:
        """The helper must not log or return the raw token value.
        Calling it should produce no output containing the secret."""
        import io as _io

        captured = _io.StringIO()
        with patch("sys.stderr", captured), patch("sys.stdout", captured):
            internal_token_matches("super_secret_value", "super_secret_value")
        output = captured.getvalue()
        self.assertNotIn("super_secret_value", output)

    # ------------------------------------------------------------------
    # Artifact path resolver: resolve_import_artifact
    # ------------------------------------------------------------------

    def _make_roots(
        self, tmp: Path
    ) -> dict:
        """Build a roots dict with ground and drill sub-directories."""
        ground = tmp / "indexing" / "ground_graph_fill"
        drill = tmp / "indexing_drill" / "drill_graph_fill"
        ground.mkdir(parents=True)
        drill.mkdir(parents=True)
        return {"ground": ground, "drill": drill}

    def test_valid_relative_csv_resolves_under_ground_root(self) -> None:
        """A relative ``foo.csv`` that exists under the ground root resolves
        to that file."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            csv_file = roots["ground"] / "foo.csv"
            csv_file.write_text("h,r,t\n", encoding="utf-8")

            result = resolve_import_artifact("ground", "foo.csv", roots)
            self.assertEqual(result, csv_file)
            self.assertTrue(result.is_file())

    def test_valid_relative_csv_resolves_under_drill_root(self) -> None:
        """A relative ``bar.csv`` that exists under the drill root resolves
        to that file."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            csv_file = roots["drill"] / "bar.csv"
            csv_file.write_text("h,r,t\n", encoding="utf-8")

            result = resolve_import_artifact("drill", "bar.csv", roots)
            self.assertEqual(result, csv_file)
            self.assertTrue(result.is_file())

    def test_unknown_graph_type_rejected(self) -> None:
        """An unrecognized graph_type raises ValueError."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            with self.assertRaises(ValueError) as ctx:
                resolve_import_artifact("unknown", "foo.csv", roots)
            self.assertIn("graph_type", str(ctx.exception).lower())

    def test_absolute_path_rejected(self) -> None:
        """An absolute artifact_path raises ValueError."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            with self.assertRaises(ValueError) as ctx:
                resolve_import_artifact("ground", "/etc/passwd", roots)
            self.assertIn("absolute", str(ctx.exception).lower())

    def test_dot_dot_traversal_rejected(self) -> None:
        """A path containing ``../`` is rejected even if the target exists."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            # Create the target file so the path *would* resolve.
            target = tmp / "secret.csv"
            target.write_text("h,r,t\n", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                resolve_import_artifact(
                    "ground", "../secret.csv", roots
                )
            msg = str(ctx.exception).lower()
            self.assertTrue(
                "traversal" in msg or "parent" in msg or ".." in msg,
                f"Error should mention traversal/parent: {ctx.exception}",
            )

    def test_nested_relative_path_accepted(self) -> None:
        """A nested relative path (``sub/dir/file.csv``) that resolves to a
        regular ``.csv`` file inside the root is accepted."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            nested = roots["ground"] / "sub" / "dir" / "file.csv"
            nested.parent.mkdir(parents=True, exist_ok=True)
            nested.write_text("h,r,t\n", encoding="utf-8")

            result = resolve_import_artifact(
                "ground", "sub/dir/file.csv", roots
            )
            self.assertEqual(result, nested)
            self.assertTrue(result.is_file())

    def test_missing_file_raises_file_not_found(self) -> None:
        """A valid relative path to a non-existent file raises
        FileNotFoundError."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            with self.assertRaises(FileNotFoundError):
                resolve_import_artifact("ground", "no_such.csv", roots)

    def test_directory_rejected_as_non_csv(self) -> None:
        """A path that resolves to a directory (not a file) raises
        ValueError."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            (roots["ground"] / "adir").mkdir()

            with self.assertRaises(ValueError):
                resolve_import_artifact("ground", "adir", roots)

    def test_non_csv_extension_rejected(self) -> None:
        """A file with a non-``.csv`` extension raises ValueError."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            txt_file = roots["ground"] / "data.txt"
            txt_file.write_text("content", encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                resolve_import_artifact("ground", "data.txt", roots)
            self.assertIn(".csv", str(ctx.exception).lower())

    def test_symlink_inside_root_pointing_outside_rejected(self) -> None:
        """A symlink inside the root that points outside the root must be
        rejected.  On platforms where symlink creation fails (e.g. Windows
        without elevated privileges), the test is skipped with the actual
        exception."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._make_roots(tmp)
            # Create target outside the root.
            outside = tmp / "outside_root.csv"
            outside.write_text("h,r,t\n", encoding="utf-8")
            # Create symlink inside the root -- may fail on some platforms.
            link = roots["ground"] / "link.csv"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"symlink creation failed: {exc}")

            with self.assertRaises(ValueError) as ctx:
                resolve_import_artifact("ground", "link.csv", roots)
            msg = str(ctx.exception).lower()
            self.assertTrue(
                "symlink" in msg or "outside" in msg or "traversal" in msg,
                f"Error should mention symlink/outside: {ctx.exception}",
            )

    def test_resolve_import_artifact_env_roots_no_roots_argument(self) -> None:
        """resolve_import_artifact(graph_type, artifact_path) -- without the
        third ``roots`` argument -- must read GRAPH_GROUND_IMPORT_ROOT and
        GRAPH_DRILL_IMPORT_ROOT from the environment and resolve correctly.

        RED: the current signature requires ``roots`` as a positional arg,
        so calling without it raises TypeError.  Once production adds
        ``roots=None`` with env-var fallback this will go green."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ground_root = tmp / "ground_fill"
            drill_root = tmp / "drill_fill"
            ground_root.mkdir()
            drill_root.mkdir()

            ground_csv = ground_root / "data.csv"
            ground_csv.write_text("h,r,t\n", encoding="utf-8")
            drill_csv = drill_root / "data.csv"
            drill_csv.write_text("h,r,t\n", encoding="utf-8")

            env = {
                "GRAPH_GROUND_IMPORT_ROOT": str(ground_root),
                "GRAPH_DRILL_IMPORT_ROOT": str(drill_root),
            }
            with patch.dict(os.environ, env):
                result_ground = resolve_import_artifact("ground", "data.csv")
                self.assertEqual(result_ground, ground_csv)
                self.assertTrue(result_ground.is_file())

                result_drill = resolve_import_artifact("drill", "data.csv")
                self.assertEqual(result_drill, drill_csv)
                self.assertTrue(result_drill.is_file())


# ============================================================================
# InternalImportRouteASTTests -- TDD red AST tests for the internal import route
# ============================================================================


class InternalImportRouteASTTests(unittest.TestCase):
    """AST-only tests that parse ``data_router.py`` without importing it.

    These tests assert the shape of the internal graph import endpoint.
    They will go red until the route is added to the production router.
    """

    ROUTER_PATH = Path(__file__).resolve().parents[1] / "server" / "routers" / "data_router.py"

    @classmethod
    def setUpClass(cls) -> None:
        source = cls.ROUTER_PATH.read_text(encoding="utf-8")
        cls._tree = ast.parse(source, filename=str(cls.ROUTER_PATH))

    # -- helpers ---------------------------------------------------------------

    def _find_endpoint(self) -> ast.AsyncFunctionDef | ast.FunctionDef:
        """Return the function node for ``internal_import_graph_artifact``."""
        for node in ast.walk(self._tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name == "internal_import_graph_artifact":
                return node
        self.fail(
            "No function named 'internal_import_graph_artifact' found in "
            f"{self.ROUTER_PATH}"
        )

    @staticmethod
    def _decorator_paths(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Extract string paths from ``@router.post(...)`` decorators."""
        paths: list[str] = []
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func if isinstance(dec, ast.Call) else dec
            if not isinstance(func, ast.Attribute) or func.attr != "post":
                continue
            if dec.args and isinstance(dec.args[0], ast.Constant):
                paths.append(dec.args[0].value)
        return paths

    @staticmethod
    def _param_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        """Return all parameter names (excluding ``self``)."""
        names = []
        for arg in node.args.args:
            if arg.arg != "self":
                names.append(arg.arg)
        return names

    @staticmethod
    def _call_names_in_node(node: ast.AST) -> set[str]:
        """Return the set of plain function/method call names inside *node*."""
        names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name):
                    names.add(child.func.id)
                elif isinstance(child.func, ast.Attribute):
                    names.add(child.func.attr)
        return names

    @staticmethod
    def _has_depends_on(node: ast.FunctionDef | ast.AsyncFunctionDef, dep_name: str) -> bool:
        """Return True if *node*'s signature or decorator contains ``Depends(dep_name)``."""
        candidates = [*node.args.defaults, *node.args.kw_defaults, *node.decorator_list]
        for candidate in candidates:
            if candidate is None:
                continue
            for child in ast.walk(candidate):
                if not (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)):
                    continue
                if child.func.id != "Depends" or not child.args:
                    continue
                dep = child.args[0]
                if isinstance(dep, ast.Name) and dep.id == dep_name:
                    return True
        return False

    # -- assertions ------------------------------------------------------------

    def test_post_decorator_path_is_exact(self) -> None:
        """The endpoint must be decorated with ``@router.post("/graph/internal/import")``."""
        node = self._find_endpoint()
        paths = self._decorator_paths(node)
        self.assertIn(
            "/graph/internal/import", paths,
            f"Expected POST decorator path '/graph/internal/import', got {paths}",
        )

    def test_endpoint_function_name(self) -> None:
        """The endpoint function must be named ``internal_import_graph_artifact``."""
        node = self._find_endpoint()
        self.assertEqual(node.name, "internal_import_graph_artifact")

    def test_endpoint_calls_required_functions(self) -> None:
        """The endpoint body must call ``internal_token_matches``,
        ``resolve_import_artifact``, and ``GraphImportService``."""
        node = self._find_endpoint()
        calls = self._call_names_in_node(node)
        for required in ("internal_token_matches", "resolve_import_artifact", "GraphImportService"):
            self.assertIn(
                required, calls,
                f"Endpoint body must call {required}; found calls: {sorted(calls)}",
            )

    def test_endpoint_has_header_parameter_x_graph_internal_token(self) -> None:
        """The endpoint must accept a ``Header(...)`` parameter with alias
        ``X-Graph-Internal-Token``."""
        node = self._find_endpoint()
        found = False
        # Walk the function's default arguments and keyword defaults
        candidates = [*node.args.defaults, *node.args.kw_defaults]
        for candidate in candidates:
            if candidate is None:
                continue
            for child in ast.walk(candidate):
                if not (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)):
                    continue
                if child.func.id != "Header":
                    continue
                # Check alias in args or keyword args
                for arg in child.args:
                    if isinstance(arg, ast.Constant) and arg.value == "X-Graph-Internal-Token":
                        found = True
                for kw in child.keywords:
                    if kw.arg == "alias" and isinstance(kw.value, ast.Constant):
                        if kw.value.value == "X-Graph-Internal-Token":
                            found = True
        self.assertTrue(
            found,
            "Endpoint must have a Header parameter with alias 'X-Graph-Internal-Token'",
        )

    def test_endpoint_has_no_user_or_admin_depends(self) -> None:
        """The endpoint must NOT depend on ``get_required_user`` or
        ``get_superadmin_user``."""
        node = self._find_endpoint()
        for guard in ("get_required_user", "get_superadmin_user", "get_admin_user"):
            self.assertFalse(
                self._has_depends_on(node, guard),
                f"Endpoint must not have Depends({guard})",
            )

    def test_request_model_accepts_task_id_graph_type_artifact_path(self) -> None:
        """The request model (Pydantic ``BaseModel``) must accept
        ``task_id``, ``graph_type``, and ``artifact_path`` fields.

        We look for a ``BaseModel`` subclass defined near the endpoint whose
        body contains assignments to those field names."""
        target_fields = {"task_id", "graph_type", "artifact_path"}
        found_model = False
        for node in ast.walk(self._tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Check that one base is a Name(id='BaseModel') or Attribute(attr='BaseModel')
            bases_ok = any(
                (isinstance(b, ast.Name) and b.id == "BaseModel")
                or (isinstance(b, ast.Attribute) and b.attr == "BaseModel")
                for b in node.bases
            )
            if not bases_ok:
                continue
            # Collect assigned names in the class body
            field_names: set[str] = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_names.add(stmt.target.id)
                elif isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            field_names.add(target.id)
            if target_fields.issubset(field_names):
                found_model = True
                break
        self.assertTrue(
            found_model,
            "A BaseModel subclass with fields task_id, graph_type, artifact_path "
            "must exist in the router module",
        )


if __name__ == "__main__":
    unittest.main()
