"""Gate-C tests for freezing the Native U0 baseline.

These tests use synthetic, hash-sealed evidence only.  They must never read a
production stage artifact or authorize a real S3 transition.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256
from paper_eval.s3_freeze import (
    AUTHORITY_SCHEMA,
    FREEZE_SCHEMA,
    REQUIRED_RUNTIME_IDENTITIES,
    REQUIRED_SOURCE_BINDINGS,
    S2_COMPLETION_SCHEMA,
    build_native_baseline_freeze,
    finalize_native_baseline_freeze,
    verify_native_baseline_freeze,
)


COMMIT = "deadbeef"
RUN_ID = "s3-freeze-test-001"


def _envelope(payload: dict, *, run_id: str) -> dict:
    return finalize_envelope(
        payload=payload,
        protocol_version="synthetic-test-only",
        git_commit=COMMIT,
        run_id=run_id,
    )


def _runtime() -> dict[str, dict[str, object]]:
    return {
        "graphiti": {"version": "0.29.3", "commit": "g" * 40},
        "construction": {"model": "qwen3-32b-fp8", "max_model_len": 65536},
        "embedding": {"model": "qwen3-embedding-0.6b", "normalized": True},
        "neo4j": {"version": "5.26", "database": "neo4j"},
        "vllm": {"version": "0.26.0", "structured_mode": "json_schema"},
    }


def _sources() -> dict[str, str]:
    return {
        name: f"{index:x}" * 64
        for index, name in enumerate(REQUIRED_SOURCE_BINDINGS, start=1)
    }


def _adapter() -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.s2-adapter-identity.v2",
        "config_sha256": "a" * 64,
        "source_sha256": "b" * 64,
    }


def _reader() -> dict[str, object]:
    return {
        "adapter": "longmemeval_reader",
        "model": "reader-model",
        "prompt_sha256": "c" * 64,
        "config_sha256": "d" * 64,
        "source_sha256": "e" * 64,
    }


def _judge() -> dict[str, object]:
    return {
        "adapter": "longmemeval_judge",
        "model": "judge-model",
        "prompt_sha256": "f" * 64,
        "config_sha256": "1" * 64,
        "source_sha256": "2" * 64,
    }


def _retrieval() -> dict[str, object]:
    return {
        "policy": "predeclared_graphiti_u0",
        "config_sha256": "3" * 64,
        "source_sha256": "4" * 64,
    }


def _execution() -> dict[str, str]:
    return {
        "prompt_schema_sha256": "5" * 64,
        "retry_policy_sha256": "6" * 64,
        "pooling_config_sha256": "7" * 64,
        "cache_policy_sha256": "8" * 64,
        "instrumentation_config_sha256": "9" * 64,
    }


def _roles() -> dict[str, list[str]]:
    return {
        "DEVELOPMENT_EXPOSED": ["dev-a", "dev-b"],
        "PILOT": ["pilot-a"],
        "FINAL_PAPER_TEST": ["final-a", "final-b"],
    }


def _s1() -> dict:
    return _envelope(
        {
            "stage": "S1",
            "method": "U0",
            "verdict": "PASS",
            "coverage": {"expected": 49, "published": 49, "lost": [], "duplicates": []},
            "failure_count": 0,
        },
        run_id="s1-test",
    )


def _binding_projection(fixture: dict[str, object]) -> dict[str, object]:
    return {
        "runtime_identity": fixture["runtime"],
        "source_sha256": fixture["sources"],
        "adapter_identity": fixture["adapter"],
        "retrieval_identity": fixture["retrieval"],
        "reader_identity": fixture["reader"],
        "judge_identity": fixture["judge"],
        "execution_identity": fixture["execution"],
        "role_registry": fixture["roles"],
    }


def _fixture(tmp_path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "runtime": _runtime(),
        "sources": _sources(),
        "adapter": _adapter(),
        "reader": _reader(),
        "judge": _judge(),
        "retrieval": _retrieval(),
        "execution": _execution(),
        "roles": _roles(),
        "s1": _s1(),
        "output": tmp_path / "NATIVE_BASELINE_FREEZE.synthetic.json",
    }
    bindings = _binding_projection(result)
    result["s2"] = _envelope(
        {
            "schema_version": S2_COMPLETION_SCHEMA,
            "stage": "S2",
            "method": "U0",
            "verdict": "PASS",
            "completion_scope": "FULL_S2_COMPLETION",
            "diagnostic_only": False,
            "retrieval_policy_selected": True,
            "reader_judge_executed": True,
            "reference_alignment_status": "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
            "reference_sanity_status": "PASS",
            "numeric_sanity_sha256": "0" * 64,
            "s1_payload_sha256": result["s1"]["payload_sha256"],
            "bindings_sha256": payload_sha256(bindings),
            "role_registry_sha256": payload_sha256(result["roles"]),
            "s3_ready": True,
        },
        run_id="s2-completion-test",
    )
    result["authority"] = _envelope(
        {
            "schema_version": AUTHORITY_SCHEMA,
            "stage": "S3",
            "method": "U0",
            "authorization": "FINALIZE_NATIVE_U0_FREEZE_ONCE",
            "run_id": RUN_ID,
            "expected_output_path": str(result["output"].resolve()),
            "s1_payload_sha256": result["s1"]["payload_sha256"],
            "s2_completion_payload_sha256": result["s2"]["payload_sha256"],
            "bindings_sha256": payload_sha256(bindings),
            "role_registry_sha256": payload_sha256(result["roles"]),
            "outcome_observed": False,
            "method_results_observed": False,
        },
        run_id=RUN_ID,
    )
    return result


def _build(fixture: dict[str, object]) -> dict:
    return build_native_baseline_freeze(
        s1_artifact=fixture["s1"],
        s2_completion_artifact=fixture["s2"],
        authority_artifact=fixture["authority"],
        runtime_identity=fixture["runtime"],
        source_sha256=fixture["sources"],
        adapter_identity=fixture["adapter"],
        retrieval_identity=fixture["retrieval"],
        reader_identity=fixture["reader"],
        judge_identity=fixture["judge"],
        execution_identity=fixture["execution"],
        role_registry=fixture["roles"],
        expected_output_path=fixture["output"],
        git_commit=COMMIT,
        run_id=RUN_ID,
    )


def test_gate_c_build_is_pure_hash_sealed_and_complete(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = _build(fixture)
    payload = artifact["payload"]
    assert not fixture["output"].exists()
    assert payload["schema_version"] == FREEZE_SCHEMA
    assert payload["stage"] == "S3"
    assert payload["method"] == "U0"
    assert payload["verdict"] == "PASS"
    assert payload["freeze_status"] == "FROZEN"
    assert payload["immutable"] is True
    assert payload["s4_authorized"] is False
    assert payload["s1_payload_sha256"] == fixture["s1"]["payload_sha256"]
    assert payload["s2_completion_payload_sha256"] == fixture["s2"]["payload_sha256"]
    assert payload["authority_payload_sha256"] == fixture["authority"]["payload_sha256"]
    assert payload["bindings_sha256"] == payload_sha256(_binding_projection(fixture))
    assert artifact["payload_sha256"] == payload_sha256(payload)
    assert verify_native_baseline_freeze(artifact)["verdict"] == "PASS"


@pytest.mark.parametrize("stage,method,verdict", [("S0", "U0", "PASS"), ("S1", "D0", "PASS"), ("S1", "U0", "FAIL")])
def test_gate_c_requires_s1_u0_pass(tmp_path: Path, stage: str, method: str, verdict: str) -> None:
    fixture = _fixture(tmp_path)
    fixture["s1"]["payload"].update(stage=stage, method=method, verdict=verdict)
    fixture["s1"]["payload_sha256"] = payload_sha256(fixture["s1"]["payload"])
    with pytest.raises(ValueError, match="S1 U0 PASS"):
        _build(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("verdict", "FAIL"),
        ("completion_scope", "DIAGNOSTIC_ONLY"),
        ("diagnostic_only", True),
        ("retrieval_policy_selected", False),
        ("reader_judge_executed", False),
        ("reference_alignment_status", None),
        ("reference_sanity_status", "NOT_RUN"),
        ("s3_ready", False),
    ],
)
def test_gate_c_rejects_incomplete_or_diagnostic_s2(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture = _fixture(tmp_path)
    fixture["s2"]["payload"][field] = value
    fixture["s2"]["payload_sha256"] = payload_sha256(fixture["s2"]["payload"])
    with pytest.raises(ValueError, match="full S2 completion PASS"):
        _build(fixture)


def test_gate_c_rejects_diagnostic_only_s2_r0_artifact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["s2"] = _envelope(
        {
            "schema_version": "membind.paper-eval-v3.s2-r0-episode-probe.v1",
            "stage": "S2-R0",
            "verdict": "PASS",
            "result_classification": "EPISODE_SURFACE_RECALL_ALL",
            "s3_authorized": False,
        },
        run_id="s2r0-test",
    )
    with pytest.raises(ValueError, match="diagnostic-only S2-R0"):
        _build(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorization", "RUN_S2_R0_EPISODE_BM25_ONCE"),
        ("run_id", "other"),
        ("outcome_observed", True),
        ("method_results_observed", True),
    ],
)
def test_gate_c_requires_exact_outcome_independent_one_shot_authority(
    tmp_path: Path, field: str, value: object
) -> None:
    fixture = _fixture(tmp_path)
    fixture["authority"]["payload"][field] = value
    fixture["authority"]["payload_sha256"] = payload_sha256(fixture["authority"]["payload"])
    with pytest.raises(ValueError, match="one-shot S3 authority"):
        _build(fixture)


def test_gate_c_rejects_authority_for_a_different_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["authority"]["payload"]["expected_output_path"] = str((tmp_path / "other.json").resolve())
    fixture["authority"]["payload_sha256"] = payload_sha256(fixture["authority"]["payload"])
    with pytest.raises(ValueError, match="one-shot S3 authority"):
        _build(fixture)


@pytest.mark.parametrize("group", REQUIRED_RUNTIME_IDENTITIES)
def test_gate_c_rejects_missing_runtime_identity(tmp_path: Path, group: str) -> None:
    fixture = _fixture(tmp_path)
    del fixture["runtime"][group]
    with pytest.raises(ValueError, match="runtime identity"):
        _build(fixture)


@pytest.mark.parametrize("binding", REQUIRED_SOURCE_BINDINGS)
def test_gate_c_rejects_missing_or_invalid_source_binding(tmp_path: Path, binding: str) -> None:
    fixture = _fixture(tmp_path)
    fixture["sources"][binding] = "not-a-sha"
    with pytest.raises(ValueError, match="source bindings"):
        _build(fixture)


@pytest.mark.parametrize("identity", ["reader", "judge"])
def test_gate_c_rejects_missing_reader_or_judge_binding(tmp_path: Path, identity: str) -> None:
    fixture = _fixture(tmp_path)
    fixture[identity].pop("prompt_sha256")
    with pytest.raises(ValueError, match=f"{identity} identity"):
        _build(fixture)


def test_gate_c_rejects_legacy_v1_adapter_identity(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["adapter"]["schema_version"] = "membind.paper-eval-v3.s2-adapter-identity.v1"
    with pytest.raises(ValueError, match="adapter identity v2"):
        _build(fixture)


def test_gate_c_rejects_role_overlap(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["roles"]["PILOT"].append("dev-a")
    with pytest.raises(ValueError, match="role registry"):
        _build(fixture)


def test_gate_c_allows_future_pilot_and_final_roles_to_remain_unassigned(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture["roles"]["PILOT"] = []
    fixture["roles"]["FINAL_PAPER_TEST"] = []
    bindings = _binding_projection(fixture)
    fixture["s2"]["payload"]["bindings_sha256"] = payload_sha256(bindings)
    fixture["s2"]["payload"]["role_registry_sha256"] = payload_sha256(
        fixture["roles"]
    )
    fixture["s2"]["payload_sha256"] = payload_sha256(fixture["s2"]["payload"])
    fixture["authority"]["payload"]["s2_completion_payload_sha256"] = fixture[
        "s2"
    ]["payload_sha256"]
    fixture["authority"]["payload"]["bindings_sha256"] = payload_sha256(bindings)
    fixture["authority"]["payload"]["role_registry_sha256"] = payload_sha256(
        fixture["roles"]
    )
    fixture["authority"]["payload_sha256"] = payload_sha256(
        fixture["authority"]["payload"]
    )

    artifact = _build(fixture)

    assert artifact["payload"]["bindings"]["role_registry"] == {
        "DEVELOPMENT_EXPOSED": ["dev-a", "dev-b"],
        "PILOT": [],
        "FINAL_PAPER_TEST": [],
    }


def test_gate_c_rejects_role_drift_after_s2_completion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["roles"]["PILOT"] = ["pilot-changed"]
    with pytest.raises(ValueError, match="role drift"):
        _build(fixture)


def test_gate_c_rejects_binding_drift_after_s2_completion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["runtime"]["vllm"]["version"] = "other"
    with pytest.raises(ValueError, match="binding drift"):
        _build(fixture)


@pytest.mark.parametrize(
    "group,key,value",
    [
        ("reader", "api_key", "secret-value"),
        ("judge", "password", "secret-value"),
        ("runtime", "raw_output", "model output"),
        ("retrieval", "question", "raw benchmark question"),
        ("execution", "prompt", "raw prompt text"),
    ],
)
def test_gate_c_rejects_secrets_and_raw_content(
    tmp_path: Path, group: str, key: str, value: str
) -> None:
    fixture = _fixture(tmp_path)
    fixture[group][key] = value
    with pytest.raises(ValueError, match="secret or raw content"):
        _build(fixture)


@pytest.mark.parametrize("group,key", [("roles", "outcome"), ("runtime", "latency"), ("reader", "accuracy"), ("retrieval", "winner")])
def test_gate_c_rejects_outcome_or_method_result_contamination(
    tmp_path: Path, group: str, key: str
) -> None:
    fixture = _fixture(tmp_path)
    fixture[group][key] = "observed"
    with pytest.raises(ValueError, match="outcome or method-result contamination"):
        _build(fixture)


@pytest.mark.parametrize("artifact_name", ["s1", "s2", "authority"])
def test_gate_c_rejects_tampered_input_payload_hash(tmp_path: Path, artifact_name: str) -> None:
    fixture = _fixture(tmp_path)
    fixture[artifact_name]["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="payload hash"):
        _build(fixture)


def test_gate_c_verifier_rejects_tampered_freeze_payload(tmp_path: Path) -> None:
    artifact = _build(_fixture(tmp_path))
    artifact["payload"]["method"] = "M2"
    with pytest.raises(ValueError, match="payload hash"):
        verify_native_baseline_freeze(artifact)


def test_gate_c_finalizer_is_exclusive_and_never_overwrites(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = _build(fixture)
    output = fixture["output"]
    finalized = finalize_native_baseline_freeze(output, artifact)
    assert finalized == artifact
    assert json.loads(output.read_text(encoding="utf-8")) == artifact
    with pytest.raises(ValueError, match="already exists"):
        finalize_native_baseline_freeze(output, artifact)
    assert json.loads(output.read_text(encoding="utf-8")) == artifact


def test_gate_c_two_finalizers_cannot_claim_same_authority_target(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = _build(fixture)

    def claim() -> str:
        try:
            finalize_native_baseline_freeze(fixture["output"], artifact)
        except ValueError:
            return "rejected"
        return "finalized"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(outcomes) == ["finalized", "rejected"]


def test_gate_c_finalizer_rejects_wrong_authority_target(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    artifact = _build(fixture)
    with pytest.raises(ValueError, match="authority target"):
        finalize_native_baseline_freeze(tmp_path / "wrong.json", artifact)
