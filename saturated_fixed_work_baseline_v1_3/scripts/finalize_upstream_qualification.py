#!/usr/bin/env python3
"""Finalize upstream-only L2 evidence and authorize a fresh formal manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab8192_adapter import (  # noqa: E402
    MAB8192Manifest,
    adapter_identity,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (  # noqa: E402
    implementation_bundle,
)
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (  # noqa: E402
    ResourceCreditPolicy,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    FORMAL_ARM_A,
    FORMAL_ARM_B,
    FORMAL_ARM_C,
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    formal_builder_source_audit,
    resolve_deployment_policy,
    strict_formal_runtime_identity_errors,
)


ARMS = (FORMAL_ARM_A, FORMAL_ARM_C, FORMAL_ARM_B)
RUNNER = SFWB / "scripts/run_mab_upstream_8b.py"
QA_RUNNER = SFWB / "scripts/run_mab_v13_qa_resume.py"
DEPLOYMENT_POLICY = resolve_deployment_policy()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _validate_compatibility_authority(
    compatibility: Mapping[str, Any],
) -> tuple[str, str]:
    """Accept either the historical replay seal or the stronger exact L1 seal."""

    if compatibility.get("schema_version") == "membind.strict-upstream-l1.v1":
        request_checks = compatibility.get("request_checks")
        response = compatibility.get("response")
        historical = compatibility.get("historical_comparison")
        runtime_identity = compatibility.get("runtime_identity")
        runtime_errors = strict_formal_runtime_identity_errors(
            runtime_identity,
            expected_arm=FORMAL_ARM_A,
            expected_deployment_policy=DEPLOYMENT_POLICY,
        )
        valid = (
            compatibility.get("status") == "PASS"
            and compatibility.get("scope")
            == "EXACT_GROWING_HISTORY_REQUEST_QUALIFICATION"
            and isinstance(request_checks, Mapping)
            and bool(request_checks)
            and all(value is True for value in request_checks.values())
            and isinstance(response, Mapping)
            and response.get("status") == "PASS"
            and response.get("finish_reason") == "stop"
            and response.get("json_valid") is True
            and response.get("pydantic_valid") is True
            and response.get("schema_valid") is True
            and response.get("reached_token_limit") is False
            and response.get("response_repair_enabled") is False
            and compatibility.get("provider_retry_count") == 0
            and compatibility.get("target_provider_request_count") == 1
            and isinstance(historical, Mapping)
            and historical.get(
                "upstream_identity_exact_except_declared_deployment"
            )
            is True
            and compatibility.get("namespace_unchanged_before_replay") is True
            and compatibility.get("namespace_unchanged_after_provider_request")
            is True
            and not runtime_errors
        )
        if not valid:
            details = "; ".join(runtime_errors) if runtime_errors else "gate mismatch"
            raise RuntimeError(f"exact strict L1 authority is invalid: {details}")
        return DEPLOYMENT_POLICY.policy_id, "EXACT_STRICT_L1"

    if (
        compatibility.get("status") == "PASS"
        and compatibility.get("selection") == DEPLOYMENT_POLICY.policy_id
        and compatibility.get("deployment_policy_id")
        == DEPLOYMENT_POLICY.policy_id
    ):
        return DEPLOYMENT_POLICY.policy_id, "LEGACY_COMPATIBILITY_REPLAY"
    raise RuntimeError("selected deployment compatibility authority is not PASS")


def _active_route_paths(platform: Mapping[str, Any]) -> dict[str, Path]:
    contracts = platform.get("routing_contracts")
    if not isinstance(contracts, Mapping):
        raise RuntimeError("platform routing contracts are missing")
    bindings = {
        "native": (
            "MEMBIND_NATIVE_ROUTING_CONFIG",
            "native_dual_resource_matched",
        ),
        "membind": (
            "MEMBIND_V61_ROUTING_CONFIG",
            "v61_dual_elastic_affinity",
        ),
    }
    paths: dict[str, Path] = {}
    for name, (environment_key, contract_key) in bindings.items():
        raw_path = os.environ.get(environment_key)
        if not raw_path:
            raise RuntimeError(f"active route path is missing: {environment_key}")
        path = Path(raw_path).resolve()
        if _read(path) != contracts.get(contract_key):
            raise RuntimeError(f"active {name} route differs from sealed platform")
        paths[name] = path
    return paths


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {"head": head, "dirty_paths": dirty}


def _validate_qualification_artifacts(
    *,
    qualification_root: Path,
    qualification: Mapping[str, Any],
    source_bundle_sha256: str,
    platform_payload_sha256: str,
    dataset_authority_sha256: str,
    workload_manifest_sha256: str,
) -> None:
    cells = qualification.get("cells")
    if not isinstance(cells, list) or [cell.get("arm") for cell in cells] != list(ARMS):
        raise RuntimeError("qualification cell inventory or order is invalid")
    for cell in cells:
        if (
            cell.get("status") != "PASS"
            or cell.get("history_index") != 0
            or cell.get("replicate_id") != 0
            or cell.get("history_id") != qualification.get("cells", [{}])[0].get("history_id")
        ):
            raise RuntimeError("qualification cell status or identity is invalid")
        attempt = (
            qualification_root
            / "history-0"
            / "replicate-0"
            / str(cell["arm"])
            / str(cell["attempt_id"])
        )
        if (attempt / "failure.json").exists():
            raise RuntimeError("qualification PASS cell has a failure artifact")
        complete = _read(attempt / "complete.json")
        contract = _read(attempt / "run_contract.json")
        seal = _read(attempt / "block/construction_seal.json")
        route_seal = _read(attempt / "route_seal.json")
        adapter = _read(attempt / "block/adapter_coverage.json")
        inventory = _read(attempt / "block/work_inventory.json")
        runtime_identity = _read(attempt / "block/runtime_identity.json")
        runtime_identity_errors = strict_formal_runtime_identity_errors(
            runtime_identity,
            expected_arm=str(cell.get("arm")),
            expected_manifest_sha256=workload_manifest_sha256,
            expected_deployment_policy=DEPLOYMENT_POLICY,
        )
        if runtime_identity_errors:
            raise RuntimeError(
                "qualification runtime identity is invalid: "
                + "; ".join(runtime_identity_errors)
            )
        verify_seal(attempt / "block")
        identity = seal.get("identity") if isinstance(seal.get("identity"), Mapping) else {}
        expected_chunks = adapter.get("chunk_count")
        if not (
            complete.get("status") == "PASS"
            and complete.get("attempt_id") == cell.get("attempt_id")
            and complete.get("namespace") == cell.get("namespace")
            and complete.get("method") == cell.get("arm")
            and contract.get("attempt_id") == cell.get("attempt_id")
            and contract.get("namespace") == cell.get("namespace")
            and contract.get("arm") == cell.get("arm")
            and contract.get("history_index") == 0
            and contract.get("replicate_id") == 0
            and contract.get("dataset_authority_sha256") == dataset_authority_sha256
            and contract.get("chunk_manifest_sha256") == workload_manifest_sha256
            and isinstance(contract.get("implementation"), Mapping)
            and contract["implementation"].get("payload_sha256") == source_bundle_sha256
            and isinstance(contract.get("platform"), Mapping)
            and contract["platform"].get("payload_sha256") == platform_payload_sha256
            and seal.get("status") == "CONSTRUCTION_SEALED"
            and identity.get("method") == cell.get("arm")
            and identity.get("namespace") == cell.get("namespace")
            and identity.get("workload_hash") == workload_manifest_sha256
            and route_seal.get("status") == "ROUTE_SEALED"
            and adapter.get("status") == "PASS"
            and adapter.get("adapter_version") == "MAB_ROLE_AWARE_LOSSLESS_8192_V1"
            and isinstance(expected_chunks, int)
            and expected_chunks > 0
            and inventory.get("expected_episode_count") == expected_chunks
            and inventory.get("submitted_count") == expected_chunks
            and inventory.get("completed_count") == expected_chunks
        ):
            raise RuntimeError("qualification cell artifact identity is invalid")


def finalize(
    *,
    qualification_root: Path,
    platform_manifest: Path,
    compatibility_replay: Path,
    output_root: Path,
) -> dict[str, Any]:
    qualification_root = qualification_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise RuntimeError("identity output root must be fresh")
    builder_audit = formal_builder_source_audit()
    qualification = _read(qualification_root / "L2_QUALIFICATION_RESULT.json")
    if (
        qualification.get("status") != "PASS"
        or tuple(qualification.get("arms", ())) != ARMS
        or qualification.get("history_index") != 0
        or qualification.get("valid_cell_count") != 3
    ):
        raise RuntimeError("full-history upstream L2 qualification is incomplete")
    platform = _read(platform_manifest.resolve())
    if (
        platform.get("platform_formal_eligible") is not True
        or platform.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
    ):
        raise RuntimeError("platform is not formal eligible")
    compatibility = _read(compatibility_replay.resolve())
    compatibility_selection, compatibility_authority_type = (
        _validate_compatibility_authority(compatibility)
    )

    authority_full = build_authority(MAB / "data/official_5_contexts.json")
    authority = {
        key: value for key, value in authority_full.items() if key != "contexts"
    }
    manifests = [
        MAB8192Manifest.from_context(
            context, dataset_revision=str(authority["revision"])
        )
        for context in authority_full["contexts"]
    ]
    source_bundle = implementation_bundle(RUNNER)
    component_hashes = {
        component["name"]: component["payload_sha256"]
        for component in source_bundle["components"]
    }
    evaluator_payload = {
        name: component_hashes[name]
        for name in ("qa_resume", "qa_core", "mab_quality_v2_final_qa", "paper_eval")
    }
    evaluator_sha256 = _canonical_sha256(evaluator_payload)
    adapter = {
        "schema_version": "membind.mab-shared-adapter-identity.v1",
        "status": "FROZEN",
        **adapter_identity(),
        "dataset_authority_sha256": authority["authority_sha256"],
        "dataset_revision": authority["revision"],
        "context_manifest_sha256": [manifest.manifest_sha256 for manifest in manifests],
        "context_chunk_counts": [len(manifest.chunks) for manifest in manifests],
        "context_session_counts": [len(context.sessions) for context in authority_full["contexts"]],
        "total_chunk_count": sum(len(manifest.chunks) for manifest in manifests),
        "total_session_count": sum(len(context.sessions) for context in authority_full["contexts"]),
    }
    adapter_sha256 = _canonical_sha256(adapter)
    _validate_qualification_artifacts(
        qualification_root=qualification_root,
        qualification=qualification,
        source_bundle_sha256=source_bundle["payload_sha256"],
        platform_payload_sha256=platform["payload_sha256"],
        dataset_authority_sha256=authority["authority_sha256"],
        workload_manifest_sha256=manifests[0].manifest_sha256,
    )
    route_paths = _active_route_paths(platform)
    config_payload = {
        "platform_payload_sha256": platform["payload_sha256"],
        "platform_file_sha256": _file_sha256(platform_manifest.resolve()),
        "routes": {name: _file_sha256(path) for name, path in route_paths.items()},
        "graphiti": {"version": GRAPHITI_VERSION, "commit": GRAPHITI_COMMIT},
        "deployment_policy_id": DEPLOYMENT_POLICY.policy_id,
        "model": DEPLOYMENT_POLICY.served_model,
        "sampling": dict(DEPLOYMENT_POLICY.sampling),
        "adapter_identity_sha256": adapter_sha256,
        "resource_credit": ResourceCreditPolicy().to_dict(),
        "sdk_retries": 0,
        "arm_order": list(ARMS),
    }
    config_sha256 = _canonical_sha256(config_payload)
    actual = {
        "schema_version": "membind.actual-formal-arm-identity.v1",
        "status": "QUALIFIED",
        "arms": list(ARMS),
        "qualification_builder": "build_formal_upstream_runtime",
        "formal_builder": "build_formal_upstream_runtime",
        "runner": str(RUNNER.resolve()),
        "runner_sha256": _file_sha256(RUNNER),
        "qa_runner": str(QA_RUNNER.resolve()),
        "qa_runner_sha256": _file_sha256(QA_RUNNER),
        "source_bundle_sha256": source_bundle["payload_sha256"],
        "source_bundle": source_bundle,
        "evaluator_sha256": evaluator_sha256,
        "evaluator_components": evaluator_payload,
        "config_sha256": config_sha256,
        "config": config_payload,
        "dataset_authority_sha256": authority["authority_sha256"],
        "adapter_identity_sha256": adapter_sha256,
        "platform_manifest": {
            "path": str(platform_manifest.resolve()),
            "file_sha256": _file_sha256(platform_manifest.resolve()),
            "payload_sha256": platform["payload_sha256"],
            "profile_id": platform.get("profile_id"),
            "deployment_policy_id": platform.get("deployment_policy_id"),
        },
        "git": _git_identity(),
        "formal_builder_source_audit": builder_audit,
    }
    actual_sha256 = _canonical_sha256(actual)
    rebound_compatibility = {
        "schema_version": "membind.model-compatibility-replay-rebound.v1",
        "status": "PASS",
        "selection": compatibility_selection,
        "authority_type": compatibility_authority_type,
        "authority_path": str(compatibility_replay.resolve()),
        "authority_file_sha256": _file_sha256(compatibility_replay.resolve()),
        "authority_payload": compatibility,
        "platform_payload_sha256": platform["payload_sha256"],
    }
    qualification_rebound = {
        "schema_version": "membind.full-history-three-arm-qualification.v1",
        "status": "PASS",
        "history_index": 0,
        "arms": list(ARMS),
        "qualification_root": str(qualification_root),
        "qualification_result_sha256": _file_sha256(
            qualification_root / "L2_QUALIFICATION_RESULT.json"
        ),
        "cells": qualification["cells"],
        "performance_use": "QUALIFICATION_ONLY_NOT_METHOD_SELECTION",
    }
    frozen = {
        "schema_version": "membind.final-upstream-method-freeze.v1",
        "status": "FINAL_METHOD_FROZEN",
        "method_identity": "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192_RESOURCE_CREDIT",
        "arms": list(ARMS),
        "arm_order": list(ARMS),
        "source_identity": {
            "source_bundle_sha256": source_bundle["payload_sha256"],
            **actual["git"],
        },
        "implementation_identity_sha256": actual_sha256,
        "adapter_identity_sha256": adapter_sha256,
        "dataset_authority_sha256": authority["authority_sha256"],
        "evaluator_identity_sha256": evaluator_sha256,
        "config_identity_sha256": config_sha256,
        "platform_manifest": actual["platform_manifest"],
        "qualification_result_sha256": _canonical_sha256(qualification_rebound),
        "compatibility_replay_sha256": _canonical_sha256(rebound_compatibility),
        "resource_credit": ResourceCreditPolicy().to_dict(),
        "formal_recovery_policy": "NO_RESUME_FORMAL_ATTEMPT",
        "formal_authorized": True,
        "frozen_unix": time.time(),
    }
    frozen["seal_sha256"] = _canonical_sha256(frozen)
    historical = {
        "schema_version": "membind.historical-evidence-classification.v1",
        "status": "PASS",
        "non_authoritative_root": str(
            SFWB / "structured_output_recovery"
        ),
        "reason": "FINITE_PAIR_TASK_OR_PRE_UPSTREAM_MAB8192_IDENTITY",
        "preservation": "FILES_RETAINED_UNMODIFIED",
    }

    output_root.mkdir(parents=True, exist_ok=False)
    for name, value in (
        ("dataset_authority.json", authority),
        ("MAB_SHARED_ADAPTER_IDENTITY.json", adapter),
        ("ACTUAL_FORMAL_ARM_IDENTITY.json", actual),
        ("MODEL_COMPATIBILITY_REPLAY.json", rebound_compatibility),
        ("FULL_HISTORY_THREE_ARM_QUALIFICATION.json", qualification_rebound),
        ("FINAL_METHOD_FROZEN.json", frozen),
        ("HISTORICAL_EVIDENCE_CLASSIFICATION.json", historical),
    ):
        _write_new(output_root / name, value)
    (output_root / "MODEL_COMPATIBILITY_REPLAY.md").write_text(
        "# Model Compatibility Authority\n\n"
        f"{DEPLOYMENT_POLICY.policy_id} {DEPLOYMENT_POLICY.source_model} passed "
        f"{compatibility_authority_type} before full-history L2.\n",
        encoding="utf-8",
    )
    (output_root / "FULL_HISTORY_THREE_ARM_QUALIFICATION.md").write_text(
        "# Full-History Three-Arm Qualification\n\nStatus: `PASS`. History 0 completed in fixed Native -> Ours -> Async order. Timing and quality were not used for method selection.\n",
        encoding="utf-8",
    )
    return frozen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification-root", type=Path, required=True)
    parser.add_argument("--platform-manifest", type=Path, required=True)
    parser.add_argument("--compatibility-replay", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    frozen = finalize(
        qualification_root=args.qualification_root,
        platform_manifest=args.platform_manifest,
        compatibility_replay=args.compatibility_replay,
        output_root=args.output_root,
    )
    print(
        json.dumps(
            {"status": frozen["status"], "seal_sha256": frozen["seal_sha256"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
