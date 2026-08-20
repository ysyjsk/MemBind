from __future__ import annotations

from paper_eval.membind_v4.mseg.offline_qualification import (
    GateDecision,
    QualificationCase,
    SyntheticDecision,
    gate_real_trace,
    qualify_synthetic,
)
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
)


def _operator(
    *,
    instance_id: str,
    entity: str,
    state_version: str = "m-1",
    state_scope: set[str] | None = None,
    effect_scope: set[str] | None = None,
) -> SemanticOperator:
    scope = state_scope or {f"entity:{entity}"}
    write_scope = effect_scope or {f"entity:{entity}"}
    contract = SemanticContract(
        contract_id="synthetic.memory.v0",
        operator_type=OperatorType.RESOLUTION,
        state=StateContract.bound(
            namespace="synthetic-history",
            version=state_version,
            read_scope=scope,
        ),
        effect=EffectContract.write(
            namespace="synthetic-history",
            kind=EffectKind.UPDATE,
            scope=write_scope,
        ),
        visibility=Visibility.PRIVATE_INTERMEDIATE,
        atomic=True,
        idempotent=True,
        retry_safe=True,
        publication_boundary=False,
    )
    return SemanticOperator(
        instance_id=instance_id,
        semantic_identity=f"identity-{instance_id}",
        evidence_ids=(f"evidence-{instance_id}",),
        contract=contract,
        control_predecessors=frozenset(),
    )


def _evidence(operator: SemanticOperator, *, opaque: bool = False) -> ExecutionEvidence:
    return ExecutionEvidence(
        instance_id=operator.instance_id,
        semantic_identity=operator.semantic_identity,
        state_version="m-1",
        read_scope=None if opaque else frozenset(operator.contract.state.read_scope or ()),
        provenance=AdapterProvenance(
            adapter_id="synthetic-adapter",
            backend_name="synthetic",
            backend_version="0",
            contract_id=operator.contract.contract_id,
            schema_fingerprint="schema",
            source_fingerprint="source",
            level=(
                CertificationLevel.OBSERVED
                if opaque
                else CertificationLevel.VALIDATED
            ),
        ),
        effect_journal=(
            EffectJournalEntry(
                effect_id=f"effect-{operator.instance_id}",
                operator_instance_id=operator.instance_id,
                kind=operator.contract.effect.kind,
                namespace="synthetic-history",
                scope=None
                if opaque
                else frozenset(operator.contract.effect.scope or ()),
                committed=False,
                transaction_id=None,
                timestamp_ns=1,
                durable=False,
            ),
        ),
        publication=None,
        terminal=True,
        child_identity_complete=True,
        hidden_effects_possible=False,
    )


def _case(operator: SemanticOperator, *, opaque: bool = False) -> QualificationCase:
    return QualificationCase(
        label=operator.instance_id,
        operator=operator,
        evidence=_evidence(operator, opaque=opaque),
    )


def test_all_certified_disjoint_cases_pass_synthetic_gate() -> None:
    left = _case(_operator(instance_id="left", entity="left"))
    right = _case(_operator(instance_id="right", entity="right"))

    result = qualify_synthetic(
        (left, right),
        reorder_pairs=((left.label, right.label),),
    )

    assert result.decision is SyntheticDecision.GO_OFFLINE_CERTIFIED
    assert result.status_counts["CERTIFIED_PRIVATE"] == 2
    assert result.reorder_counts["CERTIFIED"] == 1
    assert result.reasons == ()


def test_unknown_case_blocks_synthetic_gate() -> None:
    known = _case(_operator(instance_id="known", entity="known"))
    unknown = _case(_operator(instance_id="unknown", entity="unknown"), opaque=True)

    result = qualify_synthetic(
        (known, unknown),
        reorder_pairs=((known.label, unknown.label),),
    )

    assert result.decision is SyntheticDecision.STOP_INCOMPLETE_EVIDENCE
    assert result.status_counts["OPAQUE"] == 1
    assert "opaque_evidence_present" in result.reasons


def test_no_certified_reorder_blocks_even_when_cases_are_valid() -> None:
    left = _case(_operator(instance_id="left", entity="same"))
    right = _case(_operator(instance_id="right", entity="same"))

    result = qualify_synthetic(
        (left, right),
        reorder_pairs=((left.label, right.label),),
    )

    assert result.decision is SyntheticDecision.STOP_NO_REORDER_OPPORTUNITY
    assert result.reorder_counts["CONFLICT"] == 1


def test_invalid_evidence_has_priority_over_unknown_evidence() -> None:
    operator = _operator(instance_id="bad", entity="bad")
    evidence = _evidence(operator)
    invalid = ExecutionEvidence(
        instance_id=operator.instance_id,
        semantic_identity=operator.semantic_identity,
        state_version="wrong-version",
        read_scope=evidence.read_scope,
        provenance=evidence.provenance,
        effect_journal=evidence.effect_journal,
        publication=None,
        terminal=True,
        child_identity_complete=True,
        hidden_effects_possible=False,
    )
    case = QualificationCase(label="bad", operator=operator, evidence=invalid)

    result = qualify_synthetic((case,), reorder_pairs=())

    assert result.decision is SyntheticDecision.STOP_INVALID_EVIDENCE
    assert result.status_counts["INVALID"] == 1


def test_real_trace_gate_stops_when_mseg_is_not_recovered() -> None:
    left = _case(_operator(instance_id="left", entity="left"))
    right = _case(_operator(instance_id="right", entity="right"))
    synthetic = qualify_synthetic(
        (left, right),
        reorder_pairs=((left.label, right.label),),
    )

    result = gate_real_trace(
        synthetic,
        {
            "mseg_recovered": False,
            "blocking_reasons": [
                "memory_version_evidence_missing",
                "dependency_and_effect_scope_missing",
            ],
        },
    )

    assert result.decision is GateDecision.STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY
    assert "memory_version_evidence_missing" in result.reasons
    assert result.live_authorized is False

