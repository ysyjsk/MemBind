"""Offline/live-shared M* prepare/bind/publication scheduling core.

The core owns only concurrency, source ordering, timestamps, and sanitized
durability events.  Graphiti extraction, resolution, invalidation, and commit
remain injected callbacks.  This keeps the same scheduling mechanism usable by
an offline FX0 provider and by a future pinned Graphiti production adapter.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


SCHEMA = "membind.paper-eval-v3.s5-mstar-pipeline-evidence.v1"
MSTAR = "M*"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^s5-mstar-[a-z0-9][a-z0-9-]{2,127}$")
_SUMMARY_FIELDS = {
    "configured_prepare_concurrency",
    "observed_prepare_worker_ids",
    "max_active_prepare",
    "prepare_overlap_observed",
    "max_active_bind",
    "intent_count",
    "prepared_count",
    "publication_count",
    "published_source_sequences",
    "fallback_count",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authority",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}
_EVENT_FIELDS: dict[str, set[str]] = {
    "intent": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "logical_time_ns",
    },
    "prepare_start": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "worker_id",
        "logical_time_ns",
        "prepare_start_timestamp_ns",
    },
    "prepared": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "worker_id",
        "logical_time_ns",
        "prepare_start_timestamp_ns",
        "prepare_end_timestamp_ns",
    },
    "bind_start": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "logical_time_ns",
        "visible_publication_prefix",
        "bind_start_timestamp_ns",
    },
    "commit_returned": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "logical_time_ns",
        "bind_start_timestamp_ns",
        "commit_return_timestamp_ns",
    },
    "publication": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "logical_time_ns",
        "visible_publication_prefix",
        "bind_start_timestamp_ns",
        "commit_return_timestamp_ns",
        "publication_timestamp_ns",
    },
    "terminal_success": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "expected_source_count",
        *_SUMMARY_FIELDS,
    },
    "terminal_failure": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "failed_source_sequence",
        "failure_code",
        "error_class",
        *_SUMMARY_FIELDS,
    },
}


class MStarPipelineError(ValueError):
    """Sanitized M* pipeline or evidence contract error."""


def _fail(code: str) -> MStarPipelineError:
    return MStarPipelineError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _timestamp(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_or_legacy_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


@dataclass(frozen=True)
class MStarSpec:
    run_id: str
    production_core_identity_sha256: str
    prepare_concurrency: int
    method: str = MSTAR
    require_prepare_overlap: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or _RUN_ID.fullmatch(self.run_id) is None:
            raise _fail("run_id_invalid")
        if self.method != MSTAR:
            raise _fail("legacy_method_identity_forbidden")
        _sha(self.production_core_identity_sha256, "production_core_identity_invalid")
        if self.prepare_concurrency != 2:
            raise _fail("prepare_concurrency_must_be_two")
        if not isinstance(self.require_prepare_overlap, bool):
            raise _fail("require_prepare_overlap_invalid")


@dataclass(frozen=True)
class MStarSource:
    source_sequence: int
    source_sha256: str
    opaque_source: object
    logical_time_ns: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 0
        ):
            raise _fail("source_sequence_invalid")
        _sha(self.source_sha256, "source_identity_invalid")
        if self.opaque_source is None:
            raise _fail("opaque_source_missing")
        if self.logical_time_ns is not None:
            _timestamp(self.logical_time_ns, "logical_time_invalid")


SemanticPrepare = Callable[[object, int], Awaitable[object]]
LatestStateBind = Callable[[object, int, int, tuple[int, ...]], Awaitable[object]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]
RecoverPublication = Callable[[MStarSource, int], Awaitable[object]]


@dataclass(frozen=True)
class _PrepareOutcome:
    source: MStarSource
    logical_time_ns: int
    prepared: object | None
    error: BaseException | None


class _Ledger:
    def __init__(self, persist_event: PersistEvent) -> None:
        if not callable(persist_event):
            raise _fail("persist_event_not_callable")
        self._persist_event = persist_event
        self._events: list[dict[str, object]] = []
        self._lock = asyncio.Lock()

    @property
    def events(self) -> list[dict[str, object]]:
        return deepcopy(self._events)

    async def emit(self, event_type: str, **fields: object) -> dict[str, object]:
        async with self._lock:
            event = {
                "event_sequence": len(self._events),
                "event_type": event_type,
                **fields,
            }
            if set(event) != _EVENT_FIELDS.get(event_type):
                raise _fail("event_shape_invalid")
            _assert_public(event)
            try:
                result = self._persist_event(deepcopy(event))
                if not inspect.isawaitable(result):
                    raise TypeError("persist_event must be async")
                await result
            except MStarPipelineError:
                raise
            except Exception:
                raise _fail("durable_evidence_unavailable") from None
            self._events.append(event)
            return deepcopy(event)


def _qualified_error_class(error: BaseException) -> str:
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _validate_inputs(
    *,
    spec: MStarSpec,
    sources: Sequence[MStarSource],
    semantic_prepare: SemanticPrepare,
    latest_state_bind: LatestStateBind,
    clock_ns: ClockNs,
) -> tuple[MStarSource, ...]:
    if not isinstance(spec, MStarSpec):
        raise _fail("spec_invalid")
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise _fail("sources_invalid")
    selected = tuple(sources)
    if not selected or any(not isinstance(item, MStarSource) for item in selected):
        raise _fail("sources_invalid")
    if [item.source_sequence for item in selected] != list(range(len(selected))):
        raise _fail("source_sequence_not_contiguous")
    if not callable(semantic_prepare) or not callable(latest_state_bind):
        raise _fail("callback_not_callable")
    if not callable(clock_ns):
        raise _fail("clock_not_callable")
    return selected


def _intervals(events: Sequence[Mapping[str, object]], start_type: str, end_type: str) -> list[tuple[int, int, int, int]]:
    starts = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == start_type
    }
    ends = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == end_type
    }
    result: list[tuple[int, int, int, int]] = []
    for source, start in starts.items():
        if source not in ends:
            continue
        start_ns = _timestamp(
            start.get("prepare_start_timestamp_ns" if start_type == "prepare_start" else "bind_start_timestamp_ns"),
            "interval_timestamp_invalid",
        )
        end_ns = _timestamp(
            ends[source].get("prepare_end_timestamp_ns" if end_type == "prepared" else "publication_timestamp_ns"),
            "interval_timestamp_invalid",
        )
        worker = int(start.get("worker_id", 0))
        result.append((start_ns, end_ns, worker, source))
    return result


def _max_active(intervals: Sequence[tuple[int, int, int, int]]) -> int:
    points = sorted({point for start, end, _worker, _source in intervals for point in (start, end)})
    return max(
        (sum(start <= point < end for start, end, _worker, _source in intervals) for point in points),
        default=0,
    )


def _overlap(intervals: Sequence[tuple[int, int, int, int]]) -> bool:
    for index, (left_start, left_end, left_worker, _left_source) in enumerate(intervals):
        for right_start, right_end, right_worker, _right_source in intervals[index + 1 :]:
            if (
                left_worker != right_worker
                and left_start < right_end
                and right_start < left_end
            ):
                return True
    return False


async def _await_outcome(
    future: asyncio.Future[_PrepareOutcome],
    poison: asyncio.Event,
) -> _PrepareOutcome | None:
    poison_task = asyncio.create_task(poison.wait())
    done, pending = await asyncio.wait(
        {future, poison_task}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    if future in done:
        return future.result()
    return None


async def run_mstar_pipeline(
    *,
    spec: MStarSpec,
    sources: Sequence[MStarSource],
    semantic_prepare: SemanticPrepare,
    latest_state_bind: LatestStateBind,
    persist_event: PersistEvent,
    clock_ns: ClockNs,
    recover_publication: RecoverPublication | None = None,
) -> dict[str, object]:
    """Run two-worker prepare, one source-ordered latest-state bind, and publish."""

    selected = _validate_inputs(
        spec=spec,
        sources=sources,
        semantic_prepare=semantic_prepare,
        latest_state_bind=latest_state_bind,
        clock_ns=clock_ns,
    )
    ledger = _Ledger(persist_event)
    source_by_sequence = {item.source_sequence: item for item in selected}
    outcomes: dict[int, asyncio.Future[_PrepareOutcome]] = {
        item.source_sequence: asyncio.get_running_loop().create_future()
        for item in selected
    }
    queue: asyncio.Queue[MStarSource | None] = asyncio.Queue()
    for item in selected:
        queue.put_nowait(item)
    logical_times: dict[int, int] = {}
    observed_workers: set[int] = set()
    poison = asyncio.Event()
    first_failure: dict[str, object] = {}
    durability_failure: MStarPipelineError | None = None
    published: list[int] = []
    prepared_count = 0

    for item in selected:
        logical_time = (
            item.logical_time_ns
            if item.logical_time_ns is not None
            else _timestamp(clock_ns(), "clock_timestamp_invalid")
        )
        logical_times[item.source_sequence] = logical_time
        await ledger.emit(
            "intent",
            run_id=spec.run_id,
            method=spec.method,
            source_sequence=item.source_sequence,
            source_sha256=item.source_sha256,
            logical_time_ns=logical_time,
        )

    async def set_failure(source: MStarSource, code: str, error: BaseException) -> None:
        if not first_failure:
            first_failure.update(
                {
                    "failed_source_sequence": source.source_sequence,
                    "failure_code": code,
                    "error_class": _qualified_error_class(error),
                }
            )
        poison.set()

    async def prepare_worker(worker_id: int) -> None:
        nonlocal durability_failure, prepared_count
        while not poison.is_set():
            source = await queue.get()
            if source is None:
                queue.task_done()
                return
            observed_workers.add(worker_id)
            sequence = source.source_sequence
            start = _timestamp(clock_ns(), "clock_timestamp_invalid")
            try:
                await ledger.emit(
                    "prepare_start",
                    run_id=spec.run_id,
                    method=spec.method,
                    source_sequence=sequence,
                    source_sha256=source.source_sha256,
                    worker_id=worker_id,
                    logical_time_ns=logical_times[sequence],
                    prepare_start_timestamp_ns=start,
                )
            except MStarPipelineError as error:
                durability_failure = error
                poison.set()
                queue.task_done()
                return
            if poison.is_set():
                queue.task_done()
                return
            try:
                value = semantic_prepare(source.opaque_source, logical_times[sequence])
                if not inspect.isawaitable(value):
                    raise TypeError("semantic_prepare must be async")
                prepared_value = await value
            except asyncio.CancelledError:
                queue.task_done()
                raise
            except Exception as error:
                await set_failure(source, "SEMANTIC_PREPARE_FAILED", error)
                if not outcomes[sequence].done():
                    outcomes[sequence].set_result(
                        _PrepareOutcome(source, logical_times[sequence], None, error)
                    )
                queue.task_done()
                return
            end = _timestamp(clock_ns(), "clock_timestamp_invalid")
            if end < start:
                error = _fail("clock_moved_backwards")
                await set_failure(source, "SEMANTIC_PREPARE_FAILED", error)
                outcomes[sequence].set_result(
                    _PrepareOutcome(source, logical_times[sequence], None, error)
                )
                queue.task_done()
                return
            try:
                await ledger.emit(
                    "prepared",
                    run_id=spec.run_id,
                    method=spec.method,
                    source_sequence=sequence,
                    source_sha256=source.source_sha256,
                    worker_id=worker_id,
                    logical_time_ns=logical_times[sequence],
                    prepare_start_timestamp_ns=start,
                    prepare_end_timestamp_ns=end,
                )
            except MStarPipelineError as error:
                durability_failure = error
                poison.set()
                queue.task_done()
                return
            prepared_count += 1
            outcomes[sequence].set_result(
                _PrepareOutcome(source, logical_times[sequence], prepared_value, None)
            )
            queue.task_done()

    workers = [
        asyncio.create_task(prepare_worker(worker_id), name=f"s5-mstar-prepare-{worker_id}")
        for worker_id in range(spec.prepare_concurrency)
    ]

    async def cancel_workers() -> None:
        for worker in workers:
            if not worker.done():
                worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    try:
        for sequence in range(len(selected)):
            outcome = await _await_outcome(outcomes[sequence], poison)
            if outcome is None:
                break
            if outcome.error is not None:
                break
            if first_failure:
                break
            bind_start = _timestamp(clock_ns(), "clock_timestamp_invalid")
            visible_prefix = tuple(published)
            await ledger.emit(
                "bind_start",
                run_id=spec.run_id,
                method=spec.method,
                source_sequence=sequence,
                source_sha256=outcome.source.source_sha256,
                logical_time_ns=outcome.logical_time_ns,
                visible_publication_prefix=list(visible_prefix),
                bind_start_timestamp_ns=bind_start,
            )
            try:
                value = latest_state_bind(
                    outcome.prepared,
                    outcome.logical_time_ns,
                    sequence,
                    visible_prefix,
                )
                if not inspect.isawaitable(value):
                    raise TypeError("latest_state_bind must be async")
                await value
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await set_failure(
                    outcome.source, "LATEST_STATE_BIND_FAILED", error
                )
                break
            commit_return = _timestamp(clock_ns(), "clock_timestamp_invalid")
            await ledger.emit(
                "commit_returned",
                run_id=spec.run_id,
                method=spec.method,
                source_sequence=sequence,
                source_sha256=outcome.source.source_sha256,
                logical_time_ns=outcome.logical_time_ns,
                bind_start_timestamp_ns=bind_start,
                commit_return_timestamp_ns=commit_return,
            )
            publication = _timestamp(clock_ns(), "clock_timestamp_invalid")
            try:
                await ledger.emit(
                    "publication",
                    run_id=spec.run_id,
                    method=spec.method,
                    source_sequence=sequence,
                    source_sha256=outcome.source.source_sha256,
                    logical_time_ns=outcome.logical_time_ns,
                    visible_publication_prefix=list(visible_prefix),
                    bind_start_timestamp_ns=bind_start,
                    commit_return_timestamp_ns=commit_return,
                    publication_timestamp_ns=publication,
                )
            except MStarPipelineError:
                if recover_publication is None:
                    raise
                recovered = recover_publication(outcome.source, outcome.logical_time_ns)
                if not inspect.isawaitable(recovered):
                    raise _fail("publication_recovery_must_be_async")
                await recovered
                retry_publication = _timestamp(
                    clock_ns(), "clock_timestamp_invalid"
                )
                await ledger.emit(
                    "publication",
                    run_id=spec.run_id,
                    method=spec.method,
                    source_sequence=sequence,
                    source_sha256=outcome.source.source_sha256,
                    logical_time_ns=outcome.logical_time_ns,
                    visible_publication_prefix=list(visible_prefix),
                    bind_start_timestamp_ns=bind_start,
                    commit_return_timestamp_ns=commit_return,
                    publication_timestamp_ns=retry_publication,
                )
            published.append(sequence)
        await cancel_workers()
    except asyncio.CancelledError:
        await cancel_workers()
        raise

    if durability_failure is not None:
        raise durability_failure

    summary = _summary_from_events(
        ledger.events,
        configured_prepare_concurrency=spec.prepare_concurrency,
        observed_prepare_worker_ids=sorted(observed_workers),
        prepared_count=prepared_count,
        published_source_sequences=published,
    )
    if not first_failure and len(published) == len(selected):
        await ledger.emit(
            "terminal_success",
            run_id=spec.run_id,
            method=spec.method,
            expected_source_count=len(selected),
            **summary,
        )
        status = "PASS"
        failure_code = None
    else:
        if not first_failure:
            first_failure.update(
                {
                    "failed_source_sequence": sequence,
                    "failure_code": "PIPELINE_POISONED",
                    "error_class": "paper_eval.s5_mstar_pipeline.MStarPipelineError",
                }
            )
        await ledger.emit(
            "terminal_failure",
            run_id=spec.run_id,
            method=spec.method,
            **first_failure,
            **summary,
        )
        status = "FAIL_CLOSED"
        failure_code = str(first_failure["failure_code"])

    evidence = {
        "schema_version": SCHEMA,
        "run_id": spec.run_id,
        "method": spec.method,
        "production_core_identity_sha256": spec.production_core_identity_sha256,
        "status": status,
        "mergeable": status == "PASS",
        "failure_code": failure_code,
        "events": ledger.events,
        "summary": summary,
    }
    return verify_mstar_pipeline_evidence(
        evidence, expected_spec=spec, expected_sources=selected
    )


def _summary_from_events(
    events: Sequence[Mapping[str, object]],
    *,
    configured_prepare_concurrency: int,
    observed_prepare_worker_ids: Sequence[int],
    prepared_count: int,
    published_source_sequences: Sequence[int],
) -> dict[str, object]:
    prepare_intervals = _intervals(events, "prepare_start", "prepared")
    bind_intervals = _intervals(events, "bind_start", "publication")
    return {
        "configured_prepare_concurrency": configured_prepare_concurrency,
        "observed_prepare_worker_ids": list(observed_prepare_worker_ids),
        "max_active_prepare": _max_active(prepare_intervals),
        "prepare_overlap_observed": _overlap(prepare_intervals),
        "max_active_bind": _max_active(bind_intervals),
        "intent_count": sum(event.get("event_type") == "intent" for event in events),
        "prepared_count": prepared_count,
        "publication_count": len(published_source_sequences),
        "published_source_sequences": list(published_source_sequences),
        "fallback_count": 0,
    }


def verify_mstar_pipeline_evidence(
    value: Mapping[str, object],
    *,
    expected_spec: MStarSpec,
    expected_sources: Sequence[MStarSource],
) -> dict[str, object]:
    """Recompute source, interval, ordering, and terminal-event invariants."""

    if not isinstance(value, Mapping):
        raise _fail("evidence_not_mapping")
    evidence = deepcopy(dict(value))
    _assert_public(evidence)
    if set(evidence) != {
        "schema_version",
        "run_id",
        "method",
        "production_core_identity_sha256",
        "status",
        "mergeable",
        "failure_code",
        "events",
        "summary",
    }:
        raise _fail("evidence_shape_invalid")
    if not isinstance(expected_spec, MStarSpec):
        raise _fail("expected_spec_invalid")
    sources = tuple(expected_sources)
    if not sources or any(not isinstance(item, MStarSource) for item in sources):
        raise _fail("expected_sources_invalid")
    expected_sequences = [item.source_sequence for item in sources]
    expected_hashes = {item.source_sequence: item.source_sha256 for item in sources}
    if (
        evidence.get("schema_version") != SCHEMA
        or evidence.get("run_id") != expected_spec.run_id
        or evidence.get("method") != MSTAR
        or evidence.get("production_core_identity_sha256")
        != expected_spec.production_core_identity_sha256
    ):
        raise _fail("evidence_identity_invalid")
    status = evidence.get("status")
    if status not in {"PASS", "FAIL_CLOSED"}:
        raise _fail("status_invalid")
    if evidence.get("mergeable") is not (status == "PASS"):
        raise _fail("mergeability_invalid")
    if (status == "PASS") != (evidence.get("failure_code") is None):
        raise _fail("failure_status_invalid")
    raw_events = evidence.get("events")
    summary = evidence.get("summary")
    if (
        isinstance(raw_events, (str, bytes))
        or not isinstance(raw_events, Sequence)
        or not isinstance(summary, Mapping)
        or set(summary) != _SUMMARY_FIELDS
    ):
        raise _fail("evidence_sections_invalid")
    events = [dict(item) if isinstance(item, Mapping) else {} for item in raw_events]
    if not events or [event.get("event_sequence") for event in events] != list(range(len(events))):
        raise _fail("event_sequence_invalid")
    for event in events:
        event_type = event.get("event_type")
        if event_type not in _EVENT_FIELDS or set(event) != _EVENT_FIELDS[str(event_type)]:
            raise _fail("event_shape_invalid")
        if event.get("run_id") != expected_spec.run_id or event.get("method") != MSTAR:
            raise _fail("event_identity_invalid")
    terminal_type = "terminal_success" if status == "PASS" else "terminal_failure"
    if events[-1].get("event_type") != terminal_type or sum(
        event.get("event_type") in {"terminal_success", "terminal_failure"}
        for event in events
    ) != 1:
        raise _fail("terminal_event_invalid")
    terminal = events[-1]
    if any(terminal.get(name) != summary.get(name) for name in _SUMMARY_FIELDS):
        raise _fail("terminal_summary_invalid")

    intents = [
        int(event["source_sequence"])
        for event in events
        if event.get("event_type") == "intent"
    ]
    if intents != expected_sequences:
        raise _fail("intent_coverage_invalid")
    for event in events:
        event_type = event.get("event_type")
        if event_type in {"intent", "prepare_start", "prepared", "bind_start", "commit_returned", "publication"}:
            source = event.get("source_sequence")
            if (
                not isinstance(source, int)
                or source not in expected_hashes
                or event.get("source_sha256") != expected_hashes[source]
            ):
                raise _fail("source_identity_invalid")

    intent_by_source = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == "intent"
    }
    for source, intent in intent_by_source.items():
        _timestamp(intent.get("logical_time_ns"), "logical_time_invalid")
        expected_source = next(
            item for item in sources if item.source_sequence == source
        )
        if (
            expected_source.logical_time_ns is not None
            and intent.get("logical_time_ns") != expected_source.logical_time_ns
        ):
            raise _fail("logical_time_source_binding_invalid")
    prepared_rows = [event for event in events if event.get("event_type") == "prepared"]
    prepared_sources = [int(event["source_sequence"]) for event in prepared_rows]
    if len(prepared_sources) != len(set(prepared_sources)) or any(
        source not in expected_hashes for source in prepared_sources
    ):
        raise _fail("prepared_accounting_invalid")
    for event in events:
        if event.get("event_type") in {"prepare_start", "prepared"}:
            source = int(event["source_sequence"])
            if event.get("logical_time_ns") != intent_by_source[source].get("logical_time_ns"):
                raise _fail("logical_time_drift")
            worker = event.get("worker_id")
            if isinstance(worker, bool) or not isinstance(worker, int) or worker not in {0, 1}:
                raise _fail("prepare_worker_invalid")
            _timestamp(
                event.get(
                    "prepare_start_timestamp_ns"
                    if event.get("event_type") == "prepare_start"
                    else "prepare_end_timestamp_ns"
                ),
                "prepare_timestamp_invalid",
            )
    prepare_intervals = _intervals(events, "prepare_start", "prepared")
    if any(end < start for start, end, _worker, _source in prepare_intervals):
        raise _fail("prepare_timestamp_order_invalid")

    bind_starts = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == "bind_start"
    }
    commit_rows = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == "commit_returned"
    }
    publication_rows = [event for event in events if event.get("event_type") == "publication"]
    publications = [int(event["source_sequence"]) for event in publication_rows]
    if publications != list(range(len(publications))):
        raise _fail("publication_source_order_invalid")
    for event in bind_starts.values():
        source = int(event["source_sequence"])
        prefix = event.get("visible_publication_prefix")
        if prefix != list(range(source)) or event.get("logical_time_ns") != intent_by_source[source].get("logical_time_ns"):
            raise _fail("latest_state_prefix_invalid")
        _timestamp(event.get("bind_start_timestamp_ns"), "bind_timestamp_invalid")
    for event in commit_rows.values():
        source = int(event["source_sequence"])
        if source not in bind_starts:
            raise _fail("commit_without_bind_invalid")
        start = _timestamp(bind_starts[source].get("bind_start_timestamp_ns"), "bind_timestamp_invalid")
        returned = _timestamp(event.get("commit_return_timestamp_ns"), "bind_timestamp_invalid")
        if returned < start:
            raise _fail("bind_timestamp_order_invalid")
    for event in publication_rows:
        source = int(event["source_sequence"])
        if source not in commit_rows:
            raise _fail("publication_without_commit_invalid")
        returned = _timestamp(commit_rows[source].get("commit_return_timestamp_ns"), "bind_timestamp_invalid")
        publication = _timestamp(event.get("publication_timestamp_ns"), "bind_timestamp_invalid")
        if publication < returned or event.get("visible_publication_prefix") != list(range(source)):
            raise _fail("publication_timestamp_or_prefix_invalid")

    bind_intervals = _intervals(events, "bind_start", "publication")
    summary_expected = {
        "configured_prepare_concurrency": expected_spec.prepare_concurrency,
        "observed_prepare_worker_ids": sorted({int(event["worker_id"]) for event in events if event.get("event_type") == "prepare_start"}),
        "max_active_prepare": _max_active(prepare_intervals),
        "prepare_overlap_observed": _overlap(prepare_intervals),
        "max_active_bind": _max_active(bind_intervals),
        "intent_count": len(intents),
        "prepared_count": len(prepared_sources),
        "publication_count": len(publications),
        "published_source_sequences": publications,
        "fallback_count": 0,
    }
    if dict(summary) != summary_expected:
        raise _fail("summary_recomputation_invalid")

    if status == "PASS":
        if sorted(prepared_sources) != expected_sequences or publications != expected_sequences:
            raise _fail("successful_coverage_invalid")
        if expected_spec.require_prepare_overlap and (
            summary_expected["observed_prepare_worker_ids"] != [0, 1]
            or summary_expected["max_active_prepare"] != 2
            or summary_expected["prepare_overlap_observed"] is not True
        ):
            raise _fail("prepare_overlap_or_worker_proof_invalid")
        if summary_expected["max_active_prepare"] > expected_spec.prepare_concurrency:
            raise _fail("prepare_concurrency_observed_invalid")
        if summary_expected["max_active_bind"] > 1:
            raise _fail("bind_concurrency_invalid")
    else:
        if terminal.get("failure_code") != evidence.get("failure_code"):
            raise _fail("terminal_failure_code_invalid")
        if terminal.get("failure_code") not in {
            "SEMANTIC_PREPARE_FAILED",
            "LATEST_STATE_BIND_FAILED",
            "PIPELINE_POISONED",
        }:
            raise _fail("failure_code_invalid")
        if not isinstance(terminal.get("error_class"), str):
            raise _fail("failure_error_class_invalid")
    return evidence


__all__ = [
    "MSTAR",
    "MStarPipelineError",
    "MStarSource",
    "MStarSpec",
    "run_mstar_pipeline",
    "verify_mstar_pipeline_evidence",
]
