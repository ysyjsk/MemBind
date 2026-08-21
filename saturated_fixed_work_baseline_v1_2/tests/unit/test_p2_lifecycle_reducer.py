from __future__ import annotations

import math

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import Availability
from saturated_fixed_work_baseline_v1_2.correctness import CorrectnessClass
from saturated_fixed_work_baseline_v1_2.lifecycle import (
    EpisodeLifecycle,
    Span,
    concurrency_summary,
    episode_durations,
    ordering_summary,
    reduce_block_timing,
    span_exclusive_durations,
)
from saturated_fixed_work_baseline_v1_2.reducer import (
    ReductionError,
    reduce_construction_main_table,
)
from saturated_fixed_work_baseline_v1_2.telemetry import telemetry_attribution


def test_episode_and_block_lifecycle_boundaries_exclude_validation() -> None:
    lifecycle = EpisodeLifecycle(
        source_sequence=0,
        t_submit_ns=0,
        t_task_created_ns=1,
        t_execution_start_ns=3,
        t_caller_return_ns=10,
        t_publication_visible_ns=13,
        t_publication_durable_ns=15,
    )
    assert episode_durations(lifecycle) == {
        "submit_to_start_ns": 3,
        "service_ns": 7,
        "submit_to_return_ns": 10,
        "submit_to_visible_ns": 13,
        "submit_to_durable_ns": 15,
        "caller_return_to_durable_ns": 5,
    }
    timing = reduce_block_timing(
        t0_ns=0,
        t_last_submit_ns=2,
        t_durable_complete_ns=20,
        t_validated_seal_ns=27,
    )
    assert timing == {
        "build_makespan_ns": 20,
        "drain_tail_ns": 18,
        "validation_seal_latency_ns": 7,
    }


def test_interval_union_exclusive_and_active_integral() -> None:
    spans = (
        Span("outer", "phase-a", 0, 10, None),
        Span("child", "phase-b", 2, 5, "outer"),
        Span("overlap", "phase-c", 5, 15, None),
    )
    concurrency = concurrency_summary(spans)
    assert concurrency["inclusive_sum_ns"] == 23
    assert concurrency["interval_union_ns"] == 15
    assert concurrency["active_integral_ns"] == 23
    assert concurrency["active_max"] == 2
    assert concurrency["active_mean"] == pytest.approx(23 / 15)
    assert concurrency["active_k_time_ns"] == {1: 7, 2: 8}
    assert concurrency["overlap_wall_fraction"] == pytest.approx(8 / 15)
    assert span_exclusive_durations(spans) == {
        "outer": 7,
        "child": 3,
        "overlap": 10,
    }


def test_ordering_inversions_kendall_tau_and_displacement() -> None:
    summary = ordering_summary((0, 1, 2, 3), (1, 0, 3, 2))
    assert summary["inversion_count"] == 2
    assert summary["inversion_density"] == pytest.approx(2 / 6)
    assert summary["kendall_tau"] == pytest.approx(1 - 4 / 6)
    assert summary["max_displacement"] == 1
    assert summary["classification"] is CorrectnessClass.ORDERING_OBSERVATION
    assert summary["direct_semantic_violations"] == 0


def test_phase_duration_sum_is_never_used_as_makespan() -> None:
    spans = (
        Span("left", "llm", 0, 10, None),
        Span("right", "embedding", 5, 15, None),
    )
    summary = concurrency_summary(spans)
    assert summary["inclusive_sum_ns"] == 20
    assert summary["interval_union_ns"] == 15
    assert summary["inclusive_sum_ns"] != summary["interval_union_ns"]


def test_process_global_telemetry_requires_exclusive_window() -> None:
    measured = telemetry_attribution(
        idle_before=True,
        idle_after=True,
        no_other_clients=True,
        sampler_complete=True,
    )
    assert measured.availability is Availability.MEASURED
    assert measured.value == 1
    ambiguous = telemetry_attribution(
        idle_before=True,
        idle_after=False,
        no_other_clients=True,
        sampler_complete=True,
    )
    assert ambiguous.availability is Availability.AMBIGUOUS_PROCESS_GLOBAL
    assert ambiguous.value is None


def _block(
    method: str,
    history: str,
    makespan: float,
    llm_tokens: int,
    *,
    canonical_match: bool,
    violations: int = 0,
) -> dict[str, object]:
    return {
        "method": method,
        "history_id": history,
        "valid": True,
        "episode_count": 1,
        "source_tokens": 1_000,
        "build_makespan_s": makespan,
        "llm_input_tokens": llm_tokens,
        "direct_semantic_violations": violations,
        "canonical_exact_match": canonical_match,
    }


def test_construction_main_table_uses_paired_sum_ratio() -> None:
    histories = ("h0", "h1", "h2", "h3")
    rows = [
        _block("B0_NATIVE_SERIAL", history, makespan, 100, canonical_match=True)
        for history, makespan in zip(histories, (10.0, 20.0, 30.0, 40.0), strict=True)
    ] + [
        _block(
            "B1_NAIVE_WHOLE_UPDATE_ASYNC",
            history,
            makespan,
            150,
            canonical_match=history != "h3",
            violations=1 if history == "h2" else 0,
        )
        for history, makespan in zip(histories, (5.0, 10.0, 15.0, 20.0), strict=True)
    ]
    table = reduce_construction_main_table(rows, expected_histories=histories)
    b0, b1 = table
    assert b0["total_build_makespan_s"] == 100.0
    assert b0["speedup_vs_b0"] == 1.0
    assert b0["source_tokens_per_s"] == 40.0
    assert b1["total_build_makespan_s"] == 50.0
    assert b1["speedup_vs_b0"] == 2.0
    assert b1["source_tokens_per_s"] == 80.0
    assert b1["llm_input_token_ratio_vs_b0"] == 1.5
    assert b1["direct_semantic_violations"] == 1
    assert b1["canonical_exact_match_histories"] == "3/4"
    assert all(row["result_scope"] == "development / protocol-qualified / one run per method-history" for row in table)


def test_construction_reducer_rejects_invalid_denominator() -> None:
    histories = ("h0", "h1", "h2", "h3")
    rows = [
        _block("B0_NATIVE_SERIAL", history, 0.0, 100, canonical_match=True)
        for history in histories
    ] + [
        _block("B1_NAIVE_WHOLE_UPDATE_ASYNC", history, 1.0, 100, canonical_match=True)
        for history in histories
    ]
    with pytest.raises(ReductionError, match="B0_MAKESPAN_DENOMINATOR_INVALID"):
        reduce_construction_main_table(rows, expected_histories=histories)


def test_formal_construction_reducer_rejects_source_token_drift() -> None:
    from saturated_fixed_work_baseline_v1_2.dataset import (
        EXPECTED_EPISODE_COUNTS,
        EXPECTED_SOURCE_TOKENS,
    )

    rows = []
    for method in ("B0_NATIVE_SERIAL", "B1_NAIVE_WHOLE_UPDATE_ASYNC"):
        for history in EXPECTED_EPISODE_COUNTS:
            row = _block(method, history, 1.0, 100, canonical_match=True)
            row["episode_count"] = EXPECTED_EPISODE_COUNTS[history]
            row["source_tokens"] = EXPECTED_SOURCE_TOKENS[history]
            rows.append(row)
    rows[-1]["source_tokens"] += 1
    with pytest.raises(ReductionError, match="FORMAL_SOURCE_TOKENS_MISMATCH"):
        reduce_construction_main_table(
            rows, expected_histories=tuple(EXPECTED_EPISODE_COUNTS)
        )
