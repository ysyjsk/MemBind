"""Durable, idempotent publication journal for the M* commit boundary.

The journal records only operation/source/commit hashes and public state
transitions.  It is independent of Neo4j and therefore safe to exercise under
offline tripwires.  A recovery requires an external commit probe; the journal
never guesses that a database commit happened.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path

from .artifacts import append_jsonl_durable, payload_sha256


SCHEMA = "membind.paper-eval-v3.s5-mstar-publication-journal.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FIELDS = {
    "intent": {"event_sequence", "event_type", "operation_id", "source_sha256"},
    "commit": {
        "event_sequence",
        "event_type",
        "operation_id",
        "source_sha256",
        "commit_sha256",
    },
    "publication": {
        "event_sequence",
        "event_type",
        "operation_id",
        "source_sha256",
        "commit_sha256",
        "recovered",
    },
}


class S5MStarPublicationJournalError(ValueError):
    """Journal transition, durability, or integrity failure."""


def _fail(code: str) -> S5MStarPublicationJournalError:
    return S5MStarPublicationJournalError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _read_events(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise _fail("journal_unreadable") from None
    events: list[dict[str, object]] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("journal_record_invalid") from None
        if not isinstance(record, Mapping) or set(record) != {
            "event",
            "event_sha256",
        }:
            raise _fail("journal_record_invalid")
        event = record.get("event")
        if not isinstance(event, Mapping) or record.get("event_sha256") != payload_sha256(
            event
        ):
            raise _fail("journal_event_hash_invalid")
        row = dict(event)
        event_type = row.get("event_type")
        if event_type not in _EVENT_FIELDS or set(row) != _EVENT_FIELDS[event_type]:
            raise _fail("journal_event_shape_invalid")
        if row.get("event_sequence") != len(events):
            raise _fail("journal_event_sequence_invalid")
        _sha(row.get("operation_id"), "journal_operation_invalid")
        _sha(row.get("source_sha256"), "journal_source_invalid")
        if event_type in {"commit", "publication"}:
            _sha(row.get("commit_sha256"), "journal_commit_invalid")
        if event_type == "publication" and not isinstance(row.get("recovered"), bool):
            raise _fail("journal_recovery_flag_invalid")
        events.append(row)
    return events


class S5MStarPublicationJournal:
    """Append-only operation journal with deterministic duplicate handling."""

    def __init__(self, path: Path, events: list[dict[str, object]]) -> None:
        self.path = Path(path)
        self._events = deepcopy(events)
        self._intent_sources: dict[str, str] = {}
        self._commits: dict[str, tuple[str, str]] = {}
        self._published: set[str] = set()
        for event in self._events:
            operation = str(event["operation_id"])
            source = str(event["source_sha256"])
            event_type = event["event_type"]
            if event_type == "intent":
                if operation in self._intent_sources and self._intent_sources[operation] != source:
                    raise _fail("journal_intent_conflict")
                self._intent_sources[operation] = source
            elif event_type == "commit":
                if operation not in self._intent_sources:
                    raise _fail("journal_commit_without_intent")
                self._commits[operation] = (source, str(event["commit_sha256"]))
            elif event_type == "publication":
                if operation not in self._commits:
                    raise _fail("journal_publication_without_commit")
                self._published.add(operation)

    @classmethod
    def create(cls, path: Path) -> "S5MStarPublicationJournal":
        path = Path(path)
        if path.exists():
            raise _fail("journal_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
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
        return cls(path, [])

    @classmethod
    def load(cls, path: Path) -> "S5MStarPublicationJournal":
        path = Path(path)
        if not path.is_file():
            raise _fail("journal_missing")
        return cls(path, _read_events(path))

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(deepcopy(self._events))

    def _append(self, event_type: str, **fields: object) -> None:
        event = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            **fields,
        }
        if set(event) != _EVENT_FIELDS[event_type]:
            raise _fail("journal_event_shape_invalid")
        append_jsonl_durable(
            self.path,
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self._events.append(event)

    def record_intent(self, operation_id: str, source_sha256: str) -> str:
        operation_id = _sha(operation_id, "journal_operation_invalid")
        source_sha256 = _sha(source_sha256, "journal_source_invalid")
        prior = self._intent_sources.get(operation_id)
        if prior is not None:
            if prior != source_sha256:
                raise _fail("journal_intent_conflict")
            return "ALREADY_INTENT"
        self._append(
            "intent", operation_id=operation_id, source_sha256=source_sha256
        )
        self._intent_sources[operation_id] = source_sha256
        return "INTENT_RECORDED"

    def record_commit(self, operation_id: str, commit_sha256: str) -> str:
        operation_id = _sha(operation_id, "journal_operation_invalid")
        commit_sha256 = _sha(commit_sha256, "journal_commit_invalid")
        if operation_id not in self._intent_sources:
            raise _fail("journal_commit_without_intent")
        source = self._intent_sources[operation_id]
        prior = self._commits.get(operation_id)
        if prior is not None:
            if prior != (source, commit_sha256):
                raise _fail("commit_conflict")
            return "ALREADY_COMMITTED"
        self._append(
            "commit",
            operation_id=operation_id,
            source_sha256=source,
            commit_sha256=commit_sha256,
        )
        self._commits[operation_id] = (source, commit_sha256)
        return "COMMITTED"

    def record_publication(self, operation_id: str, *, recovered: bool = False) -> str:
        operation_id = _sha(operation_id, "journal_operation_invalid")
        if operation_id not in self._commits:
            raise _fail("publication_without_commit")
        if operation_id in self._published:
            return "ALREADY_PUBLISHED"
        source, commit = self._commits[operation_id]
        self._append(
            "publication",
            operation_id=operation_id,
            source_sha256=source,
            commit_sha256=commit,
            recovered=bool(recovered),
        )
        self._published.add(operation_id)
        return "PUBLISHED"

    def recover_publication(
        self,
        operation_id: str,
        commit_probe: Callable[[], bool],
    ) -> str:
        operation_id = _sha(operation_id, "journal_operation_invalid")
        if operation_id in self._published:
            return "ALREADY_PUBLISHED"
        if operation_id not in self._commits:
            raise _fail("commit_record_missing")
        if not callable(commit_probe):
            raise _fail("commit_probe_invalid")
        try:
            confirmed = commit_probe()
        except Exception:
            raise _fail("commit_probe_failed") from None
        if confirmed is not True:
            raise _fail("commit_not_confirmed")
        return self.record_publication(operation_id, recovered=True).replace(
            "PUBLISHED", "RECOVERED_PUBLICATION"
        )

    def published_operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._published))


__all__ = [
    "SCHEMA",
    "S5MStarPublicationJournal",
    "S5MStarPublicationJournalError",
]
