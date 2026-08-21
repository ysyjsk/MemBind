"""Provider-free contracts for qualified MEG runtime instrumentation.

The module contains no Graphiti, Neo4j, or provider import.  It defines the
evidence that a runtime adapter must produce and deliberately contains no
scheduler, admission policy, speculative execution, or persistent operation.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import Enum
from typing import Generic, TypeVar

from .mutation_epoch import MutationEpochToken, StateMutationEpoch
from .read_view import (
    ReadKind,
    ReadMaterialization,
    ReadViewStatus,
    SemanticReadView,
    capture_semantic_read_view,
    semantic_read_view_from_materialization,
)
from .version_token import MemoryVersionToken


class RuntimeInstrumentationError(ValueError):
    """Runtime instrumentation evidence is malformed or unsafe."""


def _fail(code: str) -> RuntimeInstrumentationError:
    return RuntimeInstrumentationError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _ordinal(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _canonical(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise _fail("semantic_identity_invalid")
            normalized[key] = _canonical(item)
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _canonical(model_dump(mode="json"))
        except TypeError:
            return _canonical(model_dump())
    if hasattr(value, "isoformat") and callable(getattr(value, "isoformat")):
        return value.isoformat()
    raise _fail("semantic_identity_invalid")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class SemanticOperatorClass(str, Enum):
    EVIDENCE_DERIVED = "EVIDENCE_DERIVED"
    STATE_DERIVED = "STATE_DERIVED"
    DERIVED_PRIVATE = "DERIVED_PRIVATE"
    PERSISTENT_EFFECT = "PERSISTENT_EFFECT"
    PUBLICATION = "PUBLICATION"


class InstrumentationMode(str, Enum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    SHADOW_READ = "SHADOW_READ"


class OperatorEventType(str, Enum):
    OPERATOR_MATERIALIZED = "OPERATOR_MATERIALIZED"
    OPERATOR_READY = "OPERATOR_READY"
    OPERATOR_START = "OPERATOR_START"
    OPERATOR_END = "OPERATOR_END"
    TRANSACTION_COMMIT = "TRANSACTION_COMMIT"
    PUBLICATION = "PUBLICATION"


class WriterDomainStatus(str, Enum):
    CERTIFIED_SINGLE_WRITER_DOMAIN = "CERTIFIED_SINGLE_WRITER_DOMAIN"
    OPAQUE_WRITER_DOMAIN = "OPAQUE_WRITER_DOMAIN"


@dataclass(frozen=True, slots=True)
class WriterDomainCertificate:
    namespace: str
    graph_backend: str
    authorized_writer_identity: str
    write_path_coverage: tuple[str, ...]
    expected_write_paths: tuple[str, ...]
    external_writer_policy: str
    commit_observer_coverage: str
    fresh_namespace: bool
    no_background_mutation: bool
    status: WriterDomainStatus
    certificate_hash: str

    @classmethod
    def create(
        cls,
        *,
        namespace: str,
        graph_backend: str,
        authorized_writer_identity: str,
        write_path_coverage: tuple[str, ...],
        expected_write_paths: tuple[str, ...],
        external_writer_policy: str,
        commit_observer_coverage: str,
        fresh_namespace: bool,
        no_background_mutation: bool,
    ) -> "WriterDomainCertificate":
        namespace = _text(namespace, "writer_namespace_invalid")
        backend = _text(graph_backend, "writer_backend_invalid")
        writer = _text(authorized_writer_identity, "writer_identity_invalid")
        if not isinstance(write_path_coverage, tuple) or not isinstance(
            expected_write_paths, tuple
        ):
            raise _fail("writer_path_coverage_invalid")
        covered = tuple(_text(item, "writer_path_invalid") for item in write_path_coverage)
        expected = tuple(_text(item, "writer_path_invalid") for item in expected_write_paths)
        if len(covered) != len(set(covered)) or len(expected) != len(set(expected)):
            raise _fail("writer_path_duplicate")
        policy = _text(external_writer_policy, "external_writer_policy_invalid")
        observer = _text(commit_observer_coverage, "commit_observer_coverage_invalid")
        if not isinstance(fresh_namespace, bool) or not isinstance(
            no_background_mutation, bool
        ):
            raise _fail("writer_domain_boolean_invalid")
        complete = (
            set(covered) == set(expected)
            and bool(expected)
            and policy == "DENY"
            and observer == "ALL_MANAGED_COMMITS"
            and fresh_namespace
            and no_background_mutation
        )
        status = (
            WriterDomainStatus.CERTIFIED_SINGLE_WRITER_DOMAIN
            if complete
            else WriterDomainStatus.OPAQUE_WRITER_DOMAIN
        )
        body = {
            "authorized_writer_identity": writer,
            "commit_observer_coverage": observer,
            "expected_write_paths": sorted(expected),
            "external_writer_policy": policy,
            "fresh_namespace": fresh_namespace,
            "graph_backend": backend,
            "namespace": namespace,
            "no_background_mutation": no_background_mutation,
            "status": status.value,
            "write_path_coverage": sorted(covered),
        }
        return cls(
            namespace=namespace,
            graph_backend=backend,
            authorized_writer_identity=writer,
            write_path_coverage=covered,
            expected_write_paths=expected,
            external_writer_policy=policy,
            commit_observer_coverage=observer,
            fresh_namespace=fresh_namespace,
            no_background_mutation=no_background_mutation,
            status=status,
            certificate_hash=canonical_sha256(body),
        )

    @property
    def certified(self) -> bool:
        return self.status is WriterDomainStatus.CERTIFIED_SINGLE_WRITER_DOMAIN


@dataclass(frozen=True, slots=True)
class RuntimeOperatorContract:
    operator_type: str
    classification: SemanticOperatorClass
    reads_mutable_persistent_state: bool
    read_view_required: bool
    persistent_effect: bool
    publication: bool


def classify_operator(
    *,
    operator_type: str,
    reads_mutable_persistent_state: bool,
    consumes_only_immutable_evidence_or_parent_private_result: bool,
    persistent_effect: bool,
    publication: bool,
) -> RuntimeOperatorContract:
    """Classify from explicit source facts, never from an operator's name."""

    role = _text(operator_type, "operator_type_invalid")
    flags = (
        reads_mutable_persistent_state,
        consumes_only_immutable_evidence_or_parent_private_result,
        persistent_effect,
        publication,
    )
    if any(not isinstance(flag, bool) for flag in flags):
        raise _fail("operator_classification_flag_invalid")
    if publication:
        classification = SemanticOperatorClass.PUBLICATION
    elif persistent_effect:
        classification = SemanticOperatorClass.PERSISTENT_EFFECT
    elif reads_mutable_persistent_state:
        classification = SemanticOperatorClass.STATE_DERIVED
    elif consumes_only_immutable_evidence_or_parent_private_result:
        classification = SemanticOperatorClass.DERIVED_PRIVATE
    else:
        classification = SemanticOperatorClass.EVIDENCE_DERIVED
    return RuntimeOperatorContract(
        operator_type=role,
        classification=classification,
        reads_mutable_persistent_state=reads_mutable_persistent_state,
        read_view_required=classification is SemanticOperatorClass.STATE_DERIVED,
        persistent_effect=persistent_effect,
        publication=publication,
    )


@dataclass(frozen=True, slots=True)
class SemanticOperatorInstance:
    graph_id: str
    stream_id: str
    source_sequence: int
    semantic_operator_id: str
    semantic_operator_type: str
    classification: SemanticOperatorClass
    parent_semantic_operator_ids: tuple[str, ...]
    child_ordinal: int
    semantic_input_identity: str
    materialized_before_coroutine: bool = True

    @classmethod
    def create(
        cls,
        *,
        graph_id: str,
        stream_id: str,
        source_sequence: int,
        semantic_operator_type: str,
        classification: SemanticOperatorClass,
        parent_semantic_operator_ids: tuple[str, ...],
        child_ordinal: int,
        semantic_input_identity: Mapping[str, object] | str,
        materialized_before_coroutine: bool = True,
    ) -> "SemanticOperatorInstance":
        graph = _text(graph_id, "operator_graph_invalid")
        stream = _text(stream_id, "operator_stream_invalid")
        sequence = _ordinal(source_sequence, "operator_source_sequence_invalid")
        role = _text(semantic_operator_type, "operator_type_invalid")
        if not isinstance(classification, SemanticOperatorClass):
            raise _fail("operator_classification_invalid")
        if not isinstance(parent_semantic_operator_ids, tuple):
            raise _fail("operator_parents_invalid")
        parents = tuple(_text(item, "operator_parent_invalid") for item in parent_semantic_operator_ids)
        if len(parents) != len(set(parents)):
            raise _fail("operator_parent_duplicate")
        ordinal = _ordinal(child_ordinal, "operator_child_ordinal_invalid")
        identity = (
            _text(semantic_input_identity, "semantic_input_identity_invalid")
            if isinstance(semantic_input_identity, str)
            else canonical_sha256(semantic_input_identity)
        )
        if not isinstance(materialized_before_coroutine, bool):
            raise _fail("operator_materialization_flag_invalid")
        body = {
            "child_ordinal": ordinal,
            "graph_id": graph,
            "parent_semantic_operator_ids": list(parents),
            "semantic_input_identity": identity,
            "semantic_operator_type": role,
            "source_sequence": sequence,
            "stream_id": stream,
        }
        return cls(
            graph_id=graph,
            stream_id=stream,
            source_sequence=sequence,
            semantic_operator_id=f"meg-runtime-op-{canonical_sha256(body)}",
            semantic_operator_type=role,
            classification=classification,
            parent_semantic_operator_ids=parents,
            child_ordinal=ordinal,
            semantic_input_identity=identity,
            materialized_before_coroutine=materialized_before_coroutine,
        )


def _member(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def precreate_edge_children(
    parent: SemanticOperatorInstance,
    edges: Sequence[object],
) -> tuple[SemanticOperatorInstance, ...]:
    if not isinstance(parent, SemanticOperatorInstance):
        raise _fail("edge_parent_invalid")
    if isinstance(edges, (str, bytes)) or not isinstance(edges, Sequence):
        raise _fail("edge_children_invalid")
    children: list[SemanticOperatorInstance] = []
    duplicates: dict[tuple[str, str, str, str], int] = {}
    for ordinal, edge in enumerate(edges):
        edge_uuid = _text(_member(edge, "uuid"), "edge_uuid_invalid")
        source = _text(_member(edge, "source_node_uuid"), "edge_source_invalid")
        target = _text(_member(edge, "target_node_uuid"), "edge_target_invalid")
        fact = _text(_member(edge, "fact"), "edge_fact_invalid")
        fact_hash = hashlib.sha256(fact.encode("utf-8")).hexdigest()
        duplicate_key = (edge_uuid, source, target, fact_hash)
        duplicate_ordinal = duplicates.get(duplicate_key, 0)
        duplicates[duplicate_key] = duplicate_ordinal + 1
        children.append(
            SemanticOperatorInstance.create(
                graph_id=parent.graph_id,
                stream_id=parent.stream_id,
                source_sequence=parent.source_sequence,
                semantic_operator_type="EDGE_RESOLUTION_CHILD",
                classification=SemanticOperatorClass.STATE_DERIVED,
                parent_semantic_operator_ids=(parent.semantic_operator_id,),
                child_ordinal=ordinal,
                semantic_input_identity={
                    "canonical_fact_hash": fact_hash,
                    "duplicate_ordinal": duplicate_ordinal,
                    "extracted_edge_uuid": edge_uuid,
                    "pre_scheduling_ordinal": ordinal,
                    "source_node_uuid": source,
                    "target_node_uuid": target,
                },
                materialized_before_coroutine=True,
            )
        )
    return tuple(children)


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_type: OperatorEventType
    event_sequence: int
    timestamp_ns: int
    semantic_operator_id: str | None = None
    source_sequence: int | None = None
    transaction_id: str | None = None
    status: str = "OK"
    detail_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RequestSpan:
    request_id: str
    semantic_operator_id: str
    semantic_subrequest_role: str
    prompt_name: str
    submit_ns: int
    start_ns: int
    end_ns: int
    prompt_hash: str
    model_schema_hash: str
    response_hash: str


class SemanticDependencyTracker:
    """Emit readiness from completed direct predecessors exactly once."""

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        event_observer: Callable[[RuntimeEvent], object] | None = None,
    ) -> None:
        if not callable(clock_ns):
            raise _fail("runtime_clock_invalid")
        self._clock_ns = clock_ns
        self._event_observer = event_observer
        self._operators: dict[str, SemanticOperatorInstance] = {}
        self._completed: set[str] = set()
        self._ready: set[str] = set()
        self._immutable: dict[str, bool] = {}
        self._state: dict[str, bool] = {}
        self._direct_predecessors: dict[str, tuple[str, ...]] = {}
        self._events: list[RuntimeEvent] = []

    def _emit(self, kind: OperatorEventType, operator_id: str) -> None:
        event = RuntimeEvent(
            event_type=kind,
            event_sequence=len(self._events),
            timestamp_ns=self._clock_ns(),
            semantic_operator_id=operator_id,
            source_sequence=self._operators[operator_id].source_sequence,
        )
        self._events.append(event)
        if self._event_observer is not None:
            self._event_observer(event)

    def materialize(
        self,
        operator: SemanticOperatorInstance,
        *,
        immutable_inputs_exist: bool,
        state_satisfiable: bool,
        direct_predecessor_ids: tuple[str, ...] | None = None,
    ) -> None:
        if not isinstance(operator, SemanticOperatorInstance):
            raise _fail("runtime_operator_invalid")
        if operator.semantic_operator_id in self._operators:
            raise _fail("runtime_operator_duplicate")
        if not isinstance(immutable_inputs_exist, bool) or not isinstance(
            state_satisfiable, bool
        ):
            raise _fail("runtime_readiness_flag_invalid")
        self._operators[operator.semantic_operator_id] = operator
        self._immutable[operator.semantic_operator_id] = immutable_inputs_exist
        self._state[operator.semantic_operator_id] = state_satisfiable
        self._direct_predecessors[operator.semantic_operator_id] = (
            operator.parent_semantic_operator_ids
            if direct_predecessor_ids is None
            else tuple(direct_predecessor_ids)
        )
        self._emit(OperatorEventType.OPERATOR_MATERIALIZED, operator.semantic_operator_id)
        self._refresh_ready()

    def _refresh_ready(self) -> None:
        changed = True
        while changed:
            changed = False
            for operator_id, operator in tuple(self._operators.items()):
                if operator_id in self._ready:
                    continue
                if not self._immutable[operator_id] or not self._state[operator_id]:
                    continue
                if all(
                    parent in self._completed
                    for parent in self._direct_predecessors[operator_id]
                ):
                    self._ready.add(operator_id)
                    self._emit(OperatorEventType.OPERATOR_READY, operator_id)
                    changed = True

    def complete(self, operator_id: str) -> None:
        selected = _text(operator_id, "runtime_operator_id_invalid")
        if selected not in self._operators:
            raise _fail("runtime_operator_unknown")
        if selected in self._completed:
            return
        self._completed.add(selected)
        self._refresh_ready()

    def is_ready(self, operator_id: str) -> bool:
        return operator_id in self._ready

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)


_CURRENT_OPERATOR: ContextVar[str | None] = ContextVar(
    "meg_runtime_current_operator", default=None
)


class MEGRuntimeRecorder:
    """In-memory runtime evidence sink; it performs no provider operation."""

    def __init__(
        self,
        *,
        mode: InstrumentationMode,
        writer_domain: WriterDomainCertificate | None = None,
        clock_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if not isinstance(mode, InstrumentationMode):
            raise _fail("instrumentation_mode_invalid")
        if writer_domain is not None and not isinstance(
            writer_domain, WriterDomainCertificate
        ):
            raise _fail("writer_domain_invalid")
        self.mode = mode
        self.writer_domain = writer_domain
        self._clock_ns = clock_ns
        self._events: list[RuntimeEvent] = []
        self._operators: list[SemanticOperatorInstance] = []
        self._operator_ids: set[str] = set()
        self._request_spans: list[RequestSpan] = []
        self._request_ordinals: dict[str, int] = {}
        self._read_views: dict[str, RuntimeSemanticReadView] = {}
        self._manual_operator_id: str | None = None
        self._committed_transactions: set[str] = set()
        self.production_db_read_hashes: list[str] = []
        self.shadow_db_read_hashes: list[str] = []
        self.production_write_intent_hashes: list[str] = []
        self.persistent_effect_hashes: list[str] = []
        self.publication_order: list[int] = []
        self.tracker = SemanticDependencyTracker(
            clock_ns=clock_ns, event_observer=self._record_tracker_event
        )

    def _record_tracker_event(self, event: RuntimeEvent) -> None:
        self._events.append(replace(event, event_sequence=len(self._events)))

    def materialize(
        self,
        operator: SemanticOperatorInstance,
        *,
        immutable_inputs_exist: bool,
        state_satisfiable: bool,
        direct_predecessor_ids: tuple[str, ...] | None = None,
    ) -> None:
        self._operators.append(operator)
        self._operator_ids.add(operator.semantic_operator_id)
        self.tracker.materialize(
            operator,
            immutable_inputs_exist=immutable_inputs_exist,
            state_satisfiable=state_satisfiable,
            direct_predecessor_ids=direct_predecessor_ids,
        )

    def attach_read_view(
        self, operator_id: str, read_view: RuntimeSemanticReadView
    ) -> None:
        selected = _text(operator_id, "runtime_operator_id_invalid")
        if selected not in self._operator_ids:
            raise _fail("runtime_operator_unknown")
        if not isinstance(read_view, RuntimeSemanticReadView):
            raise _fail("runtime_read_view_invalid")
        if read_view.read_view.operator_instance_id != selected:
            raise _fail("runtime_read_view_operator_mismatch")
        if selected in self._read_views:
            raise _fail("runtime_read_view_duplicate")
        self._read_views[selected] = read_view

    def _emit(
        self,
        kind: OperatorEventType,
        *,
        operator_id: str | None = None,
        source_sequence: int | None = None,
        transaction_id: str | None = None,
        status: str = "OK",
        detail: object | None = None,
    ) -> RuntimeEvent:
        event = RuntimeEvent(
            event_type=kind,
            event_sequence=len(self._events),
            timestamp_ns=self._clock_ns(),
            semantic_operator_id=operator_id,
            source_sequence=source_sequence,
            transaction_id=transaction_id,
            status=status,
            detail_hash=None if detail is None else canonical_sha256(detail),
        )
        self._events.append(event)
        return event

    def start(self, operator_id: str) -> None:
        selected = _text(operator_id, "runtime_operator_id_invalid")
        if not self.tracker.is_ready(selected):
            raise _fail("operator_start_before_ready")
        self._manual_operator_id = selected
        self._emit(OperatorEventType.OPERATOR_START, operator_id=selected)

    def end(self, operator_id: str, *, status: str = "OK") -> None:
        selected = _text(operator_id, "runtime_operator_id_invalid")
        self._emit(OperatorEventType.OPERATOR_END, operator_id=selected, status=status)
        if status == "OK":
            self.tracker.complete(selected)
        if self._manual_operator_id == selected:
            self._manual_operator_id = None

    @contextmanager
    def operator_scope(self, operator_id: str):
        self.start(operator_id)
        token = _CURRENT_OPERATOR.set(operator_id)
        try:
            yield
        except BaseException:
            self.end(operator_id, status="ERROR")
            raise
        else:
            self.end(operator_id)
        finally:
            _CURRENT_OPERATOR.reset(token)

    def record_request(
        self,
        *,
        prompt_name: str,
        prompt_hash: str,
        model_schema_hash: str,
        response_hash: str,
        semantic_subrequest_role: str | None = None,
        submit_ns: int | None = None,
        start_ns: int | None = None,
        end_ns: int | None = None,
    ) -> RequestSpan:
        operator_id = _CURRENT_OPERATOR.get() or self._manual_operator_id
        if operator_id is None:
            raise _fail("request_operator_scope_missing")
        prompt_name = _text(prompt_name, "request_prompt_name_invalid")
        ordinal = self._request_ordinals.get(operator_id, 0)
        self._request_ordinals[operator_id] = ordinal + 1
        submit = self._clock_ns() if submit_ns is None else submit_ns
        start = submit if start_ns is None else start_ns
        end = self._clock_ns() if end_ns is None else end_ns
        if start < submit or end < start:
            raise _fail("request_timeline_invalid")
        subrole = semantic_subrequest_role or prompt_name
        request_id = f"meg-runtime-request-{canonical_sha256({'operator': operator_id, 'ordinal': ordinal, 'role': subrole})}"
        span = RequestSpan(
            request_id=request_id,
            semantic_operator_id=operator_id,
            semantic_subrequest_role=subrole,
            prompt_name=prompt_name,
            submit_ns=submit,
            start_ns=start,
            end_ns=end,
            prompt_hash=_text(prompt_hash, "request_prompt_hash_invalid"),
            model_schema_hash=_text(
                model_schema_hash, "request_model_schema_hash_invalid"
            ),
            response_hash=_text(response_hash, "request_response_hash_invalid"),
        )
        self._request_spans.append(span)
        return span

    def record_db_read(self, identity: object, *, shadow: bool = False) -> None:
        digest = canonical_sha256(identity)
        if shadow:
            if self.mode is not InstrumentationMode.SHADOW_READ:
                raise _fail("shadow_read_forbidden_in_observe_only")
            self.shadow_db_read_hashes.append(digest)
        else:
            self.production_db_read_hashes.append(digest)

    def record_write_intent(self, identity: object) -> None:
        self.production_write_intent_hashes.append(canonical_sha256(identity))

    def record_effect(self, identity: object) -> None:
        self.persistent_effect_hashes.append(canonical_sha256(identity))

    def record_transaction_commit(self, *, transaction_id: str) -> None:
        selected = _text(transaction_id, "transaction_id_invalid")
        if selected in self._committed_transactions:
            raise _fail("transaction_commit_duplicate")
        self._committed_transactions.add(selected)
        self._emit(
            OperatorEventType.TRANSACTION_COMMIT,
            transaction_id=selected,
            status=(
                "CERTIFIED"
                if self.writer_domain is not None and self.writer_domain.certified
                else "OPAQUE"
            ),
        )

    def record_publication(self, *, source_sequence: int, transaction_id: str) -> None:
        sequence = _ordinal(source_sequence, "publication_source_sequence_invalid")
        transaction = _text(transaction_id, "publication_transaction_invalid")
        certified = (
            self.writer_domain is not None
            and self.writer_domain.certified
            and transaction in self._committed_transactions
        )
        status = "CERTIFIED" if certified else "OPAQUE"
        self.publication_order.append(sequence)
        self._emit(
            OperatorEventType.PUBLICATION,
            source_sequence=sequence,
            transaction_id=transaction,
            status=status,
        )

    @property
    def events(self) -> tuple[RuntimeEvent, ...]:
        return tuple(self._events)

    @property
    def operators(self) -> tuple[SemanticOperatorInstance, ...]:
        return tuple(self._operators)

    @property
    def request_spans(self) -> tuple[RequestSpan, ...]:
        return tuple(self._request_spans)

    @property
    def read_views(self) -> tuple[RuntimeSemanticReadView, ...]:
        return tuple(self._read_views[operator.semantic_operator_id] for operator in self._operators if operator.semantic_operator_id in self._read_views)


T = TypeVar("T")


class TransactionCommitObserver(Generic[T]):
    """Advance the epoch once after managed execute_write returns successfully."""

    def __init__(
        self,
        *,
        mutation_epoch: StateMutationEpoch,
        recorder: MEGRuntimeRecorder | None = None,
    ) -> None:
        if not isinstance(mutation_epoch, StateMutationEpoch):
            raise _fail("transaction_epoch_invalid")
        self.mutation_epoch = mutation_epoch
        self.recorder = recorder
        self._ordinal = 0
        self.last_transaction_id: str | None = None

    async def execute_write(
        self,
        session: object,
        callback: Callable[..., Awaitable[T]],
        *args: object,
        **kwargs: object,
    ) -> T:
        execute_write = getattr(session, "execute_write", None)
        if not callable(execute_write) or not callable(callback):
            raise _fail("managed_transaction_invalid")
        ordinal = self._ordinal
        self._ordinal += 1
        transaction_id = (
            f"meg-managed-tx-{canonical_sha256({'namespace': self.mutation_epoch.namespace, 'ordinal': ordinal})}"
        )
        result = execute_write(callback, *args, **kwargs)
        if not inspect.isawaitable(result):
            raise _fail("managed_transaction_not_awaitable")
        value = await result
        self.mutation_epoch.record_commit(transaction_id=transaction_id)
        self.last_transaction_id = transaction_id
        if self.recorder is not None:
            self.recorder.record_transaction_commit(transaction_id=transaction_id)
        return value


@dataclass(frozen=True, slots=True)
class RuntimeSemanticReadView:
    read_view: SemanticReadView
    writer_domain_certificate: WriterDomainCertificate

    @property
    def status(self) -> ReadViewStatus:
        return self.read_view.status


def capture_runtime_read_view(
    *,
    graph_id: str,
    stream_id: str,
    source_sequence: int,
    operator_instance_id: str,
    memory_version_token: MemoryVersionToken,
    mutation_epoch: StateMutationEpoch,
    writer_domain: WriterDomainCertificate,
    read_kind: ReadKind,
    materialize: Callable[[], ReadMaterialization],
) -> RuntimeSemanticReadView:
    if not isinstance(writer_domain, WriterDomainCertificate):
        raise _fail("writer_domain_invalid")
    if writer_domain.namespace != graph_id:
        raise _fail("writer_domain_namespace_mismatch")
    view = capture_semantic_read_view(
        graph_id=graph_id,
        stream_id=stream_id,
        source_sequence=source_sequence,
        operator_instance_id=operator_instance_id,
        memory_version_token=memory_version_token,
        mutation_epoch=mutation_epoch,
        read_kind=read_kind,
        materialize=materialize,
    )
    if not writer_domain.certified and view.status is ReadViewStatus.STABLE_READVIEW:
        view = replace(view, status=ReadViewStatus.OPAQUE, read_view_digest=None)
    return RuntimeSemanticReadView(
        read_view=view,
        writer_domain_certificate=writer_domain,
    )


def runtime_read_view_from_epoch_window(
    *,
    graph_id: str,
    stream_id: str,
    source_sequence: int,
    operator_instance_id: str,
    memory_version_token: MemoryVersionToken,
    mutation_epoch_before: MutationEpochToken,
    mutation_epoch_after: MutationEpochToken,
    writer_domain: WriterDomainCertificate,
    read_kind: ReadKind,
    materialized: ReadMaterialization,
) -> RuntimeSemanticReadView:
    if not isinstance(writer_domain, WriterDomainCertificate):
        raise _fail("writer_domain_invalid")
    if writer_domain.namespace != graph_id:
        raise _fail("writer_domain_namespace_mismatch")
    view = semantic_read_view_from_materialization(
        graph_id=graph_id,
        stream_id=stream_id,
        source_sequence=source_sequence,
        operator_instance_id=operator_instance_id,
        memory_version_token=memory_version_token,
        mutation_epoch_before=mutation_epoch_before,
        mutation_epoch_after=mutation_epoch_after,
        read_kind=read_kind,
        materialized=materialized,
    )
    if not writer_domain.certified and view.status is ReadViewStatus.STABLE_READVIEW:
        view = replace(view, status=ReadViewStatus.OPAQUE, read_view_digest=None)
    return RuntimeSemanticReadView(view, writer_domain)


__all__ = [
    "InstrumentationMode",
    "MEGRuntimeRecorder",
    "OperatorEventType",
    "RequestSpan",
    "RuntimeEvent",
    "RuntimeInstrumentationError",
    "RuntimeOperatorContract",
    "RuntimeSemanticReadView",
    "SemanticDependencyTracker",
    "SemanticOperatorClass",
    "SemanticOperatorInstance",
    "TransactionCommitObserver",
    "WriterDomainCertificate",
    "WriterDomainStatus",
    "canonical_sha256",
    "capture_runtime_read_view",
    "classify_operator",
    "precreate_edge_children",
    "runtime_read_view_from_epoch_window",
]
