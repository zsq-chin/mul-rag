"""Pure helpers for graph retrieval: entity normalization, relation ranking,
and context formatting.

These functions have no side effects and no dependency on Neo4j, models,
or the knowledge base.  The Retriever delegates to them after obtaining
raw rows from graph_base.query_node().
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# ---- Hard caps (cannot be overridden by config) ---------------------------

MAX_ENTITIES = 20
MAX_HOPS = 3
MAX_RELATIONS = 100


def normalize_entities(raw: List[Any], bound: int) -> List[str]:
    """Trim, remove blanks/None, preserve first-seen order, deduplicate,
    and enforce *bound*.

    Returns a list of unique non-empty strings, preserving the order of
    first appearance, truncated to *bound* items.
    """
    if bound <= 0:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        if item is None:
            continue
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
        if len(result) >= bound:
            break
    return result


def rank_unique_relations(
    rows: List[Dict[str, Any]],
    bound: int,
) -> List[Dict[str, Any]]:
    """Normalize, rank by score desc, deduplicate by (source, relation, target),
    assign stable ``ref_id`` values (G1, G2, …), and enforce *bound*.

    Tie-breaking is deterministic: when scores are equal, sort by
    ``(source, relation, target)`` lexicographically.

    Each returned dict is a *new* dict containing at least:
    ``ref_id``, ``source``, ``target``, ``relation``, ``score``,
    ``source_desc``, ``target_desc``, ``relation_desc``.
    """
    if not rows or bound <= 0:
        return []

    # Deduplicate by (source, relation, target), keeping the highest score.
    best: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        src = str(row.get("source", "")).strip()
        tgt = str(row.get("target", "")).strip()
        rel = str(row.get("relation", "")).strip()
        if not src or not tgt or not rel:
            continue
        key = (src, rel, tgt)
        raw_score = row.get("score", 0.0)
        try:
            score = float(raw_score)
            if score != score or score == float("inf") or score == float("-inf"):
                score = 0.0
        except (TypeError, ValueError):
            score = 0.0
        if key not in best or score > best[key]["score"]:
            entry: Dict[str, Any] = {
                "source": src,
                "target": tgt,
                "relation": rel,
                "score": score,
                "source_desc": str(row.get("source_desc", "")),
                "target_desc": str(row.get("target_desc", "")),
                "relation_desc": str(row.get("relation_desc", "")),
            }
            # Preserve sidebar metadata when present so that
            # format_query_result_to_graph can use real element IDs.
            for meta_key in (
                "source_id", "target_id",
                "source_properties", "target_properties",
                "relation_id",
            ):
                if meta_key in row and row[meta_key] is not None:
                    entry[meta_key] = row[meta_key]
            best[key] = entry

    # Sort: score descending, then deterministic tie-break by (source, relation, target).
    ranked = sorted(
        best.values(),
        key=lambda r: (-r["score"], r["source"], r["relation"], r["target"]),
    )

    # Enforce bound.
    ranked = ranked[:bound]

    # Assign stable ref_ids.
    for i, r in enumerate(ranked, start=1):
        r["ref_id"] = f"G{i}"

    return ranked


def format_graph_context(
    ranked: List[Dict[str, Any]],
    max_chars: int = 2000,
) -> str:
    """Format ranked relations into prompt context lines.

    Each line: ``[G1] source --relation--> target`` plus optional descriptions.
    Total output is capped at *max_chars* characters without producing malformed
    partial references — a relation line is included only if it fits entirely.
    """
    if not ranked:
        return ""

    lines: list[str] = []
    current_len = 0

    for r in ranked:
        ref_id = r.get("ref_id", "G?")
        src = r.get("source", "")
        tgt = r.get("target", "")
        rel = r.get("relation", "")
        src_desc = r.get("source_desc", "")
        tgt_desc = r.get("target_desc", "")
        rel_desc = r.get("relation_desc", "")

        line = f"[{ref_id}] {src} --{rel}--> {tgt}"

        # Append descriptions if present.
        desc_parts = []
        if src_desc:
            desc_parts.append(f"源: {src_desc}")
        if rel_desc:
            desc_parts.append(f"关系: {rel_desc}")
        if tgt_desc:
            desc_parts.append(f"目标: {tgt_desc}")
        if desc_parts:
            line += " | " + "; ".join(desc_parts)

        # Enforce character cap: only add if the whole line fits.
        sep_len = 1 if lines else 0  # newline separator
        if current_len + sep_len + len(line) > max_chars:
            break

        lines.append(line)
        current_len += sep_len + len(line)

    return "\n".join(lines)
