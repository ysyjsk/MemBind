#!/usr/bin/env python3
"""Read-only reducer for the existing four-history QA/Judge evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "membind.baseline-reuse-qa-analysis.v1"
CLAIM_SCOPE = "BASELINE_REUSE_4_HISTORY_NOT_MAB_MULTIQA"
EXACT_JUDGE_MODEL = "Qwen/Qwen3-32B"
METHODS = ("U0", "P(C=2)")
METRICS = (
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "ndcg_at_10",
)


class AnalysisError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0 or not 0 <= successes <= total:
        raise AnalysisError("WILSON_INPUT_INVALID")
    p = successes / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return centre - margin, centre + margin


def _key(row: Mapping[str, object]) -> tuple[str, str]:
    method = str(row.get("method", ""))
    history_id = str(row.get("history_id", ""))
    if method not in METHODS or not history_id:
        raise AnalysisError("ROW_IDENTITY_INVALID")
    return method, history_id


def _mean(rows: list[Mapping[str, object]], metric: str) -> float:
    values: list[float] = []
    for row in rows:
        session_metrics = row.get("session_metrics")
        if not isinstance(session_metrics, Mapping):
            raise AnalysisError("SESSION_METRICS_INVALID")
        value = session_metrics.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AnalysisError("SESSION_METRICS_INVALID")
        values.append(float(value))
    return sum(values) / len(values)


def build_analysis(
    public_rows: Iterable[Mapping[str, object]],
    judge_items: Iterable[Mapping[str, object]],
) -> dict[str, Any]:
    rows = list(public_rows)
    judges = list(judge_items)
    row_map = {_key(row): row for row in rows}
    judge_map = {_key(row): row for row in judges}
    if len(row_map) != len(rows) or len(judge_map) != len(judges):
        raise AnalysisError("DUPLICATE_ROW_IDENTITY")
    if set(row_map) != set(judge_map):
        raise AnalysisError("JUDGE_ROW_INVENTORY_MISMATCH")
    inventories = {
        method: {history for selected_method, history in row_map if selected_method == method}
        for method in METHODS
    }
    if not inventories["U0"] or inventories["U0"] != inventories["P(C=2)"]:
        raise AnalysisError("PAIRED_INVENTORY_MISMATCH")

    invalid_count = 0
    agreement_count = 0
    method_results: dict[str, dict[str, Any]] = {}
    item_results: list[dict[str, Any]] = []
    for key in sorted(row_map, key=lambda value: (value[1], value[0])):
        public = row_map[key]
        judge = judge_map[key]
        if judge.get("model") != EXACT_JUDGE_MODEL:
            raise AnalysisError("JUDGE_MODEL_DRIFT")
        valid = (
            judge.get("finish_reason") == "stop"
            and judge.get("parse_status") in {"YES", "NO"}
            and type(judge.get("label")) is bool
        )
        if not valid:
            invalid_count += 1
        agrees = judge.get("agrees_with_original") is True
        agreement_count += int(agrees)
        item_results.append(
            {
                "method": key[0],
                "history_id": key[1],
                "valid": valid,
                "correct": judge.get("label") if valid else None,
                "agrees_with_original": agrees,
                "reader_valid": (
                    isinstance(public.get("reader"), Mapping)
                    and public["reader"].get("status") == "SUCCESS"  # type: ignore[index]
                    and public["reader"].get("finish_reason") == "stop"  # type: ignore[index]
                ),
            }
        )

    for method in METHODS:
        selected_rows = [row_map[(method, history)] for history in sorted(inventories[method])]
        selected_judges = [judge_map[(method, history)] for history in sorted(inventories[method])]
        valid = [
            row
            for row in selected_judges
            if row.get("finish_reason") == "stop"
            and row.get("parse_status") in {"YES", "NO"}
            and type(row.get("label")) is bool
        ]
        correct = sum(row.get("label") is True for row in valid)
        low, high = wilson_interval(correct, len(valid))
        method_results[method] = {
            "question_count": len(selected_rows),
            "valid_count": len(valid),
            "invalid_count": len(selected_judges) - len(valid),
            "correct_count": correct,
            "accuracy": correct / len(valid),
            "accuracy_wilson_95": {"low": low, "high": high},
            "reader_valid_count": sum(
                isinstance(row.get("reader"), Mapping)
                and row["reader"].get("status") == "SUCCESS"  # type: ignore[index]
                and row["reader"].get("finish_reason") == "stop"  # type: ignore[index]
                for row in selected_rows
            ),
            "context_gold_session_coverage_mean": sum(
                float(row.get("context_gold_session_coverage_posthoc", 0.0))
                for row in selected_rows
            )
            / len(selected_rows),
            "retrieval": {metric: _mean(selected_rows, metric) for metric in METRICS},
        }

    histories = sorted(inventories["U0"])
    pair_rows = []
    for history in histories:
        u0 = judge_map[("U0", history)].get("label")
        pc2 = judge_map[("P(C=2)", history)].get("label")
        pair_rows.append(
            {
                "history_id": history,
                "U0": u0,
                "P(C=2)": pc2,
                "agreement": type(u0) is bool and u0 == pc2,
            }
        )
    agreement = sum(row["agreement"] is True for row in pair_rows)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_scope": CLAIM_SCOPE,
        "status": "PASS",
        "construction_reused": True,
        "construction_calls": 0,
        "reader_calls": 0,
        "judge_calls_reused": len(judges),
        "judge_model": EXACT_JUDGE_MODEL,
        "methods": method_results,
        "paired": {
            "pair_count": len(histories),
            "agreement_count": agreement,
            "agreement_rate": agreement / len(histories),
            "discordant_count": len(histories) - agreement,
            "accuracy_delta_pc2_minus_u0": (
                method_results["P(C=2)"]["accuracy"]
                - method_results["U0"]["accuracy"]
            ),
            "items": pair_rows,
        },
        "judge_validation": {
            "request_count": len(judges),
            "invalid_count": invalid_count,
            "agreement_with_original_count": agreement_count,
            "agreement_rate": agreement_count / len(judges),
        },
        "items": item_results,
        "limitations": [
            "Only four development histories are scored per method.",
            "The result is a baseline-reuse diagnostic, not a MAB Multi-QA result.",
            "All four questions are knowledge-update questions; no question-type generalization is supported.",
            "Identical observed accuracy does not establish method equivalence or non-inferiority.",
        ],
    }
    result["payload_sha256"] = canonical_sha256(result)
    return result


def load_frozen_evidence(
    input_manifest_path: Path, judge_results_path: Path
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]], dict[str, object]]:
    input_manifest = json.loads(input_manifest_path.read_text(encoding="utf-8"))
    judge_results = json.loads(judge_results_path.read_text(encoding="utf-8"))
    records = input_manifest.get("records")
    items = judge_results.get("items")
    if not isinstance(records, list) or not isinstance(items, list):
        raise AnalysisError("SOURCE_SCHEMA_INVALID")
    public_rows: list[Mapping[str, object]] = []
    sources: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise AnalysisError("SOURCE_SCHEMA_INVALID")
        source = Path(str(record.get("source_path", "")))
        expected_hash = str(record.get("source_sha256", ""))
        if not source.is_file() or file_sha256(source) != expected_hash:
            raise AnalysisError("SOURCE_HASH_MISMATCH")
        bundle = json.loads(source.read_text(encoding="utf-8"))
        public = bundle.get("public_artifact")
        if not isinstance(public, Mapping):
            raise AnalysisError("PUBLIC_ARTIFACT_MISSING")
        public_rows.append(public)
        sources.append(
            {
                "path": str(source.resolve()),
                "sha256": expected_hash,
                "method": record.get("method"),
                "history_id": record.get("history_id"),
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "read_only_sources": sources,
        "input_manifest": {
            "path": str(input_manifest_path.resolve()),
            "sha256": file_sha256(input_manifest_path),
        },
        "judge_results": {
            "path": str(judge_results_path.resolve()),
            "sha256": file_sha256(judge_results_path),
        },
        "historical_artifacts_modified": False,
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    return public_rows, items, manifest


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render_report(result: Mapping[str, object]) -> str:
    methods = result["methods"]
    paired = result["paired"]
    judge = result["judge_validation"]
    lines = [
        "# Baseline-reuse final QA analysis",
        "",
        "This is a read-only reanalysis of the existing four-history Quality-v1 baseline. "
        "It is not a MemoryAgentBench Multi-QA result.",
        "",
        "## Headline",
        "",
        "| Method | Valid | Correct | Accuracy | Wilson 95% interval |",
        "|---|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        value = methods[method]  # type: ignore[index]
        interval = value["accuracy_wilson_95"]
        lines.append(
            f"| {method} | {value['valid_count']}/{value['question_count']} | "
            f"{value['correct_count']} | {value['accuracy']:.1%} | "
            f"[{interval['low']:.1%}, {interval['high']:.1%}] |"
        )
    lines.extend(
        [
            "",
            f"The exact `{EXACT_JUDGE_MODEL}` rejudge produced "
            f"{judge['invalid_count']} invalid outputs across {judge['request_count']} requests "
            f"and agreed with the frozen original Judge on {judge['agreement_with_original_count']}/"
            f"{judge['request_count']} rows.",
            "",
            "## Paired interpretation",
            "",
            f"U0 and P(C=2) agree on {paired['agreement_count']}/{paired['pair_count']} histories; "
            f"the observed P(C=2)-minus-U0 accuracy delta is "
            f"{paired['accuracy_delta_pc2_minus_u0']:+.1%}. There are no observed paired "
            "wins or losses, but n=4 is far too small to claim equivalence.",
            "",
            "## Retrieval and execution validity",
            "",
            "| Method | Reader valid | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for method in METHODS:
        value = methods[method]  # type: ignore[index]
        retrieval = value["retrieval"]
        lines.append(
            f"| {method} | {value['reader_valid_count']}/{value['question_count']} | "
            f"{retrieval['recall_at_1']:.3f} | {retrieval['recall_at_3']:.3f} | "
            f"{retrieval['recall_at_5']:.3f} | {retrieval['recall_at_10']:.3f} | "
            f"{retrieval['mrr']:.3f} | {retrieval['ndcg_at_10']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Error analysis",
            "",
            "Both methods fail the same two histories:",
            "",
            "- `6071bd76`: both answers say *more water* even while describing a change from "
            "6 oz to 5 oz; the reference direction is *less water*. This is a Reader reasoning/wording "
            "error, not an invalid Judge response.",
            "- `a2f3aa27`: both answers return 1,250 Instagram followers while the current reference "
            "is 1,300. This is a stale/current-state answer error.",
            "",
            "The two successes are also shared: the old sneakers location and the count of five MCU films. "
            "Because retrieval and final labels are identical across methods on every row, this sample "
            "contains no evidence that P(C=2) changes downstream QA quality relative to U0.",
            "",
            "## Scope limits",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in result["limitations"])  # type: ignore[index]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--judge-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows, judges, sources = load_frozen_evidence(
        args.input_manifest, args.judge_results
    )
    result = build_analysis(rows, judges)
    atomic_json(args.output_dir / "SOURCE_MANIFEST.json", sources)
    atomic_json(args.output_dir / "RESULTS.json", result)
    report_path = args.output_dir / "FINAL_QA_ANALYSIS.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    print(json.dumps({"status": "PASS", "result": str(args.output_dir / "RESULTS.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
