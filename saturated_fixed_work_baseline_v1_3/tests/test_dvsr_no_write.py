"""Provider-free three-layer speculative no-write proof."""

from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_no_write import build_no_write_proof


def test_three_zero_write_layers_pass() -> None:
    result = build_no_write_proof(
        api_write_count=0,
        shadow_publication_count=0,
        graph_projection_before_digest="a" * 64,
        graph_projection_after_digest="a" * 64,
    )
    assert result["status"] == "PASS"
    assert result["state_projection_equal"] is True


def test_api_write_attempt_fails_even_when_state_is_unchanged() -> None:
    result = build_no_write_proof(
        api_write_count=1,
        shadow_publication_count=0,
        graph_projection_before_digest="a" * 64,
        graph_projection_after_digest="a" * 64,
    )
    assert result["status"] == "FAIL"
    assert "api_write_count_nonzero" in result["reasons"]


def test_shadow_publication_fails_even_when_guard_saw_no_query() -> None:
    result = build_no_write_proof(
        api_write_count=0,
        shadow_publication_count=1,
        graph_projection_before_digest="a" * 64,
        graph_projection_after_digest="a" * 64,
    )
    assert result["status"] == "FAIL"
    assert "shadow_publication_count_nonzero" in result["reasons"]


def test_projection_change_fails_even_without_observed_api_write() -> None:
    result = build_no_write_proof(
        api_write_count=0,
        shadow_publication_count=0,
        graph_projection_before_digest="a" * 64,
        graph_projection_after_digest="b" * 64,
    )
    assert result["status"] == "FAIL"
    assert "canonical_graph_projection_changed" in result["reasons"]


def test_missing_projection_is_unknown_not_pass() -> None:
    result = build_no_write_proof(
        api_write_count=0,
        shadow_publication_count=0,
        graph_projection_before_digest=None,
        graph_projection_after_digest="a" * 64,
    )
    assert result["status"] == "UNKNOWN_INCOMPLETE_EVIDENCE"
