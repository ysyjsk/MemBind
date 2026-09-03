#!/usr/bin/env python3
"""Authenticate the shared-substrate engineering canary and freeze the method.

This is deliberately provider-free.  It consumes one explicit canary root,
recomputes every referenced seal hash, and only then promotes the pre-canary
method to ``FINAL_METHOD_FROZEN``.  No timing or quality value is used for
selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from current_platform_identity import load_current_platform_identity


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"
METHODS = (
    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
    "MEMBIND_V6_1_SHARED_BOUNDED_SO",
    "RELAXED_ORDER_SHARED_BOUNDED_SO",
)
V61_METHOD = "MEMBIND_V6_1_SHARED_BOUNDED_SO"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    platform = load_current_platform_identity()
    manifest_paths = sorted(root.glob("campaign_manifest.*.json"))
    if len(manifest_paths) != 1:
        raise RuntimeError("CANARY_MANIFEST_AMBIGUOUS_OR_MISSING")
    campaign = _json(manifest_paths[0])
    if campaign.get("contexts") != [0] or campaign.get("session_limit") != 2:
        raise RuntimeError("CANARY_SCOPE_NOT_FIXED_TWO_SOURCE")
    if tuple(campaign.get("methods", ())) != METHODS:
        raise RuntimeError("CANARY_METHOD_SET_MISMATCH")
    if campaign.get("v61_method_boundary") != "MEMBIND_CORE":
        raise RuntimeError("CANARY_V61_BOUNDARY_MISMATCH")
    policy = campaign.get("v6_1_policy") or {}
    if policy.get("method_identity") != "MEMBIND_RESOURCE_CREDIT_V1":
        raise RuntimeError("CANARY_RESOURCE_CREDIT_POLICY_MISMATCH")

    rows: list[dict[str, Any]] = []
    shared_contract_hashes: dict[str, str] = {}
    shared_contracts: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        parent = root / "context-0" / method
        attempts = sorted(p for p in parent.iterdir() if p.is_dir()) if parent.is_dir() else []
        if len(attempts) != 1:
            raise RuntimeError(f"CANARY_ATTEMPT_COUNT_{method}")
        attempt = attempts[0]
        block = attempt / "block"
        complete = _json(attempt / "complete.json")
        seal = _json(block / "construction_seal.json")
        route = _json(attempt / "route_proof.json")
        route_seal = _json(attempt / "route_seal.json")
        runtime = _json(attempt / "route_runtime.json")
        run_contract = _json(attempt / "run_contract.json")
        life = _json(block / "lifecycle_validation.json")
        order = _json(block / "order_validation.json")
        inventory = _json(block / "work_inventory.json")
        issues: list[str] = []
        if complete.get("status") != "PASS": issues.append("complete")
        if seal.get("status") != "CONSTRUCTION_SEALED": issues.append("construction_seal")
        if life.get("contract_status") != "PASS" or life.get("completed_count") != life.get("expected_count"): issues.append("lifecycle")
        if order.get("order_contract_status") not in {"PASS", "NOT_REQUIRED"}: issues.append("order")
        if route.get("status") != "PASS" or route.get("all_transports_routed") is not True: issues.append("route")
        if route_seal.get("status") != "ROUTE_SEALED": issues.append("route_seal")
        if runtime.get("balanced") is not True or any(runtime.get("outstanding", {}).values()): issues.append("resource_balance")
        if inventory.get("submitted_count") != inventory.get("expected_episode_count") or inventory.get("completed_count") != inventory.get("expected_episode_count"): issues.append("work_coverage")
        if inventory.get("transport_failed_attempts", 0) != 0 or inventory.get("transport_retry_attempts", 0) != 0 or inventory.get("transport_true_retry_attempts", 0) != 0: issues.append("transport_failure_or_retry")
        run_platform = run_contract.get("platform_manifest", {})
        if (
            run_platform.get("path") != platform["path"]
            or run_platform.get("payload_sha256") != platform["payload_sha256"]
            or run_platform.get("file_sha256") != platform["file_sha256"]
        ):
            issues.append("platform_identity")
        # Policy identity belongs to the MemBind arm explicitly; do not infer
        # it from tuple position because formal execution order is
        # Native -> Ours -> Async and Async is the final element.
        if method == V61_METHOD:
            identity = seal.get("identity", {})
            if identity.get("policy") != policy: issues.append("policy_identity")
            if inventory.get("expected_transport_attempts_from_provider") != inventory.get("instrumented_transport_attempts"): issues.append("transport_accounting")
        for rel, expected in route_seal.get("members", {}).items():
            path = attempt / rel
            if not path.is_file() or _sha(path) != expected:
                issues.append(f"seal_hash:{rel}")
        # The construction identity is the authoritative source for method
        # and namespace.  No arm-specific adapter branch is accepted here.
        identity = seal.get("identity", {})
        for key in ("context_id", "namespace", "run_id", "method", "workload_hash"):
            if not identity.get(key): issues.append(f"identity:{key}")
        adapter_payload = run_contract.get("decoding_contract", {}).get(
            "shared_structured_output"
        )
        if not isinstance(adapter_payload, dict):
            issues.append("shared_adapter_identity")
            adapter_payload = {}
        elif (
            adapter_payload.get("arm_identity") is not None
            or adapter_payload.get("wire_max_tokens") != 16384
            or adapter_payload.get("termination_policy")
            != "declared_pair_task_completion"
            or adapter_payload.get("terminal_confirmation_policy")
            != "prohibited_terminal_only_success_v1"
            or adapter_payload.get("terminal_confirmation_is_context_retry") is not False
            or adapter_payload.get("terminal_only_success_allowed") is not False
            or adapter_payload.get("edge_task_protocol") != "finite_pair_task_v1"
            or adapter_payload.get("max_pages_semantics") != "finite_task_count"
            or adapter_payload.get("total_page_cap") != 1
        ):
            issues.append("shared_adapter_contract")
        shared_contracts[method] = adapter_payload
        shared_contract_hashes[method] = hashlib.sha256(_canonical(adapter_payload).encode()).hexdigest()
        rows.append({
            "method": method,
            "attempt_id": attempt.name,
            "namespace": identity.get("namespace"),
            "status": "PASS" if not issues else "FAIL",
            "issues": issues,
            "submitted_count": inventory.get("submitted_count"),
            "expected_episode_count": inventory.get("expected_episode_count"),
            "transport_attempts": inventory.get("transport_attempts"),
            "route_endpoint_counts": route.get("endpoint_counts"),
            "order_contract_status": order.get("order_contract_status"),
            "route_seal_sha256": route_seal.get("seal_sha256"),
        })
    if len(set(shared_contract_hashes.values())) != 1:
        raise RuntimeError("SHARED_ADAPTER_IDENTITY_MISMATCH")
    status = "PASS" if all(row["status"] == "PASS" for row in rows) else "FAIL"
    return {
        "schema_version": "membind.engineering-canary-validation.v1",
        "status": status,
        "canary_root": str(root),
        "run_id": campaign.get("run_id"),
        "head_commit": _git_head(),
        "methods": list(METHODS),
        "scope": {"history_indices": [0], "source_limit": 2, "selection_use": "AUDIT_ONLY_NOT_FOR_METHOD_SELECTION"},
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
        "resource_credit_policy": policy,
        "shared_adapter_identity_sha256": next(iter(shared_contract_hashes.values())),
        "shared_adapter_contract": next(iter(shared_contracts.values())),
        "platform_manifest": platform,
        "attempts": rows,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def freeze(root: Path) -> dict[str, Any]:
    validation = validate(root)
    if validation["status"] != "PASS":
        raise RuntimeError("CANARY_VALIDATION_FAILED")
    identity = _json(EVIDENCE / "EVALUATED_IMPLEMENTATION_IDENTITY.json")
    platform = load_current_platform_identity()
    method_spec = _json(EVIDENCE / "FINAL_METHOD_SPEC.json")
    method_spec["status"] = "FINAL_CANARY_AUTHENTICATED"
    method_spec["canary_validation_sha256"] = hashlib.sha256(_canonical(validation).encode()).hexdigest()
    method_spec["source_identity"] = {
        "head_commit": identity.get("head_commit"),
        "source_bundle_sha256": identity.get("source_bundle_sha256"),
        "tracked_diff_sha256": identity.get("tracked_diff_sha256"),
    }
    (EVIDENCE / "FINAL_METHOD_SPEC.json").write_text(json.dumps(method_spec, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    frozen = {
        "schema_version": "membind.final-method-frozen.v1",
        "status": "FINAL_METHOD_FROZEN",
        "method_identity": "MEMBIND_RESOURCE_CREDIT_V1",
        "policy": validation.get("resource_credit_policy", {}),
        "arms": {
            "A": "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
            "B": "MEMBIND_V6_1_SHARED_BOUNDED_SO",
            "C": "RELAXED_ORDER_SHARED_BOUNDED_SO",
        },
        "shared_structured_output_substrate": {
            "identity_sha256": validation["shared_adapter_identity_sha256"],
            "backend": "xgrammar",
            "model": "qwen3-8b-awq",
            "max_tokens": 16384,
            "terminal_confirmation": "prohibited_terminal_only_success_v1",
            "terminal_only_success_allowed": False,
            "edge_task_protocol": "finite_pair_task_v1",
            "arm_branching": False,
            "strict_upstream_characterization": "A0_STRICT_UPSTREAM_COMPATIBILITY_CHARACTERIZATION",
        },
        "implementation_identity_sha256": hashlib.sha256(_canonical(identity).encode()).hexdigest(),
        "source_bundle_sha256": identity.get("source_bundle_sha256"),
        "platform_manifest": {
            "path": platform["path"],
            "file_sha256": platform["file_sha256"],
            "payload_sha256": platform["payload_sha256"],
        },
        "canary": validation,
        "formal_status": "READY_FOR_45_CELL_MANIFEST",
        "recovery_policy": "NO_RESUME_FORMAL_ATTEMPT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    frozen["seal_sha256"] = hashlib.sha256(_canonical(frozen).encode()).hexdigest()
    (EVIDENCE / "FINAL_METHOD_FROZEN.json").write_text(json.dumps(frozen, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "ENGINEERING_CANARY_VALIDATION.json").write_text(json.dumps(validation, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "ENGINEERING_CANARY_VALIDATION.md").write_text("# Engineering Canary Validation\n\nStatus: `PASS`. Three shared-substrate arms passed construction, lifecycle, route, coverage, balance, and seal checks. Canary performance and quality are audit-only.\n", encoding="utf-8")
    return frozen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canary-root", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args.canary_root)
    print(json.dumps({"status": result["status"], "seal_sha256": result["seal_sha256"], "formal_status": result["formal_status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
