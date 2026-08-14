from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s2_qualification import finalize_u0_qualification


EXPECTED_HISTORY = "07741c45"
EXPECTED_NAMESPACE = "pev3-s1-20260814-001"
EXPECTED_RUN = "s1-20260814-001"
COMMIT = "deadbeef"


def _s0() -> dict:
    return {
        "git_commit": COMMIT,
        "payload": {
            "stage": "S0",
            "source_hashes": {"u0_runtime_source": "u" * 64},
            "runtime_identities": {
                "graphiti": {"version": "0.29.3", "repository_commit": "g" * 40},
                "construction": {
                    "served_model_id": "qwen3-32b-fp8",
                    "vllm_version": "0.26.0",
                    "max_model_len": 65536,
                    "repository_revision": "m" * 40,
                },
                "embedding": {
                    "served_model_id": "qwen3-embedding-0.6b",
                    "deployment_fingerprint": "e" * 64,
                },
            },
        },
    }


def _checkpoint() -> dict:
    body = {
        "schema_version": "membind.paper-eval-v3.s1-checkpoint.v1",
        "run_id": EXPECTED_RUN,
        "history_id": EXPECTED_HISTORY,
        "namespace": EXPECTED_NAMESPACE,
        "status": "completed",
        "completed_source_sequences": list(range(49)),
        "error_class": None,
        "retrieval_result_ids": ["r1"],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _event(event_type: str, sequence: int | None = None) -> dict:
    body = {
        "schema_version": "membind.paper-eval-v3.s1-event.v1",
        "run_id": EXPECTED_RUN,
        "history_id": EXPECTED_HISTORY,
        "namespace": EXPECTED_NAMESPACE,
        "event_type": event_type,
    }
    if sequence is not None:
        body["source_sequence"] = sequence
    body["payload_sha256"] = payload_sha256(body)
    return body


def _u0(checkpoint_hash: str, events_hash: str) -> dict:
    return {
        "git_commit": COMMIT,
        "run_id": EXPECTED_RUN,
        "payload": {
            "stage": "S1",
            "method": "U0",
            "history_id": EXPECTED_HISTORY,
            "namespace": EXPECTED_NAMESPACE,
            "coverage": {
                "expected": 49,
                "intents": 49,
                "published": 49,
                "lost": [],
                "duplicates": [],
            },
            "failure_count": 0,
            "retrieval_call_count": 1,
            "verdict": "PASS",
            "checkpoint_sha256": checkpoint_hash,
            "events_sha256": events_hash,
            "integrity": {
                "checkpoint_hash_valid": True,
                "checkpoint_identity_valid": True,
                "checkpoint_schema_valid": True,
                "checkpoint_shape_valid": True,
                "event_hash_failures": 0,
                "event_identity_failures": 0,
                "event_parse_failures": 0,
                "event_schema_failures": 0,
                "event_type_failures": 0,
                "event_field_failures": 0,
                "event_pattern_valid": True,
                "retrieval_parity_valid": True,
            },
        },
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    run_dir = tmp_path / EXPECTED_RUN
    run_dir.mkdir()
    checkpoint = _checkpoint()
    checkpoint_path = run_dir / "checkpoint.json"
    _write_json(checkpoint_path, checkpoint)
    events = []
    for sequence in range(49):
        events.extend((_event("intent", sequence), _event("publication", sequence)))
    events.append(_event("retrieval"))
    events_path = run_dir / "events.jsonl"
    events_path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in events),
        encoding="utf-8",
    )
    u0_path = tmp_path / "U0_SMOKE.json"
    _write_json(u0_path, _u0(sha256_file(checkpoint_path), sha256_file(events_path)))
    s0_path = tmp_path / "S0_CURRENT_STATE.json"
    _write_json(s0_path, _s0())
    preflight_path = tmp_path / "S1_PREFLIGHT.json"
    _write_json(preflight_path, {"status": "PASS", "scope": "single_read_only_preflight_no_qualification"})
    dataset_path = tmp_path / "DATASET_PARITY.json"
    _write_json(dataset_path, {"verdict": "PASS", "payload_sha256": "d" * 64})
    evaluator_path = tmp_path / "EVALUATOR_PARITY.json"
    _write_json(evaluator_path, {"verdict": "PASS", "payload_sha256": "a" * 64})
    return {
        "s0": s0_path,
        "preflight": preflight_path,
        "u0": u0_path,
        "run_dir": run_dir,
        "dataset": dataset_path,
        "evaluator": evaluator_path,
    }


def _call(paths: dict[str, Path], output: Path, **kwargs):
    runtime = json.loads(paths["s0"].read_text())["payload"]["runtime_identities"]
    return finalize_u0_qualification(
        output_path=output,
        s0_path=paths["s0"],
        preflight_path=paths["preflight"],
        u0_smoke_path=paths["u0"],
        run_dir=paths["run_dir"],
        dataset_parity_path=paths["dataset"],
        evaluator_parity_path=paths["evaluator"],
        git_commit=COMMIT,
        run_id="s2-qual-test",
        current_runtime_identity=runtime,
        **kwargs,
    )


def test_u0_qualification_seal_passes_only_with_all_bound_artifacts(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    artifact = _call(paths, tmp_path / "U0_QUALIFICATION.json")
    assert artifact["payload"]["verdict"] == "PASS"
    assert artifact["payload"]["history_id"] == EXPECTED_HISTORY
    assert artifact["payload"]["namespace"] == EXPECTED_NAMESPACE
    assert artifact["payload"]["checks"]["coverage_49_of_49"] is True
    assert artifact["payload_sha256"] == payload_sha256(artifact["payload"])


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("u0_verdict", "u0_smoke_not_pass"),
        ("checkpoint_hash", "checkpoint_hash_mismatch"),
        ("events_hash", "events_hash_mismatch"),
        ("preflight", "s1_preflight_not_pass"),
        ("dataset", "dataset_parity_not_pass"),
        ("evaluator", "evaluator_parity_not_pass"),
        ("identity", "runtime_identity_mismatch"),
    ],
)
def test_u0_qualification_fails_closed_on_each_gate(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    paths = _fixture(tmp_path)
    if mutation == "u0_verdict":
        value = json.loads(paths["u0"].read_text())
        value["payload"]["verdict"] = "FAIL"
        _write_json(paths["u0"], value)
    elif mutation in {"checkpoint_hash", "events_hash"}:
        value = json.loads(paths["u0"].read_text())
        value["payload"]["%s_sha256" % mutation] = "x" * 64
        _write_json(paths["u0"], value)
    elif mutation == "preflight":
        _write_json(paths["preflight"], {"status": "FAIL"})
    elif mutation == "dataset":
        _write_json(paths["dataset"], {"verdict": "FAIL", "payload_sha256": "d" * 64})
    elif mutation == "evaluator":
        _write_json(paths["evaluator"], {"verdict": "FAIL", "payload_sha256": "a" * 64})
    elif mutation == "identity":
        value = json.loads(paths["s0"].read_text())
        value["payload"]["runtime_identities"]["construction"]["served_model_id"] = "other"
        _write_json(paths["s0"], value)
    artifact = _call(paths, tmp_path / "U0_QUALIFICATION.json")
    assert artifact["payload"]["verdict"] == "FAIL"
    assert reason in artifact["payload"]["failure_reasons"]


def test_u0_qualification_rejects_raw_content_and_unbound_direct_contract(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    contract = tmp_path / "u0_contract.json"
    _write_json(contract, {
        "source": "membind-validation/src/graphiti_native.py",
        "source_sha256": "a" * 64,
        "contract_sha256": "c" * 64,
        "operation": "graphiti.add_episode",
        "namespace_field": "group_id",
        "description": "question secret",
    })
    artifact = _call(paths, tmp_path / "U0_QUALIFICATION.json", direct_u0_contract_path=contract)
    text = (tmp_path / "U0_QUALIFICATION.json").read_text()
    assert artifact["payload"]["verdict"] == "PASS"
    assert "question secret" not in text
    assert "source_hash" not in artifact["payload"]


def test_u0_qualification_requires_exact_retrieval_and_current_identity(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    value = json.loads(paths["u0"].read_text())
    value["payload"]["retrieval_call_count"] = 2
    _write_json(paths["u0"], value)
    artifact = _call(paths, tmp_path / "U0_QUALIFICATION.json")
    assert artifact["payload"]["verdict"] == "FAIL"
    assert "retrieval_count_not_one" in artifact["payload"]["failure_reasons"]
