"""Thin scheduling bridge for the lightweight three-baseline suite.

U0 is the direct serial control. A0 and P(C=2) deliberately delegate to the
already qualified S5 scheduler functions; this module only translates their
strict historical ``s5-*`` run identity into the isolated suite identity.
Runtime construction, Graphiti, telemetry, and quality composition live at a
higher layer and are injected through ``native_add_episode``.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from .s5_native_method_adapters import (
    A0,
    P_STAR,
    S5EpisodeRef,
    S5MethodSpec,
    run_a0,
    run_p_c2,
)


U0 = "U0"
P_C2 = "P(C=2)"
BASELINE_METHODS = (U0, A0, P_C2)

NativeAddEpisode = Callable[[object], Awaitable[object]]
PersistEvent = Callable[[dict[str, object]], Awaitable[object]]


def _require_inputs(
    *,
    method: str,
    run_id: str,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
) -> tuple[S5EpisodeRef, ...]:
    if method not in BASELINE_METHODS:
        raise ValueError("baseline suite method is invalid")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 120:
        raise ValueError("baseline suite run_id is invalid")
    selected = tuple(episodes)
    if (
        not selected
        or any(not isinstance(item, S5EpisodeRef) for item in selected)
        or [item.source_sequence for item in selected] != list(range(len(selected)))
    ):
        raise ValueError("baseline suite episodes are invalid")
    if method == P_C2 and len(selected) < 2:
        raise ValueError("P(C=2) requires at least two episodes")
    if not callable(native_add_episode) or not callable(persist_event):
        raise ValueError("baseline suite execution callback is invalid")
    return selected


async def _persist(
    persist_event: PersistEvent,
    event: Mapping[str, object],
) -> None:
    result = persist_event(dict(event))
    if not inspect.isawaitable(result):
        raise TypeError("persist_event must be async")
    await result


def _summary(
    *,
    episode_count: int,
    publication_count: int,
) -> dict[str, object]:
    return {
        "configured_worker_count": 1,
        "observed_worker_ids": [0] if publication_count else [],
        "max_active_calls": 1 if publication_count else 0,
        "whole_update_interval_overlap_observed": False,
        "intent_count": episode_count,
        "caller_return_count": 0,
        "publication_count": publication_count,
    }


async def _run_u0(
    *,
    run_id: str,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
) -> dict[str, object]:
    events: list[dict[str, object]] = []

    async def emit(event_type: str, **fields: object) -> None:
        event = {
            "event_sequence": len(events),
            "event_type": event_type,
            "run_id": run_id,
            "method": U0,
            **fields,
        }
        await _persist(persist_event, event)
        events.append(event)

    publication_count = 0
    for episode in episodes:
        intent = time.monotonic_ns()
        await emit(
            "intent",
            source_sequence=episode.source_sequence,
            source_sha256=episode.source_sha256,
            intent_timestamp_ns=intent,
        )
        service_start = time.monotonic_ns()
        try:
            result = native_add_episode(episode.native_episode)
            if not inspect.isawaitable(result):
                raise TypeError("native_add_episode must be async")
            await result
        except Exception as error:
            summary = _summary(
                episode_count=len(episodes),
                publication_count=publication_count,
            )
            await emit(
                "treatment_failure",
                expected_episode_count=len(episodes),
                failed_source_sequence=episode.source_sequence,
                failure_code="NATIVE_ADD_EPISODE_FAILED",
                error_class=f"{type(error).__module__}.{type(error).__qualname__}",
                **summary,
            )
            return {
                "schema_version": "membind.paper-eval-v3.baseline-suite-schedule.v1",
                "run_id": run_id,
                "method": U0,
                "status": "FAIL_CLOSED",
                "mergeable": False,
                "failure_code": "NATIVE_ADD_EPISODE_FAILED",
                "events": deepcopy(events),
                "summary": summary,
            }
        publication = time.monotonic_ns()
        await emit(
            "publication",
            source_sequence=episode.source_sequence,
            source_sha256=episode.source_sha256,
            worker_id=0,
            service_start_timestamp_ns=service_start,
            publish_timestamp_ns=publication,
            caller_return_timestamp_ns=publication,
            transaction_status="committed",
        )
        publication_count += 1

    summary = _summary(
        episode_count=len(episodes),
        publication_count=publication_count,
    )
    await emit(
        "terminal_success",
        expected_episode_count=len(episodes),
        **summary,
    )
    return {
        "schema_version": "membind.paper-eval-v3.baseline-suite-schedule.v1",
        "run_id": run_id,
        "method": U0,
        "status": "PASS",
        "mergeable": True,
        "failure_code": None,
        "events": deepcopy(events),
        "summary": summary,
    }


def _internal_s5_run_id(run_id: str) -> str:
    # S5's qualified adapter accepts only s5-prefixed identities. The suite
    # keeps its own public identity and projects the verified events back.
    return f"s5-{run_id}"


def _project_s5_evidence(
    evidence: Mapping[str, object],
    *,
    internal_run_id: str,
    public_run_id: str,
    public_method: str,
) -> dict[str, object]:
    projected = deepcopy(dict(evidence))
    if projected.get("run_id") != internal_run_id:
        raise ValueError("S5 evidence run identity mismatch")
    projected["run_id"] = public_run_id
    projected["method"] = public_method
    events = projected.get("events")
    if not isinstance(events, list):
        raise ValueError("S5 evidence events are missing")
    for event in events:
        if not isinstance(event, dict) or event.get("run_id") != internal_run_id:
            raise ValueError("S5 event run identity mismatch")
        event["run_id"] = public_run_id
        event["method"] = public_method
    return projected


async def execute_method_schedule(
    *,
    method: str,
    run_id: str,
    episodes: Sequence[S5EpisodeRef],
    native_add_episode: NativeAddEpisode,
    persist_event: PersistEvent,
) -> dict[str, object]:
    """Execute exactly one baseline scheduler over opaque Native episodes."""

    selected = _require_inputs(
        method=method,
        run_id=run_id,
        episodes=episodes,
        native_add_episode=native_add_episode,
        persist_event=persist_event,
    )
    if method == U0:
        return await _run_u0(
            run_id=run_id,
            episodes=selected,
            native_add_episode=native_add_episode,
            persist_event=persist_event,
        )

    internal_run_id = _internal_s5_run_id(run_id)
    internal_method = A0 if method == A0 else P_STAR
    spec = S5MethodSpec(
        run_id=internal_run_id,
        method=internal_method,
        native_path_identity_sha256="0" * 64,
    )

    async def bridge(event: Mapping[str, object]) -> None:
        if event.get("run_id") != internal_run_id:
            raise ValueError("S5 scheduler emitted an unexpected run identity")
        projected = dict(event)
        projected["run_id"] = run_id
        projected["method"] = method
        await _persist(persist_event, projected)

    runner = run_a0 if method == A0 else run_p_c2
    evidence = await runner(
        spec=spec,
        episodes=selected,
        native_add_episode=native_add_episode,
        persist_event=bridge,
    )
    return _project_s5_evidence(
        evidence,
        internal_run_id=internal_run_id,
        public_run_id=run_id,
        public_method=method,
    )


__all__ = ["BASELINE_METHODS", "P_C2", "U0", "execute_method_schedule"]

