from __future__ import annotations

import pytest

from paper_eval.membind_v4.mseg.semantic_contract import (
    EffectContract,
    EffectKind,
    OperatorType,
    SemanticContract,
    SemanticOperator,
    StateContract,
    Visibility,
)
from paper_eval.membind_v4.mseg.semantic_evidence import (
    AdapterProvenance,
    CertificationLevel,
    EffectJournalEntry,
    ExecutionEvidence,
    MSEGEvidenceError,
    PublicationEvidence,
)
from paper_eval.membind_v4.mseg.semantic_validator import (
    CertificationStatus,
    validate_evidence,
)


def _operator(
    *,
    publication: bool = False,
    state: StateContract | None = None,
    effect: EffectContract | None = None,
) -> SemanticOperator:
    contract = SemanticContract(
        contract_id="graphiti.entity-resolution.v0",
        operator_type=OperatorType.RESOLUTION,
        state=state
        or StateContract.bound(
            namespace="history-a",
            version="m-1",
            read_scope={"entity:alice"},
        ),
        effect=effect
        or EffectContract.write(
            namespace="history-a",
            kind=EffectKind.UPDATE,
            scope={"entity:alice"},
        ),
        visibility=(
            Visibility.PUBLISHED_STATE
            if publication
            else Visibility.PRIVATE_INTERMEDIATE
        ),
        atomic=True,
        idempotent=True,
        retry_safe=True,
        publication_boundary=publication,
    )
    return SemanticOperator(
        instance_id="op-1",
        semantic_identity="semantic-1",
        evidence_ids=("evidence-1",),
        contract=contract,
        control_predecessors=frozenset(),
    )


def _provenance(
    *,
    level: CertificationLevel = CertificationLevel.VALIDATED,
    contract_id: str = "graphiti.entity-resolution.v0",
) -> AdapterProvenance:
    return AdapterProvenance(
        adapter_id="graphiti-adapter",
        backend_name="graphiti",
        backend_version="0.29.3",
        contract_id=contract_id,
        schema_fingerprint="schema-sha",
        source_fingerprint="source-sha",
        level=level,
    )


def _journal(
    *,
    instance_id: str = "op-1",
    kind: EffectKind = EffectKind.UPDATE,
    scope: set[str] | frozenset[str] | None = frozenset({"entity:alice"}),
    committed: bool = True,
    durable: bool = False,
) -> EffectJournalEntry:
    return EffectJournalEntry(
        effect_id="effect-1",
        operator_instance_id=instance_id,
        kind=kind,
        namespace="history-a",
        scope=None if scope is None else frozenset(scope),
        committed=committed,
        transaction_id="tx-1",
        timestamp_ns=20,
        durable=durable,
    )


def _evidence(
    *,
    operator: SemanticOperator | None = None,
    provenance: AdapterProvenance | None = None,
    journal: tuple[EffectJournalEntry, ...] | None = None,
    publication: PublicationEvidence | None = None,
    state_version: str | None = "m-1",
    read_scope: set[str] | frozenset[str] | None = frozenset({"entity:alice"}),
    terminal: bool = True,
    child_identity_complete: bool = True,
    hidden_effects_possible: bool = False,
) -> ExecutionEvidence:
    selected = operator or _operator(publication=publication is not None)
    return ExecutionEvidence(
        instance_id=selected.instance_id,
        semantic_identity=selected.semantic_identity,
        state_version=state_version,
        read_scope=None if read_scope is None else frozenset(read_scope),
        provenance=provenance or _provenance(),
        effect_journal=journal if journal is not None else (_journal(),),
        publication=publication,
        terminal=terminal,
        child_identity_complete=child_identity_complete,
        hidden_effects_possible=hidden_effects_possible,
    )


def _publication(*, predecessor_version: str | None = "m-1", durable: bool = True):
    return PublicationEvidence(
        publication_id="publication-1",
        operator_instance_id="op-1",
        predecessor_version=predecessor_version,
        published_version="m-2",
        durable=durable,
        timestamp_ns=30,
        frontier_position=1,
    )


def test_validated_provenance_and_effect_journal_certify_publication() -> None:
    result = validate_evidence(
        _operator(publication=True),
        _evidence(
            journal=(_journal(committed=True, durable=True),),
            publication=_publication(),
        ),
    )

    assert result.status is CertificationStatus.CERTIFIED_PUBLISHABLE
    assert result.codes == ()


def test_declared_or_observed_only_provenance_is_opaque() -> None:
    for level in (CertificationLevel.DECLARED, CertificationLevel.OBSERVED):
        result = validate_evidence(
            _operator(),
            _evidence(provenance=_provenance(level=level)),
        )

        assert result.status is CertificationStatus.OPAQUE
        assert "adapter_provenance_not_validated" in result.codes


def test_missing_journal_and_incomplete_child_identity_are_opaque() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(
            journal=(),
            child_identity_complete=False,
            hidden_effects_possible=True,
        ),
    )

    assert result.status is CertificationStatus.OPAQUE
    assert "effect_journal_missing" in result.codes
    assert "child_identity_incomplete" in result.codes
    assert "hidden_effects_possible" in result.codes


def test_provenance_contract_mismatch_is_invalid() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(
            provenance=_provenance(contract_id="wrong-contract"),
        ),
    )

    assert result.status is CertificationStatus.INVALID
    assert "provenance_contract_mismatch" in result.codes


def test_effect_journal_identity_kind_and_scope_mismatch_are_invalid() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(
            journal=(
                _journal(
                    instance_id="other-op",
                    kind=EffectKind.MERGE,
                    scope={"entity:bob"},
                ),
            ),
        ),
    )

    assert result.status is CertificationStatus.INVALID
    assert "effect_instance_mismatch" in result.codes
    assert "effect_kind_mismatch" in result.codes
    assert "effect_scope_mismatch" in result.codes


def test_unknown_effect_scope_is_opaque_even_with_valid_provenance() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(journal=(_journal(scope=None),)),
    )

    assert result.status is CertificationStatus.OPAQUE
    assert "effect_scope_not_observable" in result.codes


def test_multiple_effect_entries_are_not_aggregated_implicitly() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(
            journal=(
                _journal(),
                EffectJournalEntry(
                    effect_id="effect-2",
                    operator_instance_id="op-1",
                    kind=EffectKind.UPDATE,
                    namespace="history-a",
                    scope=frozenset({"entity:alice"}),
                    committed=True,
                    transaction_id="tx-1",
                    timestamp_ns=21,
                    durable=False,
                ),
            ),
        ),
    )

    assert result.status is CertificationStatus.OPAQUE
    assert "effect_journal_aggregation_unknown" in result.codes


def test_publication_predecessor_mismatch_is_invalid() -> None:
    result = validate_evidence(
        _operator(publication=True),
        _evidence(
            journal=(_journal(committed=True, durable=True),),
            publication=_publication(predecessor_version="m-0"),
        ),
    )

    assert result.status is CertificationStatus.INVALID
    assert "publication_predecessor_mismatch" in result.codes


def test_non_durable_publication_is_invalid() -> None:
    result = validate_evidence(
        _operator(publication=True),
        _evidence(
            journal=(_journal(committed=True, durable=False),),
            publication=_publication(durable=False),
        ),
    )

    assert result.status is CertificationStatus.INVALID
    assert "publication_without_committed_effect" in result.codes


def test_private_effect_can_be_certified_without_publication() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(journal=(_journal(committed=False, durable=False),)),
    )

    assert result.status is CertificationStatus.CERTIFIED_PRIVATE
    assert result.publishable is False


def test_unknown_state_read_and_nonterminal_execution_are_opaque() -> None:
    result = validate_evidence(
        _operator(),
        _evidence(state_version=None, read_scope=None, terminal=False),
    )

    assert result.status is CertificationStatus.OPAQUE
    assert "state_read_not_observable" in result.codes
    assert "execution_incomplete" in result.codes


def test_evidence_models_reject_durable_uncommitted_effects() -> None:
    with pytest.raises(MSEGEvidenceError, match="durable_effect_not_committed"):
        _journal(committed=False, durable=True)


def test_evidence_models_reject_publication_without_predecessor_identity() -> None:
    with pytest.raises(MSEGEvidenceError, match="publication_predecessor_missing"):
        _publication(predecessor_version=None)
