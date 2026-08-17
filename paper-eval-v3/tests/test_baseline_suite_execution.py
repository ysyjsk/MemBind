from __future__ import annotations

from copy import deepcopy

import pytest

from paper_eval.baseline_suite_execution import (
    graph_work_attribution_status,
    normalize_schedule_lifecycle,
)


def _event(sequence: int, kind: str, source: int | None = None, **extra: object):
    value: dict[str, object] = {
        "event_sequence": sequence,
        "event_type": kind,
        "run_id": "bs-20260816-001-a0-07741c45",
        "method": "A0",
        **extra,
    }
    if source is not None:
        value.update(source_sequence=source, source_sha256=f"{source + 1:064x}")
    return value


def test_a0_lifecycle_uses_intent_as_arrival_and_caller_ack_as_enqueue() -> None:
    evidence = {
        "status": "PASS",
        "run_id": "bs-20260816-001-a0-07741c45",
        "method": "A0",
        "events": [
            _event(0, "intent", 0, intent_timestamp_ns=10),
            _event(
                1,
                "caller_return",
                0,
                durable_enqueue_ack_timestamp_ns=12,
                caller_return_timestamp_ns=12,
            ),
            _event(2, "intent", 1, intent_timestamp_ns=14),
            _event(
                3,
                "caller_return",
                1,
                durable_enqueue_ack_timestamp_ns=16,
                caller_return_timestamp_ns=16,
            ),
            _event(
                4,
                "publication",
                0,
                worker_id=0,
                service_start_timestamp_ns=20,
                publish_timestamp_ns=30,
                caller_return_timestamp_ns=12,
                transaction_status="committed",
            ),
            _event(
                5,
                "publication",
                1,
                worker_id=0,
                service_start_timestamp_ns=31,
                publish_timestamp_ns=40,
                caller_return_timestamp_ns=16,
                transaction_status="committed",
            ),
            _event(6, "terminal_success", expected_episode_count=2),
        ],
    }

    rows = normalize_schedule_lifecycle(
        evidence=evidence,
        method="A0",
        expected_sequences=[0, 1],
    )

    assert rows == [
        {
            "source_sequence": 0,
            "arrival_ts_ns": 10,
            "enqueue_ts_ns": 12,
            "service_start_ts_ns": 20,
            "publication_ts_ns": 30,
            "terminal_ts_ns": 30,
            "caller_return_ts_ns": 12,
            "queue_depth_at_enqueue": 1,
            "worker_id": 0,
        },
        {
            "source_sequence": 1,
            "arrival_ts_ns": 14,
            "enqueue_ts_ns": 16,
            "service_start_ts_ns": 31,
            "publication_ts_ns": 40,
            "terminal_ts_ns": 40,
            "caller_return_ts_ns": 16,
            "queue_depth_at_enqueue": 2,
            "worker_id": 0,
        },
    ]


def test_p_lifecycle_accepts_out_of_order_publication_but_returns_source_order() -> None:
    evidence = {
        "status": "PASS",
        "run_id": "bs-20260816-001-a0-07741c45",
        "method": "P(C=2)",
        "events": [
            {
                **_event(0, "intent", 0, intent_timestamp_ns=10),
                "method": "P(C=2)",
            },
            {
                **_event(1, "intent", 1, intent_timestamp_ns=11),
                "method": "P(C=2)",
            },
            {
                **_event(
                    2,
                    "publication",
                    1,
                    worker_id=1,
                    service_start_timestamp_ns=13,
                    publish_timestamp_ns=20,
                    caller_return_timestamp_ns=20,
                    transaction_status="committed",
                ),
                "method": "P(C=2)",
            },
            {
                **_event(
                    3,
                    "publication",
                    0,
                    worker_id=0,
                    service_start_timestamp_ns=12,
                    publish_timestamp_ns=21,
                    caller_return_timestamp_ns=21,
                    transaction_status="committed",
                ),
                "method": "P(C=2)",
            },
            {
                **_event(4, "terminal_success", expected_episode_count=2),
                "method": "P(C=2)",
            },
        ],
    }

    rows = normalize_schedule_lifecycle(
        evidence=evidence,
        method="P(C=2)",
        expected_sequences=[0, 1],
    )

    assert [row["source_sequence"] for row in rows] == [0, 1]
    assert [row["worker_id"] for row in rows] == [0, 1]
    assert rows[0]["enqueue_ts_ns"] == 10
    assert rows[1]["enqueue_ts_ns"] == 11


@pytest.mark.parametrize(
    "mutation",
    [
        lambda evidence: evidence.update(status="FAIL_CLOSED"),
        lambda evidence: evidence["events"].pop(4),
        lambda evidence: evidence["events"].append(
            deepcopy(evidence["events"][4])
        ),
    ],
)
def test_lifecycle_fails_closed_on_nonpass_missing_or_duplicate_publication(
    mutation,
) -> None:
    evidence = {
        "status": "PASS",
        "run_id": "bs-20260816-001-u0-07741c45",
        "method": "U0",
        "events": [
            {**_event(0, "intent", 0, intent_timestamp_ns=10), "method": "U0"},
            {
                **_event(
                    1,
                    "publication",
                    0,
                    worker_id=0,
                    service_start_timestamp_ns=11,
                    publish_timestamp_ns=20,
                    caller_return_timestamp_ns=20,
                    transaction_status="committed",
                ),
                "method": "U0",
            },
            {**_event(2, "intent", 1, intent_timestamp_ns=21), "method": "U0"},
            {
                **_event(
                    3,
                    "publication",
                    1,
                    worker_id=0,
                    service_start_timestamp_ns=22,
                    publish_timestamp_ns=30,
                    caller_return_timestamp_ns=30,
                    transaction_status="committed",
                ),
                "method": "U0",
            },
            {
                **_event(4, "terminal_success", expected_episode_count=2),
                "method": "U0",
            },
        ],
    }
    mutation(evidence)

    with pytest.raises(ValueError):
        normalize_schedule_lifecycle(
            evidence=evidence,
            method="U0",
            expected_sequences=[0, 1],
        )


def test_p_graph_prefix_delta_is_explicitly_concurrent_and_confounded() -> None:
    assert graph_work_attribution_status("U0") == "SERIAL_PREFIX_DELTA_OBSERVED"
    assert graph_work_attribution_status("A0") == "SERIAL_PREFIX_DELTA_OBSERVED"
    assert (
        graph_work_attribution_status("P(C=2)")
        == "CONCURRENT_PREFIX_DELTA_CONFOUNDED"
    )
