"""Offline State-Cut tests for the pinned Graphiti v0.29.3 adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v31 import (
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    StateCutCertification,
)
from paper_eval.membind_v31.graphiti_adapter import (
    MemBindV31GraphitiAdapter,
    MemBindV31GraphitiAdapterError,
)
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


HASHES = tuple(f"{value:064x}" for value in range(1, 12))


@dataclass
class _Episode:
    uuid: str
    name: str
    content: str
    group_id: str
    source: str
    valid_at: int


def _record(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"source-{sequence}",
        group_id="v31-adapter-test",
        reference_time_ns=100 + sequence,
        source_filter="message",
        episode_projection={"body": f"private-{sequence}", "name": f"episode-{sequence}"},
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
        valid_at=record.reference_time_ns,
    )


def _record_for(operator_name: str, *, offset: int) -> CertificationRecord:
    return CertificationRecord.create(
        operator_contract=OperatorContract.create(
            operator_name=operator_name,
            dependency_class=DependencyClass.EVIDENCE_BOUND,
            effect_class=EffectClass.PURE,
        ),
        memory_backend_identity_sha256=HASHES[0],
        adapter_identity_sha256=HASHES[1],
        operator_identity_sha256=f"{20 + offset:064x}",
        code_revision_sha256=HASHES[3],
        prompt_identity_sha256=f"{30 + offset:064x}",
        schema_identity_sha256=HASHES[5],
        config_identity_sha256=HASHES[6],
        allowed_evidence_inputs=("current_source", "evidence_snapshot"),
        allowed_upstream_outputs=("graphiti.extract_nodes",) if offset else (),
        allowed_apis=("llm.generate_response",),
        forbidden_apis=("graph_driver.execute_query", "memory.search", "memory.write"),
        qualification_trace_sha256=f"{40 + offset:064x}",
        persistent_state_read_count=0,
        persistent_state_write_count=0,
        undeclared_external_side_effect_count=0,
        future_evidence_access_count=0,
        undeclared_state_facing_call_count=0,
    )


def _certification(*, edge: bool) -> StateCutCertification:
    records = [_record_for("graphiti.extract_nodes", offset=0)]
    if edge:
        records.append(_record_for("graphiti.extract_edges", offset=1))
    return StateCutCertification.create(records)


def _binding(calls: list[str], observed: dict[str, object]) -> S5GraphitiSemanticBinding:
    async def extract_nodes(clients, episode, previous, *_args):
        calls.append("extract_nodes")
        observed["compile_clients"] = clients
        observed["node_previous"] = list(previous)
        return ([{"name": "Alice", "uuid": "raw-a"}], {"raw-a": [0]})

    async def extract_edges(_clients, _episode, nodes, previous, *_args):
        calls.append("extract_edges")
        observed.setdefault("edge_previous", []).append(list(previous))
        observed.setdefault("edge_nodes", []).append(list(nodes))
        return [
            {
                "uuid": "edge-a",
                "source_node_uuid": "raw-a",
                "target_node_uuid": "raw-a",
                "fact": "Alice knows Alice",
            }
        ]

    async def resolve_nodes(_clients, nodes, _episode, previous, _types):
        calls.append("resolve_nodes")
        observed["resolve_previous"] = list(previous)
        observed["resolve_input"] = list(nodes)
        canonical = {"name": "Alice", "uuid": "canonical-a"}
        return ([canonical, dict(canonical)], {"raw-a": "canonical-a"}, [])

    def resolve_pointers(edges, uuid_map):
        calls.append("resolve_edge_pointers")
        observed["pointer_edges"] = list(edges)
        return [
            {
                **edge,
                "source_node_uuid": uuid_map[edge["source_node_uuid"]],
                "target_node_uuid": uuid_map[edge["target_node_uuid"]],
            }
            for edge in edges
        ]

    async def resolve_edges(_clients, edges, _episode, nodes, *_args):
        calls.append("resolve_edges")
        observed["resolved_edge_inputs"] = list(edges)
        observed["resolved_node_inputs"] = list(nodes)
        return ([{"uuid": "resolved-edge"}], [], [{"uuid": "new-edge"}])

    async def attributes(_clients, nodes, _episode, _previous, _types, *, edges):
        calls.append("attributes")
        observed["attribute_edges"] = list(edges)
        return list(nodes)

    async def process(_graphiti, episode, _nodes, _edges, _logical, _group, _a, _b, node_map):
        calls.append("process")
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


class _CompileMustNotReadGraph:
    @property
    def clients(self):
        raise AssertionError("Compile must not access Graphiti clients")

    @property
    def driver(self):
        raise AssertionError("Compile must not access the graph driver")


def _adapter(*, edge: bool, calls: list[str], observed: dict[str, object], graphiti=None):
    selected_graphiti = graphiti if graphiti is not None else _CompileMustNotReadGraph()
    return MemBindV31GraphitiAdapter(
        graphiti=selected_graphiti,
        llm_client=object(),
        semantic_binding=_binding(calls, observed),
        episode_factory=_episode_factory,
        extracted_node_factory=lambda value: dict(value),
        extracted_edge_factory=lambda value: dict(value),
        state_cut_certification=_certification(edge=edge),
    )


def test_node_and_edge_compile_use_only_arrived_evidence_and_llm_capability() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    adapter = _adapter(edge=True, calls=calls, observed=observed)

    artifact = asyncio.run(adapter.prepare(_compile_input()))

    assert calls == ["extract_nodes", "extract_edges"]
    assert not hasattr(observed["compile_clients"], "driver")
    assert [episode.uuid for episode in observed["node_previous"]] == ["source-0"]
    assert artifact.raw_nodes == [{"name": "Alice", "uuid": "raw-a"}]
    assert artifact.raw_edges[0]["uuid"] == "edge-a"
    assert artifact.pure_intermediates == {
        "node_episode_index_map": {"raw-a": [0]}
    }
    assert artifact.certification_sha256 == adapter.state_cut_certification_sha256


def test_bind_uses_prepared_edges_and_preserves_native_stateful_suffix_order() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}

    class Graphiti:
        def __init__(self) -> None:
            self.clients = SimpleNamespace(llm_client=object(), driver="live-driver")

        async def retrieve_episodes(self, *_args, **_kwargs):
            calls.append("retrieve_latest")
            return ["latest-state"]

    adapter = _adapter(edge=True, calls=calls, observed=observed, graphiti=Graphiti())
    compile_input = _compile_input()
    artifact = asyncio.run(adapter.prepare(compile_input))
    result = asyncio.run(adapter.bind(compile_input, artifact, logical_time_ns=2_000))

    assert calls == [
        "extract_nodes",
        "extract_edges",
        "retrieve_latest",
        "resolve_nodes",
        "resolve_edge_pointers",
        "resolve_edges",
        "attributes",
        "process",
    ]
    assert observed["resolve_previous"] == ["latest-state"]
    assert observed["commit_node_map"] == {"raw-a": [0]}
    assert result.source_sequence == 1
    assert result.resolved_node_count == 1
    assert result.resolved_edge_count == 1


def test_uncertified_edge_extract_stays_inside_version_bound_bind() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}

    class Graphiti:
        def __init__(self) -> None:
            self.clients = SimpleNamespace(llm_client=object(), driver="live-driver")

        async def retrieve_episodes(self, *_args, **_kwargs):
            calls.append("retrieve_latest")
            return ["latest-state"]

    adapter = _adapter(edge=False, calls=calls, observed=observed, graphiti=Graphiti())
    compile_input = _compile_input()
    artifact = asyncio.run(adapter.prepare(compile_input))
    assert artifact.raw_edges is None
    assert calls == ["extract_nodes"]

    asyncio.run(adapter.bind(compile_input, artifact, logical_time_ns=2_000))
    assert calls == [
        "extract_nodes",
        "retrieve_latest",
        "resolve_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_edges",
        "attributes",
        "process",
    ]
    assert observed["edge_previous"][-1] == ["latest-state"]


def test_compile_forbidden_capability_access_fails_closed() -> None:
    calls: list[str] = []
    observed: dict[str, object] = {}
    binding = _binding(calls, observed)

    async def invalid_extract_nodes(clients, *_args):
        _ = clients.driver
        return ([], {})

    invalid = S5GraphitiSemanticBinding(
        extract_nodes=invalid_extract_nodes,
        resolve_extracted_nodes=binding.resolve_extracted_nodes,
        extract_attributes_from_nodes=binding.extract_attributes_from_nodes,
        extract_edges=binding.extract_edges,
        resolve_extracted_edges=binding.resolve_extracted_edges,
        resolve_edge_pointers=binding.resolve_edge_pointers,
        process_episode_data=binding.process_episode_data,
    )
    adapter = MemBindV31GraphitiAdapter(
        graphiti=_CompileMustNotReadGraph(),
        llm_client=object(),
        semantic_binding=invalid,
        episode_factory=_episode_factory,
        extracted_node_factory=lambda value: dict(value),
        extracted_edge_factory=lambda value: dict(value),
        state_cut_certification=_certification(edge=False),
    )

    with pytest.raises(MemBindV31GraphitiAdapterError, match="certified_compile_forbidden_capability"):
        asyncio.run(adapter.prepare(_compile_input()))


def test_adapter_rejects_state_cut_without_required_node_extract() -> None:
    edge_only = StateCutCertification.create(
        [_record_for("graphiti.extract_edges", offset=1)]
    )
    with pytest.raises(MemBindV31GraphitiAdapterError, match="node_extract_not_certified"):
        MemBindV31GraphitiAdapter(
            graphiti=_CompileMustNotReadGraph(),
            llm_client=object(),
            semantic_binding=_binding([], {}),
            episode_factory=_episode_factory,
            extracted_node_factory=lambda value: dict(value),
            extracted_edge_factory=lambda value: dict(value),
            state_cut_certification=edge_only,
        )
