"""RED-first contract for the data-backed main methodology document.

The document is intentionally tested against the sealed development report.
This prevents a plausible narrative from silently drifting away from the
U0/A0/P(C=2) evidence that motivated the eventual method.
"""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256


PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
REPORT = (
    PROJECT
    / "artifacts/paper_eval/development_report/runs/report-dev-20260817-001/REPORT.json"
)
METHODOLOGY = REPOSITORY / "主methodology设计.md"
DECISION = (
    PROJECT
    / "artifacts/paper_eval/methodology_finalization/runs/"
    "methodology-dev-20260817-001/METHODOLOGY_DECISION.json"
)


def _load_report() -> dict[str, object]:
    assert REPORT.is_file(), "sealed three-baseline development report is required"
    value = json.loads(REPORT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    assert value["status"] == "PASS"
    assert value["schema_version"] == (
        "membind.paper-eval-v3.development-baseline-report.v1"
    )
    stored = value["payload_sha256"]
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    assert stored == payload_sha256(body)
    return value


def _load_decision() -> dict[str, object]:
    assert DECISION.is_file(), "sealed methodology decision is required"
    value = json.loads(DECISION.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    assert value["status"] == "PASS"
    assert value["schema_version"] == (
        "membind.paper-eval-v3.methodology-decision.v1"
    )
    stored = value["payload_sha256"]
    body = {key: item for key, item in value.items() if key != "payload_sha256"}
    assert stored == payload_sha256(body)
    return value


def _document() -> str:
    return METHODOLOGY.read_text(encoding="utf-8")


def _result_row(text: str, method: str) -> str:
    prefix = f"| {method} |"
    rows = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(rows) == 1, f"expected one methodology result row for {method}"
    return rows[0]


def _table_cells(row: str, *, expected_count: int) -> list[str]:
    cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
    assert len(cells) == expected_count, row
    return cells


def _diagnostic_row(text: str, method: str) -> str:
    prefix = f"| {method} diagnostics |"
    rows = [line for line in text.splitlines() if line.startswith(prefix)]
    assert len(rows) == 1, f"expected one diagnostic result row for {method}"
    return rows[0]


def test_methodology_is_bound_to_the_sealed_development_evidence() -> None:
    report = _load_report()
    decision = _load_decision()
    text = _document()

    for identity in (
        report["report_run_id"],
        report["native_run_id"],
        report["suite_run_id"],
        report["overlay_run_id"],
        report["payload_sha256"],
    ):
        assert f"`{identity}`" in text

    bindings = decision["input_bindings"]
    assert isinstance(bindings, dict)
    assert bindings["report_payload_sha256"] == report["payload_sha256"]
    for identity in (
        decision["decision_run_id"],
        bindings["report_file_sha256"],
        bindings["c5_file_sha256"],
        bindings["c5_payload_sha256"],
        bindings["c5_events_file_sha256"],
        bindings["characterization_file_sha256"],
        bindings["characterization_payload_sha256"],
        decision["payload_sha256"],
    ):
        assert f"`{identity}`" in text

    assert "DEVELOPMENT_EXPOSED" in text
    assert "188 episodes" in text
    assert "PILOT" in text and "FINAL_PAPER_TEST" in text
    assert "descriptive" in text.casefold()
    assert "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED" in text
    assert "PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC" in text


def test_methodology_cites_the_actual_three_method_measurements() -> None:
    report = _load_report()
    text = _document()
    methods = report["methods"]
    assert isinstance(methods, dict)

    for method in ("U0", "A0", "P(C=2)"):
        value = methods[method]
        assert isinstance(value, dict)
        row = _result_row(text, method)
        cells = _table_cells(row, expected_count=8)
        graph_qa = value["graph_quality_qa_accuracy"]
        expected_graph_qa = "N/A" if graph_qa is None else f"{graph_qa:.3f}"
        valid = value["graph_quality_valid_judge_count"]
        invalid = value["graph_quality_invalid_judge_count"]
        direct = value["direct_violations"]
        expected_direct = "N/A" if direct is None else str(direct)
        statuses = ", ".join(value["direct_violations_statuses"])
        assert cells == [
            method,
            str(value["episode_count"]),
            f"{value['successful_goodput_episodes_per_second']:.6f}",
            f"{value['freshness_ns']['p95'] / 1_000_000_000:.3f}",
            f"{value['makespan_ns'] / 1_000_000_000:.3f}",
            f"{value['evidence_recall_at_10_macro']:.3f}",
            f"{expected_graph_qa} ({valid}/{valid + invalid} valid)",
            f"{expected_direct} ({statuses})",
        ]

        diagnostic = _table_cells(
            _diagnostic_row(text, method), expected_count=13
        )
        work = value["work_volume"]
        final_graph = value["final_graph"]
        workers = ",".join(str(item) for item in value["configured_worker_counts"])
        backlog = "N/A" if value["max_backlog"] is None else str(value["max_backlog"])
        assert diagnostic == [
            f"{method} diagnostics",
            f"{value['freshness_ns']['p99'] / 1_000_000_000:.3f}",
            f"{backlog} ({value['max_backlog_status']})",
            str(value["observed_max_active_calls"]),
            str(value["overlap_observed"]).lower(),
            workers,
            str(work["llm_logical_calls"]),
            f"{work['llm_input_tokens']}/{work['llm_output_tokens']}",
            f"{work['embedding_calls']}/{work['embedding_items']}",
            f"{work['db_operations']}/{work['db_transactions']}",
            str(work["candidate_count"]),
            str(final_graph["node_count_sum"]),
            str(final_graph["relationship_count_sum"]),
        ]

    assert "Session Evidence Recall@10" in text
    assert "Graph-native QA" in text
    assert "work volume" in text.casefold()
    assert "direct violations" in text.casefold()


def test_methodology_has_no_unresolved_placeholders_after_report() -> None:
    report = _load_report()
    decision = _load_decision()
    text = _document()
    methods = report["methods"]
    assert isinstance(methods, dict)

    assert "Status: `DESIGN_COMPLETE`" in text
    assert "PENDING" not in text
    for field in (
        "problem_verdict",
        "mechanism_status",
        "paper_claim_status",
        "live_method_status",
    ):
        assert f"{field}" in text
        assert str(decision[field]) in text
    assert "actual decision-matrix cell" in text
    assert str(decision["actual_decision_matrix_cell"]) in text

    boundaries = decision["comparison_boundaries"]
    assert isinstance(boundaries, dict)
    for value in boundaries.values():
        assert str(value) in text


def test_methodology_does_not_promote_a_screening_result_to_a_proof() -> None:
    text = _document()

    for forbidden in (
        "当前最终 methodology 冻结为",
        "最终方法已经冻结为",
        "188-episode characterization 已经足以支持完整 MemBind-v1",
        "61.28% arrival-ready",
        "数据库级 atomic publication 已证明",
    ):
        assert forbidden not in text

    assert "一次 development screening 不能证明" in text
    assert "capture/replay" in text
    assert "canonical projection" in text
    assert "不把未测量的 direct violations 解释为 0" in text


def test_methodology_freezes_only_the_minimum_candidate_and_gates_edge_motion() -> None:
    text = _document()

    assert "node-only minimum viable compile" in text.casefold()
    assert "extract_nodes" in text
    assert "extract_edges" in text
    assert "exact prompt/input parity" in text
    assert "zero mutable graph reads" in text
    assert "frozen-provider capture/replay parity" in text
    assert "失败则保持 node-only" in text
    assert "latest committed state" in text
    assert "source-ordered publication" in text
    assert "fail-closed durable prefix" in text
    assert "C/W/K" in text and "工程参数" in text


def test_methodology_has_falsification_tdd_and_publication_boundaries() -> None:
    text = _document()

    for heading in (
        "## 证据驱动的研究裁决",
        "## 候选机制",
        "## TDD 实现门禁",
        "## 最小评测矩阵",
        "## 停止与证伪条件",
        "## Publication 边界",
        "## 实现顺序",
    ):
        assert heading in text

    assert "RED" in text and "focused GREEN" in text and "full offline GREEN" in text
    assert "不声明 DB atomic publication" in text
    assert "rollback/visibility" in text
    assert "U0" in text and "A0" in text and "P(C=2)" in text
    assert "held-out" in text.casefold()
    assert "STOP" in text
