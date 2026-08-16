"""Offline TDD contracts for the method-specific S5 live preflight."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256
from paper_eval.s5_live_preflight import (
    S5LivePreflightError,
    collect_s5_live_preflight,
    evaluate_s5_live_preflight,
    finalize_s5_live_preflight,
    verify_s5_live_preflight,
)
from paper_eval.s5_production_runner import build_s5_production_identity
from paper_eval.s5_production_identity_qualification import (
    AP_SOURCE_ROLES,
    MSTAR_SOURCE_ROLES,
    verify_s5_production_identity_qualification,
)


POINTER_FILE_SHA256 = "1" * 64
FX0_PAYLOAD_SHA256 = "2" * 64
SOURCE_SHA256S = tuple(f"{index + 1:064x}" for index in range(49))
QUALIFICATION_FILE_SHA256 = "f" * 64


def _identity(method: str) -> dict[str, object]:
    return build_s5_production_identity(
        method=method,
        graphiti_version="0.29.3",
        graphiti_commit="021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        graphiti_native_source_sha256="3" * 64,
        graphiti_semantic_api_sha256="4" * 64,
        runtime_factory_entrypoint=(
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        runtime_factory_source_sha256="5" * 64,
        scheduler_source_sha256="6" * 64,
        scheduler_test_source_sha256="7" * 64,
        durable_store_source_sha256="8" * 64,
        durable_store_test_source_sha256="9" * 64,
        runtime_config_sha256="a" * 64,
        fx0_parity_artifact_sha256=(
            FX0_PAYLOAD_SHA256 if method == "M*" else None
        ),
    )


def _qualification(method: str) -> dict[str, object]:
    identity = _identity(method)
    roles = MSTAR_SOURCE_ROLES if method == "M*" else AP_SOURCE_ROLES
    closure = {
        role: (f"{index + 1:064x}")[-64:]
        for index, role in enumerate(sorted(roles))
    }
    mstar_fx0 = None
    if method == "M*":
        mstar_fx0 = {
            "qualification_file_sha256": "b" * 64,
            "qualification_payload_sha256": _fx0()["payload_sha256"],
            "production_core_identity_sha256": "c" * 64,
            "production_core_identity_file_sha256": "d" * 64,
            "fx0_artifact_payload_sha256": FX0_PAYLOAD_SHA256,
            "fx0_fixture_manifest_sha256": "e" * 64,
            "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        }
    return verify_s5_production_identity_qualification(
        finalize_envelope(
            payload={
                "schema_version": (
                    "membind.paper-eval-v3."
                    "s5-production-identity-qualification.v1"
                ),
                "stage": "S5_PRODUCTION_IDENTITY_QUALIFICATION",
                "method": method,
                "qualification_status": "PRODUCTION_IDENTITY_OFFLINE_QUALIFIED",
                "raw_identity_qualification_status": "IDENTITY_ONLY_UNQUALIFIED",
                "production_identity_sha256": identity["identity_sha256"],
                "production_identity_file_sha256": "a" * 64,
                "native_baseline_freeze": {
                    "file_sha256": "2" * 64,
                    "payload_sha256": "3" * 64,
                    "baseline_id": "native-graphiti-u0-reader-v2",
                },
                "current_stage_pointer": {
                    "file_sha256": POINTER_FILE_SHA256,
                    "payload_sha256": _pointer()["payload_sha256"],
                    "run_id": "s3-pointer-test",
                    "current_stage": "S3_CONFIGURATION_FROZEN",
                },
                "s5_plan": {
                    "file_sha256": "4" * 64,
                    "payload_sha256": "5" * 64,
                    "run_id": "s5-method-qualification-plan-20260815-001",
                    "status": "OFFLINE_DESIGN_ONLY",
                },
                "s5_workplan_file_sha256": "6" * 64,
                "source_closure_sha256": closure,
                "source_closure_digest": payload_sha256(closure),
                "full_regression": {
                    "junit_file_sha256": "7" * 64,
                    "tests": 1200,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                },
                "mstar_fx0": mstar_fx0,
                "authority": {
                    "s5_read_only_preflight_authorized": True,
                    "preflight_scope": "SINGLE_BOUNDED_READ_ONLY_PREFLIGHT",
                    "construction_models_get_authorized": True,
                    "construction_version_get_authorized": True,
                    "embedding_models_get_authorized": True,
                    "neo4j_connectivity_check_authorized": True,
                    "neo4j_exact_namespace_count_authorized": True,
                    "model_generation_authorized": False,
                    "embedding_generation_authorized": False,
                    "neo4j_mutation_authorized": False,
                    "s5_live_execution_authorized": False,
                    "pilot_execution_authorized": False,
                    "formal_execution_authorized": False,
                    "current_stage_pointer_update_authorized": False,
                },
            },
            protocol_version="paper-eval-v3",
            git_commit="deadbeef",
            run_id=f"s5-{method.lower()}-identity-qualification-test",
        )
    )


def test_pass_preflight_requires_sealed_identity_qualification() -> None:
    namespace = "pev3-s5-a0-qualified-preflight-test"
    result = evaluate_s5_live_preflight(
        method="A0",
        run_id="s5-a0-qualified-preflight-test",
        namespace=namespace,
        episode_source_sha256s=SOURCE_SHA256S,
        observations=_observations(namespace),
        production_identity_qualification=_qualification("A0"),
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=_pointer(),
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
    )
    assert result["production_identity_qualification"][
        "qualification_file_sha256"
    ] == QUALIFICATION_FILE_SHA256


def test_raw_identity_cannot_reach_a_pass_preflight() -> None:
    namespace = "pev3-s5-a0-raw-identity-rejected"
    with pytest.raises(
        S5LivePreflightError, match="production_identity_qualification_invalid"
    ):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-raw-identity-rejected",
            namespace=namespace,
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations(namespace),
            production_identity_qualification=_identity("A0"),
            production_identity_qualification_file_sha256=(
                QUALIFICATION_FILE_SHA256
            ),
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        )


def test_qualification_native_freeze_must_match_the_current_pointer() -> None:
    namespace = "pev3-s5-a0-freeze-mismatch"
    qualification = _qualification("A0")
    qualification["payload"]["native_baseline_freeze"]["file_sha256"] = (
        "9" * 64
    )
    qualification["payload_sha256"] = payload_sha256(qualification["payload"])
    with pytest.raises(
        S5LivePreflightError,
        match="production_identity_qualification_binding_mismatch",
    ):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-freeze-mismatch",
            namespace=namespace,
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations(namespace),
            production_identity_qualification=qualification,
            production_identity_qualification_file_sha256=(
                QUALIFICATION_FILE_SHA256
            ),
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        )


def _pointer() -> dict[str, object]:
    payload = {
        "schema_version": "membind.paper-eval-v3.current-stage-pointer.v2",
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "live_preflight_required": True,
        "native_baseline_v2_freeze_file_sha256": "2" * 64,
        "native_baseline_v2_freeze_payload_sha256": "3" * 64,
    }
    return finalize_envelope(
        payload=payload,
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id="s3-pointer-test",
    )


def _predecessor(method: str) -> dict[str, object]:
    return {
        "method": method,
        "verdict": (
            "SCIENTIFIC_OUTCOME_COMPLETE" if method == "P*" else "PASS"
        ),
        "artifact_sha256": ("b" if method == "A0" else "c") * 64,
    }


def _fx0() -> dict[str, object]:
    payload = {
        "schema_version": (
            "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
        ),
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "fixture_count": 11,
        "current_stage_pointer_sha256": POINTER_FILE_SHA256,
        "production_core_identity_sha256": "d" * 64,
        "fx0_artifact_payload_sha256": FX0_PAYLOAD_SHA256,
        "runtime_config_sha256": "e" * 64,
        "authority": {
            "model_call_authorized": False,
            "neo4j_read_authorized": False,
            "neo4j_mutation_authorized": False,
            "s5_live_execution_authorized": False,
            "current_stage_pointer_update_authorized": False,
        },
    }
    return finalize_envelope(
        payload=payload,
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id="s5-fx0-qualification-test",
    )


def _empty() -> dict[str, int]:
    return {"node_count": 0, "relationship_count": 0}


def _observations(namespace: str) -> dict[str, object]:
    return {
        "construction": {
            "served_model_id": "qwen3-32b-fp8",
            "vllm_version": "0.26.0",
            "max_model_len": 65536,
        },
        "embedding": {"served_model_id": "qwen3-embedding-0.6b"},
        "neo4j_connectivity": True,
        "namespace": namespace,
        "namespace_state": _empty(),
    }


def _evaluate(method: str = "A0") -> dict[str, object]:
    token = {"A0": "a0", "P*": "p-star", "M*": "mstar"}[method]
    run_id = f"s5-{token}-smoke-test-001"
    predecessor = None
    fx0 = None
    if method == "P*":
        predecessor = _predecessor("A0")
    elif method == "M*":
        predecessor = _predecessor("P*")
        fx0 = _fx0()
    return evaluate_s5_live_preflight(
        method=method,
        run_id=run_id,
        namespace=f"pev3-{run_id}",
        episode_source_sha256s=SOURCE_SHA256S,
        observations=_observations(f"pev3-{run_id}"),
        production_identity_qualification=_qualification(method),
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=_pointer(),
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        predecessor=predecessor,
        fx0_qualification=fx0,
    )


@pytest.mark.parametrize("method", ("A0", "P*", "M*"))
def test_method_specific_preflight_passes_only_exact_frozen_bindings(method: str) -> None:
    result = _evaluate(method)

    assert result["verdict"] == "PASS"
    assert result["failures"] == []
    assert result["method"] == method
    assert result["workload"] == {
        "history_id": "07741c45",
        "episode_count": 49,
        "source_manifest_sha256": payload_sha256(
            [
                {"source_sequence": index, "source_sha256": digest}
                for index, digest in enumerate(SOURCE_SHA256S)
            ]
        ),
    }
    assert result["production_identity_qualification"][
        "production_identity_sha256"
    ] == _identity(method)["identity_sha256"]
    assert result["current_stage_pointer"] == {
        "run_id": "s3-pointer-test",
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "file_sha256": POINTER_FILE_SHA256,
        "payload_sha256": _pointer()["payload_sha256"],
        "native_baseline_freeze_file_sha256": "2" * 64,
        "native_baseline_freeze_payload_sha256": "3" * 64,
    }
    assert result["namespace_check"] == {
        "empty": True,
        "state_sha256": payload_sha256(_empty()),
    }
    assert result["authority"] == {
        "s5_live_authority_creation_authorized": True,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


def test_preflight_binds_the_exact_49_source_workload_manifest() -> None:
    namespace = "pev3-s5-a0-source-manifest-test"
    result = evaluate_s5_live_preflight(
        method="A0",
        run_id="s5-a0-source-manifest-test",
        namespace=namespace,
        episode_source_sha256s=SOURCE_SHA256S,
        observations=_observations(namespace),
        production_identity_qualification=_qualification("A0"),
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=_pointer(),
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
    )

    assert result["workload"] == {
        "history_id": "07741c45",
        "episode_count": 49,
        "source_manifest_sha256": payload_sha256(
            [
                {"source_sequence": index, "source_sha256": digest}
                for index, digest in enumerate(SOURCE_SHA256S)
            ]
        ),
    }

    with pytest.raises(S5LivePreflightError, match="source_manifest"):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-source-manifest-short",
            namespace="pev3-s5-a0-source-manifest-short",
            episode_source_sha256s=SOURCE_SHA256S[:-1],
            observations=_observations("pev3-s5-a0-source-manifest-short"),
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        )


def test_predecessor_policy_is_strict_and_mstar_requires_fx0() -> None:
    with pytest.raises(S5LivePreflightError, match="predecessor_forbidden"):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-test",
            namespace="pev3-s5-a0-test",
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations("pev3-s5-a0-test"),
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
            predecessor=_predecessor("A0"),
        )

    with pytest.raises(S5LivePreflightError, match="predecessor_invalid"):
        evaluate_s5_live_preflight(
            method="P*",
            run_id="s5-p-star-test",
            namespace="pev3-s5-p-star-test",
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations("pev3-s5-p-star-test"),
            production_identity_qualification=_qualification("P*"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
            predecessor=_predecessor("P*"),
        )

    with pytest.raises(S5LivePreflightError, match="fx0_qualification_required"):
        evaluate_s5_live_preflight(
            method="M*",
            run_id="s5-mstar-test",
            namespace="pev3-s5-mstar-test",
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations("pev3-s5-mstar-test"),
            production_identity_qualification=_qualification("M*"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
            predecessor=_predecessor("P*"),
        )


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    (
        ("construction.served_model_id", "wrong", "construction_model"),
        ("construction.vllm_version", "0.25.0", "vllm_version"),
        ("construction.max_model_len", 65535, "max_model_len"),
        ("embedding.served_model_id", "wrong", "embedding_model"),
        ("neo4j_connectivity", False, "neo4j_connectivity"),
        ("namespace_state.node_count", 1, "namespace_not_empty"),
    ),
)
def test_service_or_namespace_drift_fails_without_authority(
    field: str, value: object, failure: str
) -> None:
    namespace = "pev3-s5-a0-drift-test"
    observations = _observations(namespace)
    target = observations
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]  # type: ignore[index,assignment]
    target[parts[-1]] = value  # type: ignore[index]

    result = evaluate_s5_live_preflight(
        method="A0",
        run_id="s5-a0-drift-test",
        namespace=namespace,
        episode_source_sha256s=SOURCE_SHA256S,
        observations=observations,
        production_identity_qualification=_qualification("A0"),
        production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
        current_stage_pointer=_pointer(),
        current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
    )

    assert failure in result["failures"]
    assert result["verdict"] == "FAIL"
    assert result["authority"]["s5_live_authority_creation_authorized"] is False
    assert result["authority"]["s5_live_execution_authorized"] is False


def test_method_namespace_identity_and_pointer_are_fail_closed() -> None:
    with pytest.raises(S5LivePreflightError, match="namespace_identity_invalid"):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-test",
            namespace="pev3-s5-p-star-test",
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations("pev3-s5-p-star-test"),
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        )

    pointer = _pointer()
    pointer["payload"]["current_stage"] = "S5"  # type: ignore[index]
    with pytest.raises(S5LivePreflightError, match="current_pointer_invalid"):
        evaluate_s5_live_preflight(
            method="A0",
            run_id="s5-a0-test",
            namespace="pev3-s5-a0-test",
            episode_source_sha256s=SOURCE_SHA256S,
            observations=_observations("pev3-s5-a0-test"),
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=pointer,
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
        )


def test_collect_performs_only_three_service_reads_and_one_namespace_read() -> None:
    calls: list[tuple[str, str]] = []
    namespace_reads: list[str] = []

    async def get_json(base_url: str, path: str) -> dict[str, object]:
        calls.append((base_url, path))
        if path == "/version":
            return {"version": "0.26.0"}
        if "8001" in base_url:
            return {"data": [{"id": "qwen3-embedding-0.6b"}]}
        return {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 65536}]}

    async def namespace_state(namespace: str) -> dict[str, int]:
        namespace_reads.append(namespace)
        return _empty()

    result = asyncio.run(
        collect_s5_live_preflight(
            method="A0",
            run_id="s5-a0-collect-test",
            namespace="pev3-s5-a0-collect-test",
            episode_source_sha256s=SOURCE_SHA256S,
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
            get_json=get_json,
            neo4j_connectivity=lambda: True,
            namespace_state=namespace_state,
        )
    )

    assert result["verdict"] == "PASS"
    assert calls == [
        ("http://10.87.5.247:8000/v1/", "/models"),
        ("http://10.87.5.247:8000", "/version"),
        ("http://10.87.5.247:8001/v1", "/models"),
    ]
    assert namespace_reads == ["pev3-s5-a0-collect-test"]


def test_finalize_is_sanitized_exclusive_and_tamper_evident(tmp_path: Path) -> None:
    output = tmp_path / "S5_A0_LIVE_PREFLIGHT.json"
    evaluation = _evaluate("A0")
    artifact = finalize_s5_live_preflight(
        output_path=output,
        evaluation=evaluation,
        source_sha256={
            "contract": "1" * 64,
            "production": "2" * 64,
            "contract_test": "3" * 64,
            "production_test": "4" * 64,
        },
        git_commit="deadbeef",
    )

    assert verify_s5_live_preflight(artifact) == artifact
    assert artifact["run_id"] == evaluation["run_id"]
    assert artifact["payload"]["method"] == "A0"
    assert artifact["payload"]["namespace"] == evaluation["namespace"]
    assert "episode_names" not in str(artifact)
    assert "api_key" not in str(artifact).casefold()

    with pytest.raises(FileExistsError):
        finalize_s5_live_preflight(
            output_path=output,
            evaluation=evaluation,
            source_sha256=artifact["payload"]["source_sha256"],
            git_commit="deadbeef",
        )

    tampered = copy.deepcopy(artifact)
    tampered["payload"]["authority"]["s5_live_execution_authorized"] = True
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(S5LivePreflightError):
        verify_s5_live_preflight(tampered)
