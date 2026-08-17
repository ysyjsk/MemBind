"""Crash-consistent public artifacts for one baseline-suite block.

The store is intentionally filesystem-only and single-attempt.  It appends
hash-bound events durably, atomically advances a non-resumable checkpoint,
and recognizes a completed block only after every source publication and a
sealed public result.  It never opens private configuration or live services.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path

from .artifacts import (
    append_jsonl_durable,
    atomic_write_json,
    canonical_bytes,
    payload_sha256,
)
from .baseline_suite import (
    CANARY_EPISODE_LIMITS,
    DEVELOPMENT_HISTORIES,
    BaselineSuiteError,
    baseline_block_namespace,
    canonicalize_baseline_method,
    verify_baseline_block_progress,
)


MANIFEST_SCHEMA = "membind.paper-eval-v3.baseline-block-manifest.v1"
CHECKPOINT_SCHEMA = "membind.paper-eval-v3.baseline-block-checkpoint.v1"
RESULT_SCHEMA = "membind.paper-eval-v3.baseline-block-result.v1"

MAX_SOURCE_COUNT = 10_000
MAX_EVENT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
_EVENTS_PER_SOURCE_LIMIT = 64
_EVENT_FIXED_ALLOWANCE = 256

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FAILURE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_BLOCK_FIELDS = {
    "block_index",
    "suite_run_id",
    "mode",
    "method",
    "history_id",
    "episode_limit",
    "attempt_ordinal",
    "namespace",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "base_url",
    "body",
    "content",
    "credential",
    "credentials",
    "episode",
    "messages",
    "password",
    "prompt",
    "raw_content",
    "raw_input",
    "raw_output",
    "raw_prompt",
    "raw_request",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
    "uri",
    "url",
}


class BaselineBlockArtifactError(ValueError):
    """A stable, sanitized durable block contract failed."""


def _fail(code: str) -> BaselineBlockArtifactError:
    return BaselineBlockArtifactError(code)


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _fail("private_or_invalid_field")
            folded = key.casefold()
            if (
                folded in _PRIVATE_FIELDS
                or folded.startswith("raw_")
                or folded.endswith(
                    (
                        "_api_key",
                        "_authorization",
                        "_base_url",
                        "_credential",
                        "_password",
                        "_prompt",
                        "_secret",
                        "_token",
                        "_uri",
                        "_url",
                    )
                )
            ):
                raise _fail("private_or_invalid_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _sealed(value: Mapping[str, object], field: str) -> dict[str, object]:
    sealed = deepcopy(dict(value))
    sealed.pop(field, None)
    sealed[field] = payload_sha256(sealed)
    return sealed


def _verify_seal(value: Mapping[str, object], field: str, code: str) -> None:
    if value.get(field) != payload_sha256(
        {key: item for key, item in value.items() if key != field}
    ):
        raise _fail(code)


def _read_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_empty_durable(path: Path) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _validated_block(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail("block_identity_invalid")
    block = deepcopy(dict(value))
    if set(block) != _BLOCK_FIELDS:
        raise _fail("block_identity_invalid")
    try:
        method = canonicalize_baseline_method(block.get("method"))
    except BaselineSuiteError:
        raise _fail("block_identity_invalid") from None
    if block.get("mode") not in {"canary", "development"}:
        raise _fail("block_identity_invalid")
    if block.get("history_id") not in DEVELOPMENT_HISTORIES:
        raise _fail("block_identity_invalid")
    for field in ("block_index", "attempt_ordinal"):
        item = block.get(field)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise _fail("block_identity_invalid")
    if block["attempt_ordinal"] < 1:
        raise _fail("block_identity_invalid")
    expected_limit = (
        CANARY_EPISODE_LIMITS[method]
        if block["mode"] == "canary"
        else None
    )
    if block.get("episode_limit") != expected_limit:
        raise _fail("block_identity_invalid")
    try:
        expected_namespace = baseline_block_namespace(
            suite_run_id=str(block.get("suite_run_id")),
            method=method,
            history_id=str(block.get("history_id")),
            attempt_ordinal=int(block["attempt_ordinal"]),
        )
    except (BaselineSuiteError, TypeError, ValueError):
        raise _fail("block_identity_invalid") from None
    if block.get("namespace") != expected_namespace:
        raise _fail("block_identity_invalid")
    _assert_public(block)
    return block


def _validated_inventory(
    expected_sequences: Sequence[int],
    source_sha256s: Sequence[str],
) -> tuple[tuple[int, ...], tuple[str, ...]]:
    expected = tuple(expected_sequences)
    hashes = tuple(source_sha256s)
    if not expected or expected != tuple(range(len(expected))):
        raise _fail("expected_sequences_invalid")
    if len(expected) > MAX_SOURCE_COUNT:
        raise _fail("source_inventory_too_large")
    if len(hashes) != len(expected):
        raise _fail("source_identity_invalid")
    if any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in hashes):
        raise _fail("source_identity_invalid")
    return expected, hashes


def _event_limit(source_count: int) -> int:
    return source_count * _EVENTS_PER_SOURCE_LIMIT + _EVENT_FIXED_ALLOWANCE


def _load_events(
    path: Path,
    *,
    block: Mapping[str, object],
    expected_sequences: Sequence[int],
    source_sha256s: Sequence[str],
) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise _fail("events_unreadable") from None
    if len(lines) > _event_limit(len(expected_sequences)):
        raise _fail("event_inventory_too_large")
    events: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        if len(line.encode("utf-8")) > MAX_EVENT_BYTES:
            raise _fail("event_too_large")
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
        if event.get("event_sequence") != index:
            raise _fail("event_sequence_invalid")
        if (
            event.get("run_id") != block.get("namespace")
            or event.get("method") != block.get("method")
        ):
            raise _fail("event_identity_invalid")
        source = event.get("source_sequence")
        if source is None:
            if "source_sha256" in event:
                raise _fail("event_source_invalid")
        elif (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source not in expected_sequences
            or event.get("source_sha256") != source_sha256s[source]
        ):
            raise _fail("event_source_invalid")
        if not isinstance(event.get("event_type"), str) or not event["event_type"]:
            raise _fail("event_type_invalid")
        events.append(event)
    return events


def _completed_sequences(events: Sequence[Mapping[str, object]]) -> list[int]:
    completed: list[int] = []
    for event in events:
        if event.get("event_type") != "publication":
            continue
        source = event.get("source_sequence")
        if not isinstance(source, int) or isinstance(source, bool):
            raise _fail("publication_source_invalid")
        if source in completed:
            raise _fail("duplicate_publication")
        completed.append(source)
    return completed


def _verify_progress(
    *,
    method: str,
    expected: Sequence[int],
    completed: Sequence[int],
    status: str,
) -> None:
    try:
        verify_baseline_block_progress(
            method=method,
            expected_sequences=expected,
            completed_sequences=completed,
            status=status,
        )
    except BaselineSuiteError:
        raise _fail("block_progress_invalid_or_not_prefix") from None


def _checkpoint_body(
    *,
    block_sha256: str,
    phase: str,
    event_count: int,
    completed_sequences: Sequence[int],
    result_payload_sha256: str | None,
    result_sha256: str | None,
    completed: bool,
) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "block_sha256": block_sha256,
        "phase": phase,
        "status": "completed" if completed else "incomplete_non_mergeable",
        "event_count": event_count,
        "completed_sequences": list(completed_sequences),
        "result_payload_sha256": result_payload_sha256,
        "result_sha256": result_sha256,
        "artifacts_verified": completed,
        "resume_authorized": False,
    }


class BaselineBlockStore:
    """Exclusive, append-only durable store for one baseline method/history."""

    def __init__(
        self,
        root: Path,
        *,
        block: Mapping[str, object],
        expected_sequences: Sequence[int],
        source_sha256s: Sequence[str],
    ) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"
        self.events_path = self.root / "events.jsonl"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.result_path = self.root / "result.json"
        self.block = deepcopy(dict(block))
        self.expected_sequences = tuple(expected_sequences)
        self.source_sha256s = tuple(source_sha256s)

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        block: Mapping[str, object],
        expected_sequences: Sequence[int],
        source_sha256s: Sequence[str],
    ) -> "BaselineBlockStore":
        target = Path(root)
        if target.exists():
            raise _fail("block_exists")
        selected_block = _validated_block(block)
        expected, hashes = _validated_inventory(expected_sequences, source_sha256s)
        target.mkdir(parents=True, exist_ok=False)
        _fsync_directory(target.parent)
        block_hash = payload_sha256(selected_block)
        manifest = _sealed(
            {
                "schema_version": MANIFEST_SCHEMA,
                "block": selected_block,
                "block_sha256": block_hash,
                "expected_sequences": list(expected),
                "source_sha256s": list(hashes),
                "max_event_count": _event_limit(len(expected)),
            },
            "manifest_sha256",
        )
        _assert_public(manifest)
        atomic_write_json(target / "manifest.json", manifest)
        _create_empty_durable(target / "events.jsonl")
        checkpoint = _sealed(
            _checkpoint_body(
                block_sha256=block_hash,
                phase="planned",
                event_count=0,
                completed_sequences=[],
                result_payload_sha256=None,
                result_sha256=None,
                completed=False,
            ),
            "checkpoint_sha256",
        )
        atomic_write_json(target / "checkpoint.json", checkpoint)
        return cls(
            target,
            block=selected_block,
            expected_sequences=expected,
            source_sha256s=hashes,
        )

    def _inspect(self) -> dict[str, object]:
        return inspect_baseline_block(self.root, self.block)

    def append_event(self, event: Mapping[str, object]) -> None:
        inspected = self._inspect()
        if inspected["phase"] not in {"planned", "running"}:
            raise _fail("block_terminal_or_quality_pending")
        if not isinstance(event, Mapping):
            raise _fail("event_invalid")
        candidate = deepcopy(dict(event))
        _assert_public(candidate)
        if len(canonical_bytes(candidate)) > MAX_EVENT_BYTES:
            raise _fail("event_too_large")
        if candidate.get("event_sequence") != inspected["event_count"]:
            raise _fail("event_sequence_invalid")
        if (
            candidate.get("run_id") != self.block["namespace"]
            or candidate.get("method") != self.block["method"]
        ):
            raise _fail("event_identity_invalid")
        source = candidate.get("source_sequence")
        if source is None:
            if "source_sha256" in candidate:
                raise _fail("event_source_invalid")
        elif (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source not in self.expected_sequences
            or candidate.get("source_sha256") != self.source_sha256s[source]
        ):
            raise _fail("event_source_invalid")
        if not isinstance(candidate.get("event_type"), str) or not candidate["event_type"]:
            raise _fail("event_type_invalid")
        prospective = list(inspected["completed_sequences"])
        if candidate["event_type"] == "publication":
            if source is None:
                raise _fail("publication_source_invalid")
            if source in prospective:
                raise _fail("duplicate_publication")
            prospective.append(source)
        _verify_progress(
            method=str(self.block["method"]),
            expected=self.expected_sequences,
            completed=prospective,
            status="running",
        )
        if int(inspected["event_count"]) >= _event_limit(len(self.expected_sequences)):
            raise _fail("event_inventory_too_large")
        append_jsonl_durable(
            self.events_path,
            {"event": candidate, "event_sha256": payload_sha256(candidate)},
        )
        checkpoint = _sealed(
            _checkpoint_body(
                block_sha256=payload_sha256(self.block),
                phase="running",
                event_count=int(inspected["event_count"]) + 1,
                completed_sequences=prospective,
                result_payload_sha256=None,
                result_sha256=None,
                completed=False,
            ),
            "checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)

    def mark_quality_pending(self) -> dict[str, object]:
        inspected = self._inspect()
        if inspected["phase"] not in {"planned", "running"}:
            raise _fail("block_terminal_or_quality_pending")
        if set(inspected["completed_sequences"]) != set(self.expected_sequences):
            raise _fail("full_publication_set_required")
        _verify_progress(
            method=str(self.block["method"]),
            expected=self.expected_sequences,
            completed=inspected["completed_sequences"],
            status="quality_pending",
        )
        checkpoint = _sealed(
            _checkpoint_body(
                block_sha256=payload_sha256(self.block),
                phase="quality_pending",
                event_count=int(inspected["event_count"]),
                completed_sequences=inspected["completed_sequences"],
                result_payload_sha256=None,
                result_sha256=None,
                completed=False,
            ),
            "checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)
        return {
            "phase": "quality_pending",
            "status": "incomplete_non_mergeable",
            "artifacts_verified": False,
            "resume_authorized": False,
        }

    def complete(self, result: Mapping[str, object]) -> dict[str, object]:
        inspected = self._inspect()
        if inspected["phase"] != "quality_pending":
            raise _fail("quality_pending_required")
        candidate = deepcopy(dict(result)) if isinstance(result, Mapping) else {}
        _assert_public(candidate)
        if len(canonical_bytes(candidate)) > MAX_RESULT_BYTES:
            raise _fail("result_too_large")
        if (
            candidate.get("run_id") != self.block["namespace"]
            or candidate.get("method") != self.block["method"]
            or candidate.get("status") != "PASS"
        ):
            raise _fail("result_identity_or_status_invalid")
        if set(inspected["completed_sequences"]) != set(self.expected_sequences):
            raise _fail("full_publication_set_required")
        payload_hash = payload_sha256(candidate)
        wrapped = _sealed(
            {
                "schema_version": RESULT_SCHEMA,
                "block_sha256": payload_sha256(self.block),
                "status": "completed",
                "artifacts_verified": True,
                "resume_authorized": False,
                "payload": candidate,
                "result_payload_sha256": payload_hash,
            },
            "result_sha256",
        )
        atomic_write_json(self.result_path, wrapped)
        checkpoint = _sealed(
            _checkpoint_body(
                block_sha256=payload_sha256(self.block),
                phase="completed",
                event_count=int(inspected["event_count"]),
                completed_sequences=inspected["completed_sequences"],
                result_payload_sha256=payload_hash,
                result_sha256=str(wrapped["result_sha256"]),
                completed=True,
            ),
            "checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)
        return {
            "status": "completed",
            "artifacts_verified": True,
            "resume_authorized": False,
            "result_payload_sha256": payload_hash,
        }

    def fail(self, error_class: str, failure_stage: str) -> dict[str, object]:
        inspected = self._inspect()
        if inspected["phase"] in {"completed", "failed"}:
            raise _fail("block_terminal")
        if (
            not isinstance(error_class, str)
            or _SAFE_FAILURE.fullmatch(error_class) is None
            or not isinstance(failure_stage, str)
            or _SAFE_FAILURE.fullmatch(failure_stage) is None
        ):
            raise _fail("failure_identity_invalid")
        payload = {"error_class": error_class, "failure_stage": failure_stage}
        payload_hash = payload_sha256(payload)
        wrapped = _sealed(
            {
                "schema_version": RESULT_SCHEMA,
                "block_sha256": payload_sha256(self.block),
                "status": "incomplete_non_mergeable",
                "artifacts_verified": False,
                "resume_authorized": False,
                "payload": payload,
                "result_payload_sha256": payload_hash,
            },
            "result_sha256",
        )
        atomic_write_json(self.result_path, wrapped)
        checkpoint = _sealed(
            _checkpoint_body(
                block_sha256=payload_sha256(self.block),
                phase="failed",
                event_count=int(inspected["event_count"]),
                completed_sequences=inspected["completed_sequences"],
                result_payload_sha256=payload_hash,
                result_sha256=str(wrapped["result_sha256"]),
                completed=False,
            ),
            "checkpoint_sha256",
        )
        atomic_write_json(self.checkpoint_path, checkpoint)
        return {
            "status": "incomplete_non_mergeable",
            "artifacts_verified": False,
            "resume_authorized": False,
        }


def inspect_baseline_block(
    root: Path,
    expected_block: Mapping[str, object],
) -> dict[str, object]:
    """Verify one block without contacting services or granting resume."""

    target = Path(root)
    expected_identity = _validated_block(expected_block)
    manifest = _read_json(target / "manifest.json", "manifest_unreadable")
    if set(manifest) != {
        "schema_version",
        "block",
        "block_sha256",
        "expected_sequences",
        "source_sha256s",
        "max_event_count",
        "manifest_sha256",
    } or manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise _fail("manifest_invalid")
    _verify_seal(manifest, "manifest_sha256", "manifest_hash_invalid")
    _assert_public(manifest)
    block = manifest.get("block")
    if not isinstance(block, Mapping) or _validated_block(block) != expected_identity:
        raise _fail("block_identity_mismatch")
    block_hash = payload_sha256(expected_identity)
    if manifest.get("block_sha256") != block_hash:
        raise _fail("block_identity_hash_invalid")
    raw_expected = manifest.get("expected_sequences")
    raw_hashes = manifest.get("source_sha256s")
    if not isinstance(raw_expected, list) or not isinstance(raw_hashes, list):
        raise _fail("manifest_inventory_invalid")
    expected, hashes = _validated_inventory(raw_expected, raw_hashes)
    if manifest.get("max_event_count") != _event_limit(len(expected)):
        raise _fail("manifest_inventory_invalid")
    events = _load_events(
        target / "events.jsonl",
        block=expected_identity,
        expected_sequences=expected,
        source_sha256s=hashes,
    )
    completed_sequences = _completed_sequences(events)
    checkpoint = _read_json(target / "checkpoint.json", "checkpoint_unreadable")
    if set(checkpoint) != {
        "schema_version",
        "block_sha256",
        "phase",
        "status",
        "event_count",
        "completed_sequences",
        "result_payload_sha256",
        "result_sha256",
        "artifacts_verified",
        "resume_authorized",
        "checkpoint_sha256",
    } or checkpoint.get("schema_version") != CHECKPOINT_SCHEMA:
        raise _fail("checkpoint_invalid")
    _verify_seal(checkpoint, "checkpoint_sha256", "checkpoint_hash_invalid")
    _assert_public(checkpoint)
    if (
        checkpoint.get("block_sha256") != block_hash
        or checkpoint.get("event_count") != len(events)
        or checkpoint.get("completed_sequences") != completed_sequences
        or checkpoint.get("resume_authorized") is not False
    ):
        raise _fail("checkpoint_event_or_identity_binding_invalid")
    phase = checkpoint.get("phase")
    if phase not in {"planned", "running", "quality_pending", "completed", "failed"}:
        raise _fail("checkpoint_phase_invalid")
    if (phase == "planned" and events) or (phase == "running" and not events):
        raise _fail("checkpoint_phase_event_binding_invalid")
    progress_status = (
        "quality_pending"
        if phase == "quality_pending"
        else "completed"
        if phase == "completed"
        else "running"
    )
    _verify_progress(
        method=str(expected_identity["method"]),
        expected=expected,
        completed=completed_sequences,
        status=progress_status,
    )
    result_path = target / "result.json"
    result: dict[str, object] | None = None
    if result_path.exists():
        result = _read_json(result_path, "result_unreadable")
        if len(canonical_bytes(result)) > MAX_RESULT_BYTES:
            raise _fail("result_too_large")
        if set(result) != {
            "schema_version",
            "block_sha256",
            "status",
            "artifacts_verified",
            "resume_authorized",
            "payload",
            "result_payload_sha256",
            "result_sha256",
        } or result.get("schema_version") != RESULT_SCHEMA:
            raise _fail("result_invalid")
        _verify_seal(result, "result_sha256", "result_hash_invalid")
        _assert_public(result)
        payload = result.get("payload")
        if (
            not isinstance(payload, Mapping)
            or result.get("result_payload_sha256") != payload_sha256(payload)
        ):
            raise _fail("result_payload_invalid")
        if (
            result.get("block_sha256") != block_hash
            or result.get("resume_authorized") is not False
            or checkpoint.get("result_payload_sha256") != result.get("result_payload_sha256")
            or checkpoint.get("result_sha256") != result.get("result_sha256")
        ):
            raise _fail("checkpoint_result_binding_invalid")
    if phase == "completed":
        if (
            result is None
            or result.get("status") != "completed"
            or result.get("artifacts_verified") is not True
            or checkpoint.get("status") != "completed"
            or checkpoint.get("artifacts_verified") is not True
            or payload_sha256(result.get("payload")) != result.get("result_payload_sha256")
            or result["payload"].get("run_id") != expected_identity["namespace"]
            or result["payload"].get("method") != expected_identity["method"]
            or result["payload"].get("status") != "PASS"
        ):
            raise _fail("completed_result_invalid")
        status = "completed"
        artifacts_verified = True
    else:
        if (
            checkpoint.get("status") != "incomplete_non_mergeable"
            or checkpoint.get("artifacts_verified") is not False
        ):
            raise _fail("partial_status_invalid")
        if phase == "failed":
            if (
                result is None
                or result.get("status") != "incomplete_non_mergeable"
                or result.get("artifacts_verified") is not False
                or set(result.get("payload", {})) != {"error_class", "failure_stage"}
            ):
                raise _fail("failed_result_invalid")
        elif (
            result is not None
            or checkpoint.get("result_payload_sha256") is not None
            or checkpoint.get("result_sha256") is not None
        ):
            raise _fail("partial_result_invalid")
        status = "incomplete_non_mergeable"
        artifacts_verified = False
    return {
        "block": deepcopy(expected_identity),
        "manifest": manifest,
        "events": events,
        "event_count": len(events),
        "completed_sequences": completed_sequences,
        "phase": phase,
        "status": status,
        "checkpoint": checkpoint,
        "result": result,
        "artifacts_verified": artifacts_verified,
        "resume_authorized": False,
    }


__all__ = [
    "BaselineBlockArtifactError",
    "BaselineBlockStore",
    "inspect_baseline_block",
]
