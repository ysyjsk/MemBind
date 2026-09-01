#!/usr/bin/env python3
"""Materialize audit tables for one recent three-arm campaign.

The live runner owns construction artifacts.  This script only consumes the
selected campaign root and writes append-only-style summaries under that
root; it never searches or reuses artifacts outside the root.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


METHODS = ("NATIVE_SERIAL", "NATIVE_PARALLEL", "V6_1")
IDENTITIES = {
    "NATIVE_SERIAL": "B0_NATIVE_SERIAL",
    "NATIVE_PARALLEL": "B1_NAIVE_WHOLE_UPDATE_ASYNC",
    "V6_1": "MEMBIND_CORE",
}
INFRA_MARKERS = (
    "timeout",
    "connectionreset",
    "connection reset",
    "service crash",
    "neo4j",
    "embedding",
    "provider truncated",
    "jsondecodeerror",
    "gpu",
    "transport",
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if value.endswith("\n") else value + "\n", encoding="utf-8")


def _attempts(root: Path, context_index: int, method: str) -> list[Path]:
    parent = root / f"context-{context_index}" / method
    if not parent.is_dir():
        return []
    return sorted(path for path in parent.iterdir() if path.is_dir())


def _terminal_attempt(root: Path, context_index: int, method: str) -> tuple[Path | None, str, dict[str, Any]]:
    attempts = _attempts(root, context_index, method)
    complete = [(path, read_json(path / "complete.json")) for path in attempts if (path / "complete.json").is_file()]
    if complete:
        path, row = complete[0]
        block = path / "block"
        seal = read_json(block / "construction_seal.json") if (block / "construction_seal.json").is_file() else {}
        lifecycle = read_json(block / "lifecycle_validation.json") if (block / "lifecycle_validation.json").is_file() else {}
        order = read_json(block / "order_validation.json") if (block / "order_validation.json").is_file() else {}
        refinement = read_json(block / "refinement_validation.json") if (block / "refinement_validation.json").is_file() else {}
        valid = (
            row.get("status") == "PASS"
            and seal.get("status") == "CONSTRUCTION_SEALED"
            and lifecycle.get("contract_status") == "PASS"
            and order.get("order_contract_status") in {"PASS", "NOT_REQUIRED"}
            and (method != "V6_1" or refinement.get("refinement_status") == "PASS")
        )
        return path, "PASS_VALID" if valid else "SCIENTIFIC_FAILURE", row
    failures = [(path, read_json(path / "failure.json")) for path in attempts if (path / "failure.json").is_file()]
    if failures:
        path, row = failures[0]
        text = f"{row.get('error_type', '')} {row.get('error', '')}".casefold()
        status = "INFRA_INVALID" if any(marker in text for marker in INFRA_MARKERS) else "SCIENTIFIC_FAILURE"
        return path, status, row
    return None, "MISSING", {}


def _numbers(value: Any) -> list[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return [float(value)]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return [float(item) for item in value if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(float(item))]
    return []


def _first_number(mapping: Mapping[str, Any], names: tuple[str, ...]) -> float | str:
    for name in names:
        if name in mapping:
            values = _numbers(mapping[name])
            if values:
                return values[0]
    return "MISSING"


def _percentile(values: list[float], fraction: float) -> float | str:
    if not values:
        return "MISSING"
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def _attempt_record(root: Path, context_index: int, method: str) -> dict[str, Any]:
    attempt_root, validity, terminal = _terminal_attempt(root, context_index, method)
    row: dict[str, Any] = {
        "history": context_index,
        "context_index": context_index,
        "method": method,
        "method_identity": IDENTITIES[method],
        "validity": validity,
        "attempt_id": terminal.get("attempt_id"),
        "attempt_root": str(attempt_root.resolve()) if attempt_root else None,
        "quality_status": "MISSING",
    }
    if attempt_root is None:
        return row
    block = attempt_root / "block"
    metrics = read_json(block / "metrics.json") if (block / "metrics.json").is_file() else {}
    inventory = read_json(block / "work_inventory.json") if (block / "work_inventory.json").is_file() else {}
    events = read_jsonl(block / "raw_events.jsonl")
    order = read_json(block / "order_validation.json") if (block / "order_validation.json").is_file() else {}
    refinement = read_json(block / "refinement_validation.json") if (block / "refinement_validation.json").is_file() else {}
    graph = read_json(block / "graph_diagnostics.json") if (block / "graph_diagnostics.json").is_file() else {}
    proof = refinement.get("proof", {}) if isinstance(refinement.get("proof"), Mapping) else {}
    proof_replay = proof.get("replay", {}) if isinstance(proof.get("replay"), Mapping) else {}
    proof_request = proof.get("request", {}) if isinstance(proof.get("request"), Mapping) else {}
    row.update({
        "construction_seal": read_json(block / "construction_seal.json") if (block / "construction_seal.json").is_file() else None,
        "expected": terminal.get("episode_count", inventory.get("expected_episode_count")),
        "submitted": inventory.get("submitted_count", terminal.get("episode_count")),
        "completed": inventory.get("completed_count", terminal.get("episode_count")),
        "makespan": _first_number(metrics, ("t_build_ns",)),
        "goodput": _first_number(metrics, ("durable_goodput",)),
        "publication_order": order.get("order_contract_status", "MISSING"),
        "canonical_state_status": graph.get("status", "PASS" if graph.get("canonical_graph_hash") else "MISSING"),
        "canonical_graph_hash": graph.get("canonical_graph_hash", graph.get("canonical_state_hash", "MISSING")),
        "nodes": graph.get("node_count", graph.get("nodes", "MISSING")),
        "edges": graph.get("relationship_count", graph.get("edges", "MISSING")),
        "logical_calls": _first_number(inventory, ("llm_logical_requests", "provider_external_logical_calls", "provider_wrapper_calls")),
        "transport_attempts": _first_number(inventory, ("transport_attempts", "expected_transport_attempts_from_provider")),
        "transport_failures": _first_number(inventory, ("transport_failed_attempts",)),
        "transport_retries": _first_number(inventory, ("transport_retry_attempts", "transport_true_retry_attempts")),
        "input_tokens": _first_number(inventory, ("prompt_tokens", "input_tokens")),
        "output_tokens": _first_number(inventory, ("completion_tokens", "output_tokens")),
        "embedding_items": _first_number(inventory, ("embedding_items",)),
        "db_writes": _first_number(inventory, ("db_writes", "db_write_statements")),
        "replay_count": _first_number(proof_replay, ("logical_consumed",)),
        "fresh_fallback_count": _first_number(proof_replay, ("fresh_fallback",)),
        "exact_match": _first_number(proof_request, ("match_count",)),
    })
    formal = next((int(event["monotonic_ns"]) for event in events if event.get("event") in {"FORMAL_START", "FORMAL_CONSTRUCTION_START"} and isinstance(event.get("monotonic_ns"), int)), None)
    durable = [event for event in events if event.get("event") == "PUBLICATION_DURABLE" and isinstance(event.get("monotonic_ns"), int)]
    if formal is not None and durable:
        row["TTFP"] = (int(durable[0]["monotonic_ns"]) - formal) / 1_000_000_000
    else:
        row["TTFP"] = "MISSING"
    sequences = [int(event["source_sequence"]) for event in durable if isinstance(event.get("source_sequence"), int)]
    inversions = sum(1 for i, left in enumerate(sequences) for right in sequences[i + 1 :] if left > right)
    row["inversion_count"] = inversions if durable else "MISSING"
    source_times = []
    enters = {int(event["source_sequence"]): int(event["monotonic_ns"]) for event in events if event.get("event") in {"NATIVE_ENTER", "NATIVE_START"} and isinstance(event.get("source_sequence"), int) and isinstance(event.get("monotonic_ns"), int)}
    for event in durable:
        seq = event.get("source_sequence")
        if isinstance(seq, int) and seq in enters:
            source_times.append((int(event["monotonic_ns"]) - enters[seq]) / 1_000_000_000)
    row["source_p50"] = _percentile(source_times, 0.50)
    row["source_p95"] = _percentile(source_times, 0.95)
    row["source_p99"] = _percentile(source_times, 0.99)
    row["mismatch_fallback_count"] = _first_number(proof_replay, ("mismatch_fallback",))
    row["missing_fallback_count"] = _first_number(proof_replay, ("missing_fallback",))
    row["prepare_native_overlap"] = "MISSING"
    row["frontier_wait"] = "MISSING"
    return row


def _history_report(history: int, rows: list[dict[str, Any]]) -> str:
    lines = [f"# History {history}", "", f"Context index: `{history}`", "", "| Method | Identity | Validity | Makespan (s) | Goodput | TTFP (s) | Publication order | Canonical state | Quality |", "|---|---|---|---:|---:|---:|---|---|---|"]
    for row in rows:
        makespan = row.get("makespan", "MISSING")
        if isinstance(makespan, (int, float)):
            makespan = float(makespan) / 1_000_000_000
        lines.append("| " + " | ".join(str(row.get(key, "MISSING")) for key in ("method", "method_identity", "validity")) + f" | {makespan} | {row.get('goodput', 'MISSING')} | {row.get('TTFP', 'MISSING')} | {row.get('publication_order', 'MISSING')} | {row.get('canonical_state_status', 'MISSING')} | {row.get('quality_status', 'MISSING')} |")
    lines += ["", "B1 is reported only as a relaxed-order upper bound. Core speedup is eligible only when B0/Core fairness and correctness checks pass.", "", "Raw attempt roots and all terminal/replacement evidence remain under the campaign root."]
    return "\n".join(lines)


def _summary_stats(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [float(row[key]) / 1_000_000_000 if key == "makespan" and isinstance(row.get(key), (int, float)) else float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    if not values:
        return {"count": 0, "median": "MISSING", "mean": "MISSING", "standard_deviation": "MISSING", "ci95": "MISSING"}
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    margin = 2.776 * sd / math.sqrt(len(values)) if len(values) > 1 else "MISSING"
    return {"count": len(values), "median": statistics.median(values), "mean": mean, "standard_deviation": sd, "ci95": [mean - margin, mean + margin] if isinstance(margin, float) else margin}


def _core_speedups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for history in range(5):
        serial = next((row.get("makespan") for row in rows if row.get("history") == history and row.get("method") == "NATIVE_SERIAL" and isinstance(row.get("makespan"), (int, float))), None)
        core = next((row.get("makespan") for row in rows if row.get("history") == history and row.get("method") == "V6_1" and isinstance(row.get("makespan"), (int, float))), None)
        result.append({"history": history, "speedup": serial / core if isinstance(serial, (int, float)) and isinstance(core, (int, float)) and core else "MISSING"})
    return result


def finalize(root: Path, *, requested_history: int | None = None) -> dict[str, Any]:
    prereg = read_json(root / "RECENT_THREE_ARM_CAMPAIGN_PREREGISTRATION.json")
    histories = prereg.get("histories", [])
    selected = [item for item in histories if requested_history is None or item.get("history") == requested_history]
    all_rows: list[dict[str, Any]] = []
    for item in selected:
        history = int(item["history"])
        rows = [_attempt_record(root, history, method) for method in METHODS]
        all_rows.extend(rows)
        history_root = root / f"history-{history}"
        write_json(history_root / "HISTORY_BLOCK_RESULT.json", {"schema_version": "membind.history-block-result.v1", "history": history, "context_index": history, "status": "HISTORY_BLOCK_SEALED" if all(row["validity"] != "MISSING" for row in rows) else "HISTORY_BLOCK_INCOMPLETE", "methods": rows, "fairness_checked": all(row["validity"] == "PASS_VALID" for row in rows if row["method"] != "NATIVE_PARALLEL")})
        write_text(history_root / "HISTORY_BLOCK_REPORT.md", _history_report(history, rows))

    complete_histories = len(selected) == len(histories) and all(row["validity"] != "MISSING" for row in all_rows)
    if not complete_histories:
        return {"status": "PARTIAL", "histories_materialized": [int(item["history"]) for item in selected], "rows": all_rows}

    table_rows = []
    for row in all_rows:
        table_rows.append({
            "history": row["history"], "method": row["method"], "method_identity": row["method_identity"], "validity": row["validity"],
            "makespan": (row["makespan"] / 1_000_000_000 if isinstance(row.get("makespan"), (int, float)) else row.get("makespan", "MISSING")),
            "goodput": row.get("goodput", "MISSING"), "TTFP": row.get("TTFP", "MISSING"), "publication_order": row.get("publication_order", "MISSING"),
            "logical_calls": row.get("logical_calls", "MISSING"), "transport_attempts": row.get("transport_attempts", "MISSING"), "input_tokens": row.get("input_tokens", "MISSING"), "output_tokens": row.get("output_tokens", "MISSING"),
            "embedding_items": row.get("embedding_items", "MISSING"), "db_writes": row.get("db_writes", "MISSING"), "replay_count": row.get("replay_count", "MISSING"), "fresh_fallback_count": row.get("fresh_fallback_count", "MISSING"),
            "canonical_state_status": row.get("canonical_state_status", "MISSING"), "quality_status": row.get("quality_status", "MISSING"),
        })
    result = {
        "schema_version": "membind.recent-three-arm-campaign-result.v1",
        "status": "PASS" if all(row["validity"] == "PASS_VALID" for row in all_rows) else "PARTIAL",
        "campaign_id": prereg.get("campaign_id"), "preregistration": str((root / "RECENT_THREE_ARM_CAMPAIGN_PREREGISTRATION.json").resolve()),
        "rows": all_rows, "main_table": table_rows,
        "statistics": {method: _summary_stats([row for row in all_rows if row["method"] == method and row["validity"] == "PASS_VALID"], "makespan") for method in METHODS},
        "invalid_and_replacements": [row for row in all_rows if row["validity"] != "PASS_VALID"],
        "speedup_core": _core_speedups(all_rows),
    }
    write_json(root / "RECENT_THREE_ARM_CAMPAIGN_RESULT.json", result)
    report = [
        "# Recent Three-Arm Campaign Report",
        "",
        f"Campaign: {prereg.get('campaign_id')}; status: {result['status']}.",
        "",
        "The paired Core comparison is T_NATIVE_SERIAL / T_MEMBIND_CORE. NATIVE_PARALLEL is a relaxed-order ceiling and is not a semantic headline baseline.",
        "",
        "## Aggregate Makespan",
        "",
        "| Method | Count | Median (s) | Mean (s) | SD (s) | 95% CI |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for method in METHODS:
        stat = result["statistics"][method]
        report.append(f"| {method} | {stat['count']} | {stat['median']} | {stat['mean']} | {stat['standard_deviation']} | {stat['ci95']} |")
    report += ["", "## Paired Core Speedup", "", "| History | Speedup |", "|---:|---:|"]
    report += [f"| {item['history']} | {item['speedup']} |" for item in result["speedup_core"]]
    report += ["", "All raw, invalid, replacement, and per-history evidence remains in this campaign root."]
    write_text(root / "RECENT_THREE_ARM_CAMPAIGN_REPORT.md", "\n".join(report))
    with (root / "RECENT_THREE_ARM_MAIN_TABLE.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table_rows[0]))
        writer.writeheader(); writer.writerows(table_rows)
    ledger = root / "campaign_ledger.jsonl"
    write_text(root / "RECENT_THREE_ARM_LEDGER.jsonl", ledger.read_text(encoding="utf-8") if ledger.is_file() else "")
    members = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "RECENT_THREE_ARM_ARTIFACT_MANIFEST.json":
            members.append({"path": str(path.relative_to(root)), "sha256": sha256(path), "bytes": path.stat().st_size})
    write_json(root / "RECENT_THREE_ARM_ARTIFACT_MANIFEST.json", {"schema_version": "membind.recent-three-arm-artifact-manifest.v1", "campaign_id": prereg.get("campaign_id"), "members": members})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--history", type=int)
    args = parser.parse_args()
    result = finalize(args.root.resolve(), requested_history=args.history)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("status") in {"PASS", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
