"""TDD contracts for pure metrics derived from one complete aligned block."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import AlignedBlockArtifactStore
from paper_eval.membind_v1.aligned_metrics import (
    AlignedMetricsError,
    build_aligned_quality_and_correctness,
    derive_aligned_block_output,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_DEVELOPMENT_HISTORIES,
    build_aligned_development_plan,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_reduce import reduce_aligned_blocks


def _plan() -> dict[str, object]:
    return verify_aligned_development_plan(
        build_aligned_development_plan(
            aligned_run_id="aligned-metrics-test-001",
            history_source_sha256s={
                history_id: [
                    payload_sha256(
                        {"history_id": history_id, "source_sequence": sequence}
                    )
                    for sequence in range(3)
                ]
                for history_id in ALIGNED_DEVELOPMENT_HISTORIES
            },
            interarrival_ns=10,
            shared_execution_envelope_sha256="a" * 64,
        )
    )


def _complete(root: Path, *, plan: dict[str, object], block_index: int = 0) -> None:
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=block_index,
        execution_identity_sha256="b" * 64,
    )
    # Events are physically appended source-by-source but use the frozen
    # logical arrivals.  The reducer must use timestamps, not append order.
    for sequence, (arrival, service_start, publication) in enumerate(
        ((100, 120, 200), (110, 200, 230), (120, 230, 260))
    ):
        store.append_lifecycle(sequence, event_type="ARRIVAL", timestamp_ns=arrival)
        store.append_lifecycle(
            sequence, event_type="ENQUEUED", timestamp_ns=arrival + 5
        )
        store.append_lifecycle(
            sequence, event_type="SERVICE_STARTED", timestamp_ns=service_start
        )
        store.append_lifecycle(
            sequence,
            event_type="PUBLICATION_DURABLE",
            timestamp_ns=publication,
            telemetry={"execution_path": "test"},
        )


def _quality(
    root: Path,
    *,
    plan: dict[str, object],
    block_index: int = 0,
    qa_accuracy: float | None = 0.25,
    quality_status: str = "NUMERICALLY_COMPARABLE",
) -> dict[str, object]:
    return build_aligned_quality_and_correctness(
        root,
        verified_plan=plan,
        block_index=block_index,
        qa_accuracy=qa_accuracy,
        evidence_recall_at_10=0.5,
        direct_violations=2,
        quality_status=quality_status,
    )


def test_complete_lifecycle_and_explicit_quality_derive_sealed_row_and_samples(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)
    quality = _quality(root, plan=plan)

    derived = derive_aligned_block_output(
        root,
        verified_plan=plan,
        block_index=0,
        quality_and_correctness=quality,
    )

    assert derived["metrics"] == {
        "qa_accuracy": 0.25,
        "evidence_recall_at_10": 0.5,
        "direct_violations": 2,
        "p95_arrival_to_publication_ns": 140,
        "p99_arrival_to_publication_ns": 140,
        "successful_goodput_episodes_per_second": pytest.approx(3e9 / 160),
        "makespan_ns": 160,
        "max_backlog": 3,
    }
    assert [sample["arrival_to_publication_ns"] for sample in derived["per_source"]] == [
        100,
        120,
        140,
    ]
    assert [sample["queue_depth_at_arrival"] for sample in derived["per_source"]] == [
        1,
        2,
        3,
    ]
    assert derived["public_row"]["row_sha256"]
    assert derived["freshness_record"]["samples_sha256"]
    assert derived["public_row"]["metrics"] == derived["metrics"]
    # The public artifacts are directly consumable by the aligned reducer;
    # one-block inventory failure is deliberate proof of shape compatibility.
    with pytest.raises(Exception, match="block inventory"):
        reduce_aligned_blocks(
            verified_plan=plan,
            public_rows=[derived["public_row"]],
            freshness_records=[derived["freshness_record"]],
        )


def test_degenerate_quality_requires_explicit_none_and_uses_only_a_labeled_schema_placeholder(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)
    quality = _quality(
        root,
        plan=plan,
        qa_accuracy=None,
        quality_status="NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE",
    )

    derived = derive_aligned_block_output(
        root,
        verified_plan=plan,
        block_index=0,
        quality_and_correctness=quality,
    )

    assert quality["qa_accuracy"] is None
    assert derived["quality_status"] == "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE"
    assert derived["qa_accuracy_input"] is None
    # Existing public-row/main-table schemas require a numeric field; the
    # renderer suppresses it under the explicit NQ status.  This is not a
    # measured zero and is retained in the derived record as None above.
    assert derived["public_row"]["metrics"]["qa_accuracy"] == 0.0


@pytest.mark.parametrize(
    ("qa_accuracy", "quality_status", "message"),
    [
        (None, "NUMERICALLY_COMPARABLE", "QA accuracy"),
        (0.25, "INVALID", "quality status"),
        (1.5, "NUMERICALLY_COMPARABLE", "QA accuracy"),
    ],
)
def test_quality_input_requires_explicit_valid_status_and_measurements(
    tmp_path: Path, qa_accuracy: float | None, quality_status: str, message: str
) -> None:
    plan = _plan()
    root = tmp_path / "block"
    _complete(root, plan=plan)

    with pytest.raises(AlignedMetricsError, match=message):
        _quality(
            root,
            plan=plan,
            qa_accuracy=qa_accuracy,
            quality_status=quality_status,
        )


def test_metrics_fail_closed_for_noncomplete_artifacts_or_resealed_quality_binding_drift(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "incomplete"
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    store.append_lifecycle(0, event_type="ARRIVAL", timestamp_ns=100)

    with pytest.raises(AlignedMetricsError, match="complete coverage"):
        _quality(root, plan=plan)

    root = tmp_path / "complete"
    _complete(root, plan=plan)
    quality = _quality(root, plan=plan)
    quality["direct_violations"] = 0

    with pytest.raises(AlignedMetricsError, match="quality.*hash"):
        derive_aligned_block_output(
            root,
            verified_plan=plan,
            block_index=0,
            quality_and_correctness=quality,
        )


def test_metrics_rejects_complete_lifecycle_with_nonpositive_makespan_for_main_table_shape(
    tmp_path: Path,
) -> None:
    plan = _plan()
    root = tmp_path / "zero-span"
    store = AlignedBlockArtifactStore.create(
        root,
        verified_plan=plan,
        block_index=0,
        execution_identity_sha256="b" * 64,
    )
    for sequence in range(3):
        for event_type in ("ARRIVAL", "ENQUEUED", "SERVICE_STARTED", "PUBLICATION_DURABLE"):
            store.append_lifecycle(sequence, event_type=event_type, timestamp_ns=100)
    quality = _quality(root, plan=plan)

    with pytest.raises(AlignedMetricsError, match="makespan"):
        derive_aligned_block_output(
            root,
            verified_plan=plan,
            block_index=0,
            quality_and_correctness=quality,
        )
