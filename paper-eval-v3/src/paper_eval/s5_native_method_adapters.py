"""Pure S5 adapters for Native Async-Serial and Whole-Update Parallel C=2.

This module owns scheduling and sanitized evidence only.  The caller injects
the one production construction operation, ``native_add_episode``; the adapter
never constructs Graphiti, a model client, a network client, or a database.
Opaque native episodes are deliberately excluded from returned evidence.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any


SCHEMA = "membind.paper-eval-v3.s5-native-method-evidence.v1"
A0 = "A0"
P_STAR = "P*"
P_STAR_CONCURRENCY = 2

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^s5-[a-z0-9][a-z0-9-]{2,127}$")
_PRIVATE_OR_LEGACY_FIELDS = {
    "answer",
    "api_key",
    "authority",
    "authorization",
    "body",
    "content",
    "credentials",
    "episode",
    "group_id",
    "legacy_authority",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "source",
    "token",
}
_SUMMARY_FIELDS = {
    "configured_worker_count",
    "observed_worker_ids",
    "max_active_calls",
    "whole_update_interval_overlap_observed",
    "intent_count",
    "caller_return_count",
    "publication_count",
}
_EVENT_FIELDS = {
    "intent": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "intent_timestamp_ns",
    },
    "caller_return": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "durable_enqueue_ack_timestamp_ns",
        "caller_return_timestamp_ns",
    },
    "publication": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "worker_id",
        "service_start_timestamp_ns",
        "publish_timestamp_ns",
        "caller_return_timestamp_ns",
        "transaction_status",
    },
    "source_terminal": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "source_sequence",
        "source_sha256",
        "terminal_classification",
        "worker_id",
        "error_class",
        "service_start_timestamp_ns",
        "terminal_timestamp_ns",
    },
    "terminal_success": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "expected_episode_count",
        *_SUMMARY_FIELDS,
    },
    "treatment_failure": {
        "event_sequence",
        "event_type",
        "run_id",
        "method",
        "expected_episode_count",
        "failed_source_sequence",
        "failure_code",
        "error_class",
        *_SUMMARY_FIELDS,
    },
}

_P_STAR_SOURCE_TERMINALS = {
    "PUBLISHED",
    "TREATMENT_FAILED",
    "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
}

NativeAddEpisode = Callable[[object], Awaitable[object]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]


class S5AdapterError(ValueError):
    """A stable, sanitized S5 adapter or evidence-contract error."""


def _fail(code: str) -> S5AdapterError:
    return S5AdapterError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _fail(code)
    return value


def _timestamp(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or key.casefold() in _PRIVATE_OR_LEGACY_FIELDS
            ):
                raise _fail("private_or_legacy_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


@dataclass(frozen=True)
class S5MethodSpec:
    """The complete public method binding; no authority or namespace is accepted."""

    run_id: str
    method: str
    native_path_identity_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or _RUN_ID_RE.fullmatch(self.run_id) is None:
            raise _fail("run_id_invalid")
        if self.method not in {A0, P_STAR}:
            raise _fail("method_invalid")
        _sha(self.native_path_identity_sha256, "native_path_identity_invalid")


@dataclass(frozen=True)
class S5EpisodeRef:
    """Hash-only public identity plus an opaque episode passed only to Native."""

    source_sequence: int
    source_sha256: str
    native_episode: object

    def __post_init__(self) -> None:
        if (
            isinstance(self.source_sequence, bool)
            or not isinstance(self.source_sequence, int)
            or self.source_sequence < 0
        ):
            raise _fail("source_sequence_invalid")
        _sha(self.source_sha256, "episode_source_identity_invalid")
        if self.native_episode is None:
            raise _fail("native_episode_missing")


class _DurableLedger:
    def __init__(self, persist_event: PersistEvent) -> None:
        if not callable(persist_event):
            raise _fail("persist_event_not_callable")
        self._persist_event = persist_event
        self._events: list[dict[str, object]] = []
        self._lock = asyncio.Lock()

    @property
    def events(self) -> list[dict[str, object]]:
        return deepcopy(self._events)

    async def emit(self, value: Mapping[str, object]) -> dict[str, object]:
        async with self._lock:
            event = {"event_sequence": len(self._events), **dict(value)}
            _assert_public(event)
            try:
                persisted = self._persist_event(deepcopy(event))
                if not inspect.isawaitable(persisted):
                    raise TypeError("persist_event must be async")
                await persisted
            except Exception:
                raise _fail("durable_evidence_unavailable") from None
            self._events.append(event)
            return deepcopy(event)


def _validate_inputs(
    *,
    spec: S5MethodSpec,
    expected_method: str,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs,
) -> tuple[S5EpisodeRef, ...]:
    if not isinstance(spec, S5MethodSpec) or spec.method != expected_method:
        raise _fail("method_binding_invalid")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("episodes_invalid")
    selected = tuple(episodes)
    if not selected or any(not isinstance(item, S5EpisodeRef) for item in selected):
        raise _fail("episodes_invalid")
    if [item.source_sequence for item in selected] != list(range(len(selected))):
        raise _fail("source_sequence_not_contiguous")
    if not callable(native_add_episode):
        raise _fail("native_add_episode_not_callable")
    if not callable(clock_ns):
        raise _fail("clock_not_callable")
    return selected


def _qualified_error_class(error: BaseException) -> str:
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _interval_overlap(
    intervals: Sequence[tuple[int, int, int, int]],
) -> bool:
    for index, (left_start, left_end, left_worker, _left_source) in enumerate(intervals):
        for right_start, right_end, right_worker, _right_source in intervals[index + 1 :]:
            if (
                left_worker != right_worker
                and left_start < right_end
                and right_start < left_end
            ):
                return True
    return False


def _peak_active(intervals: Sequence[tuple[int, int, int, int]]) -> int:
    peak = 0
    for timestamp in {start for start, _end, _worker, _source in intervals}:
        peak = max(
            peak,
            sum(
                start <= timestamp < end
                for start, end, _worker, _source in intervals
            ),
        )
    return peak


def _summary(
    *,
    worker_count: int,
    attempted_workers: set[int],
    intervals: Sequence[tuple[int, int, int, int]],
    intent_count: int,
    caller_return_count: int,
    publication_count: int,
) -> dict[str, object]:
    return {
        "configured_worker_count": worker_count,
        "observed_worker_ids": sorted(attempted_workers),
        "max_active_calls": _peak_active(intervals),
        "whole_update_interval_overlap_observed": _interval_overlap(intervals),
        "intent_count": intent_count,
        "caller_return_count": caller_return_count,
        "publication_count": publication_count,
    }


def _result(
    *,
    spec: S5MethodSpec,
    ledger: _DurableLedger,
    status: str,
    failure_code: str | None,
    summary: Mapping[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": SCHEMA,
        "run_id": spec.run_id,
        "method": spec.method,
        "native_path_identity_sha256": spec.native_path_identity_sha256,
        "status": status,
        "mergeable": status in {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"},
        "failure_code": failure_code,
        "events": ledger.events,
        "summary": dict(summary),
    }
    _assert_public(result)
    return result


async def _emit_intents(
    *,
    spec: S5MethodSpec,
    episodes: Sequence[S5EpisodeRef],
    ledger: _DurableLedger,
    clock_ns: ClockNs,
    caller_returns: bool,
) -> dict[int, int]:
    return_timestamps: dict[int, int] = {}
    for episode in episodes:
        intent_timestamp = _timestamp(clock_ns(), "clock_timestamp_invalid")
        await ledger.emit(
            {
                "event_type": "intent",
                "run_id": spec.run_id,
                "method": spec.method,
                "source_sequence": episode.source_sequence,
                "source_sha256": episode.source_sha256,
                "intent_timestamp_ns": intent_timestamp,
            }
        )
        if caller_returns:
            durable_ack = _timestamp(clock_ns(), "clock_timestamp_invalid")
            await ledger.emit(
                {
                    "event_type": "caller_return",
                    "run_id": spec.run_id,
                    "method": spec.method,
                    "source_sequence": episode.source_sequence,
                    "source_sha256": episode.source_sha256,
                    "durable_enqueue_ack_timestamp_ns": durable_ack,
                    "caller_return_timestamp_ns": durable_ack,
                }
            )
            return_timestamps[episode.source_sequence] = durable_ack
    return return_timestamps


async def run_a0(
    *,
    spec: S5MethodSpec,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
    clock_ns: ClockNs = time.monotonic_ns,
) -> dict[str, object]:
    """Run FIFO Async-Serial with durable enqueue acknowledgement."""

    selected = _validate_inputs(
        spec=spec,
        expected_method=A0,
        episodes=episodes,
        native_add_episode=native_add_episode,
        clock_ns=clock_ns,
    )
    ledger = _DurableLedger(persist_event)
    caller_returns = await _emit_intents(
        spec=spec,
        episodes=selected,
        ledger=ledger,
        clock_ns=clock_ns,
        caller_returns=True,
    )
    attempted_workers: set[int] = set()
    intervals: list[tuple[int, int, int, int]] = []
    publication_count = 0
    failure: tuple[S5EpisodeRef, BaseException] | None = None

    # This is the only service loop: A0 cannot create another Native worker.
    for episode in selected:
        attempted_workers.add(0)
        service_start = _timestamp(clock_ns(), "clock_timestamp_invalid")
        try:
            outcome = native_add_episode(episode.native_episode)
            if not inspect.isawaitable(outcome):
                raise TypeError("native_add_episode must be async")
            await outcome
        except Exception as error:
            service_end = _timestamp(clock_ns(), "clock_timestamp_invalid")
            intervals.append((service_start, service_end, 0, episode.source_sequence))
            failure = (episode, error)
            break
        publish = _timestamp(clock_ns(), "clock_timestamp_invalid")
        if publish < service_start:
            raise _fail("clock_moved_backwards")
        intervals.append((service_start, publish, 0, episode.source_sequence))
        await ledger.emit(
            {
                "event_type": "publication",
                "run_id": spec.run_id,
                "method": spec.method,
                "source_sequence": episode.source_sequence,
                "source_sha256": episode.source_sha256,
                "worker_id": 0,
                "service_start_timestamp_ns": service_start,
                "publish_timestamp_ns": publish,
                "caller_return_timestamp_ns": caller_returns[episode.source_sequence],
                "transaction_status": "committed",
            }
        )
        publication_count += 1

    summary = _summary(
        worker_count=1,
        attempted_workers=attempted_workers,
        intervals=intervals,
        intent_count=len(selected),
        caller_return_count=len(caller_returns),
        publication_count=publication_count,
    )
    if failure is not None:
        episode, error = failure
        await ledger.emit(
            {
                "event_type": "treatment_failure",
                "run_id": spec.run_id,
                "method": spec.method,
                "expected_episode_count": len(selected),
                "failed_source_sequence": episode.source_sequence,
                "failure_code": "NATIVE_ADD_EPISODE_FAILED",
                "error_class": _qualified_error_class(error),
                **summary,
            }
        )
        evidence = _result(
            spec=spec,
            ledger=ledger,
            status="FAIL_CLOSED",
            failure_code="NATIVE_ADD_EPISODE_FAILED",
            summary=summary,
        )
    else:
        await ledger.emit(
            {
                "event_type": "terminal_success",
                "run_id": spec.run_id,
                "method": spec.method,
                "expected_episode_count": len(selected),
                **summary,
            }
        )
        evidence = _result(
            spec=spec,
            ledger=ledger,
            status="PASS",
            failure_code=None,
            summary=summary,
        )
    return verify_s5_native_method_evidence(
        evidence, expected_spec=spec, expected_episodes=selected
    )


async def run_p_c2(
    *,
    spec: S5MethodSpec,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
    clock_ns: ClockNs = time.monotonic_ns,
) -> dict[str, object]:
    """Run exactly two whole-update workers and require observed overlap."""

    selected = _validate_inputs(
        spec=spec,
        expected_method=P_STAR,
        episodes=episodes,
        native_add_episode=native_add_episode,
        clock_ns=clock_ns,
    )
    if len(selected) < P_STAR_CONCURRENCY:
        raise _fail("p_c2_requires_at_least_two_episodes")
    ledger = _DurableLedger(persist_event)
    await _emit_intents(
        spec=spec,
        episodes=selected,
        ledger=ledger,
        clock_ns=clock_ns,
        caller_returns=False,
    )
    queue: asyncio.Queue[S5EpisodeRef] = asyncio.Queue()
    for episode in selected:
        queue.put_nowait(episode)
    attempted_workers: set[int] = set()
    intervals: list[tuple[int, int, int, int]] = []
    publication_count = 0
    publication_lock = asyncio.Lock()
    failure_lock = asyncio.Lock()
    stop = asyncio.Event()
    failure: tuple[S5EpisodeRef, int, BaseException] | None = None
    source_terminals: dict[
        int,
        tuple[str, int | None, str | None, int | None, int | None],
    ] = {}

    async def worker(worker_id: int) -> None:
        nonlocal publication_count, failure
        while not stop.is_set():
            try:
                episode = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            attempted_workers.add(worker_id)
            service_start = _timestamp(clock_ns(), "clock_timestamp_invalid")
            error: BaseException | None = None
            try:
                outcome = native_add_episode(episode.native_episode)
                if not inspect.isawaitable(outcome):
                    raise TypeError("native_add_episode must be async")
                await outcome
            except Exception as caught:
                error = caught
            service_end = _timestamp(clock_ns(), "clock_timestamp_invalid")
            if service_end < service_start:
                raise _fail("clock_moved_backwards")
            intervals.append(
                (service_start, service_end, worker_id, episode.source_sequence)
            )
            if error is not None:
                source_terminals[episode.source_sequence] = (
                    "TREATMENT_FAILED",
                    worker_id,
                    _qualified_error_class(error),
                    service_start,
                    service_end,
                )
                async with failure_lock:
                    if failure is None:
                        failure = (episode, worker_id, error)
                        stop.set()
                queue.task_done()
                return
            await ledger.emit(
                {
                    "event_type": "publication",
                    "run_id": spec.run_id,
                    "method": spec.method,
                    "source_sequence": episode.source_sequence,
                    "source_sha256": episode.source_sha256,
                    "worker_id": worker_id,
                    "service_start_timestamp_ns": service_start,
                    "publish_timestamp_ns": service_end,
                    "caller_return_timestamp_ns": service_end,
                    "transaction_status": "committed",
                }
            )
            async with publication_lock:
                publication_count += 1
            source_terminals[episode.source_sequence] = (
                "PUBLISHED",
                worker_id,
                None,
                service_start,
                service_end,
            )
            queue.task_done()

    workers = [
        asyncio.create_task(worker(worker_id), name=f"s5-p-c2-worker-{worker_id}")
        for worker_id in range(P_STAR_CONCURRENCY)
    ]
    await asyncio.gather(*workers)
    if failure is not None:
        for episode in selected:
            source_terminals.setdefault(
                episode.source_sequence,
                (
                    "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
                    None,
                    None,
                    None,
                    None,
                ),
            )
    if set(source_terminals) != {episode.source_sequence for episode in selected}:
        raise _fail("terminal_source_accounting_invalid")
    for episode in selected:
        (
            classification,
            worker_id,
            terminal_error_class,
            service_start,
            terminal_timestamp,
        ) = source_terminals[episode.source_sequence]
        await ledger.emit(
            {
                "event_type": "source_terminal",
                "run_id": spec.run_id,
                "method": spec.method,
                "source_sequence": episode.source_sequence,
                "source_sha256": episode.source_sha256,
                "terminal_classification": classification,
                "worker_id": worker_id,
                "error_class": terminal_error_class,
                "service_start_timestamp_ns": service_start,
                "terminal_timestamp_ns": terminal_timestamp,
            }
        )
    summary = _summary(
        worker_count=P_STAR_CONCURRENCY,
        attempted_workers=attempted_workers,
        intervals=intervals,
        intent_count=len(selected),
        caller_return_count=publication_count,
        publication_count=publication_count,
    )

    failure_code: str | None = None
    failed_source_sequence: int | None = None
    error_class: str | None = None
    if failure is not None:
        failed_episode, _worker_id, error = failure
        failure_code = "NATIVE_ADD_EPISODE_FAILED"
        failed_source_sequence = failed_episode.source_sequence
        error_class = _qualified_error_class(error)
    elif (
        summary["max_active_calls"] != P_STAR_CONCURRENCY
        or summary["observed_worker_ids"] != [0, 1]
        or summary["whole_update_interval_overlap_observed"] is not True
    ):
        failure_code = "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED"

    if failure_code is not None:
        await ledger.emit(
            {
                "event_type": "treatment_failure",
                "run_id": spec.run_id,
                "method": spec.method,
                "expected_episode_count": len(selected),
                "failed_source_sequence": failed_source_sequence,
                "failure_code": failure_code,
                "error_class": error_class,
                **summary,
            }
        )
        evidence = _result(
            spec=spec,
            ledger=ledger,
            status=(
                "SCIENTIFIC_OUTCOME_COMPLETE"
                if failure_code == "NATIVE_ADD_EPISODE_FAILED"
                else "FAIL_CLOSED"
            ),
            failure_code=failure_code,
            summary=summary,
        )
    else:
        await ledger.emit(
            {
                "event_type": "terminal_success",
                "run_id": spec.run_id,
                "method": spec.method,
                "expected_episode_count": len(selected),
                **summary,
            }
        )
        evidence = _result(
            spec=spec,
            ledger=ledger,
            status="PASS",
            failure_code=None,
            summary=summary,
        )
    return verify_s5_native_method_evidence(
        evidence, expected_spec=spec, expected_episodes=selected
    )


def _event_sources(
    events: Sequence[Mapping[str, object]], event_type: str
) -> list[int]:
    return [
        int(event["source_sequence"])
        for event in events
        if event.get("event_type") == event_type
    ]


def verify_s5_native_method_evidence(
    value: Mapping[str, object],
    *,
    expected_spec: S5MethodSpec,
    expected_episodes: Sequence[S5EpisodeRef],
) -> dict[str, object]:
    """Recompute the complete offline A0/P(C=2) evidence contract."""

    if not isinstance(value, Mapping):
        raise _fail("evidence_not_mapping")
    evidence = deepcopy(dict(value))
    _assert_public(evidence)
    if set(evidence) != {
        "schema_version",
        "run_id",
        "method",
        "native_path_identity_sha256",
        "status",
        "mergeable",
        "failure_code",
        "events",
        "summary",
    }:
        raise _fail("evidence_shape_invalid")
    if not isinstance(expected_spec, S5MethodSpec):
        raise _fail("expected_spec_invalid")
    episodes = tuple(expected_episodes)
    if not episodes or any(not isinstance(item, S5EpisodeRef) for item in episodes):
        raise _fail("expected_episodes_invalid")
    if (
        evidence.get("schema_version") != SCHEMA
        or evidence.get("run_id") != expected_spec.run_id
        or evidence.get("method") != expected_spec.method
        or evidence.get("native_path_identity_sha256")
        != expected_spec.native_path_identity_sha256
    ):
        raise _fail("evidence_identity_invalid")
    status = evidence.get("status")
    failure_code = evidence.get("failure_code")
    if status not in {"PASS", "FAIL_CLOSED", "SCIENTIFIC_OUTCOME_COMPLETE"}:
        raise _fail("status_invalid")
    if evidence.get("mergeable") is not (
        status in {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"}
    ):
        raise _fail("mergeability_invalid")
    if (status == "PASS") != (failure_code is None):
        raise _fail("failure_status_invalid")
    if status == "SCIENTIFIC_OUTCOME_COMPLETE" and expected_spec.method != P_STAR:
        raise _fail("scientific_outcome_method_invalid")

    raw_events = evidence.get("events")
    summary = evidence.get("summary")
    if (
        isinstance(raw_events, (str, bytes))
        or not isinstance(raw_events, Sequence)
        or not isinstance(summary, Mapping)
    ):
        raise _fail("evidence_sections_invalid")
    events = [dict(event) if isinstance(event, Mapping) else {} for event in raw_events]
    if set(summary) != _SUMMARY_FIELDS:
        raise _fail("summary_shape_invalid")
    if not events or [event.get("event_sequence") for event in events] != list(
        range(len(events))
    ):
        raise _fail("event_sequence_invalid")
    if any(
        event.get("event_type") not in _EVENT_FIELDS
        or set(event) != _EVENT_FIELDS[str(event.get("event_type"))]
        for event in events
    ):
        raise _fail("event_shape_invalid")
    if any(
        event.get("run_id") != expected_spec.run_id
        or event.get("method") != expected_spec.method
        for event in events
    ):
        raise _fail("event_identity_invalid")
    terminal_type = "terminal_success" if status == "PASS" else "treatment_failure"
    if events[-1].get("event_type") != terminal_type or sum(
        event.get("event_type") in {"terminal_success", "treatment_failure"}
        for event in events
    ) != 1:
        raise _fail("terminal_event_invalid")
    if any(events[-1].get(name) != summary.get(name) for name in _SUMMARY_FIELDS):
        raise _fail("terminal_summary_invalid")

    expected_sequences = [item.source_sequence for item in episodes]
    expected_hashes = {item.source_sequence: item.source_sha256 for item in episodes}
    intents = _event_sources(events, "intent")
    caller_returns = _event_sources(events, "caller_return")
    publications = _event_sources(events, "publication")
    source_terminals = _event_sources(events, "source_terminal")
    if intents != expected_sequences or len(publications) != len(set(publications)):
        raise _fail("episode_accounting_invalid")
    if any(source not in expected_hashes for source in publications):
        raise _fail("publication_source_invalid")
    intent_by_source = {
        int(event["source_sequence"]): event
        for event in events
        if event.get("event_type") == "intent"
    }
    for event in events:
        event_type = event.get("event_type")
        if event_type in {"intent", "caller_return", "publication", "source_terminal"}:
            source = event.get("source_sequence")
            if (
                not isinstance(source, int)
                or event.get("source_sha256") != expected_hashes.get(source)
            ):
                raise _fail("event_source_identity_invalid")

    source_terminal_rows = [
        event for event in events if event.get("event_type") == "source_terminal"
    ]
    if expected_spec.method == A0:
        if source_terminal_rows:
            raise _fail("a0_source_terminal_forbidden")
    else:
        if (
            source_terminals != expected_sequences
            or len(source_terminals) != len(set(source_terminals))
        ):
            raise _fail("terminal_source_accounting_invalid")
        publication_set = set(publications)
        publication_by_source = {
            int(event["source_sequence"]): event
            for event in events
            if event.get("event_type") == "publication"
        }
        treatment_failed_sources: set[int] = set()
        for event in source_terminal_rows:
            source = int(event["source_sequence"])
            classification = event.get("terminal_classification")
            worker_id = event.get("worker_id")
            terminal_error = event.get("error_class")
            service_start = event.get("service_start_timestamp_ns")
            terminal_timestamp = event.get("terminal_timestamp_ns")
            if classification not in _P_STAR_SOURCE_TERMINALS:
                raise _fail("terminal_source_classification_invalid")
            if classification == "PUBLISHED":
                publication = publication_by_source.get(source, {})
                if (
                    source not in publication_set
                    or isinstance(worker_id, bool)
                    or not isinstance(worker_id, int)
                    or worker_id not in {0, 1}
                    or terminal_error is not None
                    or worker_id != publication.get("worker_id")
                    or service_start
                    != publication.get("service_start_timestamp_ns")
                    or terminal_timestamp != publication.get("publish_timestamp_ns")
                ):
                    raise _fail("published_source_terminal_invalid")
            elif classification == "TREATMENT_FAILED":
                if (
                    source in publication_set
                    or isinstance(worker_id, bool)
                    or not isinstance(worker_id, int)
                    or worker_id not in {0, 1}
                    or not isinstance(terminal_error, str)
                    or not terminal_error
                    or isinstance(service_start, bool)
                    or not isinstance(service_start, int)
                    or service_start < 0
                    or isinstance(terminal_timestamp, bool)
                    or not isinstance(terminal_timestamp, int)
                    or terminal_timestamp < service_start
                ):
                    raise _fail("failed_source_terminal_invalid")
                treatment_failed_sources.add(source)
            elif (
                source in publication_set
                or worker_id is not None
                or terminal_error is not None
                or service_start is not None
                or terminal_timestamp is not None
            ):
                raise _fail("censored_source_terminal_invalid")
        published_terminals = {
            int(event["source_sequence"])
            for event in source_terminal_rows
            if event.get("terminal_classification") == "PUBLISHED"
        }
        if published_terminals != publication_set:
            raise _fail("terminal_publication_accounting_invalid")
        if status == "SCIENTIFIC_OUTCOME_COMPLETE":
            if (
                failure_code != "NATIVE_ADD_EPISODE_FAILED"
                or not treatment_failed_sources
                or events[-1].get("failed_source_sequence")
                not in treatment_failed_sources
            ):
                raise _fail("scientific_outcome_terminal_invalid")
        elif treatment_failed_sources:
            raise _fail("treatment_failure_status_invalid")

    publication_rows = [
        event for event in events if event.get("event_type") == "publication"
    ]
    intervals: list[tuple[int, int, int, int]] = []
    for event in publication_rows:
        start = _timestamp(
            event.get("service_start_timestamp_ns"), "publication_timestamp_invalid"
        )
        end = _timestamp(event.get("publish_timestamp_ns"), "publication_timestamp_invalid")
        worker_id = event.get("worker_id")
        source = int(event["source_sequence"])
        if (
            end < start
            or isinstance(worker_id, bool)
            or not isinstance(worker_id, int)
            or worker_id < 0
            or event.get("transaction_status") != "committed"
        ):
            raise _fail("publication_invalid")
        intervals.append((start, end, worker_id, source))
        if expected_spec.method == P_STAR and event.get(
            "caller_return_timestamp_ns"
        ) != end:
            raise _fail("p_caller_return_invalid")

    calculated = {
        "intent_count": len(intents),
        "caller_return_count": len(caller_returns) if expected_spec.method == A0 else len(publications),
        "publication_count": len(publications),
    }
    if any(summary.get(name) != count for name, count in calculated.items()):
        raise _fail("summary_accounting_invalid")
    configured = 1 if expected_spec.method == A0 else P_STAR_CONCURRENCY
    if summary.get("configured_worker_count") != configured:
        raise _fail("worker_configuration_invalid")
    observed_workers = summary.get("observed_worker_ids")
    if (
        not isinstance(observed_workers, list)
        or any(not isinstance(item, int) or item < 0 or item >= configured for item in observed_workers)
        or observed_workers != sorted(set(observed_workers))
    ):
        raise _fail("observed_workers_invalid")
    if (
        not isinstance(summary.get("max_active_calls"), int)
        or int(summary["max_active_calls"]) < 0
        or int(summary["max_active_calls"]) > configured
    ):
        raise _fail("active_worker_count_invalid")

    if status == "PASS":
        if publications != expected_sequences:
            if expected_spec.method == A0:
                raise _fail("a0_source_order_publication_invalid")
            if Counter(publications) != Counter(expected_sequences):
                raise _fail("p_publication_coverage_invalid")
        if expected_spec.method == A0:
            if caller_returns != expected_sequences:
                raise _fail("a0_caller_return_coverage_invalid")
            returned_by_source = {
                int(event["source_sequence"]): event
                for event in events
                if event.get("event_type") == "caller_return"
            }
            for publication in publication_rows:
                source = int(publication["source_sequence"])
                returned = returned_by_source[source]
                ack = _timestamp(
                    returned.get("durable_enqueue_ack_timestamp_ns"),
                    "a0_caller_return_timestamp_invalid",
                )
                caller_return = _timestamp(
                    returned.get("caller_return_timestamp_ns"),
                    "a0_caller_return_timestamp_invalid",
                )
                if (
                    ack != caller_return
                    or ack
                    < _timestamp(
                        intent_by_source[source].get("intent_timestamp_ns"),
                        "a0_intent_timestamp_invalid",
                    )
                    or publication.get("caller_return_timestamp_ns") != caller_return
                    or caller_return > int(publication["service_start_timestamp_ns"])
                    or publication.get("worker_id") != 0
                ):
                    raise _fail("a0_caller_return_or_worker_invalid")
            if (
                summary.get("observed_worker_ids") != [0]
                or summary.get("max_active_calls") != 1
                or summary.get("whole_update_interval_overlap_observed") is not False
            ):
                raise _fail("a0_single_worker_invalid")
        else:
            overlap = _interval_overlap(intervals)
            peak = _peak_active(intervals)
            workers = sorted({worker for _start, _end, worker, _source in intervals})
            if (
                workers != [0, 1]
                or summary.get("observed_worker_ids") != workers
                or peak != P_STAR_CONCURRENCY
                or summary.get("max_active_calls") != peak
                or overlap is not True
                or summary.get("whole_update_interval_overlap_observed") is not True
            ):
                raise _fail("p_c2_overlap_or_worker_proof_invalid")
    else:
        terminal = events[-1]
        if terminal.get("expected_episode_count") != len(episodes):
            raise _fail("terminal_episode_count_invalid")
        if terminal.get("failure_code") != failure_code:
            raise _fail("terminal_failure_code_invalid")
        if failure_code not in {
            "NATIVE_ADD_EPISODE_FAILED",
            "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED",
        }:
            raise _fail("failure_code_invalid")
        if failure_code == "NATIVE_ADD_EPISODE_FAILED" and not isinstance(
            terminal.get("error_class"), str
        ):
            raise _fail("error_class_invalid")
    return evidence


__all__ = [
    "A0",
    "P_STAR",
    "P_STAR_CONCURRENCY",
    "S5AdapterError",
    "S5EpisodeRef",
    "S5MethodSpec",
    "run_a0",
    "run_p_c2",
    "verify_s5_native_method_evidence",
]
