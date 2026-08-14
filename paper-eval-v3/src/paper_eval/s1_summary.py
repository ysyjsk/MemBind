"""Fail-closed finalization of the durable S1 event/checkpoint pair.

The finalizer never trusts a single self-declared field.  It binds the run to
its artifact directory, validates every record envelope, and emits a sanitized
FAIL artifact instead of turning malformed evidence into an uncategorized
exception.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256, sha256_file


CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s1-checkpoint.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.s1-event.v1"
EXPECTED_HISTORY_ID = "07741c45"
_EVENT_TYPES = {"intent", "publication", "retrieval", "failure"}


def _hash_valid(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    body = dict(record)
    stored = body.pop("payload_sha256", None)
    return isinstance(stored, str) and stored == payload_sha256(body)


def _read_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Read JSONL while counting malformed and blank records."""

    records: list[dict[str, Any]] = []
    failures = 0
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if not line.strip():
            failures += 1
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            failures += 1
            continue
        if not isinstance(value, dict):
            failures += 1
            continue
        records.append(value)
    return records, failures


def _safe_sequence(event: dict[str, Any]) -> int | None:
    value = event.get("source_sequence")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _identity_ok(
    checkpoint: dict[str, Any],
    *,
    run_dir: Path,
    expected_history_id: str,
    expected_namespace: str | None,
) -> bool:
    run_id = checkpoint.get("run_id")
    namespace = checkpoint.get("namespace")
    return (
        isinstance(run_id, str)
        and run_id == run_dir.name
        and checkpoint.get("history_id") == expected_history_id
        and isinstance(namespace, str)
        and bool(namespace)
        and namespace.startswith("pev3-")
        and (expected_namespace is None or namespace == expected_namespace)
    )


def _base_payload(
    *,
    checkpoint: dict[str, Any],
    expected_episode_count: int,
    run_id: str,
    history_id: str,
    namespace: str,
) -> dict[str, Any]:
    return {
        "stage": "S1",
        "method": "U0",
        "history_id": history_id,
        "namespace": namespace,
        "coverage": {
            "expected": expected_episode_count,
            "intents": 0,
            "published": 0,
            "lost": list(range(expected_episode_count)),
            "duplicates": [],
        },
        "add_episode_call_count": 0,
        "unexpected_source_sequences": [],
        "serial_source_order": False,
        "retrieval_call_count": 0,
        "retrieval_result_ids": [],
        "failure_count": 0,
        "failure_error_classes": [],
        "integrity": {},
        "checkpoint_sha256": "missing",
        "events_sha256": "missing",
        "verdict": "FAIL",
    }


def finalize_s1_summary(
    *,
    run_dir: Path,
    output_path: Path,
    expected_episode_count: int,
    git_commit: str,
    expected_history_id: str = EXPECTED_HISTORY_ID,
    expected_namespace: str | None = None,
) -> dict[str, Any]:
    """Validate and finalize S1; malformed evidence is always a sanitized FAIL."""

    run_dir = Path(run_dir)
    run_id = run_dir.name
    checkpoint_path = run_dir / "checkpoint.json"
    events_path = run_dir / "events.jsonl"
    checkpoint: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    checkpoint_read_failure = 0
    event_parse_failures = 0

    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(checkpoint, dict):
            checkpoint = {}
            checkpoint_read_failure = 1
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        checkpoint_read_failure = 1
    try:
        events, event_parse_failures = _read_records(events_path)
    except (OSError, UnicodeError, TypeError):
        event_parse_failures = 1

    checkpoint_hash_valid = _hash_valid(checkpoint)
    checkpoint_schema_valid = checkpoint.get("schema_version") == CHECKPOINT_SCHEMA
    checkpoint_identity_valid = _identity_ok(
        checkpoint,
        run_dir=run_dir,
        expected_history_id=expected_history_id,
        expected_namespace=expected_namespace,
    )
    authoritative_history = expected_history_id
    # Production namespaces are protocol-scoped.  This prevents a fully
    # re-sealed artifact pair from silently changing its execution identity.
    authoritative_namespace = (
        expected_namespace
        if expected_namespace is not None
        else str(checkpoint.get("namespace") or "unknown")
    )

    event_hash_failures = sum(not _hash_valid(event) for event in events)
    event_schema_failures = sum(
        event.get("schema_version") != EVENT_SCHEMA for event in events
    )
    event_type_failures = sum(event.get("event_type") not in _EVENT_TYPES for event in events)
    event_identity_failures = sum(
        not (
            event.get("run_id") == run_id
            and event.get("history_id") == authoritative_history
            and event.get("namespace") == authoritative_namespace
        )
        for event in events
    )
    event_field_failures = 0
    for event in events:
        kind = event.get("event_type")
        sequence = _safe_sequence(event)
        if kind in {"intent", "publication"} and sequence is None:
            event_field_failures += 1
        if kind == "retrieval" and sequence is not None:
            event_field_failures += 1
        if kind == "failure" and sequence is not None and sequence < 0:
            event_field_failures += 1

    intents = [
        sequence
        for event in events
        if event.get("event_type") == "intent"
        for sequence in [_safe_sequence(event)]
        if sequence is not None
    ]
    published = [
        sequence
        for event in events
        if event.get("event_type") == "publication"
        for sequence in [_safe_sequence(event)]
        if sequence is not None
    ]
    retrieval_events = [event for event in events if event.get("event_type") == "retrieval"]
    failure_events = [event for event in events if event.get("event_type") == "failure"]
    failure_error_classes = sorted(
        {str(event["error_class"]) for event in failure_events if event.get("error_class")}
    )
    counts = Counter(published)
    expected = set(range(expected_episode_count))
    present = set(published)
    lost = sorted(expected - present)
    unexpected = sorted(present - expected)
    duplicates = sorted(sequence for sequence, count in counts.items() if count != 1)

    checkpoint_sequences: list[int] = []
    checkpoint_sequence_failure = 0
    raw_sequences = checkpoint.get("completed_source_sequences", [])
    if not isinstance(raw_sequences, list):
        checkpoint_sequence_failure = 1
    else:
        for value in raw_sequences:
            if isinstance(value, bool) or not isinstance(value, int):
                checkpoint_sequence_failure = 1
            else:
                checkpoint_sequences.append(value)
    checkpoint_shape_valid = (
        isinstance(checkpoint.get("status"), str)
        and checkpoint.get("status") in {"running", "incomplete", "completed"}
        and checkpoint_sequence_failure == 0
        and isinstance(checkpoint.get("retrieval_result_ids", []), list)
    )
    retrieval_ids = [
        str(result_id)
        for event in retrieval_events
        for result_ids in [event.get("result_ids", [])]
        if isinstance(result_ids, list)
        for result_id in result_ids
    ]
    retrieval_parity_valid = (
        len(retrieval_events) == 1
        and retrieval_ids == [str(value) for value in checkpoint.get("retrieval_result_ids", [])]
    )
    expected_pattern: list[str] = []
    for sequence in range(expected_episode_count):
        expected_pattern.extend((f"intent:{sequence}", f"publication:{sequence}"))
    expected_pattern.append("retrieval:None")
    observed_pattern = [
        f"{event.get('event_type')}:{_safe_sequence(event)}" for event in events
    ]
    event_pattern_valid = observed_pattern == expected_pattern
    integrity_clean = (
        checkpoint_read_failure == 0
        and checkpoint_hash_valid
        and checkpoint_schema_valid
        and checkpoint_identity_valid
        and checkpoint_shape_valid
        and event_parse_failures == 0
        and event_hash_failures == 0
        and event_schema_failures == 0
        and event_type_failures == 0
        and event_identity_failures == 0
        and event_field_failures == 0
        and event_pattern_valid
        and retrieval_parity_valid
        and checkpoint.get("error_class") is None
    )
    pass_gate = (
        integrity_clean
        and checkpoint.get("status") == "completed"
        and checkpoint_sequences == list(range(expected_episode_count))
        and published == list(range(expected_episode_count))
        and intents == list(range(expected_episode_count))
        and not lost
        and not unexpected
        and not duplicates
        and not failure_events
    )

    payload = _base_payload(
        checkpoint=checkpoint,
        expected_episode_count=expected_episode_count,
        run_id=run_id,
        history_id=authoritative_history,
        namespace=authoritative_namespace,
    )
    payload.update(
        {
            "coverage": {
                "expected": expected_episode_count,
                "intents": len(intents),
                "published": len(published),
                "lost": lost,
                "duplicates": duplicates,
            },
            "add_episode_call_count": len(intents),
            "unexpected_source_sequences": unexpected,
            "serial_source_order": published == list(range(expected_episode_count)),
            "retrieval_call_count": len(retrieval_events),
            "retrieval_result_ids": checkpoint.get("retrieval_result_ids", []),
            "failure_count": len(failure_events),
            "failure_error_classes": failure_error_classes,
            "integrity": {
                "checkpoint_hash_valid": checkpoint_hash_valid,
                "checkpoint_schema_valid": checkpoint_schema_valid,
                "checkpoint_identity_valid": checkpoint_identity_valid,
                "checkpoint_shape_valid": checkpoint_shape_valid,
                "event_parse_failures": event_parse_failures,
                "event_hash_failures": event_hash_failures,
                "event_schema_failures": event_schema_failures,
                "event_type_failures": event_type_failures,
                "event_identity_failures": event_identity_failures,
                "event_field_failures": event_field_failures,
                "event_pattern_valid": event_pattern_valid,
                "retrieval_parity_valid": retrieval_parity_valid,
            },
            "checkpoint_sha256": sha256_file(checkpoint_path),
            "events_sha256": sha256_file(events_path),
            "verdict": "PASS" if pass_gate else "FAIL",
        }
    )
    envelope = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(output_path, envelope)
    return envelope
