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

from paper_eval.artifacts import atomic_write_json, payload_sha256
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
        "protocol_amendment",
        "a1_binding",
        "publication_durable_count",
        "llm_failed_count",
        "wrong_version_reuse_count",
        "publication_order_violation_count",
        "persistent_speculative_write_count",
        "hidden_critical_time_ns",
        "useful_token_throughput_ratio",
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
    protocol_amendment: str | None = None,
    a1_audit_path: Path | None = None,
    a1_amendment_path: Path | None = None,
) -> dict[str, object]:
    """Run one candidate and durably retain either a summary or failure."""

    if not isinstance(history_id, str) or not history_id:
        raise _fail("history_id_invalid")
    if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
        raise _fail("source_count_invalid")
    if mode not in {"fixture", "live", "blocked"}:
        raise _fail("runner_mode_invalid")
    if source_count == 20:
        if mode == "fixture":
            raise _fail("a1_fixture_not_authorized")
        if protocol_amendment != "A1":
            raise _fail("a1_protocol_amendment_required")
        if a1_audit_path is None or a1_amendment_path is None:
            raise _fail("a1_audit_amendment_required")
        # Fail before creating a candidate namespace even in fixture/blocked
        # mode.  The production runner performs the deeper canonical-plan
        # binding; this lightweight check enforces that both inputs are
        # present and sealed for every execution mode.
        a1_bodies: dict[str, dict[str, object]] = {}
        for label, path in (("audit", a1_audit_path), ("amendment", a1_amendment_path)):
            try:
                body = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise _fail(f"a1_{label}_unreadable") from error
            if not isinstance(body, dict):
                raise _fail(f"a1_{label}_invalid")
            a1_bodies[label] = body
            digest = body.get("payload_sha256")
            unsigned = dict(body)
            unsigned.pop("payload_sha256", None)
            if not isinstance(digest, str) or digest != payload_sha256(unsigned):
                raise _fail(f"a1_{label}_payload_hash_mismatch")
            if body.get("protocol_amendment_id", body.get("amendment_id")) != "A1":
                raise _fail(f"a1_{label}_identity_drift")
            selected_count = body.get(
                "development_source_count",
                body.get("prefix_source_count", body.get("source_count")),
            )
            if label == "amendment" and selected_count != 20:
                raise _fail("a1_amendment_source_count_invalid")
            if label == "audit" and selected_count not in {20, 49}:
                raise _fail("a1_audit_source_count_invalid")
        def bound(body: dict[str, object], *names: str) -> object:
            for name in names:
                if name in body:
                    return body[name]
            nested = body.get("sealed_reference")
            if isinstance(nested, dict):
                for name in names:
                    if name in nested:
                        return nested[name]
            return None
        if bound(a1_bodies["audit"], "history_id") != history_id or bound(
            a1_bodies["amendment"], "history_id"
        ) != history_id:
            raise _fail("a1_history_identity_drift")
        for label, names, code in (
            (
                "arrival_trace",
                ("arrival_trace_sha256", "history_arrival_trace_sha256"),
                "a1_arrival_trace_identity_drift",
            ),
            (
                "source_inventory",
                ("source_inventory_sha256", "source_manifest_sha256"),
                "a1_source_inventory_identity_drift",
            ),
        ):
            left = bound(a1_bodies["audit"], *names)
            right = bound(a1_bodies["amendment"], *names)
            if not isinstance(left, str) or not isinstance(right, str) or left != right:
                raise _fail(code)
    elif protocol_amendment is not None or a1_audit_path is not None or a1_amendment_path is not None:
        raise _fail("a1_protocol_amendment_unexpected")
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
        if source_count == 20 and "a1_binding" not in result:
            # Fixture/blocked-free offline paths still carry the same sealed
            # A1 identity into reduction; no live provider is initialized by
            # this verifier.
            from paper_eval.membind_v4.production_runner import (  # noqa: PLC0415
                verify_a1_protocol_amendment,
            )

            try:
                binding = verify_a1_protocol_amendment(
                    a1_audit_path,  # type: ignore[arg-type]
                    a1_amendment_path,  # type: ignore[arg-type]
                )
            except Exception as error:
                raise _fail(f"a1_sidecar_binding_invalid:{error}") from error
            result = {
                **dict(result),
                "protocol_amendment": "A1",
                "a1_binding": binding,
            }
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
            publication_source_sequences=result.get("publication_source_sequences"),
            publication_durable_count=int(
                result.get("publication_durable_count", source_count) or 0
            ),
            llm_failed_count=int(result.get("llm_failed_count", 0) or 0),
            wrong_version_reuse_count=int(
                result.get("wrong_version_reuse_count", 0) or 0
            ),
            publication_order_violation_count=int(
                result.get("publication_order_violation_count", 0) or 0
            ),
            persistent_speculative_write_count=int(
                result.get("persistent_speculative_write_count", 0) or 0
            ),
            hidden_critical_time_ns=result.get("hidden_critical_time_ns", 0),
            useful_token_throughput_ratio=float(
                result.get("useful_token_throughput_ratio", 1.0) or 1.0
            ),
            protocol_amendment=result.get("protocol_amendment"),
            a1_binding=result.get("a1_binding"),
            result=_public_result(result),
        )
        return {**summary, "result": result}
    except BaseException as error:
        return store.failure(error, **common)


__all__ = ["V4RunnerError", "run_candidate"]
