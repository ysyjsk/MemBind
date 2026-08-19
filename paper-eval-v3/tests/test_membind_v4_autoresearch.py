"""TDD checks for the small v4 autoresearch ledger.

These tests cover only durable candidate bookkeeping and deterministic gates;
the live runner is exercised separately against the pinned services.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.membind_v4.autoresearch import (
    CandidateStore,
    assess_candidate,
    candidate_config,
    summarize_events,
)


def test_candidate_config_is_frozen_and_single_factor() -> None:
    config = candidate_config("c01")
    assert config["candidate_id"] == "c01"
    assert config["policy"] == "IDLE_SLOT_VALIDATED_SPEC"
    assert config["global_k"] == 2
    assert config["speculation_distance"] == 1
    with pytest.raises(ValueError, match="candidate_unknown"):
        candidate_config("c04")


def test_candidate_store_writes_append_only_public_artifacts(tmp_path: Path) -> None:
    store = CandidateStore.create(tmp_path, "c01", source_count=6)
    store.event("speculation_launched", source_sequence=1)
    store.event("semantic_hit", source_sequence=1)
    summary = store.finalize(status="PASS")
    assert summary["event_count"] == 2
    assert summary["semantic_hit_count"] == 1
    assert (tmp_path / "candidates" / "c01" / "candidate.json").is_file()
    assert (tmp_path / "candidates" / "c01" / "events.jsonl").read_text().count("\n") == 2
    assert json.loads((tmp_path / "candidates" / "c01" / "summary.json").read_text())["status"] == "PASS"


def test_assess_candidate_stops_without_qualified_node_resolve() -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c01",
            "source_count": 6,
            "event_count": 3,
            "qualified_node_resolve_count": 0,
            "speculation_launch_count": 0,
            "exact_validation_completed_count": 0,
            "semantic_hit_count": 0,
            "overlap_count": 0,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.0,
            "freshness_p95_ratio": 1.0,
        }
    )
    assert decision["decision"] == "STOP_V4_NODE_RESOLVE"


def test_assess_candidate_freezes_only_with_hit_overlap_and_gain() -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c01",
            "source_count": 12,
            "event_count": 10,
            "qualified_node_resolve_count": 3,
            "speculation_launch_count": 3,
            "exact_validation_completed_count": 3,
            "semantic_hit_count": 2,
            "semantic_miss_count": 1,
            "overlap_count": 2,
            "hidden_critical_time_ns": 1,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.03,
            "freshness_p95_ratio": 0.90,
        }
    )
    assert decision["decision"] == "FREEZE"


def test_assess_candidate_accepts_preregistered_makespan_gain() -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c01",
            "source_count": 12,
            "qualified_node_resolve_count": 3,
            "speculation_launch_count": 3,
            "exact_validation_completed_count": 3,
            "semantic_hit_count": 2,
            "semantic_miss_count": 1,
            "overlap_count": 2,
            "hidden_critical_time_ns": 1,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.03,
            "freshness_p95_ratio": 1.01,
            "makespan_ratio": 0.94,
        }
    )
    assert decision["decision"] == "FREEZE"
    assert decision["reason"] == "pre_registered_gain"


@pytest.mark.parametrize(
    ("missing_field", "reason"),
    (
        ("speculation_launch_count", "no_speculation_launched"),
        ("exact_validation_completed_count", "no_exact_validation_completed"),
    ),
)
def test_assess_candidate_stops_when_required_mechanism_evidence_is_zero(
    missing_field: str,
    reason: str,
) -> None:
    summary = {
        "status": "PASS",
        "candidate_id": "c01",
        "source_count": 12,
        "qualified_node_resolve_count": 2,
        "speculation_launch_count": 2,
        "exact_validation_completed_count": 2,
        "semantic_hit_count": 1,
        "semantic_miss_count": 1,
        "overlap_count": 1,
        "hidden_critical_time_ns": 1,
        "direct_violation_count": 0,
        "frontier_p95_service_ratio": 1.0,
        "freshness_p95_ratio": 0.90,
        "makespan_ratio": 1.0,
    }
    summary[missing_field] = 0

    decision = assess_candidate(summary)

    assert decision == {"decision": "STOP_MECHANISM_NOT_TRIGGERED", "reason": reason}


def test_assess_candidate_extends_six_source_gain_instead_of_freezing() -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c01",
            "source_count": 6,
            "qualified_node_resolve_count": 2,
            "speculation_launch_count": 2,
            "exact_validation_completed_count": 2,
            "semantic_hit_count": 1,
            "semantic_miss_count": 1,
            "overlap_count": 1,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.0,
            "freshness_p95_ratio": 0.90,
            "makespan_ratio": 1.0,
        }
    )

    assert decision["decision"] == "EXTEND_TO_12"


@pytest.mark.parametrize("negative_ratio", ("freshness_p95_ratio", "makespan_ratio"))
def test_assess_candidate_does_not_extend_negative_six_source_trend(
    negative_ratio: str,
) -> None:
    summary = {
        "status": "PASS",
        "candidate_id": "c01",
        "source_count": 6,
        "qualified_node_resolve_count": 2,
        "speculation_launch_count": 2,
        "exact_validation_completed_count": 2,
        "semantic_hit_count": 1,
        "semantic_miss_count": 1,
        "overlap_count": 1,
        "direct_violation_count": 0,
        "frontier_p95_service_ratio": 1.0,
        "freshness_p95_ratio": 1.0,
        "makespan_ratio": 1.0,
    }
    summary[negative_ratio] = 1.06

    decision = assess_candidate(summary)

    assert decision == {
        "decision": "STOP_NO_MEASURABLE_GAIN",
        "reason": "negative_prefix_trend",
    }


@pytest.mark.parametrize(
    ("semantic_hit_count", "hidden_time", "reason"),
    (
        (0, 1, "no_semantic_hit"),
        (1, 0, "no_hidden_critical_time"),
    ),
)
def test_assess_candidate_stops_twelve_source_without_semantic_opportunity(
    semantic_hit_count: int,
    hidden_time: int,
    reason: str,
) -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c01",
            "source_count": 12,
            "qualified_node_resolve_count": 2,
            "speculation_launch_count": 2,
            "exact_validation_completed_count": 2,
            "semantic_hit_count": semantic_hit_count,
            "semantic_miss_count": 2 - semantic_hit_count,
            "overlap_count": 1,
            "hidden_critical_time_ns": hidden_time,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.0,
            "freshness_p95_ratio": 0.90,
            "makespan_ratio": 1.0,
        }
    )

    assert decision == {"decision": "STOP_V4_NODE_RESOLVE", "reason": reason}


def test_assess_candidate_never_tunes_after_c03() -> None:
    decision = assess_candidate(
        {
            "status": "PASS",
            "candidate_id": "c03",
            "source_count": 12,
            "qualified_node_resolve_count": 2,
            "speculation_launch_count": 2,
            "exact_validation_completed_count": 2,
            "semantic_hit_count": 1,
            "semantic_miss_count": 1,
            "overlap_count": 1,
            "hidden_critical_time_ns": 1,
            "direct_violation_count": 0,
            "frontier_p95_service_ratio": 1.06,
            "freshness_p95_ratio": 1.0,
            "makespan_ratio": 1.0,
        }
    )

    assert decision["decision"] not in {"TUNE_ONCE", "EXTEND_TO_12"}


def test_summarize_events_is_deterministic() -> None:
    events = [
        {"event_type": "semantic_miss"},
        {"event_type": "speculation_overlap"},
        {"event_type": "semantic_hit"},
    ]
    summary = summarize_events(events)
    assert summary["semantic_hit_count"] == 1
    assert summary["semantic_miss_count"] == 1
    assert summary["overlap_count"] == 1
    assert summary["exact_validation_completed_count"] == 2
