"""Offline verifier tests for the final one-history S4 smoke result."""

from __future__ import annotations

import copy

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope
from paper_eval.s4_smoke_result import verify_s4_smoke_result


def _runtime(mode: str) -> dict:
    return {
        "live_llm_calls": 10 if mode == "capture" else 0,
        "live_embedding_calls": 5 if mode == "capture" else 0,
        "resolved_prompt_count": 10,
        "resolved_embedding_count": 20,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
    }


def _phase(mode: str) -> dict:
    payload = {
        "schema_version": "membind.paper-eval-v3.s4-phase-result.v1",
        "stage": "S4",
        "phase": "U0_CAPTURE" if mode == "capture" else "D0_READ_ONLY_REPLAY",
        "run_id": f"s4-{mode}",
        "history_id": "07741c45",
        "namespace": f"pev3-s4-{mode}",
        "method": "U0" if mode == "capture" else "D0",
        "mode": mode,
        "cache_id": "shared",
        "status": "PASS",
        "mergeable": True,
        "expected_episode_count": 49,
        "completed_source_sequences": list(range(49)),
        "episode_coverage": 1.0,
        "canonical_graph_sha256": "a" * 64,
        "runtime_evidence": _runtime(mode),
        "cache_evidence": {
            "prompt_cache_sha256": "b" * 64,
            "embedding_cache_sha256": "c" * 64,
        },
        "cleanup": {
            "scope": "EXACT_GROUP_ID_ONLY",
            "namespace": f"pev3-s4-{mode}",
            "global_cleanup_used": False,
            "post_cleanup_node_count": 0,
            "post_cleanup_relationship_count": 0,
        },
        "error_class": None,
        "checkpoint_sha256": "d" * 64,
        "events_sha256": "e" * 64,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id=f"s4-{mode}",
    )


def _result(capture: dict, replay: dict) -> dict:
    from paper_eval.s4_d0_runner import evaluate_s4_smoke

    evaluation = evaluate_s4_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    return finalize_envelope(
        payload={
            "schema_version": "membind.paper-eval-v3.s4-d0-smoke-result.v1",
            "stage": "S4",
            "verdict": "PASS",
            "authority_file_sha256": "1" * 64,
            "authority_consumption_file_sha256": "2" * 64,
            "capture_result_file_sha256": "3" * 64,
            "replay_result_file_sha256": "4" * 64,
            "evaluation": evaluation,
            "authority": {
                "s4_four_history_qualification_authorized": True,
                "s5_authorized": False,
                "pilot_execution_authorized": False,
            },
        },
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-d0-smoke-result",
    )


def test_result_recomputes_exact_gates_and_external_file_bindings() -> None:
    capture = _phase("capture")
    replay = _phase("replay")
    result = _result(capture, replay)

    assert verify_s4_smoke_result(
        result=result,
        authority_file_sha256="1" * 64,
        consumption_file_sha256="2" * 64,
        capture_result=capture,
        capture_result_file_sha256="3" * 64,
        replay_result=replay,
        replay_result_file_sha256="4" * 64,
    ) == result


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["payload"].update(verdict="FAIL"),
        lambda value: value["payload"]["evaluation"].update(
            canonical_graph_parity=False
        ),
        lambda value: value["payload"]["authority"].update(s5_authorized=True),
        lambda value: value.update(extra="drift"),
    ],
)
def test_result_rejects_tamper_or_premature_next_stage(mutation) -> None:
    capture = _phase("capture")
    replay = _phase("replay")
    result = _result(capture, replay)
    mutation(result)

    with pytest.raises(ValueError):
        verify_s4_smoke_result(
            result=result,
            authority_file_sha256="1" * 64,
            consumption_file_sha256="2" * 64,
            capture_result=capture,
            capture_result_file_sha256="3" * 64,
            replay_result=replay,
            replay_result_file_sha256="4" * 64,
        )


def test_result_rejects_phase_or_cache_drift() -> None:
    capture = _phase("capture")
    replay = _phase("replay")
    result = _result(capture, replay)
    altered = copy.deepcopy(replay)
    altered["payload"]["cache_evidence"]["prompt_cache_sha256"] = "9" * 64

    with pytest.raises(ValueError):
        verify_s4_smoke_result(
            result=result,
            authority_file_sha256="1" * 64,
            consumption_file_sha256="2" * 64,
            capture_result=capture,
            capture_result_file_sha256="3" * 64,
            replay_result=altered,
            replay_result_file_sha256="4" * 64,
        )

