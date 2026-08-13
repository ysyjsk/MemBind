"""Crash-consistent, sanitized artifacts for the frozen C5 live screening.

The live scheduler owns execution semantics; this module owns only durable
evidence.  Every event append is serialized and fsynced, every checkpoint is
atomically replaced and directory-fsynced, and the read-only verifier treats
any broken seal or cross-file binding as non-mergeable.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import native_characterization_c5 as c5


MANIFEST_SCHEMA = "membind.native-characterization-c5-live-manifest.v1"
EVENT_SCHEMA = "membind.native-characterization-c5-live-event.v1"
EPISODE_CHECKPOINT_SCHEMA = (
    "membind.native-characterization-c5-live-episode-checkpoint.v1"
)
BLOCK_CHECKPOINT_SCHEMA = "membind.native-characterization-c5-live-block-checkpoint.v1"
ROOT_CHECKPOINT_SCHEMA = "membind.native-characterization-c5-live-root-checkpoint.v1"
VERIFICATION_SCHEMA = "membind.native-characterization-c5-live-verification.v1"
RECOVERY_AUDIT_SCHEMA = "membind.native-characterization-c5-resume-rollback.v1"
DIRECT_OBSERVATION_SCHEMA = (
    "membind.native-characterization-c5-direct-observation.v1"
)
INCOMPLETE_NON_MERGEABLE = "incomplete_invalid_non_mergeable"
DIRECT_OBSERVATION_STATUS = "direct_invariant_observed"

FROZEN_BLOCK_COUNT = 4
FROZEN_EPISODES_PER_BLOCK = 49
FROZEN_CONCURRENCY_GRID = (1, 2, 4, 8)
_RUN_ID_RE = re.compile(r"^c5-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,255}$")
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "base_url",
        "bearer",
        "body",
        "content",
        "cypher",
        "error_message",
        "exception",
        "exception_message",
        "messages",
        "password",
        "prompt",
        "raw_output",
        "raw_prompt",
        "raw_response",
        "reference_answer",
        "request",
        "response",
        "secret",
        "url",
    }
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[-_]?key|authorization)\s*[=:]\s*\S+)"
)


class C5LiveArtifactError(RuntimeError):
    """Fail-closed artifact error containing only a stable error code."""


def _fail(code: str) -> None:
    raise C5LiveArtifactError(code)


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


def payload_sha256(value: Mapping[str, Any]) -> str:
    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    return hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()


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


def seal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    candidate["payload_sha256"] = payload_sha256(candidate)
    return candidate


def _validate_sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _validate_index(value: Any, upper: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(upper):
        _fail(code)
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
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
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return sealed


def _create_empty_fsynced(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _atomic_replace_bytes(path: Path, value: bytes) -> None:
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
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_sealed_json(path: Path, code: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, dict):
        _fail(code)
    try:
        if raw != canonical_json_bytes(value) + b"\n":
            _fail(code)
        if value.get("payload_sha256") != payload_sha256(value):
            _fail(code)
        _assert_sanitized(value)
    except C5LiveArtifactError:
        _fail(code)
    return value


def _validate_schedule(schedule: Mapping[str, Any], run_id: str) -> list[dict[str, Any]]:
    candidate = deepcopy(dict(schedule))
    if candidate.get("payload_sha256") != c5.payload_sha256(candidate):
        _fail("schedule_seal_invalid")
    if (
        candidate.get("schema_version") != c5.SCHEDULE_SCHEMA
        or candidate.get("run_id") != run_id
        or tuple(candidate.get("concurrency_grid", ())) != FROZEN_CONCURRENCY_GRID
        or candidate.get("screening_pass_count") != 1
        or len(candidate.get("episode_ids", ())) != FROZEN_EPISODES_PER_BLOCK
        or len(candidate.get("episode_source_hashes", ())) != FROZEN_EPISODES_PER_BLOCK
    ):
        _fail("schedule_contract_invalid")
    raw_blocks = candidate.get("block_schedules")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != FROZEN_BLOCK_COUNT:
        _fail("schedule_blocks_invalid")
    blocks: list[dict[str, Any]] = []
    namespaces: set[str] = set()
    for index, (raw, concurrency) in enumerate(zip(raw_blocks, FROZEN_CONCURRENCY_GRID)):
        if not isinstance(raw, Mapping):
            _fail("schedule_blocks_invalid")
        namespace = raw.get("graph_namespace")
        if (
            raw.get("block_index") != index
            or raw.get("concurrency") != concurrency
            or not isinstance(namespace, str)
            or not namespace.startswith("nc-e4-")
            or namespace in namespaces
            or raw.get("absolute_arrival_offsets_ns") != [0] * FROZEN_EPISODES_PER_BLOCK
        ):
            _fail("schedule_blocks_invalid")
        namespaces.add(namespace)
        blocks.append(
            {
                "block_index": index,
                "concurrency": concurrency,
                "graph_namespace": namespace,
            }
        )
    _assert_sanitized(candidate)
    return blocks


def _read_events(path: Path, run_id: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_bytes().splitlines()
    except OSError:
        _fail("events_unreadable")
    events: list[dict[str, Any]] = []
    for sequence, line in enumerate(lines):
        try:
            value = json.loads(line.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError):
            _fail("event_invalid")
        if (
            not isinstance(value, dict)
            or line != canonical_json_bytes(value)
            or value.get("payload_sha256") != payload_sha256(value)
            or value.get("schema_version") != EVENT_SCHEMA
            or value.get("run_id") != run_id
            or value.get("event_sequence") != sequence
            or value.get("event_type") not in {"intent", "publication", "failure"}
        ):
            _fail("event_invalid")
        _assert_sanitized(value)
        events.append(value)
    return events


def _validate_event_contracts(
    events: Sequence[Mapping[str, Any]],
    blocks: Sequence[Mapping[str, Any]],
    schedule: Mapping[str, Any],
) -> None:
    source_hashes = schedule.get("episode_source_hashes")
    if not isinstance(source_hashes, list) or len(source_hashes) != FROZEN_EPISODES_PER_BLOCK:
        _fail("event_contract_invalid")
    arrivals_by_block: dict[int, set[int]] = {
        index: set() for index in range(FROZEN_BLOCK_COUNT)
    }
    for event in events:
        block_index = event.get("block_index")
        if isinstance(block_index, bool) or not isinstance(block_index, int):
            _fail("event_contract_invalid")
        if block_index not in range(FROZEN_BLOCK_COUNT):
            _fail("event_contract_invalid")
        block = blocks[block_index]
        if (
            event.get("concurrency") != block["concurrency"]
            or event.get("graph_namespace") != block["graph_namespace"]
        ):
            _fail("event_contract_invalid")
        event_type = event.get("event_type")
        source = event.get("source_sequence")
        if event_type in {"intent", "publication"} or source is not None:
            if (
                isinstance(source, bool)
                or not isinstance(source, int)
                or source not in range(FROZEN_EPISODES_PER_BLOCK)
            ):
                _fail("event_contract_invalid")
        if event_type in {"intent", "publication"}:
            worker_id = event.get("worker_id")
            arrival = event.get("arrival_timestamp_ns")
            if (
                event.get("episode_source_sha256") != source_hashes[source]
                or isinstance(worker_id, bool)
                or not isinstance(worker_id, int)
                or worker_id not in range(int(block["concurrency"]))
                or isinstance(arrival, bool)
                or not isinstance(arrival, int)
                or arrival < 0
            ):
                _fail("event_contract_invalid")
            arrivals_by_block[block_index].add(arrival)
        if event_type == "publication":
            arrival = event["arrival_timestamp_ns"]
            service_start = event.get("service_start_timestamp_ns")
            publish = event.get("publish_timestamp_ns")
            caller_return = event.get("caller_return_timestamp_ns")
            if (
                any(
                    isinstance(value, bool) or not isinstance(value, int)
                    for value in (service_start, publish, caller_return)
                )
                or not arrival <= service_start <= publish
                or caller_return < publish
                or event.get("transaction_status") != "committed"
                or not isinstance(event.get("work_counts"), Mapping)
            ):
                _fail("event_contract_invalid")
        elif event_type == "failure":
            timestamp = event.get("failure_timestamp_ns")
            failure_stage = event.get("failure_stage", "add_episode")
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, int)
                or timestamp < 0
                or not isinstance(event.get("failure_kind"), str)
                or failure_stage
                not in {
                    "runtime_init",
                    "namespace_check",
                    "add_episode",
                    "export",
                    "retrieval",
                    "judge",
                    "close",
                }
                or (failure_stage == "add_episode" and source is None)
            ):
                _fail("event_contract_invalid")
    if any(len(arrivals) > 1 for arrivals in arrivals_by_block.values()):
        _fail("event_arrival_schedule_invalid")


def _recompute_block_result(
    *,
    block_index: int,
    result: Mapping[str, Any],
    publications: Sequence[Mapping[str, Any]],
    schedule: Mapping[str, Any],
) -> None:
    canonical_parity = result.get("canonical_graph_parity")
    retrieval_parity = result.get("retrieval_parity")
    execution_path = result.get("execution_path_evidence")
    if not all(
        isinstance(value, Mapping)
        for value in (canonical_parity, retrieval_parity, execution_path)
    ):
        _fail("block_result_event_recompute_mismatch")
    try:
        recomputed = c5.analyze_c5_block(
            concurrency=FROZEN_CONCURRENCY_GRID[block_index],
            expected_episode_ids=schedule["episode_ids"],
            publication_records=publications,
            canonical_graph_parity=canonical_parity,
            retrieval_parity=retrieval_parity,
            execution_path_evidence=execution_path,
        )
    except (KeyError, TypeError, ValueError, c5.NativeCharacterizationC5Error):
        _fail("block_result_event_recompute_mismatch")
    for key, value in recomputed.items():
        if key == "payload_sha256":
            continue
        if result.get(key) != value:
            _fail("block_result_event_recompute_mismatch")


@dataclass(frozen=True)
class C5ResumeInspection:
    completed_block_indices: tuple[int, ...]
    partial_block_index: int | None
    completed_block_results: tuple[Mapping[str, Any], ...] = ()
    serial_reference: Mapping[str, Any] | None = None

    @property
    def requires_partial_block_restart(self) -> bool:
        return self.partial_block_index is not None


class C5LiveArtifactStore:
    """Append-only event log plus atomic episode/block/root checkpoints."""

    def __init__(self, run_dir: Path, run_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.manifest_path = self.run_dir / "manifest.json"
        self.schedule_path = self.run_dir / "schedule.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.root_checkpoint_path = self.run_dir / "checkpoint.json"
        self.result_path = self.run_dir / "e4_whole_parallel.json"
        self.direct_observation_path = self.run_dir / "c5_direct_observation.json"
        self._writer_lock_fd: int | None = None
        lock_fd = os.open(
            self.run_dir / ".writer.lock", os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            _fail("run_writer_locked")
        self._writer_lock_fd = lock_fd
        self._append_lock = asyncio.Lock()
        self._next_event_sequence = len(_read_events(self.events_path, run_id))

    def close(self) -> None:
        """Release the process-scoped writer lease; safe to call repeatedly."""

        descriptor = self._writer_lock_fd
        if descriptor is None:
            return
        self._writer_lock_fd = None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @classmethod
    def create(
        cls,
        runs_root: Path,
        run_id: str,
        schedule: Mapping[str, Any],
        provenance_hashes: Mapping[str, str],
        command_argv: Sequence[str],
    ) -> "C5LiveArtifactStore":
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            _fail("run_id_invalid")
        blocks = _validate_schedule(schedule, run_id)
        if not isinstance(provenance_hashes, Mapping) or not provenance_hashes:
            _fail("provenance_hashes_invalid")
        provenance = {
            str(key): _validate_sha256(value, "provenance_hashes_invalid")
            for key, value in sorted(provenance_hashes.items())
        }
        if isinstance(command_argv, (str, bytes)) or not command_argv:
            _fail("command_argv_invalid")
        argv = [str(item) for item in command_argv]
        _assert_sanitized(argv)
        run_dir = Path(runs_root) / run_id
        if run_dir.exists() and any(run_dir.iterdir()):
            _fail("run_directory_nonempty")
        run_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(run_dir / "schedule.json", schedule)
        _atomic_write_json(
            run_dir / "manifest.json",
            {
                "schema_version": MANIFEST_SCHEMA,
                "status": "planned",
                "stage": "C5/E4",
                "run_id": run_id,
                "schedule_payload_sha256": schedule["payload_sha256"],
                "provenance_hashes": provenance,
                "command_argv": argv,
                "planned_blocks": blocks,
            },
        )
        _create_empty_fsynced(run_dir / "events.jsonl")
        _atomic_write_json(
            run_dir / "checkpoint.json",
            {
                "schema_version": ROOT_CHECKPOINT_SCHEMA,
                "run_id": run_id,
                "stage": "C5/E4",
                "status": "planned",
                "completed_block_indices": [],
                "partial_block_index": None,
                "result_sha256": None,
                "result_payload_sha256": None,
            },
        )
        return cls(run_dir, run_id)

    @classmethod
    def open_existing(cls, run_dir: Path) -> "C5LiveArtifactStore":
        """Open an already verified prefix without rewriting any artifact."""

        root = Path(run_dir)
        inspected = _inspect_run(root, require_result=False)
        if (
            (root / "e4_whole_parallel.json").exists()
            or (root / "c5_direct_observation.json").exists()
        ):
            _fail("completed_run_not_resumable")
        return cls(root, str(inspected["run_id"]))

    async def _append_event(self, event_type: str, supplied: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(supplied, Mapping) or supplied.get("event_type") != event_type:
            _fail("event_contract_invalid")
        _assert_sanitized(supplied)
        async with self._append_lock:
            event = seal_payload(
                {
                    "schema_version": EVENT_SCHEMA,
                    "run_id": self.run_id,
                    "event_sequence": self._next_event_sequence,
                    **deepcopy(dict(supplied)),
                }
            )
            encoded = canonical_json_bytes(event) + b"\n"
            descriptor = os.open(self.events_path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._next_event_sequence += 1
            return event

    async def append_intent_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return await self._append_event("intent", value)

    async def append_publication_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return await self._append_event("publication", value)

    async def append_failure_event(self, value: Mapping[str, Any]) -> dict[str, Any]:
        error_class = value.get("error_class") if isinstance(value, Mapping) else None
        if not isinstance(error_class, str) or _ERROR_CLASS_RE.fullmatch(error_class) is None:
            _fail("failure_error_class_invalid")
        return await self._append_event("failure", value)

    async def write_episode_checkpoint(
        self,
        *,
        status: str,
        block_index: int,
        source_sequence: int,
        publication_event_sequence: int,
        publication_payload_sha256: str,
        intent_event_sequence: int,
        intent_payload_sha256: str,
    ) -> dict[str, Any]:
        block = _validate_index(block_index, FROZEN_BLOCK_COUNT, "block_index_invalid")
        source = _validate_index(
            source_sequence, FROZEN_EPISODES_PER_BLOCK, "source_sequence_invalid"
        )
        if status != "published":
            _fail("episode_status_invalid")
        checkpoint = {
            "schema_version": EPISODE_CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "stage": "C5/E4",
            "status": status,
            "block_index": block,
            "source_sequence": source,
            "intent_event_sequence": intent_event_sequence,
            "intent_payload_sha256": _validate_sha256(
                intent_payload_sha256, "event_hash_invalid"
            ),
            "publication_event_sequence": publication_event_sequence,
            "publication_payload_sha256": _validate_sha256(
                publication_payload_sha256, "event_hash_invalid"
            ),
        }
        path = self.run_dir / "blocks" / f"{block:03d}" / "episodes" / f"{source:06d}.json"
        return _atomic_write_json(path, checkpoint)

    async def write_block_checkpoint(
        self,
        *,
        status: str,
        block_index: int,
        concurrency: int,
        graph_namespace: str,
        block_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        block = _validate_index(block_index, FROZEN_BLOCK_COUNT, "block_index_invalid")
        if (
            status != "completed"
            or concurrency != FROZEN_CONCURRENCY_GRID[block]
            or not isinstance(graph_namespace, str)
            or block_result.get("payload_sha256") != c5.payload_sha256(block_result)
        ):
            _fail("block_checkpoint_invalid")
        checkpoint = {
            "schema_version": BLOCK_CHECKPOINT_SCHEMA,
            "run_id": self.run_id,
            "stage": "C5/E4",
            "status": status,
            "block_index": block,
            "concurrency": concurrency,
            "graph_namespace": graph_namespace,
            "block_result_payload_sha256": block_result["payload_sha256"],
            "block_result": deepcopy(dict(block_result)),
        }
        return _atomic_write_json(
            self.run_dir / "blocks" / f"{block:03d}" / "checkpoint.json",
            checkpoint,
        )

    async def write_root_checkpoint(
        self,
        *,
        status: str,
        completed_block_indices: Sequence[int],
        partial_block_index: int | None,
    ) -> dict[str, Any]:
        completed = list(completed_block_indices)
        if completed != list(range(len(completed))) or len(completed) > FROZEN_BLOCK_COUNT:
            _fail("completed_blocks_not_prefix")
        if partial_block_index is not None and partial_block_index != len(completed):
            _fail("partial_block_not_next")
        if status not in {"planned", "running", "complete", INCOMPLETE_NON_MERGEABLE}:
            _fail("root_status_invalid")
        return _atomic_write_json(
            self.root_checkpoint_path,
            {
                "schema_version": ROOT_CHECKPOINT_SCHEMA,
                "run_id": self.run_id,
                "stage": "C5/E4",
                "status": status,
                "completed_block_indices": completed,
                "partial_block_index": partial_block_index,
                "result_sha256": None,
                "result_payload_sha256": None,
            },
        )

    async def finalize_success(
        self, block_results: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        verification = _inspect_run(self.run_dir, require_result=False)
        if verification["failure_event_count"]:
            _fail("success_after_failure_invalid")
        if (
            len(block_results) != FROZEN_BLOCK_COUNT
            or verification["completed_block_count"] != FROZEN_BLOCK_COUNT
            or verification["episode_checkpoint_count"]
            != FROZEN_BLOCK_COUNT * FROZEN_EPISODES_PER_BLOCK
        ):
            _fail("four_completed_blocks_required")
        persisted_blocks = verification["block_results"]
        supplied = [deepcopy(dict(item)) for item in block_results]
        if supplied != persisted_blocks:
            _fail("block_results_not_checkpoint_bound")
        interpretations = [str(item["interpretation"]) for item in supplied]
        if c5.DIRECT_INVARIANT_VIOLATION_OBSERVED in interpretations:
            overall = c5.DIRECT_INVARIANT_VIOLATION_OBSERVED
        elif c5.OUTCOME_INSTABILITY_OR_CONFOUNDED in interpretations:
            overall = c5.OUTCOME_INSTABILITY_OR_CONFOUNDED
        else:
            overall = c5.NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED
        result = _atomic_write_json(
            self.result_path,
            {
                "schema_version": c5.RESULT_SCHEMA,
                "status": "complete",
                "stage": "C5/E4",
                "run_id": self.run_id,
                "completed_block_count": FROZEN_BLOCK_COUNT,
                "overall_interpretation": overall,
                "block_results": supplied,
                "bounded_claim": (
                    "one fixed history and one screening pass; no C5 outcome "
                    "establishes Whole-Update Parallel general safety"
                ),
            },
        )
        result_sha = hashlib.sha256(self.result_path.read_bytes()).hexdigest()
        _atomic_write_json(
            self.root_checkpoint_path,
            {
                "schema_version": ROOT_CHECKPOINT_SCHEMA,
                "run_id": self.run_id,
                "stage": "C5/E4",
                "status": "complete",
                "completed_block_indices": list(range(FROZEN_BLOCK_COUNT)),
                "partial_block_index": None,
                "result_sha256": result_sha,
                "result_payload_sha256": result["payload_sha256"],
            },
        )
        return result

    async def finalize_direct_observation(
        self,
        *,
        failure_event: Mapping[str, Any],
        completed_block_indices: Sequence[int],
    ) -> dict[str, Any]:
        """Emit the bounded scientific terminal for a direct invariant failure."""

        if self.result_path.exists() or self.direct_observation_path.exists():
            _fail("direct_observation_state_invalid")
        inspected = _inspect_run(self.run_dir, require_result=False)
        failures = [
            event for event in inspected["events"] if event.get("event_type") == "failure"
        ]
        completed = list(completed_block_indices)
        if (
            len(failures) != 1
            or dict(failures[0]) != dict(failure_event)
            or failures[0].get("failure_kind") != "direct_invariant_failure"
            or failures[0].get("scientific_interpretation")
            != c5.DIRECT_INVARIANT_VIOLATION_OBSERVED
            or completed != inspected["completed_block_indices"]
            or inspected["root_checkpoint"].get("status") != INCOMPLETE_NON_MERGEABLE
            or inspected["root_checkpoint"].get("partial_block_index")
            != failures[0].get("block_index")
        ):
            _fail("direct_observation_binding_invalid")
        return _atomic_write_json(
            self.direct_observation_path,
            {
                "schema_version": DIRECT_OBSERVATION_SCHEMA,
                "status": DIRECT_OBSERVATION_STATUS,
                "stage": "C5/E4",
                "run_id": self.run_id,
                "completed_block_count": len(completed),
                "completed_block_indices": completed,
                "failed_block_index": failures[0]["block_index"],
                "failed_source_sequence": failures[0].get("source_sequence"),
                "failure_event_sequence": failures[0]["event_sequence"],
                "failure_event_payload_sha256": failures[0]["payload_sha256"],
                "failure_stage": failures[0]["failure_stage"],
                "failure_kind": "direct_invariant_failure",
                "overall_interpretation": c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
                "bounded_claim": (
                    "direct invariant violation is an existence counterexample "
                    "for this fixed history/interleaving; it is not a failure-rate, "
                    "sufficiency, repeatability, or generality claim"
                ),
            },
        )


def _inspect_run(run_dir: Path, *, require_result: bool) -> dict[str, Any]:
    root = Path(run_dir)
    manifest = _read_sealed_json(root / "manifest.json", "manifest_invalid")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        _fail("manifest_invalid")
    if root.name != run_id:
        _fail("run_directory_identity_invalid")
    schedule = _read_sealed_json(root / "schedule.json", "schedule_invalid")
    blocks = _validate_schedule(schedule, run_id)
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("schedule_payload_sha256") != schedule.get("payload_sha256")
        or manifest.get("planned_blocks") != blocks
    ):
        _fail("manifest_schedule_binding_invalid")
    events = _read_events(root / "events.jsonl", run_id)
    _validate_event_contracts(events, blocks, schedule)
    by_sequence = {event["event_sequence"]: event for event in events}
    failures = [event for event in events if event["event_type"] == "failure"]
    block_results: list[dict[str, Any]] = []
    completed_indices: list[int] = []
    episode_checkpoint_count = 0
    checkpoint_bound_event_sequences: set[int] = set()
    for block_index in range(FROZEN_BLOCK_COUNT):
        block_dir = root / "blocks" / f"{block_index:03d}"
        episode_paths = sorted((block_dir / "episodes").glob("*.json"))
        seen_sources: set[int] = set()
        for path in episode_paths:
            checkpoint = _read_sealed_json(path, "episode_checkpoint_invalid")
            source = checkpoint.get("source_sequence")
            if (
                checkpoint.get("schema_version") != EPISODE_CHECKPOINT_SCHEMA
                or checkpoint.get("run_id") != run_id
                or checkpoint.get("block_index") != block_index
                or not isinstance(source, int)
                or source in seen_sources
            ):
                _fail("episode_checkpoint_invalid")
            intent = by_sequence.get(checkpoint.get("intent_event_sequence"))
            publication = by_sequence.get(checkpoint.get("publication_event_sequence"))
            if (
                not intent
                or not publication
                or intent.get("event_type") != "intent"
                or publication.get("event_type") != "publication"
                or intent.get("block_index") != block_index
                or publication.get("block_index") != block_index
                or intent.get("source_sequence") != source
                or publication.get("source_sequence") != source
                or intent.get("concurrency") != FROZEN_CONCURRENCY_GRID[block_index]
                or publication.get("concurrency") != FROZEN_CONCURRENCY_GRID[block_index]
                or intent.get("graph_namespace") != blocks[block_index]["graph_namespace"]
                or publication.get("graph_namespace")
                != blocks[block_index]["graph_namespace"]
                or intent.get("episode_source_sha256")
                != schedule["episode_source_hashes"][source]
                or publication.get("episode_source_sha256")
                != schedule["episode_source_hashes"][source]
                or intent.get("arrival_timestamp_ns")
                != publication.get("arrival_timestamp_ns")
                or intent.get("worker_id") != publication.get("worker_id")
                or intent.get("event_sequence") >= publication.get("event_sequence")
                or intent.get("payload_sha256") != checkpoint.get("intent_payload_sha256")
                or publication.get("payload_sha256")
                != checkpoint.get("publication_payload_sha256")
            ):
                _fail("episode_event_binding_invalid")
            event_sequences = {
                int(intent["event_sequence"]), int(publication["event_sequence"])
            }
            if checkpoint_bound_event_sequences.intersection(event_sequences):
                _fail("episode_event_binding_invalid")
            checkpoint_bound_event_sequences.update(event_sequences)
            seen_sources.add(source)
            episode_checkpoint_count += 1
        block_path = block_dir / "checkpoint.json"
        if block_path.exists():
            checkpoint = _read_sealed_json(block_path, "block_checkpoint_invalid")
            result = checkpoint.get("block_result")
            if (
                checkpoint.get("schema_version") != BLOCK_CHECKPOINT_SCHEMA
                or checkpoint.get("run_id") != run_id
                or checkpoint.get("status") != "completed"
                or checkpoint.get("block_index") != block_index
                or checkpoint.get("concurrency") != FROZEN_CONCURRENCY_GRID[block_index]
                or checkpoint.get("graph_namespace")
                != blocks[block_index]["graph_namespace"]
                or set(seen_sources) != set(range(FROZEN_EPISODES_PER_BLOCK))
                or not isinstance(result, Mapping)
                or result.get("payload_sha256") != c5.payload_sha256(result)
                or checkpoint.get("block_result_payload_sha256")
                != result.get("payload_sha256")
            ):
                _fail("block_checkpoint_invalid")
            block_publications = [
                event
                for event in events
                if event.get("event_type") == "publication"
                and event.get("block_index") == block_index
                and event.get("event_sequence") in checkpoint_bound_event_sequences
            ]
            _recompute_block_result(
                block_index=block_index,
                result=result,
                publications=block_publications,
                schedule=schedule,
            )
            completed_indices.append(block_index)
            block_results.append(deepcopy(dict(result)))
    if completed_indices != list(range(len(completed_indices))):
        _fail("completed_blocks_not_prefix")
    serial_reference: dict[str, Any] | None = None
    if completed_indices:
        candidate = block_results[0].get("serial_reference")
        if not isinstance(candidate, Mapping):
            _fail("serial_reference_missing")
        graph_hash = candidate.get("canonical_graph_sha256")
        retrieved_ids = candidate.get("retrieved_episode_ids")
        retrieved_hash = candidate.get("retrieved_episode_ids_sha256")
        if (
            not isinstance(graph_hash, str)
            or _SHA256_RE.fullmatch(graph_hash) is None
            or not isinstance(retrieved_ids, list)
            or any(not isinstance(item, str) or not item for item in retrieved_ids)
            or len(set(retrieved_ids)) != len(retrieved_ids)
            or retrieved_hash
            != hashlib.sha256(canonical_json_bytes(retrieved_ids)).hexdigest()
        ):
            _fail("serial_reference_invalid")
        serial_reference = deepcopy(dict(candidate))
        if any("serial_reference" in item for item in block_results[1:]):
            _fail("serial_reference_not_c1_only")
    root_checkpoint = _read_sealed_json(root / "checkpoint.json", "root_checkpoint_invalid")
    if (
        root_checkpoint.get("schema_version") != ROOT_CHECKPOINT_SCHEMA
        or root_checkpoint.get("run_id") != run_id
    ):
        _fail("root_checkpoint_invalid")
    if require_result:
        result = _read_sealed_json(root / "e4_whole_parallel.json", "result_invalid")
        result_raw_sha = hashlib.sha256((root / "e4_whole_parallel.json").read_bytes()).hexdigest()
        if (
            result.get("schema_version") != c5.RESULT_SCHEMA
            or result.get("run_id") != run_id
            or result.get("completed_block_count") != FROZEN_BLOCK_COUNT
            or result.get("block_results") != block_results
            or root_checkpoint.get("status") != "complete"
            or root_checkpoint.get("completed_block_indices")
            != list(range(FROZEN_BLOCK_COUNT))
            or root_checkpoint.get("result_sha256") != result_raw_sha
            or root_checkpoint.get("result_payload_sha256") != result.get("payload_sha256")
            or failures
        ):
            _fail("result_binding_invalid")
        expected_event_sequences = set(range(len(events)))
        if (
            len(events) != FROZEN_BLOCK_COUNT * FROZEN_EPISODES_PER_BLOCK * 2
            or checkpoint_bound_event_sequences != expected_event_sequences
        ):
            _fail("unreferenced_event_invalid")
    return {
        "run_id": run_id,
        "events": events,
        "event_count": len(events),
        "failure_event_count": len(failures),
        "episode_checkpoint_count": episode_checkpoint_count,
        "completed_block_indices": completed_indices,
        "completed_block_count": len(completed_indices),
        "block_results": block_results,
        "serial_reference": serial_reference,
        "root_checkpoint": root_checkpoint,
    }


def _inspect_direct_observation(
    root: Path, inspected: Mapping[str, Any]
) -> dict[str, Any]:
    if (root / "e4_whole_parallel.json").exists():
        _fail("direct_observation_result_conflict")
    observation = _read_sealed_json(
        root / "c5_direct_observation.json", "direct_observation_invalid"
    )
    failures = [
        event for event in inspected["events"] if event.get("event_type") == "failure"
    ]
    if len(failures) != 1:
        _fail("direct_observation_binding_invalid")
    failure = failures[0]
    if (
        observation.get("schema_version") != DIRECT_OBSERVATION_SCHEMA
        or observation.get("status") != DIRECT_OBSERVATION_STATUS
        or observation.get("stage") != "C5/E4"
        or observation.get("run_id") != inspected["run_id"]
        or observation.get("completed_block_count")
        != inspected["completed_block_count"]
        or observation.get("completed_block_indices")
        != inspected["completed_block_indices"]
        or observation.get("failed_block_index") != failure.get("block_index")
        or observation.get("failed_source_sequence")
        != failure.get("source_sequence")
        or observation.get("failure_event_sequence")
        != failure.get("event_sequence")
        or observation.get("failure_event_payload_sha256")
        != failure.get("payload_sha256")
        or observation.get("failure_stage") != failure.get("failure_stage")
        or observation.get("failure_kind") != "direct_invariant_failure"
        or observation.get("overall_interpretation")
        != c5.DIRECT_INVARIANT_VIOLATION_OBSERVED
        or failure.get("failure_kind") != "direct_invariant_failure"
        or failure.get("scientific_interpretation")
        != c5.DIRECT_INVARIANT_VIOLATION_OBSERVED
    ):
        _fail("direct_observation_binding_invalid")
    return observation


def inspect_c5_resume_prefix(
    run_dir: Path,
    *,
    expected_run_id: str | None = None,
    expected_schedule: Mapping[str, Any] | None = None,
    expected_provenance_hashes: Mapping[str, str] | None = None,
) -> C5ResumeInspection:
    root = Path(run_dir)
    inspected = _inspect_run(root, require_result=False)
    if any(
        value is not None
        for value in (expected_run_id, expected_schedule, expected_provenance_hashes)
    ):
        if (
            expected_run_id is None
            or expected_schedule is None
            or expected_provenance_hashes is None
            or inspected["run_id"] != expected_run_id
        ):
            _fail("resume_binding_invalid")
        try:
            _validate_recovery_binding(root, expected_schedule, expected_provenance_hashes)
        except C5LiveArtifactError:
            _fail("resume_binding_invalid")
    root_checkpoint = inspected["root_checkpoint"]
    completed = tuple(inspected["completed_block_indices"])
    declared = root_checkpoint.get("completed_block_indices")
    if declared != list(completed):
        _fail("root_completed_blocks_mismatch")
    partial = root_checkpoint.get("partial_block_index")
    if partial is not None and partial != len(completed):
        _fail("partial_block_not_next")
    if partial is None:
        event_blocks = {
            event.get("block_index")
            for event in inspected["events"]
            if isinstance(event.get("block_index"), int)
            and event.get("block_index") not in completed
        }
        partial = min(event_blocks) if event_blocks else None
    return C5ResumeInspection(
        completed,
        partial,
        tuple(deepcopy(inspected["block_results"])),
        deepcopy(inspected["serial_reference"]),
    )


def _validate_recovery_binding(
    run_dir: Path,
    schedule: Mapping[str, Any],
    provenance_hashes: Mapping[str, str],
) -> dict[str, Any]:
    manifest = _read_sealed_json(run_dir / "manifest.json", "manifest_invalid")
    persisted = _read_sealed_json(run_dir / "schedule.json", "schedule_invalid")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        _fail("recovery_binding_invalid")
    supplied = deepcopy(dict(schedule))
    _validate_schedule(supplied, run_id)
    normalized_provenance = {
        str(key): _validate_sha256(value, "recovery_binding_invalid")
        for key, value in sorted(provenance_hashes.items())
    }
    if (
        persisted != supplied
        or manifest.get("schedule_payload_sha256") != supplied.get("payload_sha256")
        or manifest.get("provenance_hashes") != normalized_provenance
    ):
        _fail("recovery_binding_invalid")
    return manifest


def _validate_completed_event_prefix(
    events: Sequence[Mapping[str, Any]], completed_block_count: int
) -> list[dict[str, Any]]:
    retained = [
        deepcopy(dict(event))
        for event in events
        if event.get("block_index") in range(completed_block_count)
        and event.get("event_type") in {"intent", "publication"}
    ]
    expected = {
        (block_index, source_sequence, event_type)
        for block_index in range(completed_block_count)
        for source_sequence in range(FROZEN_EPISODES_PER_BLOCK)
        for event_type in ("intent", "publication")
    }
    observed = [
        (
            event.get("block_index"),
            event.get("source_sequence"),
            event.get("event_type"),
        )
        for event in retained
    ]
    if len(observed) != len(expected) or set(observed) != expected:
        _fail("recovery_completed_event_prefix_invalid")
    for sequence, event in enumerate(retained):
        if event.get("event_sequence") != sequence:
            _fail("recovery_completed_event_prefix_invalid")
    return retained


def _trim_block_artifacts(
    run_dir: Path, completed_block_count: int
) -> tuple[list[str], list[str]]:
    discarded_paths: list[str] = []
    discarded_hashes: list[str] = []
    for block_index in range(completed_block_count, FROZEN_BLOCK_COUNT):
        block_root = run_dir / "blocks" / f"{block_index:03d}"
        if not block_root.exists():
            continue
        if block_root.is_symlink():
            _fail("recovery_artifact_symlink_invalid")
        files = sorted(
            (item for item in block_root.rglob("*") if item.is_file()),
            key=lambda item: item.as_posix(),
        )
        for path in files:
            if path.is_symlink():
                _fail("recovery_artifact_symlink_invalid")
            discarded_paths.append(path.relative_to(run_dir).as_posix())
            discarded_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
        for path in reversed(files):
            path.unlink()
        directories = sorted(
            (item for item in block_root.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for path in directories:
            path.rmdir()
        block_root.rmdir()
    if discarded_paths:
        _fsync_directory(run_dir / "blocks")
    return discarded_paths, discarded_hashes


def _next_recovery_audit(root: Path) -> tuple[int, str | None]:
    """Validate the immutable recovery chain and return its next link."""

    audit_root = root / "resume_rollback_audits"
    latest_path = root / "resume_rollback_audit.json"
    paths = sorted(audit_root.glob("*.json")) if audit_root.exists() else []
    if audit_root.exists() and (
        audit_root.is_symlink() or any(path.is_symlink() for path in paths)
    ):
        _fail("recovery_audit_chain_invalid")

    previous: str | None = None
    for sequence, path in enumerate(paths):
        if path.name != f"{sequence:06d}.json":
            _fail("recovery_audit_chain_invalid")
        audit = _read_sealed_json(path, "recovery_audit_chain_invalid")
        if (
            audit.get("schema_version") != RECOVERY_AUDIT_SCHEMA
            or audit.get("run_id") != root.name
            or audit.get("recovery_sequence") != sequence
            or audit.get("previous_recovery_audit_payload_sha256") != previous
        ):
            _fail("recovery_audit_chain_invalid")
        previous = str(audit["payload_sha256"])

    if latest_path.exists():
        latest = _read_sealed_json(latest_path, "recovery_audit_chain_invalid")
        if paths:
            if latest != _read_sealed_json(
                paths[-1], "recovery_audit_chain_invalid"
            ):
                _fail("recovery_audit_chain_invalid")
        else:
            # Migrate a legacy single audit into link zero without discarding it.
            legacy = deepcopy(latest)
            legacy.pop("payload_sha256", None)
            legacy["recovery_sequence"] = 0
            legacy["previous_recovery_audit_payload_sha256"] = None
            migrated = _atomic_write_json(audit_root / "000000.json", legacy)
            _atomic_write_json(latest_path, migrated)
            previous = str(migrated["payload_sha256"])
            paths = [audit_root / "000000.json"]

    return len(paths), previous


def _recover_to_completed_prefix(
    *,
    run_dir: Path,
    schedule: Mapping[str, Any],
    provenance_hashes: Mapping[str, str],
    require_terminal_failure: bool,
) -> dict[str, Any]:
    root = Path(run_dir)
    _validate_recovery_binding(root, schedule, provenance_hashes)
    inspected = _inspect_run(root, require_result=False)
    if (root / "e4_whole_parallel.json").exists():
        _fail("completed_run_not_resumable")
    if (root / "c5_direct_observation.json").exists():
        _fail("direct_observation_not_resumable")
    events = inspected["events"]
    failures = [event for event in events if event.get("event_type") == "failure"]
    root_checkpoint = inspected["root_checkpoint"]
    completed = list(inspected["completed_block_indices"])
    next_block = len(completed)
    if next_block >= FROZEN_BLOCK_COUNT:
        _fail("recovery_no_incomplete_block")
    if require_terminal_failure:
        if (
            len(failures) != 1
            or root_checkpoint.get("status") != INCOMPLETE_NON_MERGEABLE
            or root_checkpoint.get("completed_block_indices") != completed
            or root_checkpoint.get("partial_block_index") != next_block
            or failures[0].get("block_index") != next_block
        ):
            _fail("terminal_recovery_failure_invalid")
    elif failures:
        _fail("running_recovery_contains_failure")
    elif root_checkpoint.get("status") not in {"planned", "running"}:
        _fail("running_recovery_status_invalid")

    retained = _validate_completed_event_prefix(events, next_block)
    discarded = [
        deepcopy(dict(event))
        for event in events
        if event.get("event_sequence") >= len(retained)
    ]
    if len(retained) + len(discarded) != len(events):
        _fail("recovery_event_partition_invalid")
    event_audit = {
        "retained_event_count": len(retained),
        "discarded_event_count": len(discarded),
        "discarded_event_payloads_sha256": hashlib.sha256(
            canonical_json_bytes(
                [event.get("payload_sha256") for event in discarded]
            )
        ).hexdigest(),
    }
    encoded = b"".join(canonical_json_bytes(event) + b"\n" for event in retained)
    _atomic_replace_bytes(root / "events.jsonl", encoded)
    discarded_paths, discarded_hashes = _trim_block_artifacts(root, next_block)
    recovery_sequence, previous_audit_sha256 = _next_recovery_audit(root)
    audit_value = {
            "schema_version": RECOVERY_AUDIT_SCHEMA,
            "run_id": inspected["run_id"],
            "recovery_sequence": recovery_sequence,
            "previous_recovery_audit_payload_sha256": previous_audit_sha256,
            "status": (
                "terminal_failure_recovered_to_completed_block_prefix"
                if require_terminal_failure
                else "running_interruption_recovered_to_completed_block_prefix"
            ),
            "completed_block_indices": completed,
            "next_block_index": next_block,
            "terminal_root_checkpoint_payload_sha256": root_checkpoint[
                "payload_sha256"
            ],
            "terminal_failure_event_payload_sha256": (
                failures[0]["payload_sha256"] if failures else None
            ),
            **event_audit,
            "discarded_checkpoint_count": len(discarded_paths),
            "discarded_checkpoint_paths": discarded_paths,
            "discarded_checkpoint_files_sha256": hashlib.sha256(
                canonical_json_bytes(discarded_hashes)
            ).hexdigest(),
        }
    audit = _atomic_write_json(
        root / "resume_rollback_audits" / f"{recovery_sequence:06d}.json",
        audit_value,
    )
    _atomic_write_json(
        root / "resume_rollback_audit.json",
        audit,
    )
    _atomic_write_json(
        root / "checkpoint.json",
        {
            "schema_version": ROOT_CHECKPOINT_SCHEMA,
            "run_id": inspected["run_id"],
            "stage": "C5/E4",
            "status": "running",
            "completed_block_indices": completed,
            "partial_block_index": next_block,
            "result_sha256": None,
            "result_payload_sha256": None,
            "recovery_audit_payload_sha256": audit["payload_sha256"],
        },
    )
    after = _inspect_run(root, require_result=False)
    if (
        after["failure_event_count"] != 0
        or after["completed_block_indices"] != completed
        or after["root_checkpoint"].get("status") != "running"
    ):
        _fail("recovery_post_trim_invalid")
    return seal_payload(
        {
            "schema_version": "membind.native-characterization-c5-resume-prefix.v1",
            "run_id": inspected["run_id"],
            "status": "running",
            "completed_block_indices": completed,
            "next_block_index": next_block,
            "discarded_event_count": len(discarded),
            "discarded_checkpoint_count": len(discarded_paths),
            "recovered_terminal_failure": require_terminal_failure,
            "rollback_audit_payload_sha256": audit["payload_sha256"],
        }
    )


def recover_c5_terminal_failure_to_resume_prefix(
    *,
    run_dir: Path,
    schedule: Mapping[str, Any],
    provenance_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Explicitly roll a durable terminal failure back to its closed blocks."""

    return _recover_to_completed_prefix(
        run_dir=Path(run_dir),
        schedule=schedule,
        provenance_hashes=provenance_hashes,
        require_terminal_failure=True,
    )


def prepare_c5_running_resume_prefix(
    *,
    run_dir: Path,
    schedule: Mapping[str, Any],
    provenance_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Roll an abruptly interrupted running prefix back to closed blocks."""

    return _recover_to_completed_prefix(
        run_dir=Path(run_dir),
        schedule=schedule,
        provenance_hashes=provenance_hashes,
        require_terminal_failure=False,
    )


def verify_c5_live_artifacts(run_dir: Path) -> dict[str, Any]:
    """Read-only verification; corruption always yields non-mergeable status."""

    try:
        inspected = _inspect_run(Path(run_dir), require_result=False)
        root = Path(run_dir)
        result_exists = (root / "e4_whole_parallel.json").exists()
        direct_exists = (root / "c5_direct_observation.json").exists()
        if result_exists:
            if direct_exists:
                _fail("terminal_artifact_conflict")
            inspected = _inspect_run(Path(run_dir), require_result=True)
            attempt_status = "complete"
            mergeable = True
            overall_interpretation = None
        elif direct_exists:
            direct = _inspect_direct_observation(root, inspected)
            attempt_status = DIRECT_OBSERVATION_STATUS
            mergeable = True
            overall_interpretation = direct["overall_interpretation"]
        else:
            attempt_status = INCOMPLETE_NON_MERGEABLE
            mergeable = False
            overall_interpretation = None
        return seal_payload(
            {
                "schema_version": VERIFICATION_SCHEMA,
                "status": "verified",
                "run_id": inspected["run_id"],
                "attempt_status": attempt_status,
                "mergeable": mergeable,
                "overall_interpretation": overall_interpretation,
                "event_count": inspected["event_count"],
                "failure_event_count": inspected["failure_event_count"],
                "episode_checkpoint_count": inspected["episode_checkpoint_count"],
                "completed_block_count": inspected["completed_block_count"],
            }
        )
    except (C5LiveArtifactError, OSError, ValueError, TypeError):
        return seal_payload(
            {
                "schema_version": VERIFICATION_SCHEMA,
                "status": "verified",
                "run_id": None,
                "attempt_status": INCOMPLETE_NON_MERGEABLE,
                "mergeable": False,
                "overall_interpretation": None,
                "event_count": 0,
                "failure_event_count": 0,
                "episode_checkpoint_count": 0,
                "completed_block_count": 0,
            }
        )


__all__ = [
    "C5LiveArtifactError",
    "C5LiveArtifactStore",
    "C5ResumeInspection",
    "DIRECT_OBSERVATION_STATUS",
    "INCOMPLETE_NON_MERGEABLE",
    "canonical_json_bytes",
    "inspect_c5_resume_prefix",
    "payload_sha256",
    "prepare_c5_running_resume_prefix",
    "recover_c5_terminal_failure_to_resume_prefix",
    "seal_payload",
    "verify_c5_live_artifacts",
]
