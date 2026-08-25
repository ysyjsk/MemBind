from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.evaluation_contract import (
    TraceValidationError,
    validate_block_trace,
    validate_order_contract,
    validate_v6_bindings,
)


def _events(*, publication_order=(0, 1), native_order=(0, 1), equal=False):
    rows = [
        {"event": "FORMAL_START", "source_sequence": None, "event_index": 0, "monotonic_ns": 100},
    ]
    index = 1
    for seq in native_order:
        rows.append({"event": "SUBMIT", "source_sequence": seq, "event_index": index, "monotonic_ns": 100 + index})
        index += 1
        rows.append({"event": "NATIVE_ENTER", "source_sequence": seq, "event_index": index, "monotonic_ns": 100 + index})
        index += 1
    for seq in publication_order:
        timestamp = 130 + index
        if equal and seq == 1:
            timestamp = next(row["monotonic_ns"] for row in rows if row["event"] == "PUBLICATION_DURABLE" and row["source_sequence"] == 0)
        rows.append({"event": "PUBLICATION_DURABLE", "source_sequence": seq, "event_index": index, "monotonic_ns": timestamp})
        index += 1
    rows.append({"event": "CONSTRUCTION_SEAL", "source_sequence": None, "event_index": index, "monotonic_ns": 200})
    return rows


def test_fixed_work_uses_formal_start_to_last_durable_publication() -> None:
    events = [
        {"event": "FORMAL_START", "event_index": 0, "monotonic_ns": 100},
        {"event": "SUBMIT", "source_sequence": 0, "event_index": 1, "monotonic_ns": 101},
        {"event": "NATIVE_ENTER", "source_sequence": 0, "event_index": 2, "monotonic_ns": 102},
        {"event": "PUBLICATION_DURABLE", "source_sequence": 0, "event_index": 3, "monotonic_ns": 110},
        {"event": "SUBMIT", "source_sequence": 1, "event_index": 4, "monotonic_ns": 111},
        {"event": "NATIVE_ENTER", "source_sequence": 1, "event_index": 5, "monotonic_ns": 112},
        {"event": "PUBLICATION_DURABLE", "source_sequence": 1, "event_index": 6, "monotonic_ns": 155},
        {"event": "CONSTRUCTION_SEAL", "event_index": 7, "monotonic_ns": 200},
    ]
    result = validate_block_trace(events, expected_source_count=2, method="B0", context_id="ctx")
    assert result["contract_status"] == "PASS"
    assert result["submitted_count"] == 2
    assert result["completed_count"] == 2
    assert result["t_build_ns"] == 55


def test_duplicate_terminal_and_unknown_episode_fail_closed() -> None:
    events = _events()
    events.insert(-1, {"event": "PUBLICATION_DURABLE", "source_sequence": 0, "event_index": 8, "monotonic_ns": 201})
    with pytest.raises(TraceValidationError, match="duplicate terminal"):
        validate_block_trace(events, expected_source_count=2, method="B0", context_id="ctx")


def test_order_validator_reports_first_overlap_and_b1_does_not_gate() -> None:
    events = _events(native_order=(1, 0))
    b0 = validate_order_contract(events, expected_source_count=2, method="B0")
    assert b0["order_contract_status"] == "FAIL"
    assert b0["first_violation"]["source_sequence"] == 1
    b1 = validate_order_contract(events, expected_source_count=2, method="B1")
    assert b1["order_contract_status"] == "NOT_REQUIRED"
    assert b1["inversion_count"] == 1


def test_equal_or_missing_order_timestamp_is_invalid_trace() -> None:
    events = _events(equal=True)
    publication_zero = next(row["monotonic_ns"] for row in events if row["event"] == "PUBLICATION_DURABLE" and row["source_sequence"] == 0)
    next(row for row in events if row["event"] == "NATIVE_ENTER" and row["source_sequence"] == 1)["monotonic_ns"] = publication_zero
    result = validate_order_contract(events, expected_source_count=2, method="V6")
    assert result["order_contract_status"] == "INVALID_TRACE"
    events[2].pop("monotonic_ns")
    result = validate_order_contract(events, expected_source_count=2, method="V6")
    assert result["order_contract_status"] == "INVALID_TRACE"


def test_v6_binding_requires_exact_single_consume_without_replay_transport() -> None:
    rows = [
        {
            "source_sequence": 0,
            "callsite": "extract",
            "ordinal_within_episode": 0,
            "request_identity_hash": "a" * 64,
            "prepared_response_hash": "b" * 64,
            "native_request_hash": "c" * 64,
            "capture_count": 1,
            "consume_count": 1,
            "match_status": "EXACT_MATCH",
            "external_transport_attempted_during_replay": False,
        }
    ]
    assert validate_v6_bindings(rows)["refinement_status"] == "PASS"
    rows[0]["consume_count"] = 2
    with pytest.raises(TraceValidationError, match="consume"):
        validate_v6_bindings(rows)
