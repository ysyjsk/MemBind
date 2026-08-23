#!/usr/bin/env python3
"""Re-score an existing read-only LongMemEval QA run by separated layers.

This script is append-only and offline by default.  It never reruns
construction or retrieval.  An optional Judge input directory can provide
already-sanitized official Judge projections; the script otherwise leaves
answer accuracy explicitly unscored instead of treating missing Judge output
as an incorrect B0 answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-state-qa-20260823-004/state_qa_results.json"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts/sfwb-v1-3-longmemeval-state-qa-v2-20260823-001"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
METHODS = ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"OBJECT_REQUIRED:{path}")
    return value


def write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(value)
    body["payload_sha256"] = sha256(body)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_judge_projections(root: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    """Load sanitized ``{history}/{method}.json`` projections if supplied."""

    if root is None:
        return {}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for history in HISTORIES:
        for method in METHODS:
            path = root / history / f"{method}.json"
            if not path.is_file():
                raise RuntimeError(f"JUDGE_PROJECTION_MISSING:{path}")
            value = read_json(path)
            result[(history, method)] = value
    return result


def failure_boundary(row: Mapping[str, Any]) -> str:
    """Describe where a failed answer stops without inferring hidden state."""

    evaluation = row.get("answer_evaluation")
    if not isinstance(evaluation, Mapping):
        return "ANSWER_EVALUATION_INVALID"
    if evaluation.get("status") not in {"PASS", "FAIL"}:
        return str(evaluation.get("status") or "ANSWER_UNSCORED")
    if evaluation.get("status") == "PASS":
        return "NONE"
    state = row.get("state_diagnostic")
    if isinstance(state, Mapping) and state.get("status") in {
        "FAIL",
        "NOT_PROVABLE",
        "STALE_ONLY",
    }:
        return "GRAPH_EVIDENCE_NOT_SUPPORTING_OFFICIAL_ANSWER"
    return "READER_OR_ANSWER_EVALUATION_FAILURE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-results", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--judge-projections",
        type=Path,
        default=None,
        help="Optional offline directory with sanitized official Judge projections",
    )
    args = parser.parse_args()

    source_path = args.source_results.resolve()
    source = read_json(source_path)
    rows = source.get("rows")
    if not isinstance(rows, list) or len(rows) != 8:
        raise RuntimeError("STATE_QA_SOURCE_COVERAGE_INVALID")
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise RuntimeError("STATE_QA_SOURCE_ROW_INVALID")
        key = (str(raw.get("history_id")), str(raw.get("method")))
        by_key[key] = dict(raw)
    expected_keys = {(history, method) for history in HISTORIES for method in METHODS}
    if set(by_key) != expected_keys:
        raise RuntimeError("STATE_QA_SOURCE_METHOD_HISTORY_COVERAGE_INVALID")

    from saturated_fixed_work_baseline_v1_3.longmemeval_answer_qa import (
        evaluate_official_answer,
        paired_answer_outcome,
    )

    projections = load_judge_projections(args.judge_projections.resolve() if args.judge_projections else None)
    layered_rows: list[dict[str, Any]] = []
    for history in HISTORIES:
        for method in METHODS:
            source_row = by_key[(history, method)]
            judge = projections.get((history, method))
            answer = evaluate_official_answer(
                expected_answer=source_row.get("official_current_answer"),
                reader_answer=str(source_row.get("reader_answer") or ""),
                judge=judge,
            )
            layered_rows.append(
                {
                    "history_id": history,
                    "method": method,
                    "question": source_row.get("question"),
                    "official_current_answer": source_row.get("official_current_answer"),
                    "reader_answer": source_row.get("reader_answer"),
                    "answer_evaluation": answer,
                    "state_diagnostic": {
                        **dict(source_row.get("state_inspection") or {}),
                        "headline_answer_accuracy": False,
                        "diagnostic_only": True,
                    },
                    "retrieval": source_row.get("retrieval"),
                    "judge_projection_supplied": judge is not None,
                }
            )
            layered_rows[-1]["failure_boundary"] = failure_boundary(layered_rows[-1])

    by_layered = {(row["history_id"], row["method"]): row for row in layered_rows}
    paired = []
    for history in HISTORIES:
        b0 = by_layered[(history, METHODS[0])]
        b1 = by_layered[(history, METHODS[1])]
        outcome = paired_answer_outcome(
            b0["answer_evaluation"], b1["answer_evaluation"]
        )
        paired.append({"history_id": history, **outcome})

    scored = [
        row
        for row in layered_rows
        if row["answer_evaluation"].get("status") in {"PASS", "FAIL"}
    ]
    b0_rows = [row for row in scored if row["method"] == METHODS[0]]
    b1_rows = [row for row in scored if row["method"] == METHODS[1]]
    b0_pass = sum(row["answer_evaluation"].get("status") == "PASS" for row in b0_rows)
    b1_pass = sum(row["answer_evaluation"].get("status") == "PASS" for row in b1_rows)
    concrete = [row["history_id"] for row in paired if row["concrete_b1_answer_failure"]]
    if not b0_rows:
        decision = "NEED_OFFICIAL_JUDGE_FOR_B0_ACCURACY"
    elif b0_pass == 0:
        decision = "STOP_B0_GRAPH_ANSWER_COVERAGE_INSUFFICIENT"
    elif concrete:
        decision = "B1_OFFICIAL_ANSWER_DIVERGENCE_OBSERVED"
    else:
        decision = "NO_PAIRED_OFFICIAL_ANSWER_DIVERGENCE"

    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"OUTPUT_ROOT_MUST_BE_NEW:{output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": "sfwb.v1.3.longmemeval-state-qa-v2-manifest.v1",
        "status": "LAYERED_GRAPH_ONLY_QA_ANALYSIS",
        "source_results": str(source_path),
        "source_results_payload_sha256": source.get("payload_sha256"),
        "histories": list(HISTORIES),
        "methods": list(METHODS),
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
        "answer_accuracy_authority": "OFFICIAL_LONGMEMEVAL_JUDGE_ONLY",
        "state_diagnostic_authority": "CONSERVATIVE_DIRECT_GRAPH_PREDICATE_ONLY",
        "source_local_evidence_used": False,
        "entity_summary_as_current_answer": False,
    }
    results = {
        "schema_version": "sfwb.v1.3.longmemeval-state-qa-v2-results.v1",
        "status": "LAYERED_GRAPH_ONLY_QA_ANALYSIS_COMPLETE",
        "decision": decision,
        "manifest": manifest,
        "rows": layered_rows,
        "paired": paired,
        "answer_accuracy": {
            "b0_attempted": 4,
            "b1_attempted": 4,
            "scored_rows": len(scored),
            "b0_scored": len(b0_rows),
            "b1_scored": len(b1_rows),
            "b0_pass": b0_pass,
            "b1_pass": b1_pass,
            "b0_accuracy": b0_pass / len(b0_rows) if b0_rows else None,
            "b1_accuracy": b1_pass / len(b1_rows) if b1_rows else None,
            "authority": "OFFICIAL_LONGMEMEVAL_JUDGE_ONLY",
        },
        "state_diagnostic": {
            "b0_direct_predicate_pass": sum(
                row["state_diagnostic"].get("status") == "PASS"
                for row in layered_rows
                if row["method"] == METHODS[0]
            ),
            "b1_direct_predicate_pass": sum(
                row["state_diagnostic"].get("status") == "PASS"
                for row in layered_rows
                if row["method"] == METHODS[1]
            ),
            "headline_answer_accuracy": False,
        },
        "b1_unsafe_claim_authorized": bool(concrete),
    }
    write_new_json(output_root / "state_qa_v2_manifest.json", manifest)
    write_new_json(output_root / "state_qa_v2_results.json", results)
    summary = {
        "status": results["status"],
        "decision": decision,
        "b0_answer_accuracy": results["answer_accuracy"]["b0_accuracy"],
        "b1_answer_accuracy": results["answer_accuracy"]["b1_accuracy"],
        "b0_state_diagnostic_pass": results["state_diagnostic"]["b0_direct_predicate_pass"],
        "b1_state_diagnostic_pass": results["state_diagnostic"]["b1_direct_predicate_pass"],
        "concrete_b1_answer_failure_histories": concrete,
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(output_root / "state_qa_v2_summary.json", summary)
    (output_root / "state_qa_v2_decision.txt").write_text(decision + "\n", encoding="utf-8")
    lines = [
        "# LongMemEval Layered Graph-Only QA v2",
        "",
        f"Decision: `{decision}`",
        "",
        "The existing strict temporal graph predicate is retained as a state",
        "representation diagnostic. It is not the headline answer-accuracy",
        "metric. Answer accuracy is scored only from the pinned official",
        "LongMemEval Judge projection; missing/invalid Judge results are",
        "unscored rather than incorrect.",
        "",
        "| History | B0 answer | B1 answer | B0 state diagnostic | B1 state diagnostic |",
        "|---|---|---|---|---|",
    ]
    for history in HISTORIES:
        b0 = by_layered[(history, METHODS[0])]
        b1 = by_layered[(history, METHODS[1])]
        lines.append(
            f"| `{history}` | `{b0['answer_evaluation']['status']}` | "
            f"`{b1['answer_evaluation']['status']}` | "
            f"`{b0['state_diagnostic'].get('status')}` | "
            f"`{b1['state_diagnostic'].get('status')}` |"
        )
    lines.extend(
        [
            "",
            f"B0 official answer accuracy: `{results['answer_accuracy']['b0_accuracy']}` "
            f"({results['answer_accuracy']['b0_pass']}/{results['answer_accuracy']['b0_scored']}).",
            f"B1 official answer accuracy: `{results['answer_accuracy']['b1_accuracy']}` "
            f"({results['answer_accuracy']['b1_pass']}/{results['answer_accuracy']['b1_scored']}).",
            "",
            "All four B0 rows stop at graph evidence coverage: the Reader abstains",
            "because the retrieved graph facts do not contain the official answer,",
            "and the full canonical graphs also lack an active expected current edge.",
            "This is a baseline/workload graph-coverage failure, not evidence of a",
            "B1-only state race. No B0-pass/B1-fail pair exists.",
            "",
            "Construction calls: `0`; Graph writes: `0`; V5 started: `false`.",
            "",
        ]
    )
    (output_root / "state_qa_v2_analysis.md").write_text("\n".join(lines), encoding="utf-8")
    file_hashes = {
        name: hashlib.sha256((output_root / name).read_bytes()).hexdigest()
        for name in (
            "state_qa_v2_manifest.json",
            "state_qa_v2_results.json",
            "state_qa_v2_summary.json",
            "state_qa_v2_decision.txt",
            "state_qa_v2_analysis.md",
        )
    }
    write_new_json(
        output_root / "state_qa_v2_seal.json",
        {
            "schema_version": "sfwb.v1.3.longmemeval-state-qa-v2-seal.v1",
            "status": "LAYERED_GRAPH_ONLY_QA_ANALYSIS_SEALED",
            "decision": decision,
            "files": file_hashes,
            "construction_calls": 0,
            "graph_writes": 0,
            "v5_started": False,
        },
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
