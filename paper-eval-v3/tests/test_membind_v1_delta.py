"""TDD contract tests for canonical PreparedNodeArtifact hashing."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.delta import (
    MemBindV1DeltaError,
    PreparedNodeArtifact,
)


H = "a" * 64


def _artifact(**changes: object) -> PreparedNodeArtifact:
    values: dict[str, object] = {
        "source_sequence": 3,
        "source_sha256": H,
        "evidence_prefix_sha256": "b" * 64,
        "episode_projection_sha256": "c" * 64,
        "operation_identity_sha256": "d" * 64,
        "model_identity_sha256": "e" * 64,
        "prompt_identity_sha256": "f" * 64,
        "schema_identity_sha256": "1" * 64,
        "config_identity_sha256": "2" * 64,
        "extracted_nodes": [
            {"name": "Alice", "uuid": "node-1"},
            {"name": "Bob", "uuid": "node-2"},
        ],
        "node_episode_index_map": {"node-1": [0], "node-2": [0]},
    }
    values.update(changes)
    return PreparedNodeArtifact.create(**values)


def test_prepared_node_artifact_hash_is_canonical_and_verifiable() -> None:
    left = _artifact()
    right = _artifact(
        extracted_nodes=[
            {"uuid": "node-1", "name": "Alice"},
            {"uuid": "node-2", "name": "Bob"},
        ],
        node_episode_index_map={"node-2": [0], "node-1": [0]},
    )

    assert left.artifact_sha256 == right.artifact_sha256
    assert left.verify() is left
    nodes = left.extracted_nodes
    nodes[0]["name"] = "mutated"
    assert left.extracted_nodes[0]["name"] == "Alice"


def test_prepared_node_artifact_rejects_invalid_or_noncanonical_semantic_data() -> None:
    with pytest.raises(MemBindV1DeltaError, match="node_episode_index"):
        _artifact(node_episode_index_map={"node-1": [-1]})
    with pytest.raises(MemBindV1DeltaError, match="extracted_nodes"):
        _artifact(extracted_nodes="not-a-sequence")


def test_prepared_node_artifact_verification_detects_hash_tampering() -> None:
    artifact = _artifact()
    tampered = object.__new__(PreparedNodeArtifact)
    for field in PreparedNodeArtifact.__dataclass_fields__:  # type: ignore[attr-defined]
        object.__setattr__(tampered, field, getattr(artifact, field))
    object.__setattr__(tampered, "artifact_sha256", "0" * 64)

    with pytest.raises(MemBindV1DeltaError, match="artifact_hash_mismatch"):
        tampered.verify()
