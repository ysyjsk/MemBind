"""RED contracts for deterministic conflict-only profitability prediction."""

from __future__ import annotations

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.conflict_classifier import (
    ConflictClass,
    RecentConflictTelemetry,
    classify_conflict,
)
from paper_eval.membind_v4.conflict_signature import (
    enrich_conflict_signature,
    extract_conflict_signature,
)
from paper_eval.membind_v4.semantic_call import SemanticCall


def _sha(value: int) -> str:
    return f"{value:064x}"


def _signature(
    source_sequence: int,
    name: str,
    *,
    namespace: str = "group-a",
):
    artifact = PreparedArtifact.create(
        source_sequence=source_sequence,
        source_sha256=_sha(source_sequence + 1),
        evidence_sha256=_sha(source_sequence + 101),
        certification_sha256=_sha(999),
        raw_nodes=(
            {
                "uuid": f"raw-{source_sequence}",
                "group_id": namespace,
                "name": name,
                "labels": ["Entity"],
            },
        ),
        raw_edges=(),
    )
    return extract_conflict_signature(artifact)


def _enrich(signature, existing_uuid: str):
    source = signature.source_sequence
    call = SemanticCall.create(
        source_sequence=source,
        state_version=max(0, source - 1),
        operator_identity={"graphiti_version": "0.29.3"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(200 + source),
        token_sequence_sha256=_sha(300 + source),
        prompt_tokens=10,
        extracted_nodes=({"name": "fixture"},),
        candidate_order=("c0",),
        candidate_bindings=(
            {"candidate_id": "c0", "uuid": existing_uuid, "projection": {"name": "fixture"}},
        ),
        execution_mode="LLM",
    )
    return enrich_conflict_signature(signature, call)


def test_known_disjoint_entities_are_low_conflict() -> None:
    decision = classify_conflict(_signature(0, "Alice"), _signature(1, "Bob"))

    assert decision.conflict_class is ConflictClass.LOW_CONFLICT
    assert decision.reason == "KNOWN_DISJOINT"


def test_same_canonical_entity_is_high_even_when_case_and_space_differ() -> None:
    decision = classify_conflict(
        _signature(0, "Alice Smith"),
        _signature(1, "  ALICE\tSMITH  "),
    )

    assert decision.conflict_class is ConflictClass.HIGH_CONFLICT
    assert decision.reason == "DIRECT_ENTITY_OVERLAP"
    assert decision.overlapping_entity_names == ("alice smith",)


def test_same_existing_entity_uuid_is_high_conflict() -> None:
    decision = classify_conflict(
        _enrich(_signature(0, "Alice"), "same-existing-uuid"),
        _enrich(_signature(1, "Bob"), "same-existing-uuid"),
    )

    assert decision.conflict_class is ConflictClass.HIGH_CONFLICT
    assert decision.reason == "EXISTING_CANDIDATE_ID_OVERLAP"


def test_insufficient_primary_evidence_is_unknown() -> None:
    incomplete = extract_conflict_signature(
        PreparedArtifact.create(
            source_sequence=1,
            source_sha256=_sha(2),
            evidence_sha256=_sha(102),
            certification_sha256=_sha(999),
            raw_nodes=({"uuid": "raw-1", "group_id": "group-a"},),
        )
    )

    decision = classify_conflict(_signature(0, "Alice"), incomplete)

    assert decision.conflict_class is ConflictClass.UNKNOWN
    assert decision.reason == "INCOMPLETE_SIGNAL"


def test_verified_namespace_isolation_is_low_conflict() -> None:
    decision = classify_conflict(
        _signature(0, "Alice", namespace="group-a"),
        _signature(1, "Alice", namespace="group-b"),
    )

    assert decision.conflict_class is ConflictClass.LOW_CONFLICT
    assert decision.reason == "NAMESPACE_ISOLATED"


def test_recent_hot_entity_is_high_conflict_from_prior_completed_events_only() -> None:
    telemetry = RecentConflictTelemetry(window_size=4, hot_threshold=2)
    published = _signature(0, "Alice")
    telemetry.record_publication(published, validation_outcome="HIT")
    telemetry.record_publication(published, validation_outcome="MISS")

    decision = classify_conflict(
        _signature(1, "Bob"),
        _signature(2, "Alice"),
        telemetry=telemetry,
    )

    assert decision.conflict_class is ConflictClass.HIGH_CONFLICT
    assert decision.reason == "RECENT_HOT_ENTITY"
    assert telemetry.outcome_counts == {"HIT": 1, "MISS": 1}

