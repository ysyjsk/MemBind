from __future__ import annotations

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6.proof import (
    V6ProofError,
    validate_frontier_events,
    validate_provider_events,
    validate_replay_accounting,
    validate_request_comparisons,
)


def test_frontier_proof_requires_exact_order_and_no_jump() -> None:
    events = [{"event": "PUBLICATION_DURABLE", "source_sequence": index} for index in range(3)]
    assert validate_frontier_events(events, source_count=3)["status"] == "PASS"
    with pytest.raises(V6ProofError, match="durable frontier"):
        validate_frontier_events([events[0], {"event": "PUBLICATION_DURABLE", "source_sequence": 2}], source_count=3)


def test_provider_proof_checks_outstanding_and_reserved_future_credit() -> None:
    events = [
        {"event": "ADMISSION_ADMIT", "outstanding": 1, "future_outstanding": 1, "admission_class": "FUTURE_PREPARE"},
        {"event": "ADMISSION_ADMIT", "outstanding": 3, "future_outstanding": 2, "admission_class": "NATIVE_FRONTIER"},
    ]
    assert validate_provider_events(events, capacity=3)["status"] == "PASS"
    with pytest.raises(V6ProofError, match="provider outstanding"):
        validate_provider_events([{"event": "ADMISSION_ADMIT", "outstanding": 4, "future_outstanding": 0}], capacity=3)
    with pytest.raises(V6ProofError, match="future outstanding"):
        validate_provider_events([{"event": "ADMISSION_ADMIT", "outstanding": 3, "future_outstanding": 3}], capacity=3)


def test_replay_accounting_forbids_duplicates_and_unconsumed() -> None:
    assert validate_replay_accounting({"logical_captured": 4, "logical_consumed": 4, "duplicates": 0, "unconsumed": 0})["status"] == "PASS"
    with pytest.raises(V6ProofError, match="replay accounting"):
        validate_replay_accounting({"logical_captured": 4, "logical_consumed": 3, "duplicates": 0, "unconsumed": 1})


def test_request_comparison_false_accept_is_impossible() -> None:
    assert validate_request_comparisons([{"match": True, "changed_fields": []}])["status"] == "PASS"
    with pytest.raises(V6ProofError, match="false accept"):
        validate_request_comparisons([{"match": True, "changed_fields": ["messages"]}])
