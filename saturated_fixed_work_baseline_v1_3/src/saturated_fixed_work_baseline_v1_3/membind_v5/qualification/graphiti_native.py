"""Provider-free qualification through the actual pinned Graphiti.add_episode path."""

from __future__ import annotations

import asyncio
import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable

from graphiti_core.edges import EntityEdge
from graphiti_core.graphiti import Graphiti
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.tracer import NoOpTracer

from ..runtime.core.admission import AdmissionArbiter, CapacityAuthority
from ..runtime.core.binder import NativeBindingScope
from ..runtime.core.provider_admission import FrontierAwareLLMClient, provider_scope
from ..runtime.core.transcript import TranscriptStore


@dataclass(frozen=True, slots=True)
class NativeGraphitiEpisode:
    source_sequence: int
    body: str


class ScriptedGraphitiOracle:
    """Logical provider oracle with the exact response schemas expected by Graphiti."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        prompt_name = kwargs.get("prompt_name")
        if prompt_name == "extract_nodes.extract_message":
            return {
                "extracted_entities": [
                    {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]},
                    {"name": "Bob", "entity_type_id": 0, "episode_indices": [0]},
                ]
            }
        if prompt_name == "extract_edges.edge":
            return {
                "edges": [
                    {
                        "source_entity_name": "Alice",
                        "target_entity_name": "Bob",
                        "relation_type": "KNOWS",
                        "fact": "Alice knows Bob",
                        "episode_indices": [0],
                    }
                ]
            }
        raise AssertionError(f"unexpected scripted Graphiti prompt: {prompt_name}")


class _ScriptedDriver:
    """Enough driver identity for native Graphiti validation; DB paths are patched out."""

    def __init__(self, database: str) -> None:
        from graphiti_core.driver.driver import GraphProvider

        self._database = database
        self.provider = GraphProvider.NEO4J
        self.graph_operations_interface = None

    def clone(self, *, database: str) -> "_ScriptedDriver":
        return _ScriptedDriver(database)


class _ScriptedEmbedder:
    async def create(self, *, input_data: Any) -> list[float]:
        return [0.0]


class _ScriptedCrossEncoder:
    pass


def _build_graphiti(client: Any, *, namespace: str) -> Graphiti:
    graph = Graphiti.__new__(Graphiti)
    graph.driver = _ScriptedDriver(namespace)
    graph.store_raw_episode_content = True
    graph.max_coroutines = 2
    graph.llm_client = client
    graph.embedder = _ScriptedEmbedder()
    graph.cross_encoder = _ScriptedCrossEncoder()
    graph.tracer = NoOpTracer()
    graph.clients = SimpleNamespace(
        driver=graph.driver,
        llm_client=client,
        embedder=graph.embedder,
        cross_encoder=graph.cross_encoder,
        tracer=graph.tracer,
    )
    return graph


@contextmanager
def _native_db_bound_stubs() -> Any:
    """Stub only stateful operators while retaining real extraction functions."""

    import graphiti_core.graphiti as module

    original = (
        module.resolve_extracted_nodes,
        module.resolve_extracted_edges,
        module.extract_attributes_from_nodes,
    )

    async def resolve_nodes(clients: Any, extracted: list[EntityNode], episode: Any, previous: list[Any], entity_types: Any):
        return list(extracted), {node.uuid: node.uuid for node in extracted}, []

    async def resolve_edges(clients: Any, extracted: list[EntityEdge], episode: Any, nodes: list[EntityNode], edge_types: Any, edge_type_map: Any):
        return list(extracted), [], list(extracted)

    async def attributes(clients: Any, nodes: list[EntityNode], episode: Any, previous: list[Any], entity_types: Any, *, edges: list[EntityEdge] | None = None):
        return list(nodes)

    module.resolve_extracted_nodes = resolve_nodes
    module.resolve_extracted_edges = resolve_edges
    module.extract_attributes_from_nodes = attributes
    try:
        yield
    finally:
        module.resolve_extracted_nodes, module.resolve_extracted_edges, module.extract_attributes_from_nodes = original


async def _run_add_episode(
    graph: Graphiti,
    episode: NativeGraphitiEpisode,
    *,
    source_sequence: int,
    replay: bool,
    store: TranscriptStore,
    frontier: dict[str, int],
) -> dict[str, Any]:
    with provider_scope(region="NATIVE" if replay else "PREPARE", source_sequence=source_sequence):
        binding = NativeBindingScope(store, source_sequence=source_sequence) if replay else None
        if binding is None:
            result = await graph.add_episode(
                name=f"episode-{source_sequence}",
                episode_body=episode.body,
                source_description="scripted-native-graphiti",
                reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                source=EpisodeType.message,
                group_id="v5-scripted",
            )
        else:
            with binding:
                result = await graph.add_episode(
                    name=f"episode-{source_sequence}",
                    episode_body=episode.body,
                    source_description="scripted-native-graphiti",
                    reference_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    source=EpisodeType.message,
                    group_id="v5-scripted",
                )
    return {
        "source_sequence": source_sequence,
        "nodes": sorted(node.name for node in result.nodes),
        "edges": sorted((edge.name, edge.fact) for edge in result.edges),
    }


async def run_real_graphiti_serial_equivalence_async(
    episodes: Iterable[NativeGraphitiEpisode],
) -> dict[str, Any]:
    selected = tuple(episodes)
    authority = CapacityAuthority.from_runtime(2, 2)
    frontier = {"value": -1}
    native_oracle = ScriptedGraphitiOracle()
    native_graph = _build_graphiti(native_oracle, namespace="v5-scripted")
    capture_store = TranscriptStore()
    capture_oracle = ScriptedGraphitiOracle()
    capture_arbiter = AdmissionArbiter(authority)
    capture_client = FrontierAwareLLMClient(
        capture_oracle,
        store=capture_store,
        arbiter=capture_arbiter,
        mode="capture",
        durable_frontier=lambda: frontier["value"],
        client_identity={"class": "ScriptedGraphitiOracle", "source_hash": "graphiti-native-fixture"},
    )
    capture_graph = _build_graphiti(capture_client, namespace="v5-scripted")
    replay_oracle = ScriptedGraphitiOracle()
    replay_arbiter = AdmissionArbiter(authority)
    replay_client = FrontierAwareLLMClient(
        replay_oracle,
        store=capture_store,
        arbiter=replay_arbiter,
        mode="replay",
        durable_frontier=lambda: frontier["value"],
        client_identity={"class": "ScriptedGraphitiOracle", "source_hash": "graphiti-native-fixture"},
    )
    replay_graph = _build_graphiti(replay_client, namespace="v5-scripted")

    async def fake_process(self: Graphiti, episode: Any, nodes: list[EntityNode], edges: list[EntityEdge], now: Any, group_id: str, *args: Any, **kwargs: Any):
        return [], episode

    native_graph._process_episode_data = fake_process.__get__(native_graph, Graphiti)
    capture_graph._process_episode_data = fake_process.__get__(capture_graph, Graphiti)
    replay_graph._process_episode_data = fake_process.__get__(replay_graph, Graphiti)
    for graph in (native_graph, capture_graph, replay_graph):
        graph.retrieve_episodes = (lambda *args, **kwargs: asyncio.sleep(0, result=[]))

    with _native_db_bound_stubs():
        native_results = [await _run_add_episode(native_graph, episode, source_sequence=episode.source_sequence, replay=False, store=TranscriptStore(), frontier=frontier) for episode in selected]
        capture_results = [await _run_add_episode(capture_graph, episode, source_sequence=episode.source_sequence, replay=False, store=capture_store, frontier=frontier) for episode in selected]
        replay_results = []
        for episode in selected:
            replay_results.append(await _run_add_episode(replay_graph, episode, source_sequence=episode.source_sequence, replay=True, store=capture_store, frontier=frontier))
    return {
        "status": "PASS" if native_results == replay_results else "FAIL",
        "native_graph": native_results,
        "v5_graph": replay_results,
        "capture_graph": capture_results,
        "provider_calls_native": native_oracle.calls,
        "provider_calls_v5_capture": capture_oracle.calls,
        "provider_calls_v5_replay": replay_oracle.calls,
        "logical_work": capture_store.summary(),
        "admission": capture_client.provider_calls + replay_client.provider_calls,
    }


def run_real_graphiti_serial_equivalence(episodes: Iterable[NativeGraphitiEpisode]) -> dict[str, Any]:
    return asyncio.run(run_real_graphiti_serial_equivalence_async(episodes))

