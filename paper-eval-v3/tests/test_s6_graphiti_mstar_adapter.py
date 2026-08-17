"""Offline RED/GREEN tests for the variable-size S6 M* source adapter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from paper_eval.s6_graphiti_mstar_adapter import (
    S6GraphitiMStarAdapterError,
    S6MStarLiveSemanticAdapter,
    materialize_s6_mstar_sources,
)


@dataclass
class Episode:
    source_sequence: int
    source_hash: str
    group_id: str


def _episodes(count: int, namespace: str) -> tuple[Episode, ...]:
    return tuple(
        Episode(index, f"{index + 1:064x}", namespace) for index in range(count)
    )


@pytest.mark.parametrize("count", [1, 3, 49])
def test_materializer_accepts_variable_frozen_history_size(count: int) -> None:
    namespace = "pev3-s6-07741c45-mstar-c4-001"
    ticks = iter([1_800_000_000_000_000_000] * count)

    sources = materialize_s6_mstar_sources(
        _episodes(count, namespace),
        namespace=namespace,
        epoch_clock_ns=lambda: next(ticks),
    )

    assert len(sources) == count
    assert [source.source_sequence for source in sources] == list(range(count))
    assert [source.logical_time_ns for source in sources] == [
        1_800_000_000_000_000_000 + index * 1_000 for index in range(count)
    ]


@pytest.mark.parametrize(
    "namespace",
    [
        "pev3-s5-mstar-20260816-001",
        "pev3-s6-07741c45-pstar-c2-001",
        "wrong",
    ],
)
def test_materializer_rejects_non_s6_mstar_namespace(namespace: str) -> None:
    with pytest.raises(S6GraphitiMStarAdapterError, match="namespace_invalid"):
        materialize_s6_mstar_sources(
            _episodes(1, namespace),
            namespace=namespace,
            epoch_clock_ns=lambda: 1_800_000_000_000_000_000,
        )


def test_projection_accepts_s6_namespace_without_weakening_s5_adapter() -> None:
    namespace = "pev3-s6-07741c45-mstar-c1-001"
    adapter = object.__new__(S6MStarLiveSemanticAdapter)
    adapter.graphiti_episode_kwargs = lambda _episode: {
        "name": "episode-0",
        "episode_body": "private body",
        "source_description": "LongMemEval-S haystack session",
        "reference_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source": object(),
        "group_id": namespace,
    }

    class Node:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    adapter.episodic_node_type = Node

    projected = adapter._project_source(
        object(),
        logical_time_ns=1_800_000_000_000_000_000,
        previous_episodes=(),
    )

    assert projected.group_id == namespace
    assert projected.episode_node.group_id == namespace
