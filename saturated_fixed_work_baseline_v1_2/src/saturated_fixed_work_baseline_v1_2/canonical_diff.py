"""Structured semantic diff over the project's canonical graph projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .reuse import import_validation_module


def _stable(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _symmetric_count(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
    projection: Callable[[Mapping[str, Any]], Any],
) -> int:
    left_set = {_stable(projection(row)) for row in left}
    right_set = {_stable(projection(row)) for row in right}
    return len(left_set ^ right_set)


_LOGICAL_FORMAL_GROUP_ID = "__FORMAL_HISTORY_NAMESPACE__"


def _project_formal_namespace(
    graph: dict[str, Any], namespace: str
) -> int:
    """Replace only the explicitly paired Graphiti group identity."""

    replacements = 0
    for row in graph.get("entities", []):
        if row.get("group_id") == namespace:
            row["group_id"] = _LOGICAL_FORMAL_GROUP_ID
            replacements += 1
        attributes = row.get("attributes")
        if isinstance(attributes, dict) and attributes.get("group_id") == namespace:
            attributes["group_id"] = _LOGICAL_FORMAL_GROUP_ID
    return replacements


def canonical_diff(
    reference: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    repository_root: Path,
    reference_namespace: str | None = None,
    candidate_namespace: str | None = None,
) -> dict[str, Any]:
    if (reference_namespace is None) != (candidate_namespace is None):
        raise ValueError("CANONICAL_NAMESPACE_PAIR_REQUIRED")
    if reference_namespace is not None and (
        not reference_namespace or not candidate_namespace
    ):
        raise ValueError("CANONICAL_NAMESPACE_PAIR_INVALID")
    module = import_validation_module(repository_root, "canonicalize_graph")
    left = module.canonicalize_graph(dict(reference))
    right = module.canonicalize_graph(dict(candidate))
    left_replacements = 0
    right_replacements = 0
    if reference_namespace is not None and candidate_namespace is not None:
        left_replacements = _project_formal_namespace(left, reference_namespace)
        right_replacements = _project_formal_namespace(right, candidate_namespace)
    left_entities = left["entities"]
    right_entities = right["entities"]
    left_edges = left["edges"]
    right_edges = right["edges"]
    left_episodes = left["episodes"]
    right_episodes = right["episodes"]
    differences = {
        "entity_key": _symmetric_count(
            left_entities,
            right_entities,
            lambda row: (row["group_id"], row["name"]),
        ),
        "edge_key": _symmetric_count(
            left_edges,
            right_edges,
            lambda row: (
                row["source_entity_key"],
                row["target_entity_key"],
                row["relation_type"],
                row["fact"],
            ),
        ),
        "attribute": _symmetric_count(
            left_entities,
            right_entities,
            lambda row: (
                row["group_id"],
                row["name"],
                row["labels"],
                row["summary"],
                row["attributes"],
            ),
        ),
        "temporal": _symmetric_count(
            left_edges,
            right_edges,
            lambda row: (
                row["source_entity_key"],
                row["target_entity_key"],
                row["relation_type"],
                row["fact"],
                row["valid_at"],
                row["invalid_at"],
                row["expired_at"],
            ),
        ),
        "source_link": _symmetric_count(
            left_episodes,
            right_episodes,
            lambda row: row,
        )
        + _symmetric_count(
            left_edges,
            right_edges,
            lambda row: (
                row["source_entity_key"],
                row["target_entity_key"],
                row["relation_type"],
                row["fact"],
                row["source_episode_sequence"],
            ),
        ),
    }
    left_hash = module.canonical_graph_hash(left)
    right_hash = module.canonical_graph_hash(right)
    return {
        "schema_version": "membind.saturated-fixed-work.canonical-diff.v1",
        "reference_hash": left_hash,
        "candidate_hash": right_hash,
        "exact_match": left_hash == right_hash,
        "difference_counts": differences,
        "reference_counts": {
            "entities": len(left_entities),
            "edges": len(left_edges),
            "episodes": len(left_episodes),
        },
        "candidate_counts": {
            "entities": len(right_entities),
            "edges": len(right_edges),
            "episodes": len(right_episodes),
        },
        "namespace_projection": {
            "applied": reference_namespace is not None,
            "logical_group_id": (
                _LOGICAL_FORMAL_GROUP_ID if reference_namespace is not None else None
            ),
            "reference_replacements": left_replacements,
            "candidate_replacements": right_replacements,
        },
    }


__all__ = ["canonical_diff"]
