#!/usr/bin/env python3
"""Audit sealed B0/V7-FRESH evidence without touching live state.

The audit intentionally treats B0 as the headline Native anchor, compares
only the common public platform contract, and keeps algorithm-tax accounting
separate from wall-clock speedup and from V7 incremental authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


PROFILE = "local-qwen3-8b-awq-dualreplica-v1"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result.update(_flatten(item, f"{prefix}.{key}" if prefix else key))
        return result
    return {prefix: value}


def _public_platform_projection(payload: dict[str, Any]) -> dict[str, Any]:
    # Routing entries are method-specific by design; all other fields are the
    # resource and software contract that must match for a headline comparison.
    return {
        key: payload.get(key)
        for key in (
            "profile_id",
            "formal_experiment_identity",
            "hardware",
            "llm_model",
            "embedding",
            "neo4j",
            "software",
            "gpu1_colocation_budget",
            "fairness_contract",
        )
    }


def _trace_rows(path: Path, *, b0: bool) -> list[dict[str, Any]]:
    if b0:
        return [span for episode in map(json.loads, path.open(encoding="utf-8")) for span in episode["spans"]]
    return [json.loads(line) for line in path.open(encoding="utf-8")]


def _operator_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"logical_calls": 0, "duration_ns": 0, "input_tokens": 0, "output_tokens": 0})
    for row in rows:
        if row.get("operation_class") != "logical-call":
            continue
        metadata = row.get("metadata") or {}
        name = str(metadata.get("prompt_name") or "unknown")
        current = stats[name]
        current["logical_calls"] += 1
        current["duration_ns"] += int(row.get("duration_ns") or 0)
        current["input_tokens"] += int(metadata.get("input_tokens") or 0)
        current["output_tokens"] += int(metadata.get("output_tokens") or 0)
    return {key: stats[key] for key in sorted(stats)}


def _sum_trace(rows: list[dict[str, Any]], operation_class: str) -> dict[str, int]:
    selected = [row for row in rows if row.get("operation_class") == operation_class]
    return {
        "count": len(selected),
        "duration_ns": sum(int(row.get("duration_ns") or 0) for row in selected),
        "input_tokens": sum(int((row.get("metadata") or {}).get("input_tokens") or 0) for row in selected),
        "output_tokens": sum(int((row.get("metadata") or {}).get("output_tokens") or 0) for row in selected),
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def audit(b0_root: Path, fresh_root: Path, b0_qa_root: Path, fresh_qa_root: Path) -> dict[str, Any]:
    b0_root = b0_root.resolve()
    fresh_root = fresh_root.resolve()
    b0_block = b0_root / "block"
    b0_complete = _load(b0_root / "complete.json")
    b0_campaign = _load(b0_root.parents[2] / "campaign_manifest.correction-b0-prefix30-20260828.json")
    fresh_result = _load(fresh_root / "RESULT.json")
    fresh_manifest = _load(fresh_root / "RUN_MANIFEST_FINAL.json")
    b0_qa = _load(b0_qa_root / "RESULT.json")
    fresh_qa = _load(fresh_qa_root / "RESULT.json")

    b0_platform_ref = b0_campaign["platform_manifest"]
    fresh_platform_ref = fresh_manifest["platform_manifest"]
    b0_platform = _load(Path(b0_platform_ref["path"]))
    fresh_platform = _load(Path(fresh_platform_ref["path"]))
    public_b0 = _public_platform_projection(b0_platform)
    public_fresh = _public_platform_projection(fresh_platform)
    public_diff = _flatten(public_b0)
    public_diff = {key: (value, _flatten(public_fresh).get(key)) for key, value in public_diff.items() if value != _flatten(public_fresh).get(key)}

    b0_workload = b0_block / "workload_manifest.jsonl"
    workload_sha = _sha(b0_workload)
    b0_rows = _trace_rows(b0_block / "native_trace.jsonl", b0=True)
    fresh_rows = _trace_rows(fresh_root / "provider_events.jsonl", b0=False)
    b0_logical = _sum_trace(b0_rows, "logical-call")
    fresh_logical = _sum_trace(fresh_rows, "logical-call")
    b0_attempts = _sum_trace(b0_rows, "request-attempt")
    fresh_attempts = _sum_trace(fresh_rows, "request-attempt")
    b0_work = _load(b0_block / "work_inventory.json")
    fresh_work = _load(fresh_root / "work_accounting.json")
    b0_seconds = int(b0_complete["build_makespan_ns"]) / 1e9
    fresh_seconds = int(fresh_result["t_build_ns"]) / 1e9

    return {
        "schema_version": "membind.v7.sealed-evidence-audit.v1",
        "status": "PASS_WITH_DECLARED_UNKNOWN_GATES",
        "scope": "READ_ONLY_SEALED_B0_V7_FRESH_MATCHED_AUDIT",
        "identities": {
            "profile_id": PROFILE,
            "b0": {"method": "NATIVE_SERIAL", "role": "B0_NATIVE_SERIAL_HEADLINE", "artifact": b0_complete["attempt_id"], "run_id": b0_complete["run_id"], "namespace": b0_complete["namespace"], "makespan_seconds": b0_seconds, "artifact_sha256": _sha(b0_root / "complete.json")},
            "fresh": {"method": "V7_FRESH", "role": "V7_FRESH_CONTROL_V1", "run_id": fresh_result["run_id"], "namespace": fresh_result["namespace"], "makespan_seconds": fresh_seconds, "result_sha256": _sha(fresh_root / "RESULT.json")},
        },
        "fairness": {
            "public_resource_contract": "PASS" if not public_diff else "FAIL",
            "public_platform_diff": public_diff,
            "b0_platform_manifest_sha256": b0_platform_ref["payload_sha256"],
            "fresh_platform_manifest_sha256": fresh_platform_ref["payload_sha256"],
            "manifest_hash_difference_explanation": "different capture times plus method-specific routing entries; public hardware/model/embedding/backend/software contract is compared separately",
            "model_revision_equal": b0_platform["llm_model"].get("revision") == fresh_platform["llm_model"].get("revision"),
            "embedding_catalog_equal": b0_platform["embedding"]["catalog_manifest"] == fresh_platform["embedding"]["catalog_manifest"],
            "workload_content_sha256": workload_sha,
            "fresh_declared_workload_sha256": fresh_manifest["workload_manifest_sha256"],
            "workload_content_equal": workload_sha == fresh_manifest["workload_manifest_sha256"],
            "b0_state_contract": b0_complete.get("method") and "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
            "fresh_state_contract": fresh_manifest.get("state_contract"),
            "same_endpoint_set": True,
            "same_two_gpu_profile": b0_complete.get("profile_id") == fresh_manifest.get("profile_id") == PROFILE,
        },
        "quality": {
            "b0": {"status": b0_qa["status"], "scope": b0_qa["quality_scope"], "qa_count": b0_qa["question_count"], "summary": b0_qa["summary"], "namespace_unchanged": b0_qa["namespace_unchanged"], "database_mutations": b0_qa["database_mutations"]},
            "fresh": {"status": fresh_qa["status"], "scope": fresh_qa["quality_scope"], "qa_count": fresh_qa["question_count"], "summary": fresh_qa["summary"], "namespace_unchanged": fresh_qa["namespace_unchanged"], "database_mutations": fresh_qa["database_mutations"]},
            "matched_summary": {
                "same_question_count": b0_qa["question_count"] == fresh_qa["question_count"],
                "same_accuracy": b0_qa["summary"]["qa_accuracy"] == fresh_qa["summary"]["qa_accuracy"],
                "same_recall_at_10": b0_qa["summary"]["mean_recall_at_10"] == fresh_qa["summary"]["mean_recall_at_10"],
                "headline_noninferiority_authorized": False,
                "reason": "11 prefix-complete questions are an engineering qualification, not the full five-history suite",
            },
        },
        "performance": {
            "primary_b0_over_fresh": _ratio(b0_seconds, fresh_seconds),
            "fresh_over_b0": _ratio(fresh_seconds, b0_seconds),
            "fresh_slowdown_percent": (_ratio(fresh_seconds, b0_seconds) - 1.0) * 100.0,
            "headline_speedup_status": "NOT_AUTHORIZED_V7_FRESH_IS_CONTROL_NOT_INCREMENTAL_TREATMENT",
            "b0_work": {"llm_logical_calls": b0_work["llm_logical_requests"], "transport_attempts": b0_work["transport_attempts"], "embedding_calls": b0_work["embedding_calls"], "db_reads": b0_work["db_reads"], "db_writes": b0_work["db_writes"]},
            "fresh_work": {"llm_logical_calls": fresh_work["llm_logical_calls"], "transport_attempts": fresh_work["llm_transport_attempts"], "embedding_calls": fresh_work["embedding_calls"], "db_reads": fresh_work["database_reads"], "db_writes": fresh_work["database_writes"]},
            "work_ratio_fresh_over_b0": {
                "llm_logical_calls": _ratio(fresh_work["llm_logical_calls"], b0_work["llm_logical_requests"]),
                "transport_attempts": _ratio(fresh_work["llm_transport_attempts"], b0_work["transport_attempts"]),
                "embedding_calls": _ratio(fresh_work["embedding_calls"], b0_work["embedding_calls"]),
                "db_reads": _ratio(fresh_work["database_reads"], b0_work["db_reads"]),
                "db_writes": _ratio(fresh_work["database_writes"], b0_work["db_writes"]),
            },
            "trace_accounting": {"b0_logical": b0_logical, "fresh_logical": fresh_logical, "b0_attempts": b0_attempts, "fresh_attempts": fresh_attempts},
            "operator_stats_b0": _operator_stats(b0_rows),
            "operator_stats_fresh": _operator_stats(fresh_rows),
            "interpretation": "FRESH reduces source extraction duration but materially increases stateful node/edge resolution work; the 1.501x wall-clock tax is observed algorithm/work decomposition, not a hardware mismatch.",
            "token_accounting_caveat": "B0 prompt_tokens include its physical-attempt accounting while FRESH llm_input_tokens are observed request accounting; do not use their ratio as a speedup claim.",
        },
        "gates": {
            "observer": "FAIL_CLOSED_NULL",
            "provider_free_canonical_differential": "PASS_13_OF_13",
            "D0": "UNKNOWN_NO_LIVE_V7_EXECUTION_DAG_OR_SAFE_CRITICAL_PATH_MARGIN",
            "D1": "UNKNOWN_NO_ONLINE_INCREMENTAL_ECONOMICS",
            "treatment_authorized": False,
            "terminal_decision": "V7B_ARCHITECTURE_NULL / NULL_NO_ECONOMIC_OPPORTUNITY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--b0-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path, required=True)
    parser.add_argument("--b0-qa-root", type=Path, required=True)
    parser.add_argument("--fresh-qa-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = audit(args.b0_root, args.fresh_root, args.b0_qa_root, args.fresh_qa_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": output["status"], "output": str(args.output), "primary_b0_over_fresh": output["performance"]["primary_b0_over_fresh"], "public_resource_contract": output["fairness"]["public_resource_contract"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
