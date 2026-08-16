"""Crash-consistent, isolated durable artifacts for S5 attempts.

The store is deliberately independent of Graphiti and of the scheduling core.
It reuses the repository's tested atomic JSON and fsynced JSONL primitives,
binds every event to one fresh S5 run, and never grants in-place resume.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from .artifacts import append_jsonl_durable, atomic_write_json, payload_sha256


MANIFEST_SCHEMA = "membind.paper-eval-v3.s5-attempt-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.s5-attempt-checkpoint.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.s5-attempt-result.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^s5-(?:a0|p-star|mstar)-[a-z0-9][a-z0-9-]{2,127}$")
_METHODS = {"A0", "P*", "M*"}
_RESULT_STATUS_BY_EVIDENCE_STATUS = {
    "PASS": "complete",
    "FAIL_CLOSED": "incomplete_non_mergeable",
    "SCIENTIFIC_OUTCOME_COMPLETE": "scientific_outcome_complete",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authority",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}


class S5StoreError(ValueError):
    """Sanitized durable-store or artifact-contract failure."""


def _fail(code: str) -> S5StoreError:
    return S5StoreError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_or_legacy_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _sealed_payload(value: Mapping[str, object], *, seal_field: str) -> dict[str, object]:
    candidate = deepcopy(dict(value))
    candidate.pop(seal_field, None)
    candidate[seal_field] = payload_sha256(candidate)
    return candidate


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _fsync_empty_file(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _published_source_sequences(
    events: Sequence[Mapping[str, object]],
) -> list[int]:
    published: list[int] = []
    for event in events:
        if event.get("event_type") != "publication":
            continue
        source = event.get("source_sequence")
        if isinstance(source, bool) or not isinstance(source, int) or source < 0:
            raise _fail("publication_source_invalid")
        if source in published:
            raise _fail("duplicate_publication")
        published.append(source)
    return published


class S5AttemptStore:
    """Manifest-first append-only event store for one non-reusable attempt."""

    def __init__(
        self,
        root: Path,
        *,
        run_id: str,
        method: str,
        production_core_identity_sha256: str,
        source_sha256s: tuple[str, ...],
    ) -> None:
        self.root = root
        self.manifest_path = root / "manifest.json"
        self.events_path = root / "events.jsonl"
        self.checkpoint_path = root / "checkpoint.json"
        self.result_path = root / "result.json"
        self.run_id = run_id
        self.method = method
        self.production_core_identity_sha256 = production_core_identity_sha256
        self.source_sha256s = source_sha256s
        self._next_event_sequence = 0

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        run_id: str,
        method: str,
        production_core_identity_sha256: str,
        source_sha256s: Sequence[str],
    ) -> "S5AttemptStore":
        root = Path(root)
        if root.exists():
            raise _fail("attempt_exists")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise _fail("run_id_invalid")
        if method not in _METHODS:
            raise _fail("method_invalid")
        _sha(production_core_identity_sha256, "production_core_identity_invalid")
        hashes = tuple(source_sha256s)
        if not hashes or any(_SHA256.fullmatch(value or "") is None for value in hashes):
            raise _fail("source_identity_invalid")
        root.mkdir(parents=True)
        manifest = _sealed_payload(
            {
                "schema_version": MANIFEST_SCHEMA,
                "run_id": run_id,
                "method": method,
                "production_core_identity_sha256": production_core_identity_sha256,
                "source_sha256s": list(hashes),
                "status": "planned",
                "resume_authorized": False,
            },
            seal_field="manifest_sha256",
        )
        _assert_public(manifest)
        atomic_write_json(root / "manifest.json", manifest)
        _fsync_empty_file(root / "events.jsonl")
        checkpoint = _sealed_payload(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "run_id": run_id,
                "status": "planned",
                "event_count": 0,
                "published_source_sequences": [],
                "result_payload_sha256": None,
                "resume_authorized": False,
            },
            seal_field="checkpoint_sha256",
        )
        atomic_write_json(root / "checkpoint.json", checkpoint)
        return cls(
            root,
            run_id=run_id,
            method=method,
            production_core_identity_sha256=production_core_identity_sha256,
            source_sha256s=hashes,
        )

    def _check_event(self, event: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(event, Mapping):
            raise _fail("event_invalid")
        candidate = deepcopy(dict(event))
        _assert_public(candidate)
        if (
            candidate.get("run_id") != self.run_id
            or candidate.get("method") != self.method
        ):
            raise _fail("event_identity_invalid")
        sequence = candidate.get("event_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != self._next_event_sequence:
            raise _fail("event_sequence_invalid")
        source = candidate.get("source_sequence")
        if source is not None:
            if isinstance(source, bool) or not isinstance(source, int) or source < 0 or source >= len(self.source_sha256s):
                raise _fail("event_source_invalid")
            if candidate.get("source_sha256") != self.source_sha256s[source]:
                raise _fail("event_source_invalid")
        return candidate

    def append_event(self, event: Mapping[str, object]) -> None:
        candidate = self._check_event(event)
        record = {
            "event": candidate,
            "event_sha256": payload_sha256(candidate),
        }
        append_jsonl_durable(self.events_path, record)
        self._next_event_sequence += 1
        events = self._current_events()
        checkpoint = _sealed_payload(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "run_id": self.run_id,
                "status": "running",
                "event_count": len(events),
                "published_source_sequences": _published_source_sequences(events),
                "result_payload_sha256": None,
                "resume_authorized": False,
            },
            seal_field="checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)

    def _current_events(self) -> list[dict[str, object]]:
        try:
            lines = self.events_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            raise _fail("events_unreadable") from None
        events: list[dict[str, object]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except (UnicodeError, json.JSONDecodeError):
                raise _fail("event_record_invalid") from None
            if not isinstance(record, dict) or set(record) != {"event", "event_sha256"}:
                raise _fail("event_record_invalid")
            event = record.get("event")
            if not isinstance(event, dict):
                raise _fail("event_record_invalid")
            if record.get("event_sha256") != payload_sha256(event):
                raise _fail("event_hash_invalid")
            _assert_public(event)
            events.append(event)
        if [event.get("event_sequence") for event in events] != list(range(len(events))):
            raise _fail("event_sequence_invalid")
        return events

    def finalize(self, evidence: Mapping[str, object]) -> dict[str, object]:
        candidate = deepcopy(dict(evidence))
        _assert_public(candidate)
        if (
            candidate.get("run_id") != self.run_id
            or candidate.get("method") != self.method
            or candidate.get("production_core_identity_sha256")
            != self.production_core_identity_sha256
        ):
            raise _fail("result_identity_invalid")
        events = candidate.get("events")
        if not isinstance(events, list) or events != self._current_events():
            raise _fail("result_event_binding_invalid")
        evidence_status = candidate.get("status")
        if evidence_status not in _RESULT_STATUS_BY_EVIDENCE_STATUS:
            raise _fail("result_status_invalid")
        if evidence_status == "SCIENTIFIC_OUTCOME_COMPLETE" and (
            self.method != "P*" or candidate.get("mergeable") is not True
        ):
            raise _fail("result_status_invalid")
        if self.result_path.exists():
            raise _fail("result_exists")
        result_status = _RESULT_STATUS_BY_EVIDENCE_STATUS[str(evidence_status)]
        result = _sealed_payload(
            {
                "schema_version": RESULT_SCHEMA,
                "run_id": self.run_id,
                "status": result_status,
                "resume_authorized": False,
                "payload": candidate,
            },
            seal_field="result_sha256",
        )
        _assert_public(result)
        atomic_write_json(self.result_path, result)
        published = _published_source_sequences(events)
        checkpoint = _sealed_payload(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "run_id": self.run_id,
                "status": result_status,
                "event_count": len(events),
                "published_source_sequences": list(published),
                "result_payload_sha256": payload_sha256(candidate),
                "resume_authorized": False,
            },
            seal_field="checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)
        return {
            "status": result_status,
            "result_payload_sha256": payload_sha256(candidate),
            "resume_authorized": False,
        }


def inspect_s5_attempt(root: Path) -> dict[str, object]:
    """Read-only integrity inspection; never turns an attempt into resume authority."""

    root = Path(root)
    manifest = _read_json(root / "manifest.json", "manifest_unreadable")
    if (
        set(manifest)
        != {
            "schema_version",
            "run_id",
            "method",
            "production_core_identity_sha256",
            "source_sha256s",
            "status",
            "resume_authorized",
            "manifest_sha256",
        }
        or manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("manifest_sha256")
        != payload_sha256({key: value for key, value in manifest.items() if key != "manifest_sha256"})
        or not isinstance(manifest.get("run_id"), str)
        or _RUN_ID.fullmatch(str(manifest.get("run_id"))) is None
        or manifest.get("method") not in _METHODS
        or not isinstance(manifest.get("production_core_identity_sha256"), str)
        or _SHA256.fullmatch(str(manifest.get("production_core_identity_sha256")))
        is None
        or not isinstance(manifest.get("source_sha256s"), list)
        or not manifest["source_sha256s"]
        or any(
            not isinstance(value, str) or _SHA256.fullmatch(value) is None
            for value in manifest["source_sha256s"]
        )
        or manifest.get("status") != "planned"
        or manifest.get("resume_authorized") is not False
    ):
        raise _fail("manifest_invalid")
    _assert_public(manifest)
    events_path = root / "events.jsonl"
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise _fail("events_unreadable") from None
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("event_record_invalid") from None
        if not isinstance(record, dict) or set(record) != {"event", "event_sha256"}:
            raise _fail("event_record_invalid")
        event = record.get("event")
        if not isinstance(event, dict) or record.get("event_sha256") != payload_sha256(event):
            raise _fail("event_hash_invalid")
        _assert_public(event)
        source = event.get("source_sequence")
        if (
            event.get("run_id") != manifest["run_id"]
            or event.get("method") != manifest["method"]
            or (
                source is not None
                and (
                    isinstance(source, bool)
                    or not isinstance(source, int)
                    or source < 0
                    or source >= len(manifest["source_sha256s"])
                    or event.get("source_sha256")
                    != manifest["source_sha256s"][source]
                )
            )
            or (source is None and "source_sha256" in event)
        ):
            raise _fail("event_manifest_binding_invalid")
        events.append(event)
    if [event.get("event_sequence") for event in events] != list(range(len(events))):
        raise _fail("event_sequence_invalid")
    checkpoint = _read_json(root / "checkpoint.json", "checkpoint_unreadable")
    if (
        checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or checkpoint.get("checkpoint_sha256")
        != payload_sha256({key: value for key, value in checkpoint.items() if key != "checkpoint_sha256"})
        or checkpoint.get("run_id") != manifest.get("run_id")
        or checkpoint.get("resume_authorized") is not False
    ):
        raise _fail("checkpoint_invalid")
    if (
        checkpoint.get("event_count") != len(events)
        or checkpoint.get("published_source_sequences")
        != _published_source_sequences(events)
    ):
        raise _fail("checkpoint_event_binding_invalid")
    result: dict[str, object] | None = None
    result_path = root / "result.json"
    if result_path.exists():
        result = _read_json(result_path, "result_unreadable")
        if (
            set(result)
            != {
                "schema_version",
                "run_id",
                "status",
                "resume_authorized",
                "payload",
                "result_sha256",
            }
            or result.get("schema_version") != RESULT_SCHEMA
            or result.get("result_sha256")
            != payload_sha256({key: value for key, value in result.items() if key != "result_sha256"})
            or result.get("run_id") != manifest.get("run_id")
            or result.get("resume_authorized") is not False
        ):
            raise _fail("result_invalid")
        _assert_public(result)
        payload = result.get("payload")
        if not isinstance(payload, Mapping):
            raise _fail("result_invalid")
        evidence_status = payload.get("status")
        expected_result_status = _RESULT_STATUS_BY_EVIDENCE_STATUS.get(
            str(evidence_status)
        )
        if (
            expected_result_status is None
            or payload.get("run_id") != manifest.get("run_id")
            or payload.get("method") != manifest.get("method")
            or payload.get("production_core_identity_sha256")
            != manifest.get("production_core_identity_sha256")
            or payload.get("events") != events
            or (
                evidence_status == "SCIENTIFIC_OUTCOME_COMPLETE"
                and (
                    manifest.get("method") != "P*"
                    or payload.get("mergeable") is not True
                )
            )
        ):
            raise _fail("result_manifest_binding_invalid")
        if (
            expected_result_status is None
            or result.get("status") != expected_result_status
            or checkpoint.get("status") != expected_result_status
            or checkpoint.get("result_payload_sha256") != payload_sha256(payload)
        ):
            raise _fail("checkpoint_result_binding_invalid")
    elif (
        checkpoint.get("status") not in {"planned", "running"}
        or checkpoint.get("result_payload_sha256") is not None
        or (checkpoint.get("status") == "planned" and events)
        or (checkpoint.get("status") == "running" and not events)
    ):
        raise _fail("checkpoint_result_binding_invalid")
    return {
        "manifest": manifest,
        "events": events,
        "event_count": len(events),
        "checkpoint": checkpoint,
        "result": result,
        "resume_authorized": False,
    }


__all__ = ["S5AttemptStore", "S5StoreError", "inspect_s5_attempt"]
