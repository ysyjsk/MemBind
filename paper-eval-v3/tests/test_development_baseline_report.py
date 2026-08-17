"""RED-first contracts for the final three-baseline development report."""

from __future__ import annotations

import pytest

from paper_eval.baseline_suite import DEVELOPMENT_HISTORIES
from paper_eval.development_baseline_report import (
    WORK_FIELDS,
    DevelopmentBaselineReportError,
    build_development_baseline_report,
    render_development_baseline_markdown,
)


METHODS = ("U0", "A0", "P(C=2)")


def _row(method: str, history_id: str, index: int) -> dict[str, object]:
    multiplier = {"U0": 1, "A0": 10, "P(C=2)": 2}[method]
    freshness = [multiplier * (index + 1) * value for value in (10, 20, 30)]
    return {
        "method": method,
        "history_id": history_id,
        "episode_count": 3,
        "metrics": {
            "qa_accuracy": 1.0 if index == 0 else 0.0,
            "evidence_recall_at_10": 1.0,
            "direct_violations": 0 if method == "U0" else None,
            "direct_violations_status": (
                "MEASURED" if method == "U0" else "NOT_EVALUATED"
            ),
            "makespan_ns": freshness[-1],
            "max_backlog": None if method == "U0" else 4 + index,
            "max_backlog_status": (
                "NOT_APPLICABLE_SERIAL_BASELINE"
                if method == "U0"
                else "OBSERVED"
            ),
        },
        "freshness_samples_ns": freshness,
        "work_volume": {
            "llm_logical_calls": 10,
            "llm_input_tokens": 1000,
            "llm_output_tokens": 100,
            "llm_transport_attempts": 10,
            "embedding_calls": 8,
            "embedding_items": 20,
            "db_operations": 15,
            "db_transactions": 3,
            "candidate_query_count": 6,
            "candidate_count": 40,
        },
        "final_graph": {
            "node_count": 100 + index,
            "relationship_count": 200 + index,
            "episodic_count": 3,
            "episode_names_match_expected": True,
        },
        "schedule_summary": {
            "configured_worker_count": 1 if method != "P(C=2)" else 2,
            "max_active_calls": 1 if method != "P(C=2)" else 2,
            "whole_update_interval_overlap_observed": method == "P(C=2)",
        },
        "result_payload_sha256": f"{METHODS.index(method) * 4 + index + 1:064x}",
    }


def _quality() -> dict[str, object]:
    return {
        "status": "PASS",
        "payload_sha256": "f" * 64,
        "summary": {
            "claim_label": (
                "PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC"
            ),
            "heldout_data_accessed": False,
            "quality_identity": {
                "retrieval_config_sha256": "a" * 64,
                "reader_config_sha256": "b" * 64,
                "judge_config_sha256": "c" * 64,
            },
            "runtime_identity_sha256": "d" * 64,
            "by_method": {
                method: {
                    "question_count": 4,
                    "valid_judge_count": 4,
                    "invalid_judge_count": 0,
                    "qa_accuracy": 0.5 if method != "P(C=2)" else 0.75,
                    "edge_attributed_source_coverage_at_10_macro": 0.75,
                }
                for method in sorted(METHODS)
            },
        },
    }


def test_report_uses_fixed_inventory_and_pooled_distributions() -> None:
    rows = [
        _row(method, history_id, index)
        for method in METHODS
        for index, history_id in enumerate(DEVELOPMENT_HISTORIES)
    ]

    report = build_development_baseline_report(
        report_run_id="report-dev-001",
        native_run_id="nb-dev-001",
        suite_run_id="bs-dev-001",
        overlay_run_id="gq-dev-001",
        baseline_rows=rows,
        graph_quality_report=_quality(),
        artifact_paths={
            "native": "paper-eval-v3/artifacts/native",
            "suite": "paper-eval-v3/artifacts/suite",
            "graph_quality": "paper-eval-v3/artifacts/graph-quality",
        },
    )

    assert report["status"] == "PASS"
    assert report["heldout_data_accessed"] is False
    assert report["data_access_disclosure"] == {
        "evaluated_role": "DEVELOPMENT_EXPOSED",
        "live_graph_quality_input": "ISOLATED_FOUR_RECORD_ARTIFACT",
        "live_graph_quality_combined_container_opened": False,
        "input_materialization_scanned_combined_container": True,
        "project_lifetime_no_combined_container_scan_claim": False,
        "pilot_or_final_records_evaluated": False,
    }
    assert report["method_order"] == list(METHODS)
    assert report["methods"]["U0"]["episode_count"] == 12
    assert report["methods"]["U0"]["qa_accuracy_macro"] == 0.25
    assert report["methods"]["U0"]["evidence_recall_at_10_macro"] == 1.0
    assert report["methods"]["U0"]["freshness_ns"]["p95"] == 120
    assert report["methods"]["A0"]["freshness_ns"]["p95"] == 1200
    assert report["methods"]["P(C=2)"]["graph_quality_qa_accuracy"] == 0.75
    assert report["methods"]["A0"]["max_backlog"] == 7
    assert report["methods"]["P(C=2)"]["observed_max_active_calls"] == 2
    assert report["methods"]["P(C=2)"]["overlap_observed"] is True
    assert report["methods"]["A0"]["work_volume"]["llm_logical_calls"] == 40
    assert report["payload_sha256"]


@pytest.mark.parametrize("missing_field", WORK_FIELDS)
def test_report_rejects_missing_work_volume_instead_of_fabricating_zero(
    missing_field: str,
) -> None:
    rows = [
        _row(method, history_id, index)
        for method in METHODS
        for index, history_id in enumerate(DEVELOPMENT_HISTORIES)
    ]
    del rows[0]["work_volume"][missing_field]  # type: ignore[index]

    with pytest.raises(DevelopmentBaselineReportError, match="work volume"):
        build_development_baseline_report(
            report_run_id="report-dev-001",
            native_run_id="nb-dev-001",
            suite_run_id="bs-dev-001",
            overlay_run_id="gq-dev-001",
            baseline_rows=rows,
            graph_quality_report=_quality(),
            artifact_paths={"native": "n", "suite": "s", "graph_quality": "g"},
        )


def test_markdown_keeps_primary_diagnostic_and_claim_limits_explicit() -> None:
    rows = [
        _row(method, history_id, index)
        for method in METHODS
        for index, history_id in enumerate(DEVELOPMENT_HISTORIES)
    ]
    report = build_development_baseline_report(
        report_run_id="report-dev-001",
        native_run_id="nb-dev-001",
        suite_run_id="bs-dev-001",
        overlay_run_id="gq-dev-001",
        baseline_rows=rows,
        graph_quality_report=_quality(),
        artifact_paths={"native": "n", "suite": "s", "graph_quality": "g"},
    )

    markdown = render_development_baseline_markdown(report)

    assert "Native U0、Async-Serial A0 与 Whole-Update Parallel P(C=2)" in markdown
    assert "Session Evidence Recall@10" in markdown
    assert "Graph-native QA" in markdown
    assert "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED" in markdown
    assert "development/calibration" in markdown
    assert "没有访问 PILOT 或 FINAL_PAPER_TEST" in markdown
    assert "一次性 materialization" in markdown
    assert "不作“项目生命周期从未扫描 combined container”" in markdown
    assert "arrival timestamp 语义不同" in markdown
    assert "P95/P99 不能计算跨方法 freshness delta" in markdown
    assert "burst-drain capacity 的 descriptive directional signal" in markdown
    assert "n" in markdown and "s" in markdown and "g" in markdown
