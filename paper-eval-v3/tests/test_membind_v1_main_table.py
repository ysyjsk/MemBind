"""Contract tests for the isolated MemBind-v1 development main table."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.membind_v1.main_table import (
    ALIGNED_METHODS,
    GRAPH_NATIVE_PROTOCOL_DEGENERATE,
    MainTableError,
    bind_sealed_historical_references,
    build_development_main_table,
    render_development_main_table_markdown,
)


ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = (
    ROOT
    / "artifacts/paper_eval/baseline_suite/runs/bs-dev-20260816-001"
    / "THREE_BASELINE_RESULTS.json"
)
REPORT_PATH = (
    ROOT
    / "artifacts/paper_eval/development_report/runs/report-dev-20260817-001"
    / "REPORT.json"
)
OVERLAY_PATH = (
    ROOT
    / "artifacts/paper_eval/graph_quality_overlay/runs/gq-dev-20260817-001"
    / "GRAPH_QUALITY_RESULTS.json"
)
DECISION_PATH = (
    ROOT
    / "artifacts/paper_eval/methodology_finalization/runs/methodology-dev-20260817-001"
    / "METHODOLOGY_DECISION.json"
)
FINAL_ENVELOPE_PATH = (
    ROOT
    / "artifacts/paper_eval/methodology_finalization/runs/methodology-dev-20260817-001"
    / "FINAL_METHODOLOGY_ENVELOPE.json"
)
METHODOLOGY_PATH = ROOT.parent / "主methodology设计.md"


def _historical() -> dict[str, object]:
    return bind_sealed_historical_references(
        baseline_suite=json.loads(BASELINE_PATH.read_text(encoding="utf-8")),
        development_report=json.loads(REPORT_PATH.read_text(encoding="utf-8")),
        graph_quality_overlay=json.loads(OVERLAY_PATH.read_text(encoding="utf-8")),
        methodology_decision=json.loads(DECISION_PATH.read_text(encoding="utf-8")),
        final_methodology_envelope=json.loads(
            FINAL_ENVELOPE_PATH.read_text(encoding="utf-8")
        ),
        methodology_document=METHODOLOGY_PATH.read_text(encoding="utf-8"),
    )


def _rows(*, quality_status: str = "NUMERICALLY_COMPARABLE") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for ordinal, method in enumerate(ALIGNED_METHODS):
        rows.append(
            {
                "method": method,
                "aligned_run_id": "aligned-dev-20260817-001",
                "arrival_trace_sha256": "a" * 64,
                "source_manifest_sha256": "c" * 64,
                "shared_execution_envelope_sha256": "b" * 64,
                "global_llm_admission_k": 2,
                "execution_status": "COMPLETED",
                "validity_status": "VALID",
                "quality_status": quality_status,
                "metrics": {
                    "qa_accuracy": 0.5 + ordinal * 0.1,
                    "evidence_recall_at_10": 1.0,
                    "direct_violations": 0,
                    "p95_arrival_to_publication_ns": 1000 + ordinal,
                    "p99_arrival_to_publication_ns": 2000 + ordinal,
                    "successful_goodput_episodes_per_second": 1.0 + ordinal,
                    "makespan_ns": 10_000 + ordinal,
                    "max_backlog": ordinal,
                },
            }
        )
    return rows


def test_historical_references_bind_the_hard_coded_seals_and_remain_noncomparable() -> None:
    historical = _historical()

    assert historical["historical_reference_status"] == (
        "FROZEN_REFERENCE_NOT_CROSS_METHOD_FRESHNESS_COMPARABLE"
    )
    assert historical["cross_method_freshness_delta_authorized"] is False
    assert tuple(row["method"] for row in historical["rows"]) == ("U0", "P(C=2)")
    assert historical["artifact_payload_bindings"] == {
        "three_baseline": "7c087a2368724f2f8cfb0f8e17cd5d2f54684e51b3cfb9203a0f6dc04eff4ef0",
        "development_report": "ba060bd48fb933319b522ef5196c003919b2a0c0d2a81c3eb9f00f4b264e9c62",
        "graph_quality_overlay": "15bd92d9f8393a3614d8764cdb71752e59f0e0668bc2f5ccb1746df8dad31953",
        "methodology_decision": "50a76d29ff973b67465940af94d3bc9c3814db04bad2774b4ea834b78ed22f4d",
        "final_methodology_envelope": "fdce14ca14af82e1f393663bcf822a3153cecbe86c93375a231ab71bcdddec1f",
    }


def test_historical_references_reject_a_resealed_projection_that_is_not_the_pinned_artifact() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    overlay = json.loads(OVERLAY_PATH.read_text(encoding="utf-8"))
    decision = json.loads(DECISION_PATH.read_text(encoding="utf-8"))
    final_envelope = json.loads(FINAL_ENVELOPE_PATH.read_text(encoding="utf-8"))
    report = copy.deepcopy(report)
    report["payload_sha256"] = "0" * 64

    with pytest.raises(MainTableError, match="development report payload"):
        bind_sealed_historical_references(
            baseline_suite=baseline,
            development_report=report,
            graph_quality_overlay=overlay,
            methodology_decision=decision,
            final_methodology_envelope=final_envelope,
            methodology_document=METHODOLOGY_PATH.read_text(encoding="utf-8"),
        )


def test_aligned_comparative_table_requires_exact_three_methods_and_shared_identity() -> None:
    table = build_development_main_table(
        main_table_run_id="main-table-dev-20260817-001",
        historical_references=_historical(),
        aligned_rows=_rows(),
    )

    assert table["status"] == "PASS"
    assert table["data_role"] == "DEVELOPMENT_EXPOSED"
    assert table["aligned_identity"] == {
        "aligned_run_id": "aligned-dev-20260817-001",
        "arrival_trace_sha256": "a" * 64,
        "source_manifest_sha256": "c" * 64,
        "shared_execution_envelope_sha256": "b" * 64,
        "global_llm_admission_k": 2,
    }
    assert tuple(row["method"] for row in table["aligned_comparative_rows"]) == ALIGNED_METHODS
    assert table["quality_comparison_status"] == "NUMERICALLY_COMPARABLE"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda rows: rows.pop(), "aligned method inventory"),
        (
            lambda rows: rows.__setitem__(
                1,
                {**rows[1], "arrival_trace_sha256": "c" * 64},
            ),
            "arrival trace",
        ),
        (
            lambda rows: rows.__setitem__(
                1,
                {**rows[1], "source_manifest_sha256": "d" * 64},
            ),
            "source manifest",
        ),
        (
            lambda rows: rows[1].update({"global_llm_admission_k": 3}),
            "global LLM admission K",
        ),
        (
            lambda rows: rows[2].update({"execution_status": "INCOMPLETE"}),
            "execution status",
        ),
        (
            lambda rows: rows[2].update({"validity_status": "INVALID"}),
            "validity status",
        ),
    ],
)
def test_aligned_comparative_table_fails_closed_for_missing_or_unfair_rows(
    mutate, expected: str
) -> None:
    rows = _rows()
    mutate(rows)

    with pytest.raises(MainTableError, match=expected):
        build_development_main_table(
            main_table_run_id="main-table-dev-20260817-001",
            historical_references=_historical(),
            aligned_rows=rows,
        )


def test_renderer_marks_qa_not_quantitatively_comparable_when_graph_protocol_is_degenerate() -> None:
    table = build_development_main_table(
        main_table_run_id="main-table-dev-20260817-001",
        historical_references=_historical(),
        aligned_rows=_rows(quality_status=GRAPH_NATIVE_PROTOCOL_DEGENERATE),
    )

    markdown = render_development_main_table_markdown(table)

    assert table["quality_comparison_status"] == GRAPH_NATIVE_PROTOCOL_DEGENERATE
    assert "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE" in markdown
    assert "development-only" in markdown
    assert "not a final held-out paper table" in markdown
    assert "FROZEN_REFERENCE_NOT_CROSS_METHOD_FRESHNESS_COMPARABLE" in markdown


def test_renderer_is_deterministic_and_refuses_an_unsealed_main_table() -> None:
    table = build_development_main_table(
        main_table_run_id="main-table-dev-20260817-001",
        historical_references=_historical(),
        aligned_rows=_rows(),
    )

    assert render_development_main_table_markdown(table) == render_development_main_table_markdown(table)
    table["payload_sha256"] = "0" * 64
    with pytest.raises(MainTableError, match="main table payload"):
        render_development_main_table_markdown(table)
