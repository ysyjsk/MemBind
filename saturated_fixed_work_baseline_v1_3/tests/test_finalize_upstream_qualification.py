from __future__ import annotations

import importlib.util
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from graphiti_core.prompts import extract_edges, extract_nodes
from graphiti_core.prompts.extract_edges import ExtractedEdges
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    request_hash,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "saturated_fixed_work_baseline_v1_3"
    / "scripts"
    / "finalize_upstream_qualification.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "finalize_upstream_qualification", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _qualified_fixture(module, root: Path) -> dict:
    policy = module.DEPLOYMENT_POLICY
    edge_schema = ExtractedEdges.model_json_schema()
    edge_schema_text = json.dumps(
        edge_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    cells = []
    for index, arm in enumerate(module.ARMS):
        cell = {
            "status": "PASS",
            "arm": arm,
            "history_index": 0,
            "history_id": "history-0",
            "replicate_id": 0,
            "attempt_id": f"attempt-{index}",
            "namespace": f"namespace-{index}",
        }
        cells.append(cell)
        attempt = root / "history-0" / "replicate-0" / arm / cell["attempt_id"]
        _write(
            attempt / "complete.json",
            {
                "status": "PASS",
                "attempt_id": cell["attempt_id"],
                "namespace": cell["namespace"],
                "method": arm,
            },
        )
        _write(
            attempt / "run_contract.json",
            {
                "attempt_id": cell["attempt_id"],
                "namespace": cell["namespace"],
                "arm": arm,
                "history_index": 0,
                "replicate_id": 0,
                "dataset_authority_sha256": "d" * 64,
                "chunk_manifest_sha256": "w" * 64,
                "implementation": {"payload_sha256": "s" * 64},
                "platform": {"payload_sha256": "p" * 64},
            },
        )
        _write(
            attempt / "block/construction_seal.json",
            {
                "status": "CONSTRUCTION_SEALED",
                "identity": {
                    "method": arm,
                    "namespace": cell["namespace"],
                    "workload_hash": "w" * 64,
                },
            },
        )
        _write(attempt / "route_seal.json", {"status": "ROUTE_SEALED"})
        _write(
            attempt / "block/adapter_coverage.json",
            {
                "status": "PASS",
                "adapter_version": "MAB_ROLE_AWARE_LOSSLESS_8192_V1",
                "chunk_count": 123,
            },
        )
        _write(
            attempt / "block/work_inventory.json",
            {
                "expected_episode_count": 123,
                "submitted_count": 123,
                "completed_count": 123,
            },
        )
        _write(
            attempt / "block/graph_diagnostics.json",
            {
                "status": "PASS",
                "episodes": [
                    {"source_sequence": sequence, "source_hash": f"h{sequence}", "session_id": "s"}
                    for sequence in range(123)
                ],
                "entities": [{"name": "entity"}],
                "edges": [{"fact": "entity relates to entity"}],
            },
        )
        runtime_identity = {
                "status": "PASS",
                "schema_version": "membind.formal-runtime-identity.v1",
                "arm": arm,
                "strict_upstream_core": True,
                "graphiti": {
                    "version": GRAPHITI_VERSION,
                    "installed_version": GRAPHITI_VERSION,
                    "commit": GRAPHITI_COMMIT,
                    "class_module": "graphiti_core.graphiti",
                    "class_qualname": "Graphiti",
                    "add_episode_module": "graphiti_core.graphiti",
                    "add_episode_qualname": "Graphiti.add_episode",
                },
                "llm_client_class": (
                    "graphiti_core.llm_client.openai_generic_client."
                    "OpenAIGenericClient"
                ),
                "edge_response_model": {
                    "module": "graphiti_core.prompts.extract_edges",
                    "qualname": "ExtractedEdges",
                    "schema_sha256": hashlib.sha256(
                        edge_schema_text.encode("utf-8")
                    ).hexdigest(),
                    "schema": edge_schema,
                    "edges_has_max_items": False,
                },
                "upstream_prompt_source_sha256": {
                    "extract_nodes": hashlib.sha256(
                        inspect.getsource(extract_nodes.extract_message).encode("utf-8")
                    ).hexdigest(),
                    "extract_edges": hashlib.sha256(
                        inspect.getsource(extract_edges.edge).encode("utf-8")
                    ).hexdigest(),
                },
                "deployment_policy_id": policy.policy_id,
                "model": policy.served_model,
                "model_revision": policy.revision,
                "sampling": dict(policy.sampling),
                "max_tokens": 16384,
                "structured_output_mode": "json_schema",
                "logical_seed_policy": (
                    "uint32_sha256_dataset_context_source_chunk_prompt_messages"
                ),
                "sdk_retries": 0,
                "finite_pair_tasks_enabled": False,
                "response_repair_enabled": False,
                "extraction_chunking_installed": False,
                "mab8192_manifest_sha256": "w" * 64,
                "patch_inventory": {
                    "strict_upstream_core": True,
                    "graphiti_algorithm_mutated": False,
                    "shared_compatibility_substrate": False,
                    "algorithm_patches": [],
                    "prohibited_algorithm_patches": [],
                    "deployment_policy_id": policy.policy_id,
                },
            }
        runtime_identity["runtime_identity_sha256"] = request_hash(runtime_identity)
        _write(attempt / "block/runtime_identity.json", runtime_identity)
    return {"cells": cells}


def test_finalizer_revalidates_all_three_terminal_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    module._validate_qualification_artifacts(
        qualification_root=tmp_path,
        qualification=qualification,
        source_bundle_sha256="s" * 64,
        platform_payload_sha256="p" * 64,
        dataset_authority_sha256="d" * 64,
        workload_manifest_sha256="w" * 64,
    )


def test_finalizer_rejects_failure_artifact_on_declared_pass(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    failure = (
        tmp_path
        / "history-0"
        / "replicate-0"
        / first["arm"]
        / first["attempt_id"]
        / "failure.json"
    )
    _write(failure, {"status": "FAILED"})
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    with pytest.raises(RuntimeError, match="failure artifact"):
        module._validate_qualification_artifacts(
            qualification_root=tmp_path,
            qualification=qualification,
            source_bundle_sha256="s" * 64,
            platform_payload_sha256="p" * 64,
            dataset_authority_sha256="d" * 64,
            workload_manifest_sha256="w" * 64,
        )


def test_finalizer_rejects_source_bundle_drift(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)
    with pytest.raises(RuntimeError, match="artifact identity"):
        module._validate_qualification_artifacts(
            qualification_root=tmp_path,
            qualification=qualification,
            source_bundle_sha256="x" * 64,
            platform_payload_sha256="p" * 64,
            dataset_authority_sha256="d" * 64,
            workload_manifest_sha256="w" * 64,
        )


def test_finalizer_rejects_forged_runtime_callable_identity(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    identity_path = (
        tmp_path
        / "history-0"
        / "replicate-0"
        / first["arm"]
        / first["attempt_id"]
        / "block/runtime_identity.json"
    )
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["llm_client_class"] = "local.compatibility.RepairingClient"
    identity["runtime_identity_sha256"] = request_hash(
        {key: value for key, value in identity.items() if key != "runtime_identity_sha256"}
    )
    _write(identity_path, identity)
    monkeypatch.setattr(module, "verify_seal", lambda _root: None)

    with pytest.raises(RuntimeError, match="runtime identity"):
        module._validate_qualification_artifacts(
            qualification_root=tmp_path,
            qualification=qualification,
            source_bundle_sha256="s" * 64,
            platform_payload_sha256="p" * 64,
            dataset_authority_sha256="d" * 64,
            workload_manifest_sha256="w" * 64,
        )


def test_finalizer_accepts_authenticated_strict_l1_as_compatibility_authority(
    tmp_path: Path,
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    runtime_identity = json.loads(
        (
            tmp_path
            / "history-0"
            / "replicate-0"
            / first["arm"]
            / first["attempt_id"]
            / "block/runtime_identity.json"
        ).read_text(encoding="utf-8")
    )
    strict_l1 = {
        "schema_version": "membind.strict-upstream-l1.v1",
        "status": "PASS",
        "scope": "EXACT_GROWING_HISTORY_REQUEST_QUALIFICATION",
        "request_checks": {"messages": True, "schema": True, "seed": True},
        "response": {
            "status": "PASS",
            "finish_reason": "stop",
            "json_valid": True,
            "pydantic_valid": True,
            "schema_valid": True,
            "reached_token_limit": False,
            "response_repair_enabled": False,
        },
        "runtime_identity": runtime_identity,
        "provider_retry_count": 0,
        "target_provider_request_count": 1,
        "historical_comparison": {
            "upstream_identity_exact_except_declared_deployment": True
        },
        "namespace_unchanged_before_replay": True,
        "namespace_unchanged_after_provider_request": True,
    }

    selection, authority_type = module._validate_compatibility_authority(strict_l1)

    assert selection == module.DEPLOYMENT_POLICY.policy_id
    assert authority_type == "EXACT_STRICT_L1"


def test_finalizer_accepts_authenticated_content_witness_strict_l1(
    tmp_path: Path,
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    runtime_identity = json.loads(
        (
            tmp_path
            / "history-0"
            / "replicate-0"
            / first["arm"]
            / first["attempt_id"]
            / "block/runtime_identity.json"
        ).read_text(encoding="utf-8")
    )
    strict_l1 = {
        "schema_version": "membind.strict-upstream-l1.v1",
        "status": "PASS",
        "scope": "EXACT_GROWING_HISTORY_REQUEST_QUALIFICATION",
        "request_checks": {"all": True, "observed_witness_edge": True},
        "response": {
            "status": "PASS",
            "finish_reason": "stop",
            "json_valid": True,
            "pydantic_valid": True,
            "schema_valid": True,
            "reached_token_limit": False,
            "response_repair_enabled": False,
            "edge_count": 1,
            "content_bearing_witness": True,
        },
        "runtime_identity": runtime_identity,
        "provider_retry_count": 0,
        "target_provider_request_count": 1,
        "request_identity": {"source_capture": "official_mab8192_witness"},
        "witness_selection": {
            "distinct_entity_count": 2,
            "current_message_contains_all_entities": True,
        },
        "witness_provenance": {
            "status": "PASS",
            "expected_edge": {"source_entity_key": "jetblue"},
        },
        "namespace_unchanged_before_replay": True,
        "namespace_unchanged_after_provider_request": True,
    }

    selection, authority_type = module._validate_compatibility_authority(strict_l1)

    assert selection == module.DEPLOYMENT_POLICY.policy_id
    assert authority_type == "EXACT_STRICT_L1_CONTENT_WITNESS"


def test_finalizer_rejects_strict_l1_with_more_than_one_target_call(
    tmp_path: Path,
) -> None:
    module = _module()
    qualification = _qualified_fixture(module, tmp_path)
    first = qualification["cells"][0]
    runtime_identity = json.loads(
        (
            tmp_path
            / "history-0"
            / "replicate-0"
            / first["arm"]
            / first["attempt_id"]
            / "block/runtime_identity.json"
        ).read_text(encoding="utf-8")
    )
    strict_l1 = {
        "schema_version": "membind.strict-upstream-l1.v1",
        "status": "PASS",
        "scope": "EXACT_GROWING_HISTORY_REQUEST_QUALIFICATION",
        "request_checks": {"all": True},
        "response": {
            "status": "PASS",
            "finish_reason": "stop",
            "json_valid": True,
            "pydantic_valid": True,
            "schema_valid": True,
            "reached_token_limit": False,
            "response_repair_enabled": False,
        },
        "runtime_identity": runtime_identity,
        "provider_retry_count": 0,
        "target_provider_request_count": 2,
        "historical_comparison": {
            "upstream_identity_exact_except_declared_deployment": True
        },
        "namespace_unchanged_before_replay": True,
        "namespace_unchanged_after_provider_request": True,
    }

    with pytest.raises(RuntimeError, match="strict L1"):
        module._validate_compatibility_authority(strict_l1)


def test_finalizer_hashes_active_routes_and_matches_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    native = {"profile_id": "p2", "arm": "native"}
    membind = {"profile_id": "p2", "arm": "membind"}
    native_path = tmp_path / "native.json"
    membind_path = tmp_path / "membind.json"
    _write(native_path, native)
    _write(membind_path, membind)
    monkeypatch.setenv("MEMBIND_NATIVE_ROUTING_CONFIG", str(native_path))
    monkeypatch.setenv("MEMBIND_V61_ROUTING_CONFIG", str(membind_path))
    platform = {
        "routing_contracts": {
            "native_dual_resource_matched": native,
            "v61_dual_elastic_affinity": membind,
        }
    }

    paths = module._active_route_paths(platform)

    assert paths == {"native": native_path, "membind": membind_path}
