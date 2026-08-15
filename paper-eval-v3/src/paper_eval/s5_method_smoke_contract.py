"""Pure verifier for S5 method-smoke telemetry.

This module never schedules work or performs I/O.  It validates records from
future production adapters and keeps the P* scientific-outcome semantics
separate from the zero-violation requirements for A0 and M*.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


METHODS = ("A0", "P*", "M*")
_RECORD_FIELDS = {
    "method",
    "source_sequence",
    "worker_id",
    "arrival_timestamp_ns",
    "enqueue_ack_timestamp_ns",
    "service_start_timestamp_ns",
    "caller_return_timestamp_ns",
    "publish_timestamp_ns",
    "status",
    "error_class",
    "fallback",
    "intent_written",
    "publication_written",
    "direct_invariant_violation_count",
}
_PRIVATE_FIELDS = {
    "api_key",
    "content",
    "credential",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "secret",
}


class S5SmokeContractError(ValueError):
    """Future S5 smoke telemetry is incomplete, contradictory, or unsafe."""


def mstar_pipeline_to_smoke_records(
    evidence: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project verified M* pipeline events into the common smoke schema.

    The projection is intentionally lossless for the smoke-level accounting:
    intent is the arrival/ack watermark, commit return is caller return, and
    the prepare worker identifies the source's execution worker.  It never
    includes episode bodies or provider output.
    """

    if not isinstance(evidence, Mapping):
        raise S5SmokeContractError("M* evidence must be a mapping")
    if evidence.get("method") != "M*":
        raise S5SmokeContractError("M* evidence method drift")
    if evidence.get("status") != "PASS":
        raise S5SmokeContractError("M* evidence is not a successful block")
    events = evidence.get("events")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise S5SmokeContractError("M* evidence events are invalid")
    if not events or not isinstance(events[-1], Mapping) or events[-1].get("event_type") != "terminal_success":
        raise S5SmokeContractError("M* terminal success is missing")
    rows: dict[int, dict[str, Any]] = {}
    for index, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise S5SmokeContractError("M* event is invalid")
        if raw.get("event_sequence", index) != index:
            raise S5SmokeContractError("M* event sequence is invalid")
        event_type = raw.get("event_type")
        if event_type == "intent":
            source = raw.get("source_sequence")
            logical = raw.get("logical_time_ns")
            if isinstance(source, bool) or not isinstance(source, int) or source < 0:
                raise S5SmokeContractError("M* source identity is invalid")
            if isinstance(logical, bool) or not isinstance(logical, int) or logical < 0:
                raise S5SmokeContractError("M* intent timestamp is invalid")
            rows[source] = {
                "method": "M*",
                "source_sequence": source,
                "worker_id": 0,
                "arrival_timestamp_ns": logical,
                "enqueue_ack_timestamp_ns": logical,
                "status": "success",
                "error_class": None,
                "fallback": False,
                "intent_written": True,
                "publication_written": True,
                "direct_invariant_violation_count": 0,
            }
        elif event_type == "prepare_start":
            source = raw.get("source_sequence")
            worker = raw.get("worker_id")
            start = raw.get("prepare_start_timestamp_ns")
            if (
                isinstance(source, bool)
                or not isinstance(source, int)
                or source < 0
                or source not in rows
                or isinstance(worker, bool)
                or not isinstance(worker, int)
            ):
                raise S5SmokeContractError("M* prepare accounting is invalid")
            if isinstance(start, bool) or not isinstance(start, int) or start < 0:
                raise S5SmokeContractError("M* prepare timestamp is invalid")
            rows[source]["worker_id"] = worker
            rows[source]["service_start_timestamp_ns"] = start
        elif event_type == "commit_returned":
            source = raw.get("source_sequence")
            returned = raw.get("commit_return_timestamp_ns")
            if (
                isinstance(source, bool)
                or not isinstance(source, int)
                or source < 0
                or source not in rows
                or isinstance(returned, bool)
                or not isinstance(returned, int)
                or returned < 0
            ):
                raise S5SmokeContractError("M* commit accounting is invalid")
            rows[source]["caller_return_timestamp_ns"] = returned
        elif event_type == "publication":
            source = raw.get("source_sequence")
            published = raw.get("publication_timestamp_ns")
            if (
                isinstance(source, bool)
                or not isinstance(source, int)
                or source < 0
                or source not in rows
                or isinstance(published, bool)
                or not isinstance(published, int)
                or published < 0
            ):
                raise S5SmokeContractError("M* publication accounting is invalid")
            rows[source]["publish_timestamp_ns"] = published
    required = {
        "worker_id",
        "service_start_timestamp_ns",
        "caller_return_timestamp_ns",
        "publish_timestamp_ns",
    }
    if not rows or any(not required.issubset(row) for row in rows.values()):
        raise S5SmokeContractError("M* telemetry is incomplete")
    return [rows[index] for index in sorted(rows)]


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S5SmokeContractError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise S5SmokeContractError(f"{label} must be an integer >= {minimum}")
    return value


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _PRIVATE_FIELDS:
                raise S5SmokeContractError("smoke telemetry contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _overlap(rows: Sequence[Mapping[str, Any]]) -> bool:
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left["worker_id"] == right["worker_id"]:
                continue
            if max(
                left["service_start_timestamp_ns"],
                right["service_start_timestamp_ns"],
            ) < min(left["publish_timestamp_ns"], right["publish_timestamp_ns"]):
                return True
    return False


def validate_smoke_records(
    method: str,
    *,
    expected_source_sequences: Sequence[int],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one complete A0, P(C=2), or M(C=2) telemetry block."""

    if method not in METHODS:
        raise S5SmokeContractError("unsupported S5 method identity")
    if isinstance(expected_source_sequences, (str, bytes)) or not isinstance(
        expected_source_sequences, Sequence
    ):
        raise S5SmokeContractError("expected source sequences are invalid")
    expected = list(expected_source_sequences)
    if (
        not expected
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in expected
        )
        or expected != list(range(len(expected)))
    ):
        raise S5SmokeContractError("expected source coverage must be contiguous")
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise S5SmokeContractError("smoke records must be a sequence")
    _reject_private(records)
    rows = [_mapping(record, label="smoke record") for record in records]
    if not rows or any(set(row) != _RECORD_FIELDS for row in rows):
        raise S5SmokeContractError("smoke record shape drift")

    normalized: list[dict[str, Any]] = []
    for row in rows:
        source = _integer(row.get("source_sequence"), label="source_sequence")
        worker = _integer(row.get("worker_id"), label="worker_id")
        arrival = _integer(
            row.get("arrival_timestamp_ns"), label="arrival_timestamp_ns"
        )
        ack = _integer(
            row.get("enqueue_ack_timestamp_ns"), label="enqueue_ack_timestamp_ns"
        )
        start = _integer(
            row.get("service_start_timestamp_ns"), label="service_start_timestamp_ns"
        )
        caller_return = _integer(
            row.get("caller_return_timestamp_ns"),
            label="caller_return_timestamp_ns",
        )
        publish = _integer(
            row.get("publish_timestamp_ns"), label="publish_timestamp_ns"
        )
        violations = _integer(
            row.get("direct_invariant_violation_count"),
            label="direct_invariant_violation_count",
        )
        if (
            row.get("method") != method
            or row.get("status") != "success"
            or row.get("error_class") is not None
            or not isinstance(row.get("fallback"), bool)
            or row.get("intent_written") is not True
            or row.get("publication_written") is not True
            or not arrival <= ack <= caller_return <= publish
            or not arrival <= start <= publish
        ):
            raise S5SmokeContractError("smoke telemetry or publication accounting failed")
        normalized.append(
            {
                **row,
                "source_sequence": source,
                "worker_id": worker,
                "direct_invariant_violation_count": violations,
            }
        )

    sources = [row["source_sequence"] for row in normalized]
    if sorted(sources) != expected or len(sources) != len(set(sources)):
        raise S5SmokeContractError("smoke coverage contains loss or duplicate")
    fallback_count = sum(1 for row in normalized if row["fallback"])
    if fallback_count:
        raise S5SmokeContractError("smoke fallback is forbidden")
    total_violations = sum(
        row["direct_invariant_violation_count"] for row in normalized
    )
    publication_order = [
        row["source_sequence"]
        for row in sorted(
            normalized,
            key=lambda row: (row["publish_timestamp_ns"], row["source_sequence"]),
        )
    ]
    workers = {row["worker_id"] for row in normalized}
    overlap = _overlap(normalized)

    if method == "A0":
        if workers != {0}:
            raise S5SmokeContractError("A0 must use one FIFO worker")
        service_order = [
            row["source_sequence"]
            for row in sorted(
                normalized,
                key=lambda row: (
                    row["service_start_timestamp_ns"],
                    row["source_sequence"],
                ),
            )
        ]
        if service_order != expected or publication_order != expected:
            raise S5SmokeContractError("A0 FIFO or source-order publication failed")
        if total_violations:
            raise S5SmokeContractError("A0 direct invariant violation")
    elif method == "P*":
        if not workers.issubset({0, 1}) or len(workers) != 2 or not overlap:
            raise S5SmokeContractError("P(C=2) whole-update overlap was not observed")
    else:
        if publication_order != expected:
            raise S5SmokeContractError("M* source-order publication failed")
        if total_violations:
            raise S5SmokeContractError("M* direct invariant violation")

    return {
        "status": "PASS",
        "method": method,
        "episode_count": len(normalized),
        "coverage": len(normalized) / len(expected),
        "lost_count": 0,
        "duplicate_count": 0,
        "worker_count": len(workers),
        "publication_order": publication_order,
        "whole_update_overlap_observed": overlap if method == "P*" else None,
        "fallback_count": fallback_count,
        "direct_invariant_violation_count": total_violations,
        "scientific_outcome_not_adapter_failure": method == "P*",
        "post_return_stale_window_ns": [
            row["publish_timestamp_ns"] - row["caller_return_timestamp_ns"]
            for row in sorted(normalized, key=lambda row: row["source_sequence"])
        ],
    }
