"""Graph job data schemas.

Public dataclasses only — no database imports, no side-effects on import.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


VALID_GRAPH_TYPES: List[str] = ["ground", "drill"]

VALID_STATUSES: List[str] = [
    "queued",
    "copying",
    "building",
    "converting",
    "importing",
    "indexing",
    "completed",
    "failed",
    "cancelling",
    "cancelled",
    "interrupted",
]

# status -> set of legal target statuses
# Active statuses also allow self-transitions for progress/stage/log updates.
# Terminal statuses have NO outgoing transitions; use retry() to re-queue.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued":      {"queued", "copying", "failed", "cancelled", "interrupted"},
    "copying":     {"copying", "building", "failed", "cancelled", "interrupted"},
    "building":    {"building", "converting", "failed", "cancelled", "interrupted"},
    "converting":  {"converting", "importing", "failed", "cancelled", "interrupted"},
    "importing":   {"importing", "indexing", "failed", "cancelled", "interrupted"},
    "indexing":    {"indexing", "completed", "failed", "cancelled", "interrupted"},
    "completed":   set(),    # via retry only
    "failed":      set(),    # via retry only
    "cancelled":   set(),    # via retry only
    "interrupted": set(),    # via retry only
    "cancelling":  {"cancelling", "cancelled", "failed", "interrupted"},
}

# statuses that block a new job of the same graph_type
ACTIVE_STATUSES: set[str] = {
    "queued", "copying", "building", "converting", "importing", "indexing", "cancelling",
}

TERMINAL_STATUSES: set[str] = {"completed", "failed", "cancelled", "interrupted"}


@dataclass
class JobRecord:
    """Immutable snapshot of a graph job row."""

    id: str
    graph_type: str
    status: str
    stage: str
    progress: int
    created_at: str
    started_at: str | None
    finished_at: str | None
    cancel_requested: bool
    input_count: int
    relationship_count: int
    artifact_path: str
    artifact_sha256: str
    error_summary: str
    log_tail: str
