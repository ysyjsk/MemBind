"""Offline contracts for promoting an S5 identity to preflight-only status.

The raw production identity deliberately remains ``IDENTITY_ONLY_UNQUALIFIED``.
Only this separate, source-complete qualification envelope may cross into the
bounded read-only preflight.  These tests never contact a service or database.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, payload_sha256, sha256_file
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)
from paper_eval.s5_production_identity_qualification import (
    AP_SOURCE_ROLES,
    MSTAR_SOURCE_ROLES,
    S5ProductionIdentityQualificationError,
    build_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
    write_s5_production_identity_qualification_exclusive,
)
from paper_eval.s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_production_identity,
)


ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "artifacts" / "paper_eval" / "native"
FREEZE = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
CURRENT = ROOT / "runtime" / "CURRENT_STAGE_STATUS.json"
PLAN = NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json"
WORKPLAN = ROOT / "S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md"


def _write(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _junit(path: Path, *, tests: int = 1200, failures: int = 0) -> Path:
    return _write(
        path,
        (
            f'<testsuites tests="{tests}" failures="{failures}" errors="0" '
            f'skipped="0"><testsuite tests="{tests}" failures="{failures}" '
            'errors="0" skipped="0" /></testsuites>'
        ),
    )


def _source_files(tmp_path: Path, roles: frozenset[str]) -> dict[str, Path]:
    return {
        role: _write(tmp_path / "sources" / role, f"source:{role}\n")
        for role in sorted(roles)
    }


def _identity(
    tmp_path: Path,
    *,
    method: str = "A0",
    fx0_parity_artifact_sha256: str | None = None,
) -> tuple[dict[str, object], dict[str, Path]]:
    sources = _source_files(tmp_path, AP_SOURCE_ROLES)
    identity = build_s5_production_identity(
        method=method,
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=sha256_file(sources["graphiti_native"]),
        graphiti_semantic_api_sha256=sha256_file(
            sources["graphiti_semantic_api"]
        ),
        runtime_factory_entrypoint=(
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        runtime_factory_source_sha256=sha256_file(sources["runtime_factory"]),
        scheduler_source_sha256=sha256_file(sources["scheduler"]),
        scheduler_test_source_sha256=sha256_file(sources["scheduler_test"]),
        durable_store_source_sha256=sha256_file(sources["durable_store"]),
        durable_store_test_source_sha256=sha256_file(
            sources["durable_store_test"]
        ),
        runtime_config_sha256=sha256_file(sources["runtime_config"]),
        fx0_parity_artifact_sha256=fx0_parity_artifact_sha256,
    )
    return identity, sources


def _base_kwargs(tmp_path: Path, *, method: str = "A0") -> dict[str, object]:
    identity, sources = _identity(tmp_path, method=method)
    identity_path = _json(tmp_path / "identity.json", identity)
    return {
        "method": method,
        "production_identity_path": identity_path,
        "native_baseline_freeze_path": FREEZE,
        "current_stage_pointer_path": CURRENT,
        "s5_plan_path": PLAN,
        "s5_workplan_path": WORKPLAN,
        "source_paths": sources,
        "full_regression_junit_path": _junit(tmp_path / "full.xml"),
        "expected_full_test_count": 1200,
        "git_commit": "deadbeef",
        "run_id": f"s5-{method.lower().replace('*', 'star')}-qualification-test",
    }


def test_ap_qualification_binds_every_frozen_input_and_only_preflight(
    tmp_path: Path,
) -> None:
    artifact = build_s5_production_identity_qualification(**_base_kwargs(tmp_path))
    payload = artifact["payload"]

    assert payload["qualification_status"] == "PRODUCTION_IDENTITY_OFFLINE_QUALIFIED"
    assert payload["raw_identity_qualification_status"] == (
        "IDENTITY_ONLY_UNQUALIFIED"
    )
    assert payload["native_baseline_freeze"] == {
        "file_sha256": sha256_file(FREEZE),
        "payload_sha256": json.loads(FREEZE.read_text())["payload_sha256"],
        "baseline_id": "native-graphiti-u0-reader-v2",
    }
    assert payload["current_stage_pointer"]["file_sha256"] == sha256_file(CURRENT)
    assert payload["s5_plan"]["file_sha256"] == sha256_file(PLAN)
    assert payload["s5_workplan_file_sha256"] == sha256_file(WORKPLAN)
    assert set(payload["source_closure_sha256"]) == AP_SOURCE_ROLES
    assert payload["source_closure_digest"] == payload_sha256(
        payload["source_closure_sha256"]
    )
    assert payload["full_regression"] == {
        "junit_file_sha256": sha256_file(tmp_path / "full.xml"),
        "tests": 1200,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    assert payload["authority"] == {
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
    }
    assert payload["mstar_fx0"] is None
    assert verify_s5_production_identity_qualification(artifact) == artifact


def test_raw_identity_cannot_impersonate_a_qualification(tmp_path: Path) -> None:
    raw, _ = _identity(tmp_path)
    with pytest.raises(S5ProductionIdentityQualificationError):
        verify_s5_production_identity_qualification(raw)


@pytest.mark.parametrize("missing", sorted(AP_SOURCE_ROLES))
def test_ap_source_closure_is_exact_and_cannot_omit_a_role(
    tmp_path: Path, missing: str
) -> None:
    kwargs = _base_kwargs(tmp_path)
    kwargs["source_paths"] = dict(kwargs["source_paths"])
    kwargs["source_paths"].pop(missing)
    with pytest.raises(S5ProductionIdentityQualificationError, match="source_closure"):
        build_s5_production_identity_qualification(**kwargs)


def test_source_or_frozen_binding_drift_fails_closed(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    _write(kwargs["source_paths"]["scheduler"], "changed after identity\n")
    with pytest.raises(S5ProductionIdentityQualificationError, match="source_binding"):
        build_s5_production_identity_qualification(**kwargs)

    kwargs = _base_kwargs(tmp_path / "pointer")
    pointer = json.loads(CURRENT.read_text(encoding="utf-8"))
    pointer["payload"]["current_stage"] = "S4"
    pointer["payload_sha256"] = payload_sha256(pointer["payload"])
    kwargs["current_stage_pointer_path"] = _json(tmp_path / "pointer.json", pointer)
    with pytest.raises(S5ProductionIdentityQualificationError, match="pointer"):
        build_s5_production_identity_qualification(**kwargs)


def test_full_regression_must_be_exactly_green(tmp_path: Path) -> None:
    kwargs = _base_kwargs(tmp_path)
    kwargs["full_regression_junit_path"] = _junit(
        tmp_path / "failed.xml", failures=1
    )
    with pytest.raises(S5ProductionIdentityQualificationError, match="regression"):
        build_s5_production_identity_qualification(**kwargs)


def _mstar_kwargs(tmp_path: Path) -> dict[str, object]:
    sources = _source_files(tmp_path, MSTAR_SOURCE_ROLES)
    core = build_s5_mstar_production_core_identity(
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_semantic_api_sha256=sha256_file(
            sources["graphiti_semantic_api"]
        ),
        graphiti_semantic_identity_artifact_sha256="b" * 64,
        runtime_factory_entrypoint=(
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        runtime_factory_source_sha256=sha256_file(sources["runtime_factory"]),
        pipeline_source_sha256=sha256_file(sources["pipeline"]),
        pipeline_test_source_sha256=sha256_file(sources["pipeline_test"]),
        adapter_source_sha256=sha256_file(sources["adapter"]),
        adapter_test_source_sha256=sha256_file(sources["adapter_test"]),
        semantic_runtime_source_sha256=sha256_file(sources["semantic_runtime"]),
        semantic_runtime_test_source_sha256=sha256_file(
            sources["semantic_runtime_test"]
        ),
        semantic_binding_source_sha256=sha256_file(sources["semantic_binding"]),
        semantic_binding_test_source_sha256=sha256_file(
            sources["semantic_binding_test"]
        ),
        durable_store_source_sha256=sha256_file(sources["durable_store"]),
        durable_store_test_source_sha256=sha256_file(
            sources["durable_store_test"]
        ),
        runtime_config_sha256=sha256_file(sources["runtime_config"]),
    )
    fx0_payload = {
        "schema_version": "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1",
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "fixture_count": 11,
        "run_id": "fx0-test",
        "runtime_config_sha256": core["runtime_config_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "fx0_artifact_payload_sha256": "9" * 64,
        "fx0_fixture_manifest_sha256": "8" * 64,
        "current_stage_pointer_sha256": sha256_file(CURRENT),
        "full_regression_junit_sha256": "7" * 64,
        "full_regression_summary": {
            "tests": 1151,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        },
        "legacy_status_artifact_preserved": True,
        "authority": {
            "model_call_authorized": False,
            "neo4j_read_authorized": False,
            "neo4j_mutation_authorized": False,
            "s5_live_execution_authorized": False,
            "current_stage_pointer_update_authorized": False,
        },
    }
    fx0 = finalize_envelope(
        payload=fx0_payload,
        protocol_version="paper-eval-v3",
        git_commit="deadbeef",
        run_id="fx0-test-qualification",
    )
    identity = build_s5_production_identity(
        method="M*",
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=sha256_file(sources["graphiti_native"]),
        graphiti_semantic_api_sha256=core["graphiti_semantic_api_sha256"],
        runtime_factory_entrypoint=core["runtime_factory_entrypoint"],
        runtime_factory_source_sha256=core["runtime_factory_source_sha256"],
        scheduler_source_sha256=core["pipeline_source_sha256"],
        scheduler_test_source_sha256=core["pipeline_test_source_sha256"],
        durable_store_source_sha256=core["durable_store_source_sha256"],
        durable_store_test_source_sha256=core["durable_store_test_source_sha256"],
        runtime_config_sha256=core["runtime_config_sha256"],
        fx0_parity_artifact_sha256=fx0_payload["fx0_artifact_payload_sha256"],
    )
    return {
        "method": "M*",
        "production_identity_path": _json(tmp_path / "identity.json", identity),
        "native_baseline_freeze_path": FREEZE,
        "current_stage_pointer_path": CURRENT,
        "s5_plan_path": PLAN,
        "s5_workplan_path": WORKPLAN,
        "source_paths": sources,
        "full_regression_junit_path": _junit(tmp_path / "full.xml"),
        "expected_full_test_count": 1200,
        "git_commit": "deadbeef",
        "run_id": "s5-mstar-qualification-test",
        "mstar_core_identity_path": _json(tmp_path / "core.json", core),
        "mstar_fx0_qualification_path": _json(tmp_path / "fx0.json", fx0),
    }


def test_mstar_requires_and_binds_the_matching_fx0_gate(tmp_path: Path) -> None:
    kwargs = _mstar_kwargs(tmp_path)
    artifact = build_s5_production_identity_qualification(**kwargs)
    payload = artifact["payload"]
    assert set(payload["source_closure_sha256"]) == MSTAR_SOURCE_ROLES
    assert payload["mstar_fx0"]["verdict"] == (
        "PRODUCTION_PATH_EXACT_PARITY_PASS"
    )
    assert payload["mstar_fx0"]["production_core_identity_sha256"] == (
        json.loads(Path(kwargs["mstar_core_identity_path"]).read_text())["identity_sha256"]
    )

    missing = dict(kwargs)
    missing["mstar_fx0_qualification_path"] = None
    with pytest.raises(S5ProductionIdentityQualificationError, match="fx0"):
        build_s5_production_identity_qualification(**missing)

    tampered = json.loads(Path(kwargs["mstar_fx0_qualification_path"]).read_text())
    tampered["payload"]["fx0_artifact_payload_sha256"] = "6" * 64
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    mismatch = dict(kwargs)
    mismatch["mstar_fx0_qualification_path"] = _json(
        tmp_path / "fx0-mismatch.json", tampered
    )
    with pytest.raises(S5ProductionIdentityQualificationError, match="fx0"):
        build_s5_production_identity_qualification(**mismatch)


def test_qualification_is_hash_sealed_private_safe_and_exclusive(
    tmp_path: Path,
) -> None:
    artifact = build_s5_production_identity_qualification(**_base_kwargs(tmp_path))
    tampered = deepcopy(artifact)
    tampered["payload"]["authority"]["s5_live_execution_authorized"] = True
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(S5ProductionIdentityQualificationError, match="authority"):
        verify_s5_production_identity_qualification(tampered)

    private = deepcopy(artifact)
    private["payload"]["api_key"] = "must-not-enter-artifact"
    private["payload_sha256"] = payload_sha256(private["payload"])
    with pytest.raises(S5ProductionIdentityQualificationError):
        verify_s5_production_identity_qualification(private)

    output = tmp_path / "qualification.json"
    written = write_s5_production_identity_qualification_exclusive(output, artifact)
    assert written == artifact
    with pytest.raises(S5ProductionIdentityQualificationError, match="output_exists"):
        write_s5_production_identity_qualification_exclusive(output, artifact)
