#!/usr/bin/env python3
"""Validate and reduce the sealed upstream-only 45-cell formal campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", SFWB / "scripts", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from formal_three_arm_harness import (  # noqa: E402
    ARMS,
    ASYNC_ARM,
    NATIVE_ARM,
    OFFICIAL_HISTORY_COUNT,
    OURS_ARM,
    REPLICATE_COUNT,
    validate_manifest,
)
from run_formal_three_arm import _construction_contract, _qa_contract  # noqa: E402
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    strict_formal_runtime_identity_errors,
)


ARM_ORDER = {arm: index for index, arm in enumerate(ARMS)}
QUESTION_38_ID = "0ddfec37_abs"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError(f"JSONL object required: {path}")
        rows.append(value)
    return rows


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({str(key) for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def _geo(values: Iterable[float]) -> float | None:
    selected = [float(value) for value in values]
    if not selected or any(value <= 0 or not math.isfinite(value) for value in selected):
        return None
    return math.exp(sum(math.log(value) for value in selected) / len(selected))


def _selected_rows(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _jsonl(root / "formal_ledger.jsonl"):
        if row.get("event") == "CELL_COMPLETE" and isinstance(row.get("cell_id"), str):
            latest[row["cell_id"]] = row
    expected_ids = {cell["cell_id"] for cell in manifest["cells"]}
    if set(latest) != expected_ids:
        raise RuntimeError("formal ledger does not select exactly 45 manifest cells")
    return sorted(
        latest.values(),
        key=lambda row: (
            int(row["history_index"]),
            int(row["replicate_id"]),
            ARM_ORDER[str(row["arm"])],
        ),
    )


def _validate_selected_cell(row: dict[str, Any], manifest_cell: Mapping[str, Any]) -> None:
    for field in (
        "cell_id",
        "history_index",
        "history_id",
        "replicate_id",
        "arm",
        "dataset_authority_sha256",
        "implementation_identity_sha256",
        "implementation_source_bundle_sha256",
        "method_frozen_seal_sha256",
        "evaluator_identity_sha256",
        "config_identity_sha256",
        "adapter_identity_sha256",
        "workload_manifest_sha256",
        "platform_manifest_sha256",
    ):
        if row.get(field) != manifest_cell.get(field):
            raise RuntimeError(f"selected cell identity mismatch: {field}")
    if row.get("construction_status") != "PASS" or row.get("qa_status") != "PASS":
        raise RuntimeError("selected cell is not valid")
    attempt = Path(str(row.get("construction_root", ""))).resolve()
    if not attempt.is_dir() or (attempt / "failure.json").exists():
        raise RuntimeError("selected cell attempt root is invalid")
    construction = _construction_contract(attempt, row, returncode=0)
    qa = _qa_contract(attempt / "block/qa_full", returncode=0, cell=row)
    if construction["construction_status"] != "PASS" or qa["qa_status"] != "PASS":
        raise RuntimeError("selected cell terminal contract is invalid")
    runtime_identity = _json(attempt / "block/runtime_identity.json")
    runtime_identity_errors = strict_formal_runtime_identity_errors(
        runtime_identity,
        expected_arm=str(row["arm"]),
        expected_manifest_sha256=str(row["workload_manifest_sha256"]),
    )
    if runtime_identity_errors:
        raise RuntimeError(
            "selected cell runtime identity is invalid: "
            + "; ".join(runtime_identity_errors)
        )
    verify_seal(attempt / "block")


def _construction_row(row: Mapping[str, Any]) -> dict[str, Any]:
    attempt = Path(str(row["construction_root"]))
    block = attempt / "block"
    metrics = _json(block / "metrics.json")
    inventory = _json(block / "work_inventory.json")
    order = _json(block / "order_validation.json")
    lifecycle = _json(block / "lifecycle_validation.json")
    adapter = _json(block / "adapter_coverage.json")
    route = _json(attempt / "route_runtime.json")
    proof = _json(attempt / "route_proof.json")
    preparation = _json(attempt / "attempt_preparation.json")
    route_events = _jsonl(attempt / "route_events.jsonl")
    transport = _jsonl(block / "transport_trace.jsonl")
    prompt_tokens = int(inventory.get("prompt_tokens") or 0)
    completion_tokens = int(inventory.get("completion_tokens") or 0)
    chunk_count = int(adapter.get("chunk_count") or 0)
    queue_values = [
        int(item.get("queue_wait_ns") or 0)
        for item in transport
        if isinstance(item.get("queue_wait_ns"), (int, float))
    ]
    service_values = [
        int(item.get("service_ns") or 0)
        for item in transport
        if isinstance(item.get("service_ns"), (int, float))
    ]
    endpoint_counts = Counter(str(item.get("endpoint_id")) for item in route_events)
    return {
        "cell_id": row["cell_id"],
        "history_index": row["history_index"],
        "history_id": row["history_id"],
        "replicate_id": row["replicate_id"],
        "arm": row["arm"],
        "attempt_id": row.get("actual_attempt_id", row.get("attempt_id")),
        "namespace": row.get("actual_namespace", row.get("namespace")),
        "replacement_of": row.get("replacement_of"),
        "construction_status": row["construction_status"],
        "qa_status": row["qa_status"],
        "qa_rows": row["qa_rows"],
        "t_build_ns": metrics.get("t_build_ns"),
        "durable_goodput": metrics.get("durable_goodput"),
        "chunk_count": chunk_count,
        "session_count": adapter.get("session_count"),
        "submitted_count": inventory.get("submitted_count"),
        "completed_count": inventory.get("completed_count"),
        "llm_logical_requests": inventory.get("llm_logical_requests"),
        "transport_attempts": inventory.get("transport_attempts"),
        "transport_failures": inventory.get("transport_failed_attempts"),
        "transport_retries": inventory.get("transport_retry_attempts"),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "calls_per_chunk": (
            float(inventory.get("transport_attempts") or 0) / chunk_count
            if chunk_count
            else None
        ),
        "tokens_per_chunk": (
            float(prompt_tokens + completion_tokens) / chunk_count if chunk_count else None
        ),
        "embedding_calls": inventory.get("embedding_calls"),
        "embedding_items": inventory.get("embedding_items"),
        "db_reads": inventory.get("db_reads"),
        "db_writes": inventory.get("db_writes"),
        "queue_wait_ns_sum": sum(queue_values),
        "queue_wait_ns_max": max(queue_values, default=0),
        "service_ns_sum": sum(service_values),
        "service_ns_max": max(service_values, default=0),
        "route_policy": route.get("policy"),
        "route_proof_status": proof.get("status", proof.get("route_status")),
        "native_replica_calls": endpoint_counts.get("native-replica", 0),
        "prepare_replica_calls": endpoint_counts.get("prepare-replica", 0),
        "order_contract_status": order.get("order_contract_status"),
        "order_violation_count": order.get("order_violation_count"),
        "lifecycle_contract_status": lifecycle.get("contract_status"),
        "cache_preparation_status": preparation.get("status"),
        "construction_root": str(attempt),
    }


def _question_index() -> dict[tuple[str, str], int]:
    from mab_quality_v2_final_qa.mab_main_dataset import build_authority

    authority = build_authority(MAB / "data/official_5_contexts.json")
    return {
        (context.context_id, qa.qa_pair_id): index
        for context in authority["contexts"]
        for index, qa in enumerate(context.qa_items)
    }


def _quality_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    indices = _question_index()
    output: list[dict[str, Any]] = []
    for row in rows:
        attempt = Path(str(row["construction_root"]))
        for item in _jsonl(attempt / "block/qa_full/qa_results.jsonl"):
            metrics = item.get("retrieval_metrics")
            metrics = metrics if isinstance(metrics, Mapping) else {}
            qa_pair_id = str(item.get("qa_pair_id"))
            context_id = str(row["history_id"])
            output.append(
                {
                    "history_index": row["history_index"],
                    "history_id": context_id,
                    "replicate_id": row["replicate_id"],
                    "arm": row["arm"],
                    "cell_id": row["cell_id"],
                    "attempt_id": row.get("actual_attempt_id", row.get("attempt_id")),
                    "namespace": row.get("actual_namespace", row.get("namespace")),
                    "qa_pair_id": qa_pair_id,
                    "question_id": item.get("question_id"),
                    "question_index": indices.get((context_id, qa_pair_id)),
                    "question_type": item.get("question_type"),
                    "status": item.get("status"),
                    "judge_valid": item.get("judge_valid"),
                    "correct": item.get("correct"),
                    "failure_class": item.get("failure_class"),
                    "qa_identity_sha256": item.get("qa_identity_sha256"),
                    "recall_at_1": metrics.get("recall_at_1"),
                    "recall_at_3": metrics.get("recall_at_3"),
                    "recall_at_5": metrics.get("recall_at_5"),
                    "recall_at_10": metrics.get("recall_at_10"),
                    "mrr": metrics.get("mrr"),
                    "ndcg_at_10": metrics.get("ndcg_at_10"),
                    "question_38_anomaly": item.get("question_id") == QUESTION_38_ID,
                }
            )
    return output


def paired_performance(
    construction: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    replicate: list[dict[str, Any]] = []
    ceiling: list[dict[str, Any]] = []
    for history in range(OFFICIAL_HISTORY_COUNT):
        for rep in range(REPLICATE_COUNT):
            pair = {
                row["arm"]: row
                for row in construction
                if row["history_index"] == history and row["replicate_id"] == rep
            }
            if set(pair) != set(ARMS):
                raise RuntimeError("paired construction inventory is incomplete")
            times = {arm: float(pair[arm]["t_build_ns"]) for arm in ARMS}
            effect = {
                "history_index": history,
                "history_id": pair[NATIVE_ARM]["history_id"],
                "replicate_id": rep,
                "a_t_build_ns": times[NATIVE_ARM],
                "c_t_build_ns": times[OURS_ARM],
                "b_t_build_ns": times[ASYNC_ARM],
                "a_vs_c_ratio": times[NATIVE_ARM] / times[OURS_ARM],
                "a_vs_c_log_ratio": math.log(times[NATIVE_ARM] / times[OURS_ARM]),
            }
            replicate.append(effect)
            ceiling.append(
                {
                    **{
                        key: effect[key]
                        for key in ("history_index", "history_id", "replicate_id")
                    },
                    "a_vs_b_ratio": times[NATIVE_ARM] / times[ASYNC_ARM],
                    "c_vs_b_ratio": times[OURS_ARM] / times[ASYNC_ARM],
                    "b_role": "RELAXED_ORDER_CEILING_NOT_HEADLINE_BASELINE",
                }
            )
    history_rows = []
    for history in range(OFFICIAL_HISTORY_COUNT):
        selected = [row for row in replicate if row["history_index"] == history]
        history_rows.append(
            {
                "history_index": history,
                "history_id": selected[0]["history_id"],
                "replicate_count": len(selected),
                "a_vs_c_geometric_mean": _geo(
                    row["a_vs_c_ratio"] for row in selected
                ),
            }
        )
    return replicate, history_rows, ceiling


def paired_quality(
    quality: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_key = {
        (row["history_index"], row["replicate_id"], row["arm"], row["qa_pair_id"]): row
        for row in quality
    }
    pairs: list[dict[str, Any]] = []
    for history in range(OFFICIAL_HISTORY_COUNT):
        for replicate in range(REPLICATE_COUNT):
            pair_rows = [
                row
                for row in quality
                if row["history_index"] == history
                and row["replicate_id"] == replicate
                and row["arm"] == NATIVE_ARM
            ]
            a_correct = c_correct = disagreements = 0
            a_only = c_only = 0
            for a in pair_rows:
                c = by_key.get((history, replicate, OURS_ARM, a["qa_pair_id"]))
                if c is None:
                    raise RuntimeError("paired quality inventory is incomplete")
                av, cv = bool(a["correct"]), bool(c["correct"])
                a_correct += int(av)
                c_correct += int(cv)
                disagreements += int(av != cv)
                a_only += int(av and not cv)
                c_only += int(cv and not av)
            if len(pair_rows) != 60:
                raise RuntimeError("paired quality replicate does not contain 60 questions")
            pairs.append(
                {
                    "history_index": history,
                    "replicate_id": replicate,
                    "question_count": 60,
                    "a_accuracy": a_correct / 60,
                    "c_accuracy": c_correct / 60,
                    "c_minus_a_accuracy": (c_correct - a_correct) / 60,
                    "disagreement_count": disagreements,
                    "a_only_correct": a_only,
                    "c_only_correct": c_only,
                }
            )
    summary = {
        "schema_version": "membind.paired-quality-analysis.v1",
        "status": "PASS",
        "replicate_pairs": pairs,
        "mean_c_minus_a_accuracy": statistics.fmean(
            row["c_minus_a_accuracy"] for row in pairs
        ),
        "total_disagreements": sum(row["disagreement_count"] for row in pairs),
        "total_a_only_correct": sum(row["a_only_correct"] for row in pairs),
        "total_c_only_correct": sum(row["c_only_correct"] for row in pairs),
        "question_38_anomaly": {
            "question_id": QUESTION_38_ID,
            "policy": "RETAINED_EXACTLY_AS_PUBLISHED_WITH_PARTIAL_GOLD_MAPPING_METRICS_NULL",
            "rows": sum(row.get("question_38_anomaly") is True for row in quality),
        },
    }
    return pairs, summary


def _cluster_bootstrap(history_effects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float(row["a_vs_c_geometric_mean"]) for row in history_effects]
    bootstrap = []
    for a in range(5):
        for b in range(5):
            for c in range(5):
                for d in range(5):
                    for e in range(5):
                        bootstrap.append(_geo(values[index] for index in (a, b, c, d, e)))
    ordered = sorted(value for value in bootstrap if value is not None)
    lower = ordered[int(0.025 * (len(ordered) - 1))]
    upper = ordered[int(0.975 * (len(ordered) - 1))]
    wins = sum(value > 1 for value in values)
    sign_p = min(
        1.0,
        2
        * sum(math.comb(5, k) for k in range(0, min(wins, 5 - wins) + 1))
        / 2**5,
    )
    return {
        "cluster_unit": "official_history",
        "history_count": 5,
        "bootstrap_resamples": 5**5,
        "geometric_mean": _geo(values),
        "bootstrap_percentile_95_interval": [lower, upper],
        "two_sided_exact_sign_test_p": sign_p,
        "interpretation": "DESCRIPTIVE_SMALL_N_CLUSTER_AWARE_NOT_ASYMPTOTIC",
    }


def _artifact_manifest(root: Path, names: Sequence[str]) -> dict[str, Any]:
    members = {
        name: {"sha256": _sha(root / name), "bytes": (root / name).stat().st_size}
        for name in names
    }
    payload = {
        "schema_version": "membind.final-artifact-hash-manifest.v1",
        "status": "SEALED",
        "members": members,
    }
    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def finalize(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = _json(root / "FORMAL_CAMPAIGN_MANIFEST_SEAL.json")
    validate_manifest(manifest)
    selected = _selected_rows(root, manifest)
    manifest_cells = {cell["cell_id"]: cell for cell in manifest["cells"]}
    for row in selected:
        _validate_selected_cell(row, manifest_cells[row["cell_id"]])
    construction = [_construction_row(row) for row in selected]
    quality = _quality_rows(selected)
    if len(construction) != 45 or len(quality) != 2700:
        raise RuntimeError("formal campaign is incomplete after artifact validation")
    if any(row["judge_valid"] is not True or row["status"] != "COMPLETE" for row in quality):
        raise RuntimeError("formal evaluator has invalid question rows")
    identities = {
        (row["history_index"], row["replicate_id"], row["arm"], row["qa_pair_id"])
        for row in quality
    }
    if len(identities) != 2700:
        raise RuntimeError("formal quality identities are not unique")

    replicate, history, ceiling = paired_performance(construction)
    quality_pairs, quality_analysis = paired_quality(quality)
    uncertainty = _cluster_bootstrap(history)
    invalid = _json(root / "INVALID_ATTEMPT_LEDGER.json")
    progress = _json(root / "FORMAL_PROGRESS.json")
    statistical = {
        "schema_version": "membind.statistical-analysis.v2",
        "status": "PASS",
        "estimand": "same-history same-replicate paired A_vs_C T_build ratio",
        "replicate_effects": replicate,
        "history_effects": history,
        "overall_geometric_mean_a_vs_c": uncertainty["geometric_mean"],
        "cluster_aware_uncertainty": uncertainty,
        "quality": quality_analysis,
    }
    work = {
        "schema_version": "membind.work-resource-critical-path.v1",
        "status": "PASS",
        "arms": {
            arm: {
                "cell_count": sum(row["arm"] == arm for row in construction),
                "transport_attempts": sum(int(row["transport_attempts"] or 0) for row in construction if row["arm"] == arm),
                "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in construction if row["arm"] == arm),
                "completion_tokens": sum(int(row["completion_tokens"] or 0) for row in construction if row["arm"] == arm),
                "queue_wait_ns": sum(int(row["queue_wait_ns_sum"] or 0) for row in construction if row["arm"] == arm),
                "service_ns": sum(int(row["service_ns_sum"] or 0) for row in construction if row["arm"] == arm),
                "embedding_calls": sum(int(row["embedding_calls"] or 0) for row in construction if row["arm"] == arm),
                "db_writes": sum(int(row["db_writes"] or 0) for row in construction if row["arm"] == arm),
            }
            for arm in ARMS
        },
        "b_relaxed_order_ceiling": ceiling,
        "cache_policy": "RESET_THEN_IDENTICAL_P0_STRUCTURED_WARMUP_PER_CELL",
        "resource_policy": "SAME_FROZEN_DUAL_REPLICA_PLATFORM_ALL_ARMS",
        "invalid_replacement_ledger": invalid,
    }
    claim = {
        "schema_version": "membind.claim-support-matrix.v2",
        "status": "PASS",
        "necessary_validity": {
            "45_construction_cells": "SUPPORTED",
            "45_full_qa_seals": "SUPPORTED",
            "2700_evaluator_valid_rows": "SUPPORTED",
            "upstream_graphiti_identity": "SUPPORTED_BY_CELL_RUN_CONTRACTS",
        },
        "outcomes": {
            "a_vs_c_t_build": "REPORTED_WITH_FIVE_HISTORY_CLUSTER_BOUNDARY",
            "b_ceiling": "REPORTED_SEPARATELY_NOT_HEADLINE_BASELINE",
            "paired_quality": "REPORTED_WITH_DISAGREEMENT_COUNTS",
        },
        "question_38": quality_analysis["question_38_anomaly"],
        "paper_ready": False,
        "cross_hardware_generality": "NOT_EVALUATED",
    }
    summary = {
        "schema_version": "membind.final-three-arm-experiment-result.v2",
        "status": "EXPERIMENT_COMPLETE",
        "campaign_id": manifest["campaign_id"],
        "valid_construction_cells": 45,
        "full_qa_seals": 45,
        "quality_rows": 2700,
        "quality_evaluator_status": "2700_VALID",
        "replicate_effect_count": 15,
        "history_effect_count": 5,
        "overall_geometric_mean_a_vs_c": uncertainty["geometric_mean"],
        "invalid_attempt_count": len(invalid.get("entries", [])),
        "selected_progress": progress,
        "paper_ready": False,
    }

    artifacts: dict[str, Any] = {
        "FORMAL_CONSTRUCTION_TABLE.json": construction,
        "FORMAL_QUALITY_TABLE.json": quality,
        "PER_REPLICATE_PAIRED_EFFECTS.json": replicate,
        "PER_HISTORY_PAIRED_EFFECTS.json": history,
        "B_RELAXED_ORDER_CEILING.json": ceiling,
        "PAIRED_QUALITY_ANALYSIS.json": quality_analysis,
        "STATISTICAL_ANALYSIS.json": statistical,
        "WORK_RESOURCE_CRITICAL_PATH.json": work,
        "CLAIM_SUPPORT_MATRIX.json": claim,
        "FINAL_THREE_ARM_EXPERIMENT_RESULT.json": summary,
        "FINAL_EXPERIMENT_REPORT.json": summary,
    }
    for name, value in artifacts.items():
        _write(root / name, value)
    _write_csv(root / "FORMAL_CONSTRUCTION_TABLE.csv", construction)
    _write_csv(root / "FORMAL_QUALITY_TABLE.csv", quality)
    _write_csv(root / "PER_REPLICATE_PAIRED_EFFECTS.csv", replicate)
    _write_csv(root / "PER_HISTORY_PAIRED_EFFECTS.csv", history)
    _write_csv(root / "PAIRED_QUALITY_EFFECTS.csv", quality_pairs)
    report = (
        "# Final Three-Arm Experiment Report\n\n"
        "Status: `EXPERIMENT_COMPLETE`.\n\n"
        "The report includes 45/45 sealed construction cells, 45/45 FULL QA seals, "
        "and 2700/2700 evaluator-valid question rows.\n\n"
        f"The descriptive A/C geometric mean ratio is `{uncertainty['geometric_mean']}`. "
        "Uncertainty is clustered at the five official histories; B is only a relaxed-order ceiling.\n\n"
        f"Paired quality mean C-A accuracy is `{quality_analysis['mean_c_minus_a_accuracy']}` "
        f"with `{quality_analysis['total_disagreements']}` disagreements. Question 38 "
        "(`0ddfec37_abs`) is retained with its published partial-gold mapping disclosure.\n\n"
        "No automatic `PAPER_READY` claim is made.\n"
    )
    (root / "FINAL_THREE_ARM_EXPERIMENT_REPORT.md").write_text(report, encoding="utf-8")
    (root / "FINAL_EXPERIMENT_REPORT.md").write_text(report, encoding="utf-8")
    (root / "REPRODUCTION_COMMANDS.md").write_text(
        "# Reproduction Commands\n\n"
        "```bash\n"
        "source scripts/local_runtime_8b_dual/activate.sh\n"
        "python saturated_fixed_work_baseline_v1_3/scripts/formal_three_arm_harness.py --output-root <fresh-root> --frozen-root <qualification-identity-root> --manifest-only\n"
        "python saturated_fixed_work_baseline_v1_3/scripts/run_formal_three_arm.py --root <fresh-root> --frozen-root <qualification-identity-root>\n"
        "python saturated_fixed_work_baseline_v1_3/scripts/finalize_formal_three_arm.py --root <fresh-root>\n"
        "```\n\n"
        "Every cell uses `NO_RESUME_FORMAL_ATTEMPT`; only a classified infrastructure failure may receive one fresh whole-cell replacement.\n",
        encoding="utf-8",
    )
    names = [
        *artifacts,
        "FORMAL_CONSTRUCTION_TABLE.csv",
        "FORMAL_QUALITY_TABLE.csv",
        "PER_REPLICATE_PAIRED_EFFECTS.csv",
        "PER_HISTORY_PAIRED_EFFECTS.csv",
        "PAIRED_QUALITY_EFFECTS.csv",
        "FINAL_THREE_ARM_EXPERIMENT_REPORT.md",
        "FINAL_EXPERIMENT_REPORT.md",
        "REPRODUCTION_COMMANDS.md",
    ]
    _write(root / "FINAL_ARTIFACT_HASH_MANIFEST.json", _artifact_manifest(root, names))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    result = finalize(args.root)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
