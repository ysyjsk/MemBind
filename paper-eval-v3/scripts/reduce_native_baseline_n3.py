#!/usr/bin/env python3
"""Build the Native N3 screen from durable filesystem evidence only.

This command deliberately has no Graphiti, model, Reader, Judge, or Neo4j
runtime dependency. It loads the fixed U0 history artifacts, delegates every
scientific validation and decision to ``reduce_native_baseline_n3``, and then
writes deterministic presentation artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.native_baseline_n3 import reduce_native_baseline_n3
from paper_eval.native_baseline_runner import DEVELOPMENT_HISTORIES


DEFAULT_RUN_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
DEFAULT_OUTPUT_DIR = PROJECT / "artifacts/paper_eval/native_baseline"

LEVEL_ZERO_FILES = {
    "spans": "spans.jsonl",
    "events": "events.jsonl",
    "llm": "llm.jsonl",
    "embedding": "embedding.jsonl",
    "db": "db.jsonl",
    "graph_work": "graph_work.jsonl",
    "queue": "queue.jsonl",
    "per_episode": "per_episode_metrics.jsonl",
}

_ALLOWED_DECISIONS = {
    "HEALTHY_FOR_NEXT_BASELINE",
    "DIAGNOSE_BEFORE_METHODS",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"json_artifact_unreadable:{path}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"json_artifact_not_object:{path}")
    return value


def _load_jsonl(path: Path, *, stream: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError(f"level_zero_file_missing:{stream}:{path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="ascii") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError(
                        f"level_zero_row_not_object:{stream}:{line_number}"
                    )
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"level_zero_file_unreadable:{stream}:{path}") from error
    return rows


def _load_history_evidence(*, run_id: str, run_root: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for history_id in DEVELOPMENT_HISTORIES:
        root = run_root / run_id / history_id
        checkpoint_path = root / "checkpoint.json"
        result_path = root / "history_result.json"
        # A history without both durable terminal surfaces is ordinary N3
        # incompleteness. Omitting it lets the reducer issue the frozen reason.
        if not checkpoint_path.is_file() or not result_path.is_file():
            continue
        checkpoint = _load_json(checkpoint_path)
        result = _load_json(result_path)
        raw_rows: dict[str, list[dict[str, Any]]] = {}
        if checkpoint.get("status") == "completed" or all(
            (root / filename).is_file() for filename in LEVEL_ZERO_FILES.values()
        ):
            raw_rows = {
                stream: _load_jsonl(root / filename, stream=stream)
                for stream, filename in LEVEL_ZERO_FILES.items()
            }
        evidence.append(
            {
                "checkpoint": checkpoint,
                "history_result": result,
                "raw_rows": raw_rows,
            }
        )
    return evidence


def _verify_reducer_output(report: Mapping[str, Any], *, run_id: str) -> None:
    if report.get("run_id") != run_id:
        raise RuntimeError("reducer_run_identity_mismatch")
    stored_hash = report.get("payload_sha256")
    body = {name: value for name, value in report.items() if name != "payload_sha256"}
    if not isinstance(stored_hash, str) or stored_hash != payload_sha256(body):
        raise RuntimeError("reducer_payload_hash_mismatch")
    eligibility = report.get("eligibility")
    decision = report.get("decision")
    if not isinstance(eligibility, bool):
        raise RuntimeError("reducer_eligibility_not_boolean")
    if not eligibility and decision is not None:
        raise RuntimeError("ineligible_reducer_output_authorizes_decision")
    if eligibility and decision not in _ALLOWED_DECISIONS:
        raise RuntimeError("eligible_reducer_output_has_invalid_decision")


def _format_number(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _markdown_report(report: Mapping[str, Any]) -> str:
    eligible = report["eligibility"] is True
    verdict = str(report["decision"]) if eligible else "INCOMPLETE"
    lines = [
        "# Native U0 Development Baseline Screen",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Method: `{report.get('method', 'U0')}`",
        f"- Aggregation unit: `{report.get('aggregation_unit')}`",
        f"- Scientific scope: `{report.get('scientific_scope')}`",
        f"- Eligibility: `{str(eligible).lower()}`",
        f"- Verdict: `{verdict}`",
        "",
    ]
    reasons = (
        report.get("decision_reasons", [])
        if eligible
        else report.get("ineligibility_reasons", [])
    )
    lines.extend(["## Reasons", ""])
    if reasons:
        lines.extend(f"- `{reason}`" for reason in reasons)
    else:
        lines.append("- None")

    histories = report.get("per_history", [])
    if histories:
        lines.extend(
            [
                "",
                "## Per-History Headline And Secondary Metrics",
                "",
                "| History | Episodes | QA | R@10 | Violations | P95 freshness ns | P99 freshness ns | Goodput eps | Makespan ns |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in histories:
            headline = row["headline_metrics"]
            secondary = row["secondary_metrics"]
            lines.append(
                "| {history} | {episodes} | {qa} | {recall} | {violations} | "
                "{p95} | {p99} | {goodput} | {makespan} |".format(
                    history=row["history_id"],
                    episodes=row["episode_count"],
                    qa=_format_number(headline["qa_accuracy"]),
                    recall=_format_number(headline["evidence_recall_at_10"]),
                    violations=_format_number(headline["direct_violations"]),
                    p95=_format_number(headline["p95_freshness_ns"]),
                    p99=_format_number(secondary["p99_freshness_ns"]),
                    goodput=_format_number(headline["successful_goodput"]),
                    makespan=_format_number(headline["makespan_ns"]),
                )
            )

    lines.extend(
        [
            "",
            "## Interpretation Boundary",
            "",
            "This is a descriptive development-only screen. It does not establish a MemBind benefit claim, statistical significance, or held-out paper result.",
            "",
            "The JSON screen is the canonical N3 output; this Markdown file is a deterministic presentation of that reducer output.",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _decision_artifact(report: Mapping[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.native-baseline-decision.v1",
        "run_id": report["run_id"],
        "method": report["method"],
        "repeat_id": report["repeat_id"],
        "status": "completed",
        "decision": report["decision"],
        "decision_reasons": list(report["decision_reasons"]),
        "source_screen_payload_sha256": report["payload_sha256"],
        "scientific_scope": report["scientific_scope"],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def generate_outputs(*, run_id: str, run_root: Path, output_dir: Path) -> dict[str, Any]:
    evidence = _load_history_evidence(run_id=run_id, run_root=run_root)
    report = reduce_native_baseline_n3(run_id=run_id, history_evidence=evidence)
    _verify_reducer_output(report, run_id=run_id)

    decision_path = output_dir / "NATIVE_BASELINE_DECISION.json"
    if report["eligibility"] is not True and decision_path.exists():
        raise RuntimeError("incomplete_screen_cannot_coexist_with_existing_decision")

    atomic_write_json(output_dir / "NATIVE_BASELINE_SCREEN.json", report)
    _atomic_write_text(output_dir / "NATIVE_BASELINE_REPORT.md", _markdown_report(report))
    if report["eligibility"] is True:
        atomic_write_json(decision_path, _decision_artifact(report))
    return dict(report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    report = generate_outputs(
        run_id=args.run_id,
        run_root=args.run_root,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {
                "run_id": report["run_id"],
                "eligibility": report["eligibility"],
                "decision": report["decision"],
                "screen_payload_sha256": report["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
