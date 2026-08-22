#!/usr/bin/env python3
"""Offline analysis for the exploratory graph-state challenge QA.

The direct temporal graph predicate is authoritative here.  The original
challenge runner's substring comparator is retained only as a diagnostic and
is explicitly not promoted to semantic correctness because paraphrases,
Unicode formatting, and date/value normalization require a qualified judge.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def normalized_display(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.replace("×", "x").replace("–", "-").split())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    results_path = args.results.resolve()
    root = results_path.parent
    data = read_json(results_path)
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("CHALLENGE_ROWS_INVALID")

    by_sample: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if isinstance(row, dict):
            by_sample[str(row.get("sample_id"))][str(row.get("method"))].append(row)

    sample_rows: list[dict[str, Any]] = []
    for sample_id in sorted(by_sample):
        pair = by_sample[sample_id]
        b0_rows = pair.get("B0_NATIVE_SERIAL", [])
        b1_rows = pair.get("B1_NAIVE_WHOLE_UPDATE_ASYNC", [])
        if not b0_rows or not b1_rows:
            continue
        b0_state = str(b0_rows[0].get("state_inspection", {}).get("status"))
        b1_state = str(b1_rows[0].get("state_inspection", {}).get("status"))
        b0_exact = sum(bool(row.get("answer_verdict", {}).get("challenge_answer_pass")) for row in b0_rows)
        b1_exact = sum(bool(row.get("answer_verdict", {}).get("challenge_answer_pass")) for row in b1_rows)
        sample_rows.append(
            {
                "sample_id": sample_id,
                "b0_state": b0_state,
                "b1_state": b1_state,
                "state_pair": f"{b0_state}/{b1_state}",
                "b0_graph_state_pass": b0_state == "PASS",
                "b1_graph_state_pass": b1_state == "PASS",
                "b0_b1_state_divergence": b0_state != b1_state,
                "b0_pass_b1_fail": b0_state == "PASS" and b1_state == "FAIL",
                "b0_fail_b1_pass": b0_state == "FAIL" and b1_state == "PASS",
                "question_count": len(b0_rows),
                "b0_substring_heuristic_pass_count": b0_exact,
                "b1_substring_heuristic_pass_count": b1_exact,
                "substring_heuristic_status": "DIAGNOSTIC_ONLY",
            }
        )

    state_summary = {
        "paired_sample_count": len(sample_rows),
        "b0_pass_b1_fail_samples": [row["sample_id"] for row in sample_rows if row["b0_pass_b1_fail"]],
        "b0_fail_b1_pass_samples": [row["sample_id"] for row in sample_rows if row["b0_fail_b1_pass"]],
        "same_state_samples": [row["sample_id"] for row in sample_rows if not row["b0_b1_state_divergence"]],
    }
    analysis = {
        "schema_version": "sfwb.v1.3.memops-state-attack-qa-analysis.v1",
        "status": "EXPLORATORY_STATE_DIVERGENCE_OBSERVED",
        "source_results": str(results_path),
        "direct_graph_state_predicate": {
            "authoritative": True,
            "implementation": "memops_adapter.inspect_current_state",
            "pass_condition": "active expected current value and no active stale conflict",
            "reader_answer_not_substituted": True,
        },
        "llm_answer_evaluation": {
            "status": "NOT_QUALIFIED_AS_SEMANTIC_SCORE",
            "reason": "Initial substring comparator rejects valid paraphrase, Unicode formatting, and equivalent date/value expressions; raw answers remain in challenge_results.json.",
            "alternate_reader_endpoint": "http://10.87.5.247:8002/v1",
            "alternate_embedding_endpoint": "http://10.87.5.247:8003/v1",
        },
        "state_summary": state_summary,
        "sample_rows": sample_rows,
        "construction_calls": 0,
        "graph_writes": 0,
        "qualification_effect": "NONE",
        "note": "This exploratory challenge does not authorize a new B1 claim; repeated paired construction and first-divergence evidence are still required.",
    }
    analysis["payload_sha256"] = __import__("hashlib").sha256(
        json.dumps({k: v for k, v in analysis.items() if k != "payload_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (root / "challenge_analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    md = [
        "# MemOps State Attack QA Analysis",
        "",
        "Status: `EXPLORATORY_STATE_DIVERGENCE_OBSERVED`",
        "",
        "The authoritative result is the direct temporal graph-state predicate. The model substring comparator is diagnostic only and is not treated as semantic correctness.",
        "",
        f"Paired samples: `{len(sample_rows)}`",
        f"B0 PASS -> B1 FAIL: `{', '.join(state_summary['b0_pass_b1_fail_samples']) or 'none'}`",
        f"B0 FAIL -> B1 PASS: `{', '.join(state_summary['b0_fail_b1_pass_samples']) or 'none'}`",
        "",
        "| Sample | B0 state | B1 state |",
        "|---|---|---|",
    ]
    for row in sample_rows:
        md.append(f"| {row['sample_id']} | {row['b0_state']} | {row['b1_state']} |")
    md.extend(
        [
            "",
            "The challenge used graph-fact-only reader context with the alternate Qwen endpoints `8002/8003`. It performed zero construction calls and zero graph writes.",
            "",
            "This is not an official MemOps qualification result and does not by itself establish the full unordered-admission causal chain.",
        ]
    )
    (root / "challenge_analysis.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(state_summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
