"""Human-readable final QA analysis assembled from verified reducer output."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def _percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.2f}%"


def _metric(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.4f}"


def render_final_report(
    paired: Mapping[str, Any],
    *,
    run_id: str,
    dataset_manifest_sha256: str,
    freeze_sha256: str,
    live_executed: bool = False,
) -> str:
    """Render a report that keeps quality, retrieval and infrastructure separate."""

    u0 = paired["u0"]
    mb = paired["membind"]
    table = paired.get("paired_disagreements", {})
    lines = [
        "# Final MAB Quality v2 QA Report",
        "",
        f"- Run: `{run_id}`",
        f"- Generated: `{datetime.now(UTC).isoformat()}`",
        f"- Dataset manifest: `{dataset_manifest_sha256}`",
        f"- Freeze identity: `{freeze_sha256}`",
        f"- Live calls executed: `{str(bool(live_executed)).lower()}`",
        "- Claim scope: paired multi-QA diagnostic; invalid judge rows are not counted as incorrect.",
        "",
        "## Gate",
        "",
        (
            "The same `(context_id, qa_pair_id)` inventory is required for both methods. "
            "Each context is constructed once and its namespace is sealed before QA."
        ),
        "",
        "## Method Summary",
        "",
        "| Method | QA | Valid Judge | Invalid Judge | Accuracy | Cluster Bootstrap 95% CI | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@10 |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in (("U0", u0), ("MemBind", mb)):
        retrieval = summary.get("retrieval", {})
        interval = summary.get("qa_accuracy_cluster_bootstrap", {}).get("ci95")
        interval_text = (
            "N/A"
            if not interval
            else f"{_percent(interval[0])} to {_percent(interval[1])}"
        )
        lines.append(
            "| {name} | {qa} | {valid} | {invalid} | {accuracy} | {interval} | {r1} | {r3} | {r5} | {r10} | {mrr} | {ndcg} |".format(
                name=name,
                qa=summary["qa_count"],
                valid=summary["valid_judge_count"],
                invalid=summary["invalid_judge_count"],
                accuracy=_percent(summary.get("qa_accuracy")),
                interval=interval_text,
                r1=_metric(retrieval.get("recall_at_1")),
                r3=_metric(retrieval.get("recall_at_3")),
                r5=_metric(retrieval.get("recall_at_5")),
                r10=_metric(retrieval.get("recall_at_10")),
                mrr=_metric(retrieval.get("mrr")),
                ndcg=_metric(retrieval.get("ndcg_at_10")),
            )
        )
    lines.extend(
        [
            "",
            "## Paired Outcome",
            "",
            f"- Accuracy delta (MemBind - U0): `{_percent(paired.get('delta_accuracy_membind_minus_u0'))}`",
            "- Context-cluster bootstrap delta 95% CI: `{}`".format(
                "N/A"
                if not paired.get("delta_accuracy_cluster_bootstrap", {}).get("ci95")
                else "{} to {}".format(
                    _percent(paired["delta_accuracy_cluster_bootstrap"]["ci95"][0]),
                    _percent(paired["delta_accuracy_cluster_bootstrap"]["ci95"][1]),
                )
            ),
            f"- Paired valid rows: `{paired.get('paired_valid_count', 0)}`",
            "",
            "| Outcome | Count |",
            "|---|---:|",
        ]
    )
    labels = (
        "both_correct",
        "u0_only_correct",
        "membind_only_correct",
        "both_wrong",
        "invalid_u0",
        "invalid_membind",
    )
    for label in labels:
        lines.append(f"| `{label}` | {table.get(label, 0)} |")
    lines.extend(["", "## Failure Decomposition", ""])
    lines.append("| Method | Failure | Count |")
    lines.append("|---|---|---:|")
    for method, summary in (("U0", u0), ("MemBind", mb)):
        failures = summary.get("failure_decomposition", {})
        if not failures:
            lines.append(f"| {method} | none | 0 |")
        else:
            for failure, count in sorted(failures.items()):
                lines.append(f"| {method} | `{failure}` | {count} |")
    lines.extend(["", "## Question-Type Breakdown", ""])
    lines.append("| Method | Type | QA | Valid Judge | Accuracy |")
    lines.append("|---|---|---:|---:|---:|")
    for method, summary in (("U0", u0), ("MemBind", mb)):
        for question_type, detail in sorted(
            summary.get("question_type_breakdown", {}).items()
        ):
            lines.append(
                f"| {method} | `{question_type}` | {detail['qa_count']} | {detail['valid_judge_count']} | {_percent(detail.get('qa_accuracy'))} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            (
                "This report distinguishes retrieval preservation, valid answer quality, and infrastructure failures. "
                "A non-significant or small paired difference is not an equivalence claim; any non-inferiority margin "
                "must be frozen before observing the full comparison."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_final_report"]
