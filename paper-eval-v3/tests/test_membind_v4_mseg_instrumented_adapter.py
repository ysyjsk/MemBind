from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.membind_v4.mseg.instrumented_adapter import (
    MSEGInstrumentedAdapter,
    instrument_graphiti_semantic_binding,
)
from paper_eval.membind_v4.mseg.observability import MSEGOperatorTraceObserver
from paper_eval.s5_graphiti_semantic_binding import S5GraphitiSemanticBinding


@dataclass(frozen=True)
class _Source:
    source_sequence: int


@dataclass(frozen=True)
class _CompileInput:
    source: _Source


def _node(uuid: str) -> SimpleNamespace:
    return SimpleNamespace(uuid=uuid)


def _edge(uuid: str, source: str, target: str) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid,
        source_node_uuid=source,
        target_node_uuid=target,
    )


def _binding(calls: list[str]) -> S5GraphitiSemanticBinding:
    async def extract_nodes(*_args, **_kwargs):
        calls.append("extract_nodes")
        return ([_node("raw-node")], {"raw-node": [0]})

    async def resolve_nodes(*_args, **_kwargs):
        calls.append("resolve_extracted_nodes")
        return (
            [_node("canonical-node")],
            {"raw-node": "canonical-node"},
            [(_node("raw-node"), _node("canonical-node"))],
        )

    async def extract_edges(*_args, **_kwargs):
        calls.append("extract_edges")
        return [_edge("raw-edge", "raw-node", "raw-node")]

    def resolve_pointers(*_args, **_kwargs):
        calls.append("resolve_edge_pointers")
        return [_edge("raw-edge", "canonical-node", "canonical-node")]

    async def resolve_edges(*_args, **_kwargs):
        calls.append("resolve_extracted_edges")
        return (
            [_edge("resolved-edge", "canonical-node", "canonical-node")],
            [_edge("expired-edge", "canonical-node", "canonical-node")],
            [_edge("new-edge", "canonical-node", "canonical-node")],
        )

    async def attributes(*_args, **_kwargs):
        calls.append("extract_attributes_from_nodes")
        return [_node("canonical-node")]

    async def process(*_args, **_kwargs):
        calls.append("process_episode_data")
        return (
            [_edge("episodic-edge", "episode-1", "canonical-node")],
            SimpleNamespace(uuid="episode-1"),
        )

    return S5GraphitiSemanticBinding(
        extract_nodes=extract_nodes,
        resolve_extracted_nodes=resolve_nodes,
        extract_attributes_from_nodes=attributes,
        extract_edges=extract_edges,
        resolve_extracted_edges=resolve_edges,
        resolve_edge_pointers=resolve_pointers,
        process_episode_data=process,
    )


class _Adapter:
    def __init__(self, binding: S5GraphitiSemanticBinding) -> None:
        self.binding = binding

    async def prepare(self, _compile_input):
        return await self.binding.extract_nodes(object())

    async def bind(self, _compile_input, _artifact, *, logical_time_ns: int):
        assert logical_time_ns == 123
        node_result = await self.binding.resolve_extracted_nodes(object())
        edge_result = await self.binding.extract_edges(object())
        pointer_result = self.binding.resolve_edge_pointers(object())
        resolved_edge_result = await self.binding.resolve_extracted_edges(object())
        attribute_result = await self.binding.extract_attributes_from_nodes(object())
        process_result = await self.binding.process_episode_data(
            object(),
            SimpleNamespace(uuid="episode-1"),
            [_node("canonical-node")],
            [_edge("resolved-edge", "canonical-node", "canonical-node")],
            object(),
            "namespace",
            None,
            None,
            {"raw-node": [0]},
        )
        return {
            "nodes": node_result,
            "edges": edge_result,
            "pointers": pointer_result,
            "resolved_edges": resolved_edge_result,
            "attributes": attribute_result,
            "process": process_result,
        }


def test_instrumented_binding_preserves_results_and_records_exact_effect_evidence() -> None:
    baseline_calls: list[str] = []
    instrumented_calls: list[str] = []
    compile_input = _CompileInput(source=_Source(source_sequence=1))

    baseline = _Adapter(_binding(baseline_calls))
    observer = MSEGOperatorTraceObserver(clock_ns=iter(range(100, 200)).__next__)
    instrumented = MSEGInstrumentedAdapter(
        inner=_Adapter(
            instrument_graphiti_semantic_binding(_binding(instrumented_calls))
        ),
        stream_id="07741c45",
        observer=observer,
    )

    async def scenario():
        baseline_prepare = await baseline.prepare(compile_input)
        observed_prepare = await instrumented.prepare(compile_input)
        baseline_bind = await baseline.bind(
            compile_input, baseline_prepare, logical_time_ns=123
        )
        observed_bind = await instrumented.bind(
            compile_input, observed_prepare, logical_time_ns=123
        )
        return baseline_prepare, observed_prepare, baseline_bind, observed_bind

    baseline_prepare, observed_prepare, baseline_bind, observed_bind = asyncio.run(
        scenario()
    )
    assert repr(observed_prepare) == repr(baseline_prepare)
    assert repr(observed_bind) == repr(baseline_bind)
    assert instrumented_calls == baseline_calls

    enters = [event for event in observer.events if event["event_type"] == "operator_enter"]
    exits = [event for event in observer.events if event["event_type"] == "operator_exit"]
    effects = [event for event in observer.events if event["event_type"] == "operator_effect"]
    assert len(enters) == len(exits) == len(effects) == 7
    assert {event["operator_role"] for event in enters} == {
        "graphiti.extract_nodes",
        "graphiti.resolve_extracted_nodes",
        "graphiti.extract_edges",
        "graphiti.resolve_edge_pointers",
        "graphiti.resolve_extracted_edges",
        "graphiti.extract_attributes_from_nodes",
        "graphiti.process_episode_data",
    }
    assert all(event["read_scope"] == "NOT_OBSERVABLE" for event in effects)
    process_effect = next(
        event
        for event in effects
        if event["operator_role"] == "graphiti.process_episode_data"
    )
    assert process_effect["persistent_write"] is True
    assert process_effect["effect_scope"]["episode_uuids"] == ["episode-1"]
    assert process_effect["effect_scope"]["node_uuids"] == ["canonical-node"]
    assert "namespace" not in repr(observer.events)


def test_instrumented_operator_error_is_observed_without_repair_or_retry() -> None:
    calls = 0

    async def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("private upstream output")

    no_op = lambda *_args, **_kwargs: None
    binding = S5GraphitiSemanticBinding(
        extract_nodes=fail,
        resolve_extracted_nodes=fail,
        extract_attributes_from_nodes=fail,
        extract_edges=fail,
        resolve_extracted_edges=fail,
        resolve_edge_pointers=no_op,
        process_episode_data=fail,
    )
    observer = MSEGOperatorTraceObserver(clock_ns=iter((1, 2)).__next__)
    adapter = MSEGInstrumentedAdapter(
        inner=_Adapter(instrument_graphiti_semantic_binding(binding)),
        stream_id="07741c45",
        observer=observer,
    )

    with pytest.raises(RuntimeError, match="private upstream output"):
        asyncio.run(adapter.prepare(_CompileInput(source=_Source(0))))
    assert calls == 1
    assert [event["event_type"] for event in observer.events] == [
        "operator_enter",
        "operator_exit",
    ]
    assert observer.events[-1]["operator_status"] == "ERROR"
    assert "private upstream output" not in repr(observer.events)
