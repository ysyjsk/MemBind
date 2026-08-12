"""Crash-consistent, sanitized artifacts for the frozen C4/E3 replay.

This module is intentionally independent of Graphiti and all live services.  It
persists an immutable planned manifest before accepting outcome events, fsyncs
each JSONL append, atomically replaces checkpoints, and provides read-only
verification helpers.  Runtime/scheduling code supplies already-sanitized
timestamps and progress; raw episode bodies and exception messages never cross
this boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from native_characterization_c4 import (
    NativeCharacterizationC4Error,
    analyze_backlog,
    compute_episode_metrics,
)


MANIFEST_SCHEMA = "membind.native-characterization-c4-manifest.v1"
SCHEDULE_SCHEMA = "membind.native-characterization-c4-schedule-dry-run.v1"
EVENT_SCHEMA = "membind.native-characterization-c4-event.v1"
CHECKPOINT_SCHEMA = "membind.native-characterization-c4-checkpoint.v1"
VERIFICATION_SCHEMA = "membind.native-characterization-c4-verification.v1"
SUCCESS_SCHEMA = "membind.native-characterization-e3-sync-async.v1"
FAILURE_STATUS = "incomplete_invalid_non_mergeable"
FROZEN_EPISODES_PER_BLOCK = 49
FROZEN_BLOCK_COUNT = 10
FROZEN_TOTAL_EPISODES = FROZEN_EPISODES_PER_BLOCK * FROZEN_BLOCK_COUNT

FROZEN_METHODS = ("Native-Sync", "Native-Async-Serial")
FROZEN_LOADS = (0.5, 0.8, 1.0, 1.2, 1.5)
REQUIRED_PROVENANCE_HASHES = (
    "freeze_64k_sha256",
    "freeze_64k_payload_sha256",
    "c2_manifest_sha256",
    "c2_checkpoint_sha256",
    "c2_verification_sha256",
    "c2_verification_payload_sha256",
    "c3_analyzer_source_sha256",
    "c3_dependency_map_sha256",
    "c3_dependency_map_payload_sha256",
    "c3_e2_sha256",
    "c3_e2_payload_sha256",
    "dataset_source_sha256",
)

_RUN_ID_RE = re.compile(r"^c4-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")
_STATUS_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_EPISODE_CHECKPOINT_PATH_RE = re.compile(
    r"^blocks/([0-9]{3})/episodes/([0-9]{6})\.checkpoint\.json$"
)
_BLOCK_CHECKPOINT_PATH_RE = re.compile(r"^blocks/([0-9]{3})/checkpoint\.json$")
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "content",
        "cypher",
        "error_message",
        "exception",
        "exception_message",
        "messages",
        "parameters",
        "password",
        "prompt",
        "raw_prompt",
        "raw_response",
        "request",
        "response",
        "secret",
        "session_id",
    }
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[-_]?key|authorization)\s*[=:]\s*\S+)"
)
_EVENT_TYPES = ("enqueue", "publication", "failure")
_TOKEN_FIELDS = ("prompt_tokens", "output_tokens", "requested_max_tokens")
_EVENT_RESERVED_FIELDS = frozenset(
    {"schema_version", "run_id", "event_sequence", "event_type", "payload_sha256"}
)
_SUCCESS_RESULT_IDENTITY_FIELDS = (
    "block_index",
    "graph_namespace",
    "history_id",
    "method",
    "normalized_offered_load",
)
_SUCCESS_RESULT_FIELDS = frozenset(_SUCCESS_RESULT_IDENTITY_FIELDS)


class NativeCharacterizationC4ArtifactError(RuntimeError):
    """Fail-closed error containing only a stable sanitized error code."""


def _fail(code: str) -> None:
    raise NativeCharacterizationC4ArtifactError(code)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode one value as canonical ASCII JSON without a trailing newline."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("payload_not_canonical_json")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_sha256(value: Mapping[str, Any]) -> str:
    """Return the seal for a mapping, excluding any existing seal field."""

    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    return _sha256(canonical_json_bytes(candidate))


def seal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return an idempotently sealed copy of one sanitized JSON object."""

    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    candidate["payload_sha256"] = _sha256(canonical_json_bytes(candidate))
    return candidate


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS:
                _fail("artifact_not_sanitized")
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("artifact_not_sanitized")
        return
    if isinstance(value, str):
        if _SECRET_VALUE_RE.search(value):
            _fail("artifact_not_sanitized")
        return
    _fail("artifact_not_sanitized")


def _validate_sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _validate_nonnegative_int(value: Any, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(code)
    return value


def nullable_token_envelope(
    *,
    prompt_tokens: int | None = None,
    output_tokens: int | None = None,
    requested_max_tokens: int | None = None,
) -> dict[str, int | None]:
    """Build the only token envelope allowed in a failure artifact."""

    result: dict[str, int | None] = {
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "requested_max_tokens": requested_max_tokens,
    }
    for value in result.values():
        if value is not None:
            _validate_nonnegative_int(value, "token_envelope_invalid")
    return result


def _normalize_token_envelope(
    value: Mapping[str, Any] | None,
) -> dict[str, int | None]:
    if value is None:
        return nullable_token_envelope()
    if not isinstance(value, Mapping) or not set(value).issubset(_TOKEN_FIELDS):
        _fail("token_envelope_invalid")
    return nullable_token_envelope(
        prompt_tokens=value.get("prompt_tokens"),
        output_tokens=value.get("output_tokens"),
        requested_max_tokens=value.get("requested_max_tokens"),
    )


def _error_class(value: BaseException | type[BaseException] | str) -> str:
    if isinstance(value, BaseException):
        error_type = type(value)
        result = f"{error_type.__module__}.{error_type.__qualname__}"
    elif isinstance(value, type) and issubclass(value, BaseException):
        result = f"{value.__module__}.{value.__qualname__}"
    elif isinstance(value, str):
        result = value
    else:
        _fail("error_class_invalid")
    if _ERROR_CLASS_RE.fullmatch(result) is None:
        _fail("error_class_invalid")
    return result


def _validate_provenance_hashes(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(REQUIRED_PROVENANCE_HASHES):
        _fail("provenance_hashes_invalid")
    return {
        name: _validate_sha256(value.get(name), "provenance_hashes_invalid")
        for name in REQUIRED_PROVENANCE_HASHES
    }


def _planned_blocks(schedule: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks = schedule.get("block_schedules")
    history_id = schedule.get("history_id")
    expected = [
        (method, load) for method in FROZEN_METHODS for load in FROZEN_LOADS
    ]
    if (
        not isinstance(history_id, str)
        or not history_id
        or not isinstance(blocks, list)
        or len(blocks) != len(expected)
    ):
        _fail("schedule_blocks_invalid")
    normalized: list[dict[str, Any]] = []
    namespaces: set[str] = set()
    for index, (item, pair) in enumerate(zip(blocks, expected)):
        if not isinstance(item, Mapping):
            _fail("schedule_blocks_invalid")
        namespace = item.get("graph_namespace")
        if (
            item.get("block_index") != index
            or (item.get("method"), item.get("normalized_offered_load")) != pair
            or not isinstance(namespace, str)
            or not namespace.startswith("nc-e3-")
            or namespace in namespaces
            or item.get("history_id", history_id) != history_id
            or not isinstance(item.get("interarrival_ns"), int)
            or isinstance(item.get("interarrival_ns"), bool)
            or item.get("interarrival_ns") <= 0
        ):
            _fail("schedule_blocks_invalid")
        namespaces.add(namespace)
        normalized.append(
            {
                "block_index": index,
                "graph_namespace": namespace,
                "history_id": history_id,
                "interarrival_ns": item["interarrival_ns"],
                "method": pair[0],
                "normalized_offered_load": pair[1],
            }
        )
    return normalized


def _schedule_episode_ids(schedule: Mapping[str, Any]) -> list[str]:
    history_id = schedule.get("history_id")
    episode_ids = schedule.get("episode_ids")
    expected = [f"{history_id}:{index}" for index in range(FROZEN_EPISODES_PER_BLOCK)]
    if episode_ids != expected:
        _fail("schedule_episode_ids_invalid")
    return expected


def _validate_schedule(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        _fail("schedule_invalid")
    schedule = deepcopy(dict(value))
    observed = schedule.get("payload_sha256")
    if not isinstance(observed, str) or observed != payload_sha256(schedule):
        _fail("schedule_seal_invalid")
    if (
        schedule.get("schema_version") != SCHEDULE_SCHEMA
        or schedule.get("status") != "dry_run"
        or schedule.get("stage") != "C4/E3_OFFLINE_SCHEDULE"
    ):
        _fail("schedule_invalid")
    _assert_sanitized(schedule)
    _schedule_episode_ids(schedule)
    return schedule, _planned_blocks(schedule)


def _atomic_write_sealed_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    sealed = seal_payload(value)
    encoded = canonical_json_bytes(sealed) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return sealed


def _create_empty_fsynced(path: Path) -> None:
    try:
        with path.open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        _fail("event_log_already_exists")
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_sealed_json(path: Path, error_code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(error_code)
    if (
        not isinstance(value, dict)
        or raw != canonical_json_bytes(value) + b"\n"
        or value.get("payload_sha256") != payload_sha256(value)
    ):
        _fail(error_code)
    _assert_sanitized(value)
    return value


class C4ArtifactStore:
    """One non-resumable C4 attempt's durable artifact boundary."""

    def __init__(self, run_root: Path, run_id: str) -> None:
        self.run_root = run_root
        self.run_id = run_id
        self.manifest_path = run_root / "manifest.json"
        self.schedule_path = run_root / "schedule.json"
        self.events_path = run_root / "events.jsonl"
        self.root_checkpoint_path = run_root / "checkpoint.json"
        self.success_summary_path = run_root / "e3_sync_async.json"
        self._append_lock = threading.Lock()
        self._next_event_sequence = 0

    @classmethod
    def create(
        cls,
        runs_root: str | Path,
        run_id: str,
        schedule: Mapping[str, Any],
        provenance_hashes: Mapping[str, Any],
        creation_command: Sequence[str],
    ) -> "C4ArtifactStore":
        """Create a fresh run and persist its immutable plan before outcomes."""

        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            _fail("run_id_invalid")
        schedule_copy, blocks = _validate_schedule(schedule)
        provenance = _validate_provenance_hashes(provenance_hashes)
        if (
            not isinstance(creation_command, Sequence)
            or isinstance(creation_command, (str, bytes))
            or not creation_command
            or not all(isinstance(item, str) and item for item in creation_command)
        ):
            _fail("creation_command_invalid")
        _assert_sanitized(list(creation_command))

        root = Path(runs_root)
        root.mkdir(parents=True, exist_ok=True)
        run_root = root / run_id
        try:
            run_root.mkdir()
        except FileExistsError:
            try:
                if any(run_root.iterdir()):
                    _fail("run_directory_nonempty")
            except OSError:
                _fail("run_directory_invalid")
        if not run_root.is_dir() or run_root.is_symlink():
            _fail("run_directory_invalid")

        store = cls(run_root, run_id)
        manifest = {
            "schema_version": MANIFEST_SCHEMA,
            "run_id": run_id,
            "stage": "C4/E3",
            "status": "planned",
            "mergeable": False,
            "creation_command": list(creation_command),
            "schedule_schema_version": schedule_copy["schema_version"],
            "schedule_payload_sha256": schedule_copy["payload_sha256"],
            "source_c2_run_id": schedule_copy.get("run_id"),
            "history_id": schedule_copy.get("history_id"),
            "episode_ids": deepcopy(schedule_copy["episode_ids"]),
            "provenance_hashes": provenance,
            "planned_blocks": blocks,
        }
        # This is intentionally the first persisted file in the attempt.
        _atomic_write_sealed_json(store.manifest_path, manifest)
        _atomic_write_sealed_json(store.schedule_path, schedule_copy)
        _create_empty_fsynced(store.events_path)
        store.write_root_checkpoint(
            status="planned",
            progress={"completed_block_indices": [], "completed_episode_count": 0},
        )
        return store

    def _require_planned_manifest(self) -> dict[str, Any]:
        try:
            manifest = _read_sealed_json(self.manifest_path, "planned_manifest_invalid")
        except NativeCharacterizationC4ArtifactError:
            _fail("planned_manifest_invalid")
        if (
            manifest.get("schema_version") != MANIFEST_SCHEMA
            or manifest.get("run_id") != self.run_id
            or manifest.get("status") != "planned"
            or len(manifest.get("planned_blocks", [])) != 10
        ):
            _fail("planned_manifest_invalid")
        return manifest

    def _append_event(self, event_type: str, supplied: Mapping[str, Any]) -> dict[str, Any]:
        if event_type not in _EVENT_TYPES or not isinstance(supplied, Mapping):
            _fail("event_invalid")
        if set(supplied) & _EVENT_RESERVED_FIELDS:
            _fail("event_invalid")
        payload = deepcopy(dict(supplied))
        _assert_sanitized(payload)
        stage_failure = (
            event_type == "failure" and payload.get("failure_scope") == "stage"
        )
        if stage_failure:
            if payload.get("block_index") is not None or payload.get("source_sequence") is not None:
                _fail("failure_event_invalid")
            payload["block_index"] = None
            payload["source_sequence"] = None
        else:
            block_index = _validate_nonnegative_int(
                payload.get("block_index"), "event_invalid"
            )
            _validate_nonnegative_int(payload.get("source_sequence"), "event_invalid")
            if block_index >= FROZEN_BLOCK_COUNT:
                _fail("event_invalid")
        if event_type == "enqueue" and "enqueue_ack_timestamp_ns" in payload:
            _fail("enqueue_event_future_ack_invalid")
        if event_type == "failure":
            if payload.get("failure_scope") not in {"episode", "stage"}:
                _fail("failure_event_invalid")
            if payload.get("status") != FAILURE_STATUS:
                _fail("failure_event_invalid")
            payload["error_class"] = _error_class(payload.get("error_class"))
            payload["token_envelope"] = _normalize_token_envelope(
                payload.get("token_envelope")
            )

        with self._append_lock:
            self._require_planned_manifest()
            event = seal_payload(
                {
                    "schema_version": EVENT_SCHEMA,
                    "run_id": self.run_id,
                    "event_sequence": self._next_event_sequence,
                    "event_type": event_type,
                    **payload,
                }
            )
            encoded = canonical_json_bytes(event) + b"\n"
            try:
                with self.events_path.open("ab") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError:
                _fail("event_append_failed")
            self._next_event_sequence += 1
        return event

    def append_enqueue_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return self._append_event("enqueue", value)

    def append_publication_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return self._append_event("publication", value)

    def append_failure_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return self._append_event("failure", value)

    def _checkpoint(
        self,
        path: Path,
        *,
        checkpoint_level: str,
        status: str,
        block_index: int | None,
        source_sequence: int | None,
        progress: Mapping[str, Any],
        failure: Mapping[str, Any] | None = None,
    ) -> Path:
        self._require_planned_manifest()
        if not isinstance(status, str) or _STATUS_RE.fullmatch(status) is None:
            _fail("checkpoint_status_invalid")
        if not isinstance(progress, Mapping):
            _fail("checkpoint_progress_invalid")
        _assert_sanitized(progress)
        checkpoint: dict[str, Any] = {
            "schema_version": CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "stage": "C4/E3",
            "checkpoint_level": checkpoint_level,
            "status": status,
            "block_index": block_index,
            "source_sequence": source_sequence,
            "progress": deepcopy(dict(progress)),
        }
        if failure is not None:
            _assert_sanitized(failure)
            checkpoint["failure"] = deepcopy(dict(failure))
        _atomic_write_sealed_json(path, checkpoint)
        return path

    def write_episode_checkpoint(
        self,
        *,
        block_index: int,
        source_sequence: int,
        status: str,
        progress: Mapping[str, Any],
    ) -> Path:
        block_index = _validate_nonnegative_int(block_index, "checkpoint_identity_invalid")
        source_sequence = _validate_nonnegative_int(
            source_sequence, "checkpoint_identity_invalid"
        )
        if block_index >= 10:
            _fail("checkpoint_identity_invalid")
        path = (
            self.run_root
            / "blocks"
            / f"{block_index:03d}"
            / "episodes"
            / f"{source_sequence:06d}.checkpoint.json"
        )
        return self._checkpoint(
            path,
            checkpoint_level="episode",
            status=status,
            block_index=block_index,
            source_sequence=source_sequence,
            progress=progress,
        )

    def write_block_checkpoint(
        self,
        *,
        block_index: int,
        status: str,
        progress: Mapping[str, Any],
    ) -> Path:
        block_index = _validate_nonnegative_int(block_index, "checkpoint_identity_invalid")
        if block_index >= 10:
            _fail("checkpoint_identity_invalid")
        return self._checkpoint(
            self.run_root / "blocks" / f"{block_index:03d}" / "checkpoint.json",
            checkpoint_level="block",
            status=status,
            block_index=block_index,
            source_sequence=None,
            progress=progress,
        )

    def write_root_checkpoint(
        self, *, status: str, progress: Mapping[str, Any]
    ) -> Path:
        return self._checkpoint(
            self.root_checkpoint_path,
            checkpoint_level="root",
            status=status,
            block_index=None,
            source_sequence=None,
            progress=progress,
        )

    def record_failure(
        self,
        *,
        block_index: int,
        source_sequence: int,
        error: BaseException | type[BaseException] | str,
        completed_source_sequences: Sequence[int],
        completed_block_indices: Sequence[int],
        completed_episode_count: int,
        token_envelope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Durably stop an attempt without ever serializing ``str(error)``."""

        block_index = _validate_nonnegative_int(block_index, "failure_identity_invalid")
        source_sequence = _validate_nonnegative_int(
            source_sequence, "failure_identity_invalid"
        )
        if block_index >= FROZEN_BLOCK_COUNT or source_sequence >= FROZEN_EPISODES_PER_BLOCK:
            _fail("failure_identity_invalid")
        completed_sources = [
            _validate_nonnegative_int(item, "failure_progress_invalid")
            for item in completed_source_sequences
        ]
        completed_blocks = [
            _validate_nonnegative_int(item, "failure_progress_invalid")
            for item in completed_block_indices
        ]
        if (
            completed_sources != list(range(source_sequence))
            or completed_blocks != list(range(block_index))
        ):
            _fail("failure_progress_invalid")
        completed_episode_count = _validate_nonnegative_int(
            completed_episode_count, "failure_completed_episode_count_invalid"
        )
        exact_global_count = (
            block_index * FROZEN_EPISODES_PER_BLOCK + source_sequence
        )
        if completed_episode_count != exact_global_count:
            _fail("failure_completed_episode_count_invalid")
        _records, event_counts = _verify_events(self.events_path, self.run_id)
        if event_counts["failure"]:
            _fail("failure_already_recorded")
        if self.success_summary_path.exists() or self.success_summary_path.is_symlink():
            _fail("failure_after_success_invalid")
        failure = {
            "error_class": _error_class(error),
            "token_envelope": _normalize_token_envelope(token_envelope),
        }
        event = self.append_failure_event(
            {
                "block_index": block_index,
                "source_sequence": source_sequence,
                "failure_scope": "episode",
                "status": FAILURE_STATUS,
                **failure,
                "completed_episode_count": completed_episode_count,
                "completed_block_count": len(completed_blocks),
            }
        )
        episode_progress = {"completed_source_sequences": completed_sources}
        self._checkpoint(
            self.run_root
            / "blocks"
            / f"{block_index:03d}"
            / "episodes"
            / f"{source_sequence:06d}.checkpoint.json",
            checkpoint_level="episode",
            status=FAILURE_STATUS,
            block_index=block_index,
            source_sequence=source_sequence,
            progress=episode_progress,
            failure=failure,
        )
        self._checkpoint(
            self.run_root / "blocks" / f"{block_index:03d}" / "checkpoint.json",
            checkpoint_level="block",
            status=FAILURE_STATUS,
            block_index=block_index,
            source_sequence=None,
            progress=episode_progress,
            failure=failure,
        )
        root_progress = {
            "completed_block_indices": completed_blocks,
            "completed_episode_count": completed_episode_count,
            "failed_block_index": block_index,
            "failed_source_sequence": source_sequence,
        }
        self._checkpoint(
            self.root_checkpoint_path,
            checkpoint_level="root",
            status=FAILURE_STATUS,
            block_index=None,
            source_sequence=None,
            progress=root_progress,
            failure=failure,
        )
        return event

    def record_stage_failure(
        self,
        *,
        failure_stage: str,
        error: BaseException | type[BaseException] | str,
        completed_block_indices: Sequence[int],
        completed_episode_count: int,
        token_envelope: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a root-only failure after all 10x49 outcomes are durable."""

        if failure_stage not in {"finalization", "verification"}:
            _fail("failure_stage_invalid")
        completed_blocks = [
            _validate_nonnegative_int(item, "failure_progress_invalid")
            for item in completed_block_indices
        ]
        completed_episode_count = _validate_nonnegative_int(
            completed_episode_count, "failure_stage_progress_invalid"
        )
        if (
            completed_blocks != list(range(FROZEN_BLOCK_COUNT))
            or completed_episode_count != FROZEN_TOTAL_EPISODES
        ):
            _fail("failure_stage_progress_invalid")
        manifest = self._require_planned_manifest()
        schedule = _read_sealed_json(self.schedule_path, "schedule_seal_invalid")
        schedule, planned_blocks = _validate_schedule(schedule)
        records, event_counts = _verify_events(self.events_path, self.run_id)
        if event_counts["failure"]:
            _fail("failure_already_recorded")
        _checkpoint_count, checkpoints = _verify_checkpoints(
            self.run_root, self.run_id
        )
        if manifest.get("planned_blocks") != planned_blocks:
            _fail("manifest_schedule_binding_invalid")
        _reconstruct_success_evidence(manifest, schedule, records, checkpoints)
        failure = {
            "error_class": _error_class(error),
            "token_envelope": _normalize_token_envelope(token_envelope),
        }
        event = self.append_failure_event(
            {
                "block_index": None,
                "source_sequence": None,
                "failure_scope": "stage",
                "failure_stage": failure_stage,
                "status": FAILURE_STATUS,
                **failure,
                "completed_block_count": FROZEN_BLOCK_COUNT,
                "completed_episode_count": FROZEN_TOTAL_EPISODES,
            }
        )
        self._checkpoint(
            self.root_checkpoint_path,
            checkpoint_level="root",
            status=FAILURE_STATUS,
            block_index=None,
            source_sequence=None,
            progress={
                "completed_block_indices": completed_blocks,
                "completed_episode_count": completed_episode_count,
                "failure_stage": failure_stage,
            },
            failure=failure,
        )
        return event

    def finalize_success(
        self, block_results: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        """Seal the single-screening E3 result without rewriting the manifest."""

        manifest = self._require_planned_manifest()
        if self.success_summary_path.exists() or self.success_summary_path.is_symlink():
            _fail("success_summary_already_exists")
        records, event_counts = _verify_events(self.events_path, self.run_id)
        if event_counts["failure"]:
            _fail("success_after_failure_invalid")
        normalized_results = _validate_success_block_results(
            block_results, manifest["planned_blocks"]
        )
        schedule = _read_sealed_json(self.schedule_path, "schedule_seal_invalid")
        schedule, planned_blocks = _validate_schedule(schedule)
        if (
            manifest.get("schedule_payload_sha256") != schedule["payload_sha256"]
            or manifest.get("planned_blocks") != planned_blocks
            or manifest.get("episode_ids") != schedule.get("episode_ids")
        ):
            _fail("manifest_schedule_binding_invalid")
        _checkpoint_count, checkpoints = _verify_checkpoints(
            self.run_root, self.run_id
        )
        durable_evidence, reconstructed_results = _reconstruct_success_evidence(
            manifest, schedule, records, checkpoints
        )
        reconstructed_identities = [
            {field: result[field] for field in _SUCCESS_RESULT_FIELDS}
            for result in reconstructed_results
        ]
        if normalized_results != reconstructed_identities:
            _fail("success_block_results_invalid")
        manifest_raw = self.manifest_path.read_bytes()
        summary = {
            "schema_version": SUCCESS_SCHEMA,
            "run_id": self.run_id,
            "stage": "C4/E3",
            "status": "complete",
            "mergeable": False,
            "screening_repetition_count": 1,
            "screening_identity": "frozen_bounded_single_replay",
            "manifest_sha256": _sha256(manifest_raw),
            "manifest_payload_sha256": manifest["payload_sha256"],
            "schedule_payload_sha256": manifest["schedule_payload_sha256"],
            "provenance_hashes": deepcopy(manifest["provenance_hashes"]),
            "block_count": FROZEN_BLOCK_COUNT,
            "episode_count": FROZEN_TOTAL_EPISODES,
            "block_results": reconstructed_results,
            "durable_evidence": durable_evidence,
        }
        persisted = _atomic_write_sealed_json(self.success_summary_path, summary)
        summary_sha256 = _sha256(canonical_json_bytes(persisted) + b"\n")
        self.write_root_checkpoint(
            status="completed",
            progress={
                "completed_block_indices": list(range(FROZEN_BLOCK_COUNT)),
                "completed_episode_count": FROZEN_TOTAL_EPISODES,
                "success_summary_path": self.success_summary_path.name,
                "success_summary_sha256": summary_sha256,
                "success_summary_payload_sha256": persisted["payload_sha256"],
            },
        )
        return persisted


def _validate_success_block_results(
    block_results: Sequence[Mapping[str, Any]],
    planned_blocks: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if (
        not isinstance(block_results, Sequence)
        or isinstance(block_results, (str, bytes))
        or len(block_results) != FROZEN_BLOCK_COUNT
        or len(planned_blocks) != FROZEN_BLOCK_COUNT
    ):
        _fail("success_block_results_invalid")
    normalized: list[dict[str, Any]] = []
    for index, (result, planned) in enumerate(zip(block_results, planned_blocks)):
        if not isinstance(result, Mapping) or not isinstance(planned, Mapping):
            _fail("success_block_results_invalid")
        candidate = deepcopy(dict(result))
        _assert_sanitized(candidate)
        if (
            set(candidate) != _SUCCESS_RESULT_FIELDS
            or
            candidate.get("block_index") != index
            or any(
                candidate.get(field) != planned.get(field)
                for field in _SUCCESS_RESULT_IDENTITY_FIELDS
            )
        ):
            _fail("success_block_results_invalid")
        normalized.append(
            {field: candidate[field] for field in sorted(_SUCCESS_RESULT_FIELDS)}
        )
    return normalized


def _success_evidence_invalid() -> None:
    _fail("success_durable_evidence_invalid")


def _event_timestamp(event: Mapping[str, Any], name: str) -> int:
    value = event.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _success_evidence_invalid()
    return value


def _reconstruct_success_evidence(
    manifest: Mapping[str, Any],
    schedule: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    checkpoints: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reconstruct the 10x49 success prefix only from durable evidence."""

    planned_blocks = manifest.get("planned_blocks")
    schedule_blocks = schedule.get("block_schedules")
    episode_ids = schedule.get("episode_ids")
    if (
        not isinstance(planned_blocks, list)
        or len(planned_blocks) != FROZEN_BLOCK_COUNT
        or not isinstance(schedule_blocks, list)
        or len(schedule_blocks) != FROZEN_BLOCK_COUNT
        or not isinstance(episode_ids, list)
        or len(episode_ids) != FROZEN_EPISODES_PER_BLOCK
        or manifest.get("episode_ids") != episode_ids
    ):
        _success_evidence_invalid()
    expected_publications = [
        (block_index, source_sequence)
        for block_index in range(FROZEN_BLOCK_COUNT)
        for source_sequence in range(FROZEN_EPISODES_PER_BLOCK)
    ]
    expected_enqueues = [
        (block_index, source_sequence)
        for block_index, block in enumerate(planned_blocks)
        if block.get("method") == "Native-Async-Serial"
        for source_sequence in range(FROZEN_EPISODES_PER_BLOCK)
    ]
    publications = [event for event in events if event.get("event_type") == "publication"]
    enqueues = [event for event in events if event.get("event_type") == "enqueue"]
    observed_publications = [
        (event.get("block_index"), event.get("source_sequence"))
        for event in publications
    ]
    observed_enqueues = [
        (event.get("block_index"), event.get("source_sequence"))
        for event in enqueues
    ]
    if (
        observed_publications != expected_publications
        or observed_enqueues != expected_enqueues
        or len(publications) != FROZEN_TOTAL_EPISODES
        or len(enqueues) != FROZEN_EPISODES_PER_BLOCK * 5
    ):
        _success_evidence_invalid()

    publication_by_identity: dict[tuple[int, int], Mapping[str, Any]] = {}
    enqueue_by_identity: dict[tuple[int, int], Mapping[str, Any]] = {}
    for event in publications:
        identity = (int(event["block_index"]), int(event["source_sequence"]))
        block = planned_blocks[identity[0]]
        expected_episode_id = episode_ids[identity[1]]
        arrival = _event_timestamp(event, "arrival_timestamp_ns")
        scheduled_arrival = _event_timestamp(
            event, "scheduled_arrival_timestamp_ns"
        )
        ack = _event_timestamp(event, "enqueue_ack_timestamp_ns")
        service_start = _event_timestamp(event, "service_start_timestamp_ns")
        publish = _event_timestamp(event, "publish_timestamp_ns")
        caller_return = _event_timestamp(event, "caller_return_timestamp_ns")
        if (
            event.get("episode_id") != expected_episode_id
            or arrival < scheduled_arrival
            or not arrival <= ack <= service_start <= publish
            or caller_return < arrival
            or (
                block.get("method") == "Native-Sync"
                and (ack != arrival or caller_return != publish)
            )
            or (
                block.get("method") == "Native-Async-Serial"
                and caller_return != ack
            )
        ):
            _success_evidence_invalid()
        publication_by_identity[identity] = event

    for block_index, schedule_block in enumerate(schedule_blocks):
        offsets = schedule_block.get("absolute_arrival_offsets_ns")
        if (
            not isinstance(offsets, list)
            or len(offsets) != FROZEN_EPISODES_PER_BLOCK
            or not all(
                isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in offsets
            )
        ):
            _success_evidence_invalid()
        observed_scheduled = [
            publication_by_identity[(block_index, source_sequence)][
                "scheduled_arrival_timestamp_ns"
            ]
            for source_sequence in range(FROZEN_EPISODES_PER_BLOCK)
        ]
        origin = observed_scheduled[0] - offsets[0]
        if origin < 0 or observed_scheduled != [origin + offset for offset in offsets]:
            _success_evidence_invalid()

    for event in enqueues:
        identity = (int(event["block_index"]), int(event["source_sequence"]))
        publication = publication_by_identity.get(identity)
        block = planned_blocks[identity[0]]
        if (
            publication is None
            or event.get("episode_id") != episode_ids[identity[1]]
            or event.get("graph_namespace") != block.get("graph_namespace")
            or event.get("method") != block.get("method")
            or "enqueue_ack_timestamp_ns" in event
        ):
            _success_evidence_invalid()
        if (
            _event_timestamp(event, "arrival_timestamp_ns")
            != publication.get("arrival_timestamp_ns")
        ):
            _success_evidence_invalid()
        enqueue_by_identity[identity] = event

    episode_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("checkpoint_level") == "episode"
    ]
    block_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.get("checkpoint_level") == "block"
    ]
    if (
        len(episode_checkpoints) != FROZEN_TOTAL_EPISODES
        or len(block_checkpoints) != FROZEN_BLOCK_COUNT
    ):
        _success_evidence_invalid()
    episode_checkpoint_by_identity: dict[tuple[int, int], Mapping[str, Any]] = {}
    for checkpoint in episode_checkpoints:
        block_index = checkpoint.get("block_index")
        source_sequence = checkpoint.get("source_sequence")
        if (
            not isinstance(block_index, int)
            or isinstance(block_index, bool)
            or not 0 <= block_index < FROZEN_BLOCK_COUNT
            or not isinstance(source_sequence, int)
            or isinstance(source_sequence, bool)
            or not 0 <= source_sequence < FROZEN_EPISODES_PER_BLOCK
        ):
            _success_evidence_invalid()
        identity = (block_index, source_sequence)
        if identity in episode_checkpoint_by_identity:
            _success_evidence_invalid()
        progress = checkpoint.get("progress")
        block = planned_blocks[block_index]
        publication = publication_by_identity[identity]
        if (
            checkpoint.get("status") != "completed"
            or not isinstance(progress, Mapping)
            or progress.get("episode_id") != episode_ids[source_sequence]
            or progress.get("graph_namespace") != block.get("graph_namespace")
            or progress.get("method") != block.get("method")
            or progress.get("publication_event_payload_sha256")
            != publication.get("payload_sha256")
        ):
            _success_evidence_invalid()
        episode_checkpoint_by_identity[identity] = checkpoint
    if set(episode_checkpoint_by_identity) != set(expected_publications):
        _success_evidence_invalid()

    block_checkpoint_by_index: dict[int, Mapping[str, Any]] = {}
    for checkpoint in block_checkpoints:
        block_index = checkpoint.get("block_index")
        if (
            not isinstance(block_index, int)
            or isinstance(block_index, bool)
            or not 0 <= block_index < FROZEN_BLOCK_COUNT
            or block_index in block_checkpoint_by_index
        ):
            _success_evidence_invalid()
        progress = checkpoint.get("progress")
        block = planned_blocks[block_index]
        if (
            checkpoint.get("status") != "completed"
            or not isinstance(progress, Mapping)
            or progress.get("graph_namespace") != block.get("graph_namespace")
            or progress.get("history_id") != block.get("history_id")
            or progress.get("method") != block.get("method")
            or progress.get("normalized_offered_load")
            != block.get("normalized_offered_load")
            or progress.get("completed_source_sequences")
            != list(range(FROZEN_EPISODES_PER_BLOCK))
            or progress.get("completed_episode_count")
            != FROZEN_EPISODES_PER_BLOCK
        ):
            _success_evidence_invalid()
        block_checkpoint_by_index[block_index] = checkpoint
    if set(block_checkpoint_by_index) != set(range(FROZEN_BLOCK_COUNT)):
        _success_evidence_invalid()

    block_evidence: list[dict[str, Any]] = []
    reconstructed_results: list[dict[str, Any]] = []
    for block_index, block in enumerate(planned_blocks):
        identities = [
            (block_index, source_sequence)
            for source_sequence in range(FROZEN_EPISODES_PER_BLOCK)
        ]
        publication_hashes = [
            publication_by_identity[identity]["payload_sha256"]
            for identity in identities
        ]
        enqueue_hashes = [
            enqueue_by_identity[identity]["payload_sha256"]
            for identity in identities
            if identity in enqueue_by_identity
        ]
        episode_checkpoint_hashes = [
            episode_checkpoint_by_identity[identity]["payload_sha256"]
            for identity in identities
        ]
        block_evidence.append(
            {
                "block_index": block_index,
                "publication_count": len(publication_hashes),
                "enqueue_count": len(enqueue_hashes),
                "episode_checkpoint_count": len(episode_checkpoint_hashes),
                "block_checkpoint_count": 1,
                "publication_payloads_sha256": _sha256(
                    canonical_json_bytes(publication_hashes)
                ),
                "enqueue_payloads_sha256": _sha256(
                    canonical_json_bytes(enqueue_hashes)
                ),
                "episode_checkpoint_payloads_sha256": _sha256(
                    canonical_json_bytes(episode_checkpoint_hashes)
                ),
                "block_checkpoint_payload_sha256": block_checkpoint_by_index[
                    block_index
                ]["payload_sha256"],
            }
        )
        reconstructed_results.append(
            _reconstruct_block_result(
                block,
                [publication_by_identity[identity] for identity in identities],
                block_evidence[-1],
            )
        )
    durable_evidence = {
        "publication_count": len(publications),
        "enqueue_count": len(enqueues),
        "sync_enqueue_count": 0,
        "async_enqueue_count": len(enqueues),
        "episode_checkpoint_count": len(episode_checkpoints),
        "block_checkpoint_count": len(block_checkpoints),
        "block_evidence": block_evidence,
    }
    return durable_evidence, reconstructed_results


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _nearest_rank_p95(values: Sequence[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return ordered[rank - 1]


def _reconstruct_block_result(
    planned_block: Mapping[str, Any],
    publications: Sequence[Mapping[str, Any]],
    block_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    if len(publications) != FROZEN_EPISODES_PER_BLOCK:
        _success_evidence_invalid()
    episode_metrics: list[dict[str, Any]] = []
    try:
        for publication in publications:
            metrics = compute_episode_metrics(publication)
            scheduled_arrival = int(publication["scheduled_arrival_timestamp_ns"])
            arrival = int(publication["arrival_timestamp_ns"])
            episode_metrics.append(
                {
                    "episode_id": publication["episode_id"],
                    "source_sequence": publication["source_sequence"],
                    "scheduled_arrival_timestamp_ns": scheduled_arrival,
                    "arrival_timestamp_ns": arrival,
                    "enqueue_ack_timestamp_ns": publication[
                        "enqueue_ack_timestamp_ns"
                    ],
                    "service_start_timestamp_ns": publication[
                        "service_start_timestamp_ns"
                    ],
                    "publish_timestamp_ns": publication["publish_timestamp_ns"],
                    "caller_return_timestamp_ns": publication[
                        "caller_return_timestamp_ns"
                    ],
                    "schedule_lag_ns": arrival - scheduled_arrival,
                    "publication_event_payload_sha256": publication[
                        "payload_sha256"
                    ],
                    **metrics,
                }
            )
        arrivals = [int(item["arrival_timestamp_ns"]) for item in publications]
        backlog = analyze_backlog(arrivals, publications)
    except (KeyError, TypeError, ValueError, NativeCharacterizationC4Error):
        _success_evidence_invalid()
    if backlog.get("final_backlog") != 0:
        _success_evidence_invalid()
    first_arrival = arrivals[0]
    final_arrival = arrivals[-1]
    final_publish = max(int(item["publish_timestamp_ns"]) for item in publications)
    makespan_ns = final_publish - first_arrival
    metric_names = (
        "caller_return_latency_ns",
        "construction_service_time_ns",
        "queue_wait_ns",
        "arrival_to_visible_ns",
        "signed_publish_after_return_ns",
        "post_return_stale_window_ns",
    )
    aggregate: dict[str, Any] = {
        **backlog,
        "episode_count": FROZEN_EPISODES_PER_BLOCK,
        "completed_episode_count": FROZEN_EPISODES_PER_BLOCK,
        "first_arrival_timestamp_ns": first_arrival,
        "final_arrival_timestamp_ns": final_arrival,
        "final_publish_timestamp_ns": final_publish,
        "makespan_ns": makespan_ns,
        "drain_time_ns": max(0, final_publish - final_arrival),
        "throughput_episodes_per_second": (
            FROZEN_EPISODES_PER_BLOCK * 1_000_000_000 / makespan_ns
            if makespan_ns > 0
            else None
        ),
        "error_count": 0,
        "checkpoint_loss_count": 0,
    }
    for name in metric_names:
        aggregate[f"mean_{name}"] = _mean(
            [int(item[name]) for item in episode_metrics]
        )
    schedule_lags = [int(item["schedule_lag_ns"]) for item in episode_metrics]
    aggregate.update(
        {
            "mean_schedule_lag_ns": _mean(schedule_lags),
            "p95_schedule_lag_ns": _nearest_rank_p95(schedule_lags),
            "maximum_schedule_lag_ns": max(schedule_lags),
        }
    )
    result = {
        field: planned_block[field] for field in _SUCCESS_RESULT_IDENTITY_FIELDS
    }
    result.update(
        {
            "status": "complete",
            "episode_count": FROZEN_EPISODES_PER_BLOCK,
            "completed_episode_count": FROZEN_EPISODES_PER_BLOCK,
            "episode_metrics": episode_metrics,
            "aggregate": aggregate,
            "durable_evidence": deepcopy(dict(block_evidence)),
        }
    )
    _assert_sanitized(result)
    return result


def _validated_run_root(run_root: str | Path) -> Path:
    supplied = Path(run_root)
    if supplied.is_symlink():
        _fail("run_directory_invalid")
    try:
        path = supplied.resolve(strict=True)
    except (OSError, RuntimeError):
        _fail("run_directory_invalid")
    if not path.is_dir() or path.is_symlink():
        _fail("run_directory_invalid")
    return path


def build_hash_inventory(run_root: str | Path) -> dict[str, dict[str, Any]]:
    """Return a deterministic inventory without writing into the attempt."""

    root = _validated_run_root(run_root)
    result: dict[str, dict[str, Any]] = {}
    try:
        candidates = sorted(root.rglob("*"), key=lambda item: item.as_posix())
        for path in candidates:
            if path.is_symlink():
                _fail("artifact_symlink_invalid")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if path.name.startswith(".") or path.suffix == ".tmp":
                _fail("temporary_artifact_present")
            raw = path.read_bytes()
            result[relative] = {
                "sha256": _sha256(raw),
                "byte_count": len(raw),
                "line_count": raw.count(b"\n") if path.suffix == ".jsonl" else None,
            }
    except NativeCharacterizationC4ArtifactError:
        raise
    except OSError:
        _fail("artifact_inventory_failed")
    return result


def _verify_events(path: Path, run_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        raw = path.read_bytes()
    except OSError:
        _fail("events_unreadable")
    if raw and not raw.endswith(b"\n"):
        _fail("event_not_canonical")
    lines = raw.splitlines()
    records: list[dict[str, Any]] = []
    counts = {name: 0 for name in _EVENT_TYPES}
    for expected_sequence, line in enumerate(lines):
        try:
            value = json.loads(line.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError):
            _fail("event_seal_invalid")
        if (
            not isinstance(value, dict)
            or line != canonical_json_bytes(value)
            or value.get("payload_sha256") != payload_sha256(value)
        ):
            _fail("event_seal_invalid")
        _assert_sanitized(value)
        event_type = value.get("event_type")
        if (
            value.get("schema_version") != EVENT_SCHEMA
            or value.get("run_id") != run_id
            or value.get("event_sequence") != expected_sequence
            or event_type not in _EVENT_TYPES
        ):
            _fail("event_contract_invalid")
        if event_type == "failure":
            if (
                value.get("status") != FAILURE_STATUS
                or _error_class(value.get("error_class")) != value.get("error_class")
                or _normalize_token_envelope(value.get("token_envelope"))
                != value.get("token_envelope")
            ):
                _fail("failure_event_invalid")
        counts[event_type] += 1
        records.append(value)
    return records, counts


def _verify_checkpoints(root: Path, run_id: str) -> tuple[int, list[dict[str, Any]]]:
    checkpoints: list[dict[str, Any]] = []
    nested_paths = {
        path
        for path in root.rglob("*.checkpoint.json")
        if path != root / "checkpoint.json"
    }
    nested_paths.update(
        path
        for path in root.glob("blocks/*/checkpoint.json")
        if path.is_file()
    )
    for path in sorted(nested_paths, key=lambda item: item.as_posix()):
        value = _read_sealed_json(path, "checkpoint_seal_invalid")
        if value.get("schema_version") != CHECKPOINT_SCHEMA or value.get("run_id") != run_id:
            _fail("checkpoint_contract_invalid")
        relative = path.relative_to(root).as_posix()
        episode_match = _EPISODE_CHECKPOINT_PATH_RE.fullmatch(relative)
        block_match = _BLOCK_CHECKPOINT_PATH_RE.fullmatch(relative)
        if episode_match is not None:
            if (
                value.get("checkpoint_level") != "episode"
                or value.get("block_index") != int(episode_match.group(1))
                or value.get("source_sequence") != int(episode_match.group(2))
            ):
                _fail("checkpoint_path_identity_invalid")
        elif block_match is not None:
            if (
                value.get("checkpoint_level") != "block"
                or value.get("block_index") != int(block_match.group(1))
                or value.get("source_sequence") is not None
            ):
                _fail("checkpoint_path_identity_invalid")
        else:
            _fail("checkpoint_path_identity_invalid")
        checkpoints.append(value)
    root_checkpoint = root / "checkpoint.json"
    if root_checkpoint.is_file():
        value = _read_sealed_json(root_checkpoint, "checkpoint_seal_invalid")
        if (
            value.get("schema_version") != CHECKPOINT_SCHEMA
            or value.get("run_id") != run_id
            or value.get("checkpoint_level") != "root"
        ):
            _fail("checkpoint_contract_invalid")
        checkpoints.append(value)
    return len(checkpoints), checkpoints


def _verify_success_summary(
    root: Path,
    manifest: Mapping[str, Any],
    schedule: Mapping[str, Any],
    durable_evidence: Mapping[str, Any],
    reconstructed_results: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], str]:
    try:
        summary = _read_sealed_json(
            root / "e3_sync_async.json", "success_summary_contract_invalid"
        )
        summary_results = summary.get("block_results")
        if not isinstance(summary_results, list):
            _fail("success_summary_contract_invalid")
        basic_results = [
            {
                field: result.get(field)
                for field in _SUCCESS_RESULT_FIELDS
            }
            for result in summary_results
            if isinstance(result, Mapping)
        ]
        normalized_results = _validate_success_block_results(
            basic_results, manifest.get("planned_blocks")
        )
        reconstructed_identities = [
            {field: result[field] for field in _SUCCESS_RESULT_FIELDS}
            for result in reconstructed_results
        ]
        if normalized_results != reconstructed_identities:
            _fail("success_summary_contract_invalid")
    except NativeCharacterizationC4ArtifactError:
        _fail("success_summary_contract_invalid")
    manifest_raw = (root / "manifest.json").read_bytes()
    if (
        summary.get("schema_version") != SUCCESS_SCHEMA
        or summary.get("run_id") != manifest.get("run_id")
        or summary.get("stage") != "C4/E3"
        or summary.get("status") != "complete"
        or summary.get("mergeable") is not False
        or summary.get("screening_repetition_count") != 1
        or summary.get("screening_identity") != "frozen_bounded_single_replay"
        or summary.get("manifest_sha256") != _sha256(manifest_raw)
        or summary.get("manifest_payload_sha256") != manifest.get("payload_sha256")
        or summary.get("schedule_payload_sha256") != schedule.get("payload_sha256")
        or summary.get("provenance_hashes") != manifest.get("provenance_hashes")
        or summary.get("block_count") != FROZEN_BLOCK_COUNT
        or summary.get("episode_count") != FROZEN_TOTAL_EPISODES
        or summary.get("block_results") != reconstructed_results
        or summary.get("durable_evidence") != durable_evidence
    ):
        _fail("success_summary_contract_invalid")
    return summary, _sha256(canonical_json_bytes(summary) + b"\n")


def _verify_failure_checkpoint_binding(
    failure_event: Mapping[str, Any],
    root_checkpoint: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
) -> None:
    block_index = failure_event.get("block_index")
    source_sequence = failure_event.get("source_sequence")
    if (
        not isinstance(block_index, int)
        or isinstance(block_index, bool)
        or not isinstance(source_sequence, int)
        or isinstance(source_sequence, bool)
        or not 0 <= block_index < FROZEN_BLOCK_COUNT
        or not 0 <= source_sequence < FROZEN_EPISODES_PER_BLOCK
    ):
        _fail("failure_checkpoint_contract_invalid")
    block_matches = [
        value
        for value in checkpoints
        if value.get("checkpoint_level") == "block"
        and value.get("block_index") == block_index
    ]
    episode_matches = [
        value
        for value in checkpoints
        if value.get("checkpoint_level") == "episode"
        and value.get("block_index") == block_index
        and value.get("source_sequence") == source_sequence
    ]
    if len(block_matches) != 1 or len(episode_matches) != 1:
        _fail("failure_checkpoint_contract_invalid")
    block_checkpoint = block_matches[0]
    episode_checkpoint = episode_matches[0]
    root_progress = root_checkpoint.get("progress")
    block_progress = block_checkpoint.get("progress")
    episode_progress = episode_checkpoint.get("progress")
    if not all(
        isinstance(value, Mapping)
        for value in (root_progress, block_progress, episode_progress)
    ):
        _fail("failure_checkpoint_contract_invalid")
    failure_identity = {
        "error_class": failure_event.get("error_class"),
        "token_envelope": failure_event.get("token_envelope"),
    }
    if (
        root_checkpoint.get("status") != FAILURE_STATUS
        or block_checkpoint.get("status") != FAILURE_STATUS
        or episode_checkpoint.get("status") != FAILURE_STATUS
        or root_checkpoint.get("failure") != failure_identity
        or block_checkpoint.get("failure") != failure_identity
        or episode_checkpoint.get("failure") != failure_identity
    ):
        _fail("failure_checkpoint_contract_invalid")
    completed_blocks = root_progress.get("completed_block_indices")
    completed_sources = block_progress.get("completed_source_sequences")
    completed_episode_count = root_progress.get("completed_episode_count")
    if (
        not isinstance(completed_blocks, list)
        or not all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in completed_blocks
        )
        or completed_blocks != list(range(block_index))
        or not isinstance(completed_sources, list)
        or not all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in completed_sources
        )
        or completed_sources != list(range(source_sequence))
        or episode_progress.get("completed_source_sequences") != completed_sources
        or not isinstance(completed_episode_count, int)
        or isinstance(completed_episode_count, bool)
        or completed_episode_count
        != block_index * FROZEN_EPISODES_PER_BLOCK + source_sequence
        or root_progress.get("failed_block_index") != block_index
        or root_progress.get("failed_source_sequence") != source_sequence
        or failure_event.get("completed_block_count") != len(completed_blocks)
        or failure_event.get("completed_episode_count") != completed_episode_count
    ):
        _fail("failure_checkpoint_contract_invalid")


def _verify_stage_failure_binding(
    failure_event: Mapping[str, Any],
    root_checkpoint: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
) -> None:
    progress = root_checkpoint.get("progress")
    failure_identity = {
        "error_class": failure_event.get("error_class"),
        "token_envelope": failure_event.get("token_envelope"),
    }
    if (
        failure_event.get("failure_scope") != "stage"
        or failure_event.get("failure_stage") not in {"finalization", "verification"}
        or failure_event.get("block_index") is not None
        or failure_event.get("source_sequence") is not None
        or failure_event.get("completed_block_count") != FROZEN_BLOCK_COUNT
        or failure_event.get("completed_episode_count") != FROZEN_TOTAL_EPISODES
        or root_checkpoint.get("status") != FAILURE_STATUS
        or root_checkpoint.get("failure") != failure_identity
        or not isinstance(progress, Mapping)
        or progress.get("failure_stage") != failure_event.get("failure_stage")
        or progress.get("completed_block_indices")
        != list(range(FROZEN_BLOCK_COUNT))
        or progress.get("completed_episode_count") != FROZEN_TOTAL_EPISODES
        or any(
            checkpoint.get("status") == FAILURE_STATUS
            for checkpoint in checkpoints
            if checkpoint.get("checkpoint_level") != "root"
        )
    ):
        _fail("failure_stage_checkpoint_contract_invalid")


def verify_c4_artifacts(run_root: str | Path) -> dict[str, Any]:
    """Verify one current C4 prefix without mutating or blessing it as complete."""

    root = _validated_run_root(run_root)
    manifest = _read_sealed_json(root / "manifest.json", "manifest_seal_invalid")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("status") != "planned"
        or not isinstance(manifest.get("run_id"), str)
        or _RUN_ID_RE.fullmatch(manifest["run_id"]) is None
    ):
        _fail("manifest_contract_invalid")
    run_id = manifest["run_id"]
    provenance = _validate_provenance_hashes(manifest.get("provenance_hashes"))
    schedule = _read_sealed_json(root / "schedule.json", "schedule_seal_invalid")
    schedule, blocks = _validate_schedule(schedule)
    if (
        manifest.get("schedule_payload_sha256") != schedule["payload_sha256"]
        or manifest.get("planned_blocks") != blocks
        or manifest.get("episode_ids") != schedule.get("episode_ids")
    ):
        _fail("manifest_schedule_binding_invalid")
    events, event_counts = _verify_events(root / "events.jsonl", run_id)
    checkpoint_count, checkpoints = _verify_checkpoints(root, run_id)
    root_checkpoints = [
        value for value in checkpoints if value.get("checkpoint_level") == "root"
    ]
    if len(root_checkpoints) != 1:
        _fail("root_checkpoint_invalid")
    root_checkpoint = root_checkpoints[0]
    success_path = root / "e3_sync_async.json"
    success_summary: dict[str, Any] | None = None
    if event_counts["failure"]:
        failure_events = [
            value for value in events if value.get("event_type") == "failure"
        ]
        if (
            len(failure_events) != 1
            or root_checkpoint.get("status") != FAILURE_STATUS
        ):
            _fail("failure_checkpoint_missing")
        failure_event = failure_events[0]
        if failure_event.get("failure_scope") == "stage":
            _reconstruct_success_evidence(
                manifest, schedule, events, checkpoints
            )
            _verify_stage_failure_binding(
                failure_event, root_checkpoint, checkpoints
            )
        elif failure_event.get("failure_scope") == "episode":
            if success_path.exists() or success_path.is_symlink():
                _fail("failure_checkpoint_missing")
            _verify_failure_checkpoint_binding(
                failure_event, root_checkpoint, checkpoints
            )
        else:
            _fail("failure_checkpoint_contract_invalid")
        attempt_status = FAILURE_STATUS
    elif success_path.exists() or success_path.is_symlink():
        durable_evidence, reconstructed_results = _reconstruct_success_evidence(
            manifest, schedule, events, checkpoints
        )
        success_summary, success_sha256 = _verify_success_summary(
            root,
            manifest,
            schedule,
            durable_evidence,
            reconstructed_results,
        )
        progress = root_checkpoint.get("progress")
        if (
            root_checkpoint.get("status") != "completed"
            or not isinstance(progress, Mapping)
            or progress.get("completed_block_indices")
            != list(range(FROZEN_BLOCK_COUNT))
            or progress.get("completed_episode_count") != FROZEN_TOTAL_EPISODES
            or progress.get("success_summary_path") != success_path.name
            or progress.get("success_summary_sha256") != success_sha256
            or progress.get("success_summary_payload_sha256")
            != success_summary.get("payload_sha256")
        ):
            _fail("success_checkpoint_binding_invalid")
        attempt_status = "complete"
    else:
        if root_checkpoint.get("status") in {"completed", FAILURE_STATUS}:
            _fail("root_checkpoint_status_invalid")
        attempt_status = "running"
    inventory = build_hash_inventory(root)
    result = {
        "schema_version": VERIFICATION_SCHEMA,
        "status": "verified",
        "run_id": run_id,
        "attempt_status": attempt_status,
        "manifest_sha256": inventory["manifest.json"]["sha256"],
        "manifest_payload_sha256": manifest["payload_sha256"],
        "schedule_payload_sha256": schedule["payload_sha256"],
        "provenance_hashes": provenance,
        "planned_block_count": len(blocks),
        "event_count": len(events),
        "event_counts": event_counts,
        "checkpoint_count": checkpoint_count,
        "success_summary_sha256": (
            inventory["e3_sync_async.json"]["sha256"]
            if success_summary is not None
            else None
        ),
        "success_summary_payload_sha256": (
            success_summary["payload_sha256"]
            if success_summary is not None
            else None
        ),
        "hash_inventory": inventory,
    }
    return seal_payload(result)


__all__ = [
    "CHECKPOINT_SCHEMA",
    "C4ArtifactStore",
    "EVENT_SCHEMA",
    "FAILURE_STATUS",
    "FROZEN_BLOCK_COUNT",
    "FROZEN_EPISODES_PER_BLOCK",
    "FROZEN_TOTAL_EPISODES",
    "MANIFEST_SCHEMA",
    "NativeCharacterizationC4ArtifactError",
    "REQUIRED_PROVENANCE_HASHES",
    "SCHEDULE_SCHEMA",
    "SUCCESS_SCHEMA",
    "VERIFICATION_SCHEMA",
    "build_hash_inventory",
    "canonical_json_bytes",
    "nullable_token_envelope",
    "payload_sha256",
    "seal_payload",
    "verify_c4_artifacts",
]
