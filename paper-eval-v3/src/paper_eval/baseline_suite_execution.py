"""Deterministic projections from scheduler evidence to common lifecycle rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .baseline_suite import (
    BASELINE_METHODS,
    BaselineSuiteError,
    canonicalize_baseline_method,
)


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BaselineSuiteError(f"{field} timestamp is invalid")
    return value


def _source(value: object, expected: set[int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in expected:
        raise BaselineSuiteError("scheduler source sequence is invalid")
    return value


def graph_work_attribution_status(method: str) -> str:
    selected = canonicalize_baseline_method(method)
    if selected == "P(C=2)":
        return "CONCURRENT_PREFIX_DELTA_CONFOUNDED"
    return "SERIAL_PREFIX_DELTA_OBSERVED"


def normalize_schedule_lifecycle(
    *,
    evidence: Mapping[str, Any],
    method: str,
    expected_sequences: Sequence[int],
) -> list[dict[str, int | None]]:
    """Project one PASS schedule into source-ordered lifecycle timestamps.

    P(C=2) publication order may differ from source order. The projection
    therefore validates uniqueness and complete coverage, then returns rows in
    source order without rewriting the observed worker/timestamp evidence.
    """

    selected_method = canonicalize_baseline_method(method)
    expected = list(expected_sequences)
    if expected != list(range(len(expected))) or not expected:
        raise BaselineSuiteError("expected source sequence inventory is invalid")
    expected_set = set(expected)
    if not isinstance(evidence, Mapping) or evidence.get("status") != "PASS":
        raise BaselineSuiteError("scheduler evidence is not a PASS")
    run_id = evidence.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id
        or evidence.get("method") != selected_method
    ):
        raise BaselineSuiteError("scheduler evidence identity is invalid")
    events = evidence.get("events")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise BaselineSuiteError("scheduler events are invalid")
    if [event.get("event_sequence") for event in events if isinstance(event, Mapping)] != list(
        range(len(events))
    ):
        raise BaselineSuiteError("scheduler event sequence is invalid")

    arrivals: dict[int, int] = {}
    enqueue_acks: dict[int, int] = {}
    caller_returns: dict[int, int] = {}
    publications: dict[int, tuple[int, int, int]] = {}
    terminal_success = 0
    for event in events:
        if not isinstance(event, Mapping):
            raise BaselineSuiteError("scheduler event is invalid")
        if event.get("run_id") != run_id or event.get("method") != selected_method:
            raise BaselineSuiteError("scheduler event identity mismatch")
        kind = event.get("event_type")
        if kind == "intent":
            sequence = _source(event.get("source_sequence"), expected_set)
            if sequence in arrivals:
                raise BaselineSuiteError("duplicate intent event")
            arrivals[sequence] = _timestamp(
                event.get("intent_timestamp_ns"), "intent"
            )
        elif kind == "caller_return":
            sequence = _source(event.get("source_sequence"), expected_set)
            if sequence in enqueue_acks:
                raise BaselineSuiteError("duplicate caller-return event")
            enqueue_acks[sequence] = _timestamp(
                event.get("durable_enqueue_ack_timestamp_ns"), "enqueue ack"
            )
            caller_returns[sequence] = _timestamp(
                event.get("caller_return_timestamp_ns"), "caller return"
            )
            if enqueue_acks[sequence] != caller_returns[sequence]:
                raise BaselineSuiteError("caller return precedes durable enqueue ack")
        elif kind == "publication":
            sequence = _source(event.get("source_sequence"), expected_set)
            if sequence in publications:
                raise BaselineSuiteError("duplicate publication event")
            start = _timestamp(
                event.get("service_start_timestamp_ns"), "service start"
            )
            publication = _timestamp(
                event.get("publish_timestamp_ns"), "publication"
            )
            worker = event.get("worker_id")
            if (
                isinstance(worker, bool)
                or not isinstance(worker, int)
                or worker < 0
                or publication < start
                or event.get("transaction_status") != "committed"
            ):
                raise BaselineSuiteError("publication event is invalid")
            publications[sequence] = (start, publication, worker)
        elif kind == "terminal_success":
            terminal_success += 1
            if event.get("expected_episode_count") != len(expected):
                raise BaselineSuiteError("terminal episode count mismatch")

    if terminal_success != 1:
        raise BaselineSuiteError("scheduler terminal success is missing or duplicate")
    if set(arrivals) != expected_set or set(publications) != expected_set:
        raise BaselineSuiteError("scheduler lifecycle coverage is incomplete")
    if selected_method == "A0":
        if set(enqueue_acks) != expected_set or set(caller_returns) != expected_set:
            raise BaselineSuiteError("A0 caller-return coverage is incomplete")
    elif enqueue_acks or caller_returns:
        raise BaselineSuiteError("caller-return events are forbidden for this method")

    rows: list[dict[str, int | None]] = []
    for sequence in expected:
        arrival = arrivals[sequence]
        enqueue = enqueue_acks.get(sequence, arrival)
        service_start, publication, worker = publications[sequence]
        caller_return = caller_returns.get(sequence)
        if not arrival <= enqueue <= service_start <= publication:
            raise BaselineSuiteError("scheduler lifecycle timestamps are not monotonic")
        rows.append(
            {
                "source_sequence": sequence,
                "arrival_ts_ns": arrival,
                "enqueue_ts_ns": enqueue,
                "service_start_ts_ns": service_start,
                "publication_ts_ns": publication,
                "terminal_ts_ns": publication,
                "caller_return_ts_ns": caller_return,
                "queue_depth_at_enqueue": (
                    0 if selected_method == "U0" else sequence + 1
                ),
                "worker_id": worker,
            }
        )
    return rows


__all__ = ["graph_work_attribution_status", "normalize_schedule_lifecycle"]

