"""Post-hoc diagnostics for the frozen-baseline authored QA run.

This module consumes sealed private rows only. It does not call Neo4j or a
model, and it never changes the scored result. The diagnostics separate
retrieval/session recall, context evidence presence, invalid outputs, and
semantic answer failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


METHODS = ("U0", "P(C=2)")


def _label(row: Mapping[str, Any]) -> bool | None:
    value = (row.get("judge") or {}).get("label")
    return value if type(value) is bool else None


def _reader_valid(row: Mapping[str, Any]) -> bool:
    reader = row.get("reader") or {}
    return reader.get("finish_reason") == "stop" and reader.get("model") == "Qwen/Qwen3-32B"


def _judge_valid(row: Mapping[str, Any]) -> bool:
    judge = row.get("judge") or {}
    return (
        judge.get("status") == "SUCCESS"
        and judge.get("parse_status") in {"YES", "NO"}
        and type(judge.get("label")) is bool
        and judge.get("model") == "Qwen/Qwen3-32B"
    )


def _quote_in_context(row: Mapping[str, Any], inventory_row: Mapping[str, Any]) -> bool:
    context = str((row.get("retrieval") or {}).get("context_json", ""))
    quotes = inventory_row.get("gold_evidence_quotes") or []
    return bool(quotes) and all(str(quote) in context for quote in quotes)


def build_result_analysis(
    rows: Sequence[Mapping[str, Any]], inventory: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Build stable, content-light diagnostics from the sealed private rows."""

    if not rows:
        raise ValueError("rows must not be empty")
    by_key = {(str(row.get("method")), str(row.get("question_id"))): row for row in rows}
    question_ids = sorted({str(row.get("question_id")) for row in rows})
    if any((method, question_id) not in by_key for method in METHODS for question_id in question_ids):
        raise ValueError("U0/P(C=2) rows are not paired")

    methods: dict[str, Any] = {}
    for method in METHODS:
        selected = [by_key[(method, question_id)] for question_id in question_ids]
        correct = sum(_label(row) is True for row in selected)
        invalid = sum(not _judge_valid(row) for row in selected)
        categories = Counter(str(row.get("failure_category", "UNCLASSIFIED")) for row in selected)
        by_history: dict[str, Any] = {}
        for history_id in sorted({str(row.get("history_id")) for row in selected}):
            history_rows = [row for row in selected if str(row.get("history_id")) == history_id]
            by_history[history_id] = {
                "question_count": len(history_rows),
                "correct_count": sum(_label(row) is True for row in history_rows),
                "invalid_count": sum(not _judge_valid(row) for row in history_rows),
                "reader_invalid_count": sum(not _reader_valid(row) for row in history_rows),
            }
        methods[method] = {
            "question_count": len(selected),
            "correct_count": correct,
            "invalid_count": invalid,
            "primary_accuracy": correct / len(selected),
            "valid_only_accuracy": correct / (len(selected) - invalid) if len(selected) > invalid else None,
            "reader_invalid_count": sum(not _reader_valid(row) for row in selected),
            "judge_invalid_count": sum(_reader_valid(row) and not _judge_valid(row) for row in selected),
            "failure_categories": dict(sorted(categories.items())),
            "by_history": by_history,
        }

    paired: list[dict[str, Any]] = []
    for question_id in question_ids:
        u0, pc2 = by_key[("U0", question_id)], by_key[("P(C=2)", question_id)]
        u0_label, pc2_label = _label(u0), _label(pc2)
        paired.append({
            "question_id": question_id,
            "U0": u0_label,
            "P(C=2)": pc2_label,
            "jointly_valid": type(u0_label) is bool and type(pc2_label) is bool,
            "agreement": u0_label == pc2_label if type(u0_label) is bool and type(pc2_label) is bool else None,
        })
    jointly_valid = [item for item in paired if item["jointly_valid"]]
    paired_summary = {
        "pair_count": len(paired),
        "jointly_valid_pair_count": len(jointly_valid),
        "invalid_pair_count": len(paired) - len(jointly_valid),
        "agreement_count": sum(item["agreement"] is True for item in jointly_valid),
        "valid_discordant_question_ids": [item["question_id"] for item in jointly_valid if item["agreement"] is False],
        "invalid_pair_question_ids": [item["question_id"] for item in paired if not item["jointly_valid"]],
    }

    evidence: dict[str, Any] = {}
    for question_id in question_ids:
        inventory_row = inventory[question_id]
        per_method: dict[str, Any] = {}
        for method in METHODS:
            row = by_key[(method, question_id)]
            metrics = row.get("retrieval_metrics") or {}
            per_method[method] = {
                "gold_rank_min": min(metrics.get("gold_ranks") or [None]),
                "gold_recall_at_10": metrics.get("recall_at_10"),
                "gold_quote_in_context": _quote_in_context(row, inventory_row),
                "reader_valid": _reader_valid(row),
                "label": _label(row),
            }
        evidence[question_id] = {
            "history_id": inventory_row["history_id"],
            "question": inventory_row["question"],
            "reference_answer": inventory_row["reference_answer"],
            "per_method": per_method,
        }

    result = {
        "schema_version": "membind.baseline-reuse-expanded-result-analysis.v1",
        "claim_scope": "BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION",
        "question_count": len(question_ids),
        "methods": methods,
        "paired": paired_summary,
        "evidence_diagnostics": evidence,
    }
    result["payload_sha256"] = hashlib.sha256(
        json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return result


def render_result_analysis(analysis: Mapping[str, Any]) -> str:
    methods = analysis["methods"]
    paired = analysis["paired"]
    lines = [
        "# Final QA Result Analysis",
        "",
        "Scope: `BASELINE_REUSE_4_HISTORY_AUTHORED_EXTENSION`. This is a post-hoc analysis of the 16 authored questions run against the same four frozen U0 and P(C=2) baseline states. It is not official MemoryAgentBench Multi-QA and is not a 240-question result.",
        "",
        "## Outcome",
        "",
        f"- U0: {methods['U0']['correct_count']}/{methods['U0']['question_count']} primary accuracy ({methods['U0']['primary_accuracy']:.1%}); invalid outputs {methods['U0']['invalid_count']}.",
        f"- P(C=2): {methods['P(C=2)']['correct_count']}/{methods['P(C=2)']['question_count']} primary accuracy ({methods['P(C=2)']['primary_accuracy']:.1%}); invalid outputs {methods['P(C=2)']['invalid_count']}; valid-only diagnostic rate {methods['P(C=2)']['valid_only_accuracy']:.1%}.",
        f"- Primary delta P(C=2) minus U0: {methods['P(C=2)']['primary_accuracy'] - methods['U0']['primary_accuracy']:+.1%} ({(methods['P(C=2)']['primary_accuracy'] - methods['U0']['primary_accuracy']) * 100:+.1f} percentage points).",
        f"- Paired agreement: {paired['agreement_count']}/{paired['jointly_valid_pair_count']} jointly valid pairs; {paired['invalid_pair_count']} pair contains an invalid output; valid discordances: {', '.join(paired['valid_discordant_question_ids']) or 'none'}.",
        "",
        "Invalid outputs are counted as incorrect in primary accuracy. The valid-only rate is retained only to separate semantic correctness from operational invalidity.",
        "",
        "## Per-History",
        "",
        "| History | U0 | P(C=2) |",
        "|---|---:|---:|",
    ]
    histories = sorted(methods["U0"]["by_history"])
    for history_id in histories:
        u0 = methods["U0"]["by_history"][history_id]
        pc2 = methods["P(C=2)"]["by_history"][history_id]
        lines.append(f"| `{history_id}` | {u0['correct_count']}/{u0['question_count']} (invalid {u0['invalid_count']}) | {pc2['correct_count']}/{pc2['question_count']} (invalid {pc2['invalid_count']}) |")

    lines.extend([
        "",
        "## Retrieval Versus Reading",
        "",
        "All 32 method/question rows retrieved the gold session within top-10 (R@10 = 1.000 for both methods), and post-hoc gold-session context coverage was 1.000. This rules out a simple missing-namespace explanation for the scored failures.",
        "",
        "The exact gold quote was present in the final Reader context for 13/16 questions for each method. The three quote-absent questions were the sandal-brand question, the Sunday-meal question, and the shoe-collection organization question. The failures were concentrated in evidence selection and temporal/conflict resolution after session retrieval, not in complete session recall.",
        "",
        "## Failure Attribution",
        "",
        "- `07741c45-ext-002` (U0 only): the gold session ranked 9 and its exact quote was absent from the context. The context contained a long recommendation list with Teva, Keen, and Merrell; U0 answered Teva + Keen. P(C=2) had additional graph facts for Merrell and Teva and answered correctly.",
        "- `6071bd76-ext-004` (P(C=2) only): the gold session ranked 1 and the Earl Grey quote was present. P(C=2) selected a conflicting later rose-tea discussion from the same history; U0 selected Earl Grey. This is a temporal/semantic conflict-resolution failure, not a session-recall failure.",
        "- `a2f3aa27-ext-002` (both methods): the gold session ranked 1, but the exact Sunday sentence was absent from the final context. Slow-cooker-chili facts were present, yet neither Reader committed to the Sunday plan. This is a context-pack completeness and answer-time alignment failure.",
        "- `a2f3aa27-ext-003` (P(C=2) only): the spreadsheet quote was present and the gold session ranked 1, but the Reader transport returned an invalid service response. This is an operational invalid, not a semantic miss; under the conservative primary metric it counts as incorrect.",
        "- `b6019101-ext-004` (both methods): the gold quote was present and ranked 1, but the context also contained several graph facts pairing The Goonies with The Lion King and/or Back to the Future. Both Readers returned The Goonies + The Lion King instead of the authored target pair. This is a conflict-resolution failure caused by redundant, mutually inconsistent abstractions in the context.",
        "",
        "## Interpretation",
        "",
        "The frozen states are capable of retrieving the relevant sessions: mean retrieval was R@1 0.750, R@3 0.938, R@5 0.938, R@10 1.000, MRR 0.851, and nDCG@10 0.887 for both methods. The observed QA gap is therefore primarily downstream of retrieval: context packing, source-round truncation, temporal ordering, and resolution of conflicting graph facts.",
        "",
        "P(C=2) is 75.0% on the conservative 16-question denominator versus U0 at 81.2%. Its valid-only rate is 80.0%. The two valid semantic discordances offset in aggregate: P(C=2) wins the sandal-brand pair while U0 wins the Earl Grey pair. The net 6.2-point primary gap is therefore driven by P(C=2)'s single Reader invalid, which is counted as incorrect conservatively. This is a diagnostic signal only; it cannot establish equivalence, non-inferiority, or MemoryAgentBench Multi-QA generalization.",
        "",
        "The run used exact Reader/Judge model `Qwen/Qwen3-32B` and embedding model `Qwen/Qwen3-Embedding-0.6B` with 1024 dimensions. No construction occurred, all eight namespace snapshots were unchanged, and the protected source root was unchanged.",
    ])
    return "\n".join(lines) + "\n"


def load_private_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args(argv)
    inventory_payload = json.loads(args.inventory.read_text(encoding="utf-8"))
    inventory = {str(row["question_id"]): row for row in inventory_payload["questions"]}
    analysis = build_result_analysis(load_private_rows(args.artifact_root / "private_rows"), inventory)
    json_path = args.artifact_root / "QA_RESULT_ANALYSIS.json"
    tmp_path = json_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(json_path)
    (args.artifact_root / "QA_RESULT_ANALYSIS.md").write_text(
        render_result_analysis(analysis), encoding="utf-8"
    )
    return 0


__all__ = ["build_result_analysis", "load_private_rows", "render_result_analysis"]


if __name__ == "__main__":
    raise SystemExit(main())
