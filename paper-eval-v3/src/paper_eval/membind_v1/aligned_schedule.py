"""Fresh open-loop scheduling for aligned U0/A0/P(C=2) benchmark rows.

This is intentionally a small, Graphiti-free bridge.  It takes an immutable
source inventory plus the already-frozen per-history arrival offsets, and
calls one opaque whole-update callback.  The live composition owns Graphiti,
durability, telemetry, and admission; this module owns only fair arrival
semantics and bounded U0/P(C=2) scheduling.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any


U0_ALIGNED = "U0-aligned"
A0_ALIGNED = "A0-aligned"
P_C2_ALIGNED = "P(C=2)-aligned"
P_C4_ALIGNED = "P(C=4)-aligned"
P_C8_ALIGNED = "P(C=8)-aligned"
_METHODS = {U0_ALIGNED, A0_ALIGNED, P_C2_ALIGNED, P_C4_ALIGNED, P_C8_ALIGNED}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AlignedScheduleError(ValueError):
    """A fresh aligned scheduler input or execution boundary is invalid."""


def _fail(code: str) -> AlignedScheduleError:
    return AlignedScheduleError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class AlignedEpisodeRef:
    """Public source identity plus an opaque episode passed only to Graphiti."""

    source_sequence: int
    source_sha256: str
    native_episode: object

    def __post_init__(self) -> None:
        _nonnegative_int(self.source_sequence, "source sequence invalid")
        if not isinstance(self.source_sha256, str) or _SHA256.fullmatch(self.source_sha256) is None:
            raise _fail("source identity invalid")
        if self.native_episode is None:
            raise _fail("native episode missing")


NativeAddEpisode = Callable[[object], Awaitable[object]]
ClockNs = Callable[[], int]
Sleep = Callable[[float], Awaitable[object]]
LifecycleObserver = Callable[[str, int, int], Awaitable[object]]


def _validate(
    *,
    method: object,
    episodes: Sequence[AlignedEpisodeRef],
    arrival_offsets_ns: Sequence[int],
    native_add_episode: object,
    clock_ns: object,
    sleep: object,
    lifecycle_observer: object,
) -> tuple[tuple[AlignedEpisodeRef, ...], tuple[int, ...]]:
    if method not in _METHODS:
        raise _fail("method invalid")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("episodes invalid")
    selected = tuple(episodes)
    if not selected or any(not isinstance(item, AlignedEpisodeRef) for item in selected):
        raise _fail("episodes invalid")
    if [item.source_sequence for item in selected] != list(range(len(selected))):
        raise _fail("source sequence invalid")
    if isinstance(arrival_offsets_ns, (str, bytes)) or not isinstance(arrival_offsets_ns, Sequence):
        raise _fail("arrival offsets invalid")
    offsets = tuple(_nonnegative_int(item, "arrival offsets invalid") for item in arrival_offsets_ns)
    if len(offsets) != len(selected):
        raise _fail("arrival offset count invalid")
    if any(right < left for left, right in zip(offsets, offsets[1:])):
        raise _fail("arrival offsets invalid")
    if not callable(native_add_episode):
        raise _fail("native callback invalid")
    if not callable(clock_ns) or not callable(sleep):
        raise _fail("clock or sleep invalid")
    if lifecycle_observer is not None and not callable(lifecycle_observer):
        raise _fail("lifecycle observer invalid")
    return selected, offsets


async def _wait_until(target_ns: int, *, clock_ns: ClockNs, sleep: Sleep) -> None:
    now = _nonnegative_int(clock_ns(), "clock invalid")
    remaining_ns = target_ns - now
    if remaining_ns <= 0:
        return
    pending = sleep(remaining_ns / 1_000_000_000)
    if not inspect.isawaitable(pending):
        raise _fail("sleep invalid")
    await pending


def _row(
    *,
    episode: AlignedEpisodeRef,
    arrival_ns: int,
    enqueue_ns: int,
    service_start_ns: int,
    publication_ns: int,
    worker_id: int,
    caller_return_ns: int | None = None,
) -> dict[str, int | str]:
    if not arrival_ns <= enqueue_ns <= service_start_ns <= publication_ns:
        raise _fail("lifecycle timestamp order invalid")
    if caller_return_ns is None:
        caller_return_ns = publication_ns
    if not arrival_ns <= caller_return_ns <= publication_ns:
        raise _fail("caller return timestamp order invalid")
    return {
        "source_sequence": episode.source_sequence,
        "source_sha256": episode.source_sha256,
        "arrival_timestamp_ns": arrival_ns,
        "enqueue_timestamp_ns": enqueue_ns,
        "service_start_timestamp_ns": service_start_ns,
        "publication_timestamp_ns": publication_ns,
        "terminal_timestamp_ns": publication_ns,
        "caller_return_timestamp_ns": caller_return_ns,
        "worker_id": worker_id,
    }


async def _call_native(callback: NativeAddEpisode, episode: AlignedEpisodeRef) -> None:
    value = callback(episode.native_episode)
    if not inspect.isawaitable(value):
        raise _fail("native callback must be async")
    await value


async def _observe(
    observer: LifecycleObserver | None,
    event_type: str,
    source_sequence: int,
    timestamp_ns: int,
) -> None:
    if observer is None:
        return
    value = observer(event_type, source_sequence, timestamp_ns)
    if not inspect.isawaitable(value):
        raise _fail("lifecycle observer must be async")
    await value


async def _run_u0(
    *,
    episodes: tuple[AlignedEpisodeRef, ...],
    targets: tuple[int, ...],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs,
    sleep: Sleep,
    lifecycle_observer: LifecycleObserver | None,
) -> dict[str, Any]:
    rows: list[dict[str, int | str]] = []
    for episode, arrival_ns in zip(episodes, targets, strict=True):
        await _wait_until(arrival_ns, clock_ns=clock_ns, sleep=sleep)
        await _observe(lifecycle_observer, "ARRIVAL", episode.source_sequence, arrival_ns)
        enqueue_ns = _nonnegative_int(clock_ns(), "clock invalid")
        await _observe(lifecycle_observer, "ENQUEUED", episode.source_sequence, enqueue_ns)
        service_start_ns = _nonnegative_int(clock_ns(), "clock invalid")
        await _observe(
            lifecycle_observer, "SERVICE_STARTED", episode.source_sequence, service_start_ns
        )
        await _call_native(native_add_episode, episode)
        publication_ns = _nonnegative_int(clock_ns(), "clock invalid")
        await _observe(
            lifecycle_observer,
            "PUBLICATION_DURABLE",
            episode.source_sequence,
            publication_ns,
        )
        rows.append(
            _row(
                episode=episode,
                arrival_ns=arrival_ns,
                enqueue_ns=enqueue_ns,
                service_start_ns=service_start_ns,
                publication_ns=publication_ns,
                worker_id=0,
            )
        )
    return {
        "configured_worker_count": 1,
        "observed_max_active_updates": 1,
        "whole_update_interval_overlap_observed": False,
        "lifecycle": rows,
    }


async def _run_parallel(
    *,
    method: str,
    episodes: tuple[AlignedEpisodeRef, ...],
    targets: tuple[int, ...],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs,
    sleep: Sleep,
    lifecycle_observer: LifecycleObserver | None,
) -> dict[str, Any]:
    concurrency = {P_C2_ALIGNED: 2, P_C4_ALIGNED: 4, P_C8_ALIGNED: 8}[method]
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    workers: asyncio.Queue[int] = asyncio.Queue(maxsize=concurrency)
    for worker_id in range(concurrency):
        workers.put_nowait(worker_id)
    rows: list[dict[str, int | str]] = []
    intervals: list[tuple[int, int, int]] = []
    active = 0
    observed_max = 0

    async def one(episode: AlignedEpisodeRef, arrival_ns: int) -> None:
        nonlocal active, observed_max
        await _wait_until(arrival_ns, clock_ns=clock_ns, sleep=sleep)
        await _observe(lifecycle_observer, "ARRIVAL", episode.source_sequence, arrival_ns)
        enqueue_ns = _nonnegative_int(clock_ns(), "clock invalid")
        await _observe(lifecycle_observer, "ENQUEUED", episode.source_sequence, enqueue_ns)
        async with semaphore:
            service_start_ns = _nonnegative_int(clock_ns(), "clock invalid")
            await _observe(
                lifecycle_observer, "SERVICE_STARTED", episode.source_sequence, service_start_ns
            )
            worker_id = await workers.get()
            async with lock:
                active += 1
                observed_max = max(observed_max, active)
            try:
                await _call_native(native_add_episode, episode)
                publication_ns = _nonnegative_int(clock_ns(), "clock invalid")
                await _observe(
                    lifecycle_observer,
                    "PUBLICATION_DURABLE",
                    episode.source_sequence,
                    publication_ns,
                )
            finally:
                async with lock:
                    active -= 1
                workers.put_nowait(worker_id)
            row = _row(
                episode=episode,
                arrival_ns=arrival_ns,
                enqueue_ns=enqueue_ns,
                service_start_ns=service_start_ns,
                publication_ns=publication_ns,
                worker_id=worker_id,
            )
            async with lock:
                rows.append(row)
                intervals.append((service_start_ns, publication_ns, worker_id))

    tasks = [
        asyncio.create_task(one(episode, arrival_ns))
        for episode, arrival_ns in zip(episodes, targets, strict=True)
    ]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # ``asyncio.gather`` propagates the first task failure but leaves its
        # siblings alive.  They may otherwise keep calling the live Graphiti
        # callback and appending lifecycle telemetry after a failed block has
        # already returned to its owner.  Finish cancellation before exposing
        # the original failure to make a failed aligned block quiescent.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    overlap = any(
        left_worker != right_worker
        and left_start < right_end
        and right_start < left_end
        for index, (left_start, left_end, left_worker) in enumerate(intervals)
        for right_start, right_end, right_worker in intervals[index + 1 :]
    )
    return {
        "configured_worker_count": concurrency,
        "observed_max_active_updates": observed_max,
        "whole_update_interval_overlap_observed": overlap,
        "lifecycle": sorted(rows, key=lambda row: int(row["source_sequence"])),
    }


async def _run_a0(
    *,
    episodes: tuple[AlignedEpisodeRef, ...],
    targets: tuple[int, ...],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs,
    sleep: Sleep,
    lifecycle_observer: LifecycleObserver | None,
) -> dict[str, Any]:
    """Open-loop durable admission feeding exactly one FIFO Native worker."""

    queue: asyncio.Queue[tuple[AlignedEpisodeRef, int, int]] = asyncio.Queue()
    rows: list[dict[str, int | str]] = []
    producer_failure: BaseException | None = None

    async def arrive(episode: AlignedEpisodeRef, arrival_ns: int) -> None:
        await _wait_until(arrival_ns, clock_ns=clock_ns, sleep=sleep)
        await _observe(lifecycle_observer, "ARRIVAL", episode.source_sequence, arrival_ns)
        enqueue_ns = _nonnegative_int(clock_ns(), "clock invalid")
        await _observe(lifecycle_observer, "ENQUEUED", episode.source_sequence, enqueue_ns)
        await queue.put((episode, arrival_ns, enqueue_ns))

    producers = [
        asyncio.create_task(arrive(episode, arrival_ns))
        for episode, arrival_ns in zip(episodes, targets, strict=True)
    ]

    async def worker() -> None:
        for _ in episodes:
            episode, arrival_ns, enqueue_ns = await queue.get()
            service_start_ns = _nonnegative_int(clock_ns(), "clock invalid")
            await _observe(
                lifecycle_observer,
                "SERVICE_STARTED",
                episode.source_sequence,
                service_start_ns,
            )
            await _call_native(native_add_episode, episode)
            publication_ns = _nonnegative_int(clock_ns(), "clock invalid")
            await _observe(
                lifecycle_observer,
                "PUBLICATION_DURABLE",
                episode.source_sequence,
                publication_ns,
            )
            rows.append(
                _row(
                    episode=episode,
                    arrival_ns=arrival_ns,
                    enqueue_ns=enqueue_ns,
                    service_start_ns=service_start_ns,
                    publication_ns=publication_ns,
                    caller_return_ns=enqueue_ns,
                    worker_id=0,
                )
            )
            queue.task_done()

    worker_task = asyncio.create_task(worker())
    try:
        producer_results = await asyncio.gather(*producers, return_exceptions=True)
        producer_failure = next(
            (value for value in producer_results if isinstance(value, BaseException)),
            None,
        )
        if producer_failure is not None:
            raise producer_failure
        await worker_task
    except BaseException:
        for task in (*producers, worker_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(*producers, worker_task, return_exceptions=True)
        raise
    return {
        "configured_worker_count": 1,
        "observed_max_active_updates": 1 if rows else 0,
        "whole_update_interval_overlap_observed": False,
        "lifecycle": sorted(rows, key=lambda row: int(row["source_sequence"])),
    }


async def run_aligned_baseline(
    *,
    method: str,
    episodes: Sequence[AlignedEpisodeRef],
    arrival_offsets_ns: Sequence[int],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs = time.monotonic_ns,
    sleep: Sleep = asyncio.sleep,
    lifecycle_observer: LifecycleObserver | None = None,
) -> dict[str, Any]:
    """Run one fresh U0, Async-Serial, or naive whole-update P(C=2) row.

    Arrival timestamps are the frozen logical targets, not the moment a worker
    eventually gets CPU time.  This permits queue delay to be measured fairly
    even when a serial or two-worker scheduler is backlogged.
    """

    selected, offsets = _validate(
        method=method,
        episodes=episodes,
        arrival_offsets_ns=arrival_offsets_ns,
        native_add_episode=native_add_episode,
        clock_ns=clock_ns,
        sleep=sleep,
        lifecycle_observer=lifecycle_observer,
    )
    start_ns = _nonnegative_int(clock_ns(), "clock invalid")
    targets = tuple(start_ns + offset for offset in offsets)
    if method == U0_ALIGNED:
        result = await _run_u0(
            episodes=selected,
            targets=targets,
            native_add_episode=native_add_episode,
            clock_ns=clock_ns,
            sleep=sleep,
            lifecycle_observer=lifecycle_observer,
        )
    elif method == A0_ALIGNED:
        result = await _run_a0(
            episodes=selected,
            targets=targets,
            native_add_episode=native_add_episode,
            clock_ns=clock_ns,
            sleep=sleep,
            lifecycle_observer=lifecycle_observer,
        )
    else:
        result = await _run_parallel(
            method=method,
            episodes=selected,
            targets=targets,
            native_add_episode=native_add_episode,
            clock_ns=clock_ns,
            sleep=sleep,
            lifecycle_observer=lifecycle_observer,
        )
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-aligned-schedule.v1",
        "method": method,
        "arrival_offsets_ns": list(offsets),
        "run_start_timestamp_ns": start_ns,
        **result,
    }


__all__ = [
    "A0_ALIGNED",
    "AlignedEpisodeRef",
    "AlignedScheduleError",
    "P_C2_ALIGNED",
    "P_C4_ALIGNED",
    "P_C8_ALIGNED",
    "U0_ALIGNED",
    "run_aligned_baseline",
]
