#!/usr/bin/env python3
"""Manifest, execution and reduction helpers for the formal 45-cell run.

The manifest is sealed before any provider call.  Execution is history-atomic
and replicate-counterbalanced; a failed attempt is never resumed.  This
module's reducer is intentionally conservative: no paired effect is emitted
until all 45 cells have a valid construction and a 60-row FULL QA seal.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
# Formal execution order is fixed by the latest preregistration: Native
# (serial/stateful) -> Ours (MemBind resource-credit scheduler) -> Async (relaxed-order
# ceiling).  This is intentionally *not* cyclic counterbalancing; the user
# requested one history at a time in this exact order for every replicate.
ARMS = (
    "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
    "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192",
    "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
)
OFFICIAL_HISTORY_COUNT = 5
REPLICATE_COUNT = 3
HISTORY_UNIT_COUNT = OFFICIAL_HISTORY_COUNT
NATIVE_ARM = "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192"
OURS_ARM = "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192"
ASYNC_ARM = "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _identity_hash(identity: Mapping[str, Any]) -> str:
    return _sha_bytes(_canonical(identity).encode())


def _load_frozen_inputs(
    frozen_root: Path,
    *,
    active_authority: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load only the independent post-L2 authority used by formal execution."""

    frozen_root = frozen_root.resolve()
    identity = _json(frozen_root / "ACTUAL_FORMAL_ARM_IDENTITY.json")
    adapter = _json(frozen_root / "MAB_SHARED_ADAPTER_IDENTITY.json")
    frozen = _json(frozen_root / "FINAL_METHOD_FROZEN.json")
    authority = _json(frozen_root / "dataset_authority.json")
    if authority != dict(active_authority):
        raise ValueError("frozen dataset authority does not match active official dataset")
    if identity.get("status") != "QUALIFIED":
        raise ValueError("formal implementation identity is not qualified")
    if adapter.get("status") != "FROZEN":
        raise ValueError("formal adapter identity is not frozen")
    platform = identity.get("platform_manifest")
    if (
        not isinstance(platform, Mapping)
        or not isinstance(platform.get("path"), str)
        or not platform.get("path")
        or not isinstance(platform.get("payload_sha256"), str)
        or not platform.get("payload_sha256")
    ):
        raise ValueError("qualified implementation has no pinned platform manifest")
    return identity, adapter, frozen, dict(platform)


def build_manifest(
    root: Path,
    *,
    implementation_identity: Mapping[str, Any],
    method_frozen: Mapping[str, Any],
    authority: Mapping[str, Any],
    platform_identity: Mapping[str, Any],
    adapter_identity: Mapping[str, Any],
    workload_manifest_sha256_by_history: Sequence[str],
) -> dict[str, Any]:
    """Build and validate five histories × three replicates × three arms."""

    context_ids = list(authority.get("context_ids", ()))
    if len(context_ids) != OFFICIAL_HISTORY_COUNT:
        raise ValueError("formal manifest requires five context ids")
    workload_hashes = list(workload_manifest_sha256_by_history)
    if len(workload_hashes) != OFFICIAL_HISTORY_COUNT or any(
        not isinstance(value, str) or len(value) != 64 for value in workload_hashes
    ):
        raise ValueError("formal manifest requires five MAB8192 workload hashes")
    implementation_sha256 = _identity_hash(implementation_identity)
    adapter_sha256 = _identity_hash(adapter_identity)
    source_identity = method_frozen.get("source_identity")
    if method_frozen.get("status") != "FINAL_METHOD_FROZEN":
        raise ValueError("method freeze is not final")
    if list(method_frozen.get("arms", ())) != list(ARMS):
        raise ValueError("frozen arm identities do not match formal harness")
    if not isinstance(source_identity, Mapping) or source_identity.get(
        "source_bundle_sha256"
    ) != implementation_identity.get("source_bundle_sha256"):
        raise ValueError("frozen and active source bundles do not match")
    frozen_bindings = {
        "implementation": method_frozen.get("implementation_identity_sha256"),
        "adapter": method_frozen.get("adapter_identity_sha256"),
        "dataset": method_frozen.get("dataset_authority_sha256"),
        "evaluator": method_frozen.get("evaluator_identity_sha256"),
    }
    active_bindings = {
        "implementation": implementation_sha256,
        "adapter": adapter_sha256,
        "dataset": authority.get("authority_sha256"),
        "evaluator": implementation_identity.get("evaluator_sha256"),
    }
    if frozen_bindings != active_bindings:
        raise ValueError("frozen and active measured identities do not match")
    frozen_platform = method_frozen.get("platform_manifest")
    if not isinstance(frozen_platform, Mapping):
        raise ValueError("frozen method has no authenticated platform identity")
    platform_payload_sha256 = platform_identity.get("payload_sha256")
    if (
        not isinstance(platform_payload_sha256, str)
        or not platform_payload_sha256
        or frozen_platform.get("payload_sha256") != platform_payload_sha256
        or frozen_platform.get("path") != platform_identity.get("path")
    ):
        raise ValueError("active and frozen platform identities do not match")
    profile_id = str(
        platform_identity.get(
            "profile_id",
            os.environ.get(
                "MEMBIND_PROFILE_ID", "local-qwen3-8b-awq-dualreplica-v1"
            ),
        )
    )
    deployment_policy_id = str(
        platform_identity.get(
            "deployment_policy_id",
            os.environ.get("MEMBIND_DEPLOYMENT_POLICY_ID", "P0_QWEN3_8B_AWQ"),
        )
    )
    run_id = f"formal-three-arm-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    cells: list[dict[str, Any]] = []
    for history_index in range(OFFICIAL_HISTORY_COUNT):
        for replicate in range(REPLICATE_COUNT):
            for arm in ARMS:
                cell_id = f"h{history_index}-r{replicate}-{arm}"
                attempt_id = uuid.uuid4().hex[:12]
                namespace = f"{profile_id}-{run_id}-h{history_index}-r{replicate}-{arm.casefold().replace('_', '-')}-{attempt_id}"
                cells.append({
                    "cell_id": cell_id,
                    "campaign_id": run_id,
                    "history_index": history_index,
                    "history_id": context_ids[history_index],
                    "replicate_id": replicate,
                    "arm": arm,
                    "attempt_id": attempt_id,
                    "namespace": namespace,
                    "within_replicate_order": list(ARMS),
                    "scope": "FORMAL",
                    "dataset_authority_sha256": authority.get("authority_sha256"),
                    "implementation_identity_sha256": implementation_sha256,
                    "implementation_source_bundle_sha256": implementation_identity.get("source_bundle_sha256"),
                    "method_frozen_seal_sha256": method_frozen.get("seal_sha256"),
                    "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
                    "profile_id": profile_id,
                    "deployment_policy_id": deployment_policy_id,
                    "evaluator_identity_sha256": implementation_identity.get("evaluator_sha256"),
                    "config_identity_sha256": implementation_identity.get("config_sha256"),
                    "adapter_identity_sha256": adapter_sha256,
                    "workload_manifest_sha256": workload_hashes[history_index],
                    "platform_manifest_sha256": platform_payload_sha256,
                    "platform_manifest_path": platform_identity.get("path"),
                    "cache_warmup_policy": "reset_then_identical_structured_warmup_v1",
                    "expected_construction_artifacts": ["attempt_preparation.json", "run_contract.json", "complete.json", "block/construction_seal.json", "block/metrics.json", "block/runtime_identity.json", "block/order_validation.json", "block/lifecycle_validation.json", "block/work_inventory.json", "block/graph_diagnostics.json", "block/resource_evidence.json", "route_events.jsonl", "route_runtime.json", "route_proof.json", "route_seal.json"],
                    "expected_full_qa": {"scope": "FULL", "question_count": 60, "qa_seal": "qa_seal.json"},
                })
    manifest = {
        "schema_version": "membind.formal-campaign-manifest-seal.v2",
        "status": "SEALED",
        "campaign_id": run_id,
        "scope": "FORMAL",
        "history_count": OFFICIAL_HISTORY_COUNT,
        "official_history_count": OFFICIAL_HISTORY_COUNT,
        "replicate_count": REPLICATE_COUNT,
        "arm_count": 3,
        "construction_cell_count": 45,
        "full_qa_cell_count": 45,
        "full_qa_question_count": 2700,
        "arms": list(ARMS),
        "counterbalance": {
            "type": "FIXED_WITHIN_HISTORY",
            "within_replicate_order": list(ARMS),
            "replicate_orders": {
                str(rep): list(ARMS) for rep in range(REPLICATE_COUNT)
            },
        },
        "history_order": list(range(OFFICIAL_HISTORY_COUNT)),
        "history_replicate_mapping": [
            {
                "history_index": history,
                "replicate_id": replicate,
                "history_id": context_ids[history],
            }
            for history in range(OFFICIAL_HISTORY_COUNT)
            for replicate in range(REPLICATE_COUNT)
        ],
        "cells": cells,
        "identity": {
            "implementation": implementation_sha256,
            "source_bundle": implementation_identity.get("source_bundle_sha256"),
            "method_frozen": method_frozen.get("seal_sha256"),
            "dataset": authority.get("authority_sha256"),
            "adapter": adapter_sha256,
            "evaluator": implementation_identity.get("evaluator_sha256"),
            "config": implementation_identity.get("config_sha256"),
            "platform": cells[0]["platform_manifest_sha256"],
        },
        "recovery_policy": "NO_RESUME_FORMAL_ATTEMPT",
        "created_at": time.time(),
    }
    validate_manifest(manifest)
    manifest["manifest_sha256"] = _sha_bytes(_canonical(manifest).encode())
    return manifest


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("status") != "SEALED" or manifest.get("scope") != "FORMAL":
        raise ValueError("manifest is not sealed formal scope")
    cells = manifest.get("cells")
    if not isinstance(cells, list) or len(cells) != 45:
        raise ValueError("manifest must contain 45 cells")
    for key in ("cell_id", "campaign_id", "history_id", "attempt_id", "namespace", "arm", "implementation_identity_sha256", "implementation_source_bundle_sha256", "method_frozen_seal_sha256", "dataset_authority_sha256", "evaluator_identity_sha256", "config_identity_sha256", "adapter_identity_sha256", "workload_manifest_sha256", "platform_manifest_sha256", "platform_manifest_path"):
        if any(not isinstance(cell.get(key), str) or not cell[key] for cell in cells):
            raise ValueError(f"manifest cell field missing: {key}")
    for key in ("cell_id", "attempt_id", "namespace"):
        values = [cell[key] for cell in cells]
        if len(set(values)) != len(values):
            raise ValueError(f"manifest duplicate {key}")
    expected = {
        (history, replicate, arm)
        for history in range(OFFICIAL_HISTORY_COUNT)
        for replicate in range(REPLICATE_COUNT)
        for arm in ARMS
    }
    observed = {(cell.get("history_index"), cell.get("replicate_id"), cell.get("arm")) for cell in cells}
    if observed != expected:
        raise ValueError("manifest cell coverage mismatch")
    for history in range(OFFICIAL_HISTORY_COUNT):
        for replicate in range(REPLICATE_COUNT):
            rows = [
                cell
                for cell in cells
                if cell["history_index"] == history
                and cell["replicate_id"] == replicate
            ]
            if [cell["arm"] for cell in rows] != list(ARMS):
                raise ValueError("manifest fixed order mismatch")
            if any(cell.get("history_id") != rows[0].get("history_id") for cell in rows):
                raise ValueError("manifest history identity mismatch")
    identity = manifest.get("identity")
    if not isinstance(identity, Mapping):
        raise ValueError("manifest measured identity is missing")
    bindings = {
        "implementation_identity_sha256": "implementation",
        "implementation_source_bundle_sha256": "source_bundle",
        "method_frozen_seal_sha256": "method_frozen",
        "dataset_authority_sha256": "dataset",
        "adapter_identity_sha256": "adapter",
        "evaluator_identity_sha256": "evaluator",
        "config_identity_sha256": "config",
        "platform_manifest_sha256": "platform",
    }
    for cell_key, identity_key in bindings.items():
        if {cell[cell_key] for cell in cells} != {identity.get(identity_key)}:
            raise ValueError(f"manifest cell identity drift: {cell_key}")
    supplied_sha256 = manifest.get("manifest_sha256")
    if supplied_sha256 is not None:
        payload = dict(manifest)
        payload.pop("manifest_sha256", None)
        if supplied_sha256 != _sha_bytes(_canonical(payload).encode()):
            raise ValueError("manifest checksum mismatch")


def _valid_construction(row: Mapping[str, Any]) -> bool:
    return (
        row.get("construction_status") == "PASS"
        and row.get("construction_complete_status") == "PASS"
        and row.get("construction_seal_status") == "CONSTRUCTION_SEALED"
        and row.get("construction_artifacts_complete") is True
    )


def _valid_full_qa(row: Mapping[str, Any]) -> bool:
    return (
        row.get("qa_status") == "PASS"
        and row.get("qa_seal_status") == "QA_SEALED"
        and row.get("qa_rows") == 60
        and row.get("qa_result_rows") == 60
    )


def _valid_cell(row: Mapping[str, Any]) -> bool:
    return _valid_construction(row) and _valid_full_qa(row)


def reduce_formal(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in rows]
    valid = [row for row in rows if _valid_cell(row)]
    if len(rows) != 45 or len(valid) != 45:
        return {
            "schema_version": "membind.formal-three-arm-reduction.v1",
            "status": "INCOMPLETE",
            "processed_cell_count": len(rows),
            "construction_cell_count": sum(_valid_construction(r) for r in rows),
            "qa_seal_count": sum(
                _valid_construction(r) and _valid_full_qa(r) for r in rows
            ),
            "selected_valid_cell_count": len(valid),
            "history_effects": [],
            "invalid_or_replacements": [r for r in rows if not _valid_cell(r)],
        }
    effects = []
    replicate_effects = []
    for history in range(OFFICIAL_HISTORY_COUNT):
        hrows = [r for r in valid if r.get("history_index") == history]
        by_rep: list[dict[str, Any]] = []
        for rep in range(REPLICATE_COUNT):
            pair = {
                r.get("arm"): r
                for r in hrows
                if r.get("replicate_id") == rep
            }
            native, ours = pair[NATIVE_ARM], pair[OURS_ARM]
            async_row = pair[ASYNC_ARM]
            effect = {"history_index": history, "replicate_id": rep, "a_t_build_ns": native.get("t_build_ns"), "c_t_build_ns": ours.get("t_build_ns"), "b_t_build_ns": async_row.get("t_build_ns"), "a_vs_c_ratio": (float(native["t_build_ns"]) / float(ours["t_build_ns"]) if float(ours.get("t_build_ns", 0)) else None)}
            by_rep.append(effect)
            replicate_effects.append(effect)
        ratios = [x["a_vs_c_ratio"] for x in by_rep if isinstance(x["a_vs_c_ratio"], (int, float))]
        effects.append({"history_index": history, "replicate_effects": by_rep, "a_vs_c_geometric_mean": math.exp(sum(math.log(v) for v in ratios) / len(ratios)) if ratios and all(v > 0 for v in ratios) else None})
    vals = [e["a_vs_c_geometric_mean"] for e in effects if isinstance(e["a_vs_c_geometric_mean"], (int, float)) and e["a_vs_c_geometric_mean"] > 0]
    return {
        "schema_version": "membind.formal-three-arm-reduction.v1",
        "status": "PASS",
        "processed_cell_count": 45,
        "construction_cell_count": 45,
        "qa_seal_count": 45,
        "selected_valid_cell_count": 45,
        "replicate_effects": replicate_effects,
        "history_effects": effects,
        "overall_geometric_mean_a_vs_c": (
            math.exp(sum(math.log(v) for v in vals) / len(vals))
            if len(vals) == 5
            else None
        ),
        "invalid_or_replacements": [],
    }


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frozen-root", type=Path, required=True)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    root = args.output_root.resolve()
    sys.path.insert(0, str(ROOT / "mab_quality_v2_final_qa/src"))
    from mab_quality_v2_final_qa.mab8192_adapter import MAB8192Manifest
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority

    full_authority = build_authority(
        ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json"
    )
    active_authority = {
        key: value for key, value in full_authority.items() if key != "contexts"
    }
    identity, adapter, frozen, platform = _load_frozen_inputs(
        args.frozen_root,
        active_authority=active_authority,
    )
    workload_hashes = [
        MAB8192Manifest.from_context(
            context, dataset_revision=str(active_authority["revision"])
        ).manifest_sha256
        for context in full_authority["contexts"]
    ]
    manifest = build_manifest(
        root,
        implementation_identity=identity,
        method_frozen=frozen,
        authority=active_authority,
        platform_identity=platform,
        adapter_identity=adapter,
        workload_manifest_sha256_by_history=workload_hashes,
    )
    _write(root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json", manifest)
    if args.manifest_only:
        print(json.dumps({"status": "SEALED", "manifest_sha256": manifest["manifest_sha256"], "cells": 45}, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
