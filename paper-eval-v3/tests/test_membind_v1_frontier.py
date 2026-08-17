"""TDD contract tests for ordered MemBind-v1 durable publication state."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.frontier import (
    MemBindV1FrontierError,
    SourceOrderedFrontier,
    coalesce_compatible_nodes,
)


def _to_prepared(frontier: SourceOrderedFrontier, source_sequence: int) -> None:
    frontier.record_intent(source_sequence)
    frontier.record_prepare_started(source_sequence)
    frontier.record_prepared(source_sequence)


def test_frontier_requires_ordered_durable_publication_and_exposes_frontier() -> None:
    frontier = SourceOrderedFrontier(source_count=2)
    _to_prepared(frontier, 0)
    _to_prepared(frontier, 1)

    with pytest.raises(MemBindV1FrontierError, match="bind_not_at_frontier"):
        frontier.record_bind_started(1)

    frontier.record_bind_started(0)
    frontier.record_commit_returned(0)
    frontier.record_publication_durable(0)
    assert frontier.published_frontier == 0

    frontier.record_bind_started(1)
    frontier.record_commit_returned(1)
    frontier.record_publication_durable(1)
    assert frontier.published_source_sequences == (0, 1)
    assert frontier.is_complete


def test_commit_returned_crash_is_poisoned_and_blocks_in_place_progress() -> None:
    frontier = SourceOrderedFrontier(source_count=2)
    _to_prepared(frontier, 0)
    frontier.record_bind_started(0)
    frontier.record_commit_returned(0)

    frontier.poison_ambiguous_commit(0)

    assert frontier.state_of(0) == "AMBIGUOUS_COMMIT_POISONED"
    with pytest.raises(MemBindV1FrontierError, match="attempt_poisoned"):
        frontier.record_publication_durable(0)


def test_coalescing_allows_identical_uuid_projection_but_rejects_conflict() -> None:
    selected = coalesce_compatible_nodes(
        [
            {"uuid": "canonical-a", "name": "Alice"},
            {"name": "Alice", "uuid": "canonical-a"},
            {"uuid": "canonical-b", "name": "Bob"},
        ]
    )
    assert selected == (
        {"uuid": "canonical-a", "name": "Alice"},
        {"uuid": "canonical-b", "name": "Bob"},
    )

    with pytest.raises(MemBindV1FrontierError, match="conflicting_duplicate_uuid"):
        coalesce_compatible_nodes(
            [
                {"uuid": "canonical-a", "name": "Alice"},
                {"uuid": "canonical-a", "name": "Alicia"},
            ]
        )

