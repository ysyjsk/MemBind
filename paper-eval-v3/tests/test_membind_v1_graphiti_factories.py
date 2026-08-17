"""TDD for source-log hydration and lazy pinned Graphiti node factories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from paper_eval.membind_v1.graphiti_factories import (
    GraphitiFactoryError,
    build_source_log_from_episodes,
    make_graphiti_node_factories,
)


@dataclass(frozen=True)
class _Episode:
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str

    @property
    def name(self) -> str:
        return f"history::episode::{self.source_sequence:04d}"


def _episodes() -> tuple[_Episode, ...]:
    return (
        _Episode(0, "a" * 64, "2026-01-01T00:00:00+00:00", "private one"),
        _Episode(1, "b" * 64, "2026-01-02T00:00:00+00:00", "private two"),
    )


def test_source_log_hydration_uses_raw_episode_hashes_and_a_fresh_namespace() -> None:
    source_log, raw_hashes = build_source_log_from_episodes(
        _episodes(),
        namespace="pev3-aligned-u0-07741c45-a001",
        reference_time_to_ns=lambda value: int(datetime.fromisoformat(value).timestamp() * 1_000_000_000),
    )

    assert raw_hashes == ("a" * 64, "b" * 64)
    assert source_log.source_sequences == (0, 1)
    first = source_log.record(0)
    assert first.group_id == "pev3-aligned-u0-07741c45-a001"
    assert first.source_filter == "message"
    assert first.episode_projection == {
        "body": "private one",
        "name": "history::episode::0000",
        "reference_time": "2026-01-01T00:00:00+00:00",
        "source_description": "LongMemEval-S haystack session",
    }
    assert first.episode_uuid != source_log.record(1).episode_uuid


def test_source_log_hydration_rejects_noncontiguous_or_invalid_raw_source_identity() -> None:
    bad = (_Episode(1, "a" * 64, "2026-01-01T00:00:00+00:00", "private"),)
    with pytest.raises(GraphitiFactoryError, match="source sequence"):
        build_source_log_from_episodes(
            bad,
            namespace="pev3-aligned-u0-07741c45-a001",
            reference_time_to_ns=lambda _value: 1,
        )
    bad_hash = (_Episode(0, "not-a-sha", "2026-01-01T00:00:00+00:00", "private"),)
    with pytest.raises(GraphitiFactoryError, match="raw source identity"):
        build_source_log_from_episodes(
            bad_hash,
            namespace="pev3-aligned-u0-07741c45-a001",
            reference_time_to_ns=lambda _value: 1,
        )


def test_lazy_factories_materialize_expected_episode_and_extracted_entity_shapes() -> None:
    source_log, _ = build_source_log_from_episodes(
        _episodes(),
        namespace="pev3-aligned-mv1-07741c45-a001",
        reference_time_to_ns=lambda value: int(datetime.fromisoformat(value).timestamp() * 1_000_000_000),
    )

    class EpisodicNode:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class EntityNode:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    factories = make_graphiti_node_factories(
        episodic_node_type=EpisodicNode,
        entity_node_type=EntityNode,
        message_source="message-enum",
    )
    episode = factories.episode_factory(source_log.record(0))
    entity = factories.extracted_node_factory(
        {"uuid": "entity-1", "name": "Alice", "group_id": source_log.record(0).group_id}
    )

    assert isinstance(episode.kwargs["uuid"], str)
    assert episode.kwargs["uuid"] == source_log.record(0).episode_uuid
    assert episode.kwargs["name"] == "history::episode::0000"
    assert episode.kwargs["group_id"] == "pev3-aligned-mv1-07741c45-a001"
    assert episode.kwargs["source"] == "message-enum"
    assert episode.kwargs["content"] == "private one"
    assert episode.kwargs["valid_at"] == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert entity.kwargs["name"] == "Alice"
