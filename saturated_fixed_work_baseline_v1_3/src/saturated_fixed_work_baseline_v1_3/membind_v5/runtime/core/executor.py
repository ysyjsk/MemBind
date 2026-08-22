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
        prepare_admission: bool = True,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        admission_event_sink: Callable[[dict[str, Any]], None] | None = None,
        lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if source_count < 0:
            raise ValueError("source_count must be non-negative")
        self.source_count = source_count
        self.clock = clock
        self.authority = authority
        self.prepare_admission = bool(prepare_admission)
        self.admission = AdmissionArbiter(authority, event_sink=admission_event_sink)
        self.frontier = FrontierRuntime(source_count, clock=clock, event_sink=event_sink)
        self.lifecycle_sink = lifecycle_sink
        self._tasks: list[asyncio.Task[Any]] = []

    async def run(
        self,
        prepare: Callable[[int], Awaitable[Any]],
        publish: Callable[[int, Any], Awaitable[Any]],
    ) -> ExecutionResult:
        timer_start = int(self.clock())
        if self.lifecycle_sink is not None:
            self.lifecycle_sink({"event": "FORMAL_START", "monotonic_ns": timer_start, "t0_ns": timer_start})
        async def do_prepare(sequence: int) -> None:
            admission_class = AdmissionClass.FRONTIER_PREPARE if sequence == 0 else AdmissionClass.FUTURE_PREPARE
            if self.prepare_admission:
                await self.admission.acquire(admission_class, source_sequence=sequence)
                self.frontier._event("ADMITTED", sequence, admission_class=admission_class.value)
            else:
                self.frontier._event("PREPARE_SUBMITTED", sequence, admission_class=admission_class.value)
            try:
                value = await prepare(sequence)
                await self.frontier.mark_prepared(sequence, value)
            except BaseException as exc:
                self.frontier._event("PREPARE_FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
                raise
            finally:
                if self.prepare_admission:
                    await self.admission.release(admission_class)

        # Keep the coroutine window bounded by the frozen runtime-derived
        # capacity.  Provider admission still controls outstanding calls; this
        # scheduler bound prevents one task/future per source from accumulating
        # memory and cancellation scope on a full history.
        task_by_sequence: dict[int, asyncio.Task[Any]] = {}
        next_sequence = 0
        window = max(1, int(self.authority.value))

        def schedule_window() -> None:
            nonlocal next_sequence
            active = sum(1 for task in task_by_sequence.values() if not task.done())
            while next_sequence < self.source_count and active < window:
                task = asyncio.create_task(do_prepare(next_sequence))
                task_by_sequence[next_sequence] = task
                self._tasks.append(task)
                next_sequence += 1
                active += 1

        def harvest_done() -> None:
            for sequence, task in list(task_by_sequence.items()):
                if not task.done():
                    continue
                if task.cancelled():
                    continue
                error = task.exception()
                if error is not None:
                    self.frontier.failed_sequence = sequence
                    raise error
                task_by_sequence.pop(sequence, None)
                if task in self._tasks:
                    self._tasks.remove(task)

        schedule_window()
        try:
            for sequence in range(self.source_count):
                while sequence not in self.frontier.prepared:
                    harvest_done()
                    schedule_window()
                    if sequence in self.frontier.prepared:
                        break
                    pending = [task for task in task_by_sequence.values() if not task.done()]
                    if not pending:
                        raise RuntimeError(f"preparation stalled at source {sequence}")
                    await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                harvest_done()
                schedule_window()
                await self.admission.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=sequence)
                self.frontier._event("ADMITTED", sequence, admission_class=AdmissionClass.NATIVE_FRONTIER.value)
                try:
                    await self.frontier.publish(sequence, lambda value, seq=sequence: publish(seq, value))
                finally:
                    await self.admission.release(AdmissionClass.NATIVE_FRONTIER)
                task_by_sequence.pop(sequence, None)
                schedule_window()
        except BaseException:
            if self.lifecycle_sink is not None:
                self.lifecycle_sink(
                    {
                        "event": "TIMER_ABORT",
                        "monotonic_ns": int(self.clock()),
                        "timer_start_ns": timer_start,
                        "durable_frontier": self.frontier.durable_frontier,
                        "failed_sequence": self.frontier.failed_sequence,
                    }
                )
            for task in task_by_sequence.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*task_by_sequence.values(), return_exceptions=True)
            raise
        finally:
            self._tasks = []
        timer_stop = int(self.clock())
        if self.lifecycle_sink is not None:
            final_publication = max(
                (int(event["monotonic_ns"]) for event in self.frontier.events if event["event"] == "PUBLICATION_DURABLE"),
                default=timer_stop,
            )
            self.lifecycle_sink(
                {
                    "event": "TIMER_STOP",
                    "monotonic_ns": timer_stop,
                    "timer_stop_ns": timer_stop,
                    "final_publication_ns": final_publication,
                    "t_durable_complete_ns": final_publication,
                }
            )
        return ExecutionResult(
            durable_frontier=self.frontier.durable_frontier,
            events=list(self.frontier.events),
            build_makespan_ns=timer_stop - timer_start,
            timer_start_ns=timer_start,
            timer_stop_ns=timer_stop,
            failed_sequence=self.frontier.failed_sequence,
        )
