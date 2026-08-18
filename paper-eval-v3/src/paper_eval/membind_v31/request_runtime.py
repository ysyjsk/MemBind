"""Async transport integration for v3.1 frontier-first request admission."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any

from paper_eval.membind_v31.admission import (
    AdmissionPolicy,
    RequestAdmissionController,
    RequestKind,
    RequestSpec,
)
from paper_eval.membind_v31.prefix_affinity import PrefixMetadata, PrefixProviderIndex


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MemBindV31RequestRuntimeError(ValueError):
    """A request scope, async admission, or observer invariant failed."""


def _fail(code: str) -> MemBindV31RequestRuntimeError:
    return MemBindV31RequestRuntimeError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("source_sequence_invalid")
    return value


def _public_sha256(value: object) -> str | None:
    """Hash JSON-compatible request metadata without retaining its contents."""

    if value is None:
        return None
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _member(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _response_projection(response: object) -> tuple[str | None, int | None, str | None]:
    """Return finish reason, UTF-8 response byte length, and content hash."""

    choices = _member(response, "choices")
    choice = choices[0] if isinstance(choices, (list, tuple)) and choices else None
    finish_reason = _member(choice, "finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        finish_reason = str(finish_reason)
    message = _member(choice, "message")
    content = _member(message, "content")
    if not isinstance(content, str):
        return finish_reason, None, None
    encoded = content.encode("utf-8")
    return finish_reason, len(encoded), hashlib.sha256(encoded).hexdigest()


def _usage_projection(response: object) -> tuple[int | None, int | None, int | None]:
    usage = _member(response, "usage")
    values: list[int | None] = []
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _member(usage, field)
        values.append(value if isinstance(value, int) and not isinstance(value, bool) else None)
    return tuple(values)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class LLMRequestScope:
    kind: RequestKind
    stream_id: str
    source_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RequestKind):
            raise _fail("request_kind_invalid")
        _identity(self.stream_id, "stream_id_invalid")
        _sequence(self.source_sequence)


_SCOPE: contextvars.ContextVar[LLMRequestScope | None] = contextvars.ContextVar(
    "membind_v31_llm_request_scope",
    default=None,
)

# A Graphiti bind is one logical frontier state transition, but its native
# implementation may issue several actual transport calls concurrently.  The
# region identity is inherited by child tasks so request-level K accounting can
# distinguish those calls from a second independent frontier update.
_FRONTIER_GROUP: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "membind_v31_frontier_group",
    default=None,
)


@contextmanager
def llm_request_scope(
    *,
    kind: RequestKind,
    stream_id: str,
    source_sequence: int,
) -> Iterator[LLMRequestScope]:
    """Declare the semantic class inherited by nested Graphiti LLM calls."""

    scope = LLMRequestScope(
        kind=kind,
        stream_id=stream_id,
        source_sequence=source_sequence,
    )
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


class AdmittedLLMClientV31:
    """Wrap actual ``generate_response`` calls with async v3.1 admission."""

    def __init__(
        self,
        *,
        inner: object,
        limit: int,
        policy: AdmissionPolicy,
        request_id_prefix: str,
        observer: Callable[[dict[str, object]], object] | None = None,
        admission_observer: Callable[[dict[str, object]], object] | None = None,
        prefix_encoder: Callable[..., PrefixMetadata],
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if inner is None or not callable(getattr(inner, "generate_response", None)):
            raise _fail("inner_llm_client_invalid")
        prefix = _identity(request_id_prefix, "request_id_prefix_invalid")
        if observer is not None and not callable(observer):
            raise _fail("request_observer_invalid")
        if admission_observer is not None and not callable(admission_observer):
            raise _fail("admission_observer_invalid")
        if not callable(prefix_encoder):
            raise _fail("prefix_encoder_invalid")
        if not callable(clock_ns):
            raise _fail("clock_invalid")
        self._inner = inner
        self._policy = policy
        self._controller = RequestAdmissionController(limit=limit, policy=policy)
        self._prefix = prefix
        self._observer = observer
        self._admission_observer = admission_observer
        self._prefix_encoder = prefix_encoder
        self._clock_ns = clock_ns
        self._last_timestamp_ns = -1
        self._lock = asyncio.Lock()
        self._counter = 0
        self._waiters: dict[str, tuple[RequestSpec, asyncio.Event]] = {}
        self._frontier_bind_regions: set[tuple[str, int]] = set()
        self._events: list[dict[str, object]] = []
        self._admission_events: list[dict[str, object]] = []
        self._prefix_metadata: dict[str, PrefixMetadata] = {}
        self._provider_index: PrefixProviderIndex | None = None
        self._completion_sequence = 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _emit(self, event: dict[str, object]) -> None:
        timestamp_ns = self._clock_ns()
        if (
            isinstance(timestamp_ns, bool)
            or not isinstance(timestamp_ns, int)
            or timestamp_ns < 0
            or timestamp_ns < self._last_timestamp_ns
        ):
            raise _fail("clock_not_monotonic")
        self._last_timestamp_ns = timestamp_ns
        value = {
            "event_sequence": len(self._events),
            "timestamp_ns": timestamp_ns,
            **event,
        }
        self._events.append(value)
        if self._observer is not None:
            try:
                self._observer(dict(value))
            except Exception:
                raise _fail("request_observer_failed") from None

    def _next_request_id_locked(self) -> str:
        value = f"{self._prefix}:{self._counter:08d}"
        self._counter += 1
        return value

    def _dispatch_locked(self) -> None:
        self._refresh_affinity_locked()
        waiting_frontier = any(
            spec.kind is RequestKind.FRONTIER and not event.is_set()
            for spec, event in self._waiters.values()
        )
        if (
            self._policy is AdmissionPolicy.BARRIER
            and self._frontier_bind_regions
            and not waiting_frontier
        ):
            return
        for spec in self._controller.admit_available():
            waiter = self._waiters.get(spec.request_id)
            if waiter is None:
                raise _fail("admitted_request_waiter_missing")
            waiter[1].set()

    def _emit_admission_snapshot_locked(self, reason: str) -> None:
        """Publish permit state without exposing request IDs or prompt data."""

        if self._admission_observer is None:
            return
        if not isinstance(reason, str) or not reason:
            raise _fail("admission_snapshot_reason_invalid")
        active = [
            spec
            for spec, event in self._waiters.values()
            if event.is_set()
        ]
        waiting = [
            spec
            for spec, event in self._waiters.values()
            if not event.is_set()
        ]
        active_frontier_count = sum(
            spec.kind is RequestKind.FRONTIER for spec in active
        )
        waiting_frontier_count = sum(
            spec.kind is RequestKind.FRONTIER for spec in waiting
        )
        if not self._frontier_bind_regions:
            frontier_transport_phase = "OUTSIDE_FRONTIER_REGION"
        elif active_frontier_count and waiting_frontier_count:
            frontier_transport_phase = "FRONTIER_LLM_ACTIVE_WITH_WAITERS"
        elif active_frontier_count:
            frontier_transport_phase = "FRONTIER_LLM_PERMIT_ACTIVE"
        elif waiting_frontier_count:
            frontier_transport_phase = "FRONTIER_WAITING_FOR_LLM_PERMIT"
        else:
            frontier_transport_phase = "FRONTIER_LOCAL_OR_UNINSTRUMENTED"
        snapshot = {
            "schema_version": "membind.paper-eval-v3.membind-v31-admission-state.v1",
            "event_type": "admission_snapshot",
            "event_sequence": len(self._admission_events),
            "reason": reason,
            "timestamp_ns": self._clock_ns(),
            "configured_limit": self._controller.observation()["configured_limit"],
            "active_count": len(active),
            "waiting_count": len(waiting),
            "active_compile_count": sum(spec.kind is RequestKind.COMPILE for spec in active),
            "active_frontier_count": active_frontier_count,
            "waiting_compile_count": sum(spec.kind is RequestKind.COMPILE for spec in waiting),
            "waiting_frontier_count": waiting_frontier_count,
            "frontier_bind_region_count": len(self._frontier_bind_regions),
            "barrier_holds": self._policy is AdmissionPolicy.BARRIER
            and (
                bool(self._frontier_bind_regions)
                or active_frontier_count > 0
                or waiting_frontier_count > 0
            ),
            "frontier_transport_phase": frontier_transport_phase,
            "policy": self._policy.value,
        }
        timestamp_ns = snapshot["timestamp_ns"]
        if (
            isinstance(timestamp_ns, bool)
            or not isinstance(timestamp_ns, int)
            or timestamp_ns < 0
            or timestamp_ns < self._last_timestamp_ns
        ):
            raise _fail("clock_not_monotonic")
        self._last_timestamp_ns = timestamp_ns
        self._admission_events.append(snapshot)
        try:
            self._admission_observer(dict(snapshot))
        except Exception:
            raise _fail("admission_observer_failed") from None

    def _refresh_affinity_locked(self) -> None:
        waiting = [
            (request_id, spec, event, self._prefix_metadata[request_id])
            for request_id, (spec, event) in self._waiters.items()
            if not event.is_set() and spec.kind is RequestKind.COMPILE
        ]
        for request_id, _spec, event, metadata in waiting:
            if self._provider_index is None:
                affinity, recency = 0, 0
            else:
                affinity, recency = self._provider_index.affinity(metadata)
            cohort_gain = sum(
                metadata.aligned_lcp(other_metadata)
                for other_id, _other_spec, _other_event, other_metadata in waiting
                if other_id != request_id
            )
            updated = self._controller.update_waiting_affinity(
                request_id,
                affinity_score=affinity,
                provider_recency=recency,
                cohort_gain=cohort_gain,
                affinity_signature="tok-" + metadata.token_sequence_hmac_sha256,
            )
            self._waiters[request_id] = (updated, event)

    async def _submit(
        self, scope: LLMRequestScope, *, metadata: PrefixMetadata
    ) -> tuple[str, asyncio.Event]:
        async with self._lock:
            if self._provider_index is None:
                self._provider_index = PrefixProviderIndex(
                    prefix_match_unit=metadata.prefix_match_unit
                )
            elif self._provider_index.prefix_match_unit != metadata.prefix_match_unit:
                raise _fail("prefix_match_unit_mismatch")
            request_id = self._next_request_id_locked()
            spec = RequestSpec(
                request_id=request_id,
                kind=scope.kind,
                stream_id=scope.stream_id,
                source_sequence=scope.source_sequence,
                affinity_score=0,
                affinity_signature="tok-" + metadata.token_sequence_hmac_sha256,
                frontier_group=(
                    _FRONTIER_GROUP.get() if scope.kind is RequestKind.FRONTIER else None
                ),
            )
            event = asyncio.Event()
            self._waiters[request_id] = (spec, event)
            self._prefix_metadata[request_id] = metadata
            self._controller.submit(spec)
            self._emit(
                {
                    "event_type": "llm_request_submitted",
                    "request_id": request_id,
                    "request_kind": scope.kind.value,
                    "stream_id": scope.stream_id,
                    "source_sequence": scope.source_sequence,
                    **metadata.public_projection(),
                }
            )
            self._dispatch_locked()
            self._emit_admission_snapshot_locked("SUBMIT_DISPATCH")
            return request_id, event

    async def _terminal(
        self,
        request_id: str,
        *,
        error: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        async with self._lock:
            if cancelled:
                status = self._controller.cancel(request_id)
                if status == "CANCELLATION_REQUESTED":
                    self._controller.finish(request_id, outcome="cancelled")
                terminal = "cancelled"
            elif error is not None:
                self._controller.fail(request_id, error)
                terminal = "error"
            else:
                self._controller.finish(request_id)
                terminal = "ok"
                metadata = self._prefix_metadata[request_id]
                if self._provider_index is None:
                    raise _fail("prefix_provider_index_missing")
                self._provider_index.register_completed(
                    metadata,
                    completion_sequence=self._completion_sequence,
                )
                self._completion_sequence += 1
            self._waiters.pop(request_id, None)
            self._prefix_metadata.pop(request_id, None)
            self._emit(
                {
                    "event_type": "llm_request_terminal",
                    "request_id": request_id,
                    "status": terminal,
                    "error_class": None
                    if error is None
                    else f"{type(error).__module__}.{type(error).__qualname__}",
                }
            )
            self._dispatch_locked()
            self._emit_admission_snapshot_locked("TERMINAL_DISPATCH")

    async def _execute(
        self,
        operation: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        scope = _SCOPE.get()
        if scope is None:
            raise _fail("llm_request_scope_missing")
        if not callable(operation):
            raise _fail("request_operation_invalid")
        try:
            metadata = self._prefix_encoder(*args, **kwargs)
            if not isinstance(metadata, PrefixMetadata):
                raise TypeError("prefix encoder must return PrefixMetadata")
            metadata.verify()
        except Exception:
            raise _fail("prefix_metadata_failed") from None
        request_id, admitted = await self._submit(scope, metadata=metadata)
        try:
            await admitted.wait()
            self._emit(
                {
                    "event_type": "llm_request_start",
                    "request_id": request_id,
                    "request_kind": scope.kind.value,
                    "stream_id": scope.stream_id,
                    "source_sequence": scope.source_sequence,
                    "affinity_score": self._waiters[request_id][0].affinity_score,
                    "provider_recency": self._waiters[request_id][0].provider_recency,
                    "cohort_gain": self._waiters[request_id][0].cohort_gain,
                    "eligible_prefix_tokens": self._waiters[request_id][0].affinity_score,
                }
            )
            result = operation(*args, **kwargs)
            if not hasattr(result, "__await__"):
                raise TypeError("admitted operation must be async")
            selected = await result
        except asyncio.CancelledError:
            await asyncio.shield(self._terminal(request_id, cancelled=True))
            raise
        except Exception as error:
            await self._terminal(request_id, error=error)
            raise
        await self._terminal(request_id)
        return selected

    async def generate_response(self, *args: object, **kwargs: object) -> object:
        """Compatibility gate for tests and non-OpenAI LLM clients."""

        return await self._execute(self._inner.generate_response, *args, **kwargs)

    async def execute_transport(
        self,
        operation: Callable[..., object],
        *args: object,
        **kwargs: object,
    ) -> object:
        """Admit exactly one actual OpenAI-compatible transport attempt."""

        return await self._execute(operation, *args, **kwargs)

    @asynccontextmanager
    async def frontier_bind_region(
        self,
        stream_id: str,
        source_sequence: int,
    ) -> AsyncIterator[None]:
        """Expose Barrier's whole-Bind admission boundary without preemption."""

        key = (
            _identity(stream_id, "stream_id_invalid"),
            _sequence(source_sequence),
        )
        async with self._lock:
            if self._frontier_bind_regions:
                raise _fail("frontier_bind_region_busy")
            self._frontier_bind_regions.add(key)
            self._emit(
                {
                    "event_type": "frontier_bind_region_start",
                    "stream_id": key[0],
                    "source_sequence": key[1],
                }
            )
            self._emit_admission_snapshot_locked("FRONTIER_REGION_START")
            group_token = _FRONTIER_GROUP.set(f"{key[0]}:{key[1]}")
        try:
            yield
        finally:
            async with self._lock:
                self._frontier_bind_regions.discard(key)
                self._emit(
                    {
                        "event_type": "frontier_bind_region_end",
                        "stream_id": key[0],
                        "source_sequence": key[1],
                    }
                )
                self._dispatch_locked()
                self._emit_admission_snapshot_locked("FRONTIER_REGION_END")
            _FRONTIER_GROUP.reset(group_token)

    def observation(self) -> dict[str, object]:
        return {
            **self._controller.observation(),
            "frontier_bind_region_count": len(self._frontier_bind_regions),
            "completed_prefix_provider_count": 0
            if self._provider_index is None
            else self._provider_index.completion_count,
        }

    @property
    def public_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)

    @property
    def admission_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._admission_events)


class AdmittedChatCompletionsV31:
    """Install one v3.1 gate at the real ``chat.completions.create`` boundary."""

    def __init__(
        self,
        *,
        inner: object,
        admission: AdmittedLLMClientV31,
        response_observer: Callable[[dict[str, object]], object] | None = None,
        structured_backend_identity: str | None = None,
    ) -> None:
        if inner is None or not callable(getattr(inner, "create", None)):
            raise _fail("chat_completions_transport_invalid")
        if not isinstance(admission, AdmittedLLMClientV31):
            raise _fail("transport_admission_invalid")
        if response_observer is not None and not callable(response_observer):
            raise _fail("response_observer_invalid")
        if structured_backend_identity is not None and (
            not isinstance(structured_backend_identity, str)
            or not structured_backend_identity
        ):
            raise _fail("structured_backend_identity_invalid")
        self._inner = inner
        self._admission = admission
        self._response_observer = response_observer
        self._structured_backend_identity = structured_backend_identity
        self._response_events: list[dict[str, object]] = []
        self._transport_attempt_index = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: object, **kwargs: object) -> object:
        # Incrementing before the await gives each transport attempt a stable,
        # content-free ordinal even when several Graphiti calls overlap.
        attempt_index = self._transport_attempt_index
        self._transport_attempt_index += 1
        response = await self._admission.execute_transport(
            self._inner.create, *args, **kwargs
        )
        scope = _SCOPE.get()
        response_format = kwargs.get("response_format")
        schema = None
        if isinstance(response_format, Mapping):
            json_schema = response_format.get("json_schema")
            if isinstance(json_schema, Mapping):
                schema = json_schema.get("schema")
        requested_max_tokens = kwargs.get("max_tokens")
        if isinstance(requested_max_tokens, bool) or not isinstance(
            requested_max_tokens, int
        ):
            requested_max_tokens = None
        effective_max_tokens = requested_max_tokens
        if effective_max_tokens is None:
            alternate_max_tokens = kwargs.get("max_completion_tokens")
            if isinstance(alternate_max_tokens, int) and not isinstance(
                alternate_max_tokens, bool
            ):
                effective_max_tokens = alternate_max_tokens
        retry_index = kwargs.get("retry_index", kwargs.get("attempt_index"))
        if isinstance(retry_index, bool) or not isinstance(retry_index, int) or retry_index < 0:
            retry_index = None
        finish_reason, response_byte_length, response_sha256 = _response_projection(
            response
        )
        prompt_tokens, completion_tokens, total_tokens = _usage_projection(response)
        event: dict[str, object] = {
            "schema_version": "membind.paper-eval-v3.transport-response.v1",
            "event_type": "llm_transport_response",
            "transport_attempt_index": attempt_index,
            "retry_index": retry_index,
            "request_kind": None if scope is None else scope.kind.value,
            "stream_id": None if scope is None else scope.stream_id,
            "source_sequence": None if scope is None else scope.source_sequence,
            "requested_max_tokens": requested_max_tokens,
            "effective_max_tokens": effective_max_tokens,
            "response_format_sha256": _public_sha256(response_format),
            "json_schema_sha256": _public_sha256(schema),
            "response_byte_length": response_byte_length,
            "response_sha256": response_sha256,
            "finish_reason": finish_reason,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "structured_backend_identity": self._structured_backend_identity,
        }
        self._response_events.append(event)
        if self._response_observer is not None:
            try:
                self._response_observer(dict(event))
            except Exception:
                raise _fail("response_observer_failed") from None
        return response

    @property
    def public_response_events(self) -> tuple[dict[str, object], ...]:
        """Return content-free response telemetry for durable artifact writers."""

        return tuple(dict(event) for event in self._response_events)


__all__ = [
    "AdmittedLLMClientV31",
    "AdmittedChatCompletionsV31",
    "LLMRequestScope",
    "MemBindV31RequestRuntimeError",
    "llm_request_scope",
]
