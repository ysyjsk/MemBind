#!/usr/bin/env python3
"""Reduce sealed formal cells into the required construction/quality reports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


ARMS = (
    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
    "MEMBIND_V6_1_SHARED_BOUNDED_SO",
    "RELAXED_ORDER_SHARED_BOUNDED_SO",
)
NATIVE_ARM = "GRAPHITI_SERIAL_SHARED_BOUNDED_SO"
OURS_ARM = "MEMBIND_V6_1_SHARED_BOUNDED_SO"
ASYNC_ARM = "RELAXED_ORDER_SHARED_BOUNDED_SO"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cell_rows(root: Path) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _jsonl(root / "formal_ledger.jsonl"):
        if row.get("event") != "CELL_COMPLETE":
            continue
        cid = str(row.get("cell_id"))
        if cid:
            latest[cid] = row
    return [latest[key] for key in sorted(latest)]


def _construction_metrics(row: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(row.get("construction_root", "")))
    block = path / "block"
    metrics = _json(block / "metrics.json")
    inventory = _json(block / "work_inventory.json")
    order = _json(block / "order_validation.json")
    lifecycle = _json(block / "lifecycle_validation.json")
    return {
        "cell_id": row.get("cell_id"),
        "history_index": row.get("history_index"),
        "history_id": row.get("history_id"),
        "replicate_id": row.get("replicate_id"),
        "arm": row.get("arm"),
        "attempt_id": row.get("actual_attempt_id", row.get("attempt_id")),
        "namespace": row.get("actual_namespace", row.get("namespace")),
        "construction_status": row.get("construction_status"),
        "qa_status": row.get("qa_status"),
        "qa_rows": row.get("qa_rows", 0),
        "t_build_ns": metrics.get("t_build_ns"),
        "durable_goodput": metrics.get("durable_goodput"),
        "expected_episode_count": inventory.get("expected_episode_count"),
        "submitted_count": inventory.get("submitted_count"),
        "completed_count": inventory.get("completed_count"),
        "llm_logical_requests": inventory.get("llm_logical_requests"),
        "transport_attempts": inventory.get("transport_attempts"),
        "transport_failures": inventory.get("transport_failed_attempts"),
        "transport_retries": inventory.get("transport_retry_attempts"),
        "prompt_tokens": inventory.get("prompt_tokens"),
        "completion_tokens": inventory.get("completion_tokens"),
        "embedding_items": inventory.get("embedding_items"),
        "db_writes": inventory.get("db_writes"),
        "order_contract_status": order.get("order_contract_status"),
        "order_violation_count": order.get("order_violation_count"),
        "lifecycle_contract_status": lifecycle.get("contract_status"),
        "construction_root": str(path),
    }


def _quality_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("construction_status") != "PASS":
            continue
        qa_path = Path(str(row.get("construction_root"))) / "block" / "qa_full" / "qa_results.jsonl"
        for item in _jsonl(qa_path):
            result.append({
                "history_index": row.get("history_index"), "history_id": row.get("history_id"),
                "replicate_id": row.get("replicate_id"), "arm": row.get("arm"),
                "cell_id": row.get("cell_id"), "attempt_id": row.get("actual_attempt_id", row.get("attempt_id")),
                "namespace": row.get("actual_namespace", row.get("namespace")),
                "qa_pair_id": item.get("qa_pair_id"), "question_id": item.get("question_id"),
                "question_type": item.get("question_type"), "status": item.get("status"),
                "judge_valid": item.get("judge_valid"), "correct": item.get("correct"),
                "failure_class": item.get("failure_class"),
                "retrieval_metrics": item.get("retrieval_metrics", {}),
            })
    return result


def _geo(values: list[float]) -> float | None:
    return math.exp(sum(math.log(value) for value in values) / len(values)) if values and all(value > 0 for value in values) else None


def finalize(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = _json(root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json")
    rows = [_construction_metrics(row) for row in _cell_rows(root)]
    valid = [row for row in rows if row.get("construction_status") == "PASS" and row.get("qa_status") == "PASS" and row.get("qa_rows") == 60]
    quality = _quality_rows(rows)
    construction_path = root / "FORMAL_CONSTRUCTION_TABLE.json"
    construction_path.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if rows:
        with (root / "FORMAL_CONSTRUCTION_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (root / "FORMAL_QUALITY_TABLE.json").write_text(json.dumps(quality, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if quality:
        with (root / "FORMAL_QUALITY_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(quality[0])); writer.writeheader(); writer.writerows(quality)
    replicate_effects: list[dict[str, Any]] = []
    history_effects: list[dict[str, Any]] = []
    for history in range(5):
        hrows = [row for row in valid if row.get("history_index") == history]
        ratios: list[float] = []
        for replicate in range(3):
            pair = {row.get("arm"): row for row in hrows if row.get("replicate_id") == replicate}
            if set(pair) != set(ARMS) or not all(isinstance(pair[a].get("t_build_ns"), (int, float)) for a in ARMS):
                continue
            ratio = float(pair[NATIVE_ARM]["t_build_ns"]) / float(pair[OURS_ARM]["t_build_ns"])
            ratios.append(ratio)
            replicate_effects.append({"history_index": history, "replicate_id": replicate, "a_t_build_ns": pair[NATIVE_ARM]["t_build_ns"], "c_t_build_ns": pair[OURS_ARM]["t_build_ns"], "a_vs_c_ratio": ratio, "b_t_build_ns": pair[ASYNC_ARM]["t_build_ns"]})
        history_effects.append({"history_index": history, "history_id": (hrows[0].get("history_id") if hrows else None), "replicate_count": len(ratios), "a_vs_c_geometric_mean": _geo(ratios)})
    (root / "PER_REPLICATE_PAIRED_EFFECTS.json").write_text(json.dumps(replicate_effects, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "PER_HISTORY_PAIRED_EFFECTS.json").write_text(json.dumps(history_effects, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if replicate_effects:
        with (root / "PER_HISTORY_PAIRED_EFFECTS.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(replicate_effects[0])); writer.writeheader(); writer.writerows(replicate_effects)
    overall = _geo([float(e["a_vs_c_geometric_mean"]) for e in history_effects if isinstance(e.get("a_vs_c_geometric_mean"), (int, float))]) if len(history_effects) == 5 and all(isinstance(e.get("a_vs_c_geometric_mean"), (int, float)) for e in history_effects) else None
    quality_invalid = sum(1 for row in quality if row.get("judge_valid") is not True)
    status = "EXPERIMENT_COMPLETE" if len(valid) == 45 and len(quality) == 2700 else "INCOMPLETE"
    summary = {
        "schema_version": "membind.final-three-arm-experiment-result.v1", "status": status,
        "campaign_id": manifest.get("campaign_id"), "construction_cells": len(rows), "valid_construction_cells": len(valid),
        "full_qa_seals": sum(1 for row in rows if row.get("qa_status") == "PASS" and row.get("qa_rows") == 60), "quality_rows": len(quality),
        "quality_invalid_rows": quality_invalid, "per_replicate_effect_count": len(replicate_effects), "per_history_effect_count": sum(e.get("a_vs_c_geometric_mean") is not None for e in history_effects),
        "overall_geometric_mean_a_vs_c": overall, "invalid_attempt_ledger": str((root / "INVALID_ATTEMPT_LEDGER.json").resolve()),
    }
    (root / "FINAL_THREE_ARM_EXPERIMENT_RESULT.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    mechanism = {"schema_version": "membind.mechanism-critical-path-report.v1", "status": status, "arms": {arm: {"cell_count": sum(row.get("arm") == arm for row in rows), "valid_count": sum(row.get("arm") == arm and row.get("construction_status") == "PASS" for row in rows), "total_prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in rows if row.get("arm") == arm), "total_transport_attempts": sum(int(row.get("transport_attempts") or 0) for row in rows if row.get("arm") == arm)} for arm in ARMS}, "future_interference": "reported from per-cell route/runtime artifacts", "resource_fairness": "reported from per-cell route seals"}
    (root / "MECHANISM_AND_CRITICAL_PATH_REPORT.json").write_text(json.dumps(mechanism, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "CACHE_RESOURCE_FAIRNESS_REPORT.json").write_text(json.dumps({"schema_version": "membind.cache-resource-fairness-report.v1", "status": status, "policy": "same arm-agnostic shared dual-replica pool", "cells": len(rows)}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    analysis = {"schema_version": "membind.statistical-analysis.v1", "status": status, "estimand": "same-history same-replicate paired A_vs_C T_build ratio", "cluster_unit": "history", "top_level_histories": 5, "replicate_effects": replicate_effects, "history_effects": history_effects, "overall_geometric_mean": overall, "uncertainty_note": "descriptive cluster-aware uncertainty only; five top-level histories are not IID asymptotic samples"}
    (root / "STATISTICAL_ANALYSIS.json").write_text(json.dumps(analysis, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    claim = {"schema_version": "membind.claim-support-matrix.v1", "status": status, "layers": {"layer_1_necessary_validity": {"construction_and_qa_coverage": "SUPPORTED" if status == "EXPERIMENT_COMPLETE" else "NOT_EVALUABLE", "native_and_resource_identity": "SEE_SEALED_CELL_ARTIFACTS", "quality_pipeline": "SUPPORTED" if len(quality) == 2700 else "NOT_EVALUABLE"}, "layer_2_mechanism": {"bounded_future_admission": "SEE_ROUTE_AND_SCHEDULER_ARTIFACTS", "critical_path_and_idle_opportunity": "SEE_MECHANISM_REPORT", "logical_work_contract": "SEE_PER_ARM_WORK_INVENTORIES"}, "layer_3_outcome": {"a_vs_c_t_build": "SUPPORTED" if overall is not None else "NOT_EVALUABLE", "quality_delta": "COMPUTE_FROM_FORMAL_QUALITY_TABLE", "b_ceiling": "REPORTED_SEPARATELY"}}, "cross_hardware_generality": "NOT_EVALUATED", "question_38": "retained exactly as published and disclosed in official parity artifact"}
    (root / "CLAIM_SUPPORT_MATRIX.json").write_text(json.dumps(claim, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "FINAL_THREE_ARM_EXPERIMENT_REPORT.md").write_text(f"# Final Three-Arm Experiment Report\n\nStatus: `{status}`.\n\nConstruction cells: `{len(valid)}/45`; FULL QA seals: `{summary['full_qa_seals']}/45`; quality rows: `{len(quality)}/2700`.\n\nA/C overall geometric mean (descriptive): `{overall}`. B is reported as a relaxed-order ceiling. Question 38 is retained and disclosed; no PAPER_READY claim is inferred.\n", encoding="utf-8")
    (root / "REPRODUCTION_COMMANDS.md").write_text("# Reproduction Commands\n\nSource the isolated 8B profile, verify `FORMAL_CAMPAIGN_MANIFEST_SEAL.json`, run `run_formal_three_arm.py` with the frozen root, then run `finalize_formal_three_arm.py`. Failed attempts use `NO_RESUME_FORMAL_ATTEMPT` and are never resumed.\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--root", type=Path, required=True); args = parser.parse_args(); result = finalize(args.root); print(json.dumps(result, sort_keys=True)); return 0 if result["status"] == "EXPERIMENT_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
