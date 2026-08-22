"""Transparent wrappers used only by Native characterization."""

from __future__ import annotations

import contextvars
import functools
import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable

from native_characterization_tracing import TraceRecorder


_WRITE_QUERY_RE = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH)\b", re.IGNORECASE
)
_CYPHER_NON_CODE_RE = re.compile(
    r"/\*.*?\*/|//[^\r\n]*|'(?:\\.|''|[^'])*'|\"(?:\\.|\"\"|[^\"])*\"|`(?:``|[^`])*`",
    re.DOTALL,
)
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

_GRAPHITI_PHASE_ALIASES = (
    ("extract_nodes", "node-extraction"),
    ("resolve_extracted_nodes", "node-resolution"),
    ("extract_edges", "edge-extraction"),
    ("resolve_extracted_edges", "edge-resolution"),
    ("extract_attributes_from_nodes", "attributes-summary"),
)
_GRAPHITI_INSTANCE_PHASES = (
    ("add_episode", "add-episode"),
    ("retrieve_episodes", "previous-context"),
    ("_process_episode_data", "publication"),
)
_ACTIVE_GRAPHITI_PHASE_INSTALLATIONS: dict[
    tuple[int, int], tuple[TraceRecorder, "PatchHandle"]
] = {}
_ACTIVE_NATIVE_CHARACTERIZATION_INSTALLATIONS: dict[
    tuple[int, int], tuple[TraceRecorder, "PatchHandle"]
] = {}


class PatchHandle:
    def __init__(self) -> None:
        self._restorers: list[Callable[[], None]] = []
        self._restored = False

    def add(self, restorer: Callable[[], None]) -> None:
        if self._restored:
            raise RuntimeError("cannot add to a restored instrumentation handle")
        self._restorers.append(restorer)

    def restore(self) -> None:
        if self._restored:
            return
        self._restored = True
        for restorer in reversed(self._restorers):
            restorer()


def _replace_attribute(owner: Any, name: str, replacement: Any) -> Callable[[], None]:
    namespace = getattr(owner, "__dict__", {})
    had_own_attribute = name in namespace
    previous_own_value = namespace.get(name)
    setattr(owner, name, replacement)

    def restore() -> None:
        if had_own_attribute:
            setattr(owner, name, previous_own_value)
        else:
            try:
                delattr(owner, name)
            except AttributeError:
                pass

    return restore


def _safe_label(value: Any, default: str = "unknown") -> str:
    text = str(value) if value is not None else default
    return text if _SAFE_LABEL_RE.fullmatch(text) else default


def patch_phase_alias(
    owner: Any,
    attribute: str,
    phase: str,
    recorder: TraceRecorder,
) -> PatchHandle:
    original = getattr(owner, attribute)

    @functools.wraps(original)
    async def traced(*args: Any, **kwargs: Any) -> Any:
        with recorder.span(phase):
            return await original(*args, **kwargs)

    handle = PatchHandle()
    handle.add(_replace_attribute(owner, attribute, traced))
    return handle


def install_graphiti_phase_instrumentation(
    graphiti: Any,
    recorder: TraceRecorder,
    *,
    phase_module: Any | None = None,
) -> PatchHandle:
    """Instrument the exact aliases used by pinned Graphiti ``add_episode``.

    Importing the module is lazy and performs no service I/O.  Tests inject a
    fake module so the direct-import alias boundary remains independently
    verifiable without loading Graphiti or runtime configuration.
    """

    owner = phase_module or importlib.import_module("graphiti_core.graphiti")
    key = (id(graphiti), id(owner))
    active = _ACTIVE_GRAPHITI_PHASE_INSTALLATIONS.get(key)
    if active is not None:
        active_recorder, active_handle = active
        if active_recorder is not recorder:
            raise RuntimeError(
                "Graphiti phase instrumentation is active with another recorder"
            )
        return active_handle

    targets = [
        (owner, attribute, phase)
        for attribute, phase in _GRAPHITI_PHASE_ALIASES
    ] + [
        (graphiti, attribute, phase)
        for attribute, phase in _GRAPHITI_INSTANCE_PHASES
        if callable(getattr(graphiti, attribute, None))
    ]
    missing = [
        attribute
        for attribute, _phase in _GRAPHITI_PHASE_ALIASES
        if not callable(getattr(owner, attribute, None))
    ]
    if missing:
        raise AttributeError(
            "pinned Graphiti phase aliases missing: " + ",".join(sorted(missing))
        )

    handle = PatchHandle()

    def remove_active() -> None:
        current = _ACTIVE_GRAPHITI_PHASE_INSTALLATIONS.get(key)
        if current is not None and current[1] is handle:
            _ACTIVE_GRAPHITI_PHASE_INSTALLATIONS.pop(key, None)

    # Add cleanup first so reverse-order restore repairs every patched target
    # before making a new installation eligible.
    handle.add(remove_active)
    try:
        for target_owner, attribute, phase in targets:
            child = patch_phase_alias(target_owner, attribute, phase, recorder)
            handle.add(child.restore)
    except BaseException:
        handle.restore()
        raise
    _ACTIVE_GRAPHITI_PHASE_INSTALLATIONS[key] = (recorder, handle)
    return handle


@dataclass
class _LLMCallState:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def _usage_value(usage: Any, name: str) -> int:
    if isinstance(usage, dict):
        value = usage.get(name, 0)
    else:
        value = getattr(usage, name, 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _response_choices(result: Any) -> list[Any]:
    if isinstance(result, dict):
        choices = result.get("choices", [])
    else:
        choices = getattr(result, "choices", [])
    return list(choices or []) if isinstance(choices, (list, tuple)) else []


def _choice_finish_reason(result: Any) -> str | None:
    choices = _response_choices(result)
    if not choices:
        return None
    choice = choices[0]
    value = choice.get("finish_reason") if isinstance(choice, dict) else getattr(choice, "finish_reason", None)
    if value is None:
        return None
    return _safe_label(value)


def _find_chat_completions(client: Any) -> Any | None:
    seen: set[int] = set()
    current = client
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        transport = getattr(
            getattr(getattr(current, "client", None), "chat", None),
            "completions",
            None,
        )
        if callable(getattr(transport, "create", None)):
            return transport
        current = getattr(current, "inner", None)
    return None


def instrument_llm_client(client: Any, recorder: TraceRecorder) -> PatchHandle:
    handle = PatchHandle()
    original_generate = getattr(client, "generate_response")
    state_var: contextvars.ContextVar[_LLMCallState | None] = contextvars.ContextVar(
        f"native_characterization_llm_{id(client)}", default=None
    )

    @functools.wraps(original_generate)
    async def generate_response(*args: Any, **kwargs: Any) -> Any:
        state = _LLMCallState()
        token = state_var.set(state)
        try:
            with recorder.span(
                "llm",
                operation_class="logical-call",
                metadata={"prompt_name": _safe_label(kwargs.get("prompt_name"))},
            ) as span:
                try:
                    return await original_generate(*args, **kwargs)
                finally:
                    span.add_metadata("retry_count", max(0, state.attempts - 1))
                    span.add_metadata("input_tokens", state.input_tokens)
                    span.add_metadata("output_tokens", state.output_tokens)
        finally:
            state_var.reset(token)

    handle.add(_replace_attribute(client, "generate_response", generate_response))

    transport = _find_chat_completions(client)
    if transport is not None:
        original_create = transport.create

        @functools.wraps(original_create)
        async def create(*args: Any, **kwargs: Any) -> Any:
            state = state_var.get()
            attempt_index = state.attempts if state is not None else 0
            if state is not None:
                state.attempts += 1
            with recorder.span(
                "llm-transport",
                operation_class="request-attempt",
                metadata={"attempt_index": attempt_index},
            ) as span:
                try:
                    result = await original_create(*args, **kwargs)
                except BaseException:
                    # A transport exception has no response choice to inspect;
                    # retain that distinction instead of inferring a finish
                    # reason from the enclosing logical exception.
                    span.add_metadata("finish_reason_observed", False)
                    raise
                usage = getattr(result, "usage", None)
                input_tokens = _usage_value(usage, "prompt_tokens")
                output_tokens = _usage_value(usage, "completion_tokens")
                span.add_metadata("input_tokens", input_tokens)
                span.add_metadata("output_tokens", output_tokens)
                span.add_metadata("usage_observed", usage is not None)
                finish_reason = _choice_finish_reason(result)
                span.add_metadata("finish_reason_observed", finish_reason is not None)
                if finish_reason is not None:
                    span.add_metadata("finish_reason", finish_reason)
                if state is not None:
                    state.input_tokens += input_tokens
                    state.output_tokens += output_tokens
                return result

        handle.add(_replace_attribute(transport, "create", create))
    return handle


def _text_count(value: Any, *, batch: bool) -> int:
    if batch and isinstance(value, list | tuple):
        return len(value)
    return 1


def _first_argument(
    args: tuple[Any, ...], kwargs: dict[str, Any], names: tuple[str, ...]
) -> Any:
    if args:
        return args[0]
    for name in names:
        if name in kwargs:
            return kwargs[name]
    return None


def _embedding_dimension(result: Any, *, batch: bool) -> int:
    if batch:
        if not isinstance(result, list | tuple) or not result:
            return 0
        first = result[0]
        return len(first) if isinstance(first, list | tuple) else 0
    return len(result) if isinstance(result, list | tuple) else 0


def instrument_embedding_client(client: Any, recorder: TraceRecorder) -> PatchHandle:
    handle = PatchHandle()
    for method_name, batch in (("create", False), ("create_batch", True)):
        original = getattr(client, method_name, None)
        if not callable(original):
            continue

        def build_wrapper(
            bound_original: Callable[..., Any],
            call_shape: str,
            is_batch: bool,
        ) -> Callable[..., Any]:
            @functools.wraps(bound_original)
            async def traced(*args: Any, **kwargs: Any) -> Any:
                input_data = _first_argument(
                    args,
                    kwargs,
                    ("input_data_list", "input_data") if is_batch else ("input_data",),
                )
                with recorder.span(
                    "embedding",
                    operation_class=call_shape,
                    metadata={"text_count": _text_count(input_data, batch=is_batch)},
                ) as span:
                    result = await bound_original(*args, **kwargs)
                    span.add_metadata(
                        "dimension", _embedding_dimension(result, batch=is_batch)
                    )
                    return result

            return traced

        replacement = build_wrapper(original, method_name, batch)
        handle.add(_replace_attribute(client, method_name, replacement))
    return handle


def _operation_class(
    query: Any, kwargs: dict[str, Any], default: str | None = None
) -> str:
    if default is not None:
        return default
    routing = kwargs.get("routing_")
    if isinstance(routing, str):
        if routing.casefold().startswith("r"):
            return "query"
        if routing.casefold().startswith("w"):
            return "write"
    code_only = _CYPHER_NON_CODE_RE.sub(" ", str(query))
    return "write" if _WRITE_QUERY_RE.search(code_only) else "query"


class _InstrumentedTransaction:
    def __init__(
        self,
        inner: Any,
        recorder: TraceRecorder,
        transaction_id: str,
        default_operation: str | None,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._transaction_id = transaction_id
        self._default_operation = default_operation

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        query = _first_argument(args, kwargs, ("query", "cypher_query_"))
        operation = _operation_class(query, kwargs, self._default_operation)
        with self._recorder.span(
            "database",
            operation_class=operation,
            metadata={"transaction_id": self._transaction_id},
        ):
            return await self._inner.run(*args, **kwargs)


class _InstrumentedSession:
    def __init__(self, inner: Any, recorder: TraceRecorder) -> None:
        self._inner = inner
        self._delegate = inner
        self._recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        query = _first_argument(args, kwargs, ("query", "cypher_query_"))
        operation = _operation_class(query, kwargs)
        with self._recorder.span("database", operation_class=operation):
            return await self._delegate.run(*args, **kwargs)

    async def _execute_transaction(
        self,
        method_name: str,
        operation: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        original = getattr(self._delegate, method_name)
        callback_key: str | None = None
        if args:
            callback = args[0]
        else:
            callback_key = next(
                (name for name in ("func", "callback") if name in kwargs), None
            )
            callback = kwargs.get(callback_key) if callback_key is not None else None
        if not callable(callback):
            return await original(*args, **kwargs)

        @functools.wraps(callback)
        async def traced_callback(transaction: Any, *callback_args: Any, **callback_kwargs: Any) -> Any:
            transaction_id = self._recorder.next_identifier("tx")
            with self._recorder.span(
                "database-transaction",
                operation_class=operation,
                metadata={"transaction_id": transaction_id},
            ):
                proxy = _InstrumentedTransaction(
                    transaction,
                    self._recorder,
                    transaction_id,
                    operation,
                )
                return await callback(proxy, *callback_args, **callback_kwargs)

        if args:
            return await original(traced_callback, *args[1:], **kwargs)
        forwarded_kwargs = dict(kwargs)
        forwarded_kwargs[callback_key] = traced_callback
        return await original(**forwarded_kwargs)

    async def execute_write(self, *args: Any, **kwargs: Any) -> Any:
        return await self._execute_transaction("execute_write", "write", *args, **kwargs)

    async def execute_read(self, *args: Any, **kwargs: Any) -> Any:
        return await self._execute_transaction("execute_read", "query", *args, **kwargs)

    async def close(self) -> Any:
        return await self._delegate.close()

    async def __aenter__(self) -> "_InstrumentedSession":
        entered = await self._inner.__aenter__()
        self._delegate = entered
        return self

    async def __aexit__(self, *args: Any) -> Any:
        return await self._inner.__aexit__(*args)


class _InstrumentedTransactionContext:
    def __init__(self, inner: Any, recorder: TraceRecorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self._span_context: Any = None
        self._transaction_id: str | None = None

    async def __aenter__(self) -> _InstrumentedTransaction:
        transaction = await self._inner.__aenter__()
        self._transaction_id = self._recorder.next_identifier("tx")
        self._span_context = self._recorder.span(
            "database-transaction",
            operation_class="transaction",
            metadata={"transaction_id": self._transaction_id},
        )
        self._span_context.__enter__()
        return _InstrumentedTransaction(
            transaction,
            self._recorder,
            self._transaction_id,
            None,
        )

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        try:
            result = await self._inner.__aexit__(exc_type, exc, traceback)
        except BaseException as exit_error:
            self._span_context.__exit__(
                type(exit_error), exit_error, exit_error.__traceback__
            )
            raise
        self._span_context.__exit__(exc_type, exc, traceback)
        return result


async def _traced_execute_query(
    original: Callable[..., Any],
    recorder: TraceRecorder,
    *args: Any,
    **kwargs: Any,
) -> Any:
    query = _first_argument(args, kwargs, ("cypher_query_", "query"))
    operation = _operation_class(query, kwargs)
    with recorder.span("database", operation_class=operation):
        return await original(*args, **kwargs)


class _DriverInstallation(PatchHandle):
    def __init__(self, driver: Any, recorder: TraceRecorder) -> None:
        super().__init__()
        self.driver = driver
        self.recorder = recorder
        self.children: list[_DriverInstallation] = []
        self._install()

    def _install(self) -> None:
        original_execute = getattr(self.driver, "execute_query", None)
        if callable(original_execute):
            @functools.wraps(original_execute)
            async def execute_query(*args: Any, **kwargs: Any) -> Any:
                return await _traced_execute_query(
                    original_execute, self.recorder, *args, **kwargs
                )

            self.add(_replace_attribute(self.driver, "execute_query", execute_query))

        original_session = getattr(self.driver, "session", None)
        if callable(original_session):
            @functools.wraps(original_session)
            def session(*args: Any, **kwargs: Any) -> _InstrumentedSession:
                return _InstrumentedSession(original_session(*args, **kwargs), self.recorder)

            self.add(_replace_attribute(self.driver, "session", session))

        original_transaction = getattr(self.driver, "transaction", None)
        if callable(original_transaction):
            @functools.wraps(original_transaction)
            def transaction(*args: Any, **kwargs: Any) -> _InstrumentedTransactionContext:
                return _InstrumentedTransactionContext(
                    original_transaction(*args, **kwargs), self.recorder
                )

            self.add(_replace_attribute(self.driver, "transaction", transaction))

        original_clone = getattr(self.driver, "clone", None)
        if callable(original_clone):
            @functools.wraps(original_clone)
            def clone(*args: Any, **kwargs: Any) -> Any:
                cloned = original_clone(*args, **kwargs)
                if cloned is not self.driver and not any(
                    child.driver is cloned for child in self.children
                ):
                    self.children.append(_DriverInstallation(cloned, self.recorder))
                return cloned

            self.add(_replace_attribute(self.driver, "clone", clone))

    def restore(self) -> None:
        for child in reversed(self.children):
            child.restore()
        super().restore()


def instrument_driver(driver: Any, recorder: TraceRecorder) -> PatchHandle:
    return _DriverInstallation(driver, recorder)


def install_native_characterization_instrumentation(
    graphiti: Any,
    recorder: TraceRecorder,
    *,
    phase_module: Any | None = None,
) -> PatchHandle:
    """Install all C1 wrappers atomically and return one reversible handle.

    The phase module is injectable only for the deterministic offline parity
    fixture.  Production callers omit it so the pinned
    ``graphiti_core.graphiti`` aliases are instrumented.
    """

    owner = phase_module or importlib.import_module("graphiti_core.graphiti")
    key = (id(graphiti), id(owner))
    active = _ACTIVE_NATIVE_CHARACTERIZATION_INSTALLATIONS.get(key)
    if active is not None:
        active_recorder, active_handle = active
        if active_recorder is not recorder:
            raise RuntimeError(
                "Native characterization instrumentation is active with another recorder"
            )
        return active_handle

    handle = PatchHandle()

    def remove_active() -> None:
        current = _ACTIVE_NATIVE_CHARACTERIZATION_INSTALLATIONS.get(key)
        if current is not None and current[1] is handle:
            _ACTIVE_NATIVE_CHARACTERIZATION_INSTALLATIONS.pop(key, None)

    # Cleanup is registered first so reverse-order restoration leaves the
    # installation reserved until every wrapped object is back to its original.
    handle.add(remove_active)
    try:
        phases = install_graphiti_phase_instrumentation(
            graphiti,
            recorder,
            phase_module=owner,
        )
        handle.add(phases.restore)
        llm = instrument_llm_client(graphiti.llm_client, recorder)
        handle.add(llm.restore)
        embedding = instrument_embedding_client(graphiti.embedder, recorder)
        handle.add(embedding.restore)
        driver = instrument_driver(graphiti.driver, recorder)
        handle.add(driver.restore)
    except BaseException:
        handle.restore()
        raise

    _ACTIVE_NATIVE_CHARACTERIZATION_INSTALLATIONS[key] = (recorder, handle)
    return handle
