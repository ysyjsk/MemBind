#!/usr/bin/env python3
"""Seal offline analysis for a completed LongMemEval graph-state QA run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


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
    body = dict(value)
    body["payload_sha256"] = sha256(body)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    results_path = args.results.resolve()
    root = results_path.parent
    results = read_json(results_path)
    rows = results.get("rows")
    paired = results.get("paired")
    if not isinstance(rows, list) or not isinstance(paired, list) or len(rows) != 8 or len(paired) != 4:
        raise RuntimeError("STATE_QA_RESULT_COVERAGE_INVALID")

    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RuntimeError("STATE_QA_ROW_INVALID")
        key = (str(row.get("history_id")), str(row.get("method")))
        by_pair[key] = dict(row)
    if set(by_pair) != {(history, method) for history in HISTORIES for method in METHODS}:
        raise RuntimeError("STATE_QA_METHOD_HISTORY_COVERAGE_INVALID")

    failure_boundaries: list[dict[str, Any]] = []
    for history in HISTORIES:
        b0 = by_pair[(history, METHODS[0])]
        state = b0["state_inspection"]
        status = str(state.get("status"))
        if status == "FAIL":
            boundary = "NO_ACTIVE_EXPECTED_CURRENT_EDGE"
        elif status == "NOT_PROVABLE":
            boundary = "EXPECTED_TOKEN_ONLY_UNRELATED_TO_QUESTION_STATE"
        elif status == "SUMMARY_ONLY":
            boundary = "ENTITY_SUMMARY_ONLY"
        elif status == "AMBIGUOUS":
            boundary = "STRUCTURAL_ACTIVE_GROUP_MULTIPLICITY"
        else:
            boundary = "NONE"
        failure_boundaries.append(
            {
                "history_id": history,
                "b0_status": status,
                "b0_failure_boundary": boundary,
                "active_expected_edge_count": state.get("active_expected_edge_count"),
                "inactive_expected_edge_count": state.get("inactive_expected_edge_count"),
                "unrelated_expected_match_count": state.get("unrelated_expected_match_count", 0),
                "reader_diagnostic_match": bool(b0.get("reader_diagnostic", {}).get("expected_match")),
            }
        )

    analysis = {
        "schema_version": "sfwb.v1.3.longmemeval-state-qa-analysis.v1",
        "status": "READ_ONLY_GRAPH_STATE_QA_ANALYZED",
        "source_results": str(results_path),
        "source_results_payload_sha256": results.get("payload_sha256"),
        "direct_graph_predicate": {
            "authoritative": True,
            "reader_answer_substitution": False,
            "old_new_value_status": "NOT_PROVABLE",
            "predicate_version": "longmemeval-current-state-v1",
        },
        "coverage": {
            "paired_history_count": 4,
            "row_count": 8,
            "full_operation_freeze_count": 72,
            "completed_graph_coverage_count": 4,
        },
        "failure_boundaries": failure_boundaries,
        "paired_state_divergence": [
            row for row in paired if bool(row.get("state_divergence"))
        ],
        "reader_diagnostic": {
            "rows": 8,
            "expected_match_count": sum(bool(row.get("reader_diagnostic", {}).get("expected_match")) for row in rows),
            "semantic_authority": "NONE",
        },
        "causal_chain": {
            "unordered_admission": "NOT_EVALUATED",
            "predecessor_not_durable": "NOT_EVALUATED",
            "different_graph_observation": "NOT_ESTABLISHED",
            "different_semantic_request": "NOT_ESTABLISHED",
            "current_state_consequence": "NOT_ESTABLISHED",
        },
        "decision": "STOP_LONGMEMEVAL_B0_STATE_PREDICATE_INELIGIBLE",
        "decision_scope": "existing_four_completed_graph_pairs_only",
        "b1_unsafe_claim_authorized": False,
        "v5_authorized": False,
        "construction_calls": 0,
        "graph_writes": 0,
    }
    write_new_json(root / "state_qa_analysis.json", analysis)

    lines = [
        "# LongMemEval Graph-Only State QA Analysis",
        "",
        "Decision: `STOP_LONGMEMEVAL_B0_STATE_PREDICATE_INELIGIBLE`",
        "",
        "Scope is the four already completed B0/B1 graph pairs only. The raw",
        "operation freeze still contains 72 structural LongMemEval-S cases; no",
        "new construction was run in this lane.",
        "",
        "The direct temporal graph predicate is authoritative. Reader output from",
        "8002 is diagnostic only and cannot turn a missing graph current fact into",
        "a PASS. Graphiti search used the guarded read-only path with embedding 8003.",
        "",
        "| History | B0 state | B1 state | First boundary |",
        "|---|---|---|---|",
    ]
    by_history = {str(row["history_id"]): row for row in failure_boundaries}
    for history in HISTORIES:
        pair = next(row for row in paired if row["history_id"] == history)
        lines.append(
            f"| `{history}` | `{pair['b0_status']}` | `{pair['b1_status']}` | `{by_history[history]['b0_failure_boundary']}` |"
        )
    lines.extend(
        [
            "",
            "No history has `B0 PASS -> B1 FAIL`; no direct graph-state divergence",
            "was established. The 8002 Reader matched the official answer on 0/8",
            "rows, which confirms that retrieval/Reader output is not a valid",
            "substitute for current-state semantics here.",
            "",
            "The result does not authorize a B1 unsafe claim, a 72-history live",
            "expansion, scheduler work, or V5.",
            "",
        ]
    )
    markdown_path = root / "state_qa_analysis.md"
    if markdown_path.exists():
        raise RuntimeError(f"ARTIFACT_ALREADY_EXISTS:{markdown_path}")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")

    files = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in (
            "state_qa_manifest.json",
            "state_qa_results.json",
            "state_qa_summary.json",
            "state_qa_decision.txt",
            "state_qa_analysis.json",
            "state_qa_analysis.md",
        )
    }
    seal = {
        "schema_version": "sfwb.v1.3.longmemeval-state-qa-seal.v1",
        "status": "READ_ONLY_GRAPH_STATE_QA_SEALED",
        "decision": analysis["decision"],
        "files": files,
        "construction_calls": 0,
        "graph_writes": 0,
        "v5_started": False,
    }
    write_new_json(root / "state_qa_seal.json", seal)
    print(json.dumps({"status": analysis["status"], "decision": analysis["decision"], "output": str(root)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
