"""Bounded source-ordered execution for node-only MemBind-v1.

This runner deliberately implements only the first frozen scheduling shape:
one compiler, one binder, and at most one prepared source beyond the current
publication frontier.  It is not a general DAG scheduler.  The runner assumes
the installed LLM transport is already wrapped by the shared request-level
admission boundary; its own role is source order, durable checkpoints, and
strict lifecycle accounting.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from paper_eval.membind_v1.admission import RuntimeBounds
from paper_eval.membind_v1.delta import PreparedNodeArtifact
from paper_eval.membind_v1.evidence_fence import CompileInput
from paper_eval.membind_v1.store import MemBindV1AttemptStore, MemBindV1StoreError


class MemBindV1RunnerError(ValueError):
    """A bounded runtime input or source-order invariant is invalid."""


def _fail(code: str) -> MemBindV1RunnerError:
    return MemBindV1RunnerError(code)


class NodeOnlyAdapter(Protocol):
    async def prepare(self, compile_input: CompileInput) -> PreparedNodeArtifact: ...

    async def bind(
        self,
        compile_input: CompileInput,
        artifact: PreparedNodeArtifact,
        *,
        logical_time_ns: int,
    ) -> object: ...


ClockNs = Callable[[], int]
Sleep = Callable[[float], Awaitable[object]]
LifecycleObserver = Callable[[str, int, int], Awaitable[object]]


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _validate_inputs(
    *,
    compile_inputs: Sequence[CompileInput],
    logical_time_ns: Sequence[int] | None,
    arrival_time_ns: Sequence[int],
    adapter: object,
    store: object,
    bounds: RuntimeBounds,
    lifecycle_observer: object,
) -> tuple[CompileInput, ...]:
    if isinstance(compile_inputs, (str, bytes)) or not isinstance(compile_inputs, Sequence):
        raise _fail("compile_inputs_invalid")
    selected = tuple(compile_inputs)
    if not selected or any(not isinstance(item, CompileInput) for item in selected):
        raise _fail("compile_inputs_invalid")
    if [item.source.source_sequence for item in selected] != list(range(len(selected))):
        raise _fail("source_sequence_not_contiguous")
    if (
        logical_time_ns is not None
        and (
            isinstance(logical_time_ns, (str, bytes))
            or not isinstance(logical_time_ns, Sequence)
            or len(logical_time_ns) != len(selected)
        )
    ) or len(arrival_time_ns) != len(selected):
        raise _fail("schedule_length_invalid")
    logical = (
        None
        if logical_time_ns is None
        else [_nonnegative_int(value, "logical_time_invalid") for value in logical_time_ns]
    )
    arrivals = [_nonnegative_int(value, "arrival_time_invalid") for value in arrival_time_ns]
    if any(right < left for left, right in zip(arrivals, arrivals[1:])):
        raise _fail("arrival_time_not_monotonic")
    if not callable(getattr(adapter, "prepare", None)) or not callable(getattr(adapter, "bind", None)):
        raise _fail("adapter_invalid")
    if not isinstance(store, MemBindV1AttemptStore):
        raise _fail("store_invalid")
    if not isinstance(bounds, RuntimeBounds):
        raise _fail("runtime_bounds_invalid")
    if bounds.compile_concurrency != 1 or bounds.prepared_lookahead != 1 or bounds.llm_request_limit != 2:
        raise _fail("first_v1_bounds_must_be_c1_w1_k2")
    if store.source_count != len(selected):
        raise _fail("store_source_count_mismatch")
    if lifecycle_observer is not None and not callable(lifecycle_observer):
        raise _fail("lifecycle_observer_invalid")
    expected_hashes = list(store.manifest["source_sha256s"])
    if [item.source.source_sha256 for item in selected] != expected_hashes:
        raise _fail("store_source_identity_mismatch")
    # Keep local validated copies to make the no-mutation checks obvious to
    # reviewers and linters, even though the immutable CompileInput owns data.
    _ = logical, arrivals
    return selected


async def _wait_for_arrival(
    target_ns: int,
    *,
    clock_ns: ClockNs,
    sleep: Sleep,
) -> None:
    now = _nonnegative_int(clock_ns(), "clock_invalid")
    remaining = target_ns - now
    if remaining > 0:
        result = sleep(remaining / 1_000_000_000)
        if not inspect.isawaitable(result):
            raise _fail("sleep_invalid")
        await result


async def _observe_aligned_lifecycle(
    observer: LifecycleObserver | None,
    event_type: str,
    source_sequence: int,
    timestamp_ns: int,
) -> None:
    """Publish a content-free, awaitable aligned-block lifecycle boundary."""

    if observer is None:
        return
    result = observer(event_type, source_sequence, timestamp_ns)
    if not inspect.isawaitable(result):
        raise _fail("lifecycle_observer_must_be_async")
    await result


async def run_membind_v1(
    *,
    compile_inputs: Sequence[CompileInput],
    logical_time_ns: Sequence[int] | None,
    arrival_time_ns: Sequence[int],
    adapter: NodeOnlyAdapter,
    store: MemBindV1AttemptStore,
    bounds: RuntimeBounds | None = None,
    clock_ns: ClockNs = time.monotonic_ns,
    logical_clock_ns: ClockNs = time.time_ns,
    sleep: Sleep = asyncio.sleep,
    lifecycle_observer: LifecycleObserver | None = None,
) -> dict[str, Any]:
    """Run only C=1/W=1 MemBind-v1 and durably acknowledge each publication.

    Each source becomes durable at intent, prepare-start, prepared, bind-start,
    commit-returned, and publication boundaries.  A later source may prepare
    while the frontier source is binding, but bind itself always waits for the
    durable source-ordered frontier.  Exceptions deliberately leave the last
    durable state intact for the store's fail-closed recovery classifier.
    """

    selected_bounds = RuntimeBounds.conservative_defaults() if bounds is None else bounds
    selected = _validate_inputs(
        compile_inputs=compile_inputs,
        logical_time_ns=logical_time_ns,
        arrival_time_ns=arrival_time_ns,
        adapter=adapter,
        store=store,
        bounds=selected_bounds,
        lifecycle_observer=lifecycle_observer,
    )
    if not callable(clock_ns) or not callable(logical_clock_ns) or not callable(sleep):
        raise _fail("runtime_clock_or_sleep_invalid")

    lifecycle: list[dict[str, Any]] = []

    def observe(event_type: str, source_sequence: int) -> None:
        lifecycle.append(
            {
                "event_sequence": len(lifecycle),
                "event_type": event_type,
                "source_sequence": source_sequence,
                "timestamp_ns": _nonnegative_int(clock_ns(), "clock_invalid"),
            }
        )

    async def prepare(sequence: int) -> PreparedNodeArtifact:
        await _wait_for_arrival(
            int(arrival_time_ns[sequence]), clock_ns=clock_ns, sleep=sleep
        )
        await _observe_aligned_lifecycle(
            lifecycle_observer,
            "ARRIVAL",
            sequence,
            int(arrival_time_ns[sequence]),
        )
        store.record_intent(sequence)
        observe("intent_durable", sequence)
        await _observe_aligned_lifecycle(
            lifecycle_observer,
            "ENQUEUED",
            sequence,
            _nonnegative_int(clock_ns(), "clock_invalid"),
        )
        store.record_prepare_started(sequence)
        observe("prepare_started", sequence)
        artifact = await adapter.prepare(selected[sequence])
        if not isinstance(artifact, PreparedNodeArtifact):
            raise _fail("prepare_result_invalid")
        store.persist_prepared(artifact)
        observe("prepared_durable", sequence)
        return artifact

    def restored_prepared(sequence: int) -> PreparedNodeArtifact | None:
        """Load a store-verified artifact without replaying semantic prepare.

        ``open_existing`` has already inspected the manifest, lifecycle log,
        and artifact hashes.  A missing artifact means this source belongs to
        the suffix that has never reached its durable prepare boundary; every
        other store error remains fail-closed rather than being mistaken for a
        fresh source.
        """

        try:
            artifact = store.prepared_artifact(sequence)
        except MemBindV1StoreError as error:
            if str(error) == "prepared_artifact_missing":
                return None
            raise
        source = selected[sequence].source
        if (
            artifact.source_sequence != sequence
            or artifact.source_sha256 != source.source_sha256
            or artifact.evidence_prefix_sha256
            != selected[sequence].evidence.evidence_prefix_sha256
            or artifact.episode_projection_sha256
            != source.episode_projection_sha256
        ):
            raise _fail("stored_prepared_artifact_identity_mismatch")
        return artifact

    async def bind(sequence: int, artifact: PreparedNodeArtifact) -> object:
        store.record_bind_started(sequence)
        observe("bind_started", sequence)
        await _observe_aligned_lifecycle(
            lifecycle_observer,
            "SERVICE_STARTED",
            sequence,
            _nonnegative_int(clock_ns(), "clock_invalid"),
        )
        logical_time = (
            _nonnegative_int(logical_clock_ns(), "logical_time_invalid")
            if logical_time_ns is None
            else int(logical_time_ns[sequence])
        )
        result = await adapter.bind(
            selected[sequence], artifact, logical_time_ns=logical_time
        )
        store.record_commit_returned(sequence)
        observe("commit_returned", sequence)
        store.record_publication_durable(sequence)
        observe("publication_durable", sequence)
        await _observe_aligned_lifecycle(
            lifecycle_observer,
            "PUBLICATION_DURABLE",
            sequence,
            _nonnegative_int(clock_ns(), "clock_invalid"),
        )
        return result

    first_unpublished = store.published_frontier + 1
    if first_unpublished < 0 or first_unpublished >= len(selected):
        raise _fail("store_publication_frontier_invalid")

    resumed_prepared_source_count = 0

    async def prepare_or_restore(sequence: int) -> PreparedNodeArtifact:
        nonlocal resumed_prepared_source_count
        artifact = restored_prepared(sequence)
        if artifact is not None:
            resumed_prepared_source_count += 1
            observe("prepared_recovered", sequence)
            return artifact
        return await prepare(sequence)

    current = await prepare_or_restore(first_unpublished)
    results: list[object] = []
    for sequence in range(first_unpublished, len(selected)):
        bind_task = asyncio.create_task(bind(sequence, current))
        next_task: asyncio.Task[PreparedNodeArtifact] | None = None
        next_artifact: PreparedNodeArtifact | None = None
        if sequence + 1 < len(selected):
            next_artifact = restored_prepared(sequence + 1)
            if next_artifact is not None:
                resumed_prepared_source_count += 1
                observe("prepared_recovered", sequence + 1)
            else:
                next_task = asyncio.create_task(prepare(sequence + 1))
        if next_task is None:
            results.append(await bind_task)
            if next_artifact is not None:
                current = next_artifact
            continue
        bind_result, next_artifact = await asyncio.gather(bind_task, next_task)
        results.append(bind_result)
        current = next_artifact

    store.complete()
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-runner-result.v1",
        "status": "PASS",
        "source_count": len(selected),
        "published_frontier": store.published_frontier,
        "resumed_from_published_frontier": first_unpublished - 1,
        "resumed_prepared_source_count": resumed_prepared_source_count,
        "observed_bounds": {
            "compile_concurrency": selected_bounds.compile_concurrency,
            "prepared_lookahead": selected_bounds.prepared_lookahead,
            "llm_request_limit": selected_bounds.llm_request_limit,
        },
        "lifecycle": lifecycle,
        "bind_result_count": len(results),
    }


__all__ = ["MemBindV1RunnerError", "run_membind_v1"]
