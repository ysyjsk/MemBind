"""Preparation/native composition over the strict frontier and admission gates."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from .admission import AdmissionArbiter, AdmissionClass, CapacityAuthority
from .frontier import FrontierRuntime


@dataclass(slots=True)
class ExecutionResult:
    durable_frontier: int
    events: list[dict[str, Any]]
    build_makespan_ns: int
    timer_start_ns: int
    timer_stop_ns: int
    failed_sequence: int | None


class FrontierExecutor:
    """Run source-only preparation concurrently and native publication in order."""

    def __init__(
        self,
        source_count: int,
        authority: CapacityAuthority,
        *,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if source_count < 0:
            raise ValueError("source_count must be non-negative")
        self.source_count = source_count
        self.clock = clock
        self.authority = authority
        self.admission = AdmissionArbiter(authority)
        self.frontier = FrontierRuntime(source_count, clock=clock)
        self._tasks: list[asyncio.Task[Any]] = []

    async def run(
        self,
        prepare: Callable[[int], Awaitable[Any]],
        publish: Callable[[int, Any], Awaitable[Any]],
    ) -> ExecutionResult:
        timer_start = int(self.clock())
        prep_results: dict[int, Any] = {}
        prep_errors: list[BaseException] = []

        async def do_prepare(sequence: int) -> None:
            admission_class = AdmissionClass.FRONTIER_PREPARE if sequence == 0 else AdmissionClass.FUTURE_PREPARE
            await self.admission.acquire(admission_class, source_sequence=sequence)
            self.frontier._event("ADMITTED", sequence, admission_class=admission_class.value)
            try:
                value = await prepare(sequence)
                prep_results[sequence] = value
                await self.frontier.mark_prepared(sequence, value)
            except BaseException as exc:
                prep_errors.append(exc)
                self.frontier._event("PREPARE_FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
                raise
            finally:
                await self.admission.release(admission_class)

        self._tasks = [asyncio.create_task(do_prepare(sequence)) for sequence in range(self.source_count)]
        try:
            for sequence in range(self.source_count):
                while sequence not in self.frontier.prepared:
                    if any(task.done() and task.exception() is not None for task in self._tasks):
                        error = next(task.exception() for task in self._tasks if task.done() and task.exception() is not None)
                        self.frontier.failed_sequence = sequence
                        raise error
                    await asyncio.sleep(0)
                await self.admission.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=sequence)
                self.frontier._event("ADMITTED", sequence, admission_class=AdmissionClass.NATIVE_FRONTIER.value)
                try:
                    await self.frontier.publish(sequence, lambda value, seq=sequence: publish(seq, value))
                finally:
                    await self.admission.release(AdmissionClass.NATIVE_FRONTIER)
        except BaseException:
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            raise
        finally:
            self._tasks = []
        timer_stop = int(self.clock())
        return ExecutionResult(
            durable_frontier=self.frontier.durable_frontier,
            events=list(self.frontier.events),
            build_makespan_ns=timer_stop - timer_start,
            timer_start_ns=timer_start,
            timer_stop_ns=timer_stop,
            failed_sequence=self.frontier.failed_sequence,
        )

