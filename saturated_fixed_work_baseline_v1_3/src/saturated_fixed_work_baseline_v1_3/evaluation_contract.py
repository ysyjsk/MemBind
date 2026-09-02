"""Shared trace, ordering, refinement, and makespan validators for v1.3."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


class TraceValidationError(ValueError):
    """A raw event or refinement row cannot support a scientific claim."""


_EVENTS = {"FORMAL_START", "SUBMIT", "NATIVE_ENTER", "PUBLICATION_DURABLE", "CONSTRUCTION_SEAL", "QA_START", "QA_END"}
_METHOD_ORDER = {
    "B0": True,
    "B0_NATIVE_SERIAL": True,
    "GRAPHITI_UPSTREAM_SERIAL": True,
    "V6": True,
    "MEMBIND_V6": True,
    "MEMBIND_V6_1": True,
    "B1": False,
    "B1_NAIVE_WHOLE_UPDATE_ASYNC": False,
    "RELAXED_ORDER_PARALLEL": False,
    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO": True,
    "RELAXED_ORDER_SHARED_BOUNDED_SO": False,
    "MEMBIND_V6_1_SHARED_BOUNDED_SO": True,
}


def _event_index(row: Mapping[str, Any], fallback: int) -> int:
    value = row.get("event_index", fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TraceValidationError("event index is invalid")
    return value


def _timestamp(row: Mapping[str, Any]) -> int | None:
    value = row.get("monotonic_ns")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TraceValidationError("event timestamp is invalid")
    return value


def _normalized_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence) or not events:
        raise TraceValidationError("events must be a non-empty sequence")
    rows: list[dict[str, Any]] = []
    indices: set[int] = set()
    for fallback, raw in enumerate(events):
        if not isinstance(raw, Mapping):
            raise TraceValidationError("event row is invalid")
        row = dict(raw)
        event = row.get("event")
        if event not in _EVENTS:
            raise TraceValidationError(f"unknown event: {event}")
        index = _event_index(row, fallback)
        if index in indices:
            raise TraceValidationError("duplicate event index")
        indices.add(index)
        row["event_index"] = index
        row["monotonic_ns"] = _timestamp(row)
        sequence = row.get("source_sequence")
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0):
            raise TraceValidationError("source sequence is invalid")
        rows.append(row)
    rows.sort(key=lambda row: int(row["event_index"]))
    return rows


def _source_events(rows: Sequence[Mapping[str, Any]], event: str) -> dict[int, list[Mapping[str, Any]]]:
    result: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row.get("event") == event:
            sequence = row.get("source_sequence")
            if not isinstance(sequence, int):
                raise TraceValidationError(f"{event} is missing source sequence")
            result.setdefault(sequence, []).append(row)
    return result


def validate_block_trace(
    events: Sequence[Mapping[str, Any]],
    *,
    expected_source_count: int,
    method: str,
    context_id: str | None = None,
) -> dict[str, Any]:
    """Validate fixed work and calculate the common durable makespan."""

    if isinstance(expected_source_count, bool) or not isinstance(expected_source_count, int) or expected_source_count <= 0:
        raise TraceValidationError("expected source count is invalid")
    rows = _normalized_events(events)
    if context_id is not None:
        mismatches = [row for row in rows if row.get("context_id") not in {None, context_id}]
        if mismatches:
            raise TraceValidationError("cross-context event mixed into block")
    starts = [row for row in rows if row.get("event") == "FORMAL_START"]
    if len(starts) != 1 or starts[0].get("monotonic_ns") is None:
        raise TraceValidationError("FORMAL_START must occur exactly once with timestamp")
    submits = _source_events(rows, "SUBMIT")
    enters = _source_events(rows, "NATIVE_ENTER")
    terminals = _source_events(rows, "PUBLICATION_DURABLE")
    expected = set(range(expected_source_count))
    if set(submits) != expected:
        raise TraceValidationError("submitted work does not cover expected episodes")
    if set(terminals) != expected:
        unknown = sorted(set(terminals) - expected)
        if unknown:
            raise TraceValidationError("unknown episode in terminal publication")
        raise TraceValidationError("terminal publication coverage is incomplete")
    duplicates = [sequence for sequence, values in terminals.items() if len(values) != 1]
    if duplicates:
        raise TraceValidationError(f"duplicate terminal publication: {duplicates[0]}")
    if any(len(values) != 1 for values in submits.values()):
        raise TraceValidationError("duplicate submission")
    if set(enters) != expected or any(len(values) != 1 for values in enters.values()):
        raise TraceValidationError("native update enter coverage is incomplete")
    start_time = int(starts[0]["monotonic_ns"])
    publication_times: list[int] = []
    for sequence in range(expected_source_count):
        submit_time = _timestamp(submits[sequence][0])
        enter_time = _timestamp(enters[sequence][0])
        durable_time = _timestamp(terminals[sequence][0])
        if submit_time is None or enter_time is None or durable_time is None:
            raise TraceValidationError("lifecycle event timestamp is missing")
        if not (start_time < submit_time and start_time < enter_time):
            raise TraceValidationError("FORMAL_START does not precede construction")
        publication_times.append(durable_time)
    seals = [row for row in rows if row.get("event") == "CONSTRUCTION_SEAL"]
    if len(seals) != 1 or _timestamp(seals[0]) is None or int(seals[0]["monotonic_ns"]) <= max(publication_times):
        raise TraceValidationError("construction seal must follow durable publications")
    qa_starts = [row for row in rows if row.get("event") == "QA_START"]
    if qa_starts and any(_timestamp(row) is None or int(row["monotonic_ns"]) <= int(seals[0]["monotonic_ns"]) for row in qa_starts):
        raise TraceValidationError("QA started before construction seal")
    order = validate_order_contract(rows, expected_source_count=expected_source_count, method=method)
    if order["order_contract_status"] == "INVALID_TRACE":
        raise TraceValidationError("order trace is invalid")
    if _METHOD_ORDER.get(method, method.upper() != "B1") and order["order_contract_status"] != "PASS":
        raise TraceValidationError("ordered method violated publication contract")
    return {
        "schema_version": "membind.v1.3.lifecycle-validation.v1",
        "contract_status": "PASS",
        "expected_count": expected_source_count,
        "submitted_count": len(submits),
        "completed_count": len(terminals),
        "native_enter_count": len(enters),
        "formal_start_ns": start_time,
        "last_publication_durable_ns": max(publication_times),
        "t_build_ns": max(publication_times) - start_time,
        "order_validation": order,
    }


def validate_order_contract(
    events: Sequence[Mapping[str, Any]], *, expected_source_count: int, method: str
) -> dict[str, Any]:
    """Check adjacent native-enter/publication refinement order."""

    rows = _normalized_events(events)
    enters = _source_events(rows, "NATIVE_ENTER")
    terminals = _source_events(rows, "PUBLICATION_DURABLE")
    expected = set(range(expected_source_count))
    if set(enters) != expected or set(terminals) != expected:
        return {
            "schema_version": "membind.v1.3.order-validation.v1",
            "order_contract_status": "INVALID_TRACE",
            "ordered_pair_count": 0,
            "order_violation_count": 0,
            "inversion_count": 0,
            "first_violation": None,
        }
    violations: list[dict[str, Any]] = []
    invalid = False
    for sequence in range(1, expected_source_count):
        current = _timestamp(enters[sequence][0])
        previous = _timestamp(terminals[sequence - 1][0])
        if current is None or previous is None or current == previous:
            invalid = True
            continue
        if current <= previous:
            violations.append(
                {
                    "source_sequence": sequence,
                    "previous_source_sequence": sequence - 1,
                    "native_enter_ns": current,
                    "previous_publication_durable_ns": previous,
                    "native_event_index": enters[sequence][0].get("event_index"),
                    "previous_event_index": terminals[sequence - 1][0].get("event_index"),
                }
            )
    status = "INVALID_TRACE" if invalid else ("NOT_REQUIRED" if not _METHOD_ORDER.get(method, method.upper() != "B1") else ("FAIL" if violations else "PASS"))
    return {
        "schema_version": "membind.v1.3.order-validation.v1",
        "method": method,
        "order_contract_status": status,
        "ordered_pair_count": expected_source_count - 1,
        "order_violation_count": len(violations),
        "inversion_count": len(violations),
        "first_violation": violations[0] if violations else None,
    }


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise TraceValidationError(f"{field} is invalid")
    return value


def validate_v6_bindings(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise TraceValidationError("V6 binding rows are empty")
    seen: set[tuple[int, str, int]] = set()
    orphan = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TraceValidationError("V6 binding row is invalid")
        row = dict(raw)
        sequence = row.get("source_sequence")
        callsite = row.get("callsite")
        ordinal = row.get("ordinal_within_episode")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not isinstance(callsite, str) or isinstance(ordinal, bool) or not isinstance(ordinal, int):
            raise TraceValidationError("V6 binding identity is invalid")
        key = (sequence, callsite, ordinal)
        if key in seen:
            raise TraceValidationError("duplicate V6 binding consume")
        seen.add(key)
        status = row.get("match_status")
        discard_count = row.get("discard_count", 0)
        transport_attempt_count = row.get("transport_attempt_count", 0)
        external_attempted = row.get("external_transport_attempted", False)
        if (
            isinstance(discard_count, bool)
            or not isinstance(discard_count, int)
            or discard_count < 0
            or isinstance(transport_attempt_count, bool)
            or not isinstance(transport_attempt_count, int)
            or transport_attempt_count < 0
            or not isinstance(external_attempted, bool)
        ):
            raise TraceValidationError("V6 binding accounting is invalid")
        if row.get("external_transport_attempted_during_replay") is not False:
            raise TraceValidationError("replay attempted external transport")
        if status == "EXACT_MATCH":
            for field in (
                "request_identity_hash",
                "prepared_response_hash",
                "native_request_hash",
            ):
                _hash(row.get(field), field)
            if row.get("capture_count") != 1:
                raise TraceValidationError("capture count must equal one")
            if row.get("consume_count") != 1:
                raise TraceValidationError("consume count must equal one")
            if discard_count != 0 or row.get("fallback_type") is not None:
                raise TraceValidationError("exact binding has fallback accounting")
            if external_attempted or transport_attempt_count != 0:
                raise TraceValidationError("exact replay attempted external transport")
        elif status == "MISMATCH_FRESH_FALLBACK":
            for field in (
                "request_identity_hash",
                "prepared_response_hash",
                "native_request_hash",
            ):
                _hash(row.get(field), field)
            if row.get("capture_count") != 1 or row.get("consume_count") != 0:
                raise TraceValidationError("mismatch fallback must not consume")
            if discard_count != 1 or row.get("fallback_type") != "mismatch":
                raise TraceValidationError("mismatch fallback accounting is invalid")
            if not external_attempted:
                raise TraceValidationError("mismatch fallback lacks fresh transport")
        elif status == "MISSING_FRESH_FALLBACK":
            _hash(row.get("native_request_hash"), "native_request_hash")
            native_response = row.get("native_response_hash")
            _hash(native_response, "native_response_hash")
            if row.get("capture_count") != 0 or row.get("consume_count") != 0:
                raise TraceValidationError("missing fallback has capture/consume work")
            if discard_count != 0 or row.get("fallback_type") != "missing":
                raise TraceValidationError("missing fallback accounting is invalid")
            if not external_attempted:
                raise TraceValidationError("missing fallback lacks fresh transport")
        else:
            raise TraceValidationError("V6 binding status is invalid")
    return {
        "schema_version": "membind.v1.3.refinement-validation.v1",
        "refinement_status": "PASS",
        "binding_count": len(rows),
        "orphan_capture_count": orphan,
        "orphan_replay_count": orphan,
        "duplicate_consume_count": 0,
    }


__all__ = ["TraceValidationError", "validate_block_trace", "validate_order_contract", "validate_v6_bindings"]
