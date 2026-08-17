"""RED-first contracts for the evidence-bound methodology decision.

The decision is intentionally a pure projection of sealed development evidence.
It must not infer a paper claim or authorize a live method implementation.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.methodology_decision import (
    MethodologyDecisionError,
    build_methodology_decision,
)


METHODS = ("U0", "A0", "P(C=2)")
HISTORIES = ("h0", "h1", "h2", "h3")
PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent


def _method(
    method: str,
    *,
    makespan_ns: int,
    history_makespans: tuple[int, int, int, int],
    graph_qa: float = 0.5,
) -> dict[str, object]:
    return {
        "history_count": 4,
        "episode_count": 188,
        "successful_goodput_episodes_per_second": 188 / (makespan_ns / 1e9),
        "makespan_ns": makespan_ns,
        "freshness_ns": {"p95": makespan_ns // 2, "p99": makespan_ns},
        "graph_quality_qa_accuracy": graph_qa,
        "graph_quality_valid_judge_count": 4,
        "graph_quality_invalid_judge_count": 0,
        "max_backlog": None if method == "U0" else 49,
        "observed_max_active_calls": 2 if method == "P(C=2)" else 1,
        "overlap_observed": method == "P(C=2)",
        "work_volume_ratio_vs_u0": {
            "llm_logical_calls": 1.0,
            "llm_input_tokens": 1.0,
            "llm_output_tokens": 1.0,
            "llm_transport_attempts": 1.0,
            "embedding_calls": 1.0,
            "embedding_items": 1.0,
            "db_operations": 1.0,
            "db_transactions": 1.0,
            "candidate_query_count": 1.0,
            "candidate_count": 1.0,
        },
        "histories": [
            {"history_id": history_id, "makespan_ns": value}
            for history_id, value in zip(HISTORIES, history_makespans, strict=True)
        ],
    }


def _report(*, p_is_faster: bool = True) -> dict[str, object]:
    u0_history = (250, 250, 250, 250)
    p_history = (180, 190, 200, 210) if p_is_faster else (260, 270, 280, 290)
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.development-baseline-report.v1",
        "status": "PASS",
        "report_run_id": "report-dev-test",
        "native_run_id": "nb-dev-test",
        "suite_run_id": "bs-dev-test",
        "overlay_run_id": "gq-dev-test",
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "history_order": list(HISTORIES),
        "method_order": list(METHODS),
        "methods": {
            "U0": _method(
                "U0", makespan_ns=1_000, history_makespans=u0_history
            ),
            "A0": _method(
                "A0", makespan_ns=1_010, history_makespans=(252, 252, 253, 253)
            ),
            "P(C=2)": _method(
                "P(C=2)",
                makespan_ns=sum(p_history),
                history_makespans=p_history,
            ),
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _c5(*, direct_counterexample: bool = True) -> dict[str, object]:
    interpretation = (
        "DIRECT_INVARIANT_VIOLATION_OBSERVED"
        if direct_counterexample
        else "NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED"
    )
    body: dict[str, object] = {
        "schema_version": "membind.native-characterization-e4-whole-parallel.v1",
        "status": "complete",
        "run_id": "c5-test",
        "stage": "C5/E4",
        "completed_block_count": 4,
        "overall_interpretation": interpretation,
        "bounded_claim": "one screening pass is not a general safety proof",
        "block_results": [
            {
                "status": "complete",
                "interpretation": interpretation if concurrency == 2 else (
                    "NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED"
                ),
                "metrics": {"concurrency": concurrency},
                "direct_evidence": (
                    ["source-order invariant violation"]
                    if direct_counterexample and concurrency == 2
                    else []
                ),
            }
            for concurrency in (1, 2, 4, 8)
        ],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _decision(
    *, report: dict[str, object] | None = None, c5: dict[str, object] | None = None
) -> dict[str, object]:
    return build_methodology_decision(
        decision_run_id="methodology-dev-test",
        report=_report() if report is None else report,
        c5_result=_c5() if c5 is None else c5,
        report_file_sha256="a" * 64,
        c5_file_sha256="b" * 64,
        characterization_file_sha256="c" * 64,
        characterization_payload_sha256="d" * 64,
        c5_events_file_sha256="e" * 64,
    )


def test_capacity_plus_counterexample_selects_only_a_bounded_candidate() -> None:
    decision = _decision()

    assert decision["status"] == "PASS"
    assert decision["decision_run_id"] == "methodology-dev-test"
    assert decision["actual_decision_matrix_cell"] == (
        "CAPACITY_SIGNAL_WITH_DIRECT_INVARIANT_COUNTEREXAMPLE"
    )
    assert decision["problem_verdict"] == (
        "PROBLEM_SUPPORTED_FOR_BOUNDED_NODE_ONLY_PROTOTYPE"
    )
    assert decision["mechanism_status"] == "NODE_ONLY_CANDIDATE"
    assert decision["paper_claim_status"] == "NOT_AUTHORIZED_DEVELOPMENT_ONLY"
    assert decision["live_method_status"] == "NOT_AUTHORIZED"
    assert decision["observations"]["p_capacity_signal"] is True
    assert decision["observations"]["p_paired_history_makespan_wins"] == 4
    assert decision["observations"]["c5_direct_counterexample"] is True
    assert decision["observations"]["c5_model_nondeterminism_assessment"] == (
        "GRAPH_RETRIEVAL_PARITY_CONFOUNDED_DIRECT_ORDER_EVIDENCE_UNAFFECTED"
    )
    assert decision["observations"]["quality_protocol_usable"] is True
    assert decision["observations"]["a0_burst_backlog_observed"] is True
    assert decision["observations"]["p_work_volume_ratio_vs_u0"] == (
        _report()["methods"]["P(C=2)"]["work_volume_ratio_vs_u0"]
    )
    assert decision["comparison_boundaries"]["freshness"] == (
        "NOT_CROSS_METHOD_COMPARABLE_CURRENT_ARRIVAL_SEMANTICS"
    )
    assert decision["comparison_boundaries"]["makespan_goodput"] == (
        "DESCRIPTIVE_BURST_DRAIN_DEVELOPMENT_CAPACITY"
    )
    assert decision["comparison_boundaries"]["resource_comparability"] == (
        "NOT_ESTABLISHED_UNIFIED_REQUEST_ADMISSION_ABSENT"
    )
    assert decision["input_bindings"]["characterization_payload_sha256"] == (
        "d" * 64
    )
    assert decision["input_bindings"]["c5_events_file_sha256"] == "e" * 64
    assert decision["payload_sha256"] == payload_sha256(
        {key: value for key, value in decision.items() if key != "payload_sha256"}
    )


def test_no_capacity_signal_stops_even_when_c5_has_a_counterexample() -> None:
    decision = _decision(report=_report(p_is_faster=False))

    assert decision["actual_decision_matrix_cell"] == (
        "NO_CAPACITY_SIGNAL_WITH_DIRECT_INVARIANT_COUNTEREXAMPLE"
    )
    assert decision["problem_verdict"] == "PERFORMANCE_MOTIVATION_INSUFFICIENT"
    assert decision["mechanism_status"] == "STOP_OR_REFRAME_AS_SERVING_PROBLEM"


def test_capacity_direction_uses_aggregate_not_an_arbitrary_win_threshold() -> None:
    report = _report()
    p = report["methods"]["P(C=2)"]
    makespans = (200, 200, 251, 251)
    p["histories"] = [
        {"history_id": history_id, "makespan_ns": value}
        for history_id, value in zip(HISTORIES, makespans, strict=True)
    ]
    p["makespan_ns"] = sum(makespans)
    p["successful_goodput_episodes_per_second"] = 188 / (sum(makespans) / 1e9)
    report["payload_sha256"] = payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )

    decision = _decision(report=report)

    assert decision["observations"]["p_paired_history_makespan_wins"] == 2
    assert decision["observations"]["p_capacity_signal"] is True
    assert decision["actual_decision_matrix_cell"] == (
        "CAPACITY_SIGNAL_WITH_DIRECT_INVARIANT_COUNTEREXAMPLE"
    )


def test_capacity_without_counterexample_requires_reassessment_not_a_method() -> None:
    decision = _decision(c5=_c5(direct_counterexample=False))

    assert decision["actual_decision_matrix_cell"] == (
        "CAPACITY_SIGNAL_WITHOUT_OBSERVED_INSUFFICIENCY"
    )
    assert decision["problem_verdict"] == "REASSESS_MEMBIND_NECESSITY"
    assert decision["mechanism_status"] == "NO_METHOD_SELECTED"


def test_invalid_judge_denominator_blocks_the_methodology_decision() -> None:
    report = _report()
    report["methods"]["U0"]["graph_quality_valid_judge_count"] = 3
    report["methods"]["U0"]["graph_quality_invalid_judge_count"] = 1
    report["payload_sha256"] = payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )

    decision = _decision(report=report)

    assert decision["actual_decision_matrix_cell"] == "BLOCKED_QUALITY_PROTOCOL"
    assert decision["problem_verdict"] == "BLOCKED_QUALITY_PROTOCOL"
    assert decision["mechanism_status"] == "NO_METHOD_SELECTED"


def test_zero_u0_graph_quality_is_a_degenerate_block_not_a_usable_protocol() -> None:
    report = _report()
    for method in METHODS:
        report["methods"][method]["graph_quality_qa_accuracy"] = 0.0
    report["payload_sha256"] = payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )

    decision = _decision(report=report)

    assert decision["observations"]["u0_graph_quality_non_degenerate"] is False
    assert decision["observations"]["quality_protocol_usable"] is False
    assert decision["actual_decision_matrix_cell"] == "BLOCKED_QUALITY_PROTOCOL"
    assert decision["problem_verdict"] == "BLOCKED_QUALITY_PROTOCOL"
    assert decision["mechanism_status"] == "NO_METHOD_SELECTED"


def test_tampered_report_or_c5_is_rejected() -> None:
    report = _report()
    report["methods"]["P(C=2)"]["makespan_ns"] = 1
    with pytest.raises(MethodologyDecisionError, match="report payload"):
        _decision(report=report)

    c5 = _c5()
    c5["overall_interpretation"] = "NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED"
    with pytest.raises(MethodologyDecisionError, match="C5 payload"):
        _decision(c5=c5)

    inconsistent = _report()
    inconsistent["methods"]["P(C=2)"][
        "successful_goodput_episodes_per_second"
    ] = 999.0
    inconsistent["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in inconsistent.items()
            if key != "payload_sha256"
        }
    )
    with pytest.raises(MethodologyDecisionError, match="goodput/makespan"):
        _decision(report=inconsistent)

    inconsistent = _report()
    p = inconsistent["methods"]["P(C=2)"]
    p["histories"] = [
        {"history_id": history_id, "makespan_ns": 300}
        for history_id in HISTORIES
    ]
    inconsistent["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in inconsistent.items()
            if key != "payload_sha256"
        }
    )
    with pytest.raises(MethodologyDecisionError, match="aggregate makespan"):
        _decision(report=inconsistent)

    malformed_c5 = _c5()
    malformed_c5["block_results"][2]["metrics"]["concurrency"] = 2
    malformed_c5["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in malformed_c5.items()
            if key != "payload_sha256"
        }
    )
    with pytest.raises(MethodologyDecisionError, match="concurrency inventory"):
        _decision(c5=malformed_c5)


def test_history_identity_or_parallel_execution_drift_is_rejected() -> None:
    report = deepcopy(_report())
    report["methods"]["P(C=2)"]["histories"][0]["history_id"] = "wrong"
    report["payload_sha256"] = payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )
    with pytest.raises(MethodologyDecisionError, match="history identity"):
        _decision(report=report)

    report = deepcopy(_report())
    report["methods"]["P(C=2)"]["overlap_observed"] = False
    report["payload_sha256"] = payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )
    decision = _decision(report=report)
    assert decision["observations"]["p_capacity_signal"] is False


def test_file_backed_finalizer_is_idempotent_and_rejects_output_drift(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "REPORT.json"
    output_path = tmp_path / "METHODOLOGY_DECISION.json"
    report_path.write_text(
        json.dumps(_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    c3 = (
        REPOSITORY
        / "membind-validation/artifacts/native_characterization/"
        "e2_dependency_opportunity.json"
    )
    c5_root = (
        REPOSITORY
        / "membind-validation/artifacts/native_characterization/runs/"
        "c5-e3867c66ba92e7da"
    )
    command = [
        sys.executable,
        str(PROJECT / "scripts/finalize_methodology_decision.py"),
        "methodology-dev-test",
        "--report",
        str(report_path),
        "--c3",
        str(c3),
        "--c5",
        str(c5_root / "e4_whole_parallel.json"),
        "--c5-events",
        str(c5_root / "events.jsonl"),
        "--output",
        str(output_path),
    ]
    environment = {**os.environ, "PYTHONPATH": str(PROJECT / "src")}

    first = subprocess.run(
        command, check=False, capture_output=True, text=True, env=environment
    )
    assert first.returncode == 0, first.stderr
    original = output_path.read_bytes()
    second = subprocess.run(
        command, check=False, capture_output=True, text=True, env=environment
    )
    assert second.returncode == 0, second.stderr
    assert output_path.read_bytes() == original

    tampered = json.loads(original)
    tampered["problem_verdict"] = "TAMPERED"
    output_path.write_text(json.dumps(tampered), encoding="utf-8")
    rejected = subprocess.run(
        command, check=False, capture_output=True, text=True, env=environment
    )
    assert rejected.returncode != 0
    assert "existing methodology decision payload seal" in rejected.stderr

    output_path.write_bytes(original)
    typed_tamper = json.loads(original)
    typed_tamper["observations"]["p_capacity_signal"] = 1
    output_path.write_text(json.dumps(typed_tamper), encoding="utf-8")
    rejected_type_drift = subprocess.run(
        command, check=False, capture_output=True, text=True, env=environment
    )
    assert rejected_type_drift.returncode != 0
    assert "existing methodology decision payload seal" in (
        rejected_type_drift.stderr
    )

    alternate_c5 = json.loads((c5_root / "e4_whole_parallel.json").read_text())
    alternate_c5["bounded_claim"] = "rehashed but not the pinned artifact"
    alternate_c5["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in alternate_c5.items()
            if key != "payload_sha256"
        }
    )
    alternate_c5_path = tmp_path / "alternate-c5.json"
    alternate_c5_path.write_text(
        json.dumps(alternate_c5, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    alternate_command = list(command)
    alternate_command[alternate_command.index("--c5") + 1] = str(
        alternate_c5_path
    )
    alternate_command[alternate_command.index("--output") + 1] = str(
        tmp_path / "alternate-decision.json"
    )
    rejected_input = subprocess.run(
        alternate_command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert rejected_input.returncode != 0
    assert "pinned C5 file SHA256 drift" in rejected_input.stderr
