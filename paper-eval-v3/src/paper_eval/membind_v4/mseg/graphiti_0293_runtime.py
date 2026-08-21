"""Read-only runtime instrumentation seam for pinned Graphiti 0.29.3.

Graphiti is imported only through callables supplied by the caller.  The seam
uses identity-preserving wrappers and cloned function globals so edge child
identity exists before Graphiti constructs each child coroutine.  It does not
change prompts, schemas, model arguments, candidate order, scheduling, or
persistence behavior.
"""

from __future__ import annotations

import hashlib
import inspect
import re
import time
import types
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding
from paper_eval.membind_v31.request_runtime import (
    consume_completed_request_id,
    current_request_id,
)

from .mutation_epoch import MutationEpochToken, StateMutationEpoch
from .passive_equivalence import RuntimeExecutionSnapshot
from .read_view import CandidateSemanticRecord, ReadKind, ReadMaterialization
from .runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    RuntimeInstrumentationError,
    SemanticOperatorClass,
    SemanticOperatorInstance,
    TransactionCommitObserver,
    WriterDomainCertificate,
    canonical_sha256,
    precreate_edge_children,
    runtime_read_view_from_epoch_window,
)
from .version_token import MemoryVersionToken, VersionTokenFactory


class Graphiti0293RuntimeError(ValueError):
    """The pinned runtime seam cannot preserve or observe its contract."""


def _fail(code: str) -> Graphiti0293RuntimeError:
    return Graphiti0293RuntimeError(code)


def _member(value: object, name: str, default: object = None) -> object:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _sequence(value: object, code: str) -> list[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    return list(value)


def _clone_function(
    function: Callable[..., object], overrides: Mapping[str, object]
) -> Callable[..., object]:
    """Clone bytecode while changing only selected global hook identities."""

    if not isinstance(function, types.FunctionType):
        raise _fail("pinned_function_not_python")
    globals_copy = dict(function.__globals__)
    globals_copy.update(overrides)
    cloned = types.FunctionType(
        function.__code__,
        globals_copy,
        name=function.__name__,
        argdefs=function.__defaults__,
        closure=function.__closure__,
    )
    cloned.__kwdefaults__ = function.__kwdefaults__
    cloned.__annotations__ = dict(function.__annotations__)
    cloned.__qualname__ = function.__qualname__
    cloned.__module__ = function.__module__
    return cloned


@dataclass(frozen=True, slots=True)
class _SourceScope:
    graph_id: str
    stream_id: str
    source_sequence: int


_SOURCE_SCOPE: ContextVar[_SourceScope | None] = ContextVar(
    "meg_graphiti_0293_source_scope", default=None
)


@contextmanager
def graphiti_runtime_source_scope(
    *, graph_id: str, stream_id: str, source_sequence: int
):
    scope = _SourceScope(graph_id, stream_id, source_sequence)
    token = _SOURCE_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SOURCE_SCOPE.reset(token)


class MEGRuntimeInstrumentedAdapter:
    """Add source identity context around an unchanged MemBind adapter."""

    def __init__(self, *, inner: object, stream_id: str) -> None:
        if not callable(getattr(inner, "prepare", None)) or not callable(
            getattr(inner, "bind", None)
        ):
            raise _fail("instrumented_inner_adapter_invalid")
        self._inner = inner
        self._stream_id = _text(stream_id, "instrumented_stream_invalid")

    async def prepare(self, compile_input: object) -> object:
        source = getattr(compile_input, "source", None)
        with graphiti_runtime_source_scope(
            graph_id=_text(getattr(source, "group_id", None), "source_graph_id_missing"),
            stream_id=self._stream_id,
            source_sequence=int(getattr(source, "source_sequence")),
        ):
            return await self._inner.prepare(compile_input)

    async def bind(
        self, compile_input: object, artifact: object, *, logical_time_ns: int
    ) -> object:
        source = getattr(compile_input, "source", None)
        with graphiti_runtime_source_scope(
            graph_id=_text(getattr(source, "group_id", None), "source_graph_id_missing"),
            stream_id=self._stream_id,
            source_sequence=int(getattr(source, "source_sequence")),
        ):
            return await self._inner.bind(
                compile_input, artifact, logical_time_ns=logical_time_ns
            )


def _episode_from_call(operation: str, args: tuple[object, ...]) -> object | None:
    indexes = {
        "extract_nodes": 1,
        "resolve_extracted_nodes": 2,
        "extract_edges": 1,
        "resolve_extracted_edges": 2,
        "extract_attributes_from_nodes": 2,
        "process_episode_data": 1,
    }
    index = indexes.get(operation)
    return None if index is None or len(args) <= index else args[index]


def _infer_scope(
    operation: str,
    args: tuple[object, ...],
    *,
    stream_id: str,
) -> _SourceScope:
    explicit = _SOURCE_SCOPE.get()
    if explicit is not None:
        return explicit
    episode = _episode_from_call(operation, args)
    if isinstance(episode, list):
        episode = episode[0] if episode else None
    graph_id = _member(episode, "group_id")
    if graph_id is None and operation == "process_episode_data" and len(args) > 5:
        graph_id = args[5]
    graph = _text(graph_id, "runtime_graph_id_unobservable")
    uuid = str(_member(episode, "uuid", ""))
    match = re.search(r"(?:^|[-_:])(\d+)$", uuid)
    sequence = int(match.group(1)) if match is not None else 0
    return _SourceScope(graph, stream_id, sequence)


def _schema_hash(response_model: object, client: object, kwargs: Mapping[str, object]) -> str:
    schema = None
    schema_call = getattr(response_model, "model_json_schema", None)
    if callable(schema_call):
        schema = schema_call()
    config = getattr(client, "config", None)
    return canonical_sha256(
        {
            "model": getattr(client, "model", None),
            "model_size": str(kwargs.get("model_size", "medium")),
            "response_schema": schema,
            "small_model": getattr(config, "small_model", None),
        }
    )


class _LLMProxy:
    def __init__(self, inner: object, recorder: MEGRuntimeRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def generate_response(self, *args: object, **kwargs: object) -> object:
        call = getattr(self._inner, "generate_response", None)
        if not callable(call):
            raise _fail("llm_generate_response_missing")
        submit = time.monotonic_ns()
        pending = call(*args, **kwargs)
        if not inspect.isawaitable(pending):
            raise _fail("llm_generate_response_not_awaitable")
        start = time.monotonic_ns()
        response = await pending
        end = time.monotonic_ns()
        messages = args[0] if args else kwargs.get("messages")
        prompt_name = kwargs.get("prompt_name")
        self._recorder.record_request(
            prompt_name=_text(prompt_name, "graphiti_prompt_name_missing"),
            prompt_hash=canonical_sha256(messages),
            model_schema_hash=_schema_hash(
                kwargs.get("response_model"), self._inner, kwargs
            ),
            response_hash=canonical_sha256(response),
            request_id=consume_completed_request_id() or current_request_id(),
            submit_ns=submit,
            start_ns=start,
            end_ns=end,
        )
        return response


class _SearchInterfaceProxy:
    def __init__(self, inner: object, recorder: MEGRuntimeRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __getattr__(self, name: str) -> object:
        value = getattr(self._inner, name)
        if not callable(value) or not name.endswith("_search"):
            return value

        async def observed(*args: object, **kwargs: object) -> object:
            self._recorder.record_db_read(
                {
                    "arguments": args[1:],
                    "keyword_arguments": kwargs,
                    "operation": name,
                }
            )
            pending = value(*args, **kwargs)
            if not inspect.isawaitable(pending):
                raise _fail("search_operation_not_awaitable")
            return await pending

        return observed


class _GraphOperationsProxy:
    def __init__(self, inner: object, recorder: MEGRuntimeRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __getattr__(self, name: str) -> object:
        value = getattr(self._inner, name)
        if not callable(value):
            return value

        async def observed(*args: object, **kwargs: object) -> object:
            if any(marker in name for marker in ("get", "search", "retrieve")):
                self._recorder.record_db_read(
                    {"arguments": args[2:], "keyword_arguments": kwargs, "operation": name}
                )
            pending = value(*args, **kwargs)
            if not inspect.isawaitable(pending):
                raise _fail("graph_operation_not_awaitable")
            return await pending

        return observed


class _TransactionProxy:
    def __init__(self, inner: object, recorder: MEGRuntimeRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def run(self, query: object, **kwargs: object) -> object:
        self._recorder.record_write_intent({"query": str(query), "params": kwargs})
        pending = self._inner.run(query, **kwargs)
        return await pending

    async def execute_query(self, query: object, **kwargs: object) -> object:
        self._recorder.record_write_intent({"query": str(query), "params": kwargs})
        pending = self._inner.execute_query(query, **kwargs)
        return await pending


class _SessionProxy:
    def __init__(
        self,
        inner: object,
        recorder: MEGRuntimeRecorder,
        observer: TransactionCommitObserver[object],
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._observer = observer

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def execute_write(
        self, callback: Callable[..., Awaitable[object]], *args: object, **kwargs: object
    ) -> object:
        async def observed_callback(tx: object, *inner_args: object, **inner_kwargs: object):
            return await callback(
                _TransactionProxy(tx, self._recorder), *inner_args, **inner_kwargs
            )

        return await self._observer.execute_write(
            self._inner, observed_callback, *args, **kwargs
        )


class _DriverProxy:
    def __init__(
        self,
        inner: object,
        recorder: MEGRuntimeRecorder,
        observer: TransactionCommitObserver[object],
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._observer = observer

    def __getattr__(self, name: str) -> object:
        if name == "search_interface":
            selected = getattr(self._inner, name)
            # Graphiti 0.29.3 leaves this optional capability unset for the
            # native Neo4j driver; preserve that absence instead of creating
            # a truthy proxy around ``None``.
            return None if selected is None else _SearchInterfaceProxy(selected, self._recorder)
        if name == "graph_operations_interface":
            selected = getattr(self._inner, name)
            return None if selected is None else _GraphOperationsProxy(selected, self._recorder)
        return getattr(self._inner, name)

    def session(self, *args: object, **kwargs: object) -> _SessionProxy:
        return _SessionProxy(
            self._inner.session(*args, **kwargs), self._recorder, self._observer
        )

    def clone(self, *args: object, **kwargs: object) -> "_DriverProxy":
        return _DriverProxy(
            self._inner.clone(*args, **kwargs), self._recorder, self._observer
        )

    async def execute_query(self, query: object, **kwargs: object) -> object:
        text = str(query).strip().upper()
        write = any(
            token in text
            for token in (" CREATE ", " MERGE ", " DELETE ", " SET ", " REMOVE ", " DROP ")
        )
        identity = {"query": str(query), "params": kwargs}
        if write:
            self._recorder.record_write_intent(identity)
        else:
            self._recorder.record_db_read(identity)
        return await self._inner.execute_query(query, **kwargs)


class _ClientsProxy:
    def __init__(
        self,
        inner: object,
        recorder: MEGRuntimeRecorder,
        observer: TransactionCommitObserver[object],
    ) -> None:
        self._inner = inner
        self.llm_client = _LLMProxy(getattr(inner, "llm_client"), recorder)
        driver = getattr(inner, "driver", None)
        if driver is not None:
            self.driver = _DriverProxy(driver, recorder, observer)
        embedder = getattr(inner, "embedder", None)
        if embedder is not None:
            self.embedder = embedder
        self.cross_encoder = getattr(inner, "cross_encoder", None)
        self.tracer = getattr(inner, "tracer", None)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class _GraphitiProxy:
    def __init__(
        self,
        inner: object,
        recorder: MEGRuntimeRecorder,
        observer: TransactionCommitObserver[object],
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(
            self,
            "driver",
            _DriverProxy(getattr(inner, "driver"), recorder, observer),
        )
        object.__setattr__(
            self,
            "clients",
            _ClientsProxy(getattr(inner, "clients"), recorder, observer),
        )

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _candidate_record(value: object, *, role: str) -> CandidateSemanticRecord:
    uuid = _text(_member(value, "uuid"), "candidate_uuid_missing")
    def semantic_value(item: object) -> object:
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        if isinstance(item, Mapping):
            return {str(key): semantic_value(child) for key, child in item.items()}
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            return [semantic_value(child) for child in item]
        if hasattr(item, "isoformat") and callable(getattr(item, "isoformat")):
            return item.isoformat()
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            return semantic_value(model_dump(mode="json"))
        raise _fail("candidate_semantic_field_unobservable")

    fields = semantic_value({
        "attributes": _member(value, "attributes", {}),
        "episodes": _member(value, "episodes", []),
        "expired_at": _member(value, "expired_at"),
        "fact": _member(value, "fact"),
        "invalid_at": _member(value, "invalid_at"),
        "labels": _member(value, "labels", []),
        "name": _member(value, "name"),
        "role": role,
        "source_node_uuid": _member(value, "source_node_uuid"),
        "summary": str(_member(value, "summary", ""))[:120],
        "target_node_uuid": _member(value, "target_node_uuid"),
        "valid_at": _member(value, "valid_at"),
    })
    assert isinstance(fields, Mapping)
    return CandidateSemanticRecord.create(candidate_id=uuid, semantic_fields=fields)


def _context_hash(previous: Sequence[object], extra: object = None) -> str:
    return canonical_sha256(
        {
            "extra": extra,
            "previous_episodes": [
                {
                    "content": _member(item, "content", ""),
                    "timestamp": _member(item, "valid_at"),
                    "uuid": _member(item, "uuid"),
                }
                for item in previous
            ],
        }
    )


def build_observe_only_binding(
    binding: S5GraphitiSemanticBinding,
    *,
    recorder: MEGRuntimeRecorder,
    mutation_epoch: StateMutationEpoch,
    writer_domain: WriterDomainCertificate,
    stream_id: str,
) -> S5GraphitiSemanticBinding:
    """Instrument the exact pinned callables without changing their arguments."""

    if not isinstance(binding, S5GraphitiSemanticBinding):
        raise _fail("semantic_binding_invalid")
    if recorder.mode is not InstrumentationMode.OBSERVE_ONLY:
        raise _fail("observe_only_binding_mode_invalid")
    if not isinstance(mutation_epoch, StateMutationEpoch) or not isinstance(
        writer_domain, WriterDomainCertificate
    ):
        raise _fail("runtime_evidence_invalid")
    if mutation_epoch.namespace != writer_domain.namespace:
        raise _fail("runtime_evidence_namespace_mismatch")
    stream = _text(stream_id, "runtime_stream_invalid")
    transaction_observer: TransactionCommitObserver[object] = TransactionCommitObserver(
        mutation_epoch=mutation_epoch, recorder=recorder
    )
    source_operators: dict[tuple[str, int], dict[str, list[SemanticOperatorInstance]]] = {}
    versions: dict[str, MemoryVersionToken] = {}
    version_factories: dict[str, VersionTokenFactory] = {}

    def current_version(graph_id: str) -> MemoryVersionToken:
        value = versions.get(graph_id)
        if value is None:
            factory = VersionTokenFactory(backend_id="neo4j", epoch=mutation_epoch.epoch)
            value = factory.commit(
                namespace=graph_id,
                transaction_id="meg-runtime-origin",
                evidence_hash=writer_domain.certificate_hash,
            )
            version_factories[graph_id] = factory
            versions[graph_id] = value
        return value

    def state(scope: _SourceScope) -> dict[str, list[SemanticOperatorInstance]]:
        return source_operators.setdefault((scope.graph_id, scope.source_sequence), {})

    def latest(scope: _SourceScope, role: str) -> SemanticOperatorInstance | None:
        values = state(scope).get(role, [])
        return values[-1] if values else None

    def make_operator(
        scope: _SourceScope,
        *,
        role: str,
        classification: SemanticOperatorClass,
        inputs: object,
        parents: Sequence[SemanticOperatorInstance | None] = (),
        child_ordinal: int = 0,
        materialized_before_coroutine: bool = True,
        readiness_parents: Sequence[SemanticOperatorInstance | None] | None = None,
    ) -> SemanticOperatorInstance:
        selected_parents = tuple(
            item.semantic_operator_id for item in parents if item is not None
        )
        operator = SemanticOperatorInstance.create(
            graph_id=scope.graph_id,
            stream_id=scope.stream_id,
            source_sequence=scope.source_sequence,
            semantic_operator_type=role,
            classification=classification,
            parent_semantic_operator_ids=selected_parents,
            child_ordinal=child_ordinal,
            semantic_input_identity=inputs,
            materialized_before_coroutine=materialized_before_coroutine,
        )
        direct = readiness_parents if readiness_parents is not None else parents
        recorder.materialize(
            operator,
            immutable_inputs_exist=True,
            state_satisfiable=(
                classification is not SemanticOperatorClass.STATE_DERIVED
                or writer_domain.certified
            ),
            direct_predecessor_ids=tuple(
                item.semantic_operator_id for item in direct if item is not None
            ),
        )
        state(scope).setdefault(role, []).append(operator)
        return operator

    def clients_proxy(value: object) -> _ClientsProxy:
        return _ClientsProxy(value, recorder, transaction_observer)

    async def extract_nodes(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("extract_nodes", args, stream_id=stream)
        operator = make_operator(
            scope,
            role="NODE_EXTRACTION",
            classification=SemanticOperatorClass.EVIDENCE_DERIVED,
            inputs={"episode": _member(args[1], "uuid") if len(args) > 1 else None},
        )
        selected = (clients_proxy(args[0]), *args[1:])
        with recorder.operator_scope(operator.semantic_operator_id):
            return await binding.extract_nodes(*selected, **kwargs)

    async def resolve_nodes(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("resolve_extracted_nodes", args, stream_id=stream)
        extracted = _sequence(args[1], "resolved_node_inputs_invalid")
        previous = _sequence(args[3], "resolved_node_previous_invalid") if len(args) > 3 else []
        parent = latest(scope, "NODE_EXTRACTION")
        captured: dict[str, object] = {}
        similarity_operators: list[SemanticOperatorInstance] = []
        original_collect = binding.resolve_extracted_nodes.__globals__.get(
            "_collect_candidate_nodes"
        )
        original_similarity = binding.resolve_extracted_nodes.__globals__.get(
            "_resolve_with_similarity"
        )
        original_llm = binding.resolve_extracted_nodes.__globals__.get("_resolve_with_llm")
        if not all(callable(value) for value in (original_collect, original_similarity, original_llm)):
            raise _fail("node_internal_hook_missing")

        candidate_operator = make_operator(
            scope,
            role="NODE_CANDIDATE_READ",
            classification=SemanticOperatorClass.STATE_DERIVED,
            inputs={"extracted_node_uuids": [_member(item, "uuid") for item in extracted]},
            parents=(parent,),
        )

        async def collect(*inner_args: object, **inner_kwargs: object):
            before = mutation_epoch.snapshot()
            with recorder.operator_scope(candidate_operator.semantic_operator_id):
                rows = await original_collect(*inner_args, **inner_kwargs)
            after = mutation_epoch.snapshot()
            flattened = tuple(
                _candidate_record(candidate, role=f"query_{query_index}")
                for query_index, row in enumerate(rows)
                for candidate in row
            )
            materialized = ReadMaterialization.create(
                query_identity=f"graphiti-0.29.3:node-candidate:{canonical_sha256([_member(item, 'name') for item in extracted])}",
                search_configuration_hash=canonical_sha256(
                    {"candidate_limit": 10, "minimum_cosine_score": 0.6, "reranking": False}
                ),
                candidates=flattened,
                mutable_context_fragment_hash=_context_hash(previous),
                provenance_hash=writer_domain.certificate_hash,
            )
            view = runtime_read_view_from_epoch_window(
                graph_id=scope.graph_id,
                stream_id=scope.stream_id,
                source_sequence=scope.source_sequence,
                operator_instance_id=candidate_operator.semantic_operator_id,
                memory_version_token=current_version(scope.graph_id),
                mutation_epoch_before=before,
                mutation_epoch_after=after,
                writer_domain=writer_domain,
                read_kind=ReadKind.NODE_CANDIDATE,
                materialized=materialized,
            )
            recorder.attach_read_view(candidate_operator.semantic_operator_id, view)
            captured.update(
                {"before": before, "after": after, "materialized": materialized}
            )
            return rows

        def similarity(*inner_args: object, **inner_kwargs: object):
            operator = make_operator(
                scope,
                role="DETERMINISTIC_SIMILARITY",
                classification=SemanticOperatorClass.DERIVED_PRIVATE,
                inputs={"ordinal": len(similarity_operators)},
                parents=(candidate_operator,),
                child_ordinal=len(similarity_operators),
            )
            similarity_operators.append(operator)
            with recorder.operator_scope(operator.semantic_operator_id):
                return original_similarity(*inner_args, **inner_kwargs)

        async def resolve_batch(*inner_args: object, **inner_kwargs: object):
            unresolved_state = inner_args[3] if len(inner_args) > 3 else None
            unresolved = tuple(getattr(unresolved_state, "unresolved_indices", ()))
            formation = make_operator(
                scope,
                role="UNRESOLVED_SET_FORMATION",
                classification=SemanticOperatorClass.DERIVED_PRIVATE,
                inputs={"unresolved_indices": unresolved},
                parents=tuple(similarity_operators) or (candidate_operator,),
            )
            with recorder.operator_scope(formation.semantic_operator_id):
                pass
            batch = make_operator(
                scope,
                role="NODE_BATCH_RESOLUTION_DECISION",
                classification=SemanticOperatorClass.STATE_DERIVED,
                inputs={
                    "candidate_count": len(getattr(inner_args[2], "existing_nodes", ())),
                    "unresolved_indices": unresolved,
                },
                parents=(formation,),
            )
            materialized = captured.get("materialized")
            before = captured.get("before")
            after = captured.get("after")
            if isinstance(materialized, ReadMaterialization) and isinstance(
                before, MutationEpochToken
            ) and isinstance(after, MutationEpochToken):
                view = runtime_read_view_from_epoch_window(
                    graph_id=scope.graph_id,
                    stream_id=scope.stream_id,
                    source_sequence=scope.source_sequence,
                    operator_instance_id=batch.semantic_operator_id,
                    memory_version_token=current_version(scope.graph_id),
                    mutation_epoch_before=before,
                    mutation_epoch_after=after,
                    writer_domain=writer_domain,
                    read_kind=ReadKind.NODE_RESOLUTION,
                    materialized=materialized,
                )
                recorder.attach_read_view(batch.semantic_operator_id, view)
            with recorder.operator_scope(batch.semantic_operator_id):
                return await original_llm(*inner_args, **inner_kwargs)

        cloned = _clone_function(
            binding.resolve_extracted_nodes,
            {
                "_collect_candidate_nodes": collect,
                "_resolve_with_similarity": similarity,
                "_resolve_with_llm": resolve_batch,
            },
        )
        selected = (clients_proxy(args[0]), *args[1:])
        result = await cloned(*selected, **kwargs)
        identity_parent = latest(scope, "NODE_BATCH_RESOLUTION_DECISION") or (
            similarity_operators[-1] if similarity_operators else candidate_operator
        )
        identity = make_operator(
            scope,
            role="IDENTITY_MATERIALIZATION",
            classification=SemanticOperatorClass.DERIVED_PRIVATE,
            inputs={"uuid_map": result[1] if isinstance(result, tuple) and len(result) > 1 else None},
            parents=(identity_parent,),
        )
        with recorder.operator_scope(identity.semantic_operator_id):
            pass
        return result

    async def extract_edges(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("extract_edges", args, stream_id=stream)
        resolved_parent = latest(scope, "IDENTITY_MATERIALIZATION")
        classification = (
            SemanticOperatorClass.DERIVED_PRIVATE
            if resolved_parent is not None
            else SemanticOperatorClass.EVIDENCE_DERIVED
        )
        parent = resolved_parent or latest(scope, "NODE_EXTRACTION")
        operator = make_operator(
            scope,
            role="EDGE_EXTRACTION",
            classification=classification,
            inputs={"episode": _member(args[1], "uuid") if len(args) > 1 else None},
            parents=(parent,),
        )
        selected = (clients_proxy(args[0]), *args[1:])
        with recorder.operator_scope(operator.semantic_operator_id):
            return await binding.extract_edges(*selected, **kwargs)

    def resolve_pointers(*args: object, **kwargs: object) -> object:
        return binding.resolve_edge_pointers(*args, **kwargs)

    async def resolve_edges(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("resolve_extracted_edges", args, stream_id=stream)
        incoming = _sequence(args[1], "edge_resolution_inputs_invalid")
        normalize = binding.resolve_extracted_edges.__globals__.get("_normalize_string_exact")
        if not callable(normalize):
            raise _fail("edge_normalizer_hook_missing")
        deduplicated: list[object] = []
        seen: set[tuple[object, object, object]] = set()
        for edge in incoming:
            key = (
                _member(edge, "source_node_uuid"),
                _member(edge, "target_node_uuid"),
                normalize(_member(edge, "fact")),
            )
            if key not in seen:
                seen.add(key)
                deduplicated.append(edge)
        upstream = (
            latest(scope, "EDGE_EXTRACTION"),
            latest(scope, "IDENTITY_MATERIALIZATION"),
        )
        group = make_operator(
            scope,
            role="EDGE_RESOLUTION_GROUP",
            classification=SemanticOperatorClass.DERIVED_PRIVATE,
            inputs={"edge_count": len(deduplicated)},
            parents=upstream,
        )
        children = precreate_edge_children(group, deduplicated)
        group_read_before = mutation_epoch.snapshot()
        ordinal = 0
        original_child = binding.resolve_extracted_edges.__globals__.get(
            "resolve_extracted_edge"
        )
        if not callable(original_child):
            raise _fail("edge_child_hook_missing")

        def child_factory(*child_args: object, **child_kwargs: object):
            nonlocal ordinal
            child = children[ordinal]
            ordinal += 1
            related = _sequence(child_args[2], "edge_related_candidates_invalid")
            invalidation = _sequence(
                child_args[3], "edge_invalidation_candidates_invalid"
            )
            candidate_read = make_operator(
                scope,
                role="EDGE_CANDIDATE_READ",
                classification=SemanticOperatorClass.STATE_DERIVED,
                inputs={"edge_child": child.semantic_input_identity},
                parents=(group,),
                child_ordinal=child.child_ordinal,
                readiness_parents=upstream,
            )
            with recorder.operator_scope(candidate_read.semantic_operator_id):
                pass
            recorder.materialize(
                child,
                immutable_inputs_exist=True,
                state_satisfiable=writer_domain.certified,
                direct_predecessor_ids=(candidate_read.semantic_operator_id,),
            )
            materialized = ReadMaterialization.create(
                query_identity=f"graphiti-0.29.3:edge-candidate:{child.semantic_input_identity}",
                search_configuration_hash=canonical_sha256(
                    {"config": "EDGE_HYBRID_SEARCH_RRF", "duplicate_then_invalidation": True}
                ),
                candidates=tuple(
                    [
                        _candidate_record(value, role="duplicate_candidate")
                        for value in related
                    ]
                    + [
                        _candidate_record(value, role="invalidation_candidate")
                        for value in invalidation
                    ]
                ),
                mutable_context_fragment_hash=_context_hash(
                    (),
                    {
                        "edge_type_candidates": sorted(
                            str(item) for item in (child_args[5] or {})
                        ),
                        "episode_valid_at": _member(child_args[4], "valid_at"),
                    },
                ),
                provenance_hash=writer_domain.certificate_hash,
            )
            after = mutation_epoch.snapshot()
            candidate_view = runtime_read_view_from_epoch_window(
                graph_id=scope.graph_id,
                stream_id=scope.stream_id,
                source_sequence=scope.source_sequence,
                operator_instance_id=candidate_read.semantic_operator_id,
                memory_version_token=current_version(scope.graph_id),
                mutation_epoch_before=group_read_before,
                mutation_epoch_after=after,
                writer_domain=writer_domain,
                read_kind=ReadKind.EDGE_RESOLUTION,
                materialized=materialized,
            )
            recorder.attach_read_view(
                candidate_read.semantic_operator_id, candidate_view
            )
            child_view = runtime_read_view_from_epoch_window(
                graph_id=scope.graph_id,
                stream_id=scope.stream_id,
                source_sequence=scope.source_sequence,
                operator_instance_id=child.semantic_operator_id,
                memory_version_token=current_version(scope.graph_id),
                mutation_epoch_before=group_read_before,
                mutation_epoch_after=after,
                writer_domain=writer_domain,
                read_kind=ReadKind.EDGE_RESOLUTION,
                materialized=materialized,
            )
            recorder.attach_read_view(child.semantic_operator_id, child_view)

            async def execute_child():
                with recorder.operator_scope(child.semantic_operator_id):
                    return await original_child(*child_args, **child_kwargs)

            return execute_child()

        cloned = _clone_function(
            binding.resolve_extracted_edges, {"resolve_extracted_edge": child_factory}
        )
        selected = (clients_proxy(args[0]), *args[1:])
        with recorder.operator_scope(group.semantic_operator_id):
            return await cloned(*selected, **kwargs)

    async def attributes(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("extract_attributes_from_nodes", args, stream_id=stream)
        nodes = _sequence(args[1], "attribute_nodes_invalid")
        previous = _sequence(args[3], "attribute_previous_invalid") if len(args) > 3 else []
        parent = latest(scope, "EDGE_RESOLUTION_GROUP") or latest(
            scope, "IDENTITY_MATERIALIZATION"
        )
        operator = make_operator(
            scope,
            role="NODE_ATTRIBUTE_SUMMARY_BATCH",
            classification=SemanticOperatorClass.STATE_DERIVED,
            inputs={"node_uuids": [_member(item, "uuid") for item in nodes]},
            parents=(parent,),
        )
        token = mutation_epoch.snapshot()
        materialized = ReadMaterialization.create(
            query_identity="graphiti-0.29.3:node-attribute-summary-materialized-input",
            search_configuration_hash=canonical_sha256(
                {"attribute_merge": "overlay", "summary_batch": True}
            ),
            candidates=tuple(_candidate_record(item, role="resolved_node") for item in nodes),
            mutable_context_fragment_hash=_context_hash(previous, kwargs.get("edges")),
            provenance_hash=writer_domain.certificate_hash,
        )
        view = runtime_read_view_from_epoch_window(
            graph_id=scope.graph_id,
            stream_id=scope.stream_id,
            source_sequence=scope.source_sequence,
            operator_instance_id=operator.semantic_operator_id,
            memory_version_token=current_version(scope.graph_id),
            mutation_epoch_before=token,
            mutation_epoch_after=token,
            writer_domain=writer_domain,
            read_kind=ReadKind.ATTRIBUTE,
            materialized=materialized,
        )
        recorder.attach_read_view(operator.semantic_operator_id, view)
        selected = (clients_proxy(args[0]), *args[1:])
        with recorder.operator_scope(operator.semantic_operator_id):
            return await binding.extract_attributes_from_nodes(*selected, **kwargs)

    async def process(*args: object, **kwargs: object) -> object:
        scope = _infer_scope("process_episode_data", args, stream_id=stream)
        saga = args[6] if len(args) > 6 else kwargs.get("saga")
        if saga is not None:
            raise _fail("graphiti_0293_saga_unsupported")
        parent = latest(scope, "NODE_ATTRIBUTE_SUMMARY_BATCH")
        effect = make_operator(
            scope,
            role="PERSIST_AND_PUBLISH",
            classification=SemanticOperatorClass.PERSISTENT_EFFECT,
            inputs={
                "edge_uuids": [_member(item, "uuid") for item in _sequence(args[3], "effect_edges_invalid")],
                "episode_uuid": _member(args[1], "uuid"),
                "node_uuids": [_member(item, "uuid") for item in _sequence(args[2], "effect_nodes_invalid")],
            },
            parents=(parent,),
        )
        intent = {
            "edges": args[3],
            "episode": args[1],
            "nodes": args[2],
            "operation": "add_nodes_and_edges_bulk",
        }
        recorder.record_write_intent(intent)
        proxy = _GraphitiProxy(args[0], recorder, transaction_observer)
        selected = (proxy, *args[1:])
        with recorder.operator_scope(effect.semantic_operator_id):
            result = await binding.process_episode_data(*selected, **kwargs)
            recorder.record_effect(intent)
        transaction_id = transaction_observer.last_transaction_id
        if transaction_id is None:
            raise _fail("persistent_commit_not_observed")
        factory = version_factories.get(scope.graph_id)
        prior = current_version(scope.graph_id)
        assert factory is not None
        versions[scope.graph_id] = factory.commit(
            namespace=scope.graph_id,
            transaction_id=transaction_id,
            evidence_hash=canonical_sha256(intent),
            predecessor=prior,
        )
        publication = make_operator(
            scope,
            role="SOURCE_PUBLICATION",
            classification=SemanticOperatorClass.PUBLICATION,
            inputs={"source_sequence": scope.source_sequence, "transaction_id": transaction_id},
            parents=(effect,),
        )
        with recorder.operator_scope(publication.semantic_operator_id):
            recorder.record_publication(
                source_sequence=scope.source_sequence, transaction_id=transaction_id
            )
        return result

    return S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=attributes,
        extract_edges=extract_edges,
        resolve_extracted_edges=resolve_edges,
        resolve_edge_pointers=resolve_pointers,
        process_episode_data=process,
        loader_verified=binding.loader_verified,
    )


def snapshot_controlled_execution(
    fixture: object,
    result: object,
    *,
    recorder: MEGRuntimeRecorder | None = None,
) -> RuntimeExecutionSnapshot:
    """Project the existing provider-free captured-response fixture for parity."""

    evidence = tuple(getattr(getattr(fixture, "llm"), "request_evidence"))
    request_ids = tuple(str(row["request_identity"]) for row in evidence)
    prompt_hashes = tuple(str(row["prompt_sha256"]) for row in evidence)
    schema_hashes = tuple(str(row["model_schema_sha256"]) for row in evidence)
    response_hashes = tuple(str(row["response_sha256"]) for row in evidence)
    provider_consumption = tuple(getattr(fixture, "provider_consumption"))
    candidate_count = sum(item == "candidate_query" for item in provider_consumption)
    db_reads = tuple(
        canonical_sha256({"controlled_candidate_query_ordinal": ordinal})
        for ordinal in range(candidate_count)
    )
    write_events = tuple(
        {
            "count": int(event.get("count", 0)),
            "operation": str(event.get("event")),
        }
        for event in getattr(fixture, "events")
        if str(event.get("event")).endswith("_save_bulk")
    )
    write_hashes = tuple(canonical_sha256(event) for event in write_events)
    logical_effect = canonical_sha256(
        getattr(fixture, "canonical_logical_state")()
    )
    publication_order = tuple(
        int(event.get("source_sequence", 0))
        for event in getattr(fixture, "events")
        if event.get("event") == "publication"
    )
    if not publication_order and bool(getattr(result, "publication_allowed", False)):
        publication_order = (0,)
    source_sequences = tuple(dict.fromkeys(publication_order))
    return RuntimeExecutionSnapshot(
        production_request_ids=request_ids,
        production_prompt_hashes=prompt_hashes,
        production_model_schema_hashes=schema_hashes,
        captured_response_hashes=response_hashes,
        production_db_read_hashes=db_reads,
        shadow_db_read_hashes=(),
        production_write_intent_hashes=write_hashes,
        persistent_effect_hashes=(logical_effect,),
        source_publication_order=publication_order,
        source_sequences=source_sequences,
        source_exactly_once=len(source_sequences) == len(set(source_sequences)),
        production_llm_call_count=len(evidence),
        production_embedding_call_count=len(getattr(getattr(fixture, "embedder"), "calls")),
        shadow_llm_call_count=0,
        shadow_embedding_call_count=0,
        shadow_persistent_write_count=0,
        publication_modification_count=0,
    )


__all__ = [
    "Graphiti0293RuntimeError",
    "MEGRuntimeInstrumentedAdapter",
    "build_observe_only_binding",
    "graphiti_runtime_source_scope",
    "snapshot_controlled_execution",
]
