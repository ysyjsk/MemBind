"""Strict external-evidence tests for a completed sidecar smoke result."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, sha256_file
from paper_eval.s4_sidecar_authority import consume_s4_sidecar_authority
from paper_eval.s4_sidecar_controller import build_sidecar_result_payload
from paper_eval.s4_sidecar_result import evaluate_s4_sidecar_smoke
from paper_eval.s4_sidecar_smoke_result_verifier import (
    verify_s4_sidecar_smoke_result,
)


PROJECT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = (
    PROJECT
    / "artifacts/paper_eval/native/S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_007.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime(mode: str) -> dict:
    capture = mode == "capture"
    value = {
        "live_llm_calls": 10 if capture else 0,
        "live_embedding_calls": 5 if capture else 0,
        "resolved_prompt_count": 10,
        "resolved_embedding_count": 20,
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
    if not capture:
        value.update(
            {
                "exact_prompt_hit_count": 8,
                "candidate_remap_hit_count": 0,
                "candidate_remap_node_hit_count": 0,
                "candidate_remap_edge_hit_count": 0,
                "candidate_remap_rejection_count": 0,
            }
        )
    return value


def _phase(mode: str) -> dict:
    capture = mode == "capture"
    run_id = (
        "s4-d0-capture-20260815-007"
        if capture
        else "s4-d0-replay-20260815-007"
    )
    namespace = (
        "pev3-s4-u0-capture-20260815-007"
        if capture
        else "pev3-s4-d0-replay-20260815-007"
    )
    return finalize_envelope(
        payload={
            "schema_version": "membind.paper-eval-v3.s4-phase-result.v1",
            "stage": "S4",
            "phase": "U0_CAPTURE" if capture else "D0_READ_ONLY_REPLAY",
            "run_id": run_id,
            "history_id": "07741c45",
            "namespace": namespace,
            "method": "U0" if capture else "D0",
            "mode": mode,
            "cache_id": "s4-d0-sidecar-07741c45-20260815-007",
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


def _fixture(tmp_path: Path) -> dict:
    authority = _load(AUTHORITY_PATH)
    authority_sha = sha256_file(AUTHORITY_PATH)
    consumption_path = tmp_path / "consumption.json"
    consumption = consume_s4_sidecar_authority(
        authority=authority,
        authority_file_sha256=authority_sha,
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s4-sidecar-authority-consumption-20260815-007",
    )
    capture = _phase("capture")
    replay = _phase("replay")
    evaluation = evaluate_s4_sidecar_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    payload = build_sidecar_result_payload(
        evaluation=evaluation,
        authority_file_sha256=authority_sha,
        authority_consumption_file_sha256=sha256_file(consumption_path),
        capture_result_file_sha256="1" * 64,
        replay_result_file_sha256="2" * 64,
        candidate_sidecar_file_sha256="d" * 64,
    )
    result = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-d0-sidecar-smoke-result-20260815-007",
    )
    return {
        "authority": authority,
        "authority_sha": authority_sha,
        "consumption": consumption,
        "consumption_sha": sha256_file(consumption_path),
        "capture": capture,
        "replay": replay,
        "result": result,
    }


def _verify(fixture: dict) -> dict:
    return verify_s4_sidecar_smoke_result(
        result=fixture["result"],
        authority=fixture["authority"],
        authority_file_sha256=fixture["authority_sha"],
        consumption=fixture["consumption"],
        consumption_file_sha256=fixture["consumption_sha"],
        capture_result=fixture["capture"],
        capture_result_file_sha256="1" * 64,
        replay_result=fixture["replay"],
        replay_result_file_sha256="2" * 64,
        candidate_sidecar_file_sha256="d" * 64,
    )


def _refinalize(artifact: dict) -> dict:
    return finalize_envelope(
        payload=artifact["payload"],
        protocol_version=artifact["protocol_version"],
        git_commit=artifact["git_commit"],
        run_id=artifact["run_id"],
    )


def test_verifier_recomputes_all_sidecar_hard_gates(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    assert _verify(fixture) == fixture["result"]


@pytest.mark.parametrize(
    ("target", "mutate"),
    [
        (
            "replay",
            lambda value: value["payload"]["runtime_evidence"].update(
                sidecar_remaining_count=1
            ),
        ),
        (
            "replay",
            lambda value: value["payload"]["runtime_evidence"].update(
                live_llm_calls=1
            ),
        ),
        (
            "replay",
            lambda value: value["payload"]["cache_evidence"].update(
                candidate_sidecar_sha256="0" * 64
            ),
        ),
        (
            "capture",
            lambda value: value["payload"].update(
                canonical_graph_sha256="0" * 64
            ),
        ),
    ],
)
def test_verifier_rejects_phase_or_sidecar_drift(
    tmp_path: Path,
    target: str,
    mutate,
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture[target])
    fixture[target] = _refinalize(fixture[target])

    with pytest.raises(ValueError):
        _verify(fixture)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"].update(verdict="FAIL"),
        lambda value: value["payload"]["authority"].update(s5_authorized=True),
        lambda value: value["payload"]["evaluation"].update(
            sidecar_consumption_exact=False
        ),
        lambda value: value["payload"].update(raw_response="private"),
        lambda value: value.update(run_id="wrong"),
    ],
)
def test_verifier_rejects_result_tamper(
    tmp_path: Path,
    mutate,
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture["result"])
    fixture["result"] = _refinalize(fixture["result"])

    with pytest.raises(ValueError):
        _verify(fixture)


def test_verifier_rejects_external_file_hash_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["consumption_sha"] = "0" * 64

    with pytest.raises(ValueError):
        _verify(fixture)
