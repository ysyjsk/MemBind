"""RED contracts for the read-only S4 edge-identity diagnosis.

The fixtures are deliberately synthetic. Public diagnosis artifacts may contain
hashes, counts, classifications, and side-effect evidence, but never private
prompt-cache contents or raw graph facts.
"""

from __future__ import annotations

import copy
import importlib
from pathlib import Path

import pytest


REPLAY_RUN_ID = "s4-d0-replay-20260815-005"
HISTORY_ID = "07741c45"
SOURCE_SEQUENCE = 7
EVIDENCE_SHA256 = {
    "capture_canonical_graph_sha256": "1" * 64,
    "capture_phase_result_sha256": "2" * 64,
    "dataset_sha256": "3" * 64,
    "embedding_cache_sha256": "4" * 64,
    "prompt_cache_sha256": "5" * 64,
    "replay_checkpoint_sha256": "6" * 64,
    "replay_events_sha256": "7" * 64,
    "replay_phase_result_sha256": "8" * 64,
    "split_sha256": "9" * 64,
}
SOURCE_HASH = "a" * 64
MANIFEST_SHA256 = "b" * 64
PERSISTED_DIAGNOSIS = {
    "classification": "NON_INJECTIVE_FACT_ONLY_EDGE_CANDIDATE_IDENTITY_CONFIRMED",
    "source_sequence": 7,
    "edge_extraction_record_count": 1,
    "extracted_edge_count": 10,
    "edge_resolution_prompt_count": 10,
    "ambiguous_prompt_count": 9,
    "duplicate_fact_sha256": "c" * 64,
    "duplicate_fact_multiplicity": 2,
    "matching_capture_graph_edge_count": 2,
    "matching_edges_directed_endpoints_distinct": True,
    "capture_replay_bijection_proved": False,
}


@pytest.fixture
def diagnosis_module():
    """Import lazily so every behavioral contract collects while still RED."""

    return importlib.import_module("paper_eval.s4_edge_identity_diagnosis")


def _edge(**overrides) -> dict:
    value = {
        "uuid": "volatile-edge-1",
        "group_id": "volatile-namespace",
        "source_node_uuid": "source-1",
        "target_node_uuid": "target-1",
        "created_at": "volatile-created-at",
        "name": "RELATION",
        "fact": "synthetic shared fact",
        "episodes": ["episode-1"],
        "valid_at": "2025-01-01T00:00:00+00:00",
        "invalid_at": None,
        "reference_time": "2025-01-01T00:00:00+00:00",
        "expired_at": None,
        "attributes": {"stable": "value", "order": [2, 1]},
        "fact_embedding": [0.1, 0.2],
        "rank": 1,
        "position": 0,
    }
    value.update(overrides)
    return value


def _endpoint(name: str, *, summary: str | None = None) -> dict:
    return {
        "normalized_name": name.casefold(),
        "labels": ["Entity", "Synthetic"],
        "summary": summary or f"synthetic summary for {name}",
        "attributes": {"kind": "synthetic"},
    }


def _lookups() -> tuple[dict, dict]:
    endpoint_by_uuid = {
        "source-1": _endpoint("Source One"),
        "source-2": _endpoint("Source Two"),
        "target-1": _endpoint("Target One"),
        "target-2": _endpoint("Target Two"),
    }
    provenance_by_uuid = {
        "episode-1": {
            "source_sequence": 7,
            "source_hash": "1" * 64,
        },
        "episode-2": {
            "source_sequence": 6,
            "source_hash": "2" * 64,
        },
    }
    return endpoint_by_uuid, provenance_by_uuid


def _diagnosis(diagnosis_module) -> dict:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    return diagnosis_module.diagnose_candidate_partition(
        candidates=[_edge()],
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )


def _calls(diagnosis_module) -> list[dict]:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    empty = diagnosis_module.diagnose_candidate_partition(
        candidates=[],
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )
    return [
        {
            "call_correlation_sha256": f"{index:064x}",
            "partitions": {
                "related": copy.deepcopy(empty),
                "invalidation": diagnosis_module.diagnose_candidate_partition(
                    candidates=[_edge(position=index)],
                    endpoint_by_uuid=endpoint_by_uuid,
                    provenance_by_uuid=provenance_by_uuid,
                ),
            },
        }
        for index in range(10)
    ]


def _artifact(diagnosis_module) -> dict:
    return diagnosis_module.build_edge_identity_diagnosis(
        replay_run_id=REPLAY_RUN_ID,
        history_id=HISTORY_ID,
        attempt_id="005",
        source_sequence=SOURCE_SEQUENCE,
        source_hash=SOURCE_HASH,
        episode_manifest_sha256=MANIFEST_SHA256,
        evidence_sha256=copy.deepcopy(EVIDENCE_SHA256),
        persisted_evidence_diagnosis=copy.deepcopy(PERSISTED_DIAGNOSIS),
        candidate_call_diagnoses=_calls(diagnosis_module),
        pre_state={
            "node_count": 32,
            "relationship_count": 48,
            "episode_count": 7,
            "episode_names_sha256": "c" * 64,
            "canonical_snapshot_sha256": "d" * 64,
        },
        post_state={
            "node_count": 32,
            "relationship_count": 48,
            "episode_count": 7,
            "episode_names_sha256": "c" * 64,
            "canonical_snapshot_sha256": "d" * 64,
        },
        cache_sha256_before={
            "prompt": EVIDENCE_SHA256["prompt_cache_sha256"],
            "embedding": EVIDENCE_SHA256["embedding_cache_sha256"],
        },
        cache_sha256_after={
            "prompt": EVIDENCE_SHA256["prompt_cache_sha256"],
            "embedding": EVIDENCE_SHA256["embedding_cache_sha256"],
        },
        side_effect_counters={
            "network_call_count": 0,
            "live_llm_call_count": 0,
            "live_embedding_call_count": 0,
            "cross_encoder_call_count": 0,
            "db_write_count": 0,
            "publication_count": 0,
            "cache_write_count": 0,
            "neo4j_read_count": 12,
        },
    )


def _verify(diagnosis_module, artifact: dict) -> dict:
    return diagnosis_module.verify_edge_identity_diagnosis(
        artifact,
        expected_evidence_sha256=EVIDENCE_SHA256,
        expected_source_hash=SOURCE_HASH,
        expected_episode_manifest_sha256=MANIFEST_SHA256,
    )


def test_diagnosis_binds_retry_005_source_sequence_7_and_evidence(
    diagnosis_module,
) -> None:
    artifact = _artifact(diagnosis_module)

    assert _verify(diagnosis_module, artifact) == artifact
    assert artifact["execution_identity"] == {
        "attempt_id": "005",
        "history_id": HISTORY_ID,
        "replay_run_id": REPLAY_RUN_ID,
        "source_sequence": SOURCE_SEQUENCE,
        "source_hash": SOURCE_HASH,
        "episode_manifest_sha256": MANIFEST_SHA256,
    }
    assert artifact["evidence_sha256"] == EVIDENCE_SHA256
    assert artifact["persisted_evidence_diagnosis"] == PERSISTED_DIAGNOSIS


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("attempt_id", "004"),
        ("replay_run_id", "s4-d0-replay-20260815-004"),
        ("source_sequence", 6),
    ],
)
def test_verifier_rejects_retry_or_source_binding_drift(
    diagnosis_module,
    field: str,
    wrong_value,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["execution_identity"][field] = wrong_value

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


@pytest.mark.parametrize("evidence_name", sorted(EVIDENCE_SHA256))
def test_verifier_rejects_evidence_hash_drift(
    diagnosis_module,
    evidence_name: str,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["evidence_sha256"][evidence_name] = "f" * 64

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


def test_same_fact_with_different_directed_endpoints_is_unique(
    diagnosis_module,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    candidates = [
        _edge(source_node_uuid="source-1", target_node_uuid="target-1"),
        _edge(source_node_uuid="source-2", target_node_uuid="target-1"),
        _edge(source_node_uuid="target-1", target_node_uuid="source-1"),
    ]

    result = diagnosis_module.diagnose_candidate_partition(
        candidates=candidates,
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )

    assert result["classification"] == "UNIQUE"
    assert result["candidate_count"] == 3
    assert result["identity_count"] == 3
    assert len(set(result["identity_sha256"])) == 3


def test_same_fact_and_endpoints_with_different_stable_provenance_is_unique(
    diagnosis_module,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    candidates = [
        _edge(episodes=["episode-1"]),
        _edge(episodes=["episode-2"]),
    ]

    result = diagnosis_module.diagnose_candidate_partition(
        candidates=candidates,
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )

    assert result["classification"] == "UNIQUE"
    assert result["candidate_count"] == 2
    assert result["identity_count"] == 2


def test_empty_prehydration_summary_is_a_deterministic_identity_value(
    diagnosis_module,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    endpoint_by_uuid["source-1"]["summary"] = ""
    endpoint_by_uuid["target-1"]["summary"] = ""

    first = diagnosis_module.edge_identity_sha256(
        _edge(), endpoint_by_uuid, provenance_by_uuid
    )
    second = diagnosis_module.edge_identity_sha256(
        _edge(uuid="different-runtime-uuid"),
        copy.deepcopy(endpoint_by_uuid),
        provenance_by_uuid,
    )

    assert first == second


@pytest.mark.parametrize("summary", [None, 7, [], {}])
def test_endpoint_summary_must_still_be_a_string(
    diagnosis_module,
    summary: object,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    endpoint_by_uuid["source-1"]["summary"] = summary

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError, match="summary"):
        diagnosis_module.edge_identity_sha256(
            _edge(), endpoint_by_uuid, provenance_by_uuid
        )


def test_fully_identical_stable_projections_remain_ambiguous(
    diagnosis_module,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    candidates = [
        _edge(uuid="volatile-edge-a", rank=1, position=0),
        _edge(
            uuid="volatile-edge-b",
            group_id="another-volatile-namespace",
            created_at="another-volatile-time",
            rank=99,
            position=42,
        ),
    ]

    result = diagnosis_module.diagnose_candidate_partition(
        candidates=candidates,
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )

    assert result["classification"] == "AMBIGUOUS"
    assert result["candidate_count"] == 2
    assert result["identity_count"] == 1
    assert result["ambiguous_group_count"] == 1


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("uuid", "another-edge-uuid"),
        ("rank", 999),
        ("position", 999),
        ("group_id", "another-group-id"),
        ("created_at", "2030-12-31T23:59:59+00:00"),
        ("fact_embedding", [9.0, 8.0, 7.0]),
    ],
)
def test_volatile_fields_do_not_affect_identity(
    diagnosis_module,
    field: str,
    replacement,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    baseline = _edge()
    changed = copy.deepcopy(baseline)
    changed[field] = replacement

    assert diagnosis_module.edge_identity_sha256(
        baseline,
        endpoint_by_uuid,
        provenance_by_uuid,
    ) == diagnosis_module.edge_identity_sha256(
        changed,
        endpoint_by_uuid,
        provenance_by_uuid,
    )


def test_canonical_order_of_attributes_and_provenance_does_not_change_identity(
    diagnosis_module,
) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()
    baseline = _edge(
        episodes=["episode-1", "episode-2"],
        attributes={"alpha": 1, "beta": {"x": 2, "y": 3}},
    )
    reordered = _edge(
        episodes=["episode-2", "episode-1"],
        attributes={"beta": {"y": 3, "x": 2}, "alpha": 1},
    )

    assert diagnosis_module.edge_identity_sha256(
        baseline,
        endpoint_by_uuid,
        provenance_by_uuid,
    ) == diagnosis_module.edge_identity_sha256(
        reordered,
        endpoint_by_uuid,
        provenance_by_uuid,
    )


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "raw_fact",
        "raw_prompt",
        "raw_response",
        "private_cache_path",
        "private_prompt",
        "private_fact",
    ],
)
def test_public_artifact_rejects_raw_or_private_fields_recursively(
    diagnosis_module,
    forbidden_key: str,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["candidate_call_diagnoses"][0]["partitions"]["related"]["nested"] = {
        forbidden_key: "must-not-be-public"
    }

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


@pytest.mark.parametrize(
    ("section", "field", "replacement"),
    [
        ("post_state", "relationship_count", 49),
        ("cache_sha256_after", "prompt", "f" * 64),
        ("cache_sha256_after", "embedding", "f" * 64),
    ],
)
def test_verifier_requires_equal_pre_post_state_and_cache_evidence(
    diagnosis_module,
    section: str,
    field: str,
    replacement,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["read_only_evidence"][section][field] = replacement

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


@pytest.mark.parametrize(
    "counter_name",
    [
        "network_call_count",
        "live_llm_call_count",
        "live_embedding_call_count",
        "db_write_count",
        "publication_count",
        "cache_write_count",
        "cross_encoder_call_count",
    ],
)
def test_verifier_requires_zero_side_effect_counters(
    diagnosis_module,
    counter_name: str,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["side_effect_counters"][counter_name] = 1

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


def test_finalization_is_exclusive_and_cannot_overwrite(
    diagnosis_module,
    tmp_path: Path,
) -> None:
    artifact = _artifact(diagnosis_module)
    output_path = tmp_path / "S4_EDGE_IDENTITY_DIAGNOSIS.json"

    diagnosis_module.write_edge_identity_diagnosis_exclusive(output_path, artifact)
    original_bytes = output_path.read_bytes()

    with pytest.raises(FileExistsError):
        diagnosis_module.write_edge_identity_diagnosis_exclusive(
            output_path,
            _artifact(diagnosis_module),
        )

    assert output_path.read_bytes() == original_bytes


def test_empty_partition_is_not_claimed_unique(diagnosis_module) -> None:
    endpoint_by_uuid, provenance_by_uuid = _lookups()

    result = diagnosis_module.diagnose_candidate_partition(
        candidates=[],
        endpoint_by_uuid=endpoint_by_uuid,
        provenance_by_uuid=provenance_by_uuid,
    )

    assert result["classification"] == "EMPTY"
    assert result["candidate_count"] == 0
    assert result["identity_count"] == 0


def test_verifier_requires_exact_ten_unique_call_correlations(
    diagnosis_module,
) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["candidate_call_diagnoses"] = artifact[
        "candidate_call_diagnoses"
    ][:-1]
    artifact["artifact_sha256"] = diagnosis_module.payload_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


def test_verifier_recomputes_verdict_and_reason(diagnosis_module) -> None:
    artifact = _artifact(diagnosis_module)
    artifact["verdict"] = "LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP"
    artifact["reason"] = "IDENTITY_AMBIGUOUS"
    artifact["artifact_sha256"] = diagnosis_module.payload_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)


@pytest.mark.parametrize("partition", ["related", "invalidation"])
def test_verifier_requires_both_partition_diagnoses(
    diagnosis_module,
    partition: str,
) -> None:
    artifact = _artifact(diagnosis_module)
    del artifact["candidate_call_diagnoses"][0]["partitions"][partition]
    artifact["artifact_sha256"] = diagnosis_module.payload_sha256(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )

    with pytest.raises(diagnosis_module.EdgeIdentityDiagnosisError):
        _verify(diagnosis_module, artifact)
