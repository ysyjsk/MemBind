"""Frontier-aware JIT preparation and ordered native publication for V6.1."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..membind_v5.runtime.core.admission import CapacityAuthority
from .admission import ForegroundAdmissionArbiter
from .policy import V61Policy
from .resource_credit import ResourceCreditPolicy


JIT_EXECUTION_STRATEGY = "jit_frontier_interleaved_v1"
STAGED_EXECUTION_STRATEGY = "bounded_extraction_stage_then_native_v1"
DUAL_STREAMING_EXECUTION_STRATEGY = "phase_isolated_dual_streaming_v1"


@dataclass(frozen=True, slots=True)
class V61ExecutionResult:
    execution_strategy: str
    durable_frontier: int
    preparation_durable_frontier: int
    build_makespan_ns: int
    timer_start_ns: int
    timer_stop_ns: int
    events: tuple[dict[str, Any], ...]
    preparation_intervals: tuple[dict[str, int], ...]
    native_intervals: tuple[dict[str, int], ...]
    frontier_wait_intervals: tuple[dict[str, int], ...]
    max_started_ahead: int
    arbiter_instance_id: str
    stage_barrier: dict[str, Any] | None


async def run_resource_credit_frontier_history_async(
    source_count: int,
    prepare: Callable[[int], Awaitable[Any]],
    publish: Callable[[int, Any], Awaitable[Any]],
    *,
    authority: CapacityAuthority,
    policy: ResourceCreditPolicy,
    admission: ForegroundAdmissionArbiter,
    clock: Callable[[], int] = time.monotonic_ns,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    interval_sink: Callable[[dict[str, Any]], None] | None = None,
    lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
    execution_strategy: str = DUAL_STREAMING_EXECUTION_STRATEGY,
) -> V61ExecutionResult:
    """Execute with a lazy dependency-ready queue and derived future credit.

    The current authoritative source is always materialized as P0. Future
    tasks are created only after querying the current resource-credit snapshot;
    no fixed lookahead/window is consulted. The old JIT/staged functions below
    remain available for the explicit fixed-policy ablation.
    """
    if source_count < 0:
        raise ValueError("source_count must be non-negative")
    if not isinstance(policy, ResourceCreditPolicy) or not policy.is_resource_credit:
        raise ValueError("resource executor requires MEMBIND_RESOURCE_CREDIT_V1")
    if admission.authority != authority or admission.policy != policy:
        raise ValueError("scheduler and provider must share one capacity authority and policy")
    if not admission.resource_credit_enabled:
        raise ValueError("admission arbiter is not resource-credit enabled")
    events: list[dict[str, Any]] = []
    preparation_intervals: list[dict[str, int]] = []
    native_intervals: list[dict[str, int]] = []
    frontier_wait_intervals: list[dict[str, int]] = []
    tasks: dict[int, asyncio.Task[Any]] = {}
    durable_frontier = -1
    max_started_ahead = 0
    timer_start = int(clock())

    def emit(event: str, sequence: int | None = None, **fields: Any) -> None:
        row = {
            "event": event,
            "monotonic_ns": int(clock()),
            "durable_frontier": durable_frontier,
            "arbiter_instance_id": admission.instance_id,
            "execution_strategy": execution_strategy,
            "method_identity": policy.method_identity,
            **fields,
        }
        if sequence is not None:
            row["source_sequence"] = int(sequence)
        events.append(row)
        if event_sink is not None:
            event_sink(dict(row))

    if lifecycle_sink is not None:
        lifecycle_sink({
            "event": "FORMAL_START",
            "monotonic_ns": timer_start,
            "arbiter_instance_id": admission.instance_id,
            "execution_strategy": execution_strategy,
            "method_identity": policy.method_identity,
        })

    async def do_prepare(sequence: int) -> Any:
        start = int(clock())
        emit("PREPARE_START", sequence)
        try:
            value = await prepare(sequence)
            emit("PREPARE_READY", sequence)
            return value
        except asyncio.CancelledError:
            emit("PREPARE_CANCELLED", sequence)
            raise
        except BaseException as exc:
            emit("PREPARE_FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
            raise
        finally:
            row = {"source_sequence": int(sequence), "start_ns": start, "end_ns": int(clock())}
            preparation_intervals.append(row)
            if interval_sink is not None:
                interval_sink({"event": "PREPARE_INTERVAL", **row})

    def start_ready(current: int) -> None:
        nonlocal max_started_ahead
        if current >= source_count:
            return
        if current not in tasks:
            tasks[current] = asyncio.create_task(do_prepare(current))
            emit("PREPARE_SUBMITTED", current, admission_class="FRONTIER_PREPARE")
        future = [seq for seq in range(current + 1, source_count) if seq not in tasks]
        credit = admission.future_credit(dependency_ready_future_count=len(future))
        for sequence in future[:credit]:
            tasks[sequence] = asyncio.create_task(do_prepare(sequence))
            max_started_ahead = max(max_started_ahead, sequence - current)
            emit(
                "PREPARE_SUBMITTED",
                sequence,
                admission_class="FUTURE_PREPARE",
                future_credit=credit,
                dependency_ready_future_count=len(future),
            )
        emit(
            "RESOURCE_CREDIT_OBSERVED",
            current,
            future_credit=credit,
            dependency_ready_future_count=len(future),
        )

    try:
        for sequence in range(source_count):
            start_ready(sequence)
            wait_start = int(clock())
            emit("FRONTIER_WAIT_START", sequence)
            value = await tasks[sequence]
            wait_end = int(clock())
            wait_row = {"source_sequence": int(sequence), "start_ns": wait_start, "end_ns": wait_end, "duration_ns": wait_end - wait_start}
            frontier_wait_intervals.append(wait_row)
            emit("FRONTIER_WAIT_END", sequence, wait_ns=wait_row["duration_ns"])
            await admission.enter_native_guard(sequence)
            native_start = int(clock())
            emit("NATIVE_START", sequence)
            try:
                await publish(sequence, value)
                durable_frontier = sequence
                emit("PUBLICATION_DURABLE", sequence)
                await admission.frontier_advanced(sequence)
            except BaseException as exc:
                emit("NATIVE_FAILURE", sequence, error_type=f"{type(exc).__module__}.{type(exc).__qualname__}")
                raise
            finally:
                row = {"source_sequence": int(sequence), "start_ns": native_start, "end_ns": int(clock())}
                native_intervals.append(row)
                if interval_sink is not None:
                    interval_sink({"event": "NATIVE_INTERVAL", **row})
                await admission.exit_native_guard(sequence)
            tasks.pop(sequence, None)
            start_ready(sequence + 1)
    except BaseException:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if lifecycle_sink is not None:
            lifecycle_sink({"event": "TIMER_ABORT", "monotonic_ns": int(clock()), "durable_frontier": durable_frontier, "arbiter_instance_id": admission.instance_id})
        raise
    timer_stop = int(clock())
    if admission.outstanding or admission.future_outstanding or admission.native_guard_active:
        raise RuntimeError("resource-credit executor finished with leaked admission state")
    if lifecycle_sink is not None:
        lifecycle_sink({"event": "TIMER_STOP", "monotonic_ns": timer_stop, "durable_frontier": durable_frontier, "arbiter_instance_id": admission.instance_id})
    return V61ExecutionResult(
        execution_strategy=execution_strategy,
        durable_frontier=durable_frontier,
        preparation_durable_frontier=durable_frontier,
        build_makespan_ns=timer_stop - timer_start,
        timer_start_ns=timer_start,
        timer_stop_ns=timer_stop,
        events=tuple(events),
        preparation_intervals=tuple(sorted(preparation_intervals, key=lambda row: row["source_sequence"])),
        native_intervals=tuple(sorted(native_intervals, key=lambda row: row["source_sequence"])),
        frontier_wait_intervals=tuple(sorted(frontier_wait_intervals, key=lambda row: row["source_sequence"])),
        max_started_ahead=max_started_ahead,
        arbiter_instance_id=admission.instance_id,
        stage_barrier=None,
    )


async def run_jit_frontier_history_async(
    source_count: int,
    prepare: Callable[[int], Awaitable[Any]],
    publish: Callable[[int, Any], Awaitable[Any]],
    *,
    authority: CapacityAuthority,
    policy: V61Policy,
    admission: ForegroundAdmissionArbiter,
    clock: Callable[[], int] = time.monotonic_ns,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    interval_sink: Callable[[dict[str, Any]], None] | None = None,
    lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
    execution_strategy: str = JIT_EXECUTION_STRATEGY,
) -> V61ExecutionResult:
    if source_count < 0:
        raise ValueError("source_count must be non-negative")
    if admission.authority != authority:
        raise ValueError("scheduler and provider must share one capacity authority")
    if admission.policy != policy:
        raise ValueError("scheduler and provider must share one policy")
    if execution_strategy not in {JIT_EXECUTION_STRATEGY, DUAL_STREAMING_EXECUTION_STRATEGY}:
        raise ValueError("unsupported interleaved execution strategy")
    if execution_strategy == DUAL_STREAMING_EXECUTION_STRATEGY and not admission.phase_isolated:
        raise ValueError("dual streaming requires phase-isolated admission")

    events: list[dict[str, Any]] = []
    preparation_intervals: list[dict[str, int]] = []
    native_intervals: list[dict[str, int]] = []
    frontier_wait_intervals: list[dict[str, int]] = []
    tasks: dict[int, asyncio.Task[Any]] = {}
    durable_frontier = -1
    max_started_ahead = 0
    timer_start = int(clock())

    def emit(event: str, sequence: int | None = None, **fields: Any) -> None:
        row = {
            "event": event,
            "monotonic_ns": int(clock()),
            "durable_frontier": durable_frontier,
            "arbiter_instance_id": admission.instance_id,
            "execution_strategy": execution_strategy,
            **fields,
        }
        if sequence is not None:
            row["source_sequence"] = int(sequence)
        events.append(row)
        if event_sink is not None:
            event_sink(dict(row))

    if lifecycle_sink is not None:
        lifecycle_sink(
            {
                "event": "FORMAL_START",
                "monotonic_ns": timer_start,
                "arbiter_instance_id": admission.instance_id,
                "execution_strategy": execution_strategy,
            }
        )

    async def do_prepare(sequence: int) -> Any:
        start = int(clock())
        emit("PREPARE_START", sequence)
        try:
            value = await prepare(sequence)
            emit("PREPARE_READY", sequence)
            return value
        except asyncio.CancelledError:
            emit("PREPARE_CANCELLED", sequence)
            raise
        except BaseException as exc:
            emit(
                "PREPARE_FAILURE",
                sequence,
                error_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
            )
            raise
        finally:
            row = {
                "source_sequence": int(sequence),
                "start_ns": start,
                "end_ns": int(clock()),
            }
            preparation_intervals.append(row)
            if interval_sink is not None:
                interval_sink({"event": "PREPARE_INTERVAL", **row})

    def start_window(current: int) -> None:
        nonlocal max_started_ahead
        if current >= source_count:
            return
        # Keep a fixed two-source JIT window. ``lookahead`` is retained in the
        # policy schema for replay compatibility, but cannot expand this
        # implementation beyond two successors. The weighted arbiter handles
        # how many long requests can actually run at once.
        effective_lookahead = min(2, max(1, int(policy.lookahead)))
        stop = min(source_count - 1, current + effective_lookahead)
        for sequence in range(current, stop + 1):
            if sequence not in tasks:
                tasks[sequence] = asyncio.create_task(do_prepare(sequence))
                max_started_ahead = max(max_started_ahead, sequence - current)
                emit(
                    "PREPARE_SUBMITTED",
                    sequence,
                    window_current=current,
                    window_stop=stop,
                    requested_lookahead=policy.lookahead,
                    effective_lookahead=effective_lookahead,
                )

    try:
        for sequence in range(source_count):
            start_window(sequence)
            wait_start = int(clock())
            emit("FRONTIER_WAIT_START", sequence)
            value = await tasks[sequence]
            wait_end = int(clock())
            wait_row = {
                "source_sequence": int(sequence),
                "start_ns": wait_start,
                "end_ns": wait_end,
                "duration_ns": wait_end - wait_start,
            }
            frontier_wait_intervals.append(wait_row)
            emit("FRONTIER_WAIT_END", sequence, wait_ns=wait_row["duration_ns"])
            await admission.enter_native_guard(sequence)
            native_start = int(clock())
            emit("NATIVE_START", sequence)
            try:
                await publish(sequence, value)
                durable_frontier = sequence
                emit("PUBLICATION_DURABLE", sequence)
                await admission.frontier_advanced(sequence)
            except BaseException as exc:
                emit(
                    "NATIVE_FAILURE",
                    sequence,
                    error_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
                )
                raise
            finally:
                row = {
                    "source_sequence": int(sequence),
                    "start_ns": native_start,
                    "end_ns": int(clock()),
                }
                native_intervals.append(row)
                if interval_sink is not None:
                    interval_sink({"event": "NATIVE_INTERVAL", **row})
                await admission.exit_native_guard(sequence)
            tasks.pop(sequence, None)
            start_window(sequence + 1)
    except BaseException:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if lifecycle_sink is not None:
            lifecycle_sink(
                {
                    "event": "TIMER_ABORT",
                    "monotonic_ns": int(clock()),
                    "durable_frontier": durable_frontier,
                    "arbiter_instance_id": admission.instance_id,
                }
            )
        raise

    timer_stop = int(clock())
    if admission.outstanding or admission.future_outstanding or admission.native_guard_active:
        raise RuntimeError("V6.1 executor finished with leaked admission state")
    if lifecycle_sink is not None:
        lifecycle_sink(
            {
                "event": "TIMER_STOP",
                "monotonic_ns": timer_stop,
                "durable_frontier": durable_frontier,
                "arbiter_instance_id": admission.instance_id,
            }
        )
    return V61ExecutionResult(
        execution_strategy=execution_strategy,
        durable_frontier=durable_frontier,
        preparation_durable_frontier=durable_frontier,
        build_makespan_ns=timer_stop - timer_start,
        timer_start_ns=timer_start,
        timer_stop_ns=timer_stop,
        events=tuple(events),
        preparation_intervals=tuple(
            sorted(preparation_intervals, key=lambda row: row["source_sequence"])
        ),
        native_intervals=tuple(
            sorted(native_intervals, key=lambda row: row["source_sequence"])
        ),
        frontier_wait_intervals=tuple(
            sorted(frontier_wait_intervals, key=lambda row: row["source_sequence"])
        ),
        max_started_ahead=max_started_ahead,
        arbiter_instance_id=admission.instance_id,
        stage_barrier=None,
    )


async def run_staged_frontier_history_async(
    source_count: int,
    prepare: Callable[[int], Awaitable[Any]],
    publish: Callable[[int, Any], Awaitable[Any]],
    *,
    authority: CapacityAuthority,
    policy: V61Policy,
    admission: ForegroundAdmissionArbiter,
    preparation_frontier_sink: Callable[[int], None],
    clock: Callable[[], int] = time.monotonic_ns,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    interval_sink: Callable[[dict[str, Any]], None] | None = None,
    lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
) -> V61ExecutionResult:
    """Prepare certified extraction first, then publish authoritatively.

    Stage A uses the same weighted provider arbiter as the interleaved
    executor. Its task window is bounded and its admission frontier advances
    only in source order. Stage B starts only after every extraction result is
    available and all Stage A provider state has drained.
    """
    if source_count < 0:
        raise ValueError("source_count must be non-negative")
    if admission.authority != authority:
        raise ValueError("scheduler and provider must share one capacity authority")
    if admission.policy != policy:
        raise ValueError("scheduler and provider must share one policy")

    events: list[dict[str, Any]] = []
    preparation_intervals: list[dict[str, int]] = []
    native_intervals: list[dict[str, int]] = []
    preparation_wait_intervals: list[dict[str, int]] = []
    tasks: dict[int, asyncio.Task[Any]] = {}
    prepared: dict[int, Any] = {}
    preparation_failure: asyncio.Future[tuple[int, BaseException]] = (
        asyncio.get_running_loop().create_future()
    )
    durable_frontier = -1
    preparation_durable_frontier = -1
    max_started_ahead = 0
    timer_start = int(clock())

    def emit(event: str, sequence: int | None = None, **fields: Any) -> None:
        row = {
            "event": event,
            "monotonic_ns": int(clock()),
            "durable_frontier": durable_frontier,
            "preparation_durable_frontier": preparation_durable_frontier,
            "execution_strategy": STAGED_EXECUTION_STRATEGY,
            "arbiter_instance_id": admission.instance_id,
            **fields,
        }
        if sequence is not None:
            row["source_sequence"] = int(sequence)
        events.append(row)
        if event_sink is not None:
            event_sink(dict(row))

    if lifecycle_sink is not None:
        lifecycle_sink(
            {
                "event": "FORMAL_START",
                "monotonic_ns": timer_start,
                "execution_strategy": STAGED_EXECUTION_STRATEGY,
                "arbiter_instance_id": admission.instance_id,
            }
        )

    async def do_prepare(sequence: int) -> Any:
        start = int(clock())
        emit("PREPARE_START", sequence)
        try:
            value = await prepare(sequence)
            emit("PREPARE_READY", sequence)
            return value
        except asyncio.CancelledError:
            emit("PREPARE_CANCELLED", sequence)
            raise
        except BaseException as exc:
            emit(
                "PREPARE_FAILURE",
                sequence,
                error_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
            )
            if not preparation_failure.done():
                preparation_failure.set_result((sequence, exc))
            raise
        finally:
            row = {
                "source_sequence": int(sequence),
                "start_ns": start,
                "end_ns": int(clock()),
            }
            preparation_intervals.append(row)
            if interval_sink is not None:
                interval_sink({"event": "PREPARE_INTERVAL", **row})

    def start_window(current: int) -> None:
        nonlocal max_started_ahead
        if current >= source_count:
            return
        effective_lookahead = min(2, max(1, int(policy.lookahead)))
        stop = min(source_count - 1, current + effective_lookahead)
        for sequence in range(current, stop + 1):
            if sequence not in tasks and sequence not in prepared:
                tasks[sequence] = asyncio.create_task(do_prepare(sequence))
                max_started_ahead = max(max_started_ahead, sequence - current)
                emit(
                    "PREPARE_SUBMITTED",
                    sequence,
                    window_current=current,
                    window_stop=stop,
                    requested_lookahead=policy.lookahead,
                    effective_lookahead=effective_lookahead,
                )

    try:
        emit("PREPARATION_STAGE_START")
        for sequence in range(source_count):
            start_window(sequence)
            wait_start = int(clock())
            emit("PREPARATION_ORDER_WAIT_START", sequence)
            done, _pending = await asyncio.wait(
                (tasks[sequence], preparation_failure),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if preparation_failure in done:
                failed_sequence, _failure = preparation_failure.result()
                await tasks[failed_sequence]
                raise RuntimeError("unreachable preparation failure path")
            prepared[sequence] = tasks[sequence].result()
            wait_end = int(clock())
            wait_row = {
                "source_sequence": int(sequence),
                "start_ns": wait_start,
                "end_ns": wait_end,
                "duration_ns": wait_end - wait_start,
            }
            preparation_wait_intervals.append(wait_row)
            preparation_durable_frontier = sequence
            preparation_frontier_sink(sequence)
            emit(
                "PREPARATION_FRONTIER_DURABLE",
                sequence,
                wait_ns=wait_row["duration_ns"],
            )
            await admission.preparation_frontier_advanced(sequence)
            tasks.pop(sequence, None)
            start_window(sequence + 1)

        barrier = {
            "status": "PASS",
            "source_count": int(source_count),
            "prepared_count": len(prepared),
            "preparation_durable_frontier": preparation_durable_frontier,
            "outstanding": admission.outstanding,
            "future_outstanding": admission.future_outstanding,
            "native_outstanding": admission.native_outstanding,
            "tokens_outstanding": admission.tokens_outstanding,
            "waiter_count": admission.waiter_count,
            "native_guard_active": admission.native_guard_active,
        }
        if tasks or len(prepared) != source_count:
            raise RuntimeError("V6.1 preparation stage finished with incomplete tasks")
        if any(
            (
                admission.outstanding,
                admission.future_outstanding,
                admission.native_outstanding,
                admission.tokens_outstanding,
                admission.waiter_count,
            )
        ) or admission.native_guard_active:
            raise RuntimeError("V6.1 preparation barrier has leaked admission state")
        emit("PREPARATION_STAGE_DURABLE", **barrier)

        emit("NATIVE_STAGE_START")
        for sequence in range(source_count):
            await admission.enter_native_guard(sequence)
            native_start = int(clock())
            emit("NATIVE_START", sequence)
            try:
                await publish(sequence, prepared[sequence])
                durable_frontier = sequence
                emit("PUBLICATION_DURABLE", sequence)
                await admission.frontier_advanced(sequence)
            except BaseException as exc:
                emit(
                    "NATIVE_FAILURE",
                    sequence,
                    error_type=f"{type(exc).__module__}.{type(exc).__qualname__}",
                )
                raise
            finally:
                row = {
                    "source_sequence": int(sequence),
                    "start_ns": native_start,
                    "end_ns": int(clock()),
                }
                native_intervals.append(row)
                if interval_sink is not None:
                    interval_sink({"event": "NATIVE_INTERVAL", **row})
                await admission.exit_native_guard(sequence)
    except BaseException:
        for task in tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        if lifecycle_sink is not None:
            lifecycle_sink(
                {
                    "event": "TIMER_ABORT",
                    "monotonic_ns": int(clock()),
                    "durable_frontier": durable_frontier,
                    "preparation_durable_frontier": preparation_durable_frontier,
                    "execution_strategy": STAGED_EXECUTION_STRATEGY,
                    "arbiter_instance_id": admission.instance_id,
                }
            )
        raise

    timer_stop = int(clock())
    if (
        admission.outstanding
        or admission.future_outstanding
        or admission.native_outstanding
        or admission.tokens_outstanding
        or admission.waiter_count
        or admission.native_guard_active
    ):
        raise RuntimeError("V6.1 staged executor finished with leaked admission state")
    if lifecycle_sink is not None:
        lifecycle_sink(
            {
                "event": "TIMER_STOP",
                "monotonic_ns": timer_stop,
                "durable_frontier": durable_frontier,
                "preparation_durable_frontier": preparation_durable_frontier,
                "execution_strategy": STAGED_EXECUTION_STRATEGY,
                "arbiter_instance_id": admission.instance_id,
            }
        )
    return V61ExecutionResult(
        execution_strategy=STAGED_EXECUTION_STRATEGY,
        durable_frontier=durable_frontier,
        preparation_durable_frontier=preparation_durable_frontier,
        build_makespan_ns=timer_stop - timer_start,
        timer_start_ns=timer_start,
        timer_stop_ns=timer_stop,
        events=tuple(events),
        preparation_intervals=tuple(
            sorted(preparation_intervals, key=lambda row: row["source_sequence"])
        ),
        native_intervals=tuple(
            sorted(native_intervals, key=lambda row: row["source_sequence"])
        ),
        frontier_wait_intervals=tuple(
            sorted(preparation_wait_intervals, key=lambda row: row["source_sequence"])
        ),
        max_started_ahead=max_started_ahead,
        arbiter_instance_id=admission.instance_id,
        stage_barrier=barrier,
    )


__all__ = [
    "DUAL_STREAMING_EXECUTION_STRATEGY",
    "JIT_EXECUTION_STRATEGY",
    "STAGED_EXECUTION_STRATEGY",
    "V61ExecutionResult",
    "run_jit_frontier_history_async",
    "run_staged_frontier_history_async",
]
