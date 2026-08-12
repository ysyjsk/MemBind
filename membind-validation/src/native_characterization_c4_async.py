"""Pure async producer/worker adapter for frozen C4/E3 characterization.

The adapter models the real execution boundary missing from the deterministic
event core: an absolute-time producer keeps admitting episodes while one FIFO
worker awaits the native service.  Every clock, service, and durability effect
is injected, so this module performs no live I/O by itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import native_characterization_c4 as c4


Episode = c4.Episode
NATIVE_SYNC = c4.NATIVE_SYNC
NATIVE_ASYNC_SERIAL = c4.NATIVE_ASYNC_SERIAL
NativeCharacterizationC4Error = c4.NativeCharacterizationC4Error


class AsyncClock(Protocol):
    """Monotonic clock with an awaitable absolute-deadline sleep."""

    def now_ns(self) -> int: ...

    async def sleep_until_ns(self, timestamp_ns: int) -> None: ...


class AsyncService(Protocol):
    """One native construction call on the shared serial service path."""

    async def __call__(self, episode: Episode, service_start_ns: int) -> None: ...


class AsyncDurableWriter(Protocol):
    """Injected append/fsync and evidence persistence boundaries."""

    async def persist_enqueue(
        self, episode: Episode, actual_arrival_timestamp_ns: int
    ) -> None: ...

    async def persist_publication(self, record: dict[str, object]) -> None: ...

    async def persist_failure(self, checkpoint: dict[str, object]) -> None: ...


@dataclass
class _QueuedEpisode:
    episode: Episode
    planned_arrival_timestamp_ns: int
    actual_arrival_timestamp_ns: int
    schedule_lag_ns: int
    enqueue_ack_timestamp_ns: int
    caller_future: asyncio.Future[int]


@dataclass(frozen=True)
class _Failure:
    stage: str
    episode: Episode
    timestamp_ns: int
    error: Exception


_STOP = object()


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeCharacterizationC4Error(f"{field} must be an integer nanosecond timestamp")
    return value


def _validate_inputs(
    method: str,
    episodes: Sequence[Episode],
    planned_arrival_timestamps_ns: Sequence[int],
    clock: AsyncClock,
) -> list[int]:
    if method not in c4.METHODS:
        raise NativeCharacterizationC4Error(f"unsupported C4 method: {method}")
    if not episodes:
        raise NativeCharacterizationC4Error("a replay requires at least one episode")
    if len(episodes) != len(planned_arrival_timestamps_ns):
        raise NativeCharacterizationC4Error("episode and arrival counts differ")
    sequences = [episode.source_sequence for episode in episodes]
    if len(sequences) != len(set(sequences)):
        raise NativeCharacterizationC4Error("source_sequence values must be unique")
    planned = [
        _timestamp(value, f"planned_arrival_timestamps_ns[{index}]")
        for index, value in enumerate(planned_arrival_timestamps_ns)
    ]
    if any(current < previous for previous, current in zip(planned, planned[1:])):
        raise NativeCharacterizationC4Error("absolute arrivals must be non-decreasing")
    if planned[0] < _timestamp(clock.now_ns(), "clock.now_ns()"):
        raise NativeCharacterizationC4Error("first arrival precedes the injected clock")
    return planned


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _success_aggregate(
    records: Sequence[dict[str, object]],
    episode_metrics: Sequence[dict[str, int]],
) -> dict[str, object]:
    actual_arrivals = [int(record["actual_arrival_timestamp_ns"]) for record in records]
    backlog = c4.analyze_backlog(actual_arrivals, records)
    first_arrival = actual_arrivals[0]
    final_arrival = actual_arrivals[-1]
    final_publish = max(int(record["publish_timestamp_ns"]) for record in records)
    makespan_ns = final_publish - first_arrival
    aggregate: dict[str, object] = {
        **backlog,
        "episode_count": len(records),
        "completed_episode_count": len(records),
        "first_arrival_timestamp_ns": first_arrival,
        "final_arrival_timestamp_ns": final_arrival,
        "final_publish_timestamp_ns": final_publish,
        "makespan_ns": makespan_ns,
        "drain_time_ns": max(0, final_publish - final_arrival),
        "throughput_episodes_per_second": (
            len(records) * 1_000_000_000 / makespan_ns if makespan_ns > 0 else None
        ),
        "mean_schedule_lag_ns": _mean(
            [int(record["schedule_lag_ns"]) for record in records]
        ),
        "error_count": 0,
        "checkpoint_loss_count": 0,
    }
    for name in (
        "caller_return_latency_ns",
        "construction_service_time_ns",
        "queue_wait_ns",
        "arrival_to_visible_ns",
        "signed_publish_after_return_ns",
        "post_return_stale_window_ns",
    ):
        aggregate[f"mean_{name}"] = _mean([metric[name] for metric in episode_metrics])
    return aggregate


def _failure_checkpoint(
    *,
    method: str,
    failure: _Failure,
    episodes: Sequence[Episode],
    admitted: Sequence[_QueuedEpisode],
    durably_enqueued: Sequence[int],
    records: Sequence[dict[str, object]],
) -> dict[str, object]:
    completed = [int(record["source_sequence"]) for record in records]
    completed_set = set(completed)
    admitted_sequences = [item.episode.source_sequence for item in admitted]
    admitted_set = set(admitted_sequences)
    return {
        "status": "failed",
        "method": method,
        "failure_stage": failure.stage,
        "failure_timestamp_ns": failure.timestamp_ns,
        "failed_source_sequence": failure.episode.source_sequence,
        "error_class": type(failure.error).__name__,
        "completed_source_sequences": completed,
        "admitted_source_sequences": admitted_sequences,
        "durably_enqueued_source_sequences": list(durably_enqueued),
        "pending_source_sequences": [
            sequence for sequence in admitted_sequences if sequence not in completed_set
        ],
        "not_yet_arrived_source_sequences": [
            episode.source_sequence
            for episode in episodes
            if episode.source_sequence not in admitted_set
        ],
    }


async def run_async_replay(
    method: str,
    episodes: Sequence[Episode],
    planned_arrival_timestamps_ns: Sequence[int],
    clock: AsyncClock,
    service: AsyncService,
    durable_writer: AsyncDurableWriter,
) -> dict[str, object]:
    """Run one treatment with an absolute-time producer and one FIFO worker.

    Native-Sync admission is in memory and its caller future completes only at
    publication.  Native-Async-Serial is admitted only after the injected
    append/fsync await completes; that acknowledgement completes its caller
    future.  The producer never awaits either construction service or a Sync
    caller future.
    """

    planned = _validate_inputs(method, episodes, planned_arrival_timestamps_ns, clock)
    arrival_queue: asyncio.Queue[_QueuedEpisode | object] = asyncio.Queue()
    service_queue: asyncio.Queue[_QueuedEpisode | object] = asyncio.Queue()
    admitted: list[_QueuedEpisode] = []
    durably_enqueued: list[int] = []
    records: list[dict[str, object]] = []

    async def produce() -> _Failure | None:
        for episode, planned_arrival in zip(episodes, planned):
            caller_future: asyncio.Future[int] | None = None
            try:
                await clock.sleep_until_ns(planned_arrival)
                actual_arrival = _timestamp(clock.now_ns(), "actual_arrival_timestamp_ns")
                if actual_arrival < planned_arrival:
                    raise NativeCharacterizationC4Error(
                        "injected clock woke before an absolute arrival"
                    )
                caller_future = asyncio.get_running_loop().create_future()
                queued = _QueuedEpisode(
                    episode=episode,
                    planned_arrival_timestamp_ns=planned_arrival,
                    actual_arrival_timestamp_ns=actual_arrival,
                    schedule_lag_ns=actual_arrival - planned_arrival,
                    enqueue_ack_timestamp_ns=actual_arrival,
                    caller_future=caller_future,
                )
                admitted.append(queued)

                await arrival_queue.put(queued)
            except asyncio.CancelledError:
                if caller_future is not None and not caller_future.done():
                    caller_future.cancel()
                raise
            except Exception as error:
                return _Failure("enqueue", episode, clock.now_ns(), error)
        await arrival_queue.put(_STOP)
        return None

    async def admit() -> _Failure | None:
        """Preserve FIFO while keeping fsync latency off the arrival clock."""

        while True:
            queued_or_stop = await arrival_queue.get()
            if queued_or_stop is _STOP:
                arrival_queue.task_done()
                await service_queue.put(_STOP)
                return None
            queued = queued_or_stop
            assert isinstance(queued, _QueuedEpisode)
            try:
                if method == NATIVE_ASYNC_SERIAL:
                    await durable_writer.persist_enqueue(
                        queued.episode,
                        queued.actual_arrival_timestamp_ns,
                    )
                    enqueue_ack = _timestamp(
                        clock.now_ns(), "enqueue_ack_timestamp_ns"
                    )
                    if enqueue_ack < queued.actual_arrival_timestamp_ns:
                        raise NativeCharacterizationC4Error(
                            "durable enqueue ack precedes actual arrival"
                        )
                    queued.enqueue_ack_timestamp_ns = enqueue_ack
                    durably_enqueued.append(queued.episode.source_sequence)
                    queued.caller_future.set_result(enqueue_ack)
                await service_queue.put(queued)
            except asyncio.CancelledError:
                arrival_queue.task_done()
                raise
            except Exception as error:
                arrival_queue.task_done()
                return _Failure("enqueue", queued.episode, clock.now_ns(), error)
            arrival_queue.task_done()

    async def work() -> _Failure | None:
        while True:
            queued_or_stop = await service_queue.get()
            if queued_or_stop is _STOP:
                service_queue.task_done()
                return None
            queued = queued_or_stop
            assert isinstance(queued, _QueuedEpisode)
            service_start = _timestamp(clock.now_ns(), "service_start_timestamp_ns")
            if service_start < queued.enqueue_ack_timestamp_ns:
                return _Failure(
                    "service",
                    queued.episode,
                    service_start,
                    NativeCharacterizationC4Error(
                        "service start precedes enqueue acknowledgement"
                    ),
                )
            try:
                await service(queued.episode, service_start)
                publish = _timestamp(clock.now_ns(), "publish_timestamp_ns")
                if publish < service_start:
                    raise NativeCharacterizationC4Error(
                        "publication precedes service start"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                service_queue.task_done()
                return _Failure("service", queued.episode, clock.now_ns(), error)

            if method == NATIVE_SYNC:
                queued.caller_future.set_result(publish)
            caller_return = _timestamp(
                queued.caller_future.result(), "caller_return_timestamp_ns"
            )
            record: dict[str, object] = {
                "method": method,
                "source_sequence": queued.episode.source_sequence,
                "planned_arrival_timestamp_ns": queued.planned_arrival_timestamp_ns,
                "actual_arrival_timestamp_ns": queued.actual_arrival_timestamp_ns,
                "schedule_lag_ns": queued.schedule_lag_ns,
                # Existing frozen metric helpers use arrival_timestamp_ns.  It
                # denotes measured admission, while planned arrival is retained
                # separately for open-loop lag analysis.
                "arrival_timestamp_ns": queued.actual_arrival_timestamp_ns,
                "enqueue_ack_timestamp_ns": queued.enqueue_ack_timestamp_ns,
                "service_start_timestamp_ns": service_start,
                "publish_timestamp_ns": publish,
                "caller_return_timestamp_ns": caller_return,
            }
            try:
                c4.compute_episode_metrics(record)
                await durable_writer.persist_publication(record)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                service_queue.task_done()
                return _Failure(
                    "publication-record", queued.episode, clock.now_ns(), error
                )
            records.append(record)
            service_queue.task_done()

    producer_task = asyncio.create_task(produce(), name="c4-absolute-arrival-producer")
    admission_task = asyncio.create_task(admit(), name="c4-fifo-durable-admission")
    worker_task = asyncio.create_task(work(), name="c4-fifo-single-worker")
    tasks = (producer_task, admission_task, worker_task)
    active: set[asyncio.Task[_Failure | None]] = set(tasks)
    failure: _Failure | None = None
    try:
        while active and failure is None:
            done, active = await asyncio.wait(
                active, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                outcome = task.result()
                if outcome is not None:
                    failure = outcome
                    break

        if failure is not None:
            for task in active:
                task.cancel()
            if active:
                await asyncio.gather(*active, return_exceptions=True)
            for queued in admitted:
                if not queued.caller_future.done():
                    queued.caller_future.cancel()
            checkpoint = _failure_checkpoint(
                method=method,
                failure=failure,
                episodes=episodes,
                admitted=admitted,
                durably_enqueued=durably_enqueued,
                records=records,
            )
            try:
                await durable_writer.persist_failure(checkpoint)
            except Exception as checkpoint_error:
                raise NativeCharacterizationC4Error(
                    "failed to persist the terminal C4 failure checkpoint"
                ) from checkpoint_error

            metrics = [c4.compute_episode_metrics(record) for record in records]
            actual_arrivals = [item.actual_arrival_timestamp_ns for item in admitted]
            if actual_arrivals:
                partial_backlog: dict[str, Any] = c4.analyze_backlog(
                    actual_arrivals,
                    records,
                    observation_end_ns=max(
                        failure.timestamp_ns,
                        max(
                            (int(record["publish_timestamp_ns"]) for record in records),
                            default=failure.timestamp_ns,
                        ),
                    ),
                )
            else:
                partial_backlog = {
                    "backlog_time_series": [],
                    "backlog_auc_episode_ns": 0,
                    "maximum_backlog": 0,
                    "backlog_at_final_arrival": 0,
                    "final_backlog": 0,
                }
            return {
                "status": "failed",
                "method": method,
                "records": records,
                "episode_metrics": metrics,
                "aggregate": {
                    **partial_backlog,
                    "completed_episode_count": len(records),
                    "error_count": 1,
                },
                "failure_checkpoint": checkpoint,
            }
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    integrity = c4.validate_exactly_once(episodes, records)
    episode_metrics = [c4.compute_episode_metrics(record) for record in records]
    aggregate = _success_aggregate(records, episode_metrics)
    if aggregate["final_backlog"] != 0:
        raise NativeCharacterizationC4Error(
            "successful async replay retained nonzero backlog"
        )
    return {
        "status": "complete",
        "method": method,
        "records": records,
        "episode_metrics": episode_metrics,
        "aggregate": aggregate,
        "integrity": integrity,
        "failure_checkpoint": None,
    }
