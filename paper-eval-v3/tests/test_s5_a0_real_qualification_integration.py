"""Real-file integration gate from A0 materialization to qualification.

The unit fixtures for the generic qualifier intentionally use tiny text files.
This test closes the gap to production: semantic identity and runtime config
are sealed artifacts whose declared semantic digests, rather than their JSON
container byte hashes, are carried by the raw production identity.
"""

from __future__ import annotations

from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s5_a0_production_identity_materializer import (
    write_s5_a0_production_identity_materialization_exclusive,
)
from paper_eval.s5_production_identity_qualification import (
    AP_SOURCE_ROLES,
    build_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from tests.test_s5_a0_production_identity_materializer import _materialize, _paths


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"


def _green_junit(path: Path) -> Path:
    path.write_text(
        '<testsuites tests="1" failures="0" errors="0" skipped="0">'
        '<testsuite tests="1" failures="0" errors="0" skipped="0" />'
        "</testsuites>\n",
        encoding="ascii",
    )
    return path


def test_real_materialization_qualifies_with_semantic_artifact_digests(
    tmp_path: Path,
) -> None:
    materialization_paths = _paths()
    bundle = _materialize(materialization_paths)
    runtime_config = tmp_path / "S5_A0_RUNTIME_CONFIG.json"
    production_identity = tmp_path / "S5_A0_PRODUCTION_IDENTITY.json"
    materialization = tmp_path / "S5_A0_IDENTITY_MATERIALIZATION.json"
    write_s5_a0_production_identity_materialization_exclusive(
        bundle=bundle,
        runtime_config_path=runtime_config,
        production_identity_path=production_identity,
        materialization_path=materialization,
        paths=materialization_paths,
    )

    source_paths = {
        "graphiti_native": materialization_paths.graphiti_native,
        "graphiti_semantic_api": materialization_paths.graphiti_semantic_identity,
        "runtime_factory": materialization_paths.runtime_factory,
        "scheduler": materialization_paths.scheduler,
        "scheduler_test": materialization_paths.scheduler_test,
        "durable_store": materialization_paths.durable_store,
        "durable_store_test": materialization_paths.durable_store_test,
        "runtime_config": runtime_config,
        "native_binding": PROJECT / "src/paper_eval/s5_graphiti_native_binding.py",
        "native_binding_test": PROJECT / "tests/test_s5_graphiti_native_binding.py",
        "production_runner": PROJECT / "src/paper_eval/s5_production_runner.py",
        "production_runner_test": PROJECT / "tests/test_s5_production_runner.py",
        "method_smoke_contract": PROJECT / "src/paper_eval/s5_method_smoke_contract.py",
        "method_smoke_contract_test": PROJECT / "tests/test_s5_method_smoke_contract.py",
    }
    assert set(source_paths) == AP_SOURCE_ROLES

    qualification = build_s5_production_identity_qualification(
        method="A0",
        production_identity_path=production_identity,
        native_baseline_freeze_path=(NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"),
        current_stage_pointer_path=PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
        s5_plan_path=NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json",
        s5_workplan_path=(
            PROJECT / "S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md"
        ),
        source_paths=source_paths,
        full_regression_junit_path=_green_junit(tmp_path / "full.xml"),
        expected_full_test_count=1,
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-a0-production-identity-qualification-20260816-001",
    )

    verified = verify_s5_production_identity_qualification(qualification)
    closure = verified["payload"]["source_closure_sha256"]
    assert closure["graphiti_semantic_api"] == (
        bundle.production_identity["graphiti_semantic_api_sha256"]
    )
    assert closure["runtime_config"] == (
        bundle.production_identity["runtime_config_sha256"]
    )
    assert closure["graphiti_native"] == sha256_file(
        materialization_paths.graphiti_native
    )
