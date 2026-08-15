"""Fail-closed evaluation for the S4 bilateral-sidecar smoke."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


SCHEMA = "membind.paper-eval-v3.s4-sidecar-smoke-evaluation.v1"
_BASE_FIELDS = {
    "live_llm_calls",
    "live_embedding_calls",
    "resolved_prompt_count",
    "resolved_embedding_count",
    "unexpected_prompt_count",
    "unexpected_embedding_count",
    "live_fallback_count",
    "cross_encoder_call_count",
}
_SIDECAR_FIELDS = {
    "sidecar_exact_hit_count",
    "sidecar_remap_hit_count",
    "sidecar_rejection_count",
    "sidecar_capture_append_count",
    "sidecar_capture_reuse_count",
    "sidecar_replay_binding_count",
    "sidecar_record_count",
    "sidecar_consumed_count",
    "sidecar_remaining_count",
    "sidecar_resumed_consumed_count",
    "sidecar_prepared_count",
}
_REMAP_FIELDS = {
    "exact_prompt_hit_count",
    "candidate_remap_hit_count",
    "candidate_remap_node_hit_count",
    "candidate_remap_edge_hit_count",
    "candidate_remap_rejection_count",
}
_CACHE_FIELDS = {
    "prompt_cache_sha256",
    "embedding_cache_sha256",
    "candidate_sidecar_sha256",
}


def _mapping(value: object) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _nonnegative_ints(value: Mapping[str, Any], fields: set[str]) -> bool:
    return all(
        isinstance(value.get(field), int)
        and not isinstance(value.get(field), bool)
        and int(value[field]) >= 0
        for field in fields
    )


def evaluate_s4_sidecar_smoke(
    *,
    capture_result: Mapping[str, Any],
    replay_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the one-history retry without granting later live stages."""

    capture = _mapping(capture_result.get("payload"))
    replay = _mapping(replay_result.get("payload"))
    capture_runtime = _mapping(capture.get("runtime_evidence"))
    replay_runtime = _mapping(replay.get("runtime_evidence"))
    capture_cache = _mapping(capture.get("cache_evidence"))
    replay_cache = _mapping(replay.get("cache_evidence"))
    failures: list[str] = []

    def fail(code: str) -> None:
        if code not in failures:
            failures.append(code)

    complete = list(range(49))
    if (
        capture.get("status") != "PASS"
        or capture.get("expected_episode_count") != 49
        or capture.get("completed_source_sequences") != complete
    ):
        fail("capture_episode_coverage")
    if (
        replay.get("status") != "PASS"
        or replay.get("expected_episode_count") != 49
        or replay.get("completed_source_sequences") != complete
    ):
        fail("replay_episode_coverage")

    graph_parity = (
        isinstance(capture.get("canonical_graph_sha256"), str)
        and capture.get("canonical_graph_sha256")
        == replay.get("canonical_graph_sha256")
    )
    if not graph_parity:
        fail("canonical_graph_parity")

    cache_shape = set(capture_cache) == _CACHE_FIELDS == set(replay_cache)
    cache_mutation = not cache_shape or capture_cache != replay_cache
    if cache_mutation:
        fail("cache_or_sidecar_mutation")

    capture_shape = set(capture_runtime) == _BASE_FIELDS | _SIDECAR_FIELDS
    replay_shape = set(replay_runtime) == (
        _BASE_FIELDS | _SIDECAR_FIELDS | _REMAP_FIELDS
    )
    evidence_shape = (
        capture_shape
        and replay_shape
        and _nonnegative_ints(capture_runtime, _BASE_FIELDS | _SIDECAR_FIELDS)
        and _nonnegative_ints(
            replay_runtime, _BASE_FIELDS | _SIDECAR_FIELDS | _REMAP_FIELDS
        )
    )
    if not evidence_shape:
        fail("sidecar_evidence_shape")

    if evidence_shape:
        if (
            capture_runtime["live_llm_calls"] <= 0
            or capture_runtime["live_embedding_calls"] <= 0
        ):
            fail("capture_live_model_call")
        if any(
            capture_runtime[field] != 0
            for field in (
                "unexpected_prompt_count",
                "unexpected_embedding_count",
                "live_fallback_count",
                "cross_encoder_call_count",
            )
        ):
            fail("capture_runtime_anomaly")
        if (
            replay_runtime["live_llm_calls"] != 0
            or replay_runtime["live_embedding_calls"] != 0
        ):
            fail("replay_live_model_call")
        if (
            replay_runtime["unexpected_prompt_count"] != 0
            or replay_runtime["unexpected_embedding_count"] != 0
        ):
            fail("replay_oracle_miss")
        if replay_runtime["live_fallback_count"] != 0:
            fail("replay_live_fallback")
        if replay_runtime["cross_encoder_call_count"] != 0:
            fail("replay_cross_encoder_call")
        for field in ("resolved_prompt_count", "resolved_embedding_count"):
            if capture_runtime[field] != replay_runtime[field]:
                fail(field)

        if (
            capture_runtime["sidecar_rejection_count"] != 0
            or replay_runtime["sidecar_rejection_count"] != 0
        ):
            fail("sidecar_rejection")
        if replay_runtime["candidate_remap_rejection_count"] != 0:
            fail("candidate_remap_rejection")
        if replay_runtime["candidate_remap_edge_hit_count"] != 0:
            fail("legacy_edge_remap_used")
        if (
            replay_runtime["candidate_remap_node_hit_count"]
            + replay_runtime["candidate_remap_edge_hit_count"]
            != replay_runtime["candidate_remap_hit_count"]
        ):
            fail("candidate_remap_breakdown")

        capture_records = capture_runtime["sidecar_record_count"]
        replay_records = replay_runtime["sidecar_record_count"]
        if capture_records <= 0 or capture_records != replay_records:
            fail("sidecar_record_parity")
        if capture_runtime["sidecar_capture_append_count"] != capture_records:
            fail("capture_sidecar_accounting")
        if replay_runtime["sidecar_replay_binding_count"] != replay_records:
            fail("replay_sidecar_accounting")
        consumption_exact = (
            replay_runtime["sidecar_prepared_count"] == 0
            and replay_runtime["sidecar_remaining_count"] == 0
            and replay_runtime["sidecar_consumed_count"] == replay_records
        )
        if not consumption_exact:
            fail("sidecar_consumption")

        exact_non_sidecar = (
            replay_runtime["exact_prompt_hit_count"]
            - replay_runtime["sidecar_exact_hit_count"]
        )
        sidecar_accounting = (
            exact_non_sidecar >= 0
            and replay_runtime["sidecar_exact_hit_count"]
            <= replay_runtime["sidecar_remap_hit_count"]
            and replay_runtime["sidecar_remap_hit_count"] == replay_records
            and exact_non_sidecar
            + replay_runtime["candidate_remap_hit_count"]
            + replay_runtime["sidecar_remap_hit_count"]
            == replay_runtime["resolved_prompt_count"]
        )
        if not sidecar_accounting:
            fail("edge_sidecar_resolution_accounting")
    else:
        capture_records = 0
        replay_records = 0
        consumption_exact = False
        sidecar_accounting = False

    verdict = "PASS" if not failures else "FAIL"
    return {
        "schema_version": SCHEMA,
        "verdict": verdict,
        "failures": failures,
        "canonical_graph_parity": graph_parity,
        "cache_and_sidecar_mutation_during_replay": cache_mutation,
        "sidecar_record_count": capture_records,
        "replay_sidecar_record_count": replay_records,
        "sidecar_consumption_exact": consumption_exact,
        "edge_sidecar_resolution_accounting": sidecar_accounting,
        "s4_four_history_qualification_authorized": verdict == "PASS",
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }
