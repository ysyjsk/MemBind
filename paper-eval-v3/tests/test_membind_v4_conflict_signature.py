"""RED contracts for legal pre-speculation NodeResolve conflict signals."""

from __future__ import annotations

import inspect

import pytest

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.conflict_signature import (
    enrich_conflict_signature,
    extract_conflict_signature,
    normalize_entity_name,
)
from paper_eval.membind_v4.semantic_call import SemanticCall


def _sha(value: int) -> str:
    return f"{value:064x}"


def _artifact(
    source_sequence: int,
    *,
    namespace: str = "group-a",
    nodes: tuple[dict[str, object], ...],
    edges: tuple[dict[str, object], ...] = (),
) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=source_sequence,
        source_sha256=_sha(source_sequence + 1),
        evidence_sha256=_sha(source_sequence + 101),
        certification_sha256=_sha(999),
        raw_nodes=tuple(
            {"group_id": namespace, "labels": ["Entity"], **node}
            for node in nodes
        ),
        raw_edges=edges,
        pure_intermediates={"node_episode_index_map": {}},
    )


def _semantic_call(
    source_sequence: int,
    *,
    state_version: int,
    existing_uuid: str,
) -> SemanticCall:
    return SemanticCall.create(
        source_sequence=source_sequence,
        state_version=state_version,
        operator_identity={"graphiti_version": "0.29.3"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(200 + source_sequence),
        token_sequence_sha256=_sha(300 + source_sequence),
        prompt_tokens=10,
        extracted_nodes=({"name": "fixture"},),
        candidate_order=("candidate-0",),
        candidate_bindings=(
            {
                "candidate_id": "candidate-0",
                "uuid": existing_uuid,
                "projection": {"name": "fixture"},
            },
        ),
        execution_mode="LLM",
    )


def test_extracts_graphiti_exact_canonical_names_and_entity_types() -> None:
    artifact = _artifact(
        1,
        nodes=(
            {"uuid": "raw-1", "name": "  Alice\t  Smith ", "labels": ["Person", "Entity"]},
            {"uuid": "raw-2", "name": "ACME", "labels": ["Organization"]},
        ),
    )

    signature = extract_conflict_signature(artifact)

    assert normalize_entity_name("  Alice\t  Smith ") == "alice smith"
    assert signature.complete is True
    assert signature.namespace == "group-a"
    assert signature.canonical_names == ("acme", "alice smith")
    assert signature.entity_types == (
        ("acme", ("Organization",)),
        ("alice smith", ("Entity", "Person")),
    )
    assert signature.existing_candidate_ids is None


def test_extractor_has_no_state_reader_or_provider_capability() -> None:
    artifact = _artifact(0, nodes=({"uuid": "raw-0", "name": "Alice"},))

    assert tuple(inspect.signature(extract_conflict_signature).parameters) == ("artifact",)
    with pytest.raises(TypeError):
        extract_conflict_signature(  # type: ignore[call-arg]
            artifact,
            state_reader=lambda: "future-state",
        )


def test_malformed_primary_signal_fails_closed_without_a_state_read() -> None:
    signature = extract_conflict_signature(
        _artifact(0, nodes=({"uuid": "raw-0", "name": "   "},))
    )

    assert signature.complete is False
    assert signature.canonical_names == ()
    assert "ENTITY_NAME_INVALID" in signature.incomplete_reasons


def test_existing_candidate_ids_are_enriched_only_from_materialized_call() -> None:
    signature = extract_conflict_signature(
        _artifact(1, nodes=({"uuid": "raw-1", "name": "Alice"},))
    )

    enriched = enrich_conflict_signature(
        signature,
        _semantic_call(1, state_version=0, existing_uuid="entity-uuid-1"),
    )

    assert signature.existing_candidate_ids is None
    assert enriched.existing_candidate_ids == ("entity-uuid-1",)
    assert enriched.published_state_version == 0


def test_unresolved_relation_endpoint_marks_signal_unknown() -> None:
    signature = extract_conflict_signature(
        _artifact(
            1,
            nodes=({"uuid": "raw-1", "name": "Alice"},),
            edges=(
                {
                    "group_id": "group-a",
                    "source_node_uuid": "raw-1",
                    "target_node_uuid": "missing-raw-node",
                },
            ),
        )
    )

    assert signature.complete is False
    assert "RELATION_ENDPOINT_UNRESOLVED" in signature.incomplete_reasons
