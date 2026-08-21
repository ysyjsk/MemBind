"""The two and only two construction admission schedules in protocol v1.2."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import EpisodeInput


class Method(str, Enum):
    B0_NATIVE_SERIAL = "B0_NATIVE_SERIAL"
    B1_NAIVE_WHOLE_UPDATE_ASYNC = "B1_NAIVE_WHOLE_UPDATE_ASYNC"


@dataclass(frozen=True, slots=True)
class EpisodeOutcome:
    source_sequence: int
    result: Any | None
    exception: BaseException | None


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    method: Method
    created_sequences: tuple[int, ...]
    outcomes: tuple[EpisodeOutcome, ...]
    feeder_workload_await_count: int
    application_gate_count: int = 0
    artificial_sleep_count: int = 0
    configured_max_inflight: None = None


class ScheduleContractError(RuntimeError):
    def __init__(self, outcomes: tuple[EpisodeOutcome, ...]) -> None:
        self.outcomes = outcomes
        self.failed_sequences = tuple(
            outcome.source_sequence
            for outcome in outcomes
            if outcome.exception is not None
        )
        super().__init__(
            "EPISODE_TASK_FAILURES:" + ",".join(map(str, self.failed_sequences))
        )


def _ordered(episodes: Sequence[EpisodeInput]) -> tuple[EpisodeInput, ...]:
    selected = tuple(episodes)
    if any(not isinstance(episode, EpisodeInput) for episode in selected):
        raise ScheduleContractError(())
    sequences = tuple(episode.source_sequence for episode in selected)
    if sequences != tuple(range(len(selected))):
        raise ValueError("SOURCE_SEQUENCE_NOT_CONTIGUOUS")
    return selected


def _emit(
    sink: Callable[[dict[str, Any]], None] | None,
    clock: Callable[[], int],
    event: str,
    episode: EpisodeInput | None = None,
    **fields: Any,
) -> None:
    if sink is None:
        return
    sink(
        {
            "event": event,
            "monotonic_ns": clock(),
            "source_sequence": (
                None if episode is None else episode.source_sequence
            ),
            "source_hash": None if episode is None else episode.source_hash,
            **fields,
        }
    )


async def run_b0_native_serial(
    episodes: Sequence[EpisodeInput],
    add_episode: Callable[[EpisodeInput], Awaitable[Any]],
    *,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], int] = time.monotonic_ns,
) -> ScheduleResult:
    selected = _ordered(episodes)
    outcomes: list[EpisodeOutcome] = []
    for episode in selected:
        _emit(event_sink, clock, "SUBMIT", episode)
        _emit(event_sink, clock, "EXECUTION_START", episode)
        # The await is intentionally inside the source-order loop: this is B0 admission.
        try:
            value = await add_episode(episode)
        except BaseException as error:
            _emit(
                event_sink,
                clock,
                "EXCEPTION",
                episode,
                error_type=f"{type(error).__module__}.{type(error).__qualname__}",
            )
            raise
        _emit(event_sink, clock, "CALLER_RETURN", episode)
        _emit(event_sink, clock, "PUBLICATION_DURABLE", episode)
        outcomes.append(EpisodeOutcome(episode.source_sequence, value, None))
    return ScheduleResult(
        method=Method.B0_NATIVE_SERIAL,
        created_sequences=tuple(episode.source_sequence for episode in selected),
        outcomes=tuple(outcomes),
        feeder_workload_await_count=len(selected),
    )


async def run_b1_naive_whole_update_async(
    episodes: Sequence[EpisodeInput],
    add_episode: Callable[[EpisodeInput], Awaitable[Any]],
    *,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    clock: Callable[[], int] = time.monotonic_ns,
) -> ScheduleResult:
    selected = _ordered(episodes)
    tasks: list[asyncio.Task[Any]] = []

    async def execute(episode: EpisodeInput) -> Any:
        _emit(event_sink, clock, "EXECUTION_START", episode)
        try:
            value = await add_episode(episode)
        except BaseException as error:
            _emit(
                event_sink,
                clock,
                "EXCEPTION",
                episode,
                error_type=f"{type(error).__module__}.{type(error).__qualname__}",
            )
            raise
        _emit(event_sink, clock, "CALLER_RETURN", episode)
        _emit(event_sink, clock, "PUBLICATION_DURABLE", episode)
        return value

    for episode in selected:
        _emit(event_sink, clock, "SUBMIT", episode)
        tasks.append(asyncio.create_task(execute(episode)))
        _emit(event_sink, clock, "TASK_CREATED", episode)
    _emit(event_sink, clock, "SUBMISSION_CLOSED")
    terminal = await asyncio.gather(*tasks, return_exceptions=True)
    outcomes = tuple(
        EpisodeOutcome(
            source_sequence=episode.source_sequence,
            result=None if isinstance(value, BaseException) else value,
            exception=value if isinstance(value, BaseException) else None,
        )
        for episode, value in zip(selected, terminal, strict=True)
    )
    if any(outcome.exception is not None for outcome in outcomes):
        raise ScheduleContractError(outcomes)
    return ScheduleResult(
        method=Method.B1_NAIVE_WHOLE_UPDATE_ASYNC,
        created_sequences=tuple(episode.source_sequence for episode in selected),
        outcomes=outcomes,
        feeder_workload_await_count=0,
    )


__all__ = [
    "EpisodeOutcome",
    "Method",
    "ScheduleContractError",
    "ScheduleResult",
    "run_b0_native_serial",
    "run_b1_naive_whole_update_async",
]
