"""Thin, fail-closed live orchestrator for the frozen C4/E3 replay.

All live boundaries are injected as block runtimes.  This module owns only the
C4 authorization gate, immutable planned artifact creation, exact frozen-grid
validation, per-block lifecycle isolation, and durable progress coordination.
It does not construct Graphiti clients or read service configuration.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

import current_state_gate
import native_characterization_c4 as c4
import native_characterization_c4_artifacts as c4_artifacts
import native_characterization_c4_async as c4_async


FROZEN_BLOCK_COUNT = 10
FROZEN_EPISODE_COUNT = 49
FROZEN_HISTORY_ID = "07741c45"
START_LEAD_NS = 1_000_000_000
_FROZEN_METHODS = (c4.NATIVE_SYNC,) * 5 + (c4.NATIVE_ASYNC_SERIAL,) * 5
_FROZEN_LOADS = (0.5, 0.8, 1.0, 1.2, 1.5) * 2
_RUN_ID_RE = re.compile(r"^c4-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NativeCharacterizationC4RunnerError(RuntimeError):
    """Sanitized runner contract failure."""


class NamespacePreflightError(NativeCharacterizationC4RunnerError):
    """Raised when a fresh C4 block namespace is not exactly empty."""


@dataclass(frozen=True)
class NamespaceCounts:
    nodes: int
    relationships: int


@dataclass(frozen=True)
class C4Block:
    block_index: int
    method: str
    normalized_offered_load: float
    graph_namespace: str
    interarrival_ns: int
    absolute_arrival_offsets_ns: tuple[int, ...]


class C4BlockRuntime(Protocol):
    """One newly created U0 lifecycle bound to one graph namespace."""

    async def namespace_counts(self) -> NamespaceCounts: ...

    async def service(self, episode: c4.Episode, service_start_ns: int) -> None: ...

    async def close(self) -> None: ...


class C4Store(Protocol):
    def append_enqueue_event(self, value: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def append_publication_event(self, value: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def write_episode_checkpoint(self, **value: Any) -> Any: ...

    def write_block_checkpoint(self, **value: Any) -> Any: ...

    def write_root_checkpoint(self, **value: Any) -> Any: ...

    def record_failure(self, **value: Any) -> Any: ...

    def record_stage_failure(self, **value: Any) -> Any: ...

    def finalize_success(self, **value: Any) -> Any: ...


RuntimeFactory = Callable[[C4Block], Awaitable[C4BlockRuntime]]
GateChecker = Callable[..., current_state_gate.GateDecision]
StoreFactory = Callable[..., C4Store]
Replay = Callable[..., Awaitable[dict[str, object]]]
ProgressSink = Callable[[Mapping[str, object]], None]
PostFinalizeVerifier = Callable[[C4Store], Mapping[str, object]]


@dataclass
class _FailureContext:
    error: BaseException | None = None
    token_envelope: dict[str, int | None] | None = None


def new_c4_run_id() -> str:
    """Create a non-C2 attempt identity in the artifact layer's namespace."""

    return f"c4-{secrets.token_hex(8)}"


def _fail(code: str) -> NativeCharacterizationC4RunnerError:
    return NativeCharacterizationC4RunnerError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail(code)
    return value


def _validate_schedule(schedule: Mapping[str, Any]) -> list[C4Block]:
    if not isinstance(schedule, Mapping):
        raise _fail("schedule_invalid")
    if (
        schedule.get("schema_version") != c4_artifacts.SCHEDULE_SCHEMA
        or schedule.get("status") != "dry_run"
        or schedule.get("stage") != "C4/E3_OFFLINE_SCHEDULE"
        or schedule.get("history_id") != FROZEN_HISTORY_ID
        or schedule.get("payload_sha256") != c4_artifacts.payload_sha256(schedule)
    ):
        raise _fail("schedule_invalid")
    episode_ids = schedule.get("episode_ids")
    if episode_ids != [
        f"{FROZEN_HISTORY_ID}:{source_sequence}"
        for source_sequence in range(FROZEN_EPISODE_COUNT)
    ]:
        raise _fail("schedule_episode_grid_invalid")
    supplied_blocks = schedule.get("block_schedules")
    if not isinstance(supplied_blocks, list) or len(supplied_blocks) != FROZEN_BLOCK_COUNT:
        raise _fail("schedule_block_grid_invalid")

    blocks: list[C4Block] = []
    namespaces: set[str] = set()
    for block_index, (supplied, method, load) in enumerate(
        zip(supplied_blocks, _FROZEN_METHODS, _FROZEN_LOADS)
    ):
        if not isinstance(supplied, Mapping):
            raise _fail("schedule_block_invalid")
        namespace = supplied.get("graph_namespace")
        interarrival = _nonnegative_int(
            supplied.get("interarrival_ns"), "schedule_interarrival_invalid"
        )
        offsets = supplied.get("absolute_arrival_offsets_ns")
        if (
            supplied.get("block_index") != block_index
            or supplied.get("method") != method
            or supplied.get("normalized_offered_load") != load
            or not isinstance(namespace, str)
            or not namespace.startswith("nc-e3-")
            or namespace in namespaces
            or interarrival <= 0
            or not isinstance(offsets, list)
            or len(offsets) != FROZEN_EPISODE_COUNT
            or offsets
            != [
                source_sequence * interarrival
                for source_sequence in range(FROZEN_EPISODE_COUNT)
            ]
        ):
            raise _fail("schedule_block_invalid")
        namespaces.add(namespace)
        blocks.append(
            C4Block(
                block_index=block_index,
                method=method,
                normalized_offered_load=load,
                graph_namespace=namespace,
                interarrival_ns=interarrival,
                absolute_arrival_offsets_ns=tuple(offsets),
            )
        )
    return blocks


def _validate_episodes(
    episodes: Sequence[c4.Episode], episode_source_hashes: Sequence[str]
) -> tuple[list[c4.Episode], dict[int, str]]:
    if (
        isinstance(episodes, (str, bytes))
        or isinstance(episode_source_hashes, (str, bytes))
        or len(episodes) != FROZEN_EPISODE_COUNT
        or len(episode_source_hashes) != FROZEN_EPISODE_COUNT
    ):
        raise _fail("episode_grid_invalid")
    retained = list(episodes)
    if [episode.source_sequence for episode in retained] != list(
        range(FROZEN_EPISODE_COUNT)
    ):
        raise _fail("episode_sequence_invalid")
    hashes: dict[int, str] = {}
    for source_sequence, value in enumerate(episode_source_hashes):
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise _fail("episode_source_hash_invalid")
        hashes[source_sequence] = value
    return retained, hashes


def _extract_token_envelope(error: BaseException) -> dict[str, int | None]:
    supplied = getattr(error, "token_envelope", None)
    if not isinstance(supplied, Mapping):
        return c4_artifacts.nullable_token_envelope()
    try:
        return c4_artifacts.nullable_token_envelope(
            prompt_tokens=supplied.get("prompt_tokens"),
            output_tokens=supplied.get("output_tokens"),
            requested_max_tokens=supplied.get("requested_max_tokens"),
        )
    except c4_artifacts.NativeCharacterizationC4ArtifactError:
        return c4_artifacts.nullable_token_envelope()


def _discard_progress(_event: Mapping[str, object]) -> None:
    return


async def _emit_progress(
    progress_sink: ProgressSink, event: Mapping[str, object]
) -> None:
    """Emit sanitized progress without letting an observer alter the replay."""

    if progress_sink is _discard_progress:
        return
    try:
        sanitized = c4_artifacts.seal_payload(event)
        sanitized.pop("payload_sha256")
        await asyncio.to_thread(progress_sink, sanitized)
    except Exception:
        return


class _ArtifactDurableWriter:
    """Adapt the fsynced artifact API to the async scheduling boundary."""

    def __init__(
        self,
        *,
        store: C4Store,
        block: C4Block,
        episode_source_hashes: Mapping[int, str],
        completed_block_indices: list[int],
        failure_context: _FailureContext,
        run_id: str,
        progress_sink: ProgressSink,
    ) -> None:
        self.store = store
        self.block = block
        self.episode_source_hashes = episode_source_hashes
        self.episode_ids = {
            source_sequence: f"{FROZEN_HISTORY_ID}:{source_sequence}"
            for source_sequence in range(FROZEN_EPISODE_COUNT)
        }
        self.completed_block_indices = completed_block_indices
        self.failure_context = failure_context
        self.run_id = run_id
        self.progress_sink = progress_sink
        self.completed_source_sequences: list[int] = []
        self.failure_recorded = False

    async def persist_enqueue(
        self, episode: c4.Episode, actual_arrival_timestamp_ns: int
    ) -> None:
        await asyncio.to_thread(
            self.store.append_enqueue_event,
            {
                "block_index": self.block.block_index,
                "source_sequence": episode.source_sequence,
                "method": self.block.method,
                "graph_namespace": self.block.graph_namespace,
                "episode_id": self.episode_ids[episode.source_sequence],
                "arrival_timestamp_ns": actual_arrival_timestamp_ns,
                "episode_source_sha256": self.episode_source_hashes[
                    episode.source_sequence
                ],
            },
        )

    async def persist_publication(self, record: dict[str, object]) -> None:
        source_sequence = int(record["source_sequence"])
        publication_event = await asyncio.to_thread(
            self.store.append_publication_event,
            {
                "block_index": self.block.block_index,
                "graph_namespace": self.block.graph_namespace,
                "episode_id": self.episode_ids[source_sequence],
                "episode_source_sha256": self.episode_source_hashes[source_sequence],
                "scheduled_arrival_timestamp_ns": record[
                    "planned_arrival_timestamp_ns"
                ],
                **record,
            },
        )
        await asyncio.to_thread(
            self.store.write_episode_checkpoint,
            block_index=self.block.block_index,
            source_sequence=source_sequence,
            status="completed",
            progress={
                "episode_id": self.episode_ids[source_sequence],
                "graph_namespace": self.block.graph_namespace,
                "method": self.block.method,
                "publication_event_payload_sha256": publication_event[
                    "payload_sha256"
                ],
            },
        )
        self.completed_source_sequences.append(source_sequence)
        metrics = c4.compute_episode_metrics(record)
        await _emit_progress(
            self.progress_sink,
            {
                "event": "episode_published",
                "run_id": self.run_id,
                "block_index": self.block.block_index,
                "source_sequence": source_sequence,
                "completed_block_count": len(self.completed_block_indices),
                "completed_episode_count": (
                    len(self.completed_block_indices) * FROZEN_EPISODE_COUNT
                    + len(self.completed_source_sequences)
                ),
                "schedule_lag_ns": int(record["schedule_lag_ns"]),
                **metrics,
            },
        )

    async def _record_failure(
        self,
        *,
        source_sequence: int,
        error: BaseException | str,
        token_envelope: Mapping[str, Any] | None,
    ) -> None:
        if self.failure_recorded:
            return
        global_completed = (
            len(self.completed_block_indices) * FROZEN_EPISODE_COUNT
            + len(self.completed_source_sequences)
        )
        await asyncio.to_thread(
            self.store.record_failure,
            block_index=self.block.block_index,
            source_sequence=source_sequence,
            error=error,
            completed_source_sequences=self.completed_source_sequences,
            completed_block_indices=self.completed_block_indices,
            completed_episode_count=global_completed,
            token_envelope=token_envelope,
        )
        self.failure_recorded = True
        error_class = (
            f"{type(error).__module__}.{type(error).__qualname__}"
            if isinstance(error, BaseException)
            else error
        )
        await _emit_progress(
            self.progress_sink,
            {
                "event": "terminal_failure",
                "run_id": self.run_id,
                "block_index": self.block.block_index,
                "source_sequence": source_sequence,
                "completed_block_count": len(self.completed_block_indices),
                "completed_episode_count": global_completed,
                "error_class": error_class,
                "token_envelope": dict(
                    token_envelope or c4_artifacts.nullable_token_envelope()
                ),
            },
        )

    async def persist_failure(self, checkpoint: dict[str, object]) -> None:
        source_sequence = int(checkpoint["failed_source_sequence"])
        error: BaseException | str = (
            self.failure_context.error
            if self.failure_context.error is not None
            else str(checkpoint.get("error_class", "C4ServiceError"))
        )
        await self._record_failure(
            source_sequence=source_sequence,
            error=error,
            token_envelope=self.failure_context.token_envelope,
        )

    async def record_external_failure(
        self, source_sequence: int, error: BaseException
    ) -> None:
        await self._record_failure(
            source_sequence=source_sequence,
            error=error,
            token_envelope=_extract_token_envelope(error),
        )


async def _close_after_failure(runtime: C4BlockRuntime | None) -> None:
    if runtime is None:
        return
    try:
        await runtime.close()
    except Exception:
        # The primary failure is already durable.  A teardown error must not
        # replace it or permit another block to start.
        return


async def run_c4_live(
    *,
    runs_root: str | Path,
    schedule: Mapping[str, Any],
    provenance_hashes: Mapping[str, Any],
    episodes: Sequence[c4.Episode],
    episode_source_hashes: Sequence[str],
    clock: c4_async.AsyncClock,
    runtime_factory: RuntimeFactory,
    state_path: str | Path,
    creation_command: Sequence[str],
    gate_checker: GateChecker = current_state_gate.require_live_action,
    store_factory: StoreFactory = c4_artifacts.C4ArtifactStore.create,
    run_id_factory: Callable[[], str] = new_c4_run_id,
    replay: Replay = c4_async.run_async_replay,
    progress_sink: ProgressSink = _discard_progress,
    start_lead_ns: int = START_LEAD_NS,
    post_finalize_verifier: PostFinalizeVerifier | None = None,
) -> dict[str, object]:
    """Execute the exact ten-block C4 grid or durably stop at first failure."""

    blocks = _validate_schedule(schedule)
    retained_episodes, source_hash_by_sequence = _validate_episodes(
        episodes, episode_source_hashes
    )
    start_lead = _nonnegative_int(start_lead_ns, "start_lead_ns_invalid")
    if start_lead <= 0:
        raise _fail("start_lead_ns_invalid")

    decision = gate_checker(
        current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4,
        state_path=state_path,
    )
    if (
        decision.allowed is not True
        or decision.action
        != current_state_gate.LiveAction.NATIVE_CHARACTERIZATION_C4.value
    ):
        raise _fail("c4_live_grant_not_exact")

    run_id = run_id_factory()
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        raise _fail("c4_run_id_invalid")

    # C4ArtifactStore.create persists the immutable planned manifest, schedule,
    # empty event log, and planned root checkpoint before a runtime is opened.
    store = await asyncio.to_thread(
        store_factory,
        runs_root=runs_root,
        run_id=run_id,
        schedule=schedule,
        provenance_hashes=provenance_hashes,
        creation_command=creation_command,
    )
    await _emit_progress(
        progress_sink,
        {
            "event": "manifest_planned",
            "run_id": run_id,
            "planned_block_count": FROZEN_BLOCK_COUNT,
            "planned_episode_count": FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT,
        },
    )

    completed_blocks: list[int] = []
    block_summaries: list[dict[str, object]] = []
    retained_runtimes: list[C4BlockRuntime] = []
    for block in blocks:
        runtime: C4BlockRuntime | None = None
        failure_context = _FailureContext()
        writer = _ArtifactDurableWriter(
            store=store,
            block=block,
            episode_source_hashes=source_hash_by_sequence,
            completed_block_indices=completed_blocks,
            failure_context=failure_context,
            run_id=run_id,
            progress_sink=progress_sink,
        )
        await _emit_progress(
            progress_sink,
            {
                "event": "block_start",
                "run_id": run_id,
                "block_index": block.block_index,
                "method": block.method,
                "normalized_offered_load": block.normalized_offered_load,
                "graph_namespace": block.graph_namespace,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                ),
            },
        )
        try:
            runtime = await runtime_factory(block)
            if any(runtime is previous for previous in retained_runtimes):
                raise _fail("u0_lifecycle_reused")
            retained_runtimes.append(runtime)
            counts = await runtime.namespace_counts()
            if (
                not isinstance(counts, NamespaceCounts)
                or _nonnegative_int(counts.nodes, "namespace_counts_invalid") != 0
                or _nonnegative_int(
                    counts.relationships, "namespace_counts_invalid"
                )
                != 0
            ):
                raise NamespacePreflightError("namespace_not_empty")
            await _emit_progress(
                progress_sink,
                {
                    "event": "namespace_preflight",
                    "run_id": run_id,
                    "block_index": block.block_index,
                    "graph_namespace": block.graph_namespace,
                    "node_count": counts.nodes,
                    "relationship_count": counts.relationships,
                },
            )

            async def captured_service(
                episode: c4.Episode, service_start_ns: int
            ) -> None:
                try:
                    await runtime.service(episode, service_start_ns)
                except Exception as error:
                    failure_context.error = error
                    failure_context.token_envelope = _extract_token_envelope(error)
                    raise

            scheduling_origin_ns = _nonnegative_int(
                clock.now_ns(), "block_start_timestamp_invalid"
            ) + start_lead
            arrivals = [
                scheduling_origin_ns + offset
                for offset in block.absolute_arrival_offsets_ns
            ]
            result = await replay(
                block.method,
                retained_episodes,
                arrivals,
                clock,
                captured_service,
                writer,
            )
        except Exception as error:
            await writer.record_external_failure(
                min(len(writer.completed_source_sequences), FROZEN_EPISODE_COUNT - 1),
                error,
            )
            await _close_after_failure(runtime)
            return {
                "status": c4_artifacts.FAILURE_STATUS,
                "run_id": run_id,
                "failed_block_index": block.block_index,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                    + len(writer.completed_source_sequences)
                ),
            }

        if result.get("status") != "complete":
            if not writer.failure_recorded:
                await writer.record_external_failure(
                    int(
                        (result.get("failure_checkpoint") or {}).get(
                            "failed_source_sequence", 0
                        )
                    ),
                    NativeCharacterizationC4RunnerError("replay_failed_without_checkpoint"),
                )
            await _close_after_failure(runtime)
            return {
                "status": c4_artifacts.FAILURE_STATUS,
                "run_id": run_id,
                "failed_block_index": block.block_index,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                    + len(writer.completed_source_sequences)
                ),
            }

        try:
            await runtime.close()
        except Exception as error:
            await writer.record_external_failure(FROZEN_EPISODE_COUNT - 1, error)
            return {
                "status": c4_artifacts.FAILURE_STATUS,
                "run_id": run_id,
                "failed_block_index": block.block_index,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                    + len(writer.completed_source_sequences)
                ),
            }

        if writer.completed_source_sequences != list(range(FROZEN_EPISODE_COUNT)):
            error = NativeCharacterizationC4RunnerError(
                "block_publication_sequence_incomplete"
            )
            await writer.record_external_failure(
                len(writer.completed_source_sequences), error
            )
            return {
                "status": c4_artifacts.FAILURE_STATUS,
                "run_id": run_id,
                "failed_block_index": block.block_index,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                    + len(writer.completed_source_sequences)
                ),
            }

        await asyncio.to_thread(
            store.write_block_checkpoint,
            block_index=block.block_index,
            status="completed",
            progress={
                "completed_source_sequences": list(range(FROZEN_EPISODE_COUNT)),
                "completed_episode_count": FROZEN_EPISODE_COUNT,
                "graph_namespace": block.graph_namespace,
                "history_id": FROZEN_HISTORY_ID,
                "method": block.method,
                "normalized_offered_load": block.normalized_offered_load,
            },
        )
        completed_blocks.append(block.block_index)
        block_summaries.append(
            {
                "block_index": block.block_index,
                "method": block.method,
                "normalized_offered_load": block.normalized_offered_load,
                "graph_namespace": block.graph_namespace,
                "history_id": FROZEN_HISTORY_ID,
                "status": "complete",
                "episode_count": FROZEN_EPISODE_COUNT,
                "completed_episode_count": FROZEN_EPISODE_COUNT,
                "episode_metrics": result.get("episode_metrics"),
                "aggregate": result.get("aggregate"),
            }
        )
        await _emit_progress(
            progress_sink,
            {
                "event": "block_complete",
                "run_id": run_id,
                "block_index": block.block_index,
                "method": block.method,
                "normalized_offered_load": block.normalized_offered_load,
                "graph_namespace": block.graph_namespace,
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                ),
                "aggregate": result.get("aggregate"),
            },
        )
        await asyncio.to_thread(
            store.write_root_checkpoint,
            status="running",
            progress={
                "completed_block_indices": list(completed_blocks),
                "completed_block_count": len(completed_blocks),
                "completed_episode_count": (
                    len(completed_blocks) * FROZEN_EPISODE_COUNT
                ),
            },
        )

    finalization_inputs = [
        {
            "block_index": block["block_index"],
            "graph_namespace": block["graph_namespace"],
            "history_id": block["history_id"],
            "method": block["method"],
            "normalized_offered_load": block["normalized_offered_load"],
        }
        for block in block_summaries
    ]
    try:
        await asyncio.to_thread(store.finalize_success, finalization_inputs)
    except Exception as error:
        await asyncio.to_thread(
            store.record_stage_failure,
            failure_stage="finalization",
            error=error,
            completed_block_indices=list(completed_blocks),
            completed_episode_count=FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT,
            token_envelope=_extract_token_envelope(error),
        )
        await _emit_progress(
            progress_sink,
            {
                "event": "terminal_failure",
                "failure_stage": "finalization",
                "run_id": run_id,
                "completed_block_count": FROZEN_BLOCK_COUNT,
                "completed_episode_count": (
                    FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT
                ),
                "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                "token_envelope": _extract_token_envelope(error),
            },
        )
        return {
            "status": c4_artifacts.FAILURE_STATUS,
            "failure_stage": "finalization",
            "run_id": run_id,
            "completed_block_count": FROZEN_BLOCK_COUNT,
            "completed_episode_count": FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT,
        }

    if post_finalize_verifier is not None:
        try:
            verification = await asyncio.to_thread(post_finalize_verifier, store)
            if (
                not isinstance(verification, Mapping)
                or verification.get("status") != "verified"
                or verification.get("attempt_status") != "complete"
            ):
                raise NativeCharacterizationC4RunnerError(
                    "post_finalize_verification_invalid"
                )
        except Exception as error:
            await asyncio.to_thread(
                store.record_stage_failure,
                failure_stage="verification",
                error=error,
                completed_block_indices=list(completed_blocks),
                completed_episode_count=(
                    FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT
                ),
                token_envelope=_extract_token_envelope(error),
            )
            await _emit_progress(
                progress_sink,
                {
                    "event": "terminal_failure",
                    "failure_stage": "verification",
                    "run_id": run_id,
                    "completed_block_count": FROZEN_BLOCK_COUNT,
                    "completed_episode_count": (
                        FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT
                    ),
                    "error_class": (
                        f"{type(error).__module__}.{type(error).__qualname__}"
                    ),
                    "token_envelope": _extract_token_envelope(error),
                },
            )
            return {
                "status": c4_artifacts.FAILURE_STATUS,
                "failure_stage": "verification",
                "run_id": run_id,
                "completed_block_count": FROZEN_BLOCK_COUNT,
                "completed_episode_count": (
                    FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT
                ),
            }
    await _emit_progress(
        progress_sink,
        {
            "event": "terminal_success",
            "run_id": run_id,
            "completed_block_count": FROZEN_BLOCK_COUNT,
            "completed_episode_count": FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT,
        },
    )
    return {
        "status": "complete",
        "run_id": run_id,
        "completed_block_count": len(completed_blocks),
        "completed_episode_count": FROZEN_BLOCK_COUNT * FROZEN_EPISODE_COUNT,
        "blocks": block_summaries,
    }


__all__ = [
    "C4Block",
    "C4BlockRuntime",
    "FROZEN_BLOCK_COUNT",
    "FROZEN_EPISODE_COUNT",
    "START_LEAD_NS",
    "NamespaceCounts",
    "NamespacePreflightError",
    "NativeCharacterizationC4RunnerError",
    "new_c4_run_id",
    "run_c4_live",
]
