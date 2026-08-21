"""Append-only attempts, durable journals, and atomic validated seals."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import ResumeIdentity, validate_resume_identity


class ArtifactError(ValueError):
    """Durability, resume identity, or seal validity failed closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ArtifactError("ARTIFACT_ALREADY_EXISTS") from None
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2
        ).encode("utf-8") + b"\n"
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


@dataclass(frozen=True, slots=True)
class SealEvidence:
    episode_task_count: int
    terminal_episode_task_count: int
    open_spans: int
    open_requests: int
    open_transactions: int
    orphan_tasks: int
    unobserved_exceptions: int
    service_idle: bool
    canonical_snapshot_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JournalRecovery:
    events: tuple[dict[str, Any], ...]
    truncated_tail: bool
    action: str


class AttemptStore:
    def __init__(self, root: Path, identity: ResumeIdentity) -> None:
        self.root = root
        self.identity = identity
        self.journal_path = root / "raw_events.jsonl"
        self.identity_path = root / "resume_identity.json"
        self.failure_path = root / "failure.json"
        self.timeout_path = root / "timeout_diagnosis.json"
        self.seal_path = root / "seal.json"

    @classmethod
    def create(cls, block_root: Path, identity: ResumeIdentity) -> "AttemptStore":
        if not isinstance(identity, ResumeIdentity):
            raise ArtifactError("RESUME_IDENTITY_INVALID")
        block_root.mkdir(parents=True, exist_ok=True)
        ordinal = 1
        while (block_root / f"attempt-{ordinal:03d}").exists():
            ordinal += 1
        root = block_root / f"attempt-{ordinal:03d}"
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            raise ArtifactError("ATTEMPT_CREATION_RACE") from None
        store = cls(root, identity)
        _create_json(
            store.identity_path,
            {
                "schema_version": "membind.saturated-fixed-work.resume-identity.v1",
                **asdict(identity),
            },
        )
        descriptor = os.open(
            store.journal_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        os.fsync(descriptor)
        os.close(descriptor)
        _fsync_directory(root)
        return store

    @classmethod
    def open_existing(
        cls, root: Path, expected_identity: ResumeIdentity
    ) -> "AttemptStore":
        try:
            value = json.loads((root / "resume_identity.json").read_text(encoding="utf-8"))
            observed = ResumeIdentity(
                **{
                    key: value[key]
                    for key in asdict(expected_identity)
                }
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ArtifactError("RESUME_IDENTITY_UNREADABLE") from None
        validate_resume_identity(expected_identity, observed)
        return cls(root, observed)

    def append_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if self.seal_path.exists() or self.failure_path.exists() or self.timeout_path.exists():
            raise ArtifactError("ATTEMPT_TERMINAL")
        recovered = self.recover_journal()
        if recovered.truncated_tail:
            raise ArtifactError("JOURNAL_TRUNCATED_NEW_ATTEMPT_REQUIRED")
        previous = (
            recovered.events[-1]["payload_sha256"] if recovered.events else "0" * 64
        )
        body = {
            "schema_version": "membind.saturated-fixed-work.raw-event.v1",
            "ordinal": len(recovered.events),
            "previous_sha256": previous,
            **dict(event),
        }
        body["payload_sha256"] = _hash(body)
        payload = _canonical_bytes(body) + b"\n"
        descriptor = os.open(self.journal_path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return body

    def recover_journal(self) -> JournalRecovery:
        try:
            payload = self.journal_path.read_bytes()
        except OSError:
            raise ArtifactError("JOURNAL_UNREADABLE") from None
        fragments = payload.splitlines()
        trailing_newline = not payload or payload.endswith(b"\n")
        events: list[dict[str, Any]] = []
        truncated = False
        previous = "0" * 64
        for index, fragment in enumerate(fragments):
            try:
                event = json.loads(fragment)
            except (UnicodeError, json.JSONDecodeError):
                if index == len(fragments) - 1 and not trailing_newline:
                    truncated = True
                    break
                raise ArtifactError("JOURNAL_EVENT_INVALID") from None
            if not isinstance(event, dict):
                raise ArtifactError("JOURNAL_EVENT_INVALID")
            observed_hash = event.pop("payload_sha256", None)
            if (
                event.get("ordinal") != index
                or event.get("previous_sha256") != previous
                or observed_hash != _hash(event)
            ):
                raise ArtifactError("JOURNAL_HASH_CHAIN_INVALID")
            event["payload_sha256"] = observed_hash
            events.append(event)
            previous = str(observed_hash)
        return JournalRecovery(
            events=tuple(events),
            truncated_tail=truncated,
            action=(
                "START_NEW_ATTEMPT_DO_NOT_APPEND" if truncated else "APPEND_ALLOWED"
            ),
        )

    def record_failure(
        self, error_type: str, diagnosis: Mapping[str, Any]
    ) -> dict[str, Any]:
        body = {
            "schema_version": "membind.saturated-fixed-work.failure.v1",
            "status": "FAILED_NON_MERGEABLE",
            "error_type": error_type,
            "diagnosis": dict(diagnosis),
            "next_action": "START_NEW_NAMESPACE_AND_ATTEMPT",
        }
        body["payload_sha256"] = _hash(body)
        _create_json(self.failure_path, body)
        return body

    def record_timeout(
        self, *, stage: str, terminal_tasks: int, expected_tasks: int
    ) -> dict[str, Any]:
        body = {
            "schema_version": "membind.saturated-fixed-work.timeout.v1",
            "status": "FAILED_TIMEOUT_ACTION_REQUIRED",
            "stage": stage,
            "terminal_tasks": terminal_tasks,
            "expected_tasks": expected_tasks,
            "next_action": "START_NEW_NAMESPACE_AND_ATTEMPT",
        }
        body["payload_sha256"] = _hash(body)
        _create_json(self.timeout_path, body)
        return body

    def seal(self, evidence: SealEvidence) -> dict[str, Any]:
        if not isinstance(evidence, SealEvidence):
            raise ArtifactError("SEAL_EVIDENCE_INVALID")
        if evidence.terminal_episode_task_count != evidence.episode_task_count:
            raise ArtifactError("EPISODE_TASKS_NOT_TERMINAL")
        for field, code in (
            ("open_spans", "OPEN_SPANS"),
            ("open_requests", "OPEN_REQUESTS"),
            ("open_transactions", "OPEN_TRANSACTIONS"),
            ("orphan_tasks", "ORPHAN_TASKS"),
            ("unobserved_exceptions", "UNOBSERVED_EXCEPTIONS"),
        ):
            if getattr(evidence, field) != 0:
                raise ArtifactError(code)
        if evidence.service_idle is not True:
            raise ArtifactError("SERVICE_NOT_IDLE")
        hashes = evidence.canonical_snapshot_hashes
        if len(hashes) < 2 or len(set(hashes)) != 1:
            raise ArtifactError("CANONICAL_SNAPSHOT_UNSTABLE")
        body = {
            "schema_version": "membind.saturated-fixed-work.block-seal.v1",
            "status": "VALIDATED_SEALED",
            "resume_identity": asdict(self.identity),
            "evidence": asdict(evidence),
            "journal_tail_sha256": (
                self.recover_journal().events[-1]["payload_sha256"]
                if self.recover_journal().events
                else "0" * 64
            ),
        }
        body["payload_sha256"] = _hash(body)
        _create_json(self.seal_path, body)
        return body

    def verify_seal(self) -> dict[str, Any]:
        try:
            body = json.loads(self.seal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ArtifactError("SEAL_UNREADABLE") from None
        observed = body.pop("payload_sha256", None)
        if body.get("status") != "VALIDATED_SEALED" or observed != _hash(body):
            raise ArtifactError("SEAL_INVALID")
        body["payload_sha256"] = observed
        return body


__all__ = [
    "ArtifactError",
    "AttemptStore",
    "JournalRecovery",
    "SealEvidence",
]

