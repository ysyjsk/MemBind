"""Offline verifier tests for the S4 candidate-remap smoke result."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval import PROTOCOL_VERSION
from paper_eval.artifacts import finalize_envelope, sha256_file
from paper_eval.s4_d0_runner import evaluate_s4_smoke
from paper_eval.s4_remap_authority import consume_s4_remap_authority
from paper_eval.s4_remap_controller import build_remap_result_payload
from paper_eval.s4_remap_result import verify_s4_remap_smoke_result


PROJECT = Path(__file__).resolve().parents[1]
AUTHORITY_PATH = (
    PROJECT
    / "artifacts/paper_eval/native/S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime(mode: str) -> dict:
    base = {
        "live_llm_calls": 10 if mode == "capture" else 0,
        "live_embedding_calls": 5 if mode == "capture" else 0,
        "resolved_prompt_count": 10,
        "resolved_embedding_count": 20,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "cross_encoder_call_count": 0,
    }
    if mode == "replay":
        base.update(
            {
                "exact_prompt_hit_count": 8,
                "candidate_remap_hit_count": 2,
                "candidate_remap_node_hit_count": 1,
                "candidate_remap_edge_hit_count": 1,
                "candidate_remap_rejection_count": 0,
            }
        )
    return base


def _phase(mode: str) -> dict:
    capture = mode == "capture"
    run_id = (
        "s4-d0-capture-20260815-005"
        if capture
        else "s4-d0-replay-20260815-005"
    )
    namespace = (
        "pev3-s4-u0-capture-20260815-005"
        if capture
        else "pev3-s4-d0-replay-20260815-005"
    )
    payload = {
        "schema_version": "membind.paper-eval-v3.s4-phase-result.v1",
        "stage": "S4",
        "phase": "U0_CAPTURE" if capture else "D0_READ_ONLY_REPLAY",
        "run_id": run_id,
        "history_id": "07741c45",
        "namespace": namespace,
        "method": "U0" if capture else "D0",
        "mode": mode,
        "cache_id": "s4-d0-remap-07741c45-20260815-005",
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
            "namespace": namespace,
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
        run_id=run_id,
    )


def _refinalize(artifact: dict) -> dict:
    return finalize_envelope(
        payload=artifact["payload"],
        protocol_version=PROTOCOL_VERSION,
        git_commit=artifact["git_commit"],
        run_id=artifact["run_id"],
    )


def _fixture(tmp_path: Path) -> dict:
    authority = _load(AUTHORITY_PATH)
    authority_sha = sha256_file(AUTHORITY_PATH)
    consumption_path = tmp_path / "consumption.json"
    consumption = consume_s4_remap_authority(
        authority=authority,
        authority_file_sha256=authority_sha,
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s4-remap-authority-consumption-20260815-005",
    )
    capture = _phase("capture")
    replay = _phase("replay")
    evaluation = evaluate_s4_smoke(
        capture_result=capture,
        replay_result=replay,
    )
    payload = build_remap_result_payload(
        evaluation=evaluation,
        authority_file_sha256=authority_sha,
        authority_consumption_file_sha256=sha256_file(consumption_path),
        capture_result_file_sha256="3" * 64,
        replay_result_file_sha256="4" * 64,
    )
    result = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit="deadbeef",
        run_id="s4-d0-remap-smoke-result-20260815-005",
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
    return verify_s4_remap_smoke_result(
        result=fixture["result"],
        authority=fixture["authority"],
        authority_file_sha256=fixture["authority_sha"],
        consumption=fixture["consumption"],
        consumption_file_sha256=fixture["consumption_sha"],
        capture_result=fixture["capture"],
        capture_result_file_sha256="3" * 64,
        replay_result=fixture["replay"],
        replay_result_file_sha256="4" * 64,
    )


def test_result_recomputes_remap_graph_work_cache_and_authority_gates(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    assert _verify(fixture) == fixture["result"]


@pytest.mark.parametrize(
    ("target", "mutate"),
    [
        (
            "replay",
            lambda value: value["payload"]["runtime_evidence"].update(
                candidate_remap_rejection_count=1
            ),
        ),
        (
            "replay",
            lambda value: value["payload"]["runtime_evidence"].update(
                exact_prompt_hit_count=7
            ),
        ),
        (
            "replay",
            lambda value: value["payload"]["runtime_evidence"].update(
                candidate_remap_edge_hit_count=0
            ),
        ),
        (
            "replay",
            lambda value: value["payload"].update(canonical_graph_sha256="f" * 64),
        ),
        (
            "replay",
            lambda value: value["payload"]["cache_evidence"].update(
                prompt_cache_sha256="f" * 64
            ),
        ),
        (
            "capture",
            lambda value: value["payload"]["runtime_evidence"].update(
                candidate_remap_hit_count=0
            ),
        ),
        (
            "replay",
            lambda value: value.update(run_id="wrong-replay-envelope"),
        ),
    ],
)
def test_result_rejects_phase_semantic_drift(
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
            candidate_oracle_resolution_accounting=False
        ),
        lambda value: value["payload"].update(raw_response="private"),
        lambda value: value.update(run_id="wrong-result-envelope"),
        lambda value: value.update(extra="drift"),
    ],
)
def test_result_rejects_result_or_private_data_tamper(
    tmp_path: Path,
    mutate,
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture["result"])
    if set(fixture["result"]) == {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        fixture["result"] = _refinalize(fixture["result"])

    with pytest.raises(ValueError):
        _verify(fixture)


def test_result_rejects_consumption_or_external_hash_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["consumption_sha"] = "0" * 64

    with pytest.raises(ValueError):
        _verify(fixture)


def test_result_rejects_consumption_candidate_policy_hash_drift(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture["consumption"]["payload"]["candidate_oracle_sha256"] = "0" * 64
    fixture["consumption"] = _refinalize(fixture["consumption"])

    with pytest.raises(ValueError, match="authority, consumption"):
        _verify(fixture)
