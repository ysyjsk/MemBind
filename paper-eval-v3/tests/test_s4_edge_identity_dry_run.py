"""Offline RED/GREEN contracts for the source-7 diagnosis fences."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.s4_edge_identity_dry_run import (
    D2DiagnosticStop,
    D2EvidenceIncomplete,
    D2FenceError,
    D2SideEffectCounters,
    EdgeCandidateBarrier,
    LiveCallSentinel,
    model_client_fence,
    publication_fence,
    read_only_database_fence,
    replace_terminal_inner,
)


class NativeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.session_calls = 0

    async def execute_query(self, cypher_query_: str, **kwargs):
        self.calls.append((cypher_query_, kwargs))
        return ([{"value": 1}], None, None)

    def session(self, *args, **kwargs):
        self.session_calls += 1
        return object()


class Driver:
    def __init__(self) -> None:
        self._init_task = None
        self.client = NativeClient()
        self.session_calls = 0
        self.transaction_calls = 0
        self.schema_calls = 0
        self.delete_index_calls = 0
        self._entity_node_ops = SimpleNamespace(
            save=self._operation_write,
            get_by_uuids=self._operation_read,
        )
        self.operation_write_calls = 0
        self.operation_read_calls = 0

    async def _operation_write(self, *args, **kwargs):
        self.operation_write_calls += 1

    async def _operation_read(self, *args, **kwargs):
        self.operation_read_calls += 1
        return []

    async def execute_query(self, cypher_query_: str, **kwargs):
        return await self.client.execute_query(cypher_query_, **kwargs)

    def session(self, *args, **kwargs):
        self.session_calls += 1
        return self.client.session(*args, **kwargs)

    @asynccontextmanager
    async def transaction(self):
        self.transaction_calls += 1
        yield object()

    async def build_indices_and_constraints(self, *args, **kwargs):
        self.schema_calls += 1

    async def delete_all_indexes(self):
        self.delete_index_calls += 1


@pytest.mark.asyncio
async def test_read_only_database_fence_allows_only_read_routed_query_and_restores() -> None:
    driver = Driver()
    counters = D2SideEffectCounters()
    original_execute = driver.execute_query

    with read_only_database_fence(driver, counters):
        result = await driver.execute_query(
            "MATCH (n) RETURN count(n) AS value", routing_="r"
        )
        forced = await driver.execute_query("MATCH (n) RETURN count(n) AS value")
        shaped = await driver.execute_query(
            "MATCH (n) WHERE n.name = $query RETURN count(n) AS value",
            query="search term",
            routing_="r",
        )
        assert result[0][0]["value"] == 1
        assert forced[0][0]["value"] == 1
        assert shaped[0][0]["value"] == 1
        assert counters.neo4j_read_count == 3
        assert driver.client.calls == [
            ("MATCH (n) RETURN count(n) AS value", {"routing_": "r"}),
            ("MATCH (n) RETURN count(n) AS value", {"routing_": "r"}),
            (
                "MATCH (n) WHERE n.name = $query RETURN count(n) AS value",
                {"query": "search term", "routing_": "r"},
            ),
        ]

    assert driver.execute_query == original_execute
    assert await driver.execute_query("RETURN 1", routing_="r")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "kwargs"),
    [
        ("MATCH (n) SET n.x = 1 RETURN n", {"routing_": "r"}),
        ("MATCH (n) DETACH DELETE n", {"routing_": "r"}),
        ("CREATE INDEX example IF NOT EXISTS FOR (n:X) ON (n.x)", {"routing_": "r"}),
        ("MATCH (n) RETURN n", {"routing_": "w"}),
    ],
)
async def test_read_only_database_fence_rejects_write_or_unrouted_query(
    query: str,
    kwargs: dict,
) -> None:
    driver = Driver()
    counters = D2SideEffectCounters()

    with read_only_database_fence(driver, counters):
        with pytest.raises(D2FenceError):
            await driver.execute_query(query, **kwargs)

    assert driver.client.calls == []
    assert counters.db_write_count == 1


@pytest.mark.asyncio
async def test_read_only_database_fence_rejects_every_bypass_surface() -> None:
    driver = Driver()
    counters = D2SideEffectCounters()

    with read_only_database_fence(driver, counters):
        with pytest.raises(D2FenceError):
            await driver.client.execute_query("MATCH (n) RETURN n", routing_="r")
        with pytest.raises(D2FenceError):
            driver.client.session()
        with pytest.raises(D2FenceError):
            driver.session()
        with pytest.raises(D2FenceError):
            async with driver.transaction():
                pass
        with pytest.raises(D2FenceError):
            await driver.build_indices_and_constraints()
        with pytest.raises(D2FenceError):
            await driver.delete_all_indexes()
        with pytest.raises(D2FenceError):
            await driver._entity_node_ops.save(driver, object())
        assert await driver._entity_node_ops.get_by_uuids(driver, []) == []

    assert counters.db_write_count == 7
    assert driver.client.session_calls == 0
    assert driver.session_calls == 0
    assert driver.transaction_calls == 0
    assert driver.schema_calls == 0
    assert driver.delete_index_calls == 0
    assert driver.operation_write_calls == 0
    assert driver.operation_read_calls == 1


def test_read_only_database_fence_rejects_driver_with_init_task() -> None:
    driver = Driver()
    driver._init_task = object()

    with pytest.raises(D2FenceError, match="schema initialization"):
        with read_only_database_fence(driver, D2SideEffectCounters()):
            pass


@pytest.mark.asyncio
async def test_live_call_sentinel_counts_and_rejects_all_model_surfaces() -> None:
    counters = D2SideEffectCounters()
    sentinel = LiveCallSentinel(counters)

    for call in (
        sentinel.generate_response([]),
        sentinel._generate_response([]),
        sentinel.create("text"),
        sentinel.create_batch(["text"]),
        sentinel.rank("query", ["passage"]),
    ):
        with pytest.raises(D2FenceError, match="live call"):
            await call

    assert counters.network_call_count == 5
    assert counters.live_llm_call_count == 2
    assert counters.live_embedding_call_count == 2
    assert counters.cross_encoder_call_count == 1


def test_replace_terminal_inner_changes_only_leaf_and_restores() -> None:
    leaf = SimpleNamespace(identity="live")
    middle = SimpleNamespace(inner=leaf)
    outer = SimpleNamespace(inner=middle)
    sentinel = SimpleNamespace(identity="sentinel")

    with replace_terminal_inner(outer, sentinel):
        assert outer.inner is middle
        assert middle.inner is sentinel

    assert middle.inner is leaf


@pytest.mark.asyncio
async def test_model_client_fence_preserves_cache_wrappers_and_restores_all_refs() -> None:
    live_llm = SimpleNamespace(kind="live-llm")
    live_embedding = SimpleNamespace(kind="live-embedding")
    llm_cache = SimpleNamespace(inner=live_llm)
    llm_counter = SimpleNamespace(inner=llm_cache)
    embed_cache = SimpleNamespace(inner=live_embedding)
    cross = SimpleNamespace(kind="live-cross")
    node_child = SimpleNamespace(_embedder=embed_cache)
    edge_child = SimpleNamespace(_embedder=embed_cache)
    graph = SimpleNamespace(
        llm_client=llm_counter,
        embedder=embed_cache,
        cross_encoder=cross,
        clients=SimpleNamespace(
            llm_client=llm_counter,
            embedder=embed_cache,
            cross_encoder=cross,
        ),
        nodes=SimpleNamespace(entity=node_child),
        edges=SimpleNamespace(entity=edge_child),
    )
    counters = D2SideEffectCounters()

    with model_client_fence(graph, counters):
        assert graph.llm_client is llm_counter
        assert graph.embedder is embed_cache
        assert isinstance(llm_cache.inner, LiveCallSentinel)
        assert isinstance(embed_cache.inner, LiveCallSentinel)
        assert isinstance(graph.cross_encoder, LiveCallSentinel)
        assert graph.clients.cross_encoder is graph.cross_encoder
        assert node_child._embedder is embed_cache
        assert edge_child._embedder is embed_cache
        with pytest.raises(D2FenceError):
            await llm_cache.inner.generate_response([])

    assert llm_cache.inner is live_llm
    assert embed_cache.inner is live_embedding
    assert graph.cross_encoder is cross
    assert graph.clients.cross_encoder is cross
    assert node_child._embedder is embed_cache
    assert edge_child._embedder is embed_cache


@pytest.mark.asyncio
async def test_publication_fence_rejects_before_original_and_restores() -> None:
    class Graph:
        def __init__(self) -> None:
            self.calls = 0

        async def _process_episode_data(self, *args, **kwargs):
            self.calls += 1
            return "published"

    graph = Graph()
    counters = D2SideEffectCounters()

    with publication_fence(graph, counters):
        with pytest.raises(D2FenceError, match="publication"):
            await graph._process_episode_data("private")

    assert counters.publication_count == 1
    assert graph.calls == 0
    assert await graph._process_episode_data() == "published"
    assert graph.calls == 1


@dataclass
class Edge:
    source_node_uuid: str
    target_node_uuid: str
    fact: str


def _edge(index: int) -> Edge:
    return Edge(f"source-{index}", f"target-{index}", f"fact-{index}")


@pytest.mark.asyncio
async def test_edge_barrier_collects_all_ten_before_every_call_stops() -> None:
    barrier = EdgeCandidateBarrier(expected_call_count=10, timeout_seconds=1.0)

    results = await asyncio.gather(
        *[
            barrier.observe(
                extracted_edge=_edge(index),
                related_edges=[_edge(100 + index)],
                invalidation_edges=[_edge(200 + index)],
            )
            for index in range(10)
        ],
        return_exceptions=True,
    )
    await barrier.wait_until_all_released()

    assert all(isinstance(value, D2DiagnosticStop) for value in results)
    assert barrier.call_count == 10
    assert barrier.released_call_count == 10
    assert len(barrier.records) == 10


@pytest.mark.asyncio
async def test_edge_barrier_fails_closed_for_nine_of_ten_calls() -> None:
    barrier = EdgeCandidateBarrier(expected_call_count=10, timeout_seconds=0.01)

    results = await asyncio.gather(
        *[
            barrier.observe(
                extracted_edge=_edge(index),
                related_edges=[],
                invalidation_edges=[],
            )
            for index in range(9)
        ],
        return_exceptions=True,
    )

    assert all(isinstance(value, D2EvidenceIncomplete) for value in results)
    assert barrier.call_count == 9
    assert barrier.released_call_count == 0


@pytest.mark.asyncio
async def test_edge_barrier_fails_closed_for_duplicate_correlation() -> None:
    barrier = EdgeCandidateBarrier(expected_call_count=2, timeout_seconds=0.1)

    results = await asyncio.gather(
        barrier.observe(
            extracted_edge=_edge(1), related_edges=[], invalidation_edges=[]
        ),
        barrier.observe(
            extracted_edge=_edge(1), related_edges=[], invalidation_edges=[]
        ),
        return_exceptions=True,
    )

    assert all(isinstance(value, D2EvidenceIncomplete) for value in results)
    assert barrier.duplicate_correlation_count == 1


@pytest.mark.asyncio
async def test_edge_barrier_never_calls_original_resolution() -> None:
    calls = 0

    async def forbidden_original(*args, **kwargs):
        nonlocal calls
        calls += 1

    barrier = EdgeCandidateBarrier(expected_call_count=1, timeout_seconds=0.1)
    with pytest.raises(D2DiagnosticStop):
        await barrier.observe(
            extracted_edge=_edge(1),
            related_edges=[],
            invalidation_edges=[],
            original=forbidden_original,
        )

    assert calls == 0
