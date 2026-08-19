"""Candidate runner shared by the v4 CLI and offline integration tests.

The runner has two deliberately explicit modes.  ``fixture`` exercises the
complete admission/runtime/ordered-publication path without external I/O.
``live`` requires a READY preflight and an injected live callback; it fails
closed rather than silently substituting the fixture path for a formal run.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from paper_eval.artifacts import atomic_write_json
from paper_eval.membind_v4.autoresearch import CandidateStore
from paper_eval.membind_v4.coordinator import run_membind_v4_stream
from paper_eval.membind_v4.runtime import PreparedNodeResolve
from paper_eval.membind_v4.semantic_call import SemanticCall


class V4RunnerError(ValueError):
    """A candidate cannot be admitted or sealed."""


def _fail(code: str) -> V4RunnerError:
    return V4RunnerError(code)


def _sha(index: int) -> str:
    return f"{index:064x}"


@dataclass(frozen=True, slots=True)
class _FixtureCall:
    source_sequence: int
    state_version: int
    fingerprint: str
    execution_mode: str = "LLM"


class _FixtureAdapter:
    """Tiny deterministic adapter used only for the offline runner mode."""

    async def materialize(self, source: object, *, state_version: int) -> PreparedNodeResolve:
        sequence = int(source)
        return PreparedNodeResolve(
            call=_FixtureCall(sequence, state_version, f"fixture-call-{sequence}"),
            request={"source_sequence": sequence, "state_version": state_version},
        )

    async def execute(self, request: object) -> object:
        return {"response": request}

    async def interpret(self, response: object, call: object) -> object:
        return {"response": response, "source_sequence": call.source_sequence}

    async def commit(self, value: object) -> object:
        return value


def _write_event(store: CandidateStore, event: Mapping[str, object]) -> None:
    event_type = event.get("event_type")
    if isinstance(event_type, str):
        fields = {key: value for key, value in event.items() if key != "event_type"}
        store.event(event_type, **fields)


def _public_result(result: Mapping[str, object]) -> dict[str, object]:
    """Retain only the content-safe live-run projection in ``summary.json``."""

    allowed = {
        "schema_version",
        "status",
        "stream_id",
        "source_count",
        "publication_source_sequences",
        "direct_violation_count",
        "performance",
        "telemetry",
        "admission_observation",
        "prior_six_binding",
        "frontier_p95_service_ratio",
        "freshness_p95_ratio",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _persist_live_events(store: CandidateStore, result: Mapping[str, object]) -> None:
    telemetry = result.get("telemetry")
    if not isinstance(telemetry, Mapping):
        return
    events = telemetry.get("events")
    if isinstance(events, (str, bytes)) or not isinstance(events, (list, tuple)):
        return
    for event in events:
        if isinstance(event, Mapping):
            _write_event(store, event)


def _run_fixture(store: CandidateStore, source_count: int) -> dict[str, object]:
    adapter = _FixtureAdapter()
    result = asyncio.run(
        run_membind_v4_stream(
            stream_id=f"fixture-{store.candidate_id}",
            sources=tuple(range(source_count)),
            adapter=adapter,
            observer=lambda event: _write_event(store, event),
        )
    )
    telemetry = result.get("telemetry")
    if isinstance(telemetry, Mapping):
        # Keep the public candidate ledger event-based, while carrying the
        # aggregate scheduler observation into the sealed summary.
        return {**dict(result), "telemetry": dict(telemetry)}
    return result


def run_candidate(
    *,
    candidate_id: str,
    history_id: str,
    source_count: int,
    output_root: Path,
    mode: str = "live",
    preflight: Mapping[str, object] | None = None,
    live_runner: Callable[..., Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Run one candidate and durably retain either a summary or failure."""

    if not isinstance(history_id, str) or not history_id:
        raise _fail("history_id_invalid")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
        raise _fail("source_count_invalid")
    if mode not in {"fixture", "live", "blocked"}:
        raise _fail("runner_mode_invalid")
    store = CandidateStore.create(Path(output_root), candidate_id, source_count=source_count)
    if preflight is not None:
        atomic_write_json(store.root / "preflight.json", dict(preflight))
    common = {"history_id": history_id, "mode": mode}
    if mode == "blocked":
        classification = str((preflight or {}).get("classification", "SERVICE_PREFLIGHT_BLOCKED"))
        failure = store.failure(
            _fail("service_preflight_blocked"),
            classification=classification,
            **common,
        )
        return failure
    try:
        if mode == "fixture":
            result = _run_fixture(store, source_count)
        else:
            if not isinstance(preflight, Mapping) or preflight.get("status") != "READY":
                raise _fail("service_preflight_not_ready")
            if live_runner is None:
                raise _fail("live_runner_not_configured")
            result = dict(live_runner(store=store, history_id=history_id, source_count=source_count))
            _persist_live_events(store, result)
        status = str(result.get("status", "PASS"))
        performance = result.get("performance")
        performance = performance if isinstance(performance, Mapping) else {}
        summary = store.finalize(
            status="PASS" if status == "PASS" else status,
            history_id=history_id,
            runner_mode=mode,
            direct_violation_count=int(result.get("direct_violation_count", 0) or 0),
            frontier_p95_service_ratio=float(result.get("frontier_p95_service_ratio", 1.0) or 1.0),
            freshness_p95_ratio=float(result.get("freshness_p95_ratio", 1.0) or 1.0),
            makespan_ns=performance.get("makespan_ns"),
            p95_freshness_ns=performance.get("p95_freshness_ns"),
            result=_public_result(result),
        )
        return {**summary, "result": result}
    except BaseException as error:
        return store.failure(error, **common)


__all__ = ["V4RunnerError", "run_candidate"]
