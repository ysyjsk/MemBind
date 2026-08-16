"""Seal the offline production Graphiti FX0 qualification artifacts.

This finalizer is deliberately local and service-free.  It binds the complete
controlled execution closure, verifies the full offline JUnit gate, writes new
artifacts exclusively, and never mutates the current-stage pointer or grants
live authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import PROTOCOL_VERSION
from .artifacts import (
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from .fx0_mechanism_fixture import PRODUCTION_CONTROLLED_PROVIDER_NAMES
from .s5_graphiti_fx0_environment import S5GraphitiFx0ControlledEnvironment
from .s5_graphiti_fx0_inventory import build_s5_graphiti_fx0_inventory
from .s5_mstar_fx0_artifact import (
    PINNED_GRAPHITI_SEMANTIC_API_SHA256,
    PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256,
    build_s5_mstar_fx0_artifact,
    derive_s5_mstar_fx0_fixture_manifest,
    verify_s5_mstar_fx0_artifact,
    write_s5_mstar_fx0_artifact_exclusive,
)
from .s5_mstar_production_core_identity import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_mstar_production_core_identity,
    verify_s5_mstar_production_core_identity,
)


RUNTIME_CONFIG_SCHEMA = (
    "membind.paper-eval-v3.s5-graphiti-fx0-runtime-config.v1"
)
QUALIFICATION_SCHEMA = (
    "membind.paper-eval-v3.s5-graphiti-fx0-production-qualification.v1"
)
_AUTHORITY = {
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_live_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}


class S5GraphitiFx0FinalizerError(ValueError):
    """The offline gate, source closure, or sealed output is invalid."""


def _fail(code: str) -> S5GraphitiFx0FinalizerError:
    return S5GraphitiFx0FinalizerError(code)


def _file_hash(path: Path, code: str) -> str:
    digest = sha256_file(path)
    if digest == "missing":
        raise _fail(code)
    return digest


def _json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _junit_summary(path: Path) -> dict[str, int]:
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        raise _fail("full_regression_junit_invalid") from None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        suites = list(root.iter("testsuite"))
    if not suites:
        raise _fail("full_regression_junit_invalid")
    result = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for field in result:
            try:
                result[field] += int(suite.attrib.get(field, "0"))
            except ValueError:
                raise _fail("full_regression_junit_invalid") from None
    return result


def _source_closure(
    paper_eval_root: Path, workspace_root: Path
) -> dict[str, str]:
    roles = {
        "runtime_factory": workspace_root
        / "membind-validation"
        / "src"
        / "native_characterization_runtime.py",
        "pipeline": paper_eval_root / "src" / "paper_eval" / "s5_mstar_pipeline.py",
        "pipeline_test": paper_eval_root / "tests" / "test_s5_mstar_pipeline.py",
        "adapter": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_mstar_production_adapter.py",
        "adapter_test": paper_eval_root
        / "tests"
        / "test_s5_mstar_production_adapter.py",
        "semantic_runtime": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_mstar_semantics.py",
        "semantic_runtime_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_mstar_semantics.py",
        "semantic_binding": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_semantic_binding.py",
        "semantic_binding_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_semantic_binding.py",
        "durable_store": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_durable_attempt_store.py",
        "durable_store_test": paper_eval_root
        / "tests"
        / "test_s5_durable_attempt_store.py",
        "controlled_fixture": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_controlled_fixture.py",
        "controlled_fixture_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_controlled_fixture.py",
        "controlled_environment": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_fx0_environment.py",
        "controlled_environment_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_fx0_environment.py",
        "inventory": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_fx0_inventory.py",
        "inventory_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_fx0_inventory.py",
        "artifact_contract": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_mstar_fx0_artifact.py",
        "artifact_contract_test": paper_eval_root
        / "tests"
        / "test_s5_mstar_fx0_artifact.py",
        "provider_contract": paper_eval_root
        / "src"
        / "paper_eval"
        / "fx0_mechanism_fixture.py",
        "provider_contract_test": paper_eval_root
        / "tests"
        / "test_fx0_mechanism_fixture.py",
        "core_identity": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_mstar_production_core_identity.py",
        "core_identity_test": paper_eval_root
        / "tests"
        / "test_s5_mstar_production_core_identity.py",
        "finalizer": paper_eval_root
        / "src"
        / "paper_eval"
        / "s5_graphiti_fx0_finalizer.py",
        "finalizer_test": paper_eval_root
        / "tests"
        / "test_s5_graphiti_fx0_finalizer.py",
    }
    return {
        role: _file_hash(path, f"source_closure_missing:{role}")
        for role, path in roles.items()
    }


def _input_bindings(
    *,
    spec: object,
    core: Mapping[str, object],
) -> dict[str, str]:
    manifest = derive_s5_mstar_fx0_fixture_manifest(spec)
    return {
        "parent_protocol_sha256": spec.parent_protocol_sha256,
        "amendment_sha256": spec.amendment_sha256,
        "current_stage_pointer_sha256": spec.current_stage_pointer_sha256,
        "production_core_identity_sha256": str(core["identity_sha256"]),
        "graphiti_semantic_api_identity_sha256": str(
            core["graphiti_semantic_api_sha256"]
        ),
        "graphiti_semantic_identity_artifact_sha256": str(
            core["graphiti_semantic_identity_artifact_sha256"]
        ),
        "fx0_fixture_manifest_sha256": manifest[
            "fx0_fixture_manifest_sha256"
        ],
        "execution_input_set_sha256": manifest["execution_input_set_sha256"],
        "oracle_set_sha256": manifest["oracle_set_sha256"],
        "controlled_provider_set_sha256": manifest[
            "controlled_provider_set_sha256"
        ],
        "adapter_source_sha256": str(core["adapter_source_sha256"]),
        "pipeline_source_sha256": str(core["pipeline_source_sha256"]),
        "semantic_runtime_source_sha256": str(
            core["semantic_runtime_source_sha256"]
        ),
        "semantic_binding_source_sha256": str(
            core["semantic_binding_source_sha256"]
        ),
    }


def verify_s5_graphiti_fx0_qualification(
    value: Mapping[str, Any],
    *,
    expected_current_stage_pointer_sha256: str,
) -> dict[str, Any]:
    """Independently verify the in-memory qualification bundle."""

    if not isinstance(value, Mapping):
        raise _fail("qualification_bundle_invalid")
    bundle = deepcopy(dict(value))
    payload = bundle.get("payload")
    artifacts = bundle.get("artifacts")
    if not isinstance(payload, Mapping) or not isinstance(artifacts, Mapping):
        raise _fail("qualification_bundle_invalid")
    if (
        payload.get("schema_version") != QUALIFICATION_SCHEMA
        or payload.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS"
        or payload.get("fixture_count") != 11
        or payload.get("authority") != _AUTHORITY
        or payload.get("current_stage_pointer_sha256")
        != expected_current_stage_pointer_sha256
    ):
        raise _fail("qualification_payload_invalid")
    runtime_config = artifacts.get("runtime_config")
    core = artifacts.get("core_identity")
    fx0_artifact = artifacts.get("fx0_artifact")
    qualification = artifacts.get("qualification")
    if not all(
        isinstance(item, Mapping)
        for item in (runtime_config, core, fx0_artifact, qualification)
    ):
        raise _fail("qualification_artifacts_invalid")
    runtime_payload = runtime_config.get("payload")
    if (
        runtime_config.get("payload_sha256") != payload_sha256(runtime_payload)
        or not isinstance(runtime_payload, Mapping)
        or runtime_payload.get("schema_version") != RUNTIME_CONFIG_SCHEMA
        or runtime_payload.get("authority") != _AUTHORITY
    ):
        raise _fail("runtime_config_invalid")
    verified_core = verify_s5_mstar_production_core_identity(core)
    if verified_core["runtime_config_sha256"] != runtime_config["payload_sha256"]:
        raise _fail("runtime_config_core_binding_mismatch")
    input_bindings = fx0_artifact.get("payload", {}).get("input_bindings")
    if not isinstance(input_bindings, Mapping):
        raise _fail("fx0_input_bindings_invalid")
    verified_fx0 = verify_s5_mstar_fx0_artifact(
        fx0_artifact,
        expected_input_bindings=input_bindings,
        expected_fixture_manifest_sha256=input_bindings[
            "fx0_fixture_manifest_sha256"
        ],
    )
    if (
        qualification.get("payload") != payload
        or qualification.get("payload_sha256") != payload_sha256(payload)
        or payload.get("runtime_config_sha256")
        != runtime_config["payload_sha256"]
        or payload.get("production_core_identity_sha256")
        != verified_core["identity_sha256"]
        or payload.get("fx0_artifact_payload_sha256")
        != verified_fx0["payload_sha256"]
    ):
        raise _fail("qualification_artifact_binding_mismatch")
    return bundle


def finalize_s5_graphiti_fx0_qualification(
    *,
    paper_eval_root: Path,
    workspace_root: Path,
    git_commit: str,
    run_id: str,
    full_regression_log: Path,
    expected_full_test_count: int,
    runtime_config_path: Path,
    core_identity_path: Path,
    fx0_artifact_path: Path,
    qualification_path: Path,
) -> dict[str, Any]:
    """Execute and exclusively seal the service-free production FX0 gate."""

    outputs = tuple(
        Path(path)
        for path in (
            runtime_config_path,
            core_identity_path,
            fx0_artifact_path,
            qualification_path,
        )
    )
    if any(path.exists() for path in outputs):
        raise _fail("output_exists")
    paper_eval_root = Path(paper_eval_root).resolve()
    workspace_root = Path(workspace_root).resolve()
    pointer = paper_eval_root / "runtime" / "CURRENT_STAGE_STATUS.json"
    parent_protocol = (
        workspace_root
        / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
    )
    amendment = paper_eval_root / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md"
    workplan = paper_eval_root / "S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md"
    semantic_path = (
        paper_eval_root
        / "artifacts"
        / "paper_eval"
        / "native"
        / "S5_GRAPHITI_SEMANTIC_API_IDENTITY.json"
    )
    pointer_sha256 = _file_hash(pointer, "current_stage_pointer_missing")
    full_summary = _junit_summary(Path(full_regression_log))
    if full_summary != {
        "tests": expected_full_test_count,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }:
        raise _fail("full_regression_not_green")
    semantic = _json(semantic_path, "semantic_identity_artifact_invalid")
    if (
        semantic.get("identity_sha256") != PINNED_GRAPHITI_SEMANTIC_API_SHA256
        or semantic.get("payload_sha256")
        != PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256
    ):
        raise _fail("semantic_identity_artifact_mismatch")
    closure = _source_closure(paper_eval_root, workspace_root)
    runtime_payload = {
        "schema_version": RUNTIME_CONFIG_SCHEMA,
        "status": "FROZEN_OFFLINE_CONTROLLED_EXECUTION",
        "graphiti_version": GRAPHITI_VERSION,
        "graphiti_commit": GRAPHITI_COMMIT,
        "prepare_concurrency": 2,
        "strict_episode_source_schema": "GRAPHITI_EPISODE_BATCH_V1",
        "provider_factory_case_input_allowed": False,
        "controlled_nondeterminism_providers": list(
            PRODUCTION_CONTROLLED_PROVIDER_NAMES
        ),
        "transaction_io_policy": "FAIL_AFTER_CALLBACK_ATTEMPT_1_OR_NONE",
        "publication_sink_actions": ["APPEND", "DROP", "DUPLICATE"],
        "source_ordered_bind_required": True,
        "prepare_rendezvous_parties": 2,
        "source_closure_sha256": closure,
        "s5_workplan_sha256": _file_hash(workplan, "s5_workplan_missing"),
        "semantic_identity_file_sha256": _file_hash(
            semantic_path, "semantic_identity_artifact_missing"
        ),
        "authority": deepcopy(_AUTHORITY),
    }
    runtime_config = finalize_envelope(
        payload=runtime_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=f"{run_id}-runtime-config",
    )
    core = build_s5_mstar_production_core_identity(
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_semantic_api_sha256=PINNED_GRAPHITI_SEMANTIC_API_SHA256,
        graphiti_semantic_identity_artifact_sha256=(
            PINNED_GRAPHITI_SEMANTIC_IDENTITY_ARTIFACT_SHA256
        ),
        runtime_factory_entrypoint=(
            "native_characterization_runtime.build_u0_graphiti_from_env"
        ),
        runtime_factory_source_sha256=closure["runtime_factory"],
        pipeline_source_sha256=closure["pipeline"],
        pipeline_test_source_sha256=closure["pipeline_test"],
        adapter_source_sha256=closure["adapter"],
        adapter_test_source_sha256=closure["adapter_test"],
        semantic_runtime_source_sha256=closure["semantic_runtime"],
        semantic_runtime_test_source_sha256=closure["semantic_runtime_test"],
        semantic_binding_source_sha256=closure["semantic_binding"],
        semantic_binding_test_source_sha256=closure["semantic_binding_test"],
        durable_store_source_sha256=closure["durable_store"],
        durable_store_test_source_sha256=closure["durable_store_test"],
        runtime_config_sha256=runtime_config["payload_sha256"],
    )
    spec = build_s5_graphiti_fx0_inventory(
        run_id=run_id,
        parent_protocol_sha256=_file_hash(parent_protocol, "parent_protocol_missing"),
        amendment_sha256=_file_hash(amendment, "amendment_missing"),
        current_stage_pointer_sha256=pointer_sha256,
        production_core_identity_sha256=str(core["identity_sha256"]),
    )
    bindings = _input_bindings(spec=spec, core=core)
    environment = S5GraphitiFx0ControlledEnvironment()
    fx0_artifact = build_s5_mstar_fx0_artifact(
        spec=spec,
        mechanism=environment.build_adapter(production_core_identity=core),
        production_core_identity=core,
        expected_input_bindings=bindings,
        git_commit=git_commit,
    )
    verify_s5_mstar_fx0_artifact(
        fx0_artifact,
        expected_input_bindings=bindings,
        expected_fixture_manifest_sha256=bindings[
            "fx0_fixture_manifest_sha256"
        ],
    )
    qualification_payload = {
        "schema_version": QUALIFICATION_SCHEMA,
        "verdict": "PRODUCTION_PATH_EXACT_PARITY_PASS",
        "fixture_count": 11,
        "run_id": run_id,
        "runtime_config_sha256": runtime_config["payload_sha256"],
        "production_core_identity_sha256": core["identity_sha256"],
        "fx0_artifact_payload_sha256": fx0_artifact["payload_sha256"],
        "fx0_fixture_manifest_sha256": bindings[
            "fx0_fixture_manifest_sha256"
        ],
        "current_stage_pointer_sha256": pointer_sha256,
        "full_regression_junit_sha256": _file_hash(
            Path(full_regression_log), "full_regression_junit_missing"
        ),
        "full_regression_summary": full_summary,
        "legacy_status_artifact_preserved": True,
        "authority": deepcopy(_AUTHORITY),
    }
    qualification = finalize_envelope(
        payload=qualification_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=f"{run_id}-qualification",
    )
    bundle = {
        "payload": qualification_payload,
        "artifacts": {
            "runtime_config": runtime_config,
            "core_identity": core,
            "fx0_artifact": fx0_artifact,
            "qualification": qualification,
        },
    }
    verify_s5_graphiti_fx0_qualification(
        bundle,
        expected_current_stage_pointer_sha256=pointer_sha256,
    )
    atomic_write_json(Path(runtime_config_path), runtime_config)
    atomic_write_json(Path(core_identity_path), core)
    write_s5_mstar_fx0_artifact_exclusive(Path(fx0_artifact_path), fx0_artifact)
    atomic_write_json(Path(qualification_path), qualification)
    if _file_hash(pointer, "current_stage_pointer_missing") != pointer_sha256:
        raise _fail("current_stage_pointer_changed")
    return bundle


__all__ = [
    "QUALIFICATION_SCHEMA",
    "RUNTIME_CONFIG_SCHEMA",
    "S5GraphitiFx0FinalizerError",
    "finalize_s5_graphiti_fx0_qualification",
    "verify_s5_graphiti_fx0_qualification",
]
