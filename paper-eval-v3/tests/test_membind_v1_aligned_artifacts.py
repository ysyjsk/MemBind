"""TDD contracts for durable, public rows of aligned live blocks.

The artifact layer is deliberately offline.  It protects the identity and
durability boundary shared by U0-aligned, P(C=2)-aligned, and MemBind-v1;
Graphiti execution and scheduling remain outside this module.
"""

from __future__ import annotations

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import (
    AlignedArtifactsError,
    AlignedBlockArtifactStore,
    build_public_aligned_row,
    inspect_aligned_block_artifacts,
    verify_public_aligned_row,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)


def _plan(*, envelope: str = "a" * 64) -> dict[str, object]:
    source_hashes = {
        history_id: [f"{offset + item + 1:064x}" for item in range(3)]
        for offset, history_id in enumerate(ALIGNED_DEVELOPMENT_HISTORIES, start=100)
    }
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-artifact-test-001",
            history_source_sha256s=source_hashes,
            interarrival_ns=1_000,
            shared_execution_envelope_sha256=envelope,
        )
    )


def _metrics() -> dict[str, object]:
    return {
        "qa_accuracy": 0.0,
        "evidence_recall_at_10": 1.0,
        "direct_violations": 0,
        "p95_arrival_to_publication_ns": 100,
        "p99_arrival_to_publication_ns": 200,
        "successful_goodput_episodes_per_second": 1.0,
        "makespan_ns": 300,
        "max_backlog": 1,
    }


def _complete(store: AlignedBlockArtifactStore) -> None:
    for sequence in range(store.source_count):
        store.append_lifecycle(sequence, event_type="ARRIVAL", timestamp_ns=sequence * 10)
        store.append_lifecycle(sequence, event_type="ENQUEUED", timestamp_ns=sequence * 10 + 1)
        store.append_lifecycle(sequence, event_type="SERVICE_STARTED", timestamp_ns=sequence * 10 + 2)
        store.append_lifecycle(
            sequence,
            event_type="PUBLICATION_DURABLE",
            timestamp_ns=sequence * 10 + 3,
            telemetry={"worker_id": 0, "retry_count": 0},
        )


def test_block_artifacts_are_fresh_hash_bound_complete_and_emit_a_main_table_row(tmp_path) -> None:
    plan = _plan()
    store = AlignedBlockArtifactStore.create(
        tmp_path / "u0-block",
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    _complete(store)

    inspected = inspect_aligned_block_artifacts(tmp_path / "u0-block")
    row = build_public_aligned_row(
        tmp_path / "u0-block",
        verified_plan=plan,
        block_index=0,
        metrics=_metrics(),
        quality_status="NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
    )

    assert inspected["checkpoint"]["terminal_status"] == "COMPLETED"
    assert inspected["checkpoint"]["completed_source_prefix"] == 2
    assert inspected["checkpoint"]["complete_coverage"] is True
    assert row["method"] == "U0-aligned"
    assert row["execution_status"] == "COMPLETED"
    assert row["validity_status"] == "VALID"
    assert row["global_llm_admission_k"] == 2
    assert row["source_manifest_sha256"] == plan["source_manifest_sha256"]
    assert row["arrival_trace_sha256"] == plan["arrival_trace_sha256"]
    assert row["shared_execution_envelope_sha256"] == plan[
        "shared_execution_envelope_sha256"
    ]
    assert verify_public_aligned_row(row, verified_plan=plan, block_index=0) == row


def test_telemetry_is_append_only_hash_bound_and_rejects_private_content(tmp_path) -> None:
    store = AlignedBlockArtifactStore.create(
        tmp_path / "u0-block",
        verified_plan=_plan(),
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    store.append_lifecycle(0, event_type="ARRIVAL", timestamp_ns=0)

    with pytest.raises(AlignedArtifactsError, match="content safe"):
        store.append_lifecycle(
            0,
            event_type="ENQUEUED",
            timestamp_ns=1,
            telemetry={"prompt": "must never enter telemetry"},
        )

    inspected = inspect_aligned_block_artifacts(tmp_path / "u0-block")
    assert [event["event_type"] for event in inspected["events"]] == ["ARRIVAL"]
    assert inspected["events"][0]["event_sha256"]


def test_ambiguous_commit_is_non_mergeable_and_cannot_be_resumed(tmp_path) -> None:
    store = AlignedBlockArtifactStore.create(
        tmp_path / "u0-block",
        verified_plan=_plan(),
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    for event_type, timestamp_ns in (
        ("ARRIVAL", 0),
        ("ENQUEUED", 1),
        ("SERVICE_STARTED", 2),
        ("AMBIGUOUS_COMMIT", 3),
    ):
        store.append_lifecycle(0, event_type=event_type, timestamp_ns=timestamp_ns)

    inspected = inspect_aligned_block_artifacts(tmp_path / "u0-block")
    assert inspected["checkpoint"]["terminal_status"] == "INCOMPLETE_NON_MERGEABLE"
    assert inspected["checkpoint"]["resume_status"] == "AMBIGUOUS_COMMIT_POISONED"
    with pytest.raises(AlignedArtifactsError, match="ambiguous commit"):
        AlignedBlockArtifactStore.open_existing(tmp_path / "u0-block")


def test_public_row_fails_closed_when_a_different_verified_plan_is_supplied(tmp_path) -> None:
    plan = _plan()
    store = AlignedBlockArtifactStore.create(
        tmp_path / "u0-block",
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    _complete(store)

    with pytest.raises(AlignedArtifactsError, match="plan block binding"):
        build_public_aligned_row(
            tmp_path / "u0-block",
            verified_plan=_plan(envelope="c" * 64),
            block_index=0,
            metrics=_metrics(),
            quality_status="NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
        )


def test_public_row_rejects_a_resealed_manifest_hash_not_bound_to_its_plan_block(tmp_path) -> None:
    plan = _plan()
    store = AlignedBlockArtifactStore.create(
        tmp_path / "u0-block",
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    _complete(store)
    row = build_public_aligned_row(
        tmp_path / "u0-block",
        verified_plan=plan,
        block_index=0,
        metrics=_metrics(),
        quality_status="NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
    )
    row["manifest_sha256"] = "0" * 64
    row["row_sha256"] = payload_sha256(
        {key: value for key, value in row.items() if key != "row_sha256"}
    )

    with pytest.raises(AlignedArtifactsError, match="plan block binding"):
        verify_public_aligned_row(row, verified_plan=plan, block_index=0)
