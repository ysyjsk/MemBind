"""Crash-consistent, sanitized artifacts for the frozen C5 live screening.

The live scheduler owns execution semantics; this module owns only durable
evidence.  Every event append is serialized and fsynced, every checkpoint is
atomically replaced and directory-fsynced, and the read-only verifier treats
any broken seal or cross-file binding as non-mergeable.
"""

from __future__ import annotations

import asyncio
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
INCOMPLETE_NON_MERGEABLE = "incomplete_invalid_non_mergeable"

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
        self._append_lock = asyncio.Lock()
        self._next_event_sequence = len(_read_events(self.events_path, run_id))

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


def _inspect_run(run_dir: Path, *, require_result: bool) -> dict[str, Any]:
    root = Path(run_dir)
    manifest = _read_sealed_json(root / "manifest.json", "manifest_invalid")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        _fail("manifest_invalid")
    schedule = _read_sealed_json(root / "schedule.json", "schedule_invalid")
    blocks = _validate_schedule(schedule, run_id)
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA
        or manifest.get("schedule_payload_sha256") != schedule.get("payload_sha256")
        or manifest.get("planned_blocks") != blocks
    ):
        _fail("manifest_schedule_binding_invalid")
    events = _read_events(root / "events.jsonl", run_id)
    by_sequence = {event["event_sequence"]: event for event in events}
    failures = [event for event in events if event["event_type"] == "failure"]
    block_results: list[dict[str, Any]] = []
    completed_indices: list[int] = []
    episode_checkpoint_count = 0
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
                or intent.get("payload_sha256") != checkpoint.get("intent_payload_sha256")
                or publication.get("payload_sha256")
                != checkpoint.get("publication_payload_sha256")
            ):
                _fail("episode_event_binding_invalid")
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


def inspect_c5_resume_prefix(run_dir: Path) -> C5ResumeInspection:
    inspected = _inspect_run(Path(run_dir), require_result=False)
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


def verify_c5_live_artifacts(run_dir: Path) -> dict[str, Any]:
    """Read-only verification; corruption always yields non-mergeable status."""

    try:
        inspected = _inspect_run(Path(run_dir), require_result=False)
        result_exists = (Path(run_dir) / "e4_whole_parallel.json").exists()
        if result_exists:
            inspected = _inspect_run(Path(run_dir), require_result=True)
            attempt_status = "complete"
        else:
            attempt_status = INCOMPLETE_NON_MERGEABLE
        return seal_payload(
            {
                "schema_version": VERIFICATION_SCHEMA,
                "status": "verified",
                "run_id": inspected["run_id"],
                "attempt_status": attempt_status,
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
    "INCOMPLETE_NON_MERGEABLE",
    "canonical_json_bytes",
    "inspect_c5_resume_prefix",
    "payload_sha256",
    "seal_payload",
    "verify_c5_live_artifacts",
]
