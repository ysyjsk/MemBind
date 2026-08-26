"""Pure reduction helpers for bounded temporary construction-model probes."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


class DevelopmentModelProbeError(ValueError):
    pass


def sanitize_probe_execution(value: Mapping[str, Any]) -> dict[str, Any]:
    """Retain accounting and outcome only, never provider payload fingerprints."""

    usage = value.get("usage")
    safe_usage: dict[str, int | None] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        observed = usage.get(field) if isinstance(usage, Mapping) else None
        safe_usage[field] = (
            observed
            if isinstance(observed, int) and not isinstance(observed, bool) and observed >= 0
            else None
        )
    return {
        "status": value.get("status"),
        "classification": value.get("classification"),
        "probe_kind": value.get("probe_kind"),
        "http_attempt_count": value.get("http_attempt_count"),
        "finish_reason": value.get("finish_reason"),
        "usage": safe_usage,
        "parsed_item_count": value.get("parsed_item_count"),
        "duration_ns": value.get("duration_ns"),
        "raw_request_persisted": False,
        "raw_response_persisted": False,
        "response_hash_persisted": False,
    }


def _eligible(value: Mapping[str, Any], *, repetitions: int) -> bool:
    node = value.get("node")
    edges = value.get("edges")
    return (
        value.get("available") is True
        and isinstance(node, Mapping)
        and node.get("status") == "PASS"
        and node.get("classification") == "STRUCTURED_EXTRACTION_PARSED"
        and isinstance(edges, list)
        and len(edges) == repetitions
        and all(
            isinstance(edge, Mapping)
            and edge.get("status") == "PASS"
            and edge.get("classification") == "STRUCTURED_EXTRACTION_PARSED"
            for edge in edges
        )
    )


def select_development_model(
    *,
    candidates: Sequence[str],
    results: Sequence[Mapping[str, Any]],
    required_edge_repetitions: int,
) -> dict[str, Any]:
    """Apply the preregistered first-full-pass rule without post-hoc scoring."""

    selected_candidates = list(candidates)
    if (
        not selected_candidates
        or len(set(selected_candidates)) != len(selected_candidates)
        or any(not isinstance(model, str) or not model for model in selected_candidates)
        or isinstance(required_edge_repetitions, bool)
        or not isinstance(required_edge_repetitions, int)
        or required_edge_repetitions <= 0
        or len(results) != len(selected_candidates)
        or any(
            not isinstance(value, Mapping)
            or value.get("model") != model
            for model, value in zip(selected_candidates, results, strict=True)
        )
    ):
        raise DevelopmentModelProbeError("development model probe identity drifted")
    eligible = [
        model
        for model, result in zip(selected_candidates, results, strict=True)
        if _eligible(result, repetitions=required_edge_repetitions)
    ]
    return {
        "schema_version": "membind.v7.development-model-selection.v1",
        "status": "SELECTED" if eligible else "NO_ELIGIBLE_MODEL",
        "selected_model": eligible[0] if eligible else None,
        "eligible_models": eligible,
        "selection_rule": "FIRST_FULL_PASS_IN_FROZEN_ORDER",
        "required_node_passes": 1,
        "required_edge_repetitions": required_edge_repetitions,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
    }


__all__ = [
    "DevelopmentModelProbeError",
    "sanitize_probe_execution",
    "select_development_model",
]
