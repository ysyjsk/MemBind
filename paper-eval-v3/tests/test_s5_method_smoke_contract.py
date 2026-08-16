"""TDD tests for the offline S5 method smoke telemetry contract."""

from __future__ import annotations

import copy

import pytest

from paper_eval.s5_method_smoke_contract import (
    S5SmokeContractError,
    mstar_pipeline_to_smoke_records,
    native_method_to_smoke_records,
    validate_smoke_records,
)


def _record(
    sequence: int,
    *,
    method: str,
    worker: int = 0,
    arrival: int | None = None,
    start: int | None = None,
    caller_return: int | None = None,
    publish: int | None = None,
    direct_violations: int = 0,
    fallback: bool = False,
) -> dict:
    arrival = sequence * 10 if arrival is None else arrival
    start = arrival + 1 if start is None else start
    caller_return = start + 2 if caller_return is None else caller_return
    publish = caller_return + 1 if publish is None else publish
    return {
        "method": method,
        "source_sequence": sequence,
        "worker_id": worker,
        "arrival_timestamp_ns": arrival,
        "enqueue_ack_timestamp_ns": arrival + 1,
        "service_start_timestamp_ns": start,
        "caller_return_timestamp_ns": caller_return,
        "publish_timestamp_ns": publish,
        "status": "success",
        "error_class": None,
        "fallback": fallback,
        "intent_written": True,
        "publication_written": True,
        "direct_invariant_violation_count": direct_violations,
    }


def test_a0_requires_fifo_single_worker_and_separates_return_from_publish() -> None:
    records = [_record(i, method="A0", caller_return=i * 10 + 3, publish=i * 10 + 8) for i in range(3)]
    result = validate_smoke_records("A0", expected_source_sequences=[0, 1, 2], records=records)
    assert result["status"] == "PASS"
    assert result["coverage"] == 1.0
    assert result["worker_count"] == 1
    assert result["post_return_stale_window_ns"] == [5, 5, 5]


def test_p_requires_real_overlap_but_retains_direct_violation_as_observation() -> None:
    records = [
        _record(0, method="P*", worker=0, start=0, caller_return=20, publish=20, direct_violations=1),
        _record(1, method="P*", worker=1, start=11, caller_return=15, publish=15),
    ]
    result = validate_smoke_records("P*", expected_source_sequences=[0, 1], records=records)
    assert result["status"] == "PASS"
    assert result["whole_update_overlap_observed"] is True
    assert result["direct_invariant_violation_count"] == 1
    assert result["scientific_outcome_not_adapter_failure"] is True


def test_m_requires_source_order_no_fallback_and_zero_direct_violations() -> None:
    records = [_record(i, method="M*", caller_return=i * 10 + 2, publish=i * 10 + 3) for i in range(2)]
    result = validate_smoke_records("M*", expected_source_sequences=[0, 1], records=records)
    assert result["status"] == "PASS"
    assert result["publication_order"] == [0, 1]
    assert result["fallback_count"] == 0


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows.pop(),
        lambda rows: rows.append(copy.deepcopy(rows[0])),
        lambda rows: rows[0].update(source_sequence=-1),
        lambda rows: rows[0].update(publication_written=False),
        lambda rows: rows[0].update(fallback=True),
        lambda rows: rows[0].update(publish_timestamp_ns=0),
    ],
)
def test_smoke_contract_fails_closed_on_loss_duplicate_or_bad_telemetry(mutate) -> None:
    records = [_record(i, method="M*") for i in range(2)]
    mutate(records)
    with pytest.raises(S5SmokeContractError):
        validate_smoke_records("M*", expected_source_sequences=[0, 1], records=records)


def test_p_rejects_no_overlap_and_m_rejects_fallback_or_direct_violation() -> None:
    p_records = [_record(0, method="P*", worker=0), _record(1, method="P*", worker=1, start=30)]
    with pytest.raises(S5SmokeContractError, match="overlap"):
        validate_smoke_records("P*", expected_source_sequences=[0, 1], records=p_records)

    m_records = [_record(0, method="M*", fallback=True), _record(1, method="M*")]
    with pytest.raises(S5SmokeContractError, match="fallback"):
        validate_smoke_records("M*", expected_source_sequences=[0, 1], records=m_records)


def test_contract_rejects_legacy_method_ids_and_private_fields() -> None:
    records = [_record(0, method="M0")]
    with pytest.raises(S5SmokeContractError, match="method"):
        validate_smoke_records("M0", expected_source_sequences=[0], records=records)
    records = [_record(0, method="M*")]
    records[0]["prompt"] = "secret"
    with pytest.raises(S5SmokeContractError, match="private"):
        validate_smoke_records("M*", expected_source_sequences=[0], records=records)


def test_mstar_pipeline_projection_reuses_common_smoke_contract() -> None:
    evidence = {
        "method": "M*",
        "status": "PASS",
        "events": [
            {"event_sequence": 0, "event_type": "intent", "source_sequence": 0, "logical_time_ns": 10},
            {"event_sequence": 1, "event_type": "intent", "source_sequence": 1, "logical_time_ns": 10},
            {"event_sequence": 2, "event_type": "prepare_start", "source_sequence": 0, "worker_id": 0, "prepare_start_timestamp_ns": 11},
            {"event_sequence": 3, "event_type": "prepare_start", "source_sequence": 1, "worker_id": 1, "prepare_start_timestamp_ns": 12},
            {"event_sequence": 4, "event_type": "commit_returned", "source_sequence": 0, "commit_return_timestamp_ns": 30},
            {"event_sequence": 5, "event_type": "publication", "source_sequence": 0, "publication_timestamp_ns": 31},
            {"event_sequence": 6, "event_type": "commit_returned", "source_sequence": 1, "commit_return_timestamp_ns": 40},
            {"event_sequence": 7, "event_type": "publication", "source_sequence": 1, "publication_timestamp_ns": 41},
            {"event_sequence": 8, "event_type": "terminal_success"},
        ]
    }
    records = mstar_pipeline_to_smoke_records(evidence)
    result = validate_smoke_records("M*", expected_source_sequences=[0, 1], records=records)
    assert result["publication_order"] == [0, 1]
    assert result["direct_invariant_violation_count"] == 0


def test_mstar_pipeline_projection_fails_closed_on_missing_phase() -> None:
    with pytest.raises(S5SmokeContractError, match="terminal"):
        mstar_pipeline_to_smoke_records(
            {
                "method": "M*",
                "status": "PASS",
                "events": [{"event_type": "intent", "source_sequence": 0, "logical_time_ns": 1}],
            }
        )


def test_native_a0_evidence_projects_into_the_common_smoke_contract() -> None:
    evidence = {
        "method": "A0",
        "status": "PASS",
        "events": [
            {
                "event_sequence": 0,
                "event_type": "intent",
                "source_sequence": 0,
                "intent_timestamp_ns": 10,
            },
            {
                "event_sequence": 1,
                "event_type": "caller_return",
                "source_sequence": 0,
                "durable_enqueue_ack_timestamp_ns": 11,
                "caller_return_timestamp_ns": 11,
            },
            {
                "event_sequence": 2,
                "event_type": "publication",
                "source_sequence": 0,
                "worker_id": 0,
                "service_start_timestamp_ns": 12,
                "publish_timestamp_ns": 20,
                "caller_return_timestamp_ns": 11,
            },
            {
                "event_sequence": 3,
                "event_type": "terminal_success",
                "expected_episode_count": 1,
            },
        ],
    }

    records = native_method_to_smoke_records(
        evidence, direct_invariant_violations={0: 0}
    )
    result = validate_smoke_records(
        "A0", expected_source_sequences=[0], records=records
    )

    assert result["status"] == "PASS"
    assert result["post_return_stale_window_ns"] == [9]
    assert records[0]["intent_written"] is True
    assert records[0]["publication_written"] is True


def test_native_p_projection_binds_explicit_invariant_observations() -> None:
    evidence = {
        "method": "P*",
        "status": "PASS",
        "events": [
            {
                "event_sequence": 0,
                "event_type": "intent",
                "source_sequence": 0,
                "intent_timestamp_ns": 10,
            },
            {
                "event_sequence": 1,
                "event_type": "intent",
                "source_sequence": 1,
                "intent_timestamp_ns": 11,
            },
            {
                "event_sequence": 2,
                "event_type": "publication",
                "source_sequence": 1,
                "worker_id": 1,
                "service_start_timestamp_ns": 12,
                "publish_timestamp_ns": 20,
                "caller_return_timestamp_ns": 20,
            },
            {
                "event_sequence": 3,
                "event_type": "publication",
                "source_sequence": 0,
                "worker_id": 0,
                "service_start_timestamp_ns": 12,
                "publish_timestamp_ns": 30,
                "caller_return_timestamp_ns": 30,
            },
            {
                "event_sequence": 4,
                "event_type": "terminal_success",
                "expected_episode_count": 2,
            },
        ],
    }

    records = native_method_to_smoke_records(
        evidence, direct_invariant_violations={0: 1, 1: 0}
    )
    result = validate_smoke_records(
        "P*", expected_source_sequences=[0, 1], records=records
    )

    assert result["whole_update_overlap_observed"] is True
    assert result["direct_invariant_violation_count"] == 1


def test_native_projection_rejects_missing_durable_or_publication_event() -> None:
    with pytest.raises(S5SmokeContractError, match="incomplete"):
        native_method_to_smoke_records(
            {
                "method": "A0",
                "status": "PASS",
                "events": [
                    {
                        "event_sequence": 0,
                        "event_type": "intent",
                        "source_sequence": 0,
                        "intent_timestamp_ns": 10,
                    },
                    {
                        "event_sequence": 1,
                        "event_type": "terminal_success",
                        "expected_episode_count": 1,
                    },
                ],
            },
            direct_invariant_violations={0: 0},
        )


def test_native_projection_requires_explicit_complete_invariant_observation() -> None:
    evidence = {
        "method": "A0",
        "status": "PASS",
        "events": [
            {
                "event_sequence": 0,
                "event_type": "intent",
                "source_sequence": 0,
                "intent_timestamp_ns": 10,
            },
            {
                "event_sequence": 1,
                "event_type": "caller_return",
                "source_sequence": 0,
                "durable_enqueue_ack_timestamp_ns": 11,
                "caller_return_timestamp_ns": 11,
            },
            {
                "event_sequence": 2,
                "event_type": "publication",
                "source_sequence": 0,
                "worker_id": 0,
                "service_start_timestamp_ns": 12,
                "publish_timestamp_ns": 20,
                "caller_return_timestamp_ns": 11,
            },
            {
                "event_sequence": 3,
                "event_type": "terminal_success",
                "expected_episode_count": 1,
            },
        ],
    }

    with pytest.raises(S5SmokeContractError, match="invariant coverage"):
        native_method_to_smoke_records(evidence)
    with pytest.raises(S5SmokeContractError, match="invariant coverage"):
        native_method_to_smoke_records(
            evidence, direct_invariant_violations={}
        )


def test_a0_requires_caller_return_to_precede_publication_strictly() -> None:
    record = _record(
        0,
        method="A0",
        worker=0,
        caller_return=20,
        publish=20,
    )

    with pytest.raises(S5SmokeContractError, match="timestamp separation"):
        validate_smoke_records(
            "A0", expected_source_sequences=[0], records=[record]
        )
