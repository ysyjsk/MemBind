#!/usr/bin/env python3
"""Verify sealed U0/A0/P/graph-QA artifacts and write the final report.

This command is strictly offline.  It performs no Neo4j, embedding, Reader,
Judge, or construction calls; all numbers are re-derived from sealed files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.baseline_suite import (
    DEVELOPMENT_HISTORIES,
    baseline_block_namespace,
    build_baseline_suite_plan,
)
from paper_eval.baseline_suite_artifacts import inspect_baseline_block
from paper_eval.baseline_suite_u0_reuse import verify_reusable_u0_run
from paper_eval.development_baseline_report import (
    build_development_baseline_report,
    render_development_baseline_markdown,
)
from paper_eval.graph_quality_suite import (
    GraphQualitySuiteError,
    METHOD_SLUGS,
    discover_graph_quality_targets,
    graph_quality_targets_sha256,
    load_or_restore_question_bundle,
    summarize_graph_quality_results,
    verify_public_target,
)
from paper_eval.native_baseline_runner import (
    build_native_baseline_plan,
    verify_history_result,
)


NATIVE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"
SUITE_RUNS_ROOT = PROJECT / "artifacts/paper_eval/baseline_suite/runs"
GRAPH_QUALITY_RUNS_ROOT = (
    PROJECT / "artifacts/paper_eval/graph_quality_overlay/runs"
)
REPORT_RUNS_ROOT = PROJECT / "artifacts/paper_eval/development_report/runs"
LIVE_METHOD_SLUGS = {"A0": "a0", "P(C=2)": "pc2"}


class ReportInputError(ValueError):
    """A sealed source artifact failed deterministic report verification."""


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReportInputError(f"{label} contains a duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ReportInputError(f"{label} is unreadable") from None
    if not isinstance(value, dict):
        raise ReportInputError(f"{label} is invalid")
    return value


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ReportInputError(f"{label} is unreadable") from None
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ReportInputError(f"{label} contains invalid JSON") from None
        if not isinstance(value, dict):
            raise ReportInputError(f"{label} contains an invalid row")
        rows.append(value)
    return rows


def _freshness_samples(
    rows: list[Mapping[str, Any]], *, expected_count: int
) -> list[int]:
    samples: list[int] = []
    for row in rows:
        latency = row.get("latency_ns")
        if not isinstance(latency, Mapping):
            raise ReportInputError("per-episode latency is missing")
        value = latency.get("freshness")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReportInputError("per-episode freshness is invalid")
        samples.append(value)
    if len(samples) != expected_count:
        raise ReportInputError("per-episode freshness inventory drift")
    return samples


def _u0_rows(native_run_id: str) -> list[dict[str, Any]]:
    verify_reusable_u0_run(NATIVE_RUNS_ROOT, native_run_id)
    plan = build_native_baseline_plan(native_run_id)
    rows: list[dict[str, Any]] = []
    for history in plan.histories:
        root = NATIVE_RUNS_ROOT / native_run_id / history.history_id
        result = verify_history_result(
            _read_object(root / "history_result.json", label="U0 history result"),
            expected_plan=history,
        )
        aggregate = result.get("aggregate")
        final = result.get("final_namespace_observation")
        if not isinstance(aggregate, Mapping) or not isinstance(final, Mapping):
            raise ReportInputError("U0 aggregate is incomplete")
        metrics = aggregate.get("metrics")
        work = aggregate.get("work_volume")
        episode_metrics = aggregate.get("episode_metrics")
        if (
            not isinstance(metrics, Mapping)
            or not isinstance(work, Mapping)
            or not isinstance(episode_metrics, list)
        ):
            raise ReportInputError("U0 aggregate evidence is incomplete")
        episode_count = int(aggregate["episode_count"])
        rows.append(
            {
                "method": "U0",
                "history_id": history.history_id,
                "episode_count": episode_count,
                "metrics": {
                    **dict(metrics),
                    "direct_violations_status": "MEASURED",
                },
                "freshness_samples_ns": _freshness_samples(
                    episode_metrics,
                    expected_count=episode_count,
                ),
                "work_volume": dict(work),
                "final_graph": {
                    "node_count": final["node_count"],
                    "relationship_count": final["relationship_count"],
                    "episodic_count": final["episode_count"],
                    "episode_names_match_expected": final[
                        "episode_names_match_expected"
                    ],
                },
                "schedule_summary": {
                    "configured_worker_count": 1,
                    "max_active_calls": 1,
                    "whole_update_interval_overlap_observed": False,
                },
                "result_payload_sha256": result["payload_sha256"],
            }
        )
    return rows


def _live_rows(suite_run_id: str, native_run_id: str) -> list[dict[str, Any]]:
    suite_root = SUITE_RUNS_ROOT / suite_run_id
    report = _read_object(
        suite_root / "THREE_BASELINE_RESULTS.json",
        label="three-baseline report",
    )
    observed_hash = report.get("payload_sha256")
    if observed_hash != payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    ):
        raise ReportInputError("three-baseline report hash mismatch")
    if (
        report.get("status") != "PASS"
        or report.get("run_id") != suite_run_id
        or not isinstance(report.get("u0"), Mapping)
        or report["u0"].get("source_run_id") != native_run_id
    ):
        raise ReportInputError("three-baseline report identity drift")
    blocks = report.get("blocks")
    if not isinstance(blocks, list):
        raise ReportInputError("three-baseline block inventory is missing")
    plan = build_baseline_suite_plan(
        suite_run_id,
        mode="development",
        reuse_u0_run=native_run_id,
    )
    bases = {
        (str(value["method"]), str(value["history_id"])): value
        for value in plan["blocks"]
        if value["method"] in LIVE_METHOD_SLUGS
    }
    expected = [
        (method, history_id)
        for method in ("A0", "P(C=2)")
        for history_id in DEVELOPMENT_HISTORIES
    ]
    if [
        (value.get("method"), value.get("history_id"))
        for value in blocks
        if isinstance(value, Mapping)
    ] != expected:
        raise ReportInputError("three-baseline block inventory drift")
    rows: list[dict[str, Any]] = []
    for projected in blocks:
        if not isinstance(projected, Mapping):
            raise ReportInputError("three-baseline block row is invalid")
        method = str(projected["method"])
        history_id = str(projected["history_id"])
        attempt = projected.get("attempt_ordinal")
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise ReportInputError("three-baseline attempt is invalid")
        block = dict(bases[(method, history_id)])
        block["attempt_ordinal"] = attempt
        block["namespace"] = baseline_block_namespace(
            suite_run_id=suite_run_id,
            method=method,
            history_id=history_id,
            attempt_ordinal=attempt,
        )
        root = (
            suite_root
            / "blocks"
            / LIVE_METHOD_SLUGS[method]
            / history_id
            / f"attempt-{attempt:03d}"
        )
        inspected = inspect_baseline_block(root, block)
        if (
            inspected.get("status") != "completed"
            or inspected.get("artifacts_verified") is not True
        ):
            raise ReportInputError("three-baseline block is not verified")
        result = inspected.get("result")
        if not isinstance(result, Mapping):
            raise ReportInputError("three-baseline block result is missing")
        payload = result.get("payload")
        if not isinstance(payload, Mapping):
            raise ReportInputError("three-baseline block payload is missing")
        episode_count = int(payload["episode_count"])
        samples = _freshness_samples(
            _read_jsonl(
                root / "telemetry/per_episode_metrics.jsonl",
                label="live per-episode metrics",
            ),
            expected_count=episode_count,
        )
        if result.get("result_payload_sha256") != projected.get(
            "result_payload_sha256"
        ):
            raise ReportInputError("three-baseline projected result hash drift")
        rows.append(
            {
                "method": method,
                "history_id": history_id,
                "episode_count": episode_count,
                "metrics": dict(payload["metrics"]),
                "freshness_samples_ns": samples,
                "work_volume": dict(payload["work_volume"]),
                "final_graph": dict(payload["final_graph"]),
                "schedule_summary": dict(payload["schedule_summary"]),
                "result_payload_sha256": result["result_payload_sha256"],
            }
        )
    return rows


def _verified_graph_quality_report(
    *, overlay_run_id: str, native_run_id: str, suite_run_id: str
) -> dict[str, Any]:
    targets = discover_graph_quality_targets(
        native_runs_root=NATIVE_RUNS_ROOT,
        suite_runs_root=SUITE_RUNS_ROOT,
        native_run_id=native_run_id,
        suite_run_id=suite_run_id,
    )
    root = GRAPH_QUALITY_RUNS_ROOT / overlay_run_id
    report = _read_object(
        root / "GRAPH_QUALITY_RESULTS.json",
        label="graph-quality report",
    )
    if report.get("payload_sha256") != payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    ):
        raise ReportInputError("graph-quality report hash mismatch")
    if (
        report.get("schema_version")
        != "membind.paper-eval-v3.graph-quality-report.v1"
        or report.get("overlay_run_id") != overlay_run_id
        or report.get("status") != "PASS"
        or report.get("target_count") != len(targets)
    ):
        raise ReportInputError("graph-quality report identity drift")
    try:
        expected_targets_sha256 = graph_quality_targets_sha256(targets)
    except GraphQualitySuiteError as error:
        raise ReportInputError(
            f"graph-quality target inventory is invalid: {type(error).__name__}"
        ) from None
    if report.get("targets_sha256") != expected_targets_sha256:
        raise ReportInputError("graph-quality target inventory hash drift")
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        raise ReportInputError("graph-quality summary identity is missing")
    runtime_identity = summary.get("runtime_identity")
    quality_identity = summary.get("quality_identity")
    if (
        not isinstance(runtime_identity, Mapping)
        or not runtime_identity
        or summary.get("runtime_identity_sha256")
        != payload_sha256(runtime_identity)
        or not isinstance(quality_identity, Mapping)
    ):
        raise ReportInputError("graph-quality summary identity drift")
    units = report.get("units")
    if not isinstance(units, list) or len(units) != len(targets):
        raise ReportInputError("graph-quality unit inventory drift")
    public_rows: list[dict[str, Any]] = []
    for unit, target in zip(units, targets, strict=True):
        if not isinstance(unit, Mapping):
            raise ReportInputError("graph-quality unit is invalid")
        attempt = unit.get("attempt_ordinal")
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise ReportInputError("graph-quality attempt is invalid")
        if (
            unit.get("method") != target.method
            or unit.get("history_id") != target.history_id
        ):
            raise ReportInputError("graph-quality target binding drift")
        attempt_root = (
            root
            / "units"
            / METHOD_SLUGS[target.method]
            / target.history_id
            / f"attempt-{attempt:03d}"
        )
        public = load_or_restore_question_bundle(attempt_root)
        if public.get("payload_sha256") != unit.get("public_payload_sha256"):
            raise ReportInputError("graph-quality public result hash drift")
        try:
            verified_public = verify_public_target(
                public,
                target,
                overlay_run_id=overlay_run_id,
                runtime_identity=runtime_identity,
                quality_identity=quality_identity,
            )
        except GraphQualitySuiteError:
            raise ReportInputError("graph-quality target identity drift") from None
        public_rows.append(verified_public)
    if summarize_graph_quality_results(public_rows) != report.get("summary"):
        raise ReportInputError("graph-quality summary recomputation drift")
    return report


def _write_immutable_text(path: Path, value: str) -> None:
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ReportInputError("existing Markdown report is unreadable") from None
        if existing != value:
            raise ReportInputError("existing Markdown report drift")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
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


def write_report(
    *,
    report_run_id: str,
    native_run_id: str,
    suite_run_id: str,
    overlay_run_id: str,
    markdown_output: Path,
) -> dict[str, Any]:
    """Verify every input, then atomically publish JSON and Markdown."""

    graph_report = _verified_graph_quality_report(
        overlay_run_id=overlay_run_id,
        native_run_id=native_run_id,
        suite_run_id=suite_run_id,
    )
    baseline_rows = [
        *_u0_rows(native_run_id),
        *_live_rows(suite_run_id, native_run_id),
    ]
    artifact_paths = {
        "native": str(
            (NATIVE_RUNS_ROOT / native_run_id).relative_to(REPOSITORY)
        ),
        "suite": str((SUITE_RUNS_ROOT / suite_run_id).relative_to(REPOSITORY)),
        "graph_quality": str(
            (GRAPH_QUALITY_RUNS_ROOT / overlay_run_id).relative_to(REPOSITORY)
        ),
    }
    report = build_development_baseline_report(
        report_run_id=report_run_id,
        native_run_id=native_run_id,
        suite_run_id=suite_run_id,
        overlay_run_id=overlay_run_id,
        baseline_rows=baseline_rows,
        graph_quality_report=graph_report,
        artifact_paths=artifact_paths,
    )
    json_path = REPORT_RUNS_ROOT / report_run_id / "REPORT.json"
    if json_path.exists():
        if _read_object(json_path, label="existing development report") != report:
            raise ReportInputError("existing development JSON report drift")
    else:
        atomic_write_json(json_path, report)
    _write_immutable_text(
        Path(markdown_output),
        render_development_baseline_markdown(report),
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write the offline U0/A0/P development experiment report."
    )
    parser.add_argument("report_run_id")
    parser.add_argument("--native-run", required=True)
    parser.add_argument("--suite-run", required=True)
    parser.add_argument("--overlay-run", required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = write_report(
            report_run_id=args.report_run_id,
            native_run_id=args.native_run,
            suite_run_id=args.suite_run,
            overlay_run_id=args.overlay_run,
            markdown_output=args.markdown_output,
        )
    except BaseException as error:
        print(
            "STOP offline_report error_class="
            f"{type(error).__module__}.{type(error).__qualname__}",
            flush=True,
        )
        return 1
    print(
        f"PASS report_run_id={args.report_run_id} "
        f"sha256={report['payload_sha256']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
