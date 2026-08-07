"""Canonical graph export and parity comparison."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


NON_SEMANTIC_KEYS = {
    "uuid",
    "id",
    "database_id",
    "db_id",
    "element_id",
    "created_at",
    "updated_at",
    "embedding",
    "name_embedding",
    "fact_embedding",
}


def normalize_ws(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    return value


def _clean_attributes(attrs: Any) -> dict[str, Any]:
    if not isinstance(attrs, dict):
        return {}
    cleaned = {}
    for key, value in attrs.items():
        key_str = str(key)
        lowered = key_str.lower()
        if lowered in NON_SEMANTIC_KEYS or lowered.endswith("_uuid"):
            continue
        cleaned[key_str] = normalize_ws(value)
    return dict(sorted(cleaned.items()))


def canonicalize_entity(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_id": str(raw.get("group_id") or ""),
        "name": normalize_ws(raw.get("name") or "").lower(),
        "labels": sorted(str(label) for label in (raw.get("labels") or []) if str(label)),
        "summary": normalize_ws(raw.get("summary") or ""),
        "attributes": _clean_attributes(raw.get("attributes") or raw),
    }


def _entity_key(raw: dict[str, Any], side: str) -> str:
    for key in (f"{side}_entity_key", f"{side}_name", f"{side}_entity_name", f"{side}_node_name"):
        if raw.get(key):
            return normalize_ws(raw[key]).lower()
    return normalize_ws(raw.get(side) or "").lower()


def canonicalize_edge(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_entity_key": _entity_key(raw, "source"),
        "target_entity_key": _entity_key(raw, "target"),
        "relation_type": normalize_ws(raw.get("relation_type") or raw.get("name") or ""),
        "fact": normalize_ws(raw.get("fact") or raw.get("summary") or ""),
        "valid_at": normalize_ws(raw.get("valid_at")) if raw.get("valid_at") is not None else None,
        "invalid_at": normalize_ws(raw.get("invalid_at")) if raw.get("invalid_at") is not None else None,
        "expired_at": normalize_ws(raw.get("expired_at")) if raw.get("expired_at") is not None else None,
        "attributes": _clean_attributes(raw.get("attributes") or {}),
        "source_episode_sequence": raw.get("source_episode_sequence"),
    }


def canonicalize_graph(raw: dict[str, Any]) -> dict[str, Any]:
    entities = [canonicalize_entity(e) for e in raw.get("entities", [])]
    edges = [canonicalize_edge(e) for e in raw.get("edges", [])]
    episodes = [
        {
            "source_sequence": ep.get("source_sequence"),
            "source_hash": ep.get("source_hash"),
            "session_id": ep.get("session_id"),
        }
        for ep in raw.get("episodes", [])
    ]
    return {
        "entities": sorted(entities, key=_stable_key),
        "edges": sorted(edges, key=_stable_key),
        "episodes": sorted(episodes, key=_stable_key),
    }


def _stable_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_graph_hash(graph: dict[str, Any]) -> str:
    canon = canonicalize_graph(graph)
    return hashlib.sha256(_stable_key(canon).encode()).hexdigest()


def _set(items: list[dict[str, Any]]) -> set[str]:
    return {_stable_key(item) for item in items}


def _prf(left: set[str], right: set[str]) -> tuple[float, float, float]:
    if not left and not right:
        return 1.0, 1.0, 1.0
    inter = len(left & right)
    precision = inter / len(right) if right else 0.0
    recall = inter / len(left) if left else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def compare_canonical_graphs(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    ref = canonicalize_graph(reference)
    cand = canonicalize_graph(candidate)
    ref_entities, cand_entities = _set(ref["entities"]), _set(cand["entities"])
    ref_edges, cand_edges = _set(ref["edges"]), _set(cand["edges"])
    entity_precision, entity_recall, entity_f1 = _prf(ref_entities, cand_entities)
    edge_precision, edge_recall, edge_f1 = _prf(ref_edges, cand_edges)
    episode_count_matches = len(ref["episodes"]) == len(cand["episodes"])
    source_episode_mapping_matches = _set(ref["episodes"]) == _set(cand["episodes"])
    entity_exact = ref_entities == cand_entities
    edge_exact = ref_edges == cand_edges
    return {
        "entity_exact_match": entity_exact,
        "edge_exact_match": edge_exact,
        "entity_set_precision": entity_precision,
        "entity_set_recall": entity_recall,
        "entity_set_f1": entity_f1,
        "edge_set_precision": edge_precision,
        "edge_set_recall": edge_recall,
        "edge_set_f1": edge_f1,
        "episode_count_matches": episode_count_matches,
        "source_episode_mapping_matches": source_episode_mapping_matches,
        "canonical_graph_hash": canonical_graph_hash(candidate),
        "canonical_graph_parity": entity_exact
        and edge_exact
        and episode_count_matches
        and source_episode_mapping_matches,
    }
