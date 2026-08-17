"""RED-first contracts for the deterministic methodology document renderer."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.methodology_document import (
    MethodologyDocumentError,
    render_methodology_document,
    restore_methodology_template,
)


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
PENDING_DOCUMENT = REPOSITORY / "\u4e3bmethodology\u8bbe\u8ba1.md"
REAL_REPORT = (
    PROJECT
    / "artifacts/paper_eval/development_report/runs/"
    "report-dev-20260817-001/REPORT.json"
)
REAL_DECISION = (
    PROJECT
    / "artifacts/paper_eval/methodology_finalization/runs/"
    "methodology-dev-20260817-001/METHODOLOGY_DECISION.json"
)
SCRIPT = PROJECT / "scripts/finalize_main_methodology.py"
METHODS = ("U0", "A0", "P(C=2)")


def _seal(body: dict[str, object]) -> dict[str, object]:
    return {**body, "payload_sha256": payload_sha256(body)}


def _method(
    method: str,
    *,
    makespan_ns: int,
    graph_qa: float | None = 0.5,
    valid: int = 4,
) -> dict[str, object]:
    worker_count = 2 if method == "P(C=2)" else 1
    return {
        "history_count": 4,
        "episode_count": 188,
        "successful_goodput_episodes_per_second": 188 / (makespan_ns / 1e9),
        "makespan_ns": makespan_ns,
        "freshness_ns": {"p95": makespan_ns // 2, "p99": makespan_ns},
        "evidence_recall_at_10_macro": 1.0,
        "graph_quality_qa_accuracy": graph_qa,
        "graph_quality_valid_judge_count": valid,
        "graph_quality_invalid_judge_count": 4 - valid,
        "direct_violations": 0 if method == "U0" else None,
        "direct_violations_statuses": (
            ["MEASURED"] if method == "U0" else ["NOT_EVALUATED"]
        ),
        "max_backlog": None if method == "U0" else 49,
        "max_backlog_status": (
            "NOT_APPLICABLE_SERIAL_BASELINE" if method == "U0" else "OBSERVED"
        ),
        "observed_max_active_calls": worker_count,
        "overlap_observed": method == "P(C=2)",
        "configured_worker_counts": [worker_count],
        "work_volume": {
            "llm_logical_calls": 100,
            "llm_input_tokens": 1_000,
            "llm_output_tokens": 200,
            "embedding_calls": 80,
            "embedding_items": 160,
            "db_operations": 300,
            "db_transactions": 188,
            "candidate_count": 400,
        },
        "final_graph": {
            "node_count_sum": 500,
            "relationship_count_sum": 600,
        },
    }


def _report() -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": (
            "membind.paper-eval-v3.development-baseline-report.v1"
        ),
        "status": "PASS",
        "report_run_id": "report-dev-test",
        "native_run_id": "nb-dev-test",
        "suite_run_id": "bs-dev-test",
        "overlay_run_id": "gq-dev-test",
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "method_order": list(METHODS),
        "methods": {
            "U0": _method("U0", makespan_ns=1_000_000_000_000),
            "A0": _method("A0", makespan_ns=1_100_000_000_000),
            "P(C=2)": _method(
                "P(C=2)",
                makespan_ns=800_000_000_000,
                graph_qa=None,
                valid=0,
            ),
        },
        "artifact_paths": {
            "native": "paper-eval-v3/artifacts/native",
            "suite": "paper-eval-v3/artifacts/suite",
            "graph_quality": "paper-eval-v3/artifacts/graph-quality",
        },
    }
    return _seal(body)


def _decision(report: dict[str, object]) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.methodology-decision.v1",
        "status": "PASS",
        "decision_run_id": "methodology-dev-test",
        "scope": "DEVELOPMENT_EXPOSED_DESCRIPTIVE_ONLY",
        "input_bindings": {
            "report_run_id": report["report_run_id"],
            "native_run_id": report["native_run_id"],
            "suite_run_id": report["suite_run_id"],
            "overlay_run_id": report["overlay_run_id"],
            "report_file_sha256": "1" * 64,
            "report_payload_sha256": report["payload_sha256"],
            "c5_run_id": "c5-test",
            "c5_file_sha256": "2" * 64,
            "c5_payload_sha256": "3" * 64,
            "c5_events_file_sha256": "4" * 64,
            "characterization_file_sha256": "5" * 64,
            "characterization_payload_sha256": "6" * 64,
        },
        "actual_decision_matrix_cell": "BLOCKED_QUALITY_PROTOCOL",
        "problem_verdict": "BLOCKED_QUALITY_PROTOCOL",
        "mechanism_status": "NO_METHOD_SELECTED",
        "paper_claim_status": "NOT_AUTHORIZED_DEVELOPMENT_ONLY",
        "live_method_status": "NOT_AUTHORIZED",
        "comparison_boundaries": {
            "freshness": (
                "NOT_CROSS_METHOD_COMPARABLE_CURRENT_ARRIVAL_SEMANTICS"
            ),
            "makespan_goodput": (
                "DESCRIPTIVE_BURST_DRAIN_DEVELOPMENT_CAPACITY"
            ),
            "resource_comparability": (
                "NOT_ESTABLISHED_UNIFIED_REQUEST_ADMISSION_ABSENT"
            ),
            "semantic_parity": (
                "NOT_AUTHORIZED_LIVE_MODEL_OUTPUTS_NOT_CAPTURE_REPLAY_FIXED"
            ),
            "statistical_claim": (
                "NOT_AUTHORIZED_NO_REPEATS_DEVELOPMENT_ONLY"
            ),
        },
    }
    return _seal(body)


def _pending() -> str:
    document = PENDING_DOCUMENT.read_text(encoding="utf-8")
    if "> Status: `EVIDENCE_PENDING`" in document:
        return document
    report = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
    decision = json.loads(REAL_DECISION.read_text(encoding="utf-8"))
    return restore_methodology_template(document, report, decision)


def test_renderer_binds_exact_rows_hashes_states_and_artifact_index() -> None:
    report = _report()
    decision = _decision(report)

    rendered = render_methodology_document(_pending(), report, decision)

    assert "Status: `DESIGN_COMPLETE`" in rendered
    assert "PENDING" not in rendered
    for method in METHODS:
        rows = [
            line
            for line in rendered.splitlines()
            if line.startswith(f"| {method} |")
        ]
        assert len(rows) == 1
    assert (
        "| U0 | 188 | 0.188000 | 500.000 | 1000.000 | "
        "1.000 | 0.500 (4/4 valid) | 0 (MEASURED) |"
    ) in rendered
    assert "N/A (0/4 valid)" in rendered
    assert "N/A (NOT_EVALUATED)" in rendered
    assert (
        "| U0 diagnostics | 1000.000 | N/A (NOT_APPLICABLE_SERIAL_BASELINE) | 1 | false | 1 | 100 | "
        "1000/200 | 80/160 | 300/188 | 400 | 500 | 600 |"
    ) in rendered
    for value in (
        report["report_run_id"],
        report["native_run_id"],
        report["suite_run_id"],
        report["overlay_run_id"],
        report["payload_sha256"],
        decision["payload_sha256"],
        *_decision(report)["input_bindings"].values(),  # type: ignore[union-attr]
    ):
        assert f"`{value}`" in rendered
    for field in (
        "actual decision-matrix cell",
        "problem_verdict",
        "mechanism_status",
        "paper_claim_status",
        "live_method_status",
    ):
        assert field in rendered
    assert "本轮 finalizer \u5df2\u7ed1\u5b9a" in rendered
    assert "METHODOLOGY_DECISION.json" in rendered


def test_renderer_is_byte_idempotent_for_the_same_sealed_inputs() -> None:
    report = _report()
    decision = _decision(report)

    first = render_methodology_document(_pending(), report, decision)
    second = render_methodology_document(first, report, decision)

    assert second == first


def test_renderer_rejects_report_or_decision_seal_drift() -> None:
    report = _report()
    decision = _decision(report)
    report["native_run_id"] = "nb-tampered"
    with pytest.raises(MethodologyDocumentError, match="report.*seal"):
        render_methodology_document(_pending(), report, decision)

    report = _report()
    decision = _decision(report)
    decision["problem_verdict"] = "TAMPERED"
    with pytest.raises(MethodologyDocumentError, match="decision.*seal"):
        render_methodology_document(_pending(), report, decision)


def test_renderer_rejects_cross_artifact_identity_drift() -> None:
    report = _report()
    decision = _decision(report)
    bindings = decision["input_bindings"]
    assert isinstance(bindings, dict)
    bindings["suite_run_id"] = "bs-other"
    body = {key: value for key, value in decision.items() if key != "payload_sha256"}
    decision["payload_sha256"] = payload_sha256(body)

    with pytest.raises(MethodologyDocumentError, match="identity"):
        render_methodology_document(_pending(), report, decision)


def test_renderer_rejects_pending_template_or_finalized_document_drift() -> None:
    report = _report()
    decision = _decision(report)
    pending = _pending().replace(
        "| U0 | 188 | PENDING |", "| U0 | 188 | REMOVED |", 1
    )
    with pytest.raises(MethodologyDocumentError, match="template|anchor"):
        render_methodology_document(pending, report, decision)

    rendered = render_methodology_document(_pending(), report, decision)
    drifted = rendered.replace("Stateful agent-memory", "DRIFTED agent-memory", 1)
    with pytest.raises(MethodologyDocumentError, match="drift|template"):
        render_methodology_document(drifted, report, decision)


def test_renderer_rejects_a_finalized_document_bound_to_other_evidence() -> None:
    report = _report()
    decision = _decision(report)
    rendered = render_methodology_document(_pending(), report, decision)

    other = deepcopy(decision)
    other["problem_verdict"] = "OTHER_VERDICT"
    body = {key: value for key, value in other.items() if key != "payload_sha256"}
    other["payload_sha256"] = payload_sha256(body)

    with pytest.raises(MethodologyDocumentError, match="binding|drift"):
        render_methodology_document(rendered, report, other)


def test_cli_is_idempotent_and_refuses_to_overwrite_drift(
    tmp_path: Path,
) -> None:
    report = _report()
    decision = _decision(report)
    document_path = tmp_path / "methodology.md"
    report_path = tmp_path / "REPORT.json"
    decision_path = tmp_path / "METHODOLOGY_DECISION.json"
    document_path.write_text(_pending(), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    bindings = decision["input_bindings"]
    assert isinstance(bindings, dict)
    bindings["report_file_sha256"] = hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    decision_body = {
        key: value for key, value in decision.items() if key != "payload_sha256"
    }
    decision["payload_sha256"] = payload_sha256(decision_body)
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(PROJECT / "src")}
    command = [
        sys.executable,
        str(SCRIPT),
        "--document",
        str(document_path),
        "--report",
        str(report_path),
        "--decision",
        str(decision_path),
    ]

    first = subprocess.run(command, env=env, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr + first.stdout
    sealed = document_path.read_bytes()
    second = subprocess.run(command, env=env, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr + second.stdout
    assert document_path.read_bytes() == sealed

    document_path.write_text(
        document_path.read_text(encoding="utf-8").replace(
            "Stateful agent-memory", "DRIFTED agent-memory", 1
        ),
        encoding="utf-8",
    )
    drifted = document_path.read_bytes()
    failed = subprocess.run(command, env=env, capture_output=True, text=True)
    assert failed.returncode == 1
    assert document_path.read_bytes() == drifted
    assert "STOP" in failed.stdout
