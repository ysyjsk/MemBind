"""Opt-in causal telemetry overlay for the real v3.1 Graphiti adapter path.

The overlay is deliberately passive.  It does not choose work, change
admission, alter prompts, read future state, or write the graph.  Without an
explicit observer scope it is inert, preserving the frozen v3.1 event shape.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass


class MSEGObservabilityError(ValueError):
    """An opt-in telemetry context or event violated its contract."""


def _fail(code: str) -> MSEGObservabilityError:
    return MSEGObservabilityError(code)


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EFFECT_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sequence(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _stable_composed_identity(kind: str, *parts: object) -> str:
    raw = ":".join(str(part) for part in parts)
    if _IDENTITY.fullmatch(raw) is not None:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return _identity(f"mseg-{kind}:{digest}", f"{kind}_identity_invalid")


def _effect_identifier(value: object) -> str:
    if not isinstance(value, str) or _EFFECT_IDENTIFIER.fullmatch(value) is None:
        raise _fail("effect_identifier_invalid")
    return value


def _validate_effect_value(value: object) -> object:
    if isinstance(value, str):
        return _effect_identifier(value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        selected: dict[str, object] = {}
        for key, item in value.items():
            selected[_identity(key, "effect_scope_key_invalid")] = _validate_effect_value(
                item
            )
        return selected
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_validate_effect_value(item) for item in value]
    raise _fail("effect_scope_value_invalid")


@dataclass(frozen=True, slots=True)
class MSEGWorkflowContext:
    """The explicit stream/source/phase parent inherited by adapter calls."""

    stream_id: str
    source_sequence: int
    phase: str
    parent_bind_id: str

    def __post_init__(self) -> None:
        _identity(self.stream_id, "stream_id_invalid")
        _sequence(self.source_sequence, "source_sequence_invalid")
        _identity(self.phase, "phase_invalid")
        _identity(self.parent_bind_id, "parent_bind_id_invalid")


@dataclass(frozen=True, slots=True)
class MSEGOperatorContext:
    """Stable identity inherited by every nested LLM request."""

    stream_id: str
    source_sequence: int
    phase: str
    operator_role: str
    operator_id: str
    parent_bind_id: str
    parent_operator_id: str | None

    def __post_init__(self) -> None:
        _identity(self.stream_id, "stream_id_invalid")
        _sequence(self.source_sequence, "source_sequence_invalid")
        _identity(self.phase, "phase_invalid")
        _identity(self.operator_role, "operator_role_invalid")
        _identity(self.operator_id, "operator_id_invalid")
        _identity(self.parent_bind_id, "parent_bind_id_invalid")
        if self.parent_operator_id is not None:
            _identity(self.parent_operator_id, "parent_operator_id_invalid")

    def public_projection(self) -> dict[str, object]:
        return {
            "operator_role": self.operator_role,
            "operator_id": self.operator_id,
            "parent_bind_id": self.parent_bind_id,
            "parent_operator_id": self.parent_operator_id,
            "operator_phase": self.phase,
        }


_WORKFLOW: contextvars.ContextVar[MSEGWorkflowContext | None] = contextvars.ContextVar(
    "membind_v4_mseg_workflow_context",
    default=None,
)
_OPERATOR: contextvars.ContextVar[MSEGOperatorContext | None] = contextvars.ContextVar(
    "membind_v4_mseg_operator_context",
    default=None,
)
_OBSERVER: contextvars.ContextVar["MSEGOperatorTraceObserver | None"] = contextvars.ContextVar(
    "membind_v4_mseg_trace_observer",
    default=None,
)


@contextmanager
def workflow_scope(
    *,
    stream_id: str,
    source_sequence: int,
    phase: str,
) -> Iterator[MSEGWorkflowContext]:
    """Declare the current coordinator stream/source without changing policy."""

    stream = _identity(stream_id, "stream_id_invalid")
    sequence = _sequence(source_sequence, "source_sequence_invalid")
    selected_phase = _identity(phase, "phase_invalid")
    context = MSEGWorkflowContext(
        stream_id=stream,
        source_sequence=sequence,
        phase=selected_phase,
        parent_bind_id=_stable_composed_identity(
            "bind", stream, sequence, selected_phase
        ),
    )
    token = _WORKFLOW.set(context)
    try:
        yield context
    finally:
        _WORKFLOW.reset(token)


@contextmanager
def trace_observer_scope(
    observer: "MSEGOperatorTraceObserver",
) -> Iterator["MSEGOperatorTraceObserver"]:
    if not isinstance(observer, MSEGOperatorTraceObserver):
        raise _fail("trace_observer_invalid")
    token = _OBSERVER.set(observer)
    try:
        yield observer
    finally:
        _OBSERVER.reset(token)


def current_workflow_context() -> MSEGWorkflowContext | None:
    return _WORKFLOW.get()


def current_operator_context() -> MSEGOperatorContext | None:
    return _OPERATOR.get()


def current_operator_metadata() -> dict[str, object]:
    context = current_operator_context()
    return {} if context is None else context.public_projection()


def current_trace_observer() -> "MSEGOperatorTraceObserver | None":
    return _OBSERVER.get()


class MSEGOperatorTraceObserver:
    """Content-safe operator span/effect recorder used only by Q0."""

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        event_observer: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        if not callable(clock_ns):
            raise _fail("clock_invalid")
        if event_observer is not None and not callable(event_observer):
            raise _fail("event_observer_invalid")
        self._clock_ns = clock_ns
        self._event_observer = event_observer
        self._last_timestamp_ns = -1
        self._events: list[dict[str, object]] = []
        self._ordinals: dict[tuple[str, int, str, str], int] = {}

    def _timestamp(self) -> int:
        value = self._clock_ns()
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _fail("clock_invalid")
        if value < self._last_timestamp_ns:
            raise _fail("clock_not_monotonic")
        self._last_timestamp_ns = value
        return value

    def _emit(self, event: dict[str, object], *, timestamp_ns: int | None = None) -> None:
        timestamp = self._timestamp() if timestamp_ns is None else timestamp_ns
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
            or timestamp < self._last_timestamp_ns
        ):
            raise _fail("clock_not_monotonic")
        self._last_timestamp_ns = timestamp
        value = {
            "event_sequence": len(self._events),
            "timestamp_ns": timestamp,
            **event,
        }
        self._events.append(value)
        if self._event_observer is not None:
            try:
                self._event_observer(deepcopy(value))
            except Exception:
                raise _fail("event_observer_failed") from None

    def _context(self, operation: str) -> MSEGOperatorContext:
        workflow = current_workflow_context()
        if workflow is None:
            raise _fail("workflow_scope_missing")
        role = _identity(f"graphiti.{operation}", "operator_role_invalid")
        key = (workflow.stream_id, workflow.source_sequence, workflow.phase, role)
        ordinal = self._ordinals.get(key, 0)
        self._ordinals[key] = ordinal + 1
        operator_id = _stable_composed_identity(
            "op",
            workflow.stream_id,
            workflow.source_sequence,
            workflow.phase,
            operation,
            ordinal,
        )
        parent = current_operator_context()
        return MSEGOperatorContext(
            stream_id=workflow.stream_id,
            source_sequence=workflow.source_sequence,
            phase=workflow.phase,
            operator_role=role,
            operator_id=operator_id,
            parent_bind_id=workflow.parent_bind_id,
            parent_operator_id=None if parent is None else parent.operator_id,
        )

    @contextmanager
    def span(self, operation: str) -> Iterator[MSEGOperatorContext]:
        operation_name = _identity(operation, "operation_invalid")
        context = self._context(operation_name)
        enter_ns = self._timestamp()
        self._emit(
            {
                "event_type": "operator_enter",
                **context.public_projection(),
                "stream_id": context.stream_id,
                "source_sequence": context.source_sequence,
                "operator_ready_ns": None,
                "operator_enter_ns": enter_ns,
                "execution_mode": "ADAPTER_CALL",
            },
            timestamp_ns=enter_ns,
        )
        token = _OPERATOR.set(context)
        try:
            yield context
        except BaseException as error:
            end_ns = self._timestamp()
            self._emit(
                {
                    "event_type": "operator_exit",
                    **context.public_projection(),
                    "stream_id": context.stream_id,
                    "source_sequence": context.source_sequence,
                    "operator_start_ns": enter_ns,
                    "operator_end_ns": end_ns,
                    "operator_status": "ERROR",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                },
                timestamp_ns=end_ns,
            )
            raise
        else:
            end_ns = self._timestamp()
            self._emit(
                {
                    "event_type": "operator_exit",
                    **context.public_projection(),
                    "stream_id": context.stream_id,
                    "source_sequence": context.source_sequence,
                    "operator_start_ns": enter_ns,
                    "operator_end_ns": end_ns,
                    "operator_status": "OK",
                },
                timestamp_ns=end_ns,
            )
        finally:
            _OPERATOR.reset(token)

    def record_effect(
        self,
        context: MSEGOperatorContext,
        *,
        effect_scope: dict[str, object],
        persistent_write: bool,
    ) -> None:
        if not isinstance(context, MSEGOperatorContext):
            raise _fail("operator_context_invalid")
        if not isinstance(effect_scope, dict):
            raise _fail("effect_scope_invalid")
        if not isinstance(persistent_write, bool):
            raise _fail("persistent_write_invalid")
        selected_scope = _validate_effect_value(effect_scope)
        if not isinstance(selected_scope, dict):
            raise _fail("effect_scope_invalid")
        self._emit(
            {
                "event_type": "operator_effect",
                **context.public_projection(),
                "stream_id": context.stream_id,
                "source_sequence": context.source_sequence,
                "effect_scope": selected_scope,
                "effect_scope_complete": True,
                "read_scope": "NOT_OBSERVABLE",
                "read_scope_complete": False,
                "persistent_write": persistent_write,
            }
        )

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(deepcopy(event) for event in self._events)


__all__ = [
    "MSEGObservabilityError",
    "MSEGOperatorContext",
    "MSEGOperatorTraceObserver",
    "MSEGWorkflowContext",
    "current_operator_context",
    "current_operator_metadata",
    "current_trace_observer",
    "current_workflow_context",
    "trace_observer_scope",
    "workflow_scope",
]
