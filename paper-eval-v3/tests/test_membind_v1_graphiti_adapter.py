"""Offline contracts for the node-only MemBind-v1 Graphiti adapter.

These tests deliberately use only fakes.  They prove the capability boundary
between evidence-bound compilation and the later latest-state binding path
without importing Graphiti, opening Neo4j, or calling a model service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.graphiti_adapter import (
    MemBindV1GraphitiAdapter,
    MemBindV1GraphitiAdapterError,
    NodeArtifactIdentity,
)
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


H = "a" * 64


@dataclass
class _Episode:
    uuid: str
    name: str
    content: str
    group_id: str
    source: str
    valid_at_ns: int

    @property
    def valid_at(self) -> int:
        return self.valid_at_ns


def _record(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"source-{sequence}",
        group_id="mem-v1-test",
        reference_time_ns=100 + sequence,
        source_filter="message",
        episode_projection={
            "body": f"episode body {sequence}",
            "name": f"episode-{sequence}",
        },
    )


def _compile_input():
    source_log = SourceLog.create([_record(0), _record(1)])
    fence = EvidenceFence.capture(source_log, target_source_sequence=1, last_n=10)
    return build_compile_input(source_log.record(1), fence)


def _episode_factory(record: SourceRecord) -> _Episode:
    projection = record.episode_projection
    return _Episode(
        uuid=record.episode_uuid,
        name=str(projection["name"]),
        content=str(projection["body"]),
        group_id=record.group_id,
        source=record.source_filter,
        valid_at_ns=record.reference_time_ns,
    )


def _identity() -> NodeArtifactIdentity:
    return NodeArtifactIdentity(
        operation_identity_sha256="1" * 64,
        model_identity_sha256="2" * 64,
        prompt_identity_sha256="3" * 64,
        schema_identity_sha256="4" * 64,
        config_identity_sha256="5" * 64,
    )


def _binding(calls: list[str], observed: dict[str, object]) -> S5GraphitiSemanticBinding:
    async def extract_nodes(clients, episode, previous, *_args):
        calls.append("extract_nodes")
        observed["compile_clients"] = clients
        observed["compile_episode"] = episode
        observed["compile_previous"] = list(previous)
        return ([{"name": "Alice", "uuid": "extracted-1"}], {"extracted-1": [0]})

    async def resolve_nodes(_clients, nodes, episode, previous, _types):
        calls.append("resolve_nodes")
        observed["resolve_nodes_input"] = list(nodes)
        observed["resolve_episode"] = episode
        observed["resolve_previous"] = list(previous)
        canonical = {"name": "Alice", "uuid": "canonical-1"}
        return ([canonical, dict(canonical)], {"extracted-1": "canonical-1"}, [])

    async def extract_edges(_clients, episode, nodes, previous, edge_type_map, *_args):
        calls.append("extract_edges")
        observed["edge_episode"] = episode
        observed["edge_nodes"] = list(nodes)
        observed["edge_previous"] = list(previous)
        observed["edge_type_map"] = edge_type_map
        return [{"target_node_uuid": "extracted-1"}]

    def resolve_pointers(edges, uuid_map):
        calls.append("resolve_edge_pointers")
        observed["pointer_uuid_map"] = dict(uuid_map)
        return [{**edge, "target_node_uuid": uuid_map[edge["target_node_uuid"]]} for edge in edges]

    async def resolve_edges(_clients, edges, _episode, nodes, _edge_types, _edge_type_map):
        calls.append("resolve_edges")
        observed["resolved_edge_inputs"] = list(edges)
        observed["resolved_node_inputs"] = list(nodes)
        return ([{"uuid": "resolved-edge"}], [{"uuid": "invalidated-edge"}], [{"uuid": "new-edge"}])

    async def attributes(_clients, nodes, _episode, previous, _types, *, edges):
        calls.append("attributes")
        observed["attribute_nodes"] = list(nodes)
        observed["attribute_previous"] = list(previous)
        observed["attribute_edges"] = list(edges)
        return list(nodes)

    async def process(graphiti, episode, nodes, edges, logical_time, group_id, _a, _b, node_map):
        calls.append("process_episode_data")
        observed["commit_graphiti"] = graphiti
        observed["commit_episode"] = episode
        observed["commit_nodes"] = list(nodes)
        observed["commit_edges"] = list(edges)
        observed["commit_logical_time"] = logical_time
        observed["commit_group_id"] = group_id
        observed["commit_node_map"] = dict(node_map)
        return ([], episode)

    return S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=attributes,
        extract_edges=extract_edges,
        resolve_extracted_edges=resolve_edges,
        resolve_edge_pointers=resolve_pointers,
        process_episode_data=process,
    )


class _PrepareMustNotReadGraph:
    @property
    def driver(self):
        raise AssertionError("prepare must not access a Graphiti driver")

    @property
    def clients(self):
        raise AssertionError("prepare must not access full Graphiti clients")

    async def retrieve_episodes(self, *_args, **_kwargs):
        raise AssertionError("prepare must not call retrieve_episodes")


def test_prepare_uses_only_fence_objects_and_llm_only_clients_proxy() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    llm_client = object()
    adapter = MemBindV1GraphitiAdapter(
        graphiti=_PrepareMustNotReadGraph(),
        llm_client=llm_client,
        semantic_binding=_binding(calls, observed),
        episode_factory=_episode_factory,
        artifact_identity=_identity(),
    )

    artifact = asyncio.run(adapter.prepare(_compile_input()))

    assert calls == ["extract_nodes"]
    clients = observed["compile_clients"]
    assert clients.llm_client is llm_client
    assert not hasattr(clients, "driver")
    assert not hasattr(clients, "embedding_client")
    assert [episode.uuid for episode in observed["compile_previous"]] == ["source-0"]
    assert observed["compile_episode"].uuid == "source-1"
    assert artifact.source_sequence == 1
    assert artifact.extracted_nodes == [{"name": "Alice", "uuid": "extracted-1"}]
    assert artifact.node_episode_index_map == {"extracted-1": [0]}


def test_prepare_persists_pydantic_like_nodes_as_json_not_live_objects() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    llm_client = object()

    class PydanticLikeNode:
        uuid = "extracted-model-node"

        def model_dump(self, *, mode: str):
            assert mode == "json"
            return {"name": "Model Alice", "uuid": self.uuid}

    binding = _binding(calls, observed)

    async def model_extract_nodes(*_args):
        calls.append("extract_nodes")
        return ([PydanticLikeNode()], {"extracted-model-node": [0]})

    binding = S5GraphitiSemanticBinding(
        extract_nodes=model_extract_nodes,
        resolve_extracted_nodes=binding.resolve_extracted_nodes,
        extract_attributes_from_nodes=binding.extract_attributes_from_nodes,
        extract_edges=binding.extract_edges,
        resolve_extracted_edges=binding.resolve_extracted_edges,
        resolve_edge_pointers=binding.resolve_edge_pointers,
        process_episode_data=binding.process_episode_data,
    )
    adapter = MemBindV1GraphitiAdapter(
        graphiti=_PrepareMustNotReadGraph(),
        llm_client=llm_client,
        semantic_binding=binding,
        episode_factory=_episode_factory,
        artifact_identity=_identity(),
    )

    artifact = asyncio.run(adapter.prepare(_compile_input()))

    assert artifact.extracted_nodes == [{"name": "Model Alice", "uuid": "extracted-model-node"}]
    assert isinstance(artifact.extracted_nodes[0], dict)


def test_bind_retrieves_latest_state_only_then_preserves_native_node_only_order() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    llm_client = object()

    class GraphitiDouble:
        def __init__(self) -> None:
            self.clients = SimpleNamespace(llm_client=llm_client, driver="live-driver")
            self.retrievals: list[tuple[object, dict[str, object]]] = []

        async def retrieve_episodes(self, reference_time, **kwargs):
            calls.append("retrieve_latest")
            self.retrievals.append((reference_time, kwargs))
            return ["latest-state-episode"]

    graphiti = GraphitiDouble()
    adapter = MemBindV1GraphitiAdapter(
        graphiti=graphiti,
        llm_client=llm_client,
        semantic_binding=_binding(calls, observed),
        episode_factory=_episode_factory,
        artifact_identity=_identity(),
        edge_types={"REL": object()},
    )
    compile_input = _compile_input()
    artifact = asyncio.run(adapter.prepare(compile_input))
    observation = asyncio.run(adapter.bind(compile_input, artifact, logical_time_ns=2_000))

    assert calls == [
        "extract_nodes",
        "retrieve_latest",
        "resolve_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_edges",
        "attributes",
        "process_episode_data",
    ]
    assert graphiti.retrievals == [
        (
            101,
            {
                "last_n": 10,
                "group_ids": ["mem-v1-test"],
                "source": "message",
            },
        )
    ]
    assert observed["resolve_previous"] == ["latest-state-episode"]
    assert observed["edge_previous"] == ["latest-state-episode"]
    assert observed["edge_nodes"] == [{"name": "Alice", "uuid": "extracted-1"}]
    assert observed["resolved_node_inputs"] == [{"name": "Alice", "uuid": "canonical-1"}]
    assert observed["attribute_nodes"] == [{"name": "Alice", "uuid": "canonical-1"}]
    assert observed["commit_edges"] == [{"uuid": "resolved-edge"}, {"uuid": "invalidated-edge"}]
    assert observation.resolved_node_count == 1
    assert observation.resolved_edge_count == 1
    assert observation.invalidated_edge_count == 1
    assert observation.commit_result_type == "tuple"


def test_bind_fails_closed_before_edge_work_for_conflicting_canonical_duplicates() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    llm_client = object()

    async def conflicting_nodes(*_args):
        calls.append("resolve_nodes")
        return (
            [
                {"name": "Alice", "uuid": "canonical-1"},
                {"name": "Other", "uuid": "canonical-1"},
            ],
            {"extracted-1": "canonical-1"},
            [],
        )

    binding = _binding(calls, observed)
    binding = S5GraphitiSemanticBinding(
        extract_nodes=binding.extract_nodes,
        resolve_extracted_nodes=conflicting_nodes,
        extract_attributes_from_nodes=binding.extract_attributes_from_nodes,
        extract_edges=binding.extract_edges,
        resolve_extracted_edges=binding.resolve_extracted_edges,
        resolve_edge_pointers=binding.resolve_edge_pointers,
        process_episode_data=binding.process_episode_data,
    )
    graphiti = SimpleNamespace(
        clients=SimpleNamespace(llm_client=llm_client),
        retrieve_episodes=lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    adapter = MemBindV1GraphitiAdapter(
        graphiti=graphiti,
        llm_client=llm_client,
        semantic_binding=binding,
        episode_factory=_episode_factory,
        artifact_identity=_identity(),
    )
    compile_input = _compile_input()
    artifact = asyncio.run(adapter.prepare(compile_input))

    with pytest.raises(MemBindV1GraphitiAdapterError, match="conflicting_duplicate_uuid"):
        asyncio.run(adapter.bind(compile_input, artifact, logical_time_ns=2_000))
    assert calls == ["extract_nodes", "resolve_nodes"]


def test_bind_rejects_artifact_that_does_not_match_immutable_compile_input() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    llm_client = object()
    graphiti = SimpleNamespace(
        clients=SimpleNamespace(llm_client=llm_client),
        retrieve_episodes=lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    adapter = MemBindV1GraphitiAdapter(
        graphiti=graphiti,
        llm_client=llm_client,
        semantic_binding=_binding(calls, observed),
        episode_factory=_episode_factory,
        artifact_identity=_identity(),
    )
    compile_input = _compile_input()
    artifact = asyncio.run(adapter.prepare(compile_input))
    other_log = SourceLog.create([_record(0), _record(1), _record(2)])
    other_fence = EvidenceFence.capture(other_log, target_source_sequence=2, last_n=10)
    other_input = build_compile_input(other_log.record(2), other_fence)

    with pytest.raises(MemBindV1GraphitiAdapterError, match="artifact_source_sequence_mismatch"):
        asyncio.run(adapter.bind(other_input, artifact, logical_time_ns=2_000))
    assert calls == ["extract_nodes"]
