"""Task 10B -- CSV graph import service."""

from __future__ import annotations

import csv
import os
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Set, Tuple

# Cypher template: MERGE nodes and relationship, SET properties.
# All user data enters via $rows / $kgdb_name parameters -- never interpolated.
CYPHER = """\
UNWIND $rows AS row
MERGE (h:Entity {name: row.h, kgdb_name: $kgdb_name})
MERGE (t:Entity {name: row.t, kgdb_name: $kgdb_name})
MERGE (h)-[r:RELATION {type: row.r, kgdb_name: $kgdb_name}]->(t)
SET r.description = row.r,
    r.updated_at = datetime()
"""


@dataclass(frozen=True)
class ImportStats:
    """Immutable result returned by :meth:`GraphImportService.import_csv`."""

    node_count: int
    relationship_count: int
    embedded_count: int
    vector_index_ready: bool


class GraphImportService:
    """Reads a CSV of (h, r, t) triples, batches MERGE writes into a
    Neo4j-compatible graph, then runs the post-import embedding / index
    / counts pipeline."""

    def __init__(self, graph_db: Any, batch_size: int = 500) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self._graph_db = graph_db
        self._batch_size = batch_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def import_csv(self, path: Path, kgdb_name: str) -> ImportStats:
        """Import triples from *path* into namespace *kgdb_name*.

        Returns an :class:`ImportStats` reflecting post-MERGE graph state.
        """
        # -- Validate inputs -------------------------------------------
        if not isinstance(kgdb_name, str) or not kgdb_name.strip():
            raise ValueError("kgdb_name must be a nonblank string")
        if not isinstance(path, Path) or not path.is_file():
            raise ValueError(f"path must be an existing file: {path}")

        # -- Read & normalize ------------------------------------------
        rows = self._read_and_normalize(path)

        # -- Write batches via one driver session ----------------------
        entity_names: Set[str] = set()
        with self._graph_db.driver.session() as session:
            for batch_start in range(0, len(rows), self._batch_size):
                batch = rows[batch_start : batch_start + self._batch_size]
                result = session.run(CYPHER, {"rows": batch, "kgdb_name": kgdb_name})
                consume = getattr(result, "consume", None)
                if callable(consume):
                    consume()
                for row in batch:
                    entity_names.add(row["h"])
                    entity_names.add(row["t"])

        # -- Post-import pipeline --------------------------------------
        sorted_names = sorted(entity_names)
        embedded_count = self._graph_db.add_embedding_to_nodes(
            node_names=sorted_names,
            kgdb_name=kgdb_name,
            namespace=kgdb_name,
        )
        vector_index_ready = bool(self._graph_db.ensure_entity_vector_index())
        counts = self._graph_db.get_namespace_counts(kgdb_name)

        # Coerce counts to non-negative int.
        node_count = max(0, int(counts.get("node_count", 0)))
        relationship_count = max(0, int(counts.get("relationship_count", 0)))
        # When no entities were imported, embedded count is zero regardless
        # of what the embedding call returned.
        embedded_count = max(0, int(embedded_count)) if sorted_names else 0

        return ImportStats(
            node_count=node_count,
            relationship_count=relationship_count,
            embedded_count=embedded_count,
            vector_index_ready=vector_index_ready,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_and_normalize(path: Path) -> List[Dict[str, str]]:
        """Read CSV, validate headers, trim/deduplicate/sort rows."""
        expected_headers = ["h", "r", "t"]
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError("CSV file is empty or has no headers")
            fieldnames = list(reader.fieldnames)
            # Reject duplicate headers.
            if len(fieldnames) != len(set(fieldnames)):
                raise ValueError(
                    "CSV has duplicate headers"
                )
            # Must be exactly ['h', 'r', 't'] in order.
            if fieldnames != expected_headers:
                present = set(fieldnames)
                required = set(expected_headers)
                missing = required - present
                extra = present - required
                if missing:
                    raise ValueError(
                        f"CSV missing required headers: {', '.join(sorted(missing))}"
                    )
                raise ValueError(
                    f"CSV headers must be exactly ['h', 'r', 't'] in order, "
                    f"got unexpected headers: {fieldnames}"
                )
            # Normalize, filter blanks, dedupe.
            seen: Set[Tuple[str, str, str]] = set()
            for raw in reader:
                h = (raw.get("h") or "").strip()
                r = (raw.get("r") or "").strip()
                t = (raw.get("t") or "").strip()
                if not h or not r or not t:
                    continue
                seen.add((h, r, t))
        # Deterministic sort by (h, r, t).
        return [{"h": h, "r": r, "t": t} for h, r, t in sorted(seen)]


def internal_token_matches(provided: str | None, expected: str | None = None) -> bool:
    """Return True only when *provided* matches *expected* via constant-time comparison.

    If *expected* is ``None`` the value of the ``GRAPH_INTERNAL_TOKEN``
    environment variable is used instead.  Returns ``False`` when either
    side is missing or empty.
    """
    if expected is None:
        expected = os.environ.get("GRAPH_INTERNAL_TOKEN")
    if not isinstance(provided, str) or not provided:
        return False
    if not isinstance(expected, str) or not expected:
        return False
    return secrets.compare_digest(provided, expected)


def resolve_import_artifact(
    graph_type: str,
    artifact_path: str,
    roots: dict[str, Path] | None = None,
) -> Path:
    """Resolve *artifact_path* under the appropriate root for *graph_type*.

    Validates that the path is relative, has a ``.csv`` extension, does not
    escape the root via ``..`` traversal, and that the target exists and is a
    regular file.  Symlinks that resolve outside the root are rejected.

    When *roots* is ``None``, the root directories are read from the
    environment variables ``GRAPH_GROUND_IMPORT_ROOT`` (default
    ``/app/indexing/ground_graph_fill``) and ``GRAPH_DRILL_IMPORT_ROOT``
    (default ``/app/indexing_drill/drill_graph_fill``).
    """
    if roots is None:
        roots = {
            "ground": Path(os.environ.get(
                "GRAPH_GROUND_IMPORT_ROOT", "/app/indexing/ground_graph_fill",
            )),
            "drill": Path(os.environ.get(
                "GRAPH_DRILL_IMPORT_ROOT", "/app/indexing_drill/drill_graph_fill",
            )),
        }

    if graph_type not in roots:
        raise ValueError(f"Unknown graph_type: {graph_type}")

    if PurePosixPath(artifact_path).is_absolute() or PureWindowsPath(artifact_path).is_absolute():
        raise ValueError(f"artifact_path must be relative, got absolute: {artifact_path}")

    if ".." in PurePosixPath(artifact_path).parts or ".." in PureWindowsPath(artifact_path).parts:
        raise ValueError(f"Path traversal (..) is not allowed: {artifact_path}")

    root_resolved = Path(roots[graph_type]).resolve()
    target = (root_resolved / artifact_path).resolve()

    # The resolved target must be inside the root.
    if not target.is_relative_to(root_resolved):
        raise ValueError(f"Path resolves outside root (symlink or traversal): {artifact_path}")

    if target.is_dir():
        raise ValueError(f"Artifact path resolves to a directory, not a file: {artifact_path}")

    if not target.suffix.lower() == ".csv":
        raise ValueError(f"Artifact must have .csv extension, got: {artifact_path}")

    if not target.is_file():
        raise FileNotFoundError(f"Artifact file not found: {artifact_path}")

    return target
