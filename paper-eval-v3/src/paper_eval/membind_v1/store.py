"""Crash-consistent durable attempts for the isolated MemBind-v1 runtime.

The store is deliberately independent of Graphiti and network clients.  It
persists source-order lifecycle transitions and prepared artifacts before a
runner treats either as acknowledged.  Only a verified ``PREPARED_DURABLE``
boundary may be resumed in place.  Any crash that might have issued a prepare
or bind call without a durable terminal acknowledgement is non-mergeable; a
post-commit window is explicitly labelled ``AMBIGUOUS_COMMIT_POISONED``.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from paper_eval.membind_v1.delta import MemBindV1DeltaError, PreparedNodeArtifact
from paper_eval.membind_v1.frontier import (
    MemBindV1FrontierError,
    SourceOrderedFrontier,
)


MANIFEST_SCHEMA = "membind.paper-eval-v3.membind-v1-attempt-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.membind-v1-attempt-checkpoint.v1"
EVENT_SCHEMA = "membind.paper-eval-v3.membind-v1-attempt-event.v1"
_RUN_ID = re.compile(r"^mv1-[a-z0-9][a-z0-9-]{2,63}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{2,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATES = {
    "INTENT_DURABLE",
    "PREPARE_RUNNING",
    "PREPARED_DURABLE",
    "BIND_RUNNING",
    "COMMIT_RETURNED",
    "PUBLICATION_DURABLE",
    "AMBIGUOUS_COMMIT_POISONED",
}
_RESUMABLE_STATES = {"INTENT_DURABLE", "PREPARED_DURABLE"}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}


class MemBindV1StoreError(ValueError):
    """Durability, identity, or recovery checks failed closed."""


def _fail(code: str) -> MemBindV1StoreError:
    return MemBindV1StoreError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_or_invalid_field")
            _public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public(child)
    elif value is None or isinstance(value, (str, int, bool, float)):
        return
    else:
        raise _fail("public_value_invalid")


def _sealed(value: Mapping[str, object], field: str) -> dict[str, object]:
    body = deepcopy(dict(value))
    body.pop(field, None)
    body[field] = payload_sha256(body)
    return body


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(parsed, dict):
        raise _fail(code)
    return parsed


def _write_json_exclusive(path: Path, value: Mapping[str, object], code: str) -> None:
    """Create one fsynced JSON artifact without a check-then-write race."""

    serialized = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise _fail(code) from None
    try:
        offset = 0
        while offset < len(serialized):
            written = os.write(descriptor, serialized[offset:])
            if written < 1:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _validate_manifest(value: Mapping[str, object]) -> dict[str, object]:
    manifest = deepcopy(dict(value))
    expected = {
        "schema_version",
        "run_id",
        "namespace",
        "source_sha256s",
        "source_manifest_sha256",
        "execution_identity_sha256",
        "manifest_sha256",
    }
    if set(manifest) != expected or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise _fail("manifest_invalid")
    if _RUN_ID.fullmatch(_text(manifest.get("run_id"), "run_id_invalid")) is None:
        raise _fail("run_id_invalid")
    if _NAMESPACE.fullmatch(_text(manifest.get("namespace"), "namespace_invalid")) is None:
        raise _fail("namespace_invalid")
    source_hashes = manifest.get("source_sha256s")
    if (
        not isinstance(source_hashes, list)
        or not source_hashes
        or any(_SHA256.fullmatch(item or "") is None for item in source_hashes)
        or len(set(source_hashes)) != len(source_hashes)
    ):
        raise _fail("source_inventory_invalid")
    _sha(manifest.get("source_manifest_sha256"), "source_manifest_invalid")
    _sha(manifest.get("execution_identity_sha256"), "execution_identity_invalid")
    stored = _sha(manifest.get("manifest_sha256"), "manifest_hash_invalid")
    body = {key: item for key, item in manifest.items() if key != "manifest_sha256"}
    if stored != payload_sha256(body):
        raise _fail("manifest_hash_mismatch")
    _public(manifest)
    return manifest


def _artifact_from_document(value: Mapping[str, object]) -> PreparedNodeArtifact:
    try:
        payload = dict(value)
        supplied = payload.pop("artifact_sha256")
        if not isinstance(supplied, str):
            raise ValueError
        artifact = PreparedNodeArtifact.create(**payload)
    except (TypeError, ValueError, MemBindV1DeltaError):
        raise _fail("prepared_artifact_invalid") from None
    if artifact.artifact_sha256 != supplied:
        raise _fail("prepared_artifact_invalid")
    return artifact.verify()


def _event_payload(
    *, event_sequence: int, source_sequence: int, source_sha256: str, state: str
) -> dict[str, object]:
    return {
        "schema_version": EVENT_SCHEMA,
        "event_sequence": event_sequence,
        "source_sequence": source_sequence,
        "source_sha256": source_sha256,
        "state": state,
        "timestamp_ns": time.time_ns(),
    }


def _read_events(path: Path, manifest: Mapping[str, object]) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise _fail("events_unreadable") from None
    source_hashes = list(manifest["source_sha256s"])
    events: list[dict[str, object]] = []
    for expected_sequence, line in enumerate(lines):
        try:
            wrapped = json.loads(line)
        except (UnicodeError, json.JSONDecodeError):
            raise _fail("event_record_invalid") from None
        if not isinstance(wrapped, dict) or set(wrapped) != {"event", "event_sha256"}:
            raise _fail("event_record_invalid")
        event = wrapped.get("event")
        if not isinstance(event, dict) or wrapped.get("event_sha256") != payload_sha256(event):
            raise _fail("event_hash_invalid")
        expected_keys = {
            "schema_version",
            "event_sequence",
            "source_sequence",
            "source_sha256",
            "state",
            "timestamp_ns",
        }
        if set(event) != expected_keys or event.get("schema_version") != EVENT_SCHEMA:
            raise _fail("event_shape_invalid")
        if event.get("event_sequence") != expected_sequence:
            raise _fail("event_sequence_invalid")
        source = _nonnegative_int(event.get("source_sequence"), "event_source_invalid")
        if source >= len(source_hashes) or event.get("source_sha256") != source_hashes[source]:
            raise _fail("event_source_invalid")
        if event.get("state") not in _STATES:
            raise _fail("event_state_invalid")
        _nonnegative_int(event.get("timestamp_ns"), "event_timestamp_invalid")
        _public(event)
        events.append(event)
    return events


def _replay_frontier(source_count: int, events: Sequence[Mapping[str, object]]) -> SourceOrderedFrontier:
    frontier = SourceOrderedFrontier(source_count=source_count)
    methods = {
        "INTENT_DURABLE": frontier.record_intent,
        "PREPARE_RUNNING": frontier.record_prepare_started,
        "PREPARED_DURABLE": frontier.record_prepared,
        "BIND_RUNNING": frontier.record_bind_started,
        "COMMIT_RETURNED": frontier.record_commit_returned,
        "PUBLICATION_DURABLE": frontier.record_publication_durable,
        "AMBIGUOUS_COMMIT_POISONED": frontier.poison_ambiguous_commit,
    }
    try:
        for event in events:
            methods[str(event["state"])](int(event["source_sequence"]))
    except (KeyError, MemBindV1FrontierError, ValueError):
        raise _fail("frontier_event_transition_invalid") from None
    return frontier


def _resume_status(frontier: SourceOrderedFrontier) -> str:
    states = [frontier.state_of(index) for index in range(frontier.source_count)]
    if frontier.is_complete:
        return "NOT_NEEDED_COMPLETE"
    if "AMBIGUOUS_COMMIT_POISONED" in states or "COMMIT_RETURNED" in states:
        return "AMBIGUOUS_COMMIT_POISONED"
    if "BIND_RUNNING" in states:
        return "AMBIGUOUS_BIND_POISONED"
    if "PREPARE_RUNNING" in states:
        return "AMBIGUOUS_PREPARE_POISONED"
    return "RESUME_FROM_PREPARED_DURABLE"


def _checkpoint(
    *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]], frontier: SourceOrderedFrontier, completed: bool
) -> dict[str, object]:
    status = "complete" if completed else "incomplete_non_mergeable"
    resume = _resume_status(frontier)
    if resume == "RESUME_FROM_PREPARED_DURABLE":
        status = "resumable"
    return _sealed(
        {
            "schema_version": CHECKPOINT_SCHEMA,
            "manifest_sha256": manifest["manifest_sha256"],
            "status": status,
            "event_count": len(events),
            "published_frontier": frontier.published_frontier,
            "frontier_state": [frontier.state_of(index) for index in range(frontier.source_count)],
            "resume_status": resume,
        },
        "checkpoint_sha256",
    )


def _validate_checkpoint(
    value: Mapping[str, object], *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]], frontier: SourceOrderedFrontier
) -> dict[str, object]:
    checkpoint = deepcopy(dict(value))
    expected_keys = {
        "schema_version",
        "manifest_sha256",
        "status",
        "event_count",
        "published_frontier",
        "frontier_state",
        "resume_status",
        "checkpoint_sha256",
    }
    if set(checkpoint) != expected_keys or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA:
        raise _fail("checkpoint_invalid")
    stored = _sha(checkpoint.get("checkpoint_sha256"), "checkpoint_hash_invalid")
    body = {key: item for key, item in checkpoint.items() if key != "checkpoint_sha256"}
    if stored != payload_sha256(body):
        raise _fail("checkpoint_hash_mismatch")
    expected = _checkpoint(
        manifest=manifest,
        events=events,
        frontier=frontier,
        completed=frontier.is_complete,
    )
    if checkpoint != expected:
        raise _fail("checkpoint_state_mismatch")
    return checkpoint


class MemBindV1AttemptStore:
    """Manifest-first store for one fresh MemBind-v1 namespace attempt."""

    def __init__(self, root: Path, *, manifest: Mapping[str, object], events: Sequence[Mapping[str, object]], frontier: SourceOrderedFrontier) -> None:
        self.root = Path(root)
        self.manifest = deepcopy(dict(manifest))
        self.events_path = self.root / "events.jsonl"
        self.manifest_path = self.root / "manifest.json"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.prepared_root = self.root / "prepared"
        self._events = [deepcopy(dict(event)) for event in events]
        self._frontier = frontier

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        run_id: str,
        namespace: str,
        source_sha256s: Sequence[str],
        source_manifest_sha256: str,
        execution_identity_sha256: str,
    ) -> "MemBindV1AttemptStore":
        target = Path(root)
        if target.exists():
            raise _fail("attempt_exists")
        if _RUN_ID.fullmatch(_text(run_id, "run_id_invalid")) is None:
            raise _fail("run_id_invalid")
        if _NAMESPACE.fullmatch(_text(namespace, "namespace_invalid")) is None:
            raise _fail("namespace_invalid")
        hashes = list(source_sha256s)
        if not hashes or any(_SHA256.fullmatch(item or "") is None for item in hashes) or len(set(hashes)) != len(hashes):
            raise _fail("source_inventory_invalid")
        source_manifest = _sha(source_manifest_sha256, "source_manifest_invalid")
        execution_identity = _sha(execution_identity_sha256, "execution_identity_invalid")
        target.mkdir(parents=True)
        manifest = _sealed(
            {
                "schema_version": MANIFEST_SCHEMA,
                "run_id": run_id,
                "namespace": namespace,
                "source_sha256s": hashes,
                "source_manifest_sha256": source_manifest,
                "execution_identity_sha256": execution_identity,
            },
            "manifest_sha256",
        )
        _public(manifest)
        atomic_write_json(target / "manifest.json", manifest)
        prepared = target / "prepared"
        prepared.mkdir()
        descriptor = os.open(target / "events.jsonl", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        directory = os.open(target, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        frontier = SourceOrderedFrontier(source_count=len(hashes))
        store = cls(target, manifest=manifest, events=(), frontier=frontier)
        store._write_checkpoint()
        return store

    @classmethod
    def open_existing(cls, root: Path) -> "MemBindV1AttemptStore":
        checked = inspect_membind_v1_attempt(root)
        resume = str(checked["checkpoint"]["resume_status"])
        if resume != "RESUME_FROM_PREPARED_DURABLE":
            if resume == "AMBIGUOUS_COMMIT_POISONED":
                cls._durably_poison_commit(Path(root), checked)
            raise _fail("attempt_poisoned")
        return cls(
            Path(root),
            manifest=checked["manifest"],
            events=checked["events"],
            frontier=checked["_frontier"],
        )

    @classmethod
    def _durably_poison_commit(cls, root: Path, checked: Mapping[str, object]) -> None:
        frontier = checked.get("_frontier")
        manifest = checked.get("manifest")
        events = checked.get("events")
        if not isinstance(frontier, SourceOrderedFrontier) or not isinstance(manifest, Mapping) or not isinstance(events, list):
            return
        states = [frontier.state_of(index) for index in range(frontier.source_count)]
        if "COMMIT_RETURNED" not in states:
            return
        sequence = states.index("COMMIT_RETURNED")
        source_hashes = list(manifest["source_sha256s"])
        event = _event_payload(
            event_sequence=len(events),
            source_sequence=sequence,
            source_sha256=str(source_hashes[sequence]),
            state="AMBIGUOUS_COMMIT_POISONED",
        )
        append_jsonl_durable(root / "events.jsonl", {"event": event, "event_sha256": payload_sha256(event)})
        frontier.poison_ambiguous_commit(sequence)
        all_events = [*events, event]
        atomic_write_json(root / "checkpoint.json", _checkpoint(manifest=manifest, events=all_events, frontier=frontier, completed=False))

    @property
    def source_count(self) -> int:
        return self._frontier.source_count

    @property
    def published_frontier(self) -> int:
        return self._frontier.published_frontier

    def _append_transition(self, source_sequence: int, state: str) -> None:
        if state not in _STATES:
            raise _fail("event_state_invalid")
        source = _nonnegative_int(source_sequence, "source_sequence_invalid")
        source_hashes = list(self.manifest["source_sha256s"])
        if source >= len(source_hashes):
            raise _fail("source_sequence_out_of_range")
        callbacks = {
            "INTENT_DURABLE": self._frontier.record_intent,
            "PREPARE_RUNNING": self._frontier.record_prepare_started,
            "PREPARED_DURABLE": self._frontier.record_prepared,
            "BIND_RUNNING": self._frontier.record_bind_started,
            "COMMIT_RETURNED": self._frontier.record_commit_returned,
            "PUBLICATION_DURABLE": self._frontier.record_publication_durable,
            "AMBIGUOUS_COMMIT_POISONED": self._frontier.poison_ambiguous_commit,
        }
        try:
            callbacks[state](source)
        except MemBindV1FrontierError as error:
            raise _fail(str(error)) from None
        event = _event_payload(
            event_sequence=len(self._events),
            source_sequence=source,
            source_sha256=str(source_hashes[source]),
            state=state,
        )
        append_jsonl_durable(self.events_path, {"event": event, "event_sha256": payload_sha256(event)})
        self._events.append(event)
        self._write_checkpoint()

    def _write_checkpoint(self) -> None:
        atomic_write_json(
            self.checkpoint_path,
            _checkpoint(
                manifest=self.manifest,
                events=self._events,
                frontier=self._frontier,
                completed=self._frontier.is_complete,
            ),
        )

    def record_intent(self, source_sequence: int) -> None:
        self._append_transition(source_sequence, "INTENT_DURABLE")

    def record_prepare_started(self, source_sequence: int) -> None:
        self._append_transition(source_sequence, "PREPARE_RUNNING")

    def persist_prepared(self, artifact: PreparedNodeArtifact) -> None:
        if not isinstance(artifact, PreparedNodeArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            artifact.verify()
        except MemBindV1DeltaError:
            raise _fail("prepared_artifact_invalid") from None
        sequence = artifact.source_sequence
        if sequence >= self.source_count:
            raise _fail("prepared_source_identity")
        path = self.prepared_root / f"{sequence:08d}.json"
        if path.exists():
            raise _fail("prepared_artifact_exists")
        if self._frontier.state_of(sequence) != "PREPARE_RUNNING":
            raise _fail("prepared_source_identity")
        expected_hash = list(self.manifest["source_sha256s"])[sequence]
        if artifact.source_sha256 != expected_hash:
            raise _fail("prepared_source_identity")
        document = {**artifact.payload(), "artifact_sha256": artifact.artifact_sha256}
        _public(document)
        _write_json_exclusive(path, document, "prepared_artifact_exists")
        self._append_transition(sequence, "PREPARED_DURABLE")

    def prepared_artifact(self, source_sequence: int) -> PreparedNodeArtifact:
        sequence = _nonnegative_int(source_sequence, "source_sequence_invalid")
        path = self.prepared_root / f"{sequence:08d}.json"
        if not path.is_file():
            raise _fail("prepared_artifact_missing")
        return _artifact_from_document(_read_json(path, "prepared_artifact_invalid"))

    def record_bind_started(self, source_sequence: int) -> None:
        self._append_transition(source_sequence, "BIND_RUNNING")

    def record_commit_returned(self, source_sequence: int) -> None:
        self._append_transition(source_sequence, "COMMIT_RETURNED")

    def record_publication_durable(self, source_sequence: int) -> None:
        self._append_transition(source_sequence, "PUBLICATION_DURABLE")

    def complete(self) -> None:
        if not self._frontier.is_complete:
            raise _fail("attempt_not_complete")
        self._write_checkpoint()


def inspect_membind_v1_attempt(root: Path) -> dict[str, Any]:
    """Verify a durable attempt without opening Graphiti or external services."""

    target = Path(root)
    manifest = _validate_manifest(_read_json(target / "manifest.json", "manifest_unreadable"))
    events = _read_events(target / "events.jsonl", manifest)
    frontier = _replay_frontier(len(manifest["source_sha256s"]), events)
    prepared_sequences: list[int] = []
    for sequence in range(len(manifest["source_sha256s"])):
        state = frontier.state_of(sequence)
        path = target / "prepared" / f"{sequence:08d}.json"
        if state in {"PREPARED_DURABLE", "BIND_RUNNING", "COMMIT_RETURNED", "PUBLICATION_DURABLE", "AMBIGUOUS_COMMIT_POISONED"}:
            if not path.is_file():
                raise _fail("prepared_artifact_missing")
            artifact = _artifact_from_document(_read_json(path, "prepared_artifact_invalid"))
            if artifact.source_sequence != sequence or artifact.source_sha256 != manifest["source_sha256s"][sequence]:
                raise _fail("prepared_artifact_invalid")
            prepared_sequences.append(sequence)
        elif path.exists():
            raise _fail("prepared_artifact_state_mismatch")
    checkpoint = _validate_checkpoint(
        _read_json(target / "checkpoint.json", "checkpoint_unreadable"),
        manifest=manifest,
        events=events,
        frontier=frontier,
    )
    resume = _resume_status(frontier)
    state = [frontier.state_of(index) for index in range(frontier.source_count)]
    # Inspection makes the failure classification explicit even before a later
    # resume attempt durably appends its poison marker.
    if resume == "AMBIGUOUS_COMMIT_POISONED":
        state = ["AMBIGUOUS_COMMIT_POISONED" if item == "COMMIT_RETURNED" else item for item in state]
        checkpoint = dict(checkpoint)
        checkpoint["frontier_state"] = state
        checkpoint["resume_status"] = resume
        checkpoint["status"] = "incomplete_non_mergeable"
    return {
        "manifest": manifest,
        "events": events,
        "checkpoint": checkpoint,
        "prepared_source_sequences": prepared_sequences,
        "frontier_state": state,
        "_frontier": frontier,
    }


__all__ = [
    "MemBindV1AttemptStore",
    "MemBindV1StoreError",
    "inspect_membind_v1_attempt",
]
