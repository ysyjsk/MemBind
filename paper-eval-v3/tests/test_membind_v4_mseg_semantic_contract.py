from __future__ import annotations

import pytest

from paper_eval.membind_v4.mseg.semantic_contract import (
    EffectContract,
    EffectKind,
    MSEGContractError,
    OperatorType,
    SemanticContract,
    SemanticOperator,
    StateContract,
    Visibility,
)
from paper_eval.membind_v4.mseg.semantic_validator import (
    CertificationStatus,
    ReadinessStatus,
    ReorderStatus,
    certify_ready,
    certify_reorder,
    validate_execution,
)


def _operator(
    *,
    instance_id: str = "op-1",
    identity: str = "semantic-1",
    state: StateContract | None = None,
    effect: EffectContract | None = None,
    visibility: Visibility = Visibility.PRIVATE_INTERMEDIATE,
    publication_boundary: bool = False,
) -> SemanticOperator:
    contract = SemanticContract(
        contract_id="graphiti.entity-resolution.v0",
        operator_type=OperatorType.RESOLUTION,
        state=state or StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:alice"},
        ),
        effect=effect or EffectContract.write(
            namespace="history-a",
            kind=EffectKind.UPDATE,
            scope={"entity:alice"},
        ),
        visibility=visibility,
        atomic=True,
        idempotent=True,
        retry_safe=True,
        publication_boundary=publication_boundary,
    )
    return SemanticOperator(
        instance_id=instance_id,
        semantic_identity=identity,
        evidence_ids=("evidence-1",),
        contract=contract,
        control_predecessors=frozenset(),
    )


def _observed(
    *,
    instance_id: str = "op-1",
    identity: str | None = "semantic-1",
    state_version: str | None = "m-1",
    read_scope: set[str] | frozenset[str] | None = frozenset({"entity:alice"}),
    effect_kind: EffectKind | None = EffectKind.UPDATE,
    effect_scope: set[str] | frozenset[str] | None = frozenset({"entity:alice"}),
    effect_namespace: str | None = "history-a",
    terminal: bool = True,
    effect_committed: bool = True,
    published: bool = False,
):
    from paper_eval.membind_v4.mseg.semantic_contract import ObservedExecution

    return ObservedExecution(
        instance_id=instance_id,
        semantic_identity=identity,
        state_version=state_version,
        read_scope=None if read_scope is None else frozenset(read_scope),
        effect_kind=effect_kind,
        effect_scope=None if effect_scope is None else frozenset(effect_scope),
        effect_namespace=effect_namespace,
        terminal=terminal,
        effect_committed=effect_committed,
        published=published,
    )


def test_state_contract_distinguishes_unbound_from_unknown() -> None:
    unbound = StateContract.unbound(namespace="evidence-only")
    unknown = StateContract.bound(
        namespace="history-a",
        version="m-1",
        read_scope=None,
    )

    assert unbound.state_bound is False
    assert unbound.complete is True
    assert unknown.state_bound is True
    assert unknown.complete is False


def test_publication_contract_requires_atomic_published_visibility() -> None:
    with pytest.raises(MSEGContractError, match="publication_visibility_required"):
        SemanticContract(
            contract_id="bad",
            operator_type=OperatorType.MUTATION,
            state=StateContract.bound(
                namespace="history-a",
                version="m-1",
                read_scope={"entity:a"},
            ),
            effect=EffectContract.write(
                namespace="history-a",
                kind=EffectKind.UPDATE,
                scope={"entity:a"},
            ),
            visibility=Visibility.PRIVATE_INTERMEDIATE,
            atomic=True,
            idempotent=True,
            retry_safe=True,
            publication_boundary=True,
        )


def test_exact_observation_is_certified_publishable() -> None:
    operator = _operator(
        visibility=Visibility.PUBLISHED_STATE,
        publication_boundary=True,
    )
    result = validate_execution(
        operator,
        _observed(published=True),
    )

    assert result.status is CertificationStatus.CERTIFIED_PUBLISHABLE
    assert result.codes == ()


def test_state_version_mismatch_is_invalid_not_unknown() -> None:
    result = validate_execution(
        _operator(),
        _observed(state_version="m-0"),
    )

    assert result.status is CertificationStatus.INVALID
    assert "state_version_mismatch" in result.codes


def test_missing_state_or_effect_evidence_is_opaque_and_not_ready() -> None:
    operator = _operator()
    result = validate_execution(
        operator,
        _observed(state_version=None, read_scope=None, effect_scope=None),
    )

    assert result.status is CertificationStatus.OPAQUE
    assert "state_read_not_observable" in result.codes
    assert "effect_scope_not_observable" in result.codes
    assert result.publishable is False


def test_declared_unknown_scope_stays_opaque_even_when_observation_is_present() -> None:
    operator = _operator(
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope=None,
        ),
    )
    result = validate_execution(operator, _observed())

    assert result.status is CertificationStatus.OPAQUE
    assert "declared_state_scope_unknown" in result.codes


def test_private_result_is_certified_but_never_publishable() -> None:
    operator = _operator(
        visibility=Visibility.PUBLISHED_STATE,
        publication_boundary=True,
    )
    result = validate_execution(
        operator,
        _observed(published=False, effect_committed=False),
    )

    assert result.status is CertificationStatus.CERTIFIED_PRIVATE
    assert result.publishable is False


def test_publication_without_committed_effect_is_invalid() -> None:
    operator = _operator(
        visibility=Visibility.PUBLISHED_STATE,
        publication_boundary=True,
    )
    result = validate_execution(
        operator,
        _observed(published=True, effect_committed=False),
    )

    assert result.status is CertificationStatus.INVALID
    assert "publication_without_committed_effect" in result.codes


def test_nonterminal_execution_is_opaque() -> None:
    result = validate_execution(_operator(), _observed(terminal=False))

    assert result.status is CertificationStatus.OPAQUE
    assert "execution_incomplete" in result.codes


def test_unbound_contract_rejects_an_unexpected_state_read() -> None:
    operator = _operator(
        state=StateContract.unbound(namespace="evidence-only"),
        effect=EffectContract.none(namespace="evidence-only"),
    )
    result = validate_execution(
        operator,
        _observed(
            state_version="m-1",
            read_scope={"entity:alice"},
            effect_kind=EffectKind.UPDATE,
            effect_scope={"entity:alice"},
        ),
    )

    assert result.status is CertificationStatus.INVALID
    assert "unexpected_state_read" in result.codes
    assert "unexpected_effect" in result.codes


def test_ready_requires_controls_versions_and_complete_contract() -> None:
    operator = _operator()
    assert (
        certify_ready(
            operator,
            completed_controls=frozenset(),
            current_version="m-1",
            evidence_ready=True,
        )
        is ReadinessStatus.CERTIFIED_READY
    )

    dependent = SemanticOperator(
        instance_id="op-2",
        semantic_identity="semantic-2",
        evidence_ids=("evidence-2",),
        contract=operator.contract,
        control_predecessors=frozenset({"op-1"}),
    )
    assert (
        certify_ready(
            dependent,
            completed_controls=frozenset(),
            current_version="m-1",
            evidence_ready=True,
        )
        is ReadinessStatus.CERTIFIED_BLOCKED
    )

    unknown_state = _operator(
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope=None,
        ),
    )
    assert (
        certify_ready(
            unknown_state,
            completed_controls=frozenset(),
            current_version="m-1",
            evidence_ready=True,
        )
        is ReadinessStatus.UNRESOLVED
    )
    assert (
        certify_ready(
            operator,
            completed_controls=frozenset(),
            current_version="m-0",
            evidence_ready=True,
        )
        is ReadinessStatus.CERTIFIED_BLOCKED
    )


def test_reorder_is_certified_only_for_known_disjoint_contracts() -> None:
    left = _operator(
        instance_id="left",
        identity="left",
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:left"},
        ),
        effect=EffectContract.write(
            namespace="history-a",
            kind=EffectKind.UPDATE,
            scope={"entity:left"},
        ),
    )
    right = _operator(
        instance_id="right",
        identity="right",
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:right"},
        ),
        effect=EffectContract.write(
            namespace="history-a",
            kind=EffectKind.UPDATE,
            scope={"entity:right"},
        ),
    )

    assert certify_reorder(left, right) is ReorderStatus.CERTIFIED


def test_reorder_rejects_overlap_and_unknown_scope() -> None:
    left = _operator(instance_id="left", identity="left")
    overlap = _operator(instance_id="overlap", identity="overlap")
    unknown = _operator(
        instance_id="unknown",
        identity="unknown",
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope=None,
        ),
    )

    assert certify_reorder(left, overlap) is ReorderStatus.CONFLICT
    assert certify_reorder(left, unknown) is ReorderStatus.UNKNOWN


def test_cross_namespace_read_write_contract_is_conservative() -> None:
    operator = _operator(
        instance_id="cross-namespace",
        identity="cross-namespace",
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:alice"},
        ),
        effect=EffectContract.write(
            namespace="history-b",
            kind=EffectKind.UPDATE,
            scope={"entity:alice"},
        ),
    )
    peer = _operator(instance_id="peer", identity="peer")

    assert certify_reorder(operator, peer) is ReorderStatus.UNKNOWN


def test_namespace_isolation_is_explicitly_certifiable() -> None:
    left = _operator(
        instance_id="left",
        identity="left",
        state=StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:alice"},
        ),
        effect=EffectContract.write(
            namespace="history-a",
            kind=EffectKind.UPDATE,
            scope={"entity:alice"},
        ),
    )
    right = _operator(
        instance_id="right",
        identity="right",
        state=StateContract.bound(
            namespace="history-b",
            version="m-1",
            read_scope={"entity:alice"},
        ),
        effect=EffectContract.write(
            namespace="history-b",
            kind=EffectKind.UPDATE,
            scope={"entity:alice"},
        ),
    )

    assert certify_reorder(left, right) is ReorderStatus.CERTIFIED


def test_malformed_operator_identity_is_rejected() -> None:
    with pytest.raises(MSEGContractError, match="instance_id_invalid"):
        SemanticOperator(
            instance_id="",
            semantic_identity="semantic-1",
            evidence_ids=("evidence-1",),
            contract=_operator().contract,
            control_predecessors=frozenset(),
        )


def test_state_only_operator_may_have_no_immutable_evidence() -> None:
    operator = SemanticOperator(
        instance_id="publication-1",
        semantic_identity="publication-1",
        evidence_ids=(),
        contract=_operator().contract,
        control_predecessors=frozenset({"op-1"}),
    )

    assert operator.evidence_ids == ()
