"""TDD for parameterized fixed-three sidecar block results."""

from __future__ import annotations

import copy

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope
from paper_eval.s4_sidecar_qualification_result import (
    build_s4_sidecar_fixed_three_result,
    build_s4_sidecar_qualification_block_result,
    evaluate_s4_sidecar_qualification_block,
    verify_s4_sidecar_fixed_three_result,
    verify_s4_sidecar_qualification_block_result,
    verify_s4_sidecar_qualification_block_result_external,
)


BASE_FIELDS = {
    "live_llm_calls",
    "live_embedding_calls",
    "resolved_prompt_count",
    "resolved_embedding_count",
    "unexpected_prompt_count",
    "unexpected_embedding_count",
    "live_fallback_count",
    "cross_encoder_call_count",
}
SIDECAR_FIELDS = {
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
REMAP_FIELDS = {
    "exact_prompt_hit_count",
    "candidate_remap_hit_count",
    "candidate_remap_node_hit_count",
    "candidate_remap_edge_hit_count",
    "candidate_remap_rejection_count",
}


def _runtime(mode: str) -> dict[str, int]:
    result = {field: 0 for field in BASE_FIELDS | SIDECAR_FIELDS}
    result.update(
        live_llm_calls=10 if mode == "capture" else 0,
        live_embedding_calls=8 if mode == "capture" else 0,
        resolved_prompt_count=10,
        resolved_embedding_count=20,
        sidecar_record_count=3,
    )
    if mode == "capture":
        result["sidecar_capture_append_count"] = 3
    else:
        result.update({field: 0 for field in REMAP_FIELDS})
        result.update(
            exact_prompt_hit_count=10,
            sidecar_exact_hit_count=3,
            sidecar_remap_hit_count=3,
            sidecar_replay_binding_count=3,
            sidecar_consumed_count=3,
        )
    return result


def _phase(mode: str, history_id: str, count: int) -> dict:
    capture = mode == "capture"
    run_id = f"s4q-{'u0' if capture else 'd0'}-{history_id}-001"
    namespace = f"pev3-s4-{'u0' if capture else 'd0'}-qual-{history_id}-001"
    return finalize_envelope(
        payload={
            "schema_version": "membind.paper-eval-v3.s4-phase-result.v1",
            "stage": "S4",
            "phase": "U0_CAPTURE" if capture else "D0_READ_ONLY_REPLAY",
            "run_id": run_id,
            "history_id": history_id,
            "namespace": namespace,
            "method": "U0" if capture else "D0",
            "mode": mode,
            "cache_id": f"s4q-d0-{history_id}-001",
            "status": "PASS",
            "mergeable": True,
            "expected_episode_count": count,
            "completed_source_sequences": list(range(count)),
            "episode_coverage": 1.0,
            "canonical_graph_sha256": "a" * 64,
            "runtime_evidence": _runtime(mode),
            "cache_evidence": {
                "prompt_cache_sha256": "b" * 64,
                "embedding_cache_sha256": "c" * 64,
                "candidate_sidecar_sha256": "d" * 64,
            },
            "cleanup": {
                "scope": "EXACT_GROUP_ID_ONLY",
                "namespace": namespace,
                "global_cleanup_used": False,
                "post_cleanup_node_count": 0,
                "post_cleanup_relationship_count": 0,
            },
            "error_class": None,
            "checkpoint_sha256": "e" * 64,
            "events_sha256": "f" * 64,
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id=run_id,
    )


@pytest.mark.parametrize(
    ("history_id", "count"),
    [("b6019101", 49), ("6071bd76", 46), ("a2f3aa27", 44)],
)
def test_evaluator_accepts_exact_dynamic_coverage_and_sidecar_accounting(
    history_id: str, count: int
) -> None:
    evaluation = evaluate_s4_sidecar_qualification_block(
        capture_result=_phase("capture", history_id, count),
        replay_result=_phase("replay", history_id, count),
        history_id=history_id,
        expected_episode_count=count,
    )

    assert evaluation["verdict"] == "PASS"
    assert evaluation["failures"] == []
    assert evaluation["canonical_graph_parity"] is True
    assert evaluation["semantic_work_ratio"] == 1.0
    assert evaluation["s5_authorized"] is False


def test_evaluator_fails_closed_on_dynamic_coverage_or_work_drift() -> None:
    capture = _phase("capture", "6071bd76", 46)
    replay = _phase("replay", "6071bd76", 46)
    replay["payload"]["completed_source_sequences"] = list(range(45))
    replay["payload"]["runtime_evidence"]["resolved_prompt_count"] = 9

    evaluation = evaluate_s4_sidecar_qualification_block(
        capture_result=capture,
        replay_result=replay,
        history_id="6071bd76",
        expected_episode_count=46,
    )

    assert evaluation["verdict"] == "FAIL"
    assert "replay_episode_coverage" in evaluation["failures"]
    assert "resolved_prompt_count" in evaluation["failures"]


def test_block_result_binds_external_files_and_never_authorizes_s5() -> None:
    capture = _phase("capture", "a2f3aa27", 44)
    replay = _phase("replay", "a2f3aa27", 44)
    artifact = build_s4_sidecar_qualification_block_result(
        history_id="a2f3aa27",
        block_index=2,
        expected_episode_count=44,
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consumption_file_sha256="3" * 64,
        capture_result=capture,
        capture_result_file_sha256="4" * 64,
        replay_result=replay,
        replay_result_file_sha256="5" * 64,
        candidate_sidecar_file_sha256="d" * 64,
        git_commit="deadbeef",
    )
    verified = verify_s4_sidecar_qualification_block_result(artifact)

    assert verified["payload"]["verdict"] == "PASS"
    assert verified["payload"]["next_block_authorized"] is False
    assert verified["payload"]["fixed_three_aggregation_authorized"] is True
    assert verified["payload"]["s5_authorized"] is False
    assert verified["payload"]["paired_descriptive_status"] == (
        "NOT_EXECUTED_IN_CONSTRUCTION_CORRECTNESS_LANE"
    )

    tampered = copy.deepcopy(artifact)
    tampered["payload"]["s5_authorized"] = True
    tampered = finalize_envelope(
        payload=tampered["payload"],
        protocol_version=tampered["protocol_version"],
        git_commit=tampered["git_commit"],
        run_id=tampered["run_id"],
    )
    with pytest.raises(ValueError):
        verify_s4_sidecar_qualification_block_result(tampered)


def _block_result(history_id: str, block_index: int, count: int) -> dict:
    return build_s4_sidecar_qualification_block_result(
        history_id=history_id,
        block_index=block_index,
        expected_episode_count=count,
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consumption_file_sha256="3" * 64,
        capture_result=_phase("capture", history_id, count),
        capture_result_file_sha256=(str(block_index + 4) * 64),
        replay_result=_phase("replay", history_id, count),
        replay_result_file_sha256=(str(block_index + 7) * 64),
        candidate_sidecar_file_sha256="d" * 64,
        git_commit="deadbeef",
    )


def test_external_verifier_recomputes_block_from_phase_evidence() -> None:
    capture = _phase("capture", "b6019101", 49)
    replay = _phase("replay", "b6019101", 49)
    result = _block_result("b6019101", 0, 49)

    assert verify_s4_sidecar_qualification_block_result_external(
        result=result,
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consumption_file_sha256="3" * 64,
        capture_result=capture,
        capture_result_file_sha256="4" * 64,
        replay_result=replay,
        replay_result_file_sha256="7" * 64,
        candidate_sidecar_file_sha256="d" * 64,
    ) == result

    replay["payload"]["runtime_evidence"]["resolved_prompt_count"] = 9
    with pytest.raises(ValueError, match="external evidence"):
        verify_s4_sidecar_qualification_block_result_external(
            result=result,
            authority_file_sha256="1" * 64,
            authority_payload_sha256="2" * 64,
            consumption_file_sha256="3" * 64,
            capture_result=capture,
            capture_result_file_sha256="4" * 64,
            replay_result=replay,
            replay_result_file_sha256="7" * 64,
            candidate_sidecar_file_sha256="d" * 64,
        )


def test_fixed_three_aggregate_requires_all_ordered_passes_and_stays_non_s5() -> None:
    results = [
        _block_result("b6019101", 0, 49),
        _block_result("6071bd76", 1, 46),
        _block_result("a2f3aa27", 2, 44),
    ]
    artifact = build_s4_sidecar_fixed_three_result(
        authority_file_sha256="1" * 64,
        authority_payload_sha256="2" * 64,
        consumption_file_sha256="3" * 64,
        activation_file_sha256="a" * 64,
        activation_payload_sha256="b" * 64,
        block_results=results,
        block_result_file_sha256={
            history_id: character * 64
            for history_id, character in zip(
                ("b6019101", "6071bd76", "a2f3aa27"),
                ("4", "5", "6"),
                strict=True,
            )
        },
        git_commit="deadbeef",
    )
    payload = verify_s4_sidecar_fixed_three_result(artifact)["payload"]

    assert payload["verdict"] == "PASS"
    assert payload["completed_history_ids"] == [
        "b6019101",
        "6071bd76",
        "a2f3aa27",
    ]
    assert payload["construction_correctness_status"] == "PASS"
    assert payload["full_s4_freeze_authorized"] is False
    assert payload["s5_authorized"] is False

    with pytest.raises(ValueError, match="ordered strict PASS"):
        build_s4_sidecar_fixed_three_result(
            authority_file_sha256="1" * 64,
            authority_payload_sha256="2" * 64,
            consumption_file_sha256="3" * 64,
            activation_file_sha256="a" * 64,
            activation_payload_sha256="b" * 64,
            block_results=list(reversed(results)),
            block_result_file_sha256={
                "b6019101": "4" * 64,
                "6071bd76": "5" * 64,
                "a2f3aa27": "6" * 64,
            },
            git_commit="deadbeef",
        )
