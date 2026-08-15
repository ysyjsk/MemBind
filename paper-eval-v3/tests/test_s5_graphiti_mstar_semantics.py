"""TDD tests for the pinned Graphiti M* semantic callback sequence."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timezone

import pytest

from paper_eval.s5_graphiti_mstar_semantics import (
    GraphitiEpisodeInput,
    S5GraphitiMStarSemanticError,
    S5GraphitiMStarSemanticRuntime,
    logical_ns_to_datetime,
)
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


def _binding(calls: list[str]) -> S5GraphitiSemanticBinding:
    async def extract_nodes(*args):
        calls.append("extract_nodes")
        return ([{"uuid": "n1"}], {"n1": [0]})

    async def resolve_nodes(*args):
        calls.append("resolve_nodes")
        return ([{"uuid": "canonical-n1"}], {"n1": "canonical-n1"}, [])

    async def attrs(*args, **kwargs):
        calls.append("attrs")
        return [{"uuid": "canonical-n1"}]

    async def extract_edges(*args):
        calls.append("extract_edges")
        return [{"source_node_uuid": "n1", "target_node_uuid": "n1"}]

    async def resolve_edges(*args):
        calls.append("resolve_edges")
        return ([{"edge": "resolved"}], [{"edge": "invalidated"}], [{"edge": "new"}])

    def pointers(*args):
        calls.append("pointers")
        return [{"edge": "pointed"}]

    async def process(*args):
        calls.append("process")
        return {"committed": True}

    return S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=attrs,
        extract_edges=extract_edges,
        resolve_extracted_edges=resolve_edges,
        resolve_edge_pointers=pointers,
        process_episode_data=process,
    )


def _source() -> GraphitiEpisodeInput:
    return GraphitiEpisodeInput(
        episode_node=object(),
        previous_episodes=(object(),),
        group_id="test-group",
        edge_type_map={("Entity", "Entity"): ()},
    )


def test_prepare_then_bind_uses_native_order_and_same_logical_time() -> None:
    calls: list[str] = []
    graphiti = type("GraphitiDouble", (), {"clients": object()})()
    retrieved: list[GraphitiEpisodeInput] = []

    async def retrieve(source):
        retrieved.append(source)
        return [object()]

    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=graphiti,
        binding=_binding(calls),
        latest_state_retriever=retrieve,
    )
    source = _source()
    logical = 2_000_000_123
    prepared = asyncio.run(runtime.prepare(source, logical))
    observation = asyncio.run(runtime.bind(prepared, logical, 0, ()))

    assert calls == [
        "extract_nodes",
        "resolve_nodes",
        "extract_edges",
        "pointers",
        "resolve_edges",
        "attrs",
        "process",
    ]
    assert retrieved == [source]
    assert observation.source_sequence == 0
    assert observation.logical_time_ns == logical
    assert observation.resolved_node_count == 1
    assert observation.resolved_edge_count == 1
    assert observation.invalidated_edge_count == 1
    assert observation.commit_result_type == "dict"


def test_logical_time_conversion_is_utc_and_invalid_values_fail_closed() -> None:
    value = logical_ns_to_datetime(1_000_000_000)
    assert value.tzinfo == timezone.utc
    assert value.timestamp() == 1.0
    with pytest.raises(S5GraphitiMStarSemanticError, match="logical_time"):
        logical_ns_to_datetime(-1)
    with pytest.raises(S5GraphitiMStarSemanticError, match="logical_time"):
        logical_ns_to_datetime(True)


def test_prepare_and_bind_reject_malformed_inputs_without_fallback() -> None:
    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=_binding([]),
        latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(S5GraphitiMStarSemanticError, match="source"):
        asyncio.run(runtime.prepare(object(), 1))
    with pytest.raises(S5GraphitiMStarSemanticError, match="prepared"):
        asyncio.run(runtime.bind(object(), 1, 0, ()))


def test_upstream_failure_is_sanitized_and_does_not_commit() -> None:
    calls: list[str] = []
    binding = _binding(calls)

    async def broken_retrieve(_source):
        raise RuntimeError("private neo4j detail")

    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=binding,
        latest_state_retriever=broken_retrieve,
    )
    prepared = asyncio.run(runtime.prepare(_source(), 1))
    with pytest.raises(S5GraphitiMStarSemanticError, match="retrieval"):
        asyncio.run(runtime.bind(prepared, 1, 0, ()))
    assert "process" not in calls


def test_semantic_runtime_rejects_nonsequence_upstream_shapes() -> None:
    async def bad_retrieve(_source):
        return None

    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=_binding([]),
        latest_state_retriever=bad_retrieve,
    )
    prepared = asyncio.run(runtime.prepare(_source(), 1))
    with pytest.raises(S5GraphitiMStarSemanticError, match="shape"):
        asyncio.run(runtime.bind(prepared, 1, 0, ()))


def test_fx0_controlled_providers_require_and_enter_explicit_scope() -> None:
    providers = object()
    runtime_without_scope = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=_binding([]),
        latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(S5GraphitiMStarSemanticError, match="provider_scope"):
        asyncio.run(runtime_without_scope.prepare(_source(), 1, providers))

    active: list[object] = []
    calls: list[str] = []

    @contextmanager
    def scope(value):
        active.append(value)
        calls.append("scope_enter")
        try:
            yield
        finally:
            calls.append("scope_exit")
            active.pop()

    binding = _binding(calls)
    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=binding,
        latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
        controlled_provider_scope=scope,
    )
    prepared = asyncio.run(runtime.prepare(_source(), 1, providers))
    assert active == []
    assert calls[:2] == [
        "scope_enter",
        "extract_nodes",
    ]
    asyncio.run(runtime.bind(prepared, 1, 0, (), providers))
    assert active == []
    assert calls.count("scope_enter") == 2
    assert calls.count("scope_exit") == 2


def test_compatible_duplicate_resolved_uuid_is_deterministically_coalesced() -> None:
    calls: list[str] = []
    binding = _binding(calls)

    async def resolve_nodes(*args):
        calls.append("resolve_nodes")
        return (
            [{"uuid": "canonical-n1", "name": "Alice"}, {"uuid": "canonical-n1", "name": "Alice"}],
            {"n1": "canonical-n1"},
            [],
        )

    binding = S5GraphitiSemanticBinding(
        extract_nodes=binding.extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=binding.extract_attributes_from_nodes,
        extract_edges=binding.extract_edges,
        resolve_extracted_edges=binding.resolve_extracted_edges,
        resolve_edge_pointers=binding.resolve_edge_pointers,
        process_episode_data=binding.process_episode_data,
    )
    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=binding,
        latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
    )
    prepared = asyncio.run(runtime.prepare(_source(), 1))
    observation = asyncio.run(runtime.bind(prepared, 1, 0, ()))
    assert observation.resolved_node_count == 1


def test_conflicting_duplicate_resolved_uuid_fails_before_attribute_or_commit() -> None:
    calls: list[str] = []
    base = _binding(calls)

    async def resolve_nodes(*args):
        calls.append("resolve_nodes")
        return (
            [{"uuid": "canonical-n1", "name": "Alice"}, {"uuid": "canonical-n1", "name": "Alicia"}],
            {"n1": "canonical-n1"},
            [],
        )

    binding = S5GraphitiSemanticBinding(
        extract_nodes=base.extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=base.extract_attributes_from_nodes,
        extract_edges=base.extract_edges,
        resolve_extracted_edges=base.resolve_extracted_edges,
        resolve_edge_pointers=base.resolve_edge_pointers,
        process_episode_data=base.process_episode_data,
    )
    runtime = S5GraphitiMStarSemanticRuntime(
        graphiti=type("GraphitiDouble", (), {"clients": object()})(),
        binding=binding,
        latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
    )
    prepared = asyncio.run(runtime.prepare(_source(), 1))
    with pytest.raises(S5GraphitiMStarSemanticError, match="conflicting_duplicate"):
        asyncio.run(runtime.bind(prepared, 1, 0, ()))
    assert "attrs" not in calls
    assert "process" not in calls
