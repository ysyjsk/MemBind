"""Pure MEG semantic-adapter contracts and deterministic operator lineage.

This module is the adapter-facing half of the design-only compiler.  L0
records describe what an operator *may* do.  They deliberately cannot contain
an observed state version, read scope, or effect scope.  L1 lineage records are
materialized from immutable input identity before an async child is launched;
completion order is never used as identity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .semantic_contract import EffectKind, OperatorType, Visibility


class SemanticAdapterError(ValueError):
    """A static adapter contract or dynamic lineage record is unsafe."""


def _fail(code: str) -> SemanticAdapterError:
    return SemanticAdapterError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _seq(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _optional_seq(value: object, code: str) -> int | None:
    return None if value is None else _seq(value, code)


class BoundaryStatus(str, Enum):
    QUALIFIED = "QUALIFIED"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


class GraphitiOperatorKind(str, Enum):
    SEMANTIC = "SEMANTIC"
    HELPER = "HELPER"
    MUTATION = "MUTATION"
    TRANSACTION = "TRANSACTION"
    PUBLICATION = "PUBLICATION"


_FORBIDDEN_IDENTITY_FIELDS = {
    "completion_order",
    "request_order",
    "latency",
    "latency_ns",
    "token_count",
    "timestamp",
    "timestamp_ns",
    "finish_time",
    "finish_ns",
}


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        selected: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise _fail("child_key_field_invalid")
            selected[key] = _canonical_value(item)
        return {key: selected[key] for key in sorted(selected)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise _fail("child_key_value_invalid")


@dataclass(frozen=True, slots=True)
class ChildKey:
    """Immutable semantic input key for one async child operation."""

    fields: tuple[tuple[str, object], ...]
    duplicate_ordinal: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.fields, tuple) or not self.fields:
            raise _fail("child_key_empty")
        names: list[str] = []
        for name, value in self.fields:
            if not isinstance(name, str) or not name or name.strip() != name:
                raise _fail("child_key_field_invalid")
            if name.casefold() in _FORBIDDEN_IDENTITY_FIELDS:
                raise _fail("heuristic_identity_forbidden")
            _canonical_value(value)
            names.append(name)
        if len(names) != len(set(names)) or tuple(sorted(names)) != tuple(names):
            raise _fail("child_key_fields_not_canonical")
        if self.duplicate_ordinal is not None:
            _seq(self.duplicate_ordinal, "duplicate_ordinal_invalid")

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, object],
        *,
        duplicate_ordinal: int | None = None,
    ) -> "ChildKey":
        if not isinstance(values, Mapping) or not values:
            raise _fail("child_key_mapping_invalid")
        normalized: dict[str, object] = {}
        for name, value in values.items():
            if not isinstance(name, str) or not name or name.strip() != name:
                raise _fail("child_key_field_invalid")
            if name.casefold() in _FORBIDDEN_IDENTITY_FIELDS:
                raise _fail("heuristic_identity_forbidden")
            normalized[name] = _canonical_value(value)
        return cls(
            fields=tuple(sorted(normalized.items(), key=lambda item: item[0])),
            duplicate_ordinal=duplicate_ordinal,
        )

    def canonical_json(self) -> str:
        payload = {
            "duplicate_ordinal": self.duplicate_ordinal,
            "fields": {name: _canonical_value(value) for name, value in self.fields},
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def derive_operator_instance_id(
    *,
    graph_id: str,
    stream_id: str,
    source_sequence: int,
    semantic_role: str,
    adapter_revision: str,
    parent_operator_instance_id: str | None = None,
    child_key: ChildKey | None = None,
    operator_ordinal: int = 0,
    completion_order: object | None = None,
    request_order: object | None = None,
    latency_ns: object | None = None,
    token_count: object | None = None,
) -> str:
    """Derive an identity from explicit immutable attribution only."""

    if any(value is not None for value in (completion_order, request_order, latency_ns, token_count)):
        raise _fail("heuristic_identity_forbidden")
    graph = _text(graph_id, "graph_id_invalid")
    stream = _text(stream_id, "stream_id_invalid")
    sequence = _seq(source_sequence, "source_sequence_invalid")
    role = _text(semantic_role, "semantic_role_invalid")
    revision = _text(adapter_revision, "adapter_revision_invalid")
    ordinal = _seq(operator_ordinal, "operator_ordinal_invalid")
    if parent_operator_instance_id is not None:
        parent_operator_instance_id = _text(
            parent_operator_instance_id, "parent_operator_instance_id_invalid"
        )
    if child_key is not None and not isinstance(child_key, ChildKey):
        raise _fail("child_key_invalid")
    payload = {
        "adapter_revision": revision,
        "child_key": None if child_key is None else child_key.canonical_json(),
        "graph_id": graph,
        "operator_ordinal": ordinal,
        "parent_operator_instance_id": parent_operator_instance_id,
        "semantic_role": role,
        "source_sequence": sequence,
        "stream_id": stream,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"meg-op-{digest}"


def derive_request_instance_id(
    *,
    operator_instance_id: str,
    semantic_subrole: str,
    request_ordinal: int,
) -> str:
    """Identify one logical subrequest without transport or timing heuristics."""

    payload = {
        "operator_instance_id": _text(
            operator_instance_id, "request_operator_instance_id_invalid"
        ),
        "request_ordinal": _seq(request_ordinal, "request_ordinal_invalid"),
        "semantic_subrole": _text(semantic_subrole, "request_subrole_invalid"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"meg-request-{digest}"


@dataclass(frozen=True, slots=True)
class RequestLineage:
    """L1 lineage for a sequential or parallel request inside one operator."""

    operator_instance_id: str
    request_instance_id: str
    semantic_subrole: str
    request_ordinal: int
    coroutine_id: str
    created_ns: int
    enqueue_ns: int
    start_ns: int
    end_ns: int
    transport_request_id: str | None = None

    def __post_init__(self) -> None:
        expected = derive_request_instance_id(
            operator_instance_id=self.operator_instance_id,
            semantic_subrole=self.semantic_subrole,
            request_ordinal=self.request_ordinal,
        )
        if self.request_instance_id != expected:
            raise _fail("request_lineage_identity_mismatch")
        _text(self.coroutine_id, "request_coroutine_id_invalid")
        created = _seq(self.created_ns, "request_created_ns_invalid")
        enqueue = _seq(self.enqueue_ns, "request_enqueue_ns_invalid")
        start = _seq(self.start_ns, "request_start_ns_invalid")
        end = _seq(self.end_ns, "request_end_ns_invalid")
        if enqueue < created:
            raise _fail("request_enqueue_before_created")
        if start < enqueue:
            raise _fail("request_start_before_enqueue")
        if end < start:
            raise _fail("request_end_before_start")
        if self.transport_request_id is not None:
            _text(self.transport_request_id, "transport_request_id_invalid")

    @classmethod
    def create(
        cls,
        *,
        operator_instance_id: str,
        semantic_subrole: str,
        request_ordinal: int,
        coroutine_id: str,
        created_ns: int,
        enqueue_ns: int,
        start_ns: int,
        end_ns: int,
        transport_request_id: str | None = None,
    ) -> "RequestLineage":
        return cls(
            operator_instance_id=operator_instance_id,
            request_instance_id=derive_request_instance_id(
                operator_instance_id=operator_instance_id,
                semantic_subrole=semantic_subrole,
                request_ordinal=request_ordinal,
            ),
            semantic_subrole=semantic_subrole,
            request_ordinal=request_ordinal,
            coroutine_id=coroutine_id,
            created_ns=created_ns,
            enqueue_ns=enqueue_ns,
            start_ns=start_ns,
            end_ns=end_ns,
            transport_request_id=transport_request_id,
        )


@dataclass(frozen=True, slots=True)
class OperatorLineage:
    """L1 dynamic identity and timing attribution for one semantic operator."""

    graph_id: str
    stream_id: str
    source_sequence: int
    semantic_role: str
    adapter_revision: str
    instance_id: str
    parent_operator_instance_id: str | None
    child_key: ChildKey | None
    operator_ordinal: int
    created_ns: int
    ready_ns: int | None
    enqueue_ns: int | None = None
    start_ns: int | None = None
    end_ns: int | None = None
    request_id: str | None = None
    coroutine_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.graph_id, "graph_id_invalid")
        _text(self.stream_id, "stream_id_invalid")
        _seq(self.source_sequence, "source_sequence_invalid")
        _text(self.semantic_role, "semantic_role_invalid")
        _text(self.adapter_revision, "adapter_revision_invalid")
        expected = derive_operator_instance_id(
            graph_id=self.graph_id,
            stream_id=self.stream_id,
            source_sequence=self.source_sequence,
            semantic_role=self.semantic_role,
            adapter_revision=self.adapter_revision,
            parent_operator_instance_id=self.parent_operator_instance_id,
            child_key=self.child_key,
            operator_ordinal=self.operator_ordinal,
        )
        if self.instance_id != expected:
            raise _fail("lineage_identity_mismatch")
        if self.child_key is not None and not isinstance(self.child_key, ChildKey):
            raise _fail("child_key_invalid")
        _seq(self.operator_ordinal, "operator_ordinal_invalid")
        created = _seq(self.created_ns, "created_ns_invalid")
        ready = _optional_seq(self.ready_ns, "ready_ns_invalid")
        enqueue = _optional_seq(self.enqueue_ns, "enqueue_ns_invalid")
        start = _optional_seq(self.start_ns, "start_ns_invalid")
        end = _optional_seq(self.end_ns, "end_ns_invalid")
        if ready is not None and ready < created:
            raise _fail("ready_before_created")
        if enqueue is not None and enqueue < created:
            raise _fail("enqueue_before_created")
        if enqueue is not None and ready is not None and enqueue < ready:
            raise _fail("enqueue_before_ready")
        if start is not None and (start < created or (ready is not None and start < ready)):
            raise _fail("start_before_ready")
        if start is not None and enqueue is not None and start < enqueue:
            raise _fail("start_before_enqueue")
        if end is not None and (start is None or end < start):
            raise _fail("end_before_start")
        for value, code in (
            (self.request_id, "request_id_invalid"),
            (self.coroutine_id, "coroutine_id_invalid"),
        ):
            if value is not None:
                _text(value, code)

    @classmethod
    def create(
        cls,
        *,
        graph_id: str,
        stream_id: str,
        source_sequence: int,
        semantic_role: str,
        adapter_revision: str,
        parent_operator_instance_id: str | None = None,
        child_key: ChildKey | None = None,
        operator_ordinal: int = 0,
        created_ns: int = 0,
        ready_ns: int | None = None,
        enqueue_ns: int | None = None,
        start_ns: int | None = None,
        end_ns: int | None = None,
        request_id: str | None = None,
        coroutine_id: str | None = None,
    ) -> "OperatorLineage":
        instance_id = derive_operator_instance_id(
            graph_id=graph_id,
            stream_id=stream_id,
            source_sequence=source_sequence,
            semantic_role=semantic_role,
            adapter_revision=adapter_revision,
            parent_operator_instance_id=parent_operator_instance_id,
            child_key=child_key,
            operator_ordinal=operator_ordinal,
        )
        return cls(
            graph_id=graph_id,
            stream_id=stream_id,
            source_sequence=source_sequence,
            semantic_role=semantic_role,
            adapter_revision=adapter_revision,
            instance_id=instance_id,
            parent_operator_instance_id=parent_operator_instance_id,
            child_key=child_key,
            operator_ordinal=operator_ordinal,
            created_ns=created_ns,
            ready_ns=ready_ns,
            enqueue_ns=enqueue_ns,
            start_ns=start_ns,
            end_ns=end_ns,
            request_id=request_id,
            coroutine_id=coroutine_id,
        )

    @classmethod
    def child(
        cls,
        parent: "OperatorLineage",
        *,
        semantic_role: str,
        child_key: ChildKey,
        operator_ordinal: int = 0,
        created_ns: int | None = None,
        ready_ns: int | None = None,
        enqueue_ns: int | None = None,
        start_ns: int | None = None,
        end_ns: int | None = None,
        request_id: str | None = None,
        coroutine_id: str | None = None,
    ) -> "OperatorLineage":
        if not isinstance(parent, OperatorLineage):
            raise _fail("parent_lineage_invalid")
        if not isinstance(child_key, ChildKey):
            raise _fail("missing_child_key")
        return cls.create(
            graph_id=parent.graph_id,
            stream_id=parent.stream_id,
            source_sequence=parent.source_sequence,
            semantic_role=semantic_role,
            adapter_revision=parent.adapter_revision,
            parent_operator_instance_id=parent.instance_id,
            child_key=child_key,
            operator_ordinal=operator_ordinal,
            created_ns=parent.created_ns if created_ns is None else created_ns,
            ready_ns=ready_ns,
            enqueue_ns=enqueue_ns,
            start_ns=start_ns,
            end_ns=end_ns,
            request_id=request_id,
            coroutine_id=coroutine_id,
        )


class LineageBuilder:
    """Construct children before scheduling and reject ambiguous fan-out."""

    def __init__(self, parent: OperatorLineage) -> None:
        if not isinstance(parent, OperatorLineage):
            raise _fail("parent_lineage_invalid")
        self.parent = parent
        self._keys: set[str] = set()
        self._children: list[OperatorLineage] = []

    def add_child(
        self,
        *,
        semantic_role: str,
        child_key: ChildKey | None,
        operator_ordinal: int = 0,
        **kwargs: object,
    ) -> OperatorLineage:
        if child_key is None:
            raise _fail("missing_child_key")
        if not isinstance(child_key, ChildKey):
            raise _fail("child_key_invalid")
        canonical = child_key.canonical_json()
        if canonical in self._keys:
            raise _fail("duplicate_child_key")
        self._keys.add(canonical)
        child = OperatorLineage.child(
            self.parent,
            semantic_role=semantic_role,
            child_key=child_key,
            operator_ordinal=operator_ordinal,
            **kwargs,
        )
        self._children.append(child)
        return child

    def finalize(
        self,
        *,
        expected_keys: tuple[ChildKey, ...] | None = None,
    ) -> tuple[OperatorLineage, ...]:
        if expected_keys is not None:
            if not isinstance(expected_keys, tuple):
                raise _fail("expected_child_keys_invalid")
            expected = {key.canonical_json() for key in expected_keys}
            missing = expected - self._keys
            if missing:
                raise _fail("missing_child_key")
            if self._keys - expected:
                raise _fail("unexpected_child_key")
        return tuple(self._children)


@dataclass(frozen=True, slots=True)
class StaticSemanticContract:
    """L0 declaration: possible semantics, never observed dynamic facts."""

    operator_role: str
    operator_type: OperatorType
    namespace: str
    state_bound: bool
    effect_kind: EffectKind
    visibility: Visibility
    atomic: bool
    idempotent: bool
    retry_safe: bool
    publication_boundary: bool
    dependency_class: str
    resource_class: str
    child_identity_mode: str
    # These fields exist only to fail loudly if an adapter tries to smuggle L2
    # facts into L0.  They are never accepted as declarations.
    state_version: object | None = None
    effect_scope: object | None = None

    def __post_init__(self) -> None:
        _text(self.operator_role, "operator_role_invalid")
        if not isinstance(self.operator_type, OperatorType):
            raise _fail("operator_type_invalid")
        _text(self.namespace, "namespace_invalid")
        if not isinstance(self.state_bound, bool):
            raise _fail("state_bound_invalid")
        if not isinstance(self.effect_kind, EffectKind):
            raise _fail("effect_kind_invalid")
        if not isinstance(self.visibility, Visibility):
            raise _fail("visibility_invalid")
        for value, code in (
            (self.atomic, "atomic_invalid"),
            (self.idempotent, "idempotent_invalid"),
            (self.retry_safe, "retry_safe_invalid"),
            (self.publication_boundary, "publication_boundary_invalid"),
        ):
            if not isinstance(value, bool):
                raise _fail(code)
        _text(self.dependency_class, "dependency_class_invalid")
        _text(self.resource_class, "resource_class_invalid")
        _text(self.child_identity_mode, "child_identity_mode_invalid")
        if self.state_version is not None or self.effect_scope is not None:
            raise _fail("dynamic_fact_in_static_contract")
        if self.publication_boundary and self.visibility is not Visibility.PUBLISHED_STATE:
            raise _fail("publication_visibility_required")
        if self.visibility is Visibility.PUBLISHED_STATE and not self.publication_boundary:
            raise _fail("publication_boundary_required")
        if self.publication_boundary and (not self.atomic or self.effect_kind is EffectKind.NONE):
            raise _fail("publication_contract_invalid")


@dataclass(frozen=True, slots=True)
class BoundaryQualification:
    status: BoundaryStatus
    codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphitiOperatorBoundary:
    operation: str
    kind: GraphitiOperatorKind
    operator_type: OperatorType | None
    state_bound: bool
    effect_kind: EffectKind
    requires_child_boundary: bool
    child_identity_mode: str
    stable: bool
    l2_hook: str | None
    notes: str


def graphiti_operator_catalog() -> tuple[GraphitiOperatorBoundary, ...]:
    """Static audit of the pinned Graphiti 0.29.3 semantic path."""

    return (
        GraphitiOperatorBoundary(
            "extract_nodes",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.EXTRACTION,
            False,
            EffectKind.NONE,
            False,
            "single",
            True,
            None,
            "LLM evidence extraction; output UUIDs are stable input evidence.",
        ),
        GraphitiOperatorBoundary(
            "extract_edges",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.EXTRACTION,
            False,
            EffectKind.NONE,
            False,
            "single_or_episode_batch",
            True,
            None,
            "One extraction flight; its edge records key downstream children.",
        ),
        GraphitiOperatorBoundary(
            "resolve_extracted_nodes",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.RESOLUTION,
            True,
            EffectKind.NONE,
            True,
            "node_uuid_or_sorted_unresolved_set",
            True,
            "candidate/read instrumentation",
            "Per-node candidate reads plus one sorted unresolved-set LLM resolution.",
        ),
        GraphitiOperatorBoundary(
            "resolve_edge_pointers",
            GraphitiOperatorKind.HELPER,
            None,
            False,
            EffectKind.NONE,
            False,
            "none",
            True,
            None,
            "Pure UUID rematerialization helper, not an independent semantic effect.",
        ),
        GraphitiOperatorBoundary(
            "resolve_extracted_edges",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.RESOLUTION,
            True,
            EffectKind.NONE,
            True,
            "edge_uuid_endpoint_fact_hash",
            True,
            "candidate/read instrumentation",
            "Parent fan-out plus per-edge child; sequential subcalls stay under child lineage.",
        ),
        GraphitiOperatorBoundary(
            "resolve_extracted_edge",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.RESOLUTION,
            True,
            EffectKind.NONE,
            True,
            "edge_uuid_endpoint_fact_hash",
            True,
            "LLM child instrumentation",
            "Dedupe, attributes, timestamps, and contradiction checks are child steps.",
        ),
        GraphitiOperatorBoundary(
            "extract_attributes_from_nodes",
            GraphitiOperatorKind.SEMANTIC,
            OperatorType.SUMMARIZATION,
            True,
            EffectKind.NONE,
            True,
            "node_uuid_or_batch_set",
            True,
            "semaphore_gather child wrapper",
            "Per-node attributes and sorted batch summary flight have separate keys.",
        ),
        GraphitiOperatorBoundary(
            "process_episode_data",
            GraphitiOperatorKind.MUTATION,
            OperatorType.MUTATION,
            True,
            EffectKind.MERGE,
            True,
            "episode_uuid",
            True,
            "mutation wrapper",
            "Intent assembly plus saga writes; commit is not implied by return.",
        ),
        GraphitiOperatorBoundary(
            "add_nodes_and_edges_bulk_tx",
            GraphitiOperatorKind.TRANSACTION,
            OperatorType.MUTATION,
            True,
            EffectKind.MERGE,
            True,
            "transaction_id",
            True,
            "Neo4j transaction wrapper",
            "Commit is effect completion and immediate visibility; attach publication evidence.",
        ),
        GraphitiOperatorBoundary(
            "publication",
            GraphitiOperatorKind.PUBLICATION,
            OperatorType.PUBLICATION,
            True,
            EffectKind.UPDATE,
            False,
            "source_sequence",
            True,
            "durable publication journal",
            "Separate only for delayed visibility; Neo4j attaches this event to transaction commit.",
        ),
    )


def qualify_operator_boundary(boundary: GraphitiOperatorBoundary) -> BoundaryQualification:
    if not isinstance(boundary, GraphitiOperatorBoundary):
        raise _fail("operator_boundary_invalid")
    invalid: list[str] = []
    if not boundary.stable:
        invalid.append("unstable_boundary")
    if boundary.kind is not GraphitiOperatorKind.HELPER and boundary.operator_type is None:
        invalid.append("semantic_operator_type_missing")
    if boundary.requires_child_boundary and boundary.child_identity_mode in {"", "none"}:
        invalid.append("child_identity_strategy_missing")
    if invalid:
        return BoundaryQualification(BoundaryStatus.INVALID, tuple(invalid))
    if boundary.l2_hook is None and (boundary.state_bound or boundary.kind in {GraphitiOperatorKind.MUTATION, GraphitiOperatorKind.PUBLICATION}):
        return BoundaryQualification(BoundaryStatus.OPAQUE, ("l2_hook_required",))
    return BoundaryQualification(BoundaryStatus.QUALIFIED)


__all__ = [
    "BoundaryQualification",
    "BoundaryStatus",
    "ChildKey",
    "GraphitiOperatorBoundary",
    "GraphitiOperatorKind",
    "LineageBuilder",
    "OperatorLineage",
    "RequestLineage",
    "SemanticAdapterError",
    "StaticSemanticContract",
    "derive_operator_instance_id",
    "derive_request_instance_id",
    "graphiti_operator_catalog",
    "qualify_operator_boundary",
]
