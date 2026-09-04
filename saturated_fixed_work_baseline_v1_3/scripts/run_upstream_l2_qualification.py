#!/usr/bin/env python3
"""Run full history 0 upstream qualification in fixed A -> C -> B order."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", MAB / "src", SFWB / "scripts"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab8192_adapter import MAB8192Manifest  # noqa: E402
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from formal_three_arm_harness import ARMS  # noqa: E402
from run_formal_three_arm import (  # noqa: E402
    RUNNER,
    _active_exact_pids,
    _attempt_env,
    _attempt_path,
    _construction_contract,
    _formal_env,
    _run_process,
)
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (  # noqa: E402
    implementation_bundle,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    resolve_deployment_policy,
)


DEPLOYMENT_POLICY = resolve_deployment_policy()
PROFILE_ID = DEPLOYMENT_POLICY.profile_id
EXPECTED_ARTIFACTS = [
    "attempt_preparation.json",
    "run_contract.json",
    "complete.json",
    "block/construction_seal.json",
    "block/metrics.json",
    "block/adapter_coverage.json",
    "block/order_validation.json",
    "block/lifecycle_validation.json",
    "block/refinement_validation.json",
    "block/work_inventory.json",
    "route_events.jsonl",
    "route_runtime.json",
    "route_proof.json",
    "route_seal.json",
]


def _qualification_env(
    env: Mapping[str, str], cell: Mapping[str, Any]
) -> dict[str, str]:
    """Bind qualification provider evidence to this exact measured attempt."""

    return _attempt_env(dict(env), dict(cell))


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


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_manifest(root: Path, platform_manifest: Path) -> dict[str, Any]:
    authority_full = build_authority(MAB / "data/official_5_contexts.json")
    authority = {
        key: value for key, value in authority_full.items() if key != "contexts"
    }
    context = authority_full["contexts"][0]
    workload = MAB8192Manifest.from_context(
        context, dataset_revision=str(authority["revision"])
    )
    platform = _read(platform_manifest)
    if (
        platform.get("profile_id") != PROFILE_ID
        or platform.get("deployment_policy_id") != DEPLOYMENT_POLICY.policy_id
        or platform.get("llm_model", {}).get("served_model")
        != DEPLOYMENT_POLICY.served_model
        or platform.get("llm_model", {}).get("revision")
        != DEPLOYMENT_POLICY.revision
    ):
        raise RuntimeError("qualification platform deployment identity mismatch")
    source_bundle = implementation_bundle(RUNNER)
    run_id = f"upstream-l2-h0-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    cells = []
    for arm in ARMS:
        attempt_id = uuid.uuid4().hex[:12]
        cells.append(
            {
                "cell_id": f"l2-h0-r0-{arm}",
                "campaign_id": run_id,
                "history_index": 0,
                "history_id": context.context_id,
                "replicate_id": 0,
                "arm": arm,
                "attempt_id": attempt_id,
                "namespace": (
                    f"{PROFILE_ID}-{run_id}-{arm.casefold().replace('_', '-')}-"
                    f"{attempt_id}"
                ),
                "workload_manifest_sha256": workload.manifest_sha256,
                "dataset_authority_sha256": authority["authority_sha256"],
                "implementation_source_bundle_sha256": source_bundle["payload_sha256"],
                "platform_manifest_sha256": platform["payload_sha256"],
                "platform_manifest_path": str(platform_manifest),
                "expected_construction_artifacts": list(EXPECTED_ARTIFACTS),
            }
        )
    manifest = {
        "schema_version": "membind.upstream-l2-qualification-manifest.v1",
        "status": "SEALED",
        "scope": "FULL_HISTORY_0",
        "history_index": 0,
        "history_id": context.context_id,
        "arms": list(ARMS),
        "fixed_order": list(ARMS),
        "cell_count": 3,
        "cells": cells,
        "identity": {
            "source_bundle_sha256": source_bundle["payload_sha256"],
            "dataset_authority_sha256": authority["authority_sha256"],
            "workload_manifest_sha256": workload.manifest_sha256,
            "platform_manifest_sha256": platform["payload_sha256"],
            "platform_manifest_file_sha256": _file_sha256(platform_manifest),
        },
        "performance_use": "QUALIFICATION_ONLY_NOT_METHOD_SELECTION",
        "failure_policy": "NO_RESUME_QUALIFICATION_ATTEMPT",
        "created_unix": time.time(),
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if (
        manifest.get("status") != "SEALED"
        or manifest.get("scope") != "FULL_HISTORY_0"
        or manifest.get("history_index") != 0
        or tuple(manifest.get("arms", ())) != tuple(ARMS)
        or tuple(manifest.get("fixed_order", ())) != tuple(ARMS)
    ):
        raise RuntimeError("qualification manifest identity is invalid")
    cells = manifest.get("cells")
    if not isinstance(cells, list) or len(cells) != 3:
        raise RuntimeError("qualification manifest requires three cells")
    if [cell.get("arm") for cell in cells] != list(ARMS):
        raise RuntimeError("qualification cell order is invalid")
    for key in ("cell_id", "attempt_id", "namespace"):
        values = [cell.get(key) for cell in cells]
        if any(not isinstance(value, str) or not value for value in values):
            raise RuntimeError(f"qualification cell field missing: {key}")
        if len(set(values)) != len(values):
            raise RuntimeError(f"qualification duplicate {key}")
    identity = manifest.get("identity")
    if not isinstance(identity, Mapping):
        raise RuntimeError("qualification measured identity is missing")
    bindings = {
        "history_id": manifest.get("history_id"),
        "workload_manifest_sha256": identity.get("workload_manifest_sha256"),
        "dataset_authority_sha256": identity.get("dataset_authority_sha256"),
        "implementation_source_bundle_sha256": identity.get("source_bundle_sha256"),
        "platform_manifest_sha256": identity.get("platform_manifest_sha256"),
    }
    for key, expected in bindings.items():
        if {cell.get(key) for cell in cells} != {expected}:
            raise RuntimeError(f"qualification cell identity drift: {key}")
    supplied = manifest.get("manifest_sha256")
    if supplied is not None:
        payload = dict(manifest)
        payload.pop("manifest_sha256", None)
        if supplied != _canonical_sha256(payload):
            raise RuntimeError("qualification manifest checksum mismatch")


def _cell_result(root: Path, cell: dict[str, Any], returncode: int) -> dict[str, Any]:
    attempt = _attempt_path(root, cell)
    contract = _construction_contract(attempt, cell, returncode=returncode)
    if contract["construction_status"] != "PASS":
        return {**cell, **contract, "status": "FAIL"}
    verify_seal(attempt / "block")
    adapter = _read(attempt / "block/adapter_coverage.json")
    inventory = _read(attempt / "block/work_inventory.json")
    lifecycle = _read(attempt / "block/lifecycle_validation.json")
    order = _read(attempt / "block/order_validation.json")
    refinement = _read(attempt / "block/refinement_validation.json")
    expected = adapter.get("chunk_count")
    valid = (
        adapter.get("status") == "PASS"
        and adapter.get("adapter_version") == "MAB_ROLE_AWARE_LOSSLESS_8192_V1"
        and isinstance(expected, int)
        and expected > 0
        and inventory.get("expected_episode_count") == expected
        and inventory.get("submitted_count") == expected
        and inventory.get("completed_count") == expected
        and lifecycle.get("contract_status") == "PASS"
        and order.get("order_contract_status") in {"PASS", "NOT_REQUIRED"}
        and (
            cell["arm"] != ARMS[1]
            or refinement.get("refinement_status") == "PASS"
        )
    )
    return {
        **cell,
        **contract,
        "status": "PASS" if valid else "FAIL",
        "adapter_coverage": adapter,
        "work_inventory": inventory,
        "lifecycle_validation": lifecycle,
        "order_validation": order,
        "refinement_validation": refinement,
    }


def run(
    *,
    root: Path,
    platform_manifest: Path,
    compatibility_replay: Path,
    identity_output_root: Path,
) -> dict[str, Any]:
    root = root.resolve()
    platform_manifest = platform_manifest.resolve()
    manifest_path = root / "L2_QUALIFICATION_MANIFEST_SEAL.json"
    if manifest_path.exists():
        manifest = _read(manifest_path)
        validate_manifest(manifest)
    else:
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("qualification root is nonempty and unsealed")
        root.mkdir(parents=True, exist_ok=True)
        manifest = build_manifest(root, platform_manifest)
        _write_new(manifest_path, manifest)
    env = _formal_env()
    env["MEMBIND_EXPERIMENT_ROOT"] = str(root)
    results = []
    for raw_cell in manifest["cells"]:
        cell = dict(raw_cell)
        measured_env = _qualification_env(env, cell)
        attempt = _attempt_path(root, cell)
        complete = attempt / "complete.json"
        failure = attempt / "failure.json"
        cell_log = root / "logs" / f"{cell['cell_id']}.log"
        cell_pid = root / "pids" / f"{cell['cell_id']}.pid"
        command = [
            sys.executable,
            str(RUNNER),
            "--output-root",
            str(root),
            "--run-id",
            f"{cell['campaign_id']}-{cell['cell_id']}-{cell['attempt_id']}",
            "--attempt-id",
            cell["attempt_id"],
            "--namespace",
            cell["namespace"],
            "--context-index",
            "0",
            "--replicate-id",
            "0",
            "--method",
            cell["arm"],
            "--platform-manifest",
            cell["platform_manifest_path"],
        ]
        if not complete.exists() and not failure.exists():
            active = _active_exact_pids(root, cell, cell_pid)
            if active:
                while not complete.exists() and not failure.exists() and active:
                    time.sleep(30)
                    active = _active_exact_pids(root, cell, cell_pid)
            if not complete.exists() and not failure.exists():
                returncode = _run_process(
                    command,
                    env=measured_env,
                    log=cell_log,
                    pidfile=cell_pid,
                    heartbeat=root / "heartbeat.jsonl",
                    cell=cell,
                    attempt=attempt,
                )
            else:
                returncode = 0 if complete.exists() else 2
        else:
            returncode = 0 if complete.exists() and not failure.exists() else 2
        result = _cell_result(root, cell, returncode)
        results.append(result)
        if result["status"] != "PASS":
            failed = {
                "schema_version": "membind.upstream-l2-qualification-result.v1",
                "status": "FAIL",
                "history_index": 0,
                "arms": list(ARMS),
                "valid_cell_count": sum(row["status"] == "PASS" for row in results),
                "cells": results,
                "failed_arm": cell["arm"],
                "failure_policy": "NO_RESUME_QUALIFICATION_ATTEMPT",
                "ended_unix": time.time(),
            }
            _write_new(root / "L2_QUALIFICATION_RESULT.json", failed)
            return failed
    passed = {
        "schema_version": "membind.upstream-l2-qualification-result.v1",
        "status": "PASS",
        "history_index": 0,
        "arms": list(ARMS),
        "valid_cell_count": 3,
        "cells": results,
        "performance_use": "QUALIFICATION_ONLY_NOT_METHOD_SELECTION",
        "ended_unix": time.time(),
    }
    _write_new(root / "L2_QUALIFICATION_RESULT.json", passed)
    finalizer = SFWB / "scripts/finalize_upstream_qualification.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(finalizer),
            "--qualification-root",
            str(root),
            "--platform-manifest",
            str(platform_manifest),
            "--compatibility-replay",
            str(compatibility_replay.resolve()),
            "--output-root",
            str(identity_output_root.resolve()),
        ],
        cwd=ROOT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("qualification finalizer failed")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--platform-manifest", type=Path, required=True)
    parser.add_argument("--compatibility-replay", type=Path, required=True)
    parser.add_argument("--identity-output-root", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        root=args.root,
        platform_manifest=args.platform_manifest,
        compatibility_replay=args.compatibility_replay,
        identity_output_root=args.identity_output_root,
    )
    print(json.dumps({"status": result["status"], "valid_cell_count": result["valid_cell_count"]}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
