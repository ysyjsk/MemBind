"""Isolated async prototype for validated NodeResolve speculation.

This module models the runtime boundary needed by a future Graphiti adapter.
It deliberately owns no Graphiti client, database driver, scheduler, or
artifact store.  A speculative response remains in memory and cannot reach
``interpret`` or ``commit`` until an exact-predecessor SemanticCall has been
materialized and validated.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from paper_eval.membind_v31.node_resolve_speculation import (
    NodeResolveSpeculationError,
    SemanticCall,
    validate_speculation,
)


class ValidatedNodeResolveError(ValueError):
    """The prototype state machine or exact-state validation failed closed."""


def _fail(code: str) -> ValidatedNodeResolveError:
    return ValidatedNodeResolveError(code)


async def _await_if_needed(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True, slots=True)
class NodeResolvePrepared:
    """One materialized request and its content-free semantic identity."""

    call: SemanticCall
    request: object

    def __post_init__(self) -> None:
        if not isinstance(self.call, SemanticCall):
            raise _fail("semantic_call_invalid")


@dataclass(frozen=True, slots=True)
class NodeResolveOutcome:
    """Terminal prototype result; raw provider responses are never exposed."""

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
    prepared: NodeResolvePrepared
    response: object


class ValidatedNodeResolveRuntime:
    """Single-source speculate/validate/commit state machine.

    ``execute`` may contact an LLM, but ``interpret`` and ``commit`` are only
    called from ``validate_and_commit`` after the exact predecessor request is
    available.  A mismatch discards the speculative response and executes the
    exact request.  The class is intentionally one-shot and fail-closed.
    """

    def __init__(
        self,
        *,
        execute: Callable[[object], object],
        interpret: Callable[[object, SemanticCall], object],
        commit: Callable[[object], object],
    ) -> None:
        if not callable(execute) or not callable(interpret) or not callable(commit):
            raise _fail("runtime_callback_invalid")
        self._execute = execute
        self._interpret = interpret
        self._commit = commit
        self._state = "NEW"
        self._speculation: _Speculation | None = None

    @property
    def state(self) -> str:
        return self._state

    async def speculate(self, prepared: NodeResolvePrepared) -> None:
        if not isinstance(prepared, NodeResolvePrepared):
            raise _fail("prepared_request_invalid")
        if self._state == "SPECULATED" or self._speculation is not None:
            raise _fail("speculation_already_present")
        if self._state != "NEW":
            raise _fail("runtime_not_speculatable")
        self._state = "SPECULATING"
        try:
            response = await _await_if_needed(self._execute(prepared.request))
        except BaseException:
            self._state = "FAILED"
            self._speculation = None
            raise
        self._speculation = _Speculation(prepared=prepared, response=response)
        self._state = "SPECULATED"

    async def validate_and_commit(
        self, exact: NodeResolvePrepared
    ) -> NodeResolveOutcome:
        if not isinstance(exact, NodeResolvePrepared):
            raise _fail("prepared_request_invalid")
        if self._state != "SPECULATED" or self._speculation is None:
            raise _fail("speculation_not_ready")
        speculative = self._speculation
        try:
            decision = validate_speculation(speculative.prepared.call, exact.call)
        except NodeResolveSpeculationError as error:
            raise _fail(str(error)) from None

        self._state = "VALIDATING"
        exact_execution = decision.decision != "REUSE"
        try:
            response = (
                await _await_if_needed(self._execute(exact.request))
                if exact_execution
                else speculative.response
            )
            interpreted = await _await_if_needed(
                self._interpret(response, exact.call)
            )
            self._state = "INTERPRETED"
            committed = await _await_if_needed(self._commit(interpreted))
        except BaseException:
            self._state = "FAILED"
            self._speculation = None
            raise

        self._state = "COMMITTED"
        self._speculation = None
        return NodeResolveOutcome(
            status="FALLBACK_EXACT" if exact_execution else "REUSED",
            source_sequence=exact.call.source_sequence,
            speculative_state_version=speculative.prepared.call.state_version,
            exact_state_version=exact.call.state_version,
            speculative_fingerprint=decision.speculative_fingerprint,
            exact_fingerprint=decision.exact_fingerprint,
            exact_execution_performed=exact_execution,
            commit_result=committed,
        )


__all__ = [
    "NodeResolveOutcome",
    "NodeResolvePrepared",
    "ValidatedNodeResolveError",
    "ValidatedNodeResolveRuntime",
]
