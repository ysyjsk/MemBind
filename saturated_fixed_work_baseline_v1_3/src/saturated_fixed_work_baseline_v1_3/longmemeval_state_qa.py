"""Conservative graph-only current-state QA for frozen LongMemEval cases.

The direct graph predicate in this module is authoritative.  A later live
runner may use the existing read-only Graphiti search path and the qualified
Reader on the alternate 8002/8003 endpoints, but Reader output is recorded as
diagnostic evidence only and never substitutes for the graph-state result.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


ALT_CHAT_BASE_URL = "http://10.87.5.247:8002/v1"
ALT_EMBEDDING_BASE_URL = "http://10.87.5.247:8003/v1"
READER_MODEL = "qwen3-32b-fp8"
EMBEDDING_MODEL = "qwen3-embedding-0.6b"
EMBEDDING_DIM = 1024

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?![A-Za-z0-9])")
_QUESTION_STOPWORDS = frozenset(
    {
        "what", "where", "when", "which", "who", "how", "many", "much",
        "did", "does", "do", "is", "are", "was", "were", "the", "a", "an",
        "my", "me", "i", "you", "your", "now", "currently", "current", "last",
        "in", "on", "of", "to", "for", "and", "or", "have", "has", "had",
    }
)


class LongMemEvalStateQAError(ValueError):
    """The graph-only current-state contract failed closed."""


def normalize_state_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("×", "x").replace("–", "-").replace("—", "-")
    return " ".join(text.split())


def _parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y/%m/%d (%a) %H:%M")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _contains_answer(answer: str, text: str) -> bool:
    expected = normalize_state_text(answer)
    candidate = normalize_state_text(text)
    if not expected or not candidate:
        return False
    # Numeric answers need token boundaries so an answer such as ``5`` does
    # not match ``50`` or a date embedded in an unrelated fact.
    if _NUMBER_RE.fullmatch(expected):
        return any(normalize_state_text(match.group(0)) == expected for match in _NUMBER_RE.finditer(candidate))
    return expected in candidate


def _edge_text(edge: Mapping[str, Any]) -> str:
    return " ".join(
        str(edge.get(field) or "")
        for field in (
            "fact",
            "source_entity_key",
            "target_entity_key",
            "relation_type",
            "attributes",
        )
    )


def _edge_active(edge: Mapping[str, Any], observation_time: datetime) -> bool:
    valid_at = _parse_time(edge.get("valid_at"))
    invalid_at = _parse_time(edge.get("invalid_at"))
    expired_at = _parse_time(edge.get("expired_at"))
    return (
        (valid_at is None or valid_at <= observation_time)
        and (invalid_at is None or observation_time < invalid_at)
        and (expired_at is None or observation_time < expired_at)
    )


def _group_key(edge: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        normalize_state_text(edge.get("source_entity_key")),
        normalize_state_text(edge.get("relation_type")),
        normalize_state_text(edge.get("target_entity_key")),
    )


def _question_anchor_tokens(question: str | None) -> frozenset[str]:
    if not isinstance(question, str):
        return frozenset()
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+", normalize_state_text(question))
        if token not in _QUESTION_STOPWORDS and len(token) >= 3
    )


def _question_relevant(text: str, question: str | None) -> bool:
    anchors = _question_anchor_tokens(question)
    if not anchors:
        return True
    candidate = set(re.findall(r"[a-z0-9]+", normalize_state_text(text)))
    return bool(anchors.intersection(candidate))


def inspect_longmemeval_current_state(
    graph: Mapping[str, Any],
    *,
    expected_answer: Any,
    observation_time: str,
    question: str | None = None,
) -> dict[str, Any]:
    """Inspect only canonical temporal graph facts for one current answer.

    The predicate is intentionally conservative.  It never treats an entity
    summary as a current fact and never invents an old value.  Multiplicity in
    one source/relation/target group is reported as a structural ambiguity; it
    is not called a semantic stale-value conflict without an explicit old/new
    gold pair.
    """

    if not isinstance(graph, Mapping):
        raise LongMemEvalStateQAError("LONGMEMEVAL_GRAPH_OBJECT_REQUIRED")
    parsed_observation = _parse_time(observation_time)
    if parsed_observation is None:
        raise LongMemEvalStateQAError("LONGMEMEVAL_OBSERVATION_TIME_INVALID")
    if isinstance(expected_answer, bool) or not isinstance(expected_answer, (str, int, float)):
        raise LongMemEvalStateQAError("LONGMEMEVAL_EXPECTED_ANSWER_INVALID")
    expected_text = str(expected_answer)
    edges = graph.get("edges")
    entities = graph.get("entities")
    if not isinstance(edges, list) or not isinstance(entities, list):
        raise LongMemEvalStateQAError("LONGMEMEVAL_GRAPH_SURFACE_INVALID")

    active_edges: list[dict[str, Any]] = []
    inactive_expected_edges: list[dict[str, Any]] = []
    active_expected_edges: list[dict[str, Any]] = []
    unrelated_expected_matches = 0
    for raw_edge in edges:
        if not isinstance(raw_edge, Mapping):
            continue
        edge = dict(raw_edge)
        edge_text = _edge_text(edge)
        matches = _contains_answer(expected_text, edge_text)
        relevant = _question_relevant(edge_text, question)
        if matches and not relevant:
            unrelated_expected_matches += 1
            matches = False
        if _edge_active(edge, parsed_observation):
            active_edges.append(edge)
            if matches:
                active_expected_edges.append(edge)
        elif matches:
            inactive_expected_edges.append(edge)

    entity_summary_matches = [
        {
            "name": str(entity.get("name") or ""),
            "summary": str(entity.get("summary") or ""),
        }
        for entity in entities
        if isinstance(entity, Mapping)
        and _contains_answer(expected_text, " ".join(str(entity.get(field) or "") for field in ("name", "summary", "attributes")))
        and _question_relevant(" ".join(str(entity.get(field) or "") for field in ("name", "summary", "attributes")), question)
    ]

    active_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in active_edges:
        active_groups.setdefault(_group_key(edge), []).append(edge)
    expected_groups = {
        _group_key(edge)
        for edge in active_expected_edges
        if _group_key(edge) != ("", "", "")
    }
    structural_ambiguities = [
        {
            "group": list(group),
            "active_edge_count": len(active_groups[group]),
            "facts": [str(edge.get("fact") or "") for edge in active_groups[group]],
        }
        for group in sorted(expected_groups)
        if len(active_groups[group]) > 1
    ]

    if active_expected_edges and structural_ambiguities:
        status = "AMBIGUOUS"
    elif active_expected_edges:
        status = "PASS"
    elif entity_summary_matches:
        status = "SUMMARY_ONLY"
    elif inactive_expected_edges:
        status = "STALE_ONLY"
    elif unrelated_expected_matches:
        status = "NOT_PROVABLE"
    else:
        status = "FAIL"
    return {
        "status": status,
        "predicate_authoritative": True,
        "predicate_version": "longmemeval-current-state-v1",
        "expected_answer": expected_text,
        "observation_time": observation_time,
        "current_value_active": bool(active_expected_edges),
        "current_value_inactive": bool(inactive_expected_edges),
        "current_value_entity_summary_only": bool(entity_summary_matches and not active_expected_edges),
        "active_expected_edge_count": len(active_expected_edges),
        "inactive_expected_edge_count": len(inactive_expected_edges),
        "active_edge_count": len(active_edges),
        "entity_summary_match_count": len(entity_summary_matches),
        "unrelated_expected_match_count": unrelated_expected_matches,
        "structural_ambiguities": structural_ambiguities,
        "semantic_stale_value_status": "NOT_PROVABLE",
        "old_new_value_source": "NOT_EXPOSED_BY_LONGMEMEVAL",
        "conflict_detection": "STRUCTURAL_GROUP_MULTIPLICITY_ONLY",
        "reader_answer_not_substituted": True,
    }


def reader_diagnostic_verdict(expected_answer: Any, answer: str) -> dict[str, Any]:
    """Report a transparent diagnostic match without promoting it to truth."""

    if not isinstance(answer, str):
        raise LongMemEvalStateQAError("LONGMEMEVAL_READER_ANSWER_INVALID")
    expected = normalize_state_text(expected_answer)
    observed = normalize_state_text(answer)
    return {
        "expected_match": bool(expected and expected in observed),
        "expected_answer_normalized": expected,
        "reader_answer_normalized": observed,
        "semantic_authority": "DIRECT_GRAPH_PREDICATE",
        "status": "DIAGNOSTIC_ONLY",
    }


def paired_state_outcome(
    b0: Mapping[str, Any], b1: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(b0, Mapping) or not isinstance(b1, Mapping):
        raise LongMemEvalStateQAError("LONGMEMEVAL_PAIRED_STATE_INVALID")
    b0_status = str(b0.get("status") or "")
    b1_status = str(b1.get("status") or "")
    if not b0_status or not b1_status:
        raise LongMemEvalStateQAError("LONGMEMEVAL_PAIRED_STATE_STATUS_INVALID")
    return {
        "b0_status": b0_status,
        "b1_status": b1_status,
        "state_divergence": b0_status != b1_status,
        "b0_pass_b1_fail": b0_status == "PASS" and b1_status in {"FAIL", "STALE_ONLY", "SUMMARY_ONLY", "AMBIGUOUS"},
        "b0_eligible": b0_status == "PASS",
        "b1_semantic_failure": b0_status == "PASS" and b1_status in {"FAIL", "STALE_ONLY"},
        "decision_authority": "DIRECT_GRAPH_PREDICATE",
    }


__all__ = [
    "ALT_CHAT_BASE_URL",
    "ALT_EMBEDDING_BASE_URL",
    "READER_MODEL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "LongMemEvalStateQAError",
    "normalize_state_text",
    "inspect_longmemeval_current_state",
    "reader_diagnostic_verdict",
    "paired_state_outcome",
]
