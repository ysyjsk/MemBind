"""Backend-neutral semantic contracts for the design-only MSEG front-end."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MSEGContractError(ValueError):
    """A semantic contract or observation is malformed."""


def _fail(code: str) -> MSEGContractError:
    return MSEGContractError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _fail(code)
    return value


def _scope(value: object, code: str) -> frozenset[str]:
    if not isinstance(value, (set, frozenset)):
        raise _fail(code)
    selected = frozenset(value)
    if any(not isinstance(item, str) or not item or item.strip() != item for item in selected):
        raise _fail(code)
    return selected


def _optional_scope(value: object, code: str) -> frozenset[str] | None:
    return None if value is None else _scope(value, code)


def _bool(value: object, code: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(code)
    return value


class OperatorType(str, Enum):
    EXTRACTION = "EXTRACTION"
    RETRIEVAL = "RETRIEVAL"
    RESOLUTION = "RESOLUTION"
    MUTATION = "MUTATION"
    SUMMARIZATION = "SUMMARIZATION"
    PUBLICATION = "PUBLICATION"


class EffectKind(str, Enum):
    NONE = "NONE"
    ADD = "ADD"
    UPDATE = "UPDATE"
    MERGE = "MERGE"
    INVALIDATE = "INVALIDATE"


class Visibility(str, Enum):
    PRIVATE_INTERMEDIATE = "PRIVATE_INTERMEDIATE"
    PUBLISHED_STATE = "PUBLISHED_STATE"


@dataclass(frozen=True, slots=True)
class StateContract:
    namespace: str
    state_bound: bool
    version: str | None
    read_scope: frozenset[str] | None

    def __post_init__(self) -> None:
        _text(self.namespace, "state_namespace_invalid")
        _bool(self.state_bound, "state_bound_invalid")
        if self.state_bound:
            _text(self.version, "state_version_required")
            if self.read_scope is not None:
                _scope(self.read_scope, "state_read_scope_invalid")
        else:
            if self.version is not None:
                raise _fail("unbound_state_has_version")
            if self.read_scope != frozenset():
                raise _fail("unbound_state_scope_invalid")

    @classmethod
    def unbound(cls, *, namespace: str) -> "StateContract":
        return cls(
            namespace=_text(namespace, "state_namespace_invalid"),
            state_bound=False,
            version=None,
            read_scope=frozenset(),
        )

    @classmethod
    def bound(
        cls,
        *,
        namespace: str,
        version: str,
        read_scope: set[str] | frozenset[str] | None,
    ) -> "StateContract":
        return cls(
            namespace=_text(namespace, "state_namespace_invalid"),
            state_bound=True,
            version=_text(version, "state_version_required"),
            read_scope=_optional_scope(read_scope, "state_read_scope_invalid"),
        )

    @property
    def complete(self) -> bool:
        return not self.state_bound or self.read_scope is not None


@dataclass(frozen=True, slots=True)
class EffectContract:
    namespace: str
    kind: EffectKind
    scope: frozenset[str] | None

    def __post_init__(self) -> None:
        _text(self.namespace, "effect_namespace_invalid")
        if not isinstance(self.kind, EffectKind):
            raise _fail("effect_kind_invalid")
        if self.kind is EffectKind.NONE:
            if self.scope != frozenset():
                raise _fail("none_effect_scope_invalid")
        elif self.scope is not None:
            _scope(self.scope, "effect_scope_invalid")

    @classmethod
    def none(cls, *, namespace: str) -> "EffectContract":
        return cls(
            namespace=_text(namespace, "effect_namespace_invalid"),
            kind=EffectKind.NONE,
            scope=frozenset(),
        )

    @classmethod
    def write(
        cls,
        *,
        namespace: str,
        kind: EffectKind,
        scope: set[str] | frozenset[str] | None,
    ) -> "EffectContract":
        if kind is EffectKind.NONE:
            raise _fail("write_effect_kind_required")
        return cls(
            namespace=_text(namespace, "effect_namespace_invalid"),
            kind=kind,
            scope=_optional_scope(scope, "effect_scope_invalid"),
        )

    @property
    def complete(self) -> bool:
        return self.scope is not None


@dataclass(frozen=True, slots=True)
class SemanticContract:
    contract_id: str
    operator_type: OperatorType
    state: StateContract
    effect: EffectContract
    visibility: Visibility
    atomic: bool
    idempotent: bool
    retry_safe: bool
    publication_boundary: bool

    def __post_init__(self) -> None:
        _text(self.contract_id, "contract_id_invalid")
        if not isinstance(self.operator_type, OperatorType):
            raise _fail("operator_type_invalid")
        if not isinstance(self.state, StateContract):
            raise _fail("state_contract_invalid")
        if not isinstance(self.effect, EffectContract):
            raise _fail("effect_contract_invalid")
        if not isinstance(self.visibility, Visibility):
            raise _fail("visibility_invalid")
        _bool(self.atomic, "atomic_invalid")
        _bool(self.idempotent, "idempotent_invalid")
        _bool(self.retry_safe, "retry_safe_invalid")
        _bool(self.publication_boundary, "publication_boundary_invalid")
        if self.publication_boundary and self.visibility is not Visibility.PUBLISHED_STATE:
            raise _fail("publication_visibility_required")
        if self.visibility is Visibility.PUBLISHED_STATE and not self.publication_boundary:
            raise _fail("publication_boundary_required")
        if self.publication_boundary and not self.atomic:
            raise _fail("publication_atomic_required")
        if self.publication_boundary and self.effect.kind is EffectKind.NONE:
            raise _fail("publication_effect_required")

    @property
    def complete(self) -> bool:
        return self.state.complete and self.effect.complete


@dataclass(frozen=True, slots=True)
class SemanticOperator:
    instance_id: str
    semantic_identity: str
    evidence_ids: tuple[str, ...]
    contract: SemanticContract
    control_predecessors: frozenset[str]

    def __post_init__(self) -> None:
        _text(self.instance_id, "instance_id_invalid")
        _text(self.semantic_identity, "semantic_identity_invalid")
        if not isinstance(self.evidence_ids, tuple):
            raise _fail("evidence_ids_invalid")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise _fail("evidence_ids_duplicate")
        if any(
            not isinstance(item, str) or not item or item.strip() != item
            for item in self.evidence_ids
        ):
            raise _fail("evidence_id_invalid")
        if not isinstance(self.contract, SemanticContract):
            raise _fail("semantic_contract_invalid")
        if not isinstance(self.control_predecessors, frozenset):
            raise _fail("control_predecessors_invalid")
        if any(
            not isinstance(item, str) or not item or item.strip() != item
            for item in self.control_predecessors
        ):
            raise _fail("control_predecessor_invalid")
        if self.instance_id in self.control_predecessors:
            raise _fail("control_self_dependency")


@dataclass(frozen=True, slots=True)
class ObservedExecution:
    instance_id: str
    semantic_identity: str | None
    state_version: str | None
    read_scope: frozenset[str] | None
    effect_kind: EffectKind | None
    effect_scope: frozenset[str] | None
    effect_namespace: str | None
    terminal: bool
    effect_committed: bool
    published: bool

    def __post_init__(self) -> None:
        _text(self.instance_id, "observed_instance_id_invalid")
        if self.semantic_identity is not None:
            _text(self.semantic_identity, "observed_semantic_identity_invalid")
        if self.state_version is not None:
            _text(self.state_version, "observed_state_version_invalid")
        if self.read_scope is not None:
            _scope(self.read_scope, "observed_read_scope_invalid")
        if self.effect_kind is not None and not isinstance(self.effect_kind, EffectKind):
            raise _fail("observed_effect_kind_invalid")
        if self.effect_scope is not None:
            _scope(self.effect_scope, "observed_effect_scope_invalid")
        if self.effect_namespace is not None:
            _text(self.effect_namespace, "observed_effect_namespace_invalid")
        _bool(self.terminal, "observed_terminal_invalid")
        _bool(self.effect_committed, "observed_effect_committed_invalid")
        _bool(self.published, "observed_published_invalid")


__all__ = [
    "EffectContract",
    "EffectKind",
    "MSEGContractError",
    "ObservedExecution",
    "OperatorType",
    "SemanticContract",
    "SemanticOperator",
    "StateContract",
    "Visibility",
]
