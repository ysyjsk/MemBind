"""TDD contract for real ready/need DVSR speculation windows."""

from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_window import (
    bound_hidden_cp_components,
    compute_pair_window_from_observer_evidence,
    compute_speculation_window,
    recover_frozen_v6_window_fields,
)


def test_window_uses_effective_ready_and_caps_removable_cp() -> None:
    result = compute_speculation_window(
        artifact_ready_ns=100,
        old_snapshot_ready_ns=120,
        old_snapshot_close_ns=180,
        authoritative_need_ns=260,
        removable_operator_cp_ns=200,
    )

    assert result["status"] == "COMPLETE"
    assert result["effective_ready_ns"] == 120
    assert result["speculation_window_ns"] == 140
    assert result["maximum_hideable_cp_ns"] == 140


def test_window_caps_at_operator_cp_when_window_is_larger() -> None:
    result = compute_speculation_window(
        artifact_ready_ns=100,
        old_snapshot_ready_ns=100,
        old_snapshot_close_ns=200,
        authoritative_need_ns=500,
        removable_operator_cp_ns=75,
    )

    assert result["speculation_window_ns"] == 400
    assert result["maximum_hideable_cp_ns"] == 75


def test_late_ready_cannot_delay_predecessor_publication() -> None:
    result = compute_speculation_window(
        artifact_ready_ns=210,
        old_snapshot_ready_ns=100,
        old_snapshot_close_ns=200,
        authoritative_need_ns=500,
        removable_operator_cp_ns=300,
    )

    assert result["status"] == "INELIGIBLE_CROSS_SNAPSHOT_LAUNCH"
    assert result["reason"] == "prepared_artifact_not_ready_before_old_snapshot_close"
    assert result["speculation_window_ns"] == 0
    assert result["maximum_hideable_cp_ns"] == 0


@pytest.mark.parametrize(
    "missing",
    [
        "artifact_ready_ns",
        "old_snapshot_ready_ns",
        "old_snapshot_close_ns",
        "authoritative_need_ns",
        "removable_operator_cp_ns",
    ],
)
def test_missing_measured_field_fails_closed(missing: str) -> None:
    values = {
        "artifact_ready_ns": 100,
        "old_snapshot_ready_ns": 100,
        "old_snapshot_close_ns": 200,
        "authoritative_need_ns": 300,
        "removable_operator_cp_ns": 150,
    }
    values[missing] = None

    result = compute_speculation_window(**values)

    assert result["status"] == "MISSING_FIELD"
    assert missing in result["missing_fields"]
    assert result["maximum_hideable_cp_ns"] is None


def test_hidden_components_share_one_window_cap() -> None:
    result = bound_hidden_cp_components(
        reuse_hidden_cp_ns=90,
        reconvergence_saved_descendant_cp_ns=60,
        maximum_hideable_cp_ns=100,
    )

    assert result == {
        "reuse_hidden_cp_ns": 90,
        "reconvergence_saved_descendant_cp_ns": 10,
        "window_bounded_hidden_cp_ns": 100,
        "uncredited_due_to_window_ns": 50,
    }


def test_negative_or_non_integral_timing_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        compute_speculation_window(
            artifact_ready_ns=-1,
            old_snapshot_ready_ns=0,
            old_snapshot_close_ns=1,
            authoritative_need_ns=2,
            removable_operator_cp_ns=1,
        )


def test_pair_window_uses_old_capture_ready_and_fresh_node_need() -> None:
    result = compute_pair_window_from_observer_evidence(
        source_sequence=1,
        old_capture={"end_ns": 140},
        fresh_capture={
            "trace": [
                {"phase": "node-resolution", "status": "ok", "start_ns": 250, "end_ns": 320},
            ]
        },
        formal_start_ns=100,
        previous_durable_ns=None,
        predecessor_publication_start_ns=200,
        removable_operator_cp_ns=90,
    )

    assert result["status"] == "COMPLETE"
    assert result["effective_ready_ns"] == 140
    assert result["speculation_window_ns"] == 110
    assert result["maximum_hideable_cp_ns"] == 90
    assert result["ready_event"] == "OLD.capture.end_ns"


def test_pair_window_missing_need_is_fail_closed() -> None:
    result = compute_pair_window_from_observer_evidence(
        source_sequence=2,
        old_capture={"end_ns": 140},
        fresh_capture={"trace": []},
        formal_start_ns=None,
        previous_durable_ns=100,
        predecessor_publication_start_ns=200,
        removable_operator_cp_ns=90,
    )

    assert result["status"] == "MISSING_FIELD"
    assert "authoritative_need_ns" in result["missing_fields"]


def test_pair_window_rejects_duplicate_need_span() -> None:
    result = compute_pair_window_from_observer_evidence(
        source_sequence=1,
        old_capture={"end_ns": 140},
        fresh_capture={
            "trace": [
                {"phase": "node-resolution", "status": "ok", "start_ns": 250, "end_ns": 320},
                {"phase": "node-resolution", "status": "ok", "start_ns": 260, "end_ns": 330},
            ]
        },
        formal_start_ns=100,
        previous_durable_ns=None,
        predecessor_publication_start_ns=200,
        removable_operator_cp_ns=90,
    )

    assert result["status"] == "MISSING_FIELD"


def test_recover_existing_frozen_v6_fields_uses_node_resolution_need() -> None:
    frontier = [
        {"event": "PREPARE_READY", "source_sequence": 1, "monotonic_ns": 100},
        {"event": "PREPARE_READY", "source_sequence": 2, "monotonic_ns": 310},
        {"event": "PUBLICATION_DURABLE", "source_sequence": 0, "monotonic_ns": 205},
    ]
    traces = [
        {
            "source_sequence": 0,
            "spans": [{"phase": "publication", "start_ns": 180, "end_ns": 200, "status": "ok"}],
        },
        {
            "source_sequence": 1,
            "spans": [
                {"phase": "node-resolution", "start_ns": 250, "end_ns": 280, "status": "ok"},
                {"phase": "publication", "start_ns": 300, "end_ns": 305, "status": "ok"},
            ],
        },
        {
            "source_sequence": 2,
            "spans": [{"phase": "node-resolution", "start_ns": 400, "end_ns": 450, "status": "ok"}],
        },
    ]
    raw = [{"event": "FORMAL_START", "monotonic_ns": 10}]

    recovered = recover_frozen_v6_window_fields(
        frontier_events=frontier,
        native_trace_envelopes=traces,
        raw_events=raw,
    )

    first, second = recovered["rows"]
    assert first == {
        "source_sequence": 1,
        "artifact_ready_ns": 100,
        "old_snapshot_ready_ns": 10,
        "old_snapshot_close_ns": 180,
        "authoritative_need_ns": 250,
        "field_status": "COMPLETE",
        "cross_snapshot_launch_eligible": True,
        "raw_window_ns": 150,
        "need_event": "node-resolution.start_ns",
        "ready_event": "PREPARE_READY.monotonic_ns",
    }
    assert second["old_snapshot_ready_ns"] == 205
    assert second["old_snapshot_close_ns"] == 300
    assert second["cross_snapshot_launch_eligible"] is False
    assert second["raw_window_ns"] == 0


def test_duplicate_need_span_is_missing_evidence_not_arbitrarily_selected() -> None:
    recovered = recover_frozen_v6_window_fields(
        frontier_events=[
            {"event": "PREPARE_READY", "source_sequence": 1, "monotonic_ns": 100}
        ],
        native_trace_envelopes=[
            {
                "source_sequence": 0,
                "spans": [{"phase": "publication", "start_ns": 180, "end_ns": 200, "status": "ok"}],
            },
            {
                "source_sequence": 1,
                "spans": [
                    {"phase": "node-resolution", "start_ns": 250, "end_ns": 280, "status": "ok"},
                    {"phase": "node-resolution", "start_ns": 251, "end_ns": 281, "status": "ok"},
                ],
            },
        ],
        raw_events=[{"event": "FORMAL_START", "monotonic_ns": 10}],
    )

    assert recovered["rows"][0]["field_status"] == "MISSING_FIELD"
    assert "authoritative_need_ns" in recovered["rows"][0]["missing_fields"]
