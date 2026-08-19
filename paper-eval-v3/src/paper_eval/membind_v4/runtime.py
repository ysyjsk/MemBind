"""Validated one-version-ahead NodeResolve runtime for MemBind v4.

The runtime deliberately separates ``speculate`` from ``validate``.  A
speculative provider response is private state; it cannot be interpreted or
committed before an exact predecessor call is available and its fingerprint
matches.  A MISS executes the exact request and commits only that result.
"""

from __future__ import annotations

import inspect
import asyncio
from dataclasses import dataclass
from typing import Callable


class V4RuntimeError(ValueError):
    """The validated speculation state machine failed closed."""


def _fail(code: str) -> V4RuntimeError:
    return V4RuntimeError(code)


async def _await(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


def _call_attr(call: object, name: str, code: str) -> object:
    if not hasattr(call, name):
        raise _fail(code)
    return getattr(call, name)


def _fingerprint(call: object) -> str:
    value = _call_attr(call, "fingerprint", "semantic_call_fingerprint_missing")
    if not isinstance(value, str) or not value:
        raise _fail("semantic_call_fingerprint_invalid")
    return value


@dataclass(frozen=True, slots=True)
class PreparedNodeResolve:
    """A materialized request and its content-safe semantic identity."""

    call: object
    request: object

    def __post_init__(self) -> None:
        _fingerprint(self.call)
        source = _call_attr(self.call, "source_sequence", "source_sequence_missing")
        state = _call_attr(self.call, "state_version", "state_version_missing")
        if isinstance(source, bool) or not isinstance(source, int) or source < 0:
            raise _fail("source_sequence_invalid")
        if isinstance(state, bool) or not isinstance(state, int) or state < 0:
            raise _fail("state_version_invalid")
        mode = getattr(self.call, "execution_mode", "LLM")
        if mode not in {"LLM", "NO_LLM"}:
            raise _fail("execution_mode_invalid")


@dataclass(frozen=True, slots=True)
class NodeResolveOutcome:
    status: str
    source_sequence: int
    speculative_state_version: int
    exact_state_version: int
    speculative_fingerprint: str
    exact_fingerprint: str
    exact_execution_performed: bool
    commit_result: object


@dataclass(frozen=True, slots=True)
class _Speculation:
    prepared: PreparedNodeResolve
    response: object


class ValidatedSpeculationRuntime:
    """One-shot validated speculation state machine.

    Callbacks may be synchronous or async.  ``commit`` is never called by
    :meth:`speculate`; this is the persistent-effect fence for v4.
    """

    def __init__(
        self,
        *,
        execute: Callable[[object], object],
        interpret: Callable[[object, object], object],
        commit: Callable[[object], object],
        observer: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        if not callable(execute) or not callable(interpret) or not callable(commit):
            raise _fail("runtime_callback_invalid")
        if observer is not None and not callable(observer):
            raise _fail("observer_invalid")
        self._execute = execute
        self._interpret = interpret
        self._commit = commit
        self._observer = observer
        self._state = "NEW"
        self._speculation: _Speculation | None = None
        self._outcome: NodeResolveOutcome | None = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def outcome(self) -> NodeResolveOutcome | None:
        return self._outcome

    def _emit(self, event_type: str, **fields: object) -> None:
        if self._observer is not None:
            result = self._observer({"event_type": event_type, **fields})
            # Observers are intentionally synchronous in the small runtime;
            # live callers should use their durable writer around this hook.
            if inspect.isawaitable(result):
                raise _fail("async_observer_unsupported")

    async def _execute_prepared(self, prepared: PreparedNodeResolve) -> object:
        mode = getattr(prepared.call, "execution_mode", "LLM")
        if mode == "NO_LLM":
            self._emit("no_llm_deterministic", source_sequence=prepared.call.source_sequence)
            return prepared.request
        try:
            return await _await(self._execute(prepared.request))
        except ValueError as error:
            # The Graphiti-facing v4 adapter accepts the whole prepared-call
            # object, while lightweight fixtures commonly accept ``request``.
            # Retry only on its preflight shape error; never retry arbitrary
            # provider ValueErrors, which could duplicate a real request.
            if str(error) not in {
                "prepared_call_invalid",
                "exact_call_invalid",
            }:
                raise
            return await _await(self._execute(prepared))

    async def speculate(self, prepared: PreparedNodeResolve) -> None:
        if not isinstance(prepared, PreparedNodeResolve):
            raise _fail("prepared_request_invalid")
        if self._state != "NEW" or self._speculation is not None:
            raise _fail("runtime_not_speculatable")
        self._state = "SPECULATING"
        self._emit(
            "speculation_started",
            source_sequence=prepared.call.source_sequence,
            state_version=prepared.call.state_version,
        )
        try:
            response = await self._execute_prepared(prepared)
        except asyncio.CancelledError:
            self._state = "CANCELLED"
            self._speculation = None
            self._emit("speculation_cancelled")
            raise
        except BaseException:
            self._state = "FAILED"
            self._speculation = None
            self._emit("speculation_failed")
            raise
        self._speculation = _Speculation(prepared, response)
        self._state = "SPECULATED"
        self._emit("speculation_ready")

    async def validate_and_commit(self, exact: PreparedNodeResolve) -> NodeResolveOutcome:
        if not isinstance(exact, PreparedNodeResolve):
            raise _fail("prepared_request_invalid")
        if self._state != "SPECULATED" or self._speculation is None:
            raise _fail("speculation_not_ready")
        stale = self._speculation.prepared
        if stale.call.source_sequence != exact.call.source_sequence:
            raise _fail("source_sequence_mismatch")
        if exact.call.state_version != stale.call.state_version + 1:
            raise _fail("state_order_invalid")
        stale_fp = _fingerprint(stale.call)
        exact_fp = _fingerprint(exact.call)
        stale_mode = getattr(stale.call, "execution_mode", "LLM")
        exact_mode = getattr(exact.call, "execution_mode", "LLM")
        hit = stale_fp == exact_fp and stale_mode == exact_mode
        self._state = "VALIDATING"
        exact_execution = not hit
        try:
            response = self._speculation.response if hit else await self._execute_prepared(exact)
            interpreted = await _await(self._interpret(response, exact.call))
            self._state = "INTERPRETED"
            committed = await _await(self._commit(interpreted))
        except asyncio.CancelledError:
            self._state = "CANCELLED"
            self._speculation = None
            raise
        except BaseException:
            self._state = "FAILED"
            self._speculation = None
            raise
        outcome = NodeResolveOutcome(
            status="HIT" if hit else "MISS",
            source_sequence=exact.call.source_sequence,
            speculative_state_version=stale.call.state_version,
            exact_state_version=exact.call.state_version,
            speculative_fingerprint=stale_fp,
            exact_fingerprint=exact_fp,
            exact_execution_performed=exact_execution,
            commit_result=committed,
        )
        self._outcome = outcome
        self._speculation = None
        self._state = "COMMITTED"
        self._emit(
            "semantic_hit" if hit else "semantic_miss",
            source_sequence=outcome.source_sequence,
            exact_execution_performed=exact_execution,
        )
        return outcome

    async def validate(self, exact: PreparedNodeResolve) -> NodeResolveOutcome:
        """Alias used by adapters that separate validation from commit naming."""

        return await self.validate_and_commit(exact)

    def cancel(self) -> None:
        if self._state in {"NEW", "COMMITTED", "FAILED", "CANCELLED"}:
            return
        self._state = "CANCELLED"
        self._speculation = None
        self._emit("speculation_cancelled")


# Names used by early v4 design notes; aliases keep adapter imports stable.
V4NodeResolveRuntime = ValidatedSpeculationRuntime
NodeResolveV4Runtime = ValidatedSpeculationRuntime
ValidatedNodeResolveRuntime = ValidatedSpeculationRuntime
NodeResolvePrepared = PreparedNodeResolve


__all__ = [
    "NodeResolveOutcome",
    "NodeResolvePrepared",
    "NodeResolveV4Runtime",
    "PreparedNodeResolve",
    "V4NodeResolveRuntime",
    "V4RuntimeError",
    "ValidatedSpeculationRuntime",
    "ValidatedNodeResolveRuntime",
]
