"""TDD contracts for MemBind-v1 durable attempt storage and recovery."""

from __future__ import annotations

import json

import pytest

from paper_eval.membind_v1.delta import PreparedNodeArtifact
from paper_eval.membind_v1.store import (
    MemBindV1StoreError,
    MemBindV1AttemptStore,
    inspect_membind_v1_attempt,
)


def _artifact(sequence: int) -> PreparedNodeArtifact:
    return PreparedNodeArtifact.create(
        source_sequence=sequence,
        source_sha256=f"{sequence + 1:064x}",
        evidence_prefix_sha256="b" * 64,
        episode_projection_sha256="c" * 64,
        operation_identity_sha256="d" * 64,
        model_identity_sha256="e" * 64,
        prompt_identity_sha256="f" * 64,
        schema_identity_sha256="1" * 64,
        config_identity_sha256="2" * 64,
        extracted_nodes=[{"name": f"node-{sequence}", "uuid": f"node-{sequence}"}],
        node_episode_index_map={f"node-{sequence}": [0]},
    )


def _store(tmp_path) -> MemBindV1AttemptStore:
    return MemBindV1AttemptStore.create(
        tmp_path / "attempt",
        run_id="mv1-store-test-001",
        namespace="pev3-mv1-store-test-001-u0-07741c45-a001",
        source_sha256s=(f"{1:064x}", f"{2:064x}"),
        source_manifest_sha256="a" * 64,
        execution_identity_sha256="b" * 64,
    )


def test_store_writes_hash_bound_durable_prepared_artifact_and_recovers_only_verified_state(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    store.record_intent(0)
    store.record_prepare_started(0)
    store.persist_prepared(_artifact(0))

    checked = inspect_membind_v1_attempt(tmp_path / "attempt")

    assert checked["checkpoint"]["resume_status"] == "RESUME_FROM_PREPARED_DURABLE"
    assert checked["checkpoint"]["published_frontier"] == -1
    assert checked["prepared_source_sequences"] == [0]
    assert checked["events"][-1]["state"] == "PREPARED_DURABLE"
    artifact_path = tmp_path / "attempt" / "prepared" / "00000000.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["artifact_sha256"] == _artifact(0).artifact_sha256


def test_store_rejects_duplicate_or_mismatched_prepared_artifact(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_intent(0)
    store.record_prepare_started(0)
    store.persist_prepared(_artifact(0))

    with pytest.raises(MemBindV1StoreError, match="prepared_artifact_exists"):
        store.persist_prepared(_artifact(0))
    with pytest.raises(MemBindV1StoreError, match="prepared_source_identity"):
        store.persist_prepared(_artifact(1))


def test_crash_after_commit_return_is_explicitly_poisoned_and_never_resumable(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_intent(0)
    store.record_prepare_started(0)
    store.persist_prepared(_artifact(0))
    store.record_bind_started(0)
    store.record_commit_returned(0)

    checked = inspect_membind_v1_attempt(tmp_path / "attempt")

    assert checked["checkpoint"]["resume_status"] == "AMBIGUOUS_COMMIT_POISONED"
    assert checked["checkpoint"]["status"] == "incomplete_non_mergeable"
    assert checked["frontier_state"][0] == "AMBIGUOUS_COMMIT_POISONED"
    with pytest.raises(MemBindV1StoreError, match="attempt_poisoned"):
        MemBindV1AttemptStore.open_existing(tmp_path / "attempt")


def test_store_advances_only_a_source_ordered_durable_publication_prefix(tmp_path) -> None:
    store = _store(tmp_path)
    for sequence in (0, 1):
        store.record_intent(sequence)
        store.record_prepare_started(sequence)
        store.persist_prepared(_artifact(sequence))

    with pytest.raises(MemBindV1StoreError, match="bind_not_at_frontier"):
        store.record_bind_started(1)

    store.record_bind_started(0)
    store.record_commit_returned(0)
    store.record_publication_durable(0)
    store.record_bind_started(1)
    store.record_commit_returned(1)
    store.record_publication_durable(1)
    store.complete()

    checked = inspect_membind_v1_attempt(tmp_path / "attempt")
    assert checked["checkpoint"]["status"] == "complete"
    assert checked["checkpoint"]["published_frontier"] == 1
    assert checked["checkpoint"]["resume_status"] == "NOT_NEEDED_COMPLETE"


def test_store_inspection_fails_closed_when_a_prepared_artifact_is_tampered(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_intent(0)
    store.record_prepare_started(0)
    store.persist_prepared(_artifact(0))
    artifact_path = tmp_path / "attempt" / "prepared" / "00000000.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["artifact_sha256"] = "0" * 64
    artifact_path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(MemBindV1StoreError, match="prepared_artifact_invalid"):
        inspect_membind_v1_attempt(tmp_path / "attempt")
