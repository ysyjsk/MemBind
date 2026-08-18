"""TDD contract for the read-only v3.1 scheduler diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.diagnostics import (
    MemBindV31DiagnosticError,
    analyze_llm_trace_file,
)


def _write_trace(path: Path, rows: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for sequence, row in enumerate(rows):
        body = {
            "schema_version": "membind.paper-eval-v3.membind-v31-llm.v1",
            "row": {"event_sequence": sequence, **row},
        }
        lines.append(
            json.dumps(
                {"record": body, "record_sha256": payload_sha256(body)},
                sort_keys=True,
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_diagnostic_separates_request_prompt_and_service_fractions(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_trace(
        trace,
        [
            {
                "event_type": "llm_request_submitted",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 0,
                "token_count": 100,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 1,
            },
            {
                "event_type": "llm_request_submitted",
                "request_id": "c1",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 0,
                "token_count": 50,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "c1",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 3,
            },
            {
                "event_type": "llm_request_submitted",
                "request_id": "f0",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 2,
                "token_count": 25,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "f0",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 5,
            },
            {
                "event_type": "llm_request_terminal",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 5,
                "status": "ok",
            },
            {
                "event_type": "llm_request_terminal",
                "request_id": "c1",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 8,
                "status": "ok",
            },
            {
                "event_type": "llm_request_terminal",
                "request_id": "f0",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 9,
                "status": "ok",
            },
        ],
    )

    result = analyze_llm_trace_file(trace, admission_capacity=2)

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["trace_status"] == "COMPLETE_TRACE"
    assert result["request_count_by_kind"] == {"COMPILE": 2, "FRONTIER": 1}
    assert result["prompt_tokens_by_kind"] == {"COMPILE": 150, "FRONTIER": 25}
    assert result["rho"]["COMPILE"] == {
        "request_fraction": pytest.approx(2 / 3),
        "prompt_fraction": pytest.approx(150 / 175),
        "service_span_fraction": pytest.approx(9 / 13),
        "service_union_fraction": pytest.approx(7 / 11),
    }
    assert result["service_span_sum_ns_by_kind"] == {"COMPILE": 9, "FRONTIER": 4}
    assert result["service_interval_union_ns_by_kind"] == {"COMPILE": 7, "FRONTIER": 4}
    assert result["service_overlap_excess_ns_by_kind"] == {"COMPILE": 2, "FRONTIER": 0}


def test_diagnostic_reports_occupancy_and_transport_wait_without_claiming_ready_pool(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_trace(
        trace,
        [
            {
                "event_type": "llm_request_submitted",
                "request_id": "a",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 0,
                "token_count": 1,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "a",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 1,
            },
            {
                "event_type": "llm_request_submitted",
                "request_id": "b",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 0,
                "token_count": 1,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "b",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 3,
            },
            {
                "event_type": "llm_request_terminal",
                "request_id": "a",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 5,
                "status": "ok",
            },
            {
                "event_type": "llm_request_terminal",
                "request_id": "b",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 1,
                "timestamp_ns": 8,
                "status": "ok",
            },
        ],
    )

    result = analyze_llm_trace_file(trace, admission_capacity=2)

    assert result["occupancy"]["observed_max_active"] == 2
    assert result["occupancy"]["active_time_by_count_ns"] == {"1": 5, "2": 2}
    assert result["occupancy"]["under_capacity_ns"] == 5
    assert result["transport_wait"]["submitted_wait_sum_ns"] == 4
    assert result["transport_wait"]["under_capacity_ns"] == 3
    assert result["evidence_boundary"] == {
        "ready_pool_observable": False,
        "frontier_wait_reason_observable": False,
        "conclusion": "READY_POOL_STARVATION_NOT_IDENTIFIABLE_FROM_LLM_TRACE",
    }


def test_incomplete_request_is_diagnostic_only_and_not_silently_passed(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_trace(
        trace,
        [
            {
                "event_type": "llm_request_submitted",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 0,
                "token_count": 10,
            }
        ],
    )

    result = analyze_llm_trace_file(trace)

    assert result["status"] == "DIAGNOSTIC_ONLY"
    assert result["trace_status"] == "INCOMPLETE_TRACE"
    assert result["complete_request_count"] == 0
    assert result["incomplete_request_ids"] == ["c0"]


def test_trace_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_trace(
        trace,
        [
            {
                "event_type": "llm_request_submitted",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 0,
                "token_count": 1,
            }
        ],
    )
    trace.write_text(trace.read_text(encoding="utf-8").replace('"token_count": 1', '"token_count": 2'), encoding="utf-8")

    with pytest.raises(MemBindV31DiagnosticError, match="record_hash_mismatch"):
        analyze_llm_trace_file(trace)


def test_request_identity_drift_fails_closed(tmp_path: Path) -> None:
    trace = tmp_path / "llm.jsonl"
    _write_trace(
        trace,
        [
            {
                "event_type": "llm_request_submitted",
                "request_id": "c0",
                "request_kind": "COMPILE",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 0,
                "token_count": 1,
            },
            {
                "event_type": "llm_request_start",
                "request_id": "c0",
                "request_kind": "FRONTIER",
                "stream_id": "s",
                "source_sequence": 0,
                "timestamp_ns": 1,
            },
        ],
    )

    with pytest.raises(MemBindV31DiagnosticError, match="request_identity_mismatch"):
        analyze_llm_trace_file(trace)
