"""TDD contracts for the APC-aligned U0/A0/P(C=2) development lane."""

from __future__ import annotations

import asyncio

from paper_eval.apc_aligned_baseline import (
    APC_BASELINE_HISTORIES,
    build_apc_aligned_baseline_plan,
    derive_apc_aligned_performance,
    summarize_direct_violations,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.apc_quality_targets import (
    build_apc_quality_target_manifest,
    verify_apc_quality_target_manifest,
)
from paper_eval.membind_v1.aligned_schedule import (
    A0_ALIGNED,
    AlignedEpisodeRef,
    run_aligned_baseline,
)


def _sources(count: int = 3) -> dict[str, list[str]]:
    return {
        history_id: [f"{history_index + 1:032x}{sequence + 1:032x}" for sequence in range(count)]
        for history_index, history_id in enumerate(APC_BASELINE_HISTORIES)
    }


def _episodes(count: int = 3) -> tuple[AlignedEpisodeRef, ...]:
    return tuple(
        AlignedEpisodeRef(
            source_sequence=sequence,
            source_sha256=f"{sequence + 1:064x}",
            native_episode={"sequence": sequence},
        )
        for sequence in range(count)
    )


def test_plan_freezes_one_relative_arrival_trace_for_all_three_methods() -> None:
    plan = verify_apc_aligned_baseline_plan(
        build_apc_aligned_baseline_plan(
            run_id="apc-baseline-test-001",
            history_source_sha256s=_sources(),
            interarrival_ns=41_811_191_012,
            execution_envelope_sha256="a" * 64,
            service_reference_ns=50_173_429_214,
            normalized_offered_load=1.2,
        )
    )

    assert plan["methods"] == ["U0-aligned", "A0-aligned", "P(C=2)-aligned"]
    assert plan["interarrival_ns"] == 41_811_191_012
    assert len(plan["blocks"]) == 12
    assert {block["arrival_trace_sha256"] for block in plan["blocks"]} == {
        plan["arrival_trace_sha256"]
    }
    for history_id in APC_BASELINE_HISTORIES:
        assert plan["arrival_traces"][history_id]["arrival_offsets_ns"] == [
            0,
            41_811_191_012,
            83_622_382_024,
        ]
    positions = {
        method: [
            block["method_position"]
            for block in plan["blocks"]
            if block["method"] == method
        ]
        for method in plan["methods"]
    }
    assert all(set(value) == {0, 1, 2} for value in positions.values())


def test_a0_is_open_loop_fifo_with_one_worker_and_early_caller_return() -> None:
    async def scenario() -> dict[str, object]:
        release = asyncio.Event()
        first_entered = asyncio.Event()
        order: list[int] = []

        async def native(episode: object) -> None:
            sequence = int(episode["sequence"])  # type: ignore[index]
            order.append(sequence)
            if sequence == 0:
                first_entered.set()
                await release.wait()

        task = asyncio.create_task(
            run_aligned_baseline(
                method=A0_ALIGNED,
                episodes=_episodes(),
                arrival_offsets_ns=(0, 0, 0),
                native_add_episode=native,
            )
        )
        await asyncio.wait_for(first_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        release.set()
        result = await task
        assert order == [0, 1, 2]
        return result

    result = asyncio.run(scenario())
    assert result["configured_worker_count"] == 1
    assert result["observed_max_active_updates"] == 1
    assert result["whole_update_interval_overlap_observed"] is False
    assert all(
        row["caller_return_timestamp_ns"] == row["enqueue_timestamp_ns"]
        and row["caller_return_timestamp_ns"] <= row["publication_timestamp_ns"]
        for row in result["lifecycle"]
    )


def test_performance_uses_arrived_but_unpublished_backlog_and_waiting_depth() -> None:
    rows = [
        {
            "source_sequence": 0,
            "arrival_timestamp_ns": 0,
            "enqueue_timestamp_ns": 1,
            "service_start_timestamp_ns": 10,
            "publication_timestamp_ns": 30,
            "caller_return_timestamp_ns": 1,
        },
        {
            "source_sequence": 1,
            "arrival_timestamp_ns": 5,
            "enqueue_timestamp_ns": 6,
            "service_start_timestamp_ns": 30,
            "publication_timestamp_ns": 50,
            "caller_return_timestamp_ns": 6,
        },
        {
            "source_sequence": 2,
            "arrival_timestamp_ns": 15,
            "enqueue_timestamp_ns": 16,
            "service_start_timestamp_ns": 50,
            "publication_timestamp_ns": 70,
            "caller_return_timestamp_ns": 16,
        },
    ]

    metrics = derive_apc_aligned_performance(rows)

    assert metrics["max_outstanding_backlog"] == 3
    assert metrics["max_waiting_queue_depth"] == 2
    assert metrics["makespan_ns"] == 70
    assert metrics["per_source"][1]["freshness_ns"] == 45
    assert metrics["per_source"][1]["queue_delay_ns"] == 25
    assert metrics["per_source"][1]["post_return_stale_window_ns"] == 44


def test_direct_violation_total_counts_objects_and_keeps_category_count_separate() -> None:
    result = summarize_direct_violations(
        expected_source_count=3,
        publication_source_sequences=(0, 2, 2),
        visibility_by_source={0: True, 1: False, 2: True},
        graph_counts={
            "lost_episodic_count": 1,
            "duplicate_episodic_count": 2,
            "unexpected_episodic_count": 0,
            "episodic_namespace_escape_count": 0,
            "entity_namespace_escape_count": 1,
            "relation_namespace_escape_count": 0,
            "endpoint_escape_count": 0,
            "provenance_dangling_count": 2,
            "provenance_cross_namespace_count": 0,
            "valid_invalid_reversal_count": 1,
        },
    )

    assert result["checker_status"] == "MEASURED"
    assert result["counts"]["lost_or_missing_source_count"] == 2
    assert result["counts"]["duplicate_source_or_publication_count"] == 3
    assert result["counts"]["source_publication_order_violation_count"] == 1
    assert result["counts"]["visibility_publication_violation_count"] == 1
    assert result["counts"]["temporal_provenance_hard_violation_count"] == 4
    assert result["direct_violations_total"] == 11
    assert result["violated_category_count"] == 5


def test_quality_target_manifest_reorders_history_major_blocks_to_method_major() -> None:
    rows = []
    for history in APC_BASELINE_HISTORIES:
        for method in ("P(C=2)-aligned", "U0-aligned", "A0-aligned"):
            rows.append(
                {
                    "status": "PASS",
                    "method": method,
                    "history_id": history,
                    "namespace": f"ns-{method}-{history}",
                    "episode_count": 47,
                    "payload_sha256": f"{len(rows) + 1:064x}",
                }
            )
    manifest = verify_apc_quality_target_manifest(
        build_apc_quality_target_manifest(
            run_id="apc-baseline-quality-test-001", block_results=rows
        )
    )
    assert [value["method"] for value in manifest["targets"][:4]] == ["U0"] * 4
    assert [value["method"] for value in manifest["targets"][4:8]] == ["A0"] * 4
