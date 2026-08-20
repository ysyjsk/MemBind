"""Live-safe NodeResolve bridge for the isolated MemBind v4 lane.

The v3.1 production adapter has two deliberately different boundaries:

``prepare(CompileInput) -> PreparedArtifact``
    Immutable, evidence-bounded work.  This is owned by v3.1 and is not
    changed here.

``bind(CompileInput, PreparedArtifact, logical_time_ns=...)``
    Stateful Native continuation.  v4 may call the continuation only after a
    stale NodeResolve response has been paired with an exact predecessor call.

``V4LiveNodeResolveBridge`` is the small adapter between those boundaries.  It
does not import Graphiti, expose a driver, or perform a persistent write.  A
production integration supplies four callbacks that factor the existing
NodeResolve stage into materialisation, transport execution, interpretation,
and Native continuation.  The callbacks are intentionally dependency
injected so this module can be tested without starting Graphiti, vLLM, or
Neo4j.  If the pinned Graphiti version cannot expose this split, the caller
must leave this bridge disabled and continue using v3.1 unchanged.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.node_resolve_adapter import (
    ExactNodeResolveResult,
    NodeResolveV4Adapter,
    PreparedSemanticCall,
)
from paper_eval.membind_v4.semantic_call import SemanticCallDecision


class V4LiveNodeResolveError(ValueError):
    """The live v4 callback or speculative lifecycle failed closed."""


def _fail(code: str) -> V4LiveNodeResolveError:
    return V4LiveNodeResolveError(code)


def graphiti_node_resolve_capability(native_adapter: object) -> dict[str, object]:
    """Describe whether a v3.1 adapter exposes a safe v4 split boundary.

    ``MemBindV31GraphitiAdapter.bind`` is intentionally treated as an opaque
    Native suffix.  A v4 bridge may only be constructed from a separate
    callback surface, supplied either through
    ``v4_node_resolve_callbacks()`` or explicit factory arguments below.
    """

    has_prepare = callable(getattr(native_adapter, "prepare", None))
    has_bind = callable(getattr(native_adapter, "bind", None))
    callback_factory = getattr(native_adapter, "v4_node_resolve_callbacks", None)
    factorized = callable(callback_factory)
    return {
        "schema_version": "membind.paper-eval-v4.graphiti-node-resolve-capability.v1",
        "native_prepare_available": has_prepare,
        "native_bind_available": has_bind,
        "factorized_callback_surface_available": factorized,
        "factorized": bool(has_prepare and has_bind and factorized),
        "reason": (
            "READY"
            if has_prepare and has_bind and factorized
            else "NODE_RESOLVE_FACTORIZATION_UNAVAILABLE"
        ),
    }


def build_v31_graphiti_v4_bridge(
    native_adapter: object,
    *,
    materialize_request: Callable[..., object] | None = None,
    execute_request: Callable[[object], object] | None = None,
    interpret_response: Callable[[object, PreparedSemanticCall], object] | None = None,
    continue_native_bind: Callable[..., object] | None = None,
) -> "V4LiveNodeResolveBridge":
    """Build a v4 bridge only from an explicitly factorized v3.1 surface.

    This factory deliberately does not infer callbacks from the monolithic
    v3.1 ``bind`` implementation.  If every callback is not supplied, the
    adapter may provide a ``v4_node_resolve_callbacks()`` method returning a
    mapping with exactly ``materialize_request``, ``execute_request``,
    ``interpret_response`` and ``continue_native_bind``.  The method is an
    opt-in production extension and is absent from the frozen v3.1 adapter,
    so calling this factory there fails closed before any live work.
    """

    capability = graphiti_node_resolve_capability(native_adapter)
    if not capability["native_prepare_available"] or not capability["native_bind_available"]:
        raise _fail("native_adapter_surface_invalid")
    supplied = {
        "materialize_request": materialize_request,
        "execute_request": execute_request,
        "interpret_response": interpret_response,
        "continue_native_bind": continue_native_bind,
    }
    callback_factory = getattr(native_adapter, "v4_node_resolve_callbacks", None)
    if any(value is None for value in supplied.values()):
        if not callable(callback_factory):
            raise _fail("node_resolve_factorization_unavailable")
        try:
            produced = callback_factory()
        except Exception:
            raise _fail("node_resolve_factorization_unavailable") from None
        if not isinstance(produced, dict):
            raise _fail("node_resolve_callback_surface_invalid")
        for name in supplied:
            if supplied[name] is None:
                supplied[name] = produced.get(name)
    if any(not callable(value) for value in supplied.values()):
        raise _fail("node_resolve_callback_surface_invalid")
    return V4LiveNodeResolveBridge(
        materialize_request=supplied["materialize_request"],  # type: ignore[arg-type]
        execute_request=supplied["execute_request"],  # type: ignore[arg-type]
        interpret_response=supplied["interpret_response"],  # type: ignore[arg-type]
        continue_native_bind=supplied["continue_native_bind"],  # type: ignore[arg-type]
    )


async def _await(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


@dataclass(slots=True)
class _Speculation:
    source_sequence: int
    prepared: PreparedArtifact
    call: PreparedSemanticCall
    task: asyncio.Task[object]
    started_timestamp_ns: int
    timing: dict[str, int | None]


class V4LiveNodeResolveBridge:
    """Run one-version-ahead NodeResolve work without allowing stale writes.

    ``launch_speculation`` starts a transport task and returns while it is in
    flight.  ``bind`` materialises the exact predecessor call, waits for the
    stale task, applies the semantic identity gate, and then invokes
    ``continue_native_bind`` exactly once.  A speculative response can never
    reach the continuation directly.

    Callback contract:

    ``materialize_request(compile_input, prepared, state_version)``
        Read-only request construction.  It returns ``PreparedSemanticCall``,
        ``(SemanticCall, request)``, or a mapping with ``call`` and ``request``.
    ``execute_request(request)``
        The provider call.  It may be sync or async and must not write memory.
    ``interpret_response(response, exact_call)``
        Parse/validate a response into a private result.  It runs only after
        the exact-state gate.
    ``continue_native_bind(compile_input, prepared, result, logical_time_ns=...)``
        The only continuation/persistent-effect boundary.

    Public telemetry deliberately contains counts and semantic fingerprints,
    never prompts, responses, Graphiti objects, or database identifiers.
    """

    def __init__(
        self,
        *,
        materialize_request: Callable[..., object],
        execute_request: Callable[[object], object],
        interpret_response: Callable[[object, PreparedSemanticCall], object],
        continue_native_bind: Callable[..., object],
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        callbacks = (
            (materialize_request, "materialize_callback_invalid"),
            (execute_request, "execute_callback_invalid"),
            (interpret_response, "interpret_callback_invalid"),
            (continue_native_bind, "continue_callback_invalid"),
        )
        for callback, code in callbacks:
            if not callable(callback):
                raise _fail(code)
        if not callable(clock_ns):
            raise _fail("clock_invalid")
        self._adapter = NodeResolveV4Adapter(
            materialize_request=materialize_request,
            execute_request=execute_request,
            interpret_response=interpret_response,
            continue_native_bind=continue_native_bind,
        )
        self._active: dict[int, _Speculation] = {}
        self._events: list[dict[str, object]] = []
        self._hit_count = 0
        self._miss_count = 0
        self._cancelled_count = 0
        self._continuation_count = 0
        self._clock_ns = clock_ns
        self._last_timestamp_ns = -1

    def _timestamp_ns(self) -> int:
        value = self._clock_ns()
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or value < self._last_timestamp_ns
        ):
            raise _fail("clock_not_monotonic")
        self._last_timestamp_ns = value
        return value

    async def _timed_execute(
        self, call: PreparedSemanticCall, timing: dict[str, int | None]
    ) -> object:
        try:
            return await self._adapter.execute(call)
        finally:
            # Record completion even for a failed/cancelled provider call.  A
            # cancelled attempt is never eligible for hidden-time attribution,
            # but the timestamp keeps the public trace diagnosable.
            timing["completed_timestamp_ns"] = self._timestamp_ns()

    @property
    def active_speculation_count(self) -> int:
        """Number of outstanding or completed-but-not-validated tasks."""

        return len(self._active)

    @staticmethod
    def _validate_inputs(
        compile_input: object,
        prepared: object,
        state_version: object,
        *,
        logical_time_ns: object | None = None,
    ) -> PreparedArtifact:
        del compile_input  # The callback owns the compile-input shape.
        if not isinstance(prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            prepared.verify()
        except Exception:
            raise _fail("prepared_artifact_invalid") from None
        if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
            raise _fail("state_version_invalid")
        if logical_time_ns is not None and (
            isinstance(logical_time_ns, bool)
            or not isinstance(logical_time_ns, int)
            or logical_time_ns < 0
        ):
            raise _fail("logical_time_invalid")
        return prepared

    def _emit(self, event_type: str, **fields: object) -> None:
        self._events.append(
            {
                "event_sequence": len(self._events),
                "event_type": event_type,
                **fields,
            }
        )

    async def launch_speculation(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        state_version: int,
    ) -> None:
        """Materialise and launch one stale request in the background."""

        selected = self._validate_inputs(compile_input, prepared, state_version)
        source = selected.source_sequence
        if source in self._active:
            raise _fail("speculation_duplicate")
        try:
            call = await self._adapter.materialize(
                compile_input,
                selected,
                state_version=state_version,
            )
        except V4LiveNodeResolveError:
            raise
        except ValueError as error:
            # Preserve the adapter's stable fail-closed code at the live
            # boundary (for example ``state_version_mismatch``).
            raise _fail(str(error) or "materialize_failed") from None
        except Exception as error:
            raise _fail(f"materialize_failed:{type(error).__qualname__}") from None
        started = self._timestamp_ns()
        timing: dict[str, int | None] = {
            "started_timestamp_ns": started,
            "completed_timestamp_ns": None,
        }
        task = asyncio.create_task(self._timed_execute(call, timing))
        self._active[source] = _Speculation(
            source, selected, call, task, started, timing
        )
        self._emit(
            "speculation_launched",
            source_sequence=source,
            state_version=state_version,
            semantic_call_fingerprint=call.call.fingerprint,
            token_sequence_hmac_sha256=call.call.token_sequence_sha256,
            execution_mode=call.call.execution_mode,
            timestamp_ns=started,
        )
        # Give the event loop one turn so a real transport starts while the
        # frontier continuation is running.  Do not await the task here.
        try:
            await asyncio.sleep(0)
        except asyncio.CancelledError:
            self._active.pop(source, None)
            if not task.done():
                task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
            if result and isinstance(result[0], asyncio.CancelledError):
                self._cancelled_count += 1
                self._emit("speculation_cancelled", source_sequence=source)
            raise

    async def bind(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        state_version: int,
        logical_time_ns: int,
    ) -> object:
        """Validate stale work, then continue Native Bind exactly once."""

        selected = self._validate_inputs(
            compile_input,
            prepared,
            state_version,
            logical_time_ns=logical_time_ns,
        )
        source = selected.source_sequence
        speculation = self._active.get(source)
        try:
            exact = await self._adapter.materialize(
                compile_input,
                selected,
                state_version=state_version,
            )
            exact_ready = self._timestamp_ns()
            validation_started = exact_ready
            if speculation is None:
                # A caller may run a frontier without speculation.  It still
                # uses the same exact interpretation and continuation fence.
                response = await self._adapter.execute(exact)
                result = ExactNodeResolveResult(
                    response=response,
                    exact_call=exact,
                    interpreted=await self._adapter.interpret(response, exact),
                    decision=_exact_decision(exact),
                    exact_execution_performed=True,
                )
            else:
                response = await speculation.task
                result = await self._adapter.validate_and_interpret(
                    response,
                    speculation.call,
                    exact,
                )
            validation_completed = self._timestamp_ns()
            started = speculation.started_timestamp_ns if speculation else None
            completed = (
                speculation.timing.get("completed_timestamp_ns")
                if speculation is not None
                else None
            )
            service_span = (
                max(0, completed - started)
                if isinstance(started, int) and isinstance(completed, int)
                else 0
            )
            lead = (
                max(0, exact_ready - started)
                if isinstance(started, int)
                else 0
            )
            hidden = (
                min(service_span, lead)
                if speculation is not None and result.decision.decision == "REUSE"
                else 0
            )
            usage = _response_usage(response)
            if speculation is None:
                event_type = "exact_node_resolve"
            elif result.decision.decision == "REUSE":
                event_type = "semantic_hit"
                self._hit_count += 1
            else:
                event_type = "semantic_miss"
                self._miss_count += 1
            self._emit(
                event_type,
                source_sequence=source,
                speculative_fingerprint=(
                    speculation.call.call.fingerprint if speculation else None
                ),
                exact_fingerprint=exact.call.fingerprint,
                exact_execution_performed=result.exact_execution_performed,
                timestamp_ns=validation_completed,
                exact_ready_timestamp_ns=exact_ready,
                validation_started_timestamp_ns=validation_started,
                validation_completed_timestamp_ns=validation_completed,
                validation_latency_ns=max(0, validation_completed - validation_started),
                speculation_started_timestamp_ns=started,
                speculation_completed_timestamp_ns=completed,
                speculation_service_span_ns=service_span,
                speculation_lead_time_ns=lead,
                hidden_critical_time_ns=hidden,
                token_sequence_hmac_sha256=exact.call.token_sequence_sha256,
                # A MISS is validated against the exact successor call, but
                # its provider trace belongs to the speculative call.  Keep
                # both identities so post-run accounting can bind waste
                # tokens to the request that actually consumed them.
                speculative_token_sequence_hmac_sha256=(
                    speculation.call.call.token_sequence_sha256
                    if speculation is not None
                    else None
                ),
                execution_mode=exact.call.execution_mode,
                prompt_tokens=usage[0],
                completion_tokens=usage[1],
                total_tokens=usage[2],
            )
            continued = await self._adapter.continue_native_bind(
                compile_input,
                selected,
                result,
                logical_time_ns=logical_time_ns,
            )
            self._continuation_count += 1
            self._emit("native_continuation", source_sequence=source)
            return continued
        except asyncio.CancelledError:
            raise
        except V4LiveNodeResolveError:
            raise
        except ValueError as error:
            raise _fail(str(error) or "live_adapter_failed") from None
        finally:
            if speculation is not None and self._active.get(source) is speculation:
                self._active.pop(source, None)

    def record_overlap(
        self,
        *,
        source_sequence: int,
        frontier_source_sequence: int,
    ) -> None:
        """Record overlap only while the speculative task is still owned.

        The coordinator-compatible facade calls this after the provider task
        has started and only if the frontier task remains active.  Keeping the
        proof at that boundary avoids inferring overlap from launch counts.
        """

        if (
            isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 0
            or isinstance(frontier_source_sequence, bool)
            or not isinstance(frontier_source_sequence, int)
            or frontier_source_sequence < 0
        ):
            raise _fail("overlap_source_invalid")
        if source_sequence not in self._active:
            raise _fail("overlap_without_speculation")
        if any(
            event.get("event_type") == "speculation_overlap"
            and event.get("source_sequence") == source_sequence
            for event in self._events
        ):
            raise _fail("overlap_duplicate")
        self._emit(
            "speculation_overlap",
            source_sequence=source_sequence,
            frontier_source_sequence=frontier_source_sequence,
        )

    async def cancel(self) -> None:
        """Cancel and await every outstanding speculative task."""

        records = tuple(self._active.values())
        for record in records:
            if not record.task.done():
                record.task.cancel()
        if records:
            results = await asyncio.gather(
                *(record.task for record in records),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, asyncio.CancelledError):
                    self._cancelled_count += 1
                    self._emit("speculation_cancelled")
        self._active.clear()

    def telemetry(self) -> dict[str, object]:
        """Return content-safe lifecycle counters and immutable event rows."""

        semantic = tuple(
            event
            for event in self._events
            if event.get("event_type") in {"semantic_hit", "semantic_miss"}
        )
        hits = tuple(event for event in semantic if event["event_type"] == "semantic_hit")
        misses = tuple(event for event in semantic if event["event_type"] == "semantic_miss")

        def total(events: tuple[dict[str, object], ...], field: str) -> int:
            return sum(
                int(event[field])
                for event in events
                if isinstance(event.get(field), int)
                and not isinstance(event.get(field), bool)
            )

        return {
            "schema_version": "membind.paper-eval-v4.live-node-resolve-telemetry.v1",
            "active_speculation_count": len(self._active),
            "speculation_launched_count": sum(
                event["event_type"] == "speculation_launched" for event in self._events
            ),
            "speculation_cancelled_count": self._cancelled_count,
            "semantic_hit_count": self._hit_count,
            "semantic_miss_count": self._miss_count,
            "qualified_node_resolve_count": self._hit_count + self._miss_count,
            "exact_validation_completed_count": self._hit_count + self._miss_count,
            "hidden_critical_time_ns": total(hits, "hidden_critical_time_ns"),
            "weighted_hit_service_time_ns": total(hits, "speculation_service_span_ns"),
            "speculation_lead_time_ns": total(hits, "speculation_lead_time_ns")
            + total(misses, "speculation_lead_time_ns"),
            "validation_latency_ns": total(semantic, "validation_latency_ns"),
            "miss_prompt_tokens": total(misses, "prompt_tokens"),
            "miss_completion_tokens": total(misses, "completion_tokens"),
            "miss_service_span_ns": total(misses, "speculation_service_span_ns"),
            "native_continuation_count": self._continuation_count,
            "persistent_write_count": self._adapter.persistent_write_count,
            "events": tuple(dict(event) for event in self._events),
        }

    def to_artifact(self, *, stream_id: str, status: str = "PASS") -> dict[str, object]:
        """Build the public bridge artifact without raw request content."""

        if not isinstance(stream_id, str) or not stream_id:
            raise _fail("stream_id_invalid")
        if status not in {"PASS", "FAILED_NON_MERGEABLE"}:
            raise _fail("status_invalid")
        telemetry = self.telemetry()
        body: dict[str, object] = {
            "schema_version": "membind.paper-eval-v4.live-node-resolve-artifact.v1",
            "status": status,
            "stream_id": stream_id,
            "semantic_hit_count": telemetry["semantic_hit_count"],
            "semantic_miss_count": telemetry["semantic_miss_count"],
            "speculation_launched_count": telemetry["speculation_launched_count"],
            "speculation_cancelled_count": telemetry["speculation_cancelled_count"],
            "native_continuation_count": telemetry["native_continuation_count"],
            "persistent_write_count": telemetry["persistent_write_count"],
            "event_count": len(self._events),
            "events": telemetry["events"],
        }
        body["payload_sha256"] = payload_sha256(body)
        return body

    artifact = to_artifact


def _exact_decision(call: PreparedSemanticCall) -> SemanticCallDecision:
    """Construct the explicit no-speculation decision for a frontier call."""

    return SemanticCallDecision(
        decision="REEXECUTE",
        reason="NO_SPECULATION",
        speculative_fingerprint=call.call.fingerprint,
        exact_fingerprint=call.call.fingerprint,
    )


def _response_usage(response: object) -> tuple[int | None, int | None, int | None]:
    """Project provider usage without retaining the response payload."""

    usage = response.get("usage") if isinstance(response, Mapping) else getattr(response, "usage", None)
    values: list[int | None] = []
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(field) if isinstance(usage, Mapping) else getattr(usage, field, None)
        values.append(value if isinstance(value, int) and not isinstance(value, bool) else None)
    return tuple(values)  # type: ignore[return-value]


__all__ = [
    "V4LiveNodeResolveBridge",
    "V4LiveNodeResolveError",
    "build_v31_graphiti_v4_bridge",
    "graphiti_node_resolve_capability",
]
