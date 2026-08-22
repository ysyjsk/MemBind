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
        admission: AdmissionArbiter | None = None,
        durable_frontier_sink: Callable[[int], None] | None = None,
        admit_native: bool = True,
    ) -> None:
        if source_count < 0:
            raise ValueError("source_count must be non-negative")
        self.source_count = source_count
        self.clock = clock
        self.authority = authority
        self.prepare_admission = bool(prepare_admission)
        if admission is not None and admission.authority != authority:
            raise ValueError("shared admission authority mismatch")
        self.admission = admission or AdmissionArbiter(
            authority,
            name="executor",
            event_sink=admission_event_sink,
        )
        self.durable_frontier_sink = durable_frontier_sink
        self.admit_native = bool(admit_native)

        def frontier_event(row: dict[str, Any]) -> None:
            if event_sink is not None:
                event_sink(dict(row))
            if row.get("event") == "PUBLICATION_DURABLE" and durable_frontier_sink is not None:
                durable_frontier_sink(int(row["source_sequence"]))

        self.frontier = FrontierRuntime(source_count, clock=clock, event_sink=frontier_event)
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
        def prepare_admission_class(sequence: int) -> AdmissionClass:
            # A queued preparation follows the live durable frontier.  The
            # source is not assigned a permanent staging class when its task is
            # created: frontier advancement can promote d+1 while it waits.
            return (
                AdmissionClass.FRONTIER_PREPARE
                if sequence == self.frontier.durable_frontier + 1
                else AdmissionClass.FUTURE_PREPARE
            )

        async def do_prepare(sequence: int) -> None:
            admission_class = prepare_admission_class(sequence)
            admitted = False
            if self.prepare_admission:
                admitted_class = await self.admission.acquire(
                    admission_class,
                    source_sequence=sequence,
                    class_resolver=lambda: prepare_admission_class(sequence),
                )
                admitted = True
                self.frontier._event("ADMITTED", sequence, admission_class=admitted_class.value)
            else:
                self.frontier._event("PREPARE_SUBMITTED", sequence, admission_class=admission_class.value)
            try:
                value = await prepare(sequence)
                await self.frontier.mark_prepared(sequence, value)
            except asyncio.CancelledError:
                self.frontier._event("PREPARE_CANCELLED", sequence)
                raise
            except BaseException as exc:
                self.frontier._event("PREPARE_FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
                raise
            finally:
                if admitted:
                    await self.admission.release(admitted_class)

        # Materialize every semantic-safe source immediately.  This is a task
        # registry, not a provider-work permit: expensive LLM calls still wait
        # on the shared provider AdmissionArbiter, which is the only capacity
        # envelope.  Keeping all tasks visible also lets a waiting d+1 request
        # be reclassified as FRONTIER_PREPARE as soon as the frontier advances.
        task_by_sequence: dict[int, asyncio.Task[Any]] = {}
        prep_errors: dict[int, BaseException] = {}
        for sequence in range(self.source_count):
            task = asyncio.create_task(do_prepare(sequence))
            task_by_sequence[sequence] = task
            self._tasks.append(task)

        def harvest_done() -> None:
            for sequence, task in list(task_by_sequence.items()):
                if not task.done():
                    continue
                if task.cancelled():
                    continue
                error = task.exception()
                if error is not None:
                    prep_errors[sequence] = error
                    task_by_sequence.pop(sequence, None)
                    if task in self._tasks:
                        self._tasks.remove(task)
                    self.frontier._event(
                        "PREPARE_DEFERRED_FAILURE",
                        sequence,
                        error_type=f"{type(error).__module__}.{type(error).__qualname__}",
                    )
                    failed_at = min(prep_errors)
                    for future_sequence, future_task in list(task_by_sequence.items()):
                        if future_sequence > failed_at and not future_task.done():
                            future_task.cancel()
                    continue
                task_by_sequence.pop(sequence, None)
                if task in self._tasks:
                    self._tasks.remove(task)

        try:
            for sequence in range(self.source_count):
                while sequence not in self.frontier.prepared:
                    harvest_done()
                    if sequence in prep_errors:
                        self.frontier.failed_sequence = sequence
                        raise prep_errors[sequence]
                    if sequence in self.frontier.prepared:
                        break
                    pending = [task for task in task_by_sequence.values() if not task.done()]
                    if not pending:
                        raise RuntimeError(f"preparation stalled at source {sequence}")
                    await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                harvest_done()
                native_admitted = False
                if self.admit_native:
                    await self.admission.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=sequence)
                    native_admitted = True
                    self.frontier._event("ADMITTED", sequence, admission_class=AdmissionClass.NATIVE_FRONTIER.value)
                try:
                    await self.frontier.publish(sequence, lambda value, seq=sequence: publish(seq, value))
                    # Publication changes the class of queued provider work,
                    # regardless of whether native itself used an outer permit.
                    await self.admission.frontier_advanced(sequence)
                finally:
                    if native_admitted:
                        await self.admission.release(AdmissionClass.NATIVE_FRONTIER)
                task_by_sequence.pop(sequence, None)
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
