"""Focused tests for independent Graphiti post-construction observation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from paper_eval.apc_aligned_correctness import observe_graph_correctness_counts


class _Driver:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_query(self, query: str, *, params: dict[str, object]):
        self.calls += 1
        if "RETURN episode.name AS name" in query:
            return SimpleNamespace(
                records=[
                    {"name": "e0", "group_id": params["namespace"]},
                    {"name": "e1", "group_id": params["namespace"]},
                ]
            )
        return SimpleNamespace(
            records=[
                {
                    "entity_namespace_escape_count": 0,
                    "relation_namespace_escape_count": 0,
                    "endpoint_escape_count": 0,
                    "provenance_dangling_count": 0,
                    "provenance_cross_namespace_count": 0,
                    "valid_invalid_reversal_count": 0,
                }
            ]
        )


class _SharedSourceNameDriver:
    """Expose the false-positive caused by matching names across namespaces."""

    async def execute_query(self, query: str, *, params: dict[str, object]):
        if "RETURN episode.name AS name" in query:
            if "expected_names" in params:
                return SimpleNamespace(
                    records=[
                        {"name": "e0", "group_id": params["namespace"]},
                        {"name": "e1", "group_id": params["namespace"]},
                        {"name": "e0", "group_id": "other-method-namespace"},
                        {"name": "e1", "group_id": "other-method-namespace"},
                    ]
                )
            return SimpleNamespace(
                records=[
                    {"name": "e0", "group_id": params["namespace"]},
                    {"name": "e1", "group_id": params["namespace"]},
                ]
            )
        return SimpleNamespace(
            records=[
                {
                    "entity_namespace_escape_count": 0,
                    "relation_namespace_escape_count": 0,
                    "endpoint_escape_count": 0,
                    "provenance_dangling_count": 0,
                    "provenance_cross_namespace_count": 0,
                    "valid_invalid_reversal_count": 0,
                }
            ]
        )


def test_graph_observer_does_not_treat_canonical_entity_merges_as_duplicates() -> None:
    driver = _Driver()
    counts = asyncio.run(
        observe_graph_correctness_counts(
            driver=driver,
            namespace="namespace",
            expected_episode_names=("e0", "e1"),
        )
    )
    assert driver.calls == 2
    assert all(value == 0 for value in counts.values())
    assert "duplicate_entity_count" not in counts


def test_graph_observer_does_not_attribute_other_methods_same_source_names_as_escapes() -> None:
    counts = asyncio.run(
        observe_graph_correctness_counts(
            driver=_SharedSourceNameDriver(),
            namespace="current-method-namespace",
            expected_episode_names=("e0", "e1"),
        )
    )

    assert counts["lost_episodic_count"] == 0
    assert counts["duplicate_episodic_count"] == 0
    assert counts["unexpected_episodic_count"] == 0
    assert counts["episodic_namespace_escape_count"] == 0
