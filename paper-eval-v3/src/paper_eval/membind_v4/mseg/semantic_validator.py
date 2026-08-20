"""Fail-closed validation for the design-only MSEG contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .conflict import ConflictClass, MemoryScope, classify_operator_conflict
from .semantic_contract import EffectKind, ObservedExecution, SemanticOperator
from .semantic_evidence import CertificationLevel, ExecutionEvidence


class MSEGValidationError(ValueError):
    """Validation input is malformed."""


def _fail(code: str) -> MSEGValidationError:
    return MSEGValidationError(code)


class CertificationStatus(str, Enum):
    CERTIFIED_PRIVATE = "CERTIFIED_PRIVATE"
    CERTIFIED_PUBLISHABLE = "CERTIFIED_PUBLISHABLE"
    OPAQUE = "OPAQUE"
    INVALID = "INVALID"


class ReadinessStatus(str, Enum):
    CERTIFIED_READY = "CERTIFIED_READY"
    CERTIFIED_BLOCKED = "CERTIFIED_BLOCKED"
    UNRESOLVED = "UNRESOLVED"


class ReorderStatus(str, Enum):
    CERTIFIED = "CERTIFIED"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    status: CertificationStatus
    codes: tuple[str, ...]

    @property
    def publishable(self) -> bool:
        return self.status is CertificationStatus.CERTIFIED_PUBLISHABLE


def _check_types(operator: SemanticOperator, observed: ObservedExecution) -> None:
    if not isinstance(operator, SemanticOperator):
        raise _fail("semantic_operator_invalid")
    if not isinstance(observed, ObservedExecution):
        raise _fail("observed_execution_invalid")


def validate_execution(
    operator: SemanticOperator,
    observed: ObservedExecution,
) -> ValidationResult:
    _check_types(operator, observed)
    invalid: list[str] = []
    opaque: list[str] = []
    contract = operator.contract

    if observed.instance_id != operator.instance_id:
        invalid.append("instance_id_mismatch")
    if observed.semantic_identity is None:
        opaque.append("semantic_identity_not_observable")
    elif observed.semantic_identity != operator.semantic_identity:
        invalid.append("semantic_identity_mismatch")
    if not observed.terminal:
        opaque.append("execution_incomplete")

    state = contract.state
    if not state.complete:
        opaque.append("declared_state_scope_unknown")
    if state.state_bound:
        if observed.state_version is None or observed.read_scope is None:
            opaque.append("state_read_not_observable")
        else:
            if observed.state_version != state.version:
                invalid.append("state_version_mismatch")
            if state.read_scope is not None and observed.read_scope != state.read_scope:
                invalid.append("read_scope_mismatch")
    else:
        if observed.state_version is not None:
            invalid.append("unexpected_state_read")
        elif observed.read_scope is None:
            opaque.append("state_read_not_observable")
        elif observed.read_scope != frozenset():
            invalid.append("unexpected_state_read")

    effect = contract.effect
    if not effect.complete:
        opaque.append("declared_effect_scope_unknown")
    if observed.effect_kind is None:
        opaque.append("effect_not_observable")
    elif observed.effect_kind is not effect.kind:
        invalid.append("effect_kind_mismatch")
    if observed.effect_scope is None:
        opaque.append("effect_scope_not_observable")
    elif effect.kind is EffectKind.NONE and observed.effect_scope != frozenset():
        invalid.append("unexpected_effect")
    elif (
        effect.kind is not EffectKind.NONE
        and effect.scope is not None
        and observed.effect_scope != effect.scope
    ):
        invalid.append("effect_scope_mismatch")
    if observed.effect_namespace is None:
        opaque.append("effect_namespace_not_observable")
    elif observed.effect_namespace != effect.namespace:
        invalid.append("effect_namespace_mismatch")

    if effect.kind is EffectKind.NONE:
        if observed.effect_kind is not None and observed.effect_kind is not EffectKind.NONE:
            invalid.append("unexpected_effect")
    elif observed.effect_kind is not None and observed.effect_kind is EffectKind.NONE:
        invalid.append("missing_effect")

    if observed.published:
        if not contract.publication_boundary or contract.visibility.value != "PUBLISHED_STATE":
            invalid.append("unexpected_publication")
        if not observed.effect_committed:
            invalid.append("publication_without_committed_effect")
    elif contract.publication_boundary and observed.effect_committed:
        opaque.append("publication_not_observed")

    if invalid:
        return ValidationResult(CertificationStatus.INVALID, tuple(dict.fromkeys(invalid)))
    if opaque:
        return ValidationResult(CertificationStatus.OPAQUE, tuple(dict.fromkeys(opaque)))
    if contract.publication_boundary and observed.published and observed.effect_committed:
        return ValidationResult(CertificationStatus.CERTIFIED_PUBLISHABLE, ())
    return ValidationResult(CertificationStatus.CERTIFIED_PRIVATE, ())


def validate_evidence(
    operator: SemanticOperator,
    evidence: ExecutionEvidence,
) -> ValidationResult:
    """Validate provenance and effect-journal evidence without inference."""

    if not isinstance(operator, SemanticOperator):
        raise _fail("semantic_operator_invalid")
    if not isinstance(evidence, ExecutionEvidence):
        raise _fail("execution_evidence_invalid")
    invalid: list[str] = []
    opaque: list[str] = []
    contract = operator.contract

    def invalid_once(code: str) -> None:
        if code not in invalid:
            invalid.append(code)

    def opaque_once(code: str) -> None:
        if code not in opaque:
            opaque.append(code)

    if evidence.instance_id != operator.instance_id:
        invalid_once("instance_id_mismatch")
    if evidence.semantic_identity is None:
        opaque_once("semantic_identity_not_observable")
    elif evidence.semantic_identity != operator.semantic_identity:
        invalid_once("semantic_identity_mismatch")
    if not evidence.terminal:
        opaque_once("execution_incomplete")
    if evidence.provenance is None:
        opaque_once("adapter_provenance_not_observable")
    else:
        if evidence.provenance.contract_id != contract.contract_id:
            invalid_once("provenance_contract_mismatch")
        if evidence.provenance.level is not CertificationLevel.VALIDATED:
            opaque_once("adapter_provenance_not_validated")
    if not evidence.child_identity_complete:
        opaque_once("child_identity_incomplete")
    if evidence.hidden_effects_possible:
        opaque_once("hidden_effects_possible")

    state = contract.state
    if not state.complete:
        opaque_once("declared_state_scope_unknown")
    if state.state_bound:
        if evidence.state_version is None or evidence.read_scope is None:
            opaque_once("state_read_not_observable")
        else:
            if evidence.state_version != state.version:
                invalid_once("state_version_mismatch")
            if state.read_scope is not None and evidence.read_scope != state.read_scope:
                invalid_once("read_scope_mismatch")
    else:
        if evidence.state_version is not None:
            invalid_once("unexpected_state_read")
        elif evidence.read_scope is None:
            opaque_once("state_read_not_observable")
        elif evidence.read_scope != frozenset():
            invalid_once("unexpected_state_read")

    effect = contract.effect
    if not effect.complete:
        opaque_once("declared_effect_scope_unknown")
    entry: object | None = None
    if not evidence.effect_journal:
        opaque_once("effect_journal_missing")
    elif len(evidence.effect_journal) != 1:
        opaque_once("effect_journal_aggregation_unknown")
    else:
        entry = evidence.effect_journal[0]
        if entry.operator_instance_id != operator.instance_id:
            invalid_once("effect_instance_mismatch")
        if entry.namespace != effect.namespace:
            invalid_once("effect_namespace_mismatch")
        if entry.kind is not effect.kind:
            invalid_once("effect_kind_mismatch")
        if entry.scope is None:
            opaque_once("effect_scope_not_observable")
        elif effect.scope is None:
            opaque_once("declared_effect_scope_unknown")
        elif entry.scope != effect.scope:
            invalid_once("effect_scope_mismatch")
        if effect.kind is EffectKind.NONE and (entry.committed or entry.durable):
            invalid_once("unexpected_committed_noop")

    publication = evidence.publication
    if publication is not None:
        if not contract.publication_boundary:
            invalid_once("unexpected_publication")
        if publication.operator_instance_id != operator.instance_id:
            invalid_once("publication_instance_mismatch")
        if state.state_bound and publication.predecessor_version != state.version:
            invalid_once("publication_predecessor_mismatch")
        if not publication.durable:
            opaque_once("publication_not_durable")
        if entry is None:
            opaque_once("publication_effect_not_validated")
        elif not entry.committed or not entry.durable:
            invalid_once("publication_without_committed_effect")
    elif contract.publication_boundary:
        opaque_once("publication_not_observable")

    if invalid:
        return ValidationResult(CertificationStatus.INVALID, tuple(invalid))
    if opaque:
        return ValidationResult(CertificationStatus.OPAQUE, tuple(opaque))
    if contract.publication_boundary and publication is not None:
        return ValidationResult(CertificationStatus.CERTIFIED_PUBLISHABLE, ())
    return ValidationResult(CertificationStatus.CERTIFIED_PRIVATE, ())


def certify_ready(
    operator: SemanticOperator,
    *,
    completed_controls: frozenset[str],
    current_version: str | None,
    evidence_ready: bool,
) -> ReadinessStatus:
    if not isinstance(operator, SemanticOperator):
        raise _fail("semantic_operator_invalid")
    if not isinstance(completed_controls, frozenset):
        raise _fail("completed_controls_invalid")
    if not isinstance(evidence_ready, bool):
        raise _fail("evidence_ready_invalid")
    if any(not isinstance(item, str) or not item for item in completed_controls):
        raise _fail("completed_control_invalid")
    if not evidence_ready:
        return ReadinessStatus.CERTIFIED_BLOCKED
    if not operator.control_predecessors.issubset(completed_controls):
        return ReadinessStatus.CERTIFIED_BLOCKED
    if not operator.contract.complete:
        return ReadinessStatus.UNRESOLVED
    state = operator.contract.state
    if state.state_bound:
        if current_version is None:
            return ReadinessStatus.UNRESOLVED
        if current_version != state.version:
            return ReadinessStatus.CERTIFIED_BLOCKED
    return ReadinessStatus.CERTIFIED_READY


def _scope_for(operator: SemanticOperator) -> MemoryScope | None:
    contract = operator.contract
    if not contract.complete:
        return None
    state = contract.state
    effect = contract.effect
    if state.namespace != effect.namespace:
        return None
    assert state.read_scope is not None
    assert effect.scope is not None
    return MemoryScope.known(
        namespace=effect.namespace,
        read_items=set(state.read_scope),
        effect_items=set(effect.scope),
    )


def certify_reorder(left: SemanticOperator, right: SemanticOperator) -> ReorderStatus:
    if not isinstance(left, SemanticOperator) or not isinstance(right, SemanticOperator):
        raise _fail("semantic_operator_invalid")
    if left.instance_id == right.instance_id:
        return ReorderStatus.CONFLICT
    if (
        {right.instance_id}.intersection(left.control_predecessors)
        or {left.instance_id}.intersection(right.control_predecessors)
    ):
        return ReorderStatus.CONFLICT
    if left.contract.publication_boundary or right.contract.publication_boundary:
        return ReorderStatus.UNKNOWN
    left_state = left.contract.state
    right_state = right.contract.state
    if left_state.state_bound != right_state.state_bound:
        return ReorderStatus.UNKNOWN
    if left_state.state_bound and left_state.version != right_state.version:
        return ReorderStatus.UNKNOWN
    left_scope = _scope_for(left)
    right_scope = _scope_for(right)
    if left_scope is None or right_scope is None:
        return ReorderStatus.UNKNOWN
    classification = classify_operator_conflict(left_scope, right_scope)
    if classification is ConflictClass.CERTIFIED_NON_CONFLICTING:
        return ReorderStatus.CERTIFIED
    if classification is ConflictClass.CONFLICTING:
        return ReorderStatus.CONFLICT
    return ReorderStatus.UNKNOWN


__all__ = [
    "CertificationStatus",
    "MSEGValidationError",
    "ReadinessStatus",
    "ReorderStatus",
    "ValidationResult",
    "certify_ready",
    "certify_reorder",
    "validate_evidence",
    "validate_execution",
]
