"""Adversarial provider-free contract for nested CUT-D observation."""

from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.deep_prefix import (
    DeepPrefixStatus,
    SUMMARY_OBSERVATION_REQUIRED,
    compare_deep_prefix_observations,
    validate_summary_observation,
)


def _observation() -> dict[str, object]:
    return {
        "read_epoch": "s0",
        "resolved_node_ids": ["n1", "n2"],
        "resolved_node_order": ["n1", "n2"],
        "existing_summaries": {"n1": "a", "n2": "b"},
        "new_edges": [{"uuid": "e1", "source": "n1", "target": "n2"}],
        "new_edge_order": ["e1"],
        "previous_episode_projection": ["ep0"],
        "entity_type_schema": {"Person": ["name", "summary"]},
        "batch_membership": {"summary": ["n1", "n2"]},
        "batch_order": [["n1", "n2"]],
        "canonical_request_digest": "request-v1",
        "hydrated_continuation_digest": "continuation-v1",
    }


def test_complete_identical_observations_are_stable() -> None:
    result = compare_deep_prefix_observations(_observation(), _observation())
    assert result.status is DeepPrefixStatus.STABLE


def test_missing_any_required_field_is_unknown() -> None:
    observation = _observation()
    observation.pop("canonical_request_digest")
    assert "canonical_request_digest" in validate_summary_observation(observation)
    result = compare_deep_prefix_observations(observation, _observation())
    assert result.status is DeepPrefixStatus.UNKNOWN


def test_node_order_change_is_unknown() -> None:
    fresh = _observation()
    fresh["resolved_node_order"] = ["n2", "n1"]
    result = compare_deep_prefix_observations(_observation(), fresh)
    assert result.status is DeepPrefixStatus.UNKNOWN


def test_batch_partition_or_order_change_is_unknown() -> None:
    for field, value in (
        ("batch_membership", {"summary": ["n1"], "summary-2": ["n2"]}),
        ("batch_order", [["n2", "n1"]]),
    ):
        fresh = _observation()
        fresh[field] = value
        result = compare_deep_prefix_observations(_observation(), fresh)
        assert result.status is DeepPrefixStatus.UNKNOWN, field


def test_summary_edge_previous_projection_and_schema_changes_are_unknown() -> None:
    for field in ("existing_summaries", "new_edges", "new_edge_order", "previous_episode_projection", "entity_type_schema"):
        fresh = _observation()
        value = fresh[field]
        if isinstance(value, list):
            fresh[field] = [*value, "changed"]
        elif isinstance(value, dict):
            fresh[field] = {**value, "changed": "value"}
        result = compare_deep_prefix_observations(_observation(), fresh)
        assert result.status is DeepPrefixStatus.UNKNOWN, field


def test_upstream_repair_or_canonical_request_change_is_unknown() -> None:
    for field in ("resolved_node_ids", "canonical_request_digest", "hydrated_continuation_digest"):
        fresh = _observation()
        fresh[field] = "changed"
        result = compare_deep_prefix_observations(_observation(), fresh)
        assert result.status is DeepPrefixStatus.UNKNOWN, field


def test_mixed_snapshot_is_unknown_even_when_payload_matches() -> None:
    fresh = _observation()
    fresh["read_epoch"] = "s1"
    result = compare_deep_prefix_observations(_observation(), fresh)
    assert result.status is DeepPrefixStatus.UNKNOWN


def test_required_schema_lists_all_nested_cut_inputs() -> None:
    assert {
        "resolved_node_order",
        "new_edge_order",
        "batch_membership",
        "batch_order",
        "previous_episode_projection",
        "canonical_request_digest",
    } <= SUMMARY_OBSERVATION_REQUIRED

