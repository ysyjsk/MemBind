"""Provider-free TDD for MEG validated semantic execution opportunity.

The fixtures in this file are pure records.  Importing this module must not
import Graphiti, a database driver, an LLM client, tmux, or a network stack.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from paper_eval.membind_v4.mseg.continuation import (
    ContinuationValidationStatus,
    SemanticContinuation,
    SemanticContinuationError,
    SemanticRequestIdentity,
    validate_semantic_continuation,
)
from paper_eval.membind_v4.mseg.mutation_epoch import StateMutationEpoch
from paper_eval.membind_v4.mseg.operator_readiness import (
    DependencyEvidenceSource,
    DirectReadinessDependency,
    OperatorReadinessInput,
    OperatorReadinessStatus,
    ReadinessDependencyKind,
    audit_operator_readiness,
    compute_operator_readiness,
)
from paper_eval.membind_v4.mseg.passive_equivalence import (
    InstrumentationExecutionSnapshot,
    compare_instrumentation_execution,
)
from paper_eval.membind_v4.mseg.read_view import (
    CandidateSemanticRecord,
    ReadKind,
    ReadMaterialization,
    ReadViewStatus,
    capture_semantic_read_view,
)
from paper_eval.membind_v4.mseg.semantic_adapter import (
    ChildKey,
    LineageBuilder,
    OperatorLineage,
)
from paper_eval.membind_v4.mseg.validated_execution_oracle import (
    OracleThresholds,
    ValidatedExecutionRow,
    reduce_validated_execution_opportunity,
)
from paper_eval.membind_v4.mseg.version_token import (
    MemoryVersionToken,
    VersionTokenFactory,
)


def _hash(character: str) -> str:
    return character * 64


def _versions() -> tuple[MemoryVersionToken, MemoryVersionToken]:
    factory = VersionTokenFactory(backend_id="neo4j-fixture", epoch="db-start-1")
    stale = factory.commit(
        namespace="graph-a",
        transaction_id="published-tx-1",
        evidence_hash=_hash("a"),
    )
    exact = factory.commit(
        namespace="graph-a",
        transaction_id="published-tx-2",
        evidence_hash=_hash("b"),
        predecessor=stale,
    )
    return stale, exact


def _candidate(
    candidate_id: str,
    *,
    name: str,
    summary: str = "",
    score: float | None = None,
) -> CandidateSemanticRecord:
    return CandidateSemanticRecord.create(
        candidate_id=candidate_id,
        semantic_fields={"name": name, "summary": summary},
        order_evidence=None if score is None else {"rrf_score": score},
    )


def _materialization(
    candidates: tuple[CandidateSemanticRecord, ...] | None = None,
    *,
    context_hash: str | None = None,
    unknown_state_fields: tuple[str, ...] = (),
    irrelevant_metadata: dict[str, object] | None = None,
) -> ReadMaterialization:
    return ReadMaterialization.create(
        query_identity="entity-search:alice",
        search_configuration_hash=_hash("c"),
        candidates=(
            (
                _candidate("node-a", name="Alice", score=0.91),
                _candidate("node-b", name="Alice A.", score=0.82),
            )
            if candidates is None
            else candidates
        ),
        mutable_context_fragment_hash=context_hash or _hash("d"),
        provenance_hash=_hash("e"),
        unknown_state_fields=unknown_state_fields,
        irrelevant_metadata=irrelevant_metadata or {},
        excluded_metadata_reasons=(
            {}
            if not irrelevant_metadata
            else {key: "not consumed by request or decision" for key in irrelevant_metadata}
        ),
    )


def _view(
    *,
    materialization: ReadMaterialization | None = None,
    version: MemoryVersionToken | None = None,
    tracker: StateMutationEpoch | None = None,
):
    stale, _ = _versions()
    epoch = tracker or StateMutationEpoch(
        namespace="graph-a", backend_id="neo4j-fixture", epoch="db-start-1"
    )
    return capture_semantic_read_view(
        graph_id="graph-a",
        stream_id="07741c45",
        source_sequence=3,
        operator_instance_id="meg-op-node-batch-3",
        memory_version_token=version or stale,
        mutation_epoch=epoch,
        read_kind=ReadKind.NODE_RESOLUTION,
        materialize=lambda: materialization or _materialization(),
    )


def _request_identity() -> SemanticRequestIdentity:
    return SemanticRequestIdentity.create(
        rendered_request_hash=_hash("1"),
        model_id="qwen3-30b-a3b",
        schema_hash=_hash("2"),
        prompt_name="dedupe_nodes.nodes",
        prompt_hash=_hash("3"),
    )


def _continuation(view=None, *, descendants=("op-attr", "op-effect")):
    return SemanticContinuation.create(
        operator_instance_id="meg-op-node-batch-3",
        read_view=view or _view(),
        request_identity=_request_identity(),
        llm_output_hash=_hash("4"),
        deterministic_post_processing_identity="graphiti-0.29.3:node-resolution",
        descendant_operator_ids=descendants,
    )


def test_stable_epoch_before_after_produces_stable_readview() -> None:
    view = _view()
    assert view.status is ReadViewStatus.STABLE_READVIEW
    assert view.mutation_epoch_before == view.mutation_epoch_after
    assert view.read_view_digest is not None


def test_commit_during_capture_is_unstable_and_discarded() -> None:
    tracker = StateMutationEpoch(
        namespace="graph-a", backend_id="neo4j-fixture", epoch="db-start-1"
    )

    def materialize() -> ReadMaterialization:
        tracker.record_commit(transaction_id="persistent-tx-during-read")
        return _materialization()

    stale, _ = _versions()
    view = capture_semantic_read_view(
        graph_id="graph-a",
        stream_id="07741c45",
        source_sequence=3,
        operator_instance_id="meg-op-node-batch-3",
        memory_version_token=stale,
        mutation_epoch=tracker,
        read_kind=ReadKind.NODE_RESOLUTION,
        materialize=materialize,
    )
    assert view.status is ReadViewStatus.INVALID_UNSTABLE_READ
    assert view.read_view_digest is None


def test_same_candidate_ids_in_different_prompt_order_is_miss() -> None:
    stale = _view()
    reversed_view = _view(
        materialization=_materialization(tuple(reversed(stale.candidates)))
    )
    result = validate_semantic_continuation(_continuation(stale), reversed_view)
    assert result.status is ContinuationValidationStatus.VALIDATION_MISS


def test_candidate_semantic_attribute_change_is_miss() -> None:
    stale = _view()
    changed = _view(
        materialization=_materialization(
            (
                _candidate("node-a", name="Alice", summary="changed", score=0.91),
                stale.candidates[1],
            )
        )
    )
    assert (
        validate_semantic_continuation(_continuation(stale), changed).status
        is ContinuationValidationStatus.VALIDATION_MISS
    )


def test_irrelevant_metadata_change_with_explicit_reason_is_hit() -> None:
    stale = _view(
        materialization=_materialization(irrelevant_metadata={"db_debug_etag": "left"})
    )
    exact = _view(
        materialization=_materialization(irrelevant_metadata={"db_debug_etag": "right"})
    )
    assert stale.read_view_digest == exact.read_view_digest
    assert (
        validate_semantic_continuation(_continuation(stale), exact).status
        is ContinuationValidationStatus.VALIDATION_HIT
    )


def _edge_parent() -> OperatorLineage:
    return OperatorLineage.create(
        graph_id="graph-a",
        stream_id="07741c45",
        source_sequence=3,
        semantic_role="graphiti.resolve_extracted_edges",
        adapter_revision="graphiti-0.29.3-meg-v1",
        created_ns=10,
        ready_ns=20,
    )


def _edge_key(edge_uuid: str, ordinal: int) -> ChildKey:
    return ChildKey.from_mapping(
        {
            "deterministic_ordinal": ordinal,
            "extracted_edge_uuid": edge_uuid,
            "fact_hash": _hash("f" if ordinal == 0 else "9"),
            "source_node_uuid": "node-a",
            "target_node_uuid": "node-b",
        }
    )


def test_per_edge_identity_is_materialized_without_completion_order() -> None:
    child = OperatorLineage.child(
        _edge_parent(),
        semantic_role="graphiti.resolve_extracted_edge",
        child_key=_edge_key("edge-a", 0),
    )
    assert child.instance_id.startswith("meg-op-")
    assert "completion" not in child.child_key.canonical_json()


def test_concurrent_completion_reversal_preserves_child_identity() -> None:
    keys = (_edge_key("edge-a", 0), _edge_key("edge-b", 1))
    first = LineageBuilder(_edge_parent())
    launched = tuple(
        first.add_child(
            semantic_role="graphiti.resolve_extracted_edge", child_key=key
        )
        for key in keys
    )
    second = LineageBuilder(_edge_parent())
    relaunched = tuple(
        second.add_child(
            semantic_role="graphiti.resolve_extracted_edge", child_key=key
        )
        for key in reversed(keys)
    )
    assert {child.instance_id for child in launched} == {
        child.instance_id for child in relaunched
    }


def test_exact_rematerialization_equality_is_hit_across_published_versions() -> None:
    stale_token, exact_token = _versions()
    stale = _view(version=stale_token)
    exact = _view(version=exact_token)
    result = validate_semantic_continuation(_continuation(stale), exact)
    assert stale.memory_version_token != exact.memory_version_token
    assert stale.provenance_hash != exact.provenance_hash
    assert result.status is ContinuationValidationStatus.VALIDATION_HIT


@pytest.mark.parametrize(
    "candidates",
    [
        (
            _candidate("node-a", name="Alice", score=0.91),
            _candidate("node-b", name="Alice A.", score=0.82),
            _candidate("node-c", name="Alice Corp.", score=0.70),
        ),
        (_candidate("node-a", name="Alice", score=0.91),),
    ],
    ids=("candidate_addition", "candidate_deletion"),
)
def test_candidate_set_phantom_or_deletion_is_miss(candidates) -> None:
    stale = _view()
    exact = _view(materialization=_materialization(candidates))
    assert (
        validate_semantic_continuation(_continuation(stale), exact).status
        is ContinuationValidationStatus.VALIDATION_MISS
    )


def test_disjoint_effect_scopes_cannot_override_changed_readview() -> None:
    stale = _view()
    exact = _view(
        materialization=_materialization(
            (_candidate("other-id", name="Different", score=0.99),)
        )
    )
    result = validate_semantic_continuation(
        _continuation(stale), exact, effect_scopes_disjoint=True
    )
    assert result.status is ContinuationValidationStatus.VALIDATION_MISS
    assert result.independence_certified is False


def test_unknown_state_derived_field_is_opaque() -> None:
    opaque = _view(
        materialization=_materialization(
            unknown_state_fields=("candidate.attributes.department",)
        )
    )
    assert opaque.status is ReadViewStatus.OPAQUE
    assert opaque.read_view_digest is None
    assert (
        validate_semantic_continuation(_continuation(opaque), opaque).status
        is ContinuationValidationStatus.OPAQUE
    )


def test_semantic_continuation_rejects_write_or_publication_intent() -> None:
    for write, publication in ((True, False), (False, True)):
        with pytest.raises(SemanticContinuationError, match="private_continuation_only"):
            SemanticContinuation.create(
                operator_instance_id="meg-op-node-batch-3",
                read_view=_view(),
                request_identity=_request_identity(),
                llm_output_hash=_hash("4"),
                deterministic_post_processing_identity="graphiti-0.29.3",
                descendant_operator_ids=("op-effect",),
                persistent_write_intent=write,
                publication_intent=publication,
            )


def test_miss_invalidates_only_operator_and_actual_descendants() -> None:
    stale = _view()
    changed = _view(
        materialization=_materialization(
            (_candidate("node-z", name="Zed", score=0.8),)
        )
    )
    result = validate_semantic_continuation(
        _continuation(stale, descendants=("op-attr", "op-effect")), changed
    )
    assert result.invalidated_operator_ids == (
        "meg-op-node-batch-3",
        "op-attr",
        "op-effect",
    )
    assert "unrelated-episode-op" not in result.invalidated_operator_ids


def test_mutation_epoch_and_publication_version_are_strictly_distinct() -> None:
    published, _ = _versions()
    tracker = StateMutationEpoch(
        namespace="graph-a", backend_id="neo4j-fixture", epoch="db-start-1"
    )
    before = tracker.snapshot()
    after = tracker.record_commit(transaction_id="non-publication-tx")
    assert after.counter == before.counter + 1
    assert published.counter == 1
    assert published.transaction_id == "published-tx-1"
    assert after.transaction_id == "non-publication-tx"
    assert after.canonical != published.canonical


def test_multi_input_node_batch_readiness_waits_only_for_direct_inputs() -> None:
    item = OperatorReadinessInput(
        operator_instance_id="node-resolution-batch-3",
        operator_kind="node_resolution",
        operator_available_ns=90,
        prepared_artifact_ready_ns=300,
        exact_predecessor_publication_ns=250,
        direct_dependencies=(
            DirectReadinessDependency(
                dependency_id="candidate-read-node-a",
                kind=ReadinessDependencyKind.EVIDENCE,
                satisfied_ns=120,
                evidence_source=DependencyEvidenceSource.SOURCE_CODE,
            ),
            DirectReadinessDependency(
                dependency_id="candidate-read-node-b",
                kind=ReadinessDependencyKind.EVIDENCE,
                satisfied_ns=140,
                evidence_source=DependencyEvidenceSource.RUNTIME_LINEAGE,
            ),
            DirectReadinessDependency(
                dependency_id="old-committed-state",
                kind=ReadinessDependencyKind.STATE,
                satisfied_ns=110,
                evidence_source=DependencyEvidenceSource.ADAPTER_CONTRACT,
            ),
        ),
    )
    result = compute_operator_readiness(item)
    assert result.status is OperatorReadinessStatus.LOCALLY_READY
    assert result.local_operator_ready_time_ns == 140
    assert result.readiness_advance_ns == 160
    assert result.local_ready_before_exact_predecessor_publication is True


def test_unknown_direct_dependency_fails_readiness_closed() -> None:
    item = OperatorReadinessInput(
        operator_instance_id="edge-resolution-3",
        operator_kind="edge_resolution",
        operator_available_ns=90,
        prepared_artifact_ready_ns=300,
        exact_predecessor_publication_ns=250,
        direct_dependencies=(
            DirectReadinessDependency(
                dependency_id="edge-candidate-order",
                kind=ReadinessDependencyKind.EVIDENCE,
                satisfied_ns=None,
                evidence_source=DependencyEvidenceSource.UNKNOWN,
            ),
        ),
    )
    assert compute_operator_readiness(item).status is OperatorReadinessStatus.UNKNOWN


def test_readiness_audit_reports_whole_barrier_comparison() -> None:
    rows = tuple(
        compute_operator_readiness(
            OperatorReadinessInput(
                operator_instance_id=f"op-{index}",
                operator_kind="edge_resolution",
                operator_available_ns=10,
                prepared_artifact_ready_ns=100 + index,
                exact_predecessor_publication_ns=90 + index,
                direct_dependencies=(
                    DirectReadinessDependency(
                        dependency_id=f"relation-{index}",
                        kind=ReadinessDependencyKind.EVIDENCE,
                        satisfied_ns=50 + index,
                        evidence_source=DependencyEvidenceSource.SOURCE_CODE,
                    ),
                ),
            )
        )
        for index in range(3)
    )
    audit = audit_operator_readiness(rows)
    assert audit.total_semantic_operators == 3
    assert audit.locally_ready_operators == 3
    assert audit.local_ready_before_whole_prepared_artifact == 3
    assert audit.readiness_advance_p50_ns == 50


def _instrumentation_snapshot(**overrides) -> InstrumentationExecutionSnapshot:
    values = {
        "request_count": 2,
        "prompt_hashes": (_hash("1"), _hash("2")),
        "model_schema_hashes": (_hash("3"), _hash("4")),
        "db_query_semantics_hashes": (_hash("5"), _hash("6")),
        "persistent_mutation_hashes": (_hash("7"),),
        "source_sequences": (0, 1),
        "publication_order": (0, 1),
        "llm_call_count": 2,
        "shadow_llm_call_count": 0,
        "shadow_persistent_write_count": 0,
        "publication_modification_count": 0,
    }
    values.update(overrides)
    return InstrumentationExecutionSnapshot(**values)


def test_instrumentation_passive_equivalence_does_not_require_response_bytes() -> None:
    certificate = compare_instrumentation_execution(
        _instrumentation_snapshot(), _instrumentation_snapshot()
    )
    assert certificate.passed is True
    assert "response_hashes" not in certificate.compared_fields


@pytest.mark.parametrize(
    ("override", "violation"),
    [
        ({"request_count": 3}, "request_count_changed"),
        ({"prompt_hashes": (_hash("8"), _hash("2"))}, "prompt_hash_changed"),
        ({"model_schema_hashes": (_hash("9"), _hash("4"))}, "model_schema_changed"),
        ({"db_query_semantics_hashes": (_hash("a"), _hash("6"))}, "db_query_semantics_changed"),
        ({"persistent_mutation_hashes": (_hash("b"),)}, "persistent_mutation_changed"),
        ({"source_sequences": (1, 0)}, "source_order_changed"),
        ({"publication_order": (1, 0)}, "publication_order_changed"),
        ({"shadow_llm_call_count": 1}, "shadow_llm_call_detected"),
        ({"shadow_persistent_write_count": 1}, "shadow_write_detected"),
    ],
)
def test_instrumentation_passive_equivalence_fails_closed(override, violation) -> None:
    certificate = compare_instrumentation_execution(
        _instrumentation_snapshot(), _instrumentation_snapshot(**override)
    )
    assert certificate.passed is False
    assert violation in certificate.violations


def test_validated_oracle_reports_upper_bound_value_and_go_only_on_all_gates() -> None:
    readiness = compute_operator_readiness(
        OperatorReadinessInput(
            operator_instance_id="meg-op-node-batch-3",
            operator_kind="node_resolution",
            operator_available_ns=10,
            prepared_artifact_ready_ns=100,
            exact_predecessor_publication_ns=90,
            direct_dependencies=(
                DirectReadinessDependency(
                    dependency_id="entity-extraction",
                    kind=ReadinessDependencyKind.EVIDENCE,
                    satisfied_ns=20,
                    evidence_source=DependencyEvidenceSource.SOURCE_CODE,
                ),
            ),
        )
    )
    stale_token, exact_token = _versions()
    stale = _view(version=stale_token)
    exact = _view(version=exact_token)
    row = ValidatedExecutionRow(
        operator_instance_id="meg-op-node-batch-3",
        operator_kind="node_resolution",
        readiness=readiness,
        shadow_read_view=stale,
        exact_read_view=exact,
        exact_llm_service_ns=1_000,
        readview_materialization_ns=50,
        exact_revalidation_ns=30,
        shadow_llm_calls=0,
        shadow_persistent_writes=0,
        publication_modifications=0,
    )
    report = reduce_validated_execution_opportunity(
        (row,),
        offline_gates_passed=True,
        capture_complete=True,
        thresholds=OracleThresholds(
            minimum_early_fraction=0.5,
            minimum_stable_fraction=0.5,
            minimum_hit_fraction=0.5,
            minimum_net_value_ns=1,
        ),
    )
    assert report.status == "GO_VALIDATED_SEMANTIC_CONTINUATION"
    assert report.validation_hit == 1
    assert report.potentially_hideable_llm_service_ns == 1_000
    assert report.potential_net_value_ns == 920
    assert report.value_label == "OFFLINE/SHADOW UPPER-BOUND DIAGNOSTIC"


def test_oracle_discards_unstable_readview_from_hit_miss_denominator() -> None:
    stable = _view()
    unstable = replace(
        stable,
        mutation_epoch_after=StateMutationEpoch(
            namespace="graph-a", backend_id="neo4j-fixture", epoch="db-start-1"
        ).record_commit(transaction_id="other-tx"),
        status=ReadViewStatus.INVALID_UNSTABLE_READ,
        read_view_digest=None,
    )
    readiness = compute_operator_readiness(
        OperatorReadinessInput(
            operator_instance_id="meg-op-node-batch-3",
            operator_kind="node_resolution",
            operator_available_ns=10,
            prepared_artifact_ready_ns=100,
            exact_predecessor_publication_ns=90,
            direct_dependencies=(),
        )
    )
    row = ValidatedExecutionRow(
        operator_instance_id="meg-op-node-batch-3",
        operator_kind="node_resolution",
        readiness=readiness,
        shadow_read_view=unstable,
        exact_read_view=stable,
        exact_llm_service_ns=1_000,
        readview_materialization_ns=50,
        exact_revalidation_ns=30,
    )
    report = reduce_validated_execution_opportunity(
        (row,), offline_gates_passed=True, capture_complete=True
    )
    assert report.unstable_discarded_readviews == 1
    assert report.validation_hit == 0
    assert report.validation_miss == 0
    assert report.hit_rate is None

