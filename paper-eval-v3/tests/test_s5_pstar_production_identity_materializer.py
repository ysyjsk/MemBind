"""TDD for service-free P*(C=2) runtime and identity materialization."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s5_production_runner import verify_s5_production_identity
from paper_eval.s5_production_identity_qualification import (
    AP_SOURCE_ROLES,
    build_s5_production_identity_qualification,
    verify_s5_production_identity_qualification,
)
from paper_eval.s5_pstar_production_identity_materializer import (
    S5PStarMaterializationError,
    S5PStarMaterializationPaths,
    materialize_s5_pstar_production_identity,
    verify_s5_pstar_runtime_config,
    write_s5_pstar_materialization_exclusive,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"


def _paths(**overrides: Path) -> S5PStarMaterializationPaths:
    values = {
        "a0_runtime_config": NATIVE / "S5_A0_RUNTIME_CONFIG_20260816.json",
        "current_stage_pointer": PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
        "graphiti_native": ROOT / "membind-validation/src/graphiti_native.py",
        "runtime_factory": (
            ROOT / "membind-validation/src/native_characterization_runtime.py"
        ),
        "dataset_builder": ROOT / "membind-validation/src/dataset.py",
        "scheduler": PROJECT / "src/paper_eval/s5_native_method_adapters.py",
        "scheduler_test": PROJECT / "tests/test_s5_native_method_adapters.py",
        "durable_store": PROJECT / "src/paper_eval/s5_durable_attempt_store.py",
        "durable_store_test": PROJECT / "tests/test_s5_durable_attempt_store.py",
    }
    values.update(overrides)
    return S5PStarMaterializationPaths(**values)


def test_materialization_binds_two_worker_policy_and_current_sources() -> None:
    bundle = materialize_s5_pstar_production_identity(
        paths=_paths(),
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-p-star-production-identity-20260816-001",
    )

    config = verify_s5_pstar_runtime_config(bundle.runtime_config)
    identity = verify_s5_production_identity(bundle.production_identity)
    payload = config["payload"]
    assert payload["method"] == "P*"
    assert payload["method_policy"] == {
        "configured_concurrency": 2,
        "scheduler": "WHOLE_UPDATE_TWO_WORKERS",
        "serial_source_order": False,
        "whole_update_parallel": True,
    }
    assert payload["derived_from_a0"]["file_sha256"] == sha256_file(
        _paths().a0_runtime_config
    )
    assert identity["method"] == "P*"
    assert identity["runtime_config_sha256"] == config["payload_sha256"]
    assert identity["scheduler_source_sha256"] == sha256_file(
        _paths().scheduler
    )
    assert identity["durable_store_source_sha256"] == sha256_file(
        _paths().durable_store
    )


def test_runtime_config_tamper_and_source_drift_fail_closed(tmp_path: Path) -> None:
    bundle = materialize_s5_pstar_production_identity(
        paths=_paths(),
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-p-star-production-identity-20260816-001",
    )
    tampered = copy.deepcopy(bundle.runtime_config)
    tampered["payload"]["method_policy"]["configured_concurrency"] = 3
    with pytest.raises(S5PStarMaterializationError):
        verify_s5_pstar_runtime_config(tampered)

    resealed_drift = copy.deepcopy(bundle.runtime_config)
    resealed_drift["payload"]["construction"]["max_model_len"] = 131072
    resealed_drift["payload_sha256"] = payload_sha256(resealed_drift["payload"])
    with pytest.raises(S5PStarMaterializationError, match="execution_envelope"):
        verify_s5_pstar_runtime_config(resealed_drift)

    scheduler = tmp_path / "scheduler.py"
    scheduler.write_bytes(_paths().scheduler.read_bytes() + b"\n# drift\n")
    with pytest.raises(S5PStarMaterializationError, match="source_closure"):
        materialize_s5_pstar_production_identity(
            paths=_paths(scheduler=scheduler),
            git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
            run_id="s5-p-star-production-identity-20260816-001",
        )


def test_exclusive_writer_never_overwrites(tmp_path: Path) -> None:
    bundle = materialize_s5_pstar_production_identity(
        paths=_paths(),
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-p-star-production-identity-20260816-001",
    )
    runtime = tmp_path / "runtime.json"
    identity = tmp_path / "identity.json"
    write_s5_pstar_materialization_exclusive(
        bundle=bundle,
        runtime_config_path=runtime,
        production_identity_path=identity,
    )
    assert json.loads(runtime.read_text()) == bundle.runtime_config
    assert json.loads(identity.read_text()) == bundle.production_identity
    with pytest.raises(FileExistsError):
        write_s5_pstar_materialization_exclusive(
            bundle=bundle,
            runtime_config_path=runtime,
            production_identity_path=identity,
        )


def test_real_pstar_materialization_passes_generic_ap_qualifier(
    tmp_path: Path,
) -> None:
    bundle = materialize_s5_pstar_production_identity(
        paths=_paths(),
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-p-star-production-identity-20260816-001",
    )
    runtime = tmp_path / "runtime.json"
    identity = tmp_path / "identity.json"
    write_s5_pstar_materialization_exclusive(
        bundle=bundle,
        runtime_config_path=runtime,
        production_identity_path=identity,
    )
    junit = tmp_path / "full.xml"
    junit.write_text(
        '<testsuites tests="1" failures="0" errors="0" skipped="0">'
        '<testsuite tests="1" failures="0" errors="0" skipped="0" />'
        "</testsuites>\n",
        encoding="ascii",
    )
    source_paths = {
        "graphiti_native": _paths().graphiti_native,
        "graphiti_semantic_api": NATIVE / "S5_GRAPHITI_SEMANTIC_API_IDENTITY.json",
        "runtime_factory": _paths().runtime_factory,
        "scheduler": _paths().scheduler,
        "scheduler_test": _paths().scheduler_test,
        "durable_store": _paths().durable_store,
        "durable_store_test": _paths().durable_store_test,
        "runtime_config": runtime,
        "native_binding": PROJECT / "src/paper_eval/s5_graphiti_native_binding.py",
        "native_binding_test": PROJECT / "tests/test_s5_graphiti_native_binding.py",
        "production_runner": PROJECT / "src/paper_eval/s5_production_runner.py",
        "production_runner_test": PROJECT / "tests/test_s5_production_runner.py",
        "method_smoke_contract": PROJECT / "src/paper_eval/s5_method_smoke_contract.py",
        "method_smoke_contract_test": PROJECT / "tests/test_s5_method_smoke_contract.py",
    }
    assert set(source_paths) == AP_SOURCE_ROLES
    qualification = build_s5_production_identity_qualification(
        method="P*",
        production_identity_path=identity,
        native_baseline_freeze_path=NATIVE / "NATIVE_BASELINE_V2_FREEZE.json",
        current_stage_pointer_path=PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
        s5_plan_path=NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json",
        s5_workplan_path=(
            PROJECT / "S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md"
        ),
        source_paths=source_paths,
        full_regression_junit_path=junit,
        expected_full_test_count=1,
        git_commit="568afb26053a5f8fb133e29f0583eaa524dad1bd",
        run_id="s5-p-star-production-qualification-20260816-001",
    )
    verified = verify_s5_production_identity_qualification(qualification)
    assert verified["payload"]["method"] == "P*"
    assert verified["payload"]["source_closure_sha256"]["runtime_config"] == (
        bundle.runtime_config["payload_sha256"]
    )
