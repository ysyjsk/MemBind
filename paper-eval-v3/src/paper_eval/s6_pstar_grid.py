"""Parameterized whole-update P* scheduler for the S6 calibration grid.

The event and failure semantics deliberately follow the qualified S5 P*(C=2)
adapter.  This additive module changes only the frozen worker count and treats
C=1 as whole-update serial execution, never as A0 async enqueue.
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

from .s5_native_method_adapters import S5EpisodeRef
from .s6_calibration_contract import CONCURRENCIES, DEVELOPMENT_HISTORIES


SCHEMA = "membind.paper-eval-v3.s6-pstar-grid-evidence.v1"
METHOD = "P*"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(
    rf"^s6-({'|'.join(DEVELOPMENT_HISTORIES)})-pstar-c(1|2|4|8)-001$"
)
_PRIVATE_FIELDS = {
    "answer",
    "api_key",
    "authorization",
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
_TERMINAL_CLASSES = {
    "PUBLISHED",
    "TREATMENT_FAILED",
    "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
}

NativeAddEpisode = Callable[[object], Awaitable[object]]
PersistEvent = Callable[[Mapping[str, object]], Awaitable[object]]
ClockNs = Callable[[], int]


class S6PStarError(ValueError):
    """A P* grid binding, durable event, or evidence invariant failed."""


class S6TreatmentFailure(RuntimeError):
    """Explicit marker for a method treatment failure, never infrastructure."""


def _fail(code: str) -> S6PStarError:
    return S6PStarError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _timestamp(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_field_forbidden")
            _public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _public(child)


@dataclass(frozen=True)
class S6PStarSpec:
    run_id: str
    configured_concurrency: int
    execution_identity_sha256: str

    def __post_init__(self) -> None:
        if self.configured_concurrency not in CONCURRENCIES:
            raise _fail("configured_concurrency_invalid")
        match = _RUN_ID.fullmatch(self.run_id) if isinstance(self.run_id, str) else None
        if match is None or int(match.group(2)) != self.configured_concurrency:
            raise _fail("run_id_invalid")
        _sha(self.execution_identity_sha256, "execution_identity_invalid")


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

    async def emit(self, value: Mapping[str, object]) -> None:
        async with self._lock:
            event = {"event_sequence": len(self._events), **dict(value)}
            _public(event)
            try:
                result = self._persist_event(deepcopy(event))
                if not inspect.isawaitable(result):
                    raise TypeError("persist_event must be async")
                await result
            except Exception:
                raise _fail("durable_evidence_unavailable") from None
            self._events.append(event)


def _validate_inputs(
    *,
    spec: S6PStarSpec,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    clock_ns: ClockNs,
) -> tuple[S5EpisodeRef, ...]:
    if not isinstance(spec, S6PStarSpec):
        raise _fail("spec_invalid")
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("episodes_invalid")
    selected = tuple(episodes)
    if (
        not selected
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected] != list(range(len(selected)))
    ):
        raise _fail("episodes_invalid")
    if len(selected) < spec.configured_concurrency:
        raise _fail("insufficient_episodes_for_concurrency")
    if not callable(native_add_episode):
        raise _fail("native_add_episode_not_callable")
    if not callable(clock_ns):
        raise _fail("clock_not_callable")
    return selected


def _qualified_error_class(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _overlap(intervals: Sequence[tuple[int, int, int, int]]) -> bool:
    for index, (left_start, left_end, left_worker, _source) in enumerate(intervals):
        for right_start, right_end, right_worker, _other in intervals[index + 1 :]:
            if (
                left_worker != right_worker
                and left_start < right_end
                and right_start < left_end
            ):
                return True
    return False


def _peak(intervals: Sequence[tuple[int, int, int, int]]) -> int:
    return max(
        (
            sum(start <= timestamp < end for start, end, _worker, _source in intervals)
            for timestamp in {start for start, _end, _worker, _source in intervals}
        ),
        default=0,
    )


def _summary(
    *,
    concurrency: int,
    workers: set[int],
    intervals: Sequence[tuple[int, int, int, int]],
    expected_count: int,
    publication_count: int,
) -> dict[str, object]:
    return {
        "configured_worker_count": concurrency,
        "observed_worker_ids": sorted(workers),
        "max_active_calls": _peak(intervals),
        "whole_update_interval_overlap_observed": _overlap(intervals),
        "intent_count": expected_count,
        "caller_return_count": publication_count,
        "publication_count": publication_count,
    }


def _result(
    *,
    spec: S6PStarSpec,
    ledger: _Ledger,
    status: str,
    failure_code: str | None,
    summary: Mapping[str, object],
) -> dict[str, object]:
    evidence = {
        "schema_version": SCHEMA,
        "run_id": spec.run_id,
        "method": METHOD,
        "configured_concurrency": spec.configured_concurrency,
        "execution_identity_sha256": spec.execution_identity_sha256,
        "status": status,
        "mergeable": status in {"PASS", "SCIENTIFIC_OUTCOME_COMPLETE"},
        "failure_code": failure_code,
        "events": ledger.events,
        "summary": dict(summary),
    }
    _public(evidence)
    return evidence


async def run_s6_pstar(
    *,
    spec: S6PStarSpec,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
    clock_ns: ClockNs = time.monotonic_ns,
) -> dict[str, object]:
    selected = _validate_inputs(
        spec=spec,
        episodes=episodes,
        native_add_episode=native_add_episode,
        clock_ns=clock_ns,
    )
    ledger = _Ledger(persist_event)
    for episode in selected:
        await ledger.emit(
            {
                "event_type": "intent",
                "run_id": spec.run_id,
                "method": METHOD,
                "source_sequence": episode.source_sequence,
                "source_sha256": episode.source_sha256,
                "intent_timestamp_ns": _timestamp(
                    clock_ns(), "clock_timestamp_invalid"
                ),
            }
        )

    queue: asyncio.Queue[S5EpisodeRef] = asyncio.Queue()
    for episode in selected:
        queue.put_nowait(episode)
    workers_seen: set[int] = set()
    intervals: list[tuple[int, int, int, int]] = []
    publication_count = 0
    publication_lock = asyncio.Lock()
    failure_lock = asyncio.Lock()
    stop = asyncio.Event()
    first_failure: tuple[S5EpisodeRef, int, BaseException] | None = None
    terminals: dict[
        int, tuple[str, int | None, str | None, int | None, int | None]
    ] = {}

    async def worker(worker_id: int) -> None:
        nonlocal publication_count, first_failure
        while not stop.is_set():
            try:
                episode = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            workers_seen.add(worker_id)
            service_start = _timestamp(clock_ns(), "clock_timestamp_invalid")
            error: BaseException | None = None
            try:
                outcome = native_add_episode(episode.native_episode)
                if not inspect.isawaitable(outcome):
                    raise TypeError("native_add_episode must be async")
                await outcome
            except S6TreatmentFailure as caught:
                error = caught
            except Exception:
                stop.set()
                queue.task_done()
                raise
            service_end = _timestamp(clock_ns(), "clock_timestamp_invalid")
            if service_end < service_start:
                raise _fail("clock_moved_backwards")
            intervals.append(
                (service_start, service_end, worker_id, episode.source_sequence)
            )
            if error is not None:
                terminals[episode.source_sequence] = (
                    "TREATMENT_FAILED",
                    worker_id,
                    _qualified_error_class(error),
                    service_start,
                    service_end,
                )
                async with failure_lock:
                    if first_failure is None:
                        first_failure = (episode, worker_id, error)
                        stop.set()
                queue.task_done()
                return
            await ledger.emit(
                {
                    "event_type": "publication",
                    "run_id": spec.run_id,
                    "method": METHOD,
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
            terminals[episode.source_sequence] = (
                "PUBLISHED",
                worker_id,
                None,
                service_start,
                service_end,
            )
            queue.task_done()

    tasks = [
        asyncio.create_task(worker(worker_id), name=f"s6-pstar-worker-{worker_id}")
        for worker_id in range(spec.configured_concurrency)
    ]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        stop.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if first_failure is not None:
        for episode in selected:
            terminals.setdefault(
                episode.source_sequence,
                (
                    "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE",
                    None,
                    None,
                    None,
                    None,
                ),
            )
    if set(terminals) != {episode.source_sequence for episode in selected}:
        raise _fail("terminal_source_accounting_invalid")
    for episode in selected:
        classification, worker_id, error_class, start, end = terminals[
            episode.source_sequence
        ]
        await ledger.emit(
            {
                "event_type": "source_terminal",
                "run_id": spec.run_id,
                "method": METHOD,
                "source_sequence": episode.source_sequence,
                "source_sha256": episode.source_sha256,
                "terminal_classification": classification,
                "worker_id": worker_id,
                "error_class": error_class,
                "service_start_timestamp_ns": start,
                "terminal_timestamp_ns": end,
            }
        )
    summary = _summary(
        concurrency=spec.configured_concurrency,
        workers=workers_seen,
        intervals=intervals,
        expected_count=len(selected),
        publication_count=publication_count,
    )

    failure_code: str | None = None
    failed_source: int | None = None
    error_class: str | None = None
    if first_failure is not None:
        episode, _worker_id, error = first_failure
        failure_code = "NATIVE_ADD_EPISODE_FAILED"
        failed_source = episode.source_sequence
        error_class = _qualified_error_class(error)
    else:
        expected_workers = list(range(spec.configured_concurrency))
        if spec.configured_concurrency == 1:
            qualified = (
                summary["observed_worker_ids"] == [0]
                and summary["max_active_calls"] == 1
                and summary["whole_update_interval_overlap_observed"] is False
            )
        else:
            qualified = (
                summary["observed_worker_ids"] == expected_workers
                and summary["max_active_calls"] == spec.configured_concurrency
                and summary["whole_update_interval_overlap_observed"] is True
            )
        if not qualified:
            failure_code = "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED"

    if failure_code is None:
        await ledger.emit(
            {
                "event_type": "terminal_success",
                "run_id": spec.run_id,
                "method": METHOD,
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
    else:
        await ledger.emit(
            {
                "event_type": "treatment_failure",
                "run_id": spec.run_id,
                "method": METHOD,
                "expected_episode_count": len(selected),
                "failed_source_sequence": failed_source,
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
    return verify_s6_pstar_evidence(
        evidence, expected_spec=spec, expected_episodes=selected
    )


def _event_sources(events: Sequence[Mapping[str, object]], kind: str) -> list[int]:
    return [
        int(event["source_sequence"])
        for event in events
        if event.get("event_type") == kind
    ]


def verify_s6_pstar_evidence(
    value: Mapping[str, object],
    *,
    expected_spec: S6PStarSpec,
    expected_episodes: Sequence[S5EpisodeRef],
) -> dict[str, object]:
    if not isinstance(value, Mapping) or not isinstance(expected_spec, S6PStarSpec):
        raise _fail("evidence_or_spec_invalid")
    evidence = deepcopy(dict(value))
    _public(evidence)
    if set(evidence) != {
        "schema_version",
        "run_id",
        "method",
        "configured_concurrency",
        "execution_identity_sha256",
        "status",
        "mergeable",
        "failure_code",
        "events",
        "summary",
    }:
        raise _fail("evidence_shape_invalid")
    episodes = tuple(expected_episodes)
    if (
        not episodes
        or any(not isinstance(item, S5EpisodeRef) for item in episodes)
        or [item.source_sequence for item in episodes] != list(range(len(episodes)))
    ):
        raise _fail("expected_episodes_invalid")
    if (
        evidence.get("schema_version") != SCHEMA
        or evidence.get("run_id") != expected_spec.run_id
        or evidence.get("method") != METHOD
        or evidence.get("configured_concurrency")
        != expected_spec.configured_concurrency
        or evidence.get("execution_identity_sha256")
        != expected_spec.execution_identity_sha256
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
    if status == "SCIENTIFIC_OUTCOME_COMPLETE" and failure_code != "NATIVE_ADD_EPISODE_FAILED":
        raise _fail("scientific_outcome_invalid")
    if status == "FAIL_CLOSED" and failure_code != "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED":
        raise _fail("qualification_failure_invalid")

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
    if not events or [item.get("event_sequence") for item in events] != list(
        range(len(events))
    ):
        raise _fail("event_sequence_invalid")
    if any(
        item.get("event_type") not in _EVENT_FIELDS
        or set(item) != _EVENT_FIELDS[str(item.get("event_type"))]
        or item.get("run_id") != expected_spec.run_id
        or item.get("method") != METHOD
        for item in events
    ):
        raise _fail("event_shape_or_identity_invalid")
    expected_terminal = "terminal_success" if status == "PASS" else "treatment_failure"
    if (
        events[-1].get("event_type") != expected_terminal
        or sum(
            item.get("event_type") in {"terminal_success", "treatment_failure"}
            for item in events
        )
        != 1
        or any(events[-1].get(key) != summary.get(key) for key in _SUMMARY_FIELDS)
        or events[-1].get("expected_episode_count") != len(episodes)
    ):
        raise _fail("terminal_event_invalid")

    expected_sequences = list(range(len(episodes)))
    expected_hashes = {item.source_sequence: item.source_sha256 for item in episodes}
    intents = _event_sources(events, "intent")
    publications = _event_sources(events, "publication")
    terminal_sources = _event_sources(events, "source_terminal")
    if intents != expected_sequences or terminal_sources != expected_sequences:
        raise _fail("terminal_source_accounting_invalid")
    if len(publications) != len(set(publications)):
        raise _fail("publication_duplicate_invalid")
    for event in events:
        if event.get("event_type") in {"intent", "publication", "source_terminal"}:
            source = event.get("source_sequence")
            if (
                isinstance(source, bool)
                or not isinstance(source, int)
                or event.get("source_sha256") != expected_hashes.get(source)
            ):
                raise _fail("event_source_identity_invalid")

    publication_by_source = {
        int(item["source_sequence"]): item
        for item in events
        if item.get("event_type") == "publication"
    }
    terminal_rows = [
        item for item in events if item.get("event_type") == "source_terminal"
    ]
    intervals: list[tuple[int, int, int, int]] = []
    failed_sources: set[int] = set()
    censored_sources: set[int] = set()
    for terminal in terminal_rows:
        source = int(terminal["source_sequence"])
        classification = terminal.get("terminal_classification")
        worker_id = terminal.get("worker_id")
        error_class = terminal.get("error_class")
        start = terminal.get("service_start_timestamp_ns")
        end = terminal.get("terminal_timestamp_ns")
        if classification not in _TERMINAL_CLASSES:
            raise _fail("terminal_classification_invalid")
        if classification == "CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE":
            if any(item is not None for item in (worker_id, error_class, start, end)):
                raise _fail("censored_terminal_invalid")
            censored_sources.add(source)
            continue
        if (
            isinstance(worker_id, bool)
            or not isinstance(worker_id, int)
            or worker_id < 0
            or worker_id >= expected_spec.configured_concurrency
        ):
            raise _fail("terminal_worker_invalid")
        selected_start = _timestamp(start, "terminal_timestamp_invalid")
        selected_end = _timestamp(end, "terminal_timestamp_invalid")
        if selected_end < selected_start:
            raise _fail("terminal_timestamp_invalid")
        intervals.append((selected_start, selected_end, worker_id, source))
        publication = publication_by_source.get(source)
        if classification == "PUBLISHED":
            if (
                publication is None
                or error_class is not None
                or publication.get("worker_id") != worker_id
                or publication.get("service_start_timestamp_ns") != selected_start
                or publication.get("publish_timestamp_ns") != selected_end
                or publication.get("caller_return_timestamp_ns") != selected_end
                or publication.get("transaction_status") != "committed"
            ):
                raise _fail("published_terminal_invalid")
        else:
            if publication is not None or not isinstance(error_class, str) or not error_class:
                raise _fail("failed_terminal_invalid")
            failed_sources.add(source)
    if set(publication_by_source) != {
        int(item["source_sequence"])
        for item in terminal_rows
        if item.get("terminal_classification") == "PUBLISHED"
    }:
        raise _fail("terminal_publication_accounting_invalid")

    calculated_summary = {
        "configured_worker_count": expected_spec.configured_concurrency,
        "observed_worker_ids": sorted(
            {worker for _start, _end, worker, _source in intervals}
        ),
        "max_active_calls": _peak(intervals),
        "whole_update_interval_overlap_observed": _overlap(intervals),
        "intent_count": len(intents),
        "caller_return_count": len(publications),
        "publication_count": len(publications),
    }
    if dict(summary) != calculated_summary:
        raise _fail("summary_recomputation_invalid")

    if status == "PASS":
        if (
            Counter(publications) != Counter(expected_sequences)
            or failed_sources
            or censored_sources
        ):
            raise _fail("pass_coverage_invalid")
        if expected_spec.configured_concurrency == 1:
            if calculated_summary != {
                **calculated_summary,
                "observed_worker_ids": [0],
                "max_active_calls": 1,
                "whole_update_interval_overlap_observed": False,
            }:
                raise _fail("c1_worker_proof_invalid")
        elif (
            calculated_summary["observed_worker_ids"]
            != list(range(expected_spec.configured_concurrency))
            or calculated_summary["max_active_calls"]
            != expected_spec.configured_concurrency
            or calculated_summary["whole_update_interval_overlap_observed"] is not True
        ):
            raise _fail("parallel_worker_proof_invalid")
    elif status == "SCIENTIFIC_OUTCOME_COMPLETE":
        if (
            not failed_sources
            or events[-1].get("failed_source_sequence") not in failed_sources
            or events[-1].get("failure_code") != "NATIVE_ADD_EPISODE_FAILED"
            or not isinstance(events[-1].get("error_class"), str)
        ):
            raise _fail("scientific_failure_accounting_invalid")
    else:
        if (
            failed_sources
            or censored_sources
            or Counter(publications) != Counter(expected_sequences)
            or events[-1].get("failed_source_sequence") is not None
            or events[-1].get("error_class") is not None
            or events[-1].get("failure_code")
            != "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED"
        ):
            raise _fail("overlap_failure_accounting_invalid")
    return evidence


__all__ = [
    "SCHEMA",
    "S6PStarError",
    "S6PStarSpec",
    "S6TreatmentFailure",
    "run_s6_pstar",
    "verify_s6_pstar_evidence",
]
