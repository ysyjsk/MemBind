"""Dependency-injected Graphiti NodeResolve boundary for MemBind v4.

The adapter intentionally has no Graphiti or database import.  Its
``materialize`` callback is a deterministic/read-only boundary and its
``continue_native_bind`` callback is the only place where a caller may hand a
validated result back to the native Bind path.  Speculative responses are
tracked by identity and cannot be interpreted until an exact predecessor call
has validated them.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.semantic_call import (
    SemanticCall,
    SemanticCallDecision,
    SemanticCallError,
    validate_semantic_call_pair,
)


class NodeResolveAdapterError(ValueError):
    """The factorized Graphiti boundary failed closed."""


def _fail(code: str) -> NodeResolveAdapterError:
    return NodeResolveAdapterError(code)


async def _await_if_needed(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True, slots=True)
class PreparedSemanticCall:
    """Materialized request plus content-safe semantic identity."""

    call: SemanticCall
    request: object

    def __post_init__(self) -> None:
        if not isinstance(self.call, SemanticCall):
            raise _fail("semantic_call_invalid")
        try:
            self.call.verify()
        except SemanticCallError as error:
            raise _fail(str(error)) from None


@dataclass(frozen=True, slots=True)
class ExactNodeResolveResult:
    """Interpreted result authorized against an exact predecessor request."""

    response: object
    exact_call: PreparedSemanticCall
    interpreted: object
    decision: SemanticCallDecision
    exact_execution_performed: bool


@runtime_checkable
class NodeResolveV4AdapterProtocol(Protocol):
    async def materialize(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        state_version: int,
    ) -> PreparedSemanticCall: ...

    async def execute(self, call: PreparedSemanticCall) -> object: ...

    async def interpret(self, response: object, exact_call: PreparedSemanticCall) -> object: ...

    async def continue_native_bind(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        node_result: ExactNodeResolveResult,
        *,
        logical_time_ns: int,
    ) -> object: ...


class NodeResolveV4Adapter:
    """Small adapter with explicit read-only materialization and exact gate.

    Callbacks may be synchronous or asynchronous.  The adapter itself never
    receives a database client and therefore has no persistent write path.
    ``persistent_write_count`` is retained as a telemetry assertion for
    offline fixtures and remains zero for every successful operation.
    """

    def __init__(
        self,
        *,
        materialize_request: Callable[..., object] | None = None,
        execute_request: Callable[[object], object] | None = None,
        interpret_response: Callable[[object, PreparedSemanticCall], object] | None = None,
        continue_native_bind: Callable[..., object] | None = None,
    ) -> None:
        for callback, code in (
            (materialize_request, "materialize_callback_invalid"),
            (execute_request, "execute_callback_invalid"),
            (interpret_response, "interpret_callback_invalid"),
            (continue_native_bind, "continue_callback_invalid"),
        ):
            if callback is not None and not callable(callback):
                raise _fail(code)
        self._materialize_request = materialize_request
        self._execute_request = execute_request or (lambda request: request)
        self._interpret_response = interpret_response or (lambda response, _call: response)
        self._continue_native_bind = continue_native_bind or (
            lambda _input, _prepared, result, *, logical_time_ns: result.interpreted
        )
        self._responses: dict[int, PreparedSemanticCall] = {}
        self._validated: set[int] = set()
        self._persistent_write_count = 0

    @property
    def persistent_write_count(self) -> int:
        return self._persistent_write_count

    def assert_no_persistent_writes(self) -> None:
        if self._persistent_write_count != 0:
            raise _fail("speculative_persistent_write")

    async def materialize(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        *,
        state_version: int,
    ) -> PreparedSemanticCall:
        if not isinstance(prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            prepared.verify()
        except Exception as error:
            raise _fail(f"prepared_artifact_invalid:{error}") from None
        if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 0:
            raise _fail("state_version_invalid")
        if self._materialize_request is None:
            raise _fail("materialize_callback_missing")
        # The callback receives only an already verified artifact and compile
        # input.  No storage/driver object is made available by this API.
        produced = await _await_if_needed(
            self._materialize_request(compile_input, prepared, state_version)
        )
        if isinstance(produced, PreparedSemanticCall):
            result = produced
        elif isinstance(produced, Mapping) and isinstance(produced.get("call"), SemanticCall):
            result = PreparedSemanticCall(call=produced["call"], request=produced.get("request"))
        elif isinstance(produced, tuple) and len(produced) == 2 and isinstance(produced[0], SemanticCall):
            result = PreparedSemanticCall(call=produced[0], request=produced[1])
        else:
            raise _fail("materialized_call_invalid")
        if result.call.source_sequence != prepared.source_sequence:
            raise _fail("source_sequence_mismatch")
        if result.call.state_version != state_version:
            raise _fail("state_version_mismatch")
        self.assert_no_persistent_writes()
        return result

    async def execute(self, call: PreparedSemanticCall) -> object:
        if not isinstance(call, PreparedSemanticCall):
            raise _fail("prepared_call_invalid")
        try:
            response = await _await_if_needed(self._execute_request(call.request))
        except BaseException:
            # A provider failure has no interpretation or bind side effects.
            raise
        self._responses[id(response)] = call
        return response

    async def interpret(self, response: object, exact_call: PreparedSemanticCall) -> object:
        if not isinstance(exact_call, PreparedSemanticCall):
            raise _fail("exact_call_invalid")
        owner = self._responses.get(id(response))
        if owner is None:
            raise _fail("response_execution_unknown")
        # A response is directly interpretable only when it came from this
        # exact materialized call object.  A stale response may have the same
        # fingerprint, but still needs the explicit pair-validation gate.
        if owner is not exact_call and id(response) not in self._validated:
            raise _fail("speculative_response_unvalidated")
        return await _await_if_needed(self._interpret_response(response, exact_call))

    async def validate_and_interpret(
        self,
        response: object,
        speculative_call: PreparedSemanticCall,
        exact_call: PreparedSemanticCall,
    ) -> ExactNodeResolveResult:
        """Validate a stale response, falling back to exact execution on MISS."""

        if not isinstance(speculative_call, PreparedSemanticCall) or not isinstance(exact_call, PreparedSemanticCall):
            raise _fail("prepared_call_invalid")
        try:
            decision = validate_semantic_call_pair(speculative_call.call, exact_call.call)
        except SemanticCallError as error:
            raise _fail(str(error)) from None
        exact_execution = decision.decision != "REUSE"
        selected_response = response
        if exact_execution:
            selected_response = await self.execute(exact_call)
        else:
            if id(response) not in self._responses:
                raise _fail("response_execution_unknown")
            self._validated.add(id(response))
        interpreted = await self.interpret(selected_response, exact_call)
        self.assert_no_persistent_writes()
        return ExactNodeResolveResult(
            response=selected_response,
            exact_call=exact_call,
            interpreted=interpreted,
            decision=decision,
            exact_execution_performed=exact_execution,
        )

    async def continue_native_bind(
        self,
        compile_input: object,
        prepared: PreparedArtifact,
        node_result: ExactNodeResolveResult,
        *,
        logical_time_ns: int,
    ) -> object:
        if not isinstance(prepared, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        if not isinstance(node_result, ExactNodeResolveResult):
            raise _fail("node_result_invalid")
        if isinstance(logical_time_ns, bool) or not isinstance(logical_time_ns, int) or logical_time_ns < 0:
            raise _fail("logical_time_invalid")
        self.assert_no_persistent_writes()
        callback = self._continue_native_bind
        try:
            parameters = inspect.signature(callback).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_keyword = "logical_time_ns" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_keyword:
            value = callback(
                compile_input,
                prepared,
                node_result,
                logical_time_ns=logical_time_ns,
            )
        else:
            value = callback(compile_input, prepared, node_result, logical_time_ns)
        result = await _await_if_needed(value)
        return result


def _sync_callback(callback: Callable[..., object], *args: object) -> object:
    value = callback(*args)
    if not inspect.isawaitable(value):
        return value
    try:
        inspect.get_running_loop()
    except RuntimeError:
        import asyncio

        return asyncio.run(value)
    raise NodeResolveAdapterError("parity_callback_async_in_running_loop")


def assert_serial_factorized_parity(
    *,
    native_materialize: Callable[..., object],
    adapter_materialize: Callable[..., object],
    compile_input: object,
    prepared: PreparedArtifact,
    state_version: int,
) -> dict[str, object]:
    """Compare the factorized adapter against one serial Native fixture."""

    if not isinstance(prepared, PreparedArtifact):
        raise _fail("prepared_artifact_invalid")
    prepared.verify()
    native = _sync_callback(native_materialize, compile_input, prepared, state_version)
    factorized = _sync_callback(adapter_materialize, compile_input, prepared, state_version)
    if not isinstance(native, PreparedSemanticCall) or not isinstance(factorized, PreparedSemanticCall):
        raise _fail("parity_materialized_call_invalid")
    return {
        "source_sequence": prepared.source_sequence,
        "state_version": state_version,
        "semantic_call_fingerprint_equal": native.call.fingerprint == factorized.call.fingerprint,
        "candidate_order_equal": native.call.candidate_order == factorized.call.candidate_order,
        "request_equal": native.request == factorized.request,
        "parity": (
            native.call.fingerprint == factorized.call.fingerprint
            and native.call.candidate_order == factorized.call.candidate_order
            and native.request == factorized.request
        ),
    }


# Short aliases are useful to callers migrating from the v3.1 prototype.
NodeResolveAdapter = NodeResolveV4Adapter
PreparedNodeResolveCall = PreparedSemanticCall
serial_factorized_parity = assert_serial_factorized_parity


__all__ = [
    "ExactNodeResolveResult",
    "NodeResolveAdapter",
    "NodeResolveAdapterError",
    "NodeResolveV4Adapter",
    "NodeResolveV4AdapterProtocol",
    "PreparedNodeResolveCall",
    "PreparedSemanticCall",
    "assert_serial_factorized_parity",
    "serial_factorized_parity",
]
