"""Event-driven single-stream MemBind v3.1 coordinator."""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.source_log import SourceLog
from paper_eval.membind_v31.admission import RequestKind
from paper_eval.membind_v31.request_runtime import llm_request_scope
from paper_eval.membind_v31.scheduler import ArrivalGate, PreparedROB, SourceEnvelope


class MemBindV31CoordinatorError(ValueError):
    """A live semantic-path invariant or callback failed closed."""


def _fail(code: str) -> MemBindV31CoordinatorError:
    return MemBindV31CoordinatorError(code)


def _positive(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


async def _await_if_needed(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


async def run_membind_v31_stream(
    *,
    stream_id: str,
    source_log: SourceLog,
    arrival_offsets_ns: Sequence[int],
    adapter: object,
    request_client: object,
    compile_workers: int,
    lookahead: int,
    observer: Callable[[dict[str, object]], object],
    scheduler_observer: Callable[[dict[str, object]], object] | None = None,
    publication_probe: Callable[[int, object], object],
    prepared_persistor: Callable[[object], object] | None = None,
    commit_observer: Callable[[int, object], object] | None = None,
    publication_persistor: Callable[[int, object], object] | None = None,
    previous_episode_limit: int = 10,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    logical_clock_ns: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], object] = asyncio.sleep,
) -> dict[str, object]:
    """Run arrived evidence in parallel while binding one exact frontier."""

    if not isinstance(stream_id, str) or not stream_id:
        raise _fail("stream_id_invalid")
    if not isinstance(source_log, SourceLog):
        raise _fail("source_log_invalid")
    if isinstance(arrival_offsets_ns, (str, bytes)) or not isinstance(
        arrival_offsets_ns, Sequence
    ):
        raise _fail("arrival_trace_invalid")
    offsets = tuple(_nonnegative(value, "arrival_trace_invalid") for value in arrival_offsets_ns)
    if len(offsets) != source_log.source_count or tuple(sorted(offsets)) != offsets:
        raise _fail("arrival_trace_invalid")
    workers = _positive(compile_workers, "compile_workers_invalid")
    window = _nonnegative(lookahead, "lookahead_invalid")
    last_n = _positive(previous_episode_limit, "previous_episode_limit_invalid")
    if not callable(getattr(adapter, "prepare", None)) or not callable(
        getattr(adapter, "bind", None)
    ):
        raise _fail("adapter_invalid")
    if not callable(getattr(request_client, "frontier_bind_region", None)):
        raise _fail("request_client_invalid")
    if not callable(observer) or not callable(publication_probe):
        raise _fail("observer_or_probe_invalid")
    if scheduler_observer is not None and not callable(scheduler_observer):
        raise _fail("scheduler_observer_invalid")
    for callback in (prepared_persistor, commit_observer, publication_persistor):
        if callback is not None and not callable(callback):
            raise _fail("durability_callback_invalid")
    if not callable(clock_ns) or not callable(logical_clock_ns) or not callable(sleep):
        raise _fail("clock_or_sleep_invalid")

    run_start_ns = _nonnegative(clock_ns(), "clock_invalid")
    arrival_times = tuple(run_start_ns + offset for offset in offsets)
    gate = ArrivalGate(
        SourceEnvelope(
            stream_id=stream_id,
            source_sequence=record.source_sequence,
            arrival_time=float(arrival_times[record.source_sequence]),
            payload=record,
        )
        for record in source_log.records
    )
    rob = PreparedROB(compile_workers=workers, lookahead=window)
    condition = asyncio.Condition()
    states = ["NEW"] * source_log.source_count
    compile_inputs: dict[int, object] = {}
    compile_tasks: dict[int, asyncio.Task[None]] = {}
    bind_task: asyncio.Task[None] | None = None
    publications: list[int] = []
    direct_violations: list[dict[str, object]] = []
    failure: tuple[str, BaseException] | None = None
    scheduler_event_count = 0
    max_ready_compile_count = 0
    max_arrived_beyond_lookahead_count = 0
    max_reserved_compile_count = 0
    max_prepared_rob_occupancy = 0

    def emit(event_type: str, sequence: int, **fields: object) -> None:
        event = {
            "event_type": event_type,
            "stream_id": stream_id,
            "source_sequence": sequence,
            "timestamp_ns": _nonnegative(clock_ns(), "clock_invalid"),
            **fields,
        }
        try:
            observer(event)
        except Exception:
            raise _fail("observer_failed") from None

    def emit_scheduler_state(reason: str, trigger_sequence: int | None = None) -> None:
        """Emit content-safe scheduler state for the isolated optimization lane."""

        nonlocal scheduler_event_count
        nonlocal max_ready_compile_count
        nonlocal max_arrived_beyond_lookahead_count
        nonlocal max_reserved_compile_count
        nonlocal max_prepared_rob_occupancy
        if scheduler_observer is None:
            return
        if not isinstance(reason, str) or not reason:
            raise _fail("scheduler_reason_invalid")
        if trigger_sequence is not None:
            _nonnegative(trigger_sequence, "scheduler_trigger_invalid")
        rob_state = rob.observation()
        frontier = (
            rob.frontier(stream_id)
            if any(state != "NEW" for state in states)
            else 0
        )
        scheduled = {
            sequence
            for sequence, task in compile_tasks.items()
            if not task.done()
        }
        reserved = {
            sequence
            for sequence in scheduled
            if states[sequence] == "ARRIVED"
        }
        legal_candidates = [
            sequence
            for sequence, state in enumerate(states)
            if state == "ARRIVED" and sequence <= frontier + window
        ]
        ready = [
            sequence
            for sequence in legal_candidates
            if sequence not in reserved
        ]
        beyond = [
            sequence
            for sequence, state in enumerate(states)
            if state == "ARRIVED" and sequence > frontier + window
        ]
        if frontier >= source_log.source_count:
            frontier_phase = "PUBLISHED"
            frontier_wait_reason = "NONE"
        else:
            state = states[frontier]
            if state == "NEW":
                frontier_phase = "NO_SOURCE_ARRIVED"
                frontier_wait_reason = "SOURCE_NOT_ARRIVED"
            elif state == "ARRIVED":
                frontier_phase = "WAITING_FOR_COMPILE"
                frontier_wait_reason = (
                    "COMPILE_DISPATCHED"
                    if frontier in reserved
                    else "COMPILE_WORKER_BUSY"
                    if int(rob.observation()["active_compile_count"]) >= workers
                    else "COMPILE_READY"
                )
            elif state == "COMPILING":
                frontier_phase = "COMPILE_ACTIVE"
                frontier_wait_reason = "COMPILE_IN_PROGRESS"
            elif state == "PREPARED":
                bind_reserved = bind_task is not None and not bind_task.done()
                frontier_phase = "BIND_DISPATCHED" if bind_reserved else "READY_TO_BIND"
                frontier_wait_reason = "BIND_DISPATCHED" if bind_reserved else "NONE"
            elif state == "BINDING":
                frontier_phase = "BINDING"
                frontier_wait_reason = "BIND_IN_PROGRESS"
            elif state == "FAILED":
                frontier_phase = "TERMINAL"
                frontier_wait_reason = "STREAM_FAILED"
            else:
                frontier_phase = "TERMINAL"
                frontier_wait_reason = "UNCLASSIFIED"
        request_observation = getattr(request_client, "observation", None)
        raw_request = request_observation() if callable(request_observation) else {}
        if not isinstance(raw_request, Mapping):
            raw_request = {}

        def optional_counter(name: str) -> int | None:
            value = raw_request.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value

        prepared = int(rob_state["prepared_count"])
        ready_count = len(ready)
        beyond_count = len(beyond)
        max_ready_compile_count = max(max_ready_compile_count, ready_count)
        max_arrived_beyond_lookahead_count = max(
            max_arrived_beyond_lookahead_count,
            beyond_count,
        )
        max_reserved_compile_count = max(max_reserved_compile_count, len(reserved))
        max_prepared_rob_occupancy = max(max_prepared_rob_occupancy, prepared)
        event = {
            "schema_version": "membind.paper-eval-v3.membind-v31-scheduler-state.v1",
            "event_type": "scheduler_state",
            "event_sequence": scheduler_event_count,
            "reason": reason,
            "stream_id": stream_id,
            "trigger_source_sequence": trigger_sequence,
            "timestamp_ns": _nonnegative(clock_ns(), "clock_invalid"),
            "frontier_source_sequence": frontier,
            "frontier_phase": frontier_phase,
            "frontier_wait_reason": frontier_wait_reason,
            "lookahead": window,
            "compile_workers": workers,
            "arrived_count": sum(state != "NEW" for state in states),
            "legal_compile_candidate_count": len(legal_candidates),
            "reserved_compile_count": len(reserved),
            "legal_ready_compile_count": ready_count,
            "arrived_beyond_lookahead_count": beyond_count,
            "arrived_outside_window_count": beyond_count,
            "scheduled_compile_count": len(scheduled),
            "active_compile_count": int(rob_state["active_compile_count"]),
            "compile_slot_occupancy": len(reserved)
            + int(rob_state["active_compile_count"]),
            "prepared_rob_occupancy": prepared,
            "active_bind_count": 1 if rob_state["active_bind"] is not None else 0,
            "bind_active": rob_state["active_bind"] is not None,
            "bind_task_reserved": bind_task is not None and not bind_task.done(),
            "published_count": len(publications),
            "llm_active_count": optional_counter("active_count"),
            "llm_waiting_count": optional_counter("waiting_count"),
        }
        try:
            scheduler_observer(event)
        except Exception:
            raise _fail("scheduler_observer_failed") from None
        scheduler_event_count += 1

    async def arrive(sequence: int) -> None:
        nonlocal failure
        try:
            target = arrival_times[sequence]
            while True:
                now = _nonnegative(clock_ns(), "clock_invalid")
                if now >= target:
                    break
                await _await_if_needed(sleep((target - now) / 1_000_000_000))
            envelope = gate.claim(
                stream_id=stream_id,
                source_sequence=sequence,
                now=float(_nonnegative(clock_ns(), "clock_invalid")),
            )
            prefix = SourceLog.create(source_log.records[: sequence + 1])
            fence = EvidenceFence.capture(
                prefix,
                target_source_sequence=sequence,
                last_n=last_n,
            )
            compile_input = build_compile_input(envelope.payload, fence)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            # Arrival preparation runs in independent tasks.  Always turn a
            # task-local validation failure into an observable stream failure;
            # otherwise the coordinator could sleep forever on ``condition``.
            async with condition:
                if failure is None:
                    failure = ("arrival_failed", error)
                    states[sequence] = "FAILED"
                    emit(
                        "arrival_failure",
                        sequence,
                        error_class=f"{type(error).__module__}.{type(error).__qualname__}",
                    )
                    emit_scheduler_state("ARRIVAL_FAILURE", sequence)
                condition.notify_all()
            return
        async with condition:
            rob.record_arrival(stream_id, sequence)
            compile_inputs[sequence] = compile_input
            states[sequence] = "ARRIVED"
            emit("arrival", sequence, arrival_time_ns=target)
            emit_scheduler_state("ARRIVAL", sequence)
            condition.notify_all()

    async def compile_one(sequence: int) -> None:
        nonlocal failure
        async with condition:
            rob.start_compile(stream_id, sequence)
            states[sequence] = "COMPILING"
            emit("compile_start", sequence)
            emit_scheduler_state("COMPILE_STARTED", sequence)
        try:
            with llm_request_scope(
                kind=RequestKind.COMPILE,
                stream_id=stream_id,
                source_sequence=sequence,
            ):
                artifact = await adapter.prepare(compile_inputs[sequence])
            if prepared_persistor is not None:
                await _await_if_needed(prepared_persistor(artifact))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            async with condition:
                if failure is None:
                    failure = ("compile_failed", error)
                    try:
                        rob.fail_compile(stream_id, sequence, error)
                    except ValueError:
                        pass
                    states[sequence] = "FAILED"
                    emit(
                        "compile_failure",
                        sequence,
                        error_class=f"{type(error).__module__}.{type(error).__qualname__}",
                    )
                    emit_scheduler_state("COMPILE_FAILURE", sequence)
                condition.notify_all()
            return
        async with condition:
            if failure is not None:
                return
            try:
                rob.complete_compile(stream_id, sequence, artifact=artifact)
            except ValueError as error:
                failure = ("compile_state_failed", error)
                states[sequence] = "FAILED"
                emit(
                    "compile_failure",
                    sequence,
                    error_class=f"{type(error).__module__}.{type(error).__qualname__}",
                )
                condition.notify_all()
                return
            states[sequence] = "PREPARED"
            emit("prepared_durable", sequence)
            emit_scheduler_state("PREPARED_DURABLE", sequence)
            condition.notify_all()

    async def bind_one(sequence: int) -> None:
        nonlocal failure
        async with condition:
            artifact = rob.start_bind(stream_id, sequence)
            states[sequence] = "BINDING"
            emit(
                "bind_start",
                sequence,
                predecessor_version=sequence - 1,
            )
            emit_scheduler_state("BIND_STARTED", sequence)
        try:
            async with request_client.frontier_bind_region(stream_id, sequence):
                with llm_request_scope(
                    kind=RequestKind.FRONTIER,
                    stream_id=stream_id,
                    source_sequence=sequence,
                ):
                    result = await adapter.bind(
                        compile_inputs[sequence],
                        artifact,
                        logical_time_ns=_nonnegative(logical_clock_ns(), "logical_clock_invalid"),
                    )
            if commit_observer is not None:
                await _await_if_needed(commit_observer(sequence, result))
            emit("commit_returned", sequence)
            visible = await _await_if_needed(publication_probe(sequence, result))
            if not isinstance(visible, bool):
                raise _fail("publication_probe_invalid")
            if not visible:
                direct_violations.append(
                    {
                        "violation": "lost_publish",
                        "stream_id": stream_id,
                        "source_sequence": sequence,
                    }
                )
                raise _fail("publication_visibility_failed")
            if publication_persistor is not None:
                await _await_if_needed(publication_persistor(sequence, result))
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            async with condition:
                if failure is None:
                    code = (
                        str(error)
                        if isinstance(error, MemBindV31CoordinatorError)
                        else "bind_failed"
                    )
                    failure = (code, error)
                    try:
                        rob.fail_bind(stream_id, sequence, error)
                    except ValueError:
                        pass
                    states[sequence] = "FAILED"
                    emit(
                        "bind_failure",
                        sequence,
                        error_class=f"{type(error).__module__}.{type(error).__qualname__}",
                    )
                    emit_scheduler_state("BIND_FAILURE", sequence)
                condition.notify_all()
            return
        async with condition:
            rob.publish(stream_id, sequence)
            states[sequence] = "PUBLISHED"
            publications.append(sequence)
            emit(
                "publication_durable",
                sequence,
                predecessor_version=sequence - 1,
                published_version=sequence,
                visibility_confirmed=True,
            )
            emit_scheduler_state("PUBLICATION_DURABLE", sequence)
            condition.notify_all()

    arrival_tasks = [asyncio.create_task(arrive(sequence)) for sequence in range(source_log.source_count)]
    try:
        while len(publications) < source_log.source_count:
            async with condition:
                if failure is not None:
                    break
                frontier = rob.frontier(stream_id) if any(state != "NEW" for state in states) else 0
                active_compiles = sum(not task.done() for task in compile_tasks.values())
                dispatched = False
                for sequence, state in enumerate(states):
                    if active_compiles >= workers:
                        break
                    if state == "ARRIVED" and sequence <= frontier + window:
                        task = asyncio.create_task(compile_one(sequence))
                        compile_tasks[sequence] = task
                        active_compiles += 1
                        dispatched = True
                if (
                    bind_task is None or bind_task.done()
                ) and frontier < source_log.source_count and states[frontier] == "PREPARED":
                    bind_task = asyncio.create_task(bind_one(frontier))
                    dispatched = True
                if dispatched:
                    emit_scheduler_state("DISPATCH", frontier)
                if len(publications) >= source_log.source_count or failure is not None:
                    break
                await condition.wait()
        if failure is not None:
            code, _error = failure
            raise _fail(code)
        await asyncio.gather(*arrival_tasks)
        await asyncio.gather(*compile_tasks.values())
        if bind_task is not None:
            await bind_task
    finally:
        all_tasks = [
            task
            for task in [*arrival_tasks, *compile_tasks.values(), bind_task]
            if task is not None
        ]
        pending = [task for task in all_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)

    if publications != list(range(source_log.source_count)):
        raise _fail("publication_coverage_invalid")
    result: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.membind-v31-stream-result.v1",
        "status": "PASS",
        "stream_id": stream_id,
        "source_count": source_log.source_count,
        "publication_source_sequences": publications,
        "direct_violation_count": len(direct_violations),
        "direct_violations": direct_violations,
        "rob_observation": rob.observation(),
    }
    if scheduler_observer is not None:
        result["scheduler_observation"] = {
            "event_count": scheduler_event_count,
            "max_ready_compile_count": max_ready_compile_count,
            "max_arrived_beyond_lookahead_count": max_arrived_beyond_lookahead_count,
            "max_reserved_compile_count": max_reserved_compile_count,
            "max_prepared_rob_occupancy": max_prepared_rob_occupancy,
            "ready_pool_observable": True,
        }
    return result


__all__ = ["MemBindV31CoordinatorError", "run_membind_v31_stream"]
