"""TDD contracts for Versioned Dependency Certificates and the 12-source gate."""

from __future__ import annotations

from dataclasses import replace

from paper_eval.membind_v4.semantic_call import SemanticCall
from paper_eval.membind_v4.vdc.certificate import (
    DependencyClass,
    FrontierDependencyCertificate,
    VersionedReadCertificate,
    classify_early_execution,
)
from paper_eval.membind_v4.vdc.oracle import VDCOracleRow, reduce_vdc_oracle


def _call(
    *,
    state_version: int,
    previous: tuple[str, ...],
    candidate_uuid: str = "candidate-bob",
) -> SemanticCall:
    return SemanticCall.create(
        source_sequence=2,
        state_version=state_version,
        operator_identity={"graphiti_version": "0.29.3", "operator": "NodeResolve"},
        model_identity={"model": "qwen3-32b-fp8"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=("a" if state_version == 1 else "b") * 64,
        token_sequence_sha256=("c" if state_version == 1 else "d") * 64,
        prompt_tokens=128,
        extracted_nodes=({"uuid": "raw-bob", "name": "Bob", "group_id": "g"},),
        candidate_order=(0,),
        candidate_bindings=(
            {
                "candidate_id": 0,
                "extracted_node_indices": [0],
                "projection": {"name": "Bob", "uuid": candidate_uuid},
                "uuid": candidate_uuid,
            },
        ),
        previous_episodes=tuple({"uuid": uuid} for uuid in previous),
        episode_context={"group_id": "g", "uuid": "episode-2"},
        entity_types=("Entity",),
        execution_mode="LLM",
        operator_revision="graphiti-0.29.3-node-resolve-v4-1",
    )


def _read(*, state_version: int = 1, previous=("episode-0",)) -> VersionedReadCertificate:
    return VersionedReadCertificate.create(
        source_sequence=2,
        state_version=state_version,
        group_id="g",
        semantic_call=_call(state_version=state_version, previous=tuple(previous)),
        candidate_ids=("candidate-bob",),
        semantic_keys=("bob|entity",),
        candidate_scope_complete=True,
        previous_episode_scope_complete=True,
    )


def test_same_group_previous_episode_publication_is_a_real_conflict() -> None:
    frontier = FrontierDependencyCertificate.create(
        source_sequence=1,
        group_id="g",
        semantic_keys=("alice|entity",),
        candidate_ids=("candidate-alice",),
        node_write_ids=("resolved-alice",),
        publishes_episode=True,
        published_episode_uuid="episode-0",
        effect_scope_complete=True,
    )

    decision = classify_early_execution(frontier, _read())

    assert decision.dependency_class is DependencyClass.CERTIFIED_CONFLICT
    assert decision.reason == "PREVIOUS_EPISODE_CONTEXT_WILL_CHANGE"


def test_cross_namespace_is_certified_disjoint() -> None:
    frontier = FrontierDependencyCertificate.create(
        source_sequence=1,
        group_id="other-group",
        semantic_keys=("alice|entity",),
        candidate_ids=("candidate-alice",),
        node_write_ids=("resolved-alice",),
        publishes_episode=True,
        published_episode_uuid="episode-not-in-context",
        effect_scope_complete=True,
    )

    assert (
        classify_early_execution(frontier, _read()).dependency_class
        is DependencyClass.CERTIFIED_DISJOINT
    )


def test_incomplete_read_scope_stays_unknown_and_never_proves_disjoint() -> None:
    frontier = FrontierDependencyCertificate.create(
        source_sequence=1,
        group_id="g",
        semantic_keys=(),
        candidate_ids=(),
        node_write_ids=(),
        publishes_episode=False,
        effect_scope_complete=False,
    )
    incomplete = replace(_read(), candidate_scope_complete=False)

    assert (
        classify_early_execution(frontier, incomplete).dependency_class
        is DependencyClass.UNKNOWN
    )


def test_candidate_uuid_overlap_is_certified_conflict() -> None:
    frontier = FrontierDependencyCertificate.create(
        source_sequence=1,
        group_id="g",
        semantic_keys=("alice|entity",),
        candidate_ids=("candidate-bob",),
        node_write_ids=(),
        publishes_episode=False,
        effect_scope_complete=True,
    )

    decision = classify_early_execution(frontier, _read())
    assert decision.dependency_class is DependencyClass.CERTIFIED_CONFLICT
    assert decision.reason == "CANDIDATE_ID_OVERLAP"


def test_oracle_rejects_prepared_artifacts_that_arrive_after_publication() -> None:
    row = VDCOracleRow(
        source_sequence=2,
        prepared_durable_ns=120,
        predecessor_publication_ns=100,
        stale_probe_completed_ns=None,
        dependency_class=DependencyClass.UNKNOWN,
        stale_read=None,
        exact_read=_read(state_version=2, previous=("episode-0", "episode-1")),
        exact_node_resolve_service_ns=50,
    )

    result = reduce_vdc_oracle([row], expected_source_sequences=(2,))

    assert result["counts"]["future_prepared_before_publication_count"] == 0
    assert result["counts"]["validatable_unknown_count"] == 0
    assert result["total_hideable_node_resolve_service_ns"] == 0
    assert result["decision"]["status"] == "STOP_V4_VDC_NO_LEGAL_WINDOW"
    assert result["decision"]["live_candidate_authorized"] is False


def test_unknown_is_validatable_only_after_full_exact_identity_match() -> None:
    stale = _read(state_version=1, previous=("episode-0",))
    exact_same = VersionedReadCertificate.create(
        source_sequence=2,
        state_version=2,
        group_id="g",
        semantic_call=replace(stale.semantic_call, state_version=2),
        candidate_ids=stale.candidate_ids,
        semantic_keys=stale.semantic_keys,
        candidate_scope_complete=True,
        previous_episode_scope_complete=True,
    )
    row = VDCOracleRow(
        source_sequence=2,
        prepared_durable_ns=10,
        predecessor_publication_ns=100,
        stale_probe_completed_ns=20,
        dependency_class=DependencyClass.UNKNOWN,
        stale_read=stale,
        exact_read=exact_same,
        exact_node_resolve_service_ns=50,
    )

    result = reduce_vdc_oracle([row], expected_source_sequences=(2,))

    assert result["counts"]["validatable_unknown_count"] == 1
    assert result["total_hideable_node_resolve_service_ns"] == 50
    assert result["decision"]["status"] == "GO_MEMBIND_VDC_IMPLEMENTATION"


def test_previous_context_drift_is_an_exact_validation_miss() -> None:
    stale = _read(state_version=1, previous=("episode-0",))
    exact = _read(state_version=2, previous=("episode-0", "episode-1"))
    row = VDCOracleRow(
        source_sequence=2,
        prepared_durable_ns=10,
        predecessor_publication_ns=100,
        stale_probe_completed_ns=20,
        dependency_class=DependencyClass.UNKNOWN,
        stale_read=stale,
        exact_read=exact,
        exact_node_resolve_service_ns=50,
    )

    result = reduce_vdc_oracle([row], expected_source_sequences=(2,))

    assert result["counts"]["validation_miss_count"] == 1
    assert result["counts"]["validatable_unknown_count"] == 0
    assert result["total_hideable_node_resolve_service_ns"] == 0
    assert result["decision"]["status"] == "STOP_V4_VDC_DEPENDENCY_BOUNDARY"
