"""Fail-closed evaluation tests for the retry-006 bilateral-sidecar smoke."""

from __future__ import annotations

import copy

import pytest

from paper_eval.s4_sidecar_result import evaluate_s4_sidecar_smoke


def _base_runtime(*, capture: bool) -> dict[str, int]:
    return {
        "live_llm_calls": 10 if capture else 0,
        "live_embedding_calls": 10 if capture else 0,
        "resolved_prompt_count": 10,
        "resolved_embedding_count": 10,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
        "sidecar_exact_hit_count": 0 if capture else 2,
        "sidecar_remap_hit_count": 0 if capture else 4,
        "sidecar_rejection_count": 0,
        "sidecar_capture_append_count": 4 if capture else 0,
        "sidecar_capture_reuse_count": 0,
        "sidecar_replay_binding_count": 0 if capture else 4,
        "sidecar_record_count": 4,
        "sidecar_consumed_count": 0 if capture else 4,
        "sidecar_remaining_count": 0,
        "sidecar_resumed_consumed_count": 0,
        "sidecar_prepared_count": 0,
    }


def _phase(*, capture: bool) -> dict:
    runtime = _base_runtime(capture=capture)
    if not capture:
        runtime.update(
            exact_prompt_hit_count=7,
            candidate_remap_hit_count=1,
            candidate_remap_node_hit_count=1,
            candidate_remap_edge_hit_count=0,
            candidate_remap_rejection_count=0,
        )
    return {
        "status": "finalized",
        "payload": {
            "status": "PASS",
            "mode": "capture" if capture else "replay",
            "completed_source_sequences": list(range(49)),
            "expected_episode_count": 49,
            "canonical_graph_sha256": "a" * 64,
            "runtime_evidence": runtime,
            "cache_evidence": {
                "prompt_cache_sha256": "1" * 64,
                "embedding_cache_sha256": "2" * 64,
                "candidate_sidecar_sha256": "3" * 64,
            },
        },
    }


def test_sidecar_smoke_requires_exact_bilateral_coverage_and_parity() -> None:
    evaluation = evaluate_s4_sidecar_smoke(
        capture_result=_phase(capture=True),
        replay_result=_phase(capture=False),
    )

    assert evaluation["verdict"] == "PASS"
    assert evaluation["failures"] == []
    assert evaluation["sidecar_record_count"] == 4
    assert evaluation["sidecar_consumption_exact"] is True
    assert evaluation["edge_sidecar_resolution_accounting"] is True
    assert evaluation["canonical_graph_parity"] is True
    assert evaluation["cache_and_sidecar_mutation_during_replay"] is False
    assert evaluation["s4_four_history_qualification_authorized"] is True
    assert evaluation["s5_authorized"] is False


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("graph", "canonical_graph_parity"),
        ("cache", "cache_or_sidecar_mutation"),
        ("live", "replay_live_model_call"),
        ("sidecar_reject", "sidecar_rejection"),
        ("remaining", "sidecar_consumption"),
        ("consumed", "sidecar_consumption"),
        ("record", "sidecar_record_parity"),
        ("accounting", "edge_sidecar_resolution_accounting"),
        ("edge_legacy", "legacy_edge_remap_used"),
        ("coverage", "replay_episode_coverage"),
    ],
)
def test_sidecar_smoke_fails_closed_on_every_bilateral_gate(
    mutation: str,
    failure: str,
) -> None:
    capture = _phase(capture=True)
    replay = _phase(capture=False)
    runtime = replay["payload"]["runtime_evidence"]
    if mutation == "graph":
        replay["payload"]["canonical_graph_sha256"] = "b" * 64
    elif mutation == "cache":
        replay["payload"]["cache_evidence"]["candidate_sidecar_sha256"] = "4" * 64
    elif mutation == "live":
        runtime["live_llm_calls"] = 1
    elif mutation == "sidecar_reject":
        runtime["sidecar_rejection_count"] = 1
    elif mutation == "remaining":
        runtime["sidecar_remaining_count"] = 1
    elif mutation == "consumed":
        runtime["sidecar_consumed_count"] = 3
    elif mutation == "record":
        runtime["sidecar_record_count"] = 5
    elif mutation == "accounting":
        runtime["sidecar_remap_hit_count"] = 3
    elif mutation == "edge_legacy":
        runtime["candidate_remap_edge_hit_count"] = 1
        runtime["candidate_remap_node_hit_count"] = 0
    else:
        replay["payload"]["completed_source_sequences"].pop()

    evaluation = evaluate_s4_sidecar_smoke(
        capture_result=capture,
        replay_result=replay,
    )

    assert evaluation["verdict"] == "FAIL"
    assert failure in evaluation["failures"]
    assert evaluation["s4_four_history_qualification_authorized"] is False


def test_sidecar_smoke_rejects_partial_counter_shapes() -> None:
    replay = _phase(capture=False)
    del replay["payload"]["runtime_evidence"]["sidecar_prepared_count"]

    evaluation = evaluate_s4_sidecar_smoke(
        capture_result=_phase(capture=True),
        replay_result=replay,
    )

    assert evaluation["verdict"] == "FAIL"
    assert "sidecar_evidence_shape" in evaluation["failures"]
