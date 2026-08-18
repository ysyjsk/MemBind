"""Offline TDD contracts for the frozen MemBind v3.1 Graphiti adapter.

The adapter is dependency injected and must never import Graphiti, contact a
model service, or open Neo4j while these tests run.  The fixtures model the
observable v0.29.3 extraction and captured-transition shapes only.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from paper_eval.membind_v31.adapter import (
    ArrivedEvidence,
    CapturedStateTransition,
    CompileRuntimeGuard,
    GraphitiV31Adapter,
    GraphitiV31AdapterError,
    build_arrival_fenced_input,
    coalesce_compatible_resolved_nodes,
    graphiti_v0293_operator_map,
    verify_captured_transition_parity,
)
from paper_eval.membind_v31.certification import CertificationRecord
from paper_eval.membind_v31.contracts import (
    DependencyClass,
    EffectClass,
)


def _sha(index: int) -> str:
    return f"{index:064x}"


def _certification(operator_name: str, *, trace: int) -> CertificationRecord:
    contract = graphiti_v0293_operator_map()[operator_name]
    return CertificationRecord.create(
        operator_contract=contract,
        memory_backend_identity_sha256=_sha(1),
        adapter_identity_sha256=_sha(2),
        operator_identity_sha256=_sha(trace + 10),
        code_revision_sha256=_sha(3),
        prompt_identity_sha256=_sha(trace + 20),
        schema_identity_sha256=_sha(4),
        config_identity_sha256=_sha(5),
        allowed_evidence_inputs=("current_source", "evidence_snapshot"),
        allowed_upstream_outputs=("raw_nodes",) if "edges" in operator_name else (),
        allowed_apis=("llm.generate_structured",),
        forbidden_apis=(
            "graph_driver.execute_query",
            "memory.search",
            "memory.write",
        ),
        qualification_trace_sha256=_sha(trace),
        persistent_state_read_count=0,
        persistent_state_write_count=0,
        undeclared_external_side_effect_count=0,
        future_evidence_access_count=0,
        undeclared_state_facing_call_count=0,
    )


def _source(
    sequence: int,
    *,
    arrival_time_ns: int,
    payload: dict[str, object] | None = None,
) -> ArrivedEvidence:
    return ArrivedEvidence.create(
        stream_id="history-1",
        source_sequence=sequence,
        arrival_time_ns=arrival_time_ns,
        source_sha256=_sha(sequence + 30),
        payload=payload or {"name": f"episode-{sequence}", "body": "bounded"},
    )


def _compile_input(*, observed_time_ns: int = 20):
    return build_arrival_fenced_input(
        source=_source(2, arrival_time_ns=20),
        evidence_snapshot=(
            _source(0, arrival_time_ns=0),
            _source(1, arrival_time_ns=10),
        ),
        observed_time_ns=observed_time_ns,
    )


def test_graphiti_v0293_operator_map_freezes_node_and_edge_extract_as_compile() -> None:
    operators = graphiti_v0293_operator_map()

    assert tuple(operators) == (
        "graphiti.extract_nodes",
        "graphiti.extract_edges",
        "graphiti.resolve_nodes",
        "graphiti.resolve_edge_pointers",
        "graphiti.resolve_edges",
        "graphiti.attributes_summary",
        "graphiti.temporal_invalidation",
        "graphiti.persistence",
        "graphiti.publish",
    )
    assert operators["graphiti.extract_nodes"].dependency_class is DependencyClass.EVIDENCE_BOUND
    assert operators["graphiti.extract_nodes"].effect_class is EffectClass.PURE
    assert operators["graphiti.extract_edges"].compile_eligible is True
    assert operators["graphiti.resolve_nodes"].dependency_class is DependencyClass.STATE_BOUND
    assert operators["graphiti.persistence"].effect_class is EffectClass.STATE_WRITE
    assert operators["graphiti.publish"].effect_class is EffectClass.PUBLISH


def test_arrival_fence_is_immutable_and_rejects_current_or_future_evidence() -> None:
    source_payload = {"name": "current", "nested": {"value": 1}}
    source = _source(2, arrival_time_ns=20, payload=source_payload)
    fenced = build_arrival_fenced_input(
        source=source,
        evidence_snapshot=(_source(0, arrival_time_ns=0), _source(1, arrival_time_ns=10)),
        observed_time_ns=20,
    )
    source_payload["nested"]["value"] = 99  # type: ignore[index]

    assert fenced.source_payload["nested"]["value"] == 1
    assert [item.source_sequence for item in fenced.evidence_snapshot] == [0, 1]

    with pytest.raises(GraphitiV31AdapterError, match="compile_before_arrival"):
        build_arrival_fenced_input(
            source=source,
            evidence_snapshot=(),
            observed_time_ns=19,
        )
    with pytest.raises(GraphitiV31AdapterError, match="future_evidence_access"):
        build_arrival_fenced_input(
            source=source,
            evidence_snapshot=(_source(3, arrival_time_ns=20),),
            observed_time_ns=20,
        )
    with pytest.raises(GraphitiV31AdapterError, match="future_evidence_access"):
        build_arrival_fenced_input(
            source=source,
            evidence_snapshot=(_source(1, arrival_time_ns=21),),
            observed_time_ns=20,
        )


def test_runtime_guard_fails_closed_on_forbidden_or_undeclared_api() -> None:
    certification = _certification("graphiti.extract_nodes", trace=6)
    guard = CompileRuntimeGuard(certification)

    guard.observe_api("llm.generate_structured")
    with pytest.raises(GraphitiV31AdapterError, match="state_cut_certification_failure"):
        guard.observe_api("memory.search")

    fresh = CompileRuntimeGuard(certification)
    with pytest.raises(GraphitiV31AdapterError, match="state_cut_certification_failure"):
        fresh.observe_api("unknown.side_effect")


def test_adapter_compiles_node_then_edge_from_only_fenced_inputs() -> None:
    calls: list[tuple[str, object]] = []

    async def extract_nodes(compile_input, guard):
        guard.observe_api("llm.generate_structured")
        calls.append(("nodes", compile_input.source_sequence))
        return [
            {"uuid": "raw-a", "name": "Ada", "labels": ["Entity", "Person"]},
            {"uuid": "raw-b", "name": "Engine", "labels": ["Entity"]},
        ]

    async def extract_edges(compile_input, raw_nodes, guard):
        guard.observe_api("llm.generate_structured")
        calls.append(("edges", tuple(node["uuid"] for node in raw_nodes)))
        return [
            {
                "uuid": "edge-a",
                "source_node_uuid": "raw-a",
                "target_node_uuid": "raw-b",
                "fact": "Ada described the Engine",
            }
        ]

    adapter = GraphitiV31Adapter(
        node_certification=_certification("graphiti.extract_nodes", trace=6),
        edge_certification=_certification("graphiti.extract_edges", trace=7),
        extract_nodes=extract_nodes,
        extract_edges=extract_edges,
    )
    result = asyncio.run(adapter.compile(_compile_input()))

    assert calls == [("nodes", 2), ("edges", ("raw-a", "raw-b"))]
    assert result.raw_nodes[0]["name"] == "Ada"
    assert result.raw_edges is not None
    assert result.raw_edges[0]["fact"] == "Ada described the Engine"
    assert result.source_sequence == 2
    assert result.verify() is result


def test_adapter_rejects_wrong_operator_certification_before_callbacks() -> None:
    calls: list[str] = []

    async def never(*_args):
        calls.append("called")
        return []

    wrong_edge = replace(
        _certification("graphiti.extract_nodes", trace=6),
        certification_sha256=_sha(63),
    )
    with pytest.raises(GraphitiV31AdapterError, match="certification_invalid"):
        GraphitiV31Adapter(
            node_certification=_certification("graphiti.extract_nodes", trace=6),
            edge_certification=wrong_edge,
            extract_nodes=never,
            extract_edges=never,
        )
    assert calls == []


def test_adapter_rejects_tampered_arrival_fence_before_callbacks() -> None:
    calls: list[str] = []

    async def never(*_args):
        calls.append("called")
        return []

    adapter = GraphitiV31Adapter(
        node_certification=_certification("graphiti.extract_nodes", trace=6),
        edge_certification=_certification("graphiti.extract_edges", trace=7),
        extract_nodes=never,
        extract_edges=never,
    )
    tampered = replace(_compile_input(), evidence_sha256=_sha(60))

    with pytest.raises(GraphitiV31AdapterError, match="compile_input_invalid"):
        asyncio.run(adapter.compile(tampered))
    assert calls == []


def test_duplicate_runtime_uuid_coalesces_only_for_same_canonical_projection() -> None:
    nodes = coalesce_compatible_resolved_nodes(
        (
            {"uuid": "canonical-a", "name": "Ada", "summary": "mathematician"},
            {"summary": "mathematician", "name": "Ada", "uuid": "canonical-a"},
            {"uuid": "canonical-b", "name": "Engine"},
        )
    )
    assert [node["uuid"] for node in nodes] == ["canonical-a", "canonical-b"]

    with pytest.raises(GraphitiV31AdapterError, match="conflicting_duplicate_uuid"):
        coalesce_compatible_resolved_nodes(
            (
                {"uuid": "canonical-a", "name": "Ada", "summary": "first"},
                {"uuid": "canonical-a", "name": "Ada", "summary": "changed"},
            )
        )


def _transition(
    *,
    resolved_nodes: tuple[dict[str, object], ...] | None = None,
    successor_state: dict[str, object] | None = None,
) -> CapturedStateTransition:
    return CapturedStateTransition.create(
        stream_id="history-1",
        source_sequence=2,
        predecessor_version=1,
        successor_version=2,
        predecessor_state={"nodes": [{"uuid": "old", "name": "Earlier"}]},
        successor_state=successor_state
        or {
            "nodes": [
                {"uuid": "old", "name": "Earlier"},
                {"uuid": "canonical-a", "name": "Ada"},
            ],
            "edges": [{"source": "canonical-a", "target": "old", "fact": "knows"}],
        },
        prepared_artifact_sha256=_sha(62),
        resolved_nodes=resolved_nodes
        or ({"uuid": "canonical-a", "name": "Ada"},),
    )


def test_captured_transition_parity_is_exact_after_compatible_coalescing() -> None:
    serial = _transition()
    candidate = _transition(
        resolved_nodes=(
            {"uuid": "canonical-a", "name": "Ada"},
            {"name": "Ada", "uuid": "canonical-a"},
        )
    )

    evidence = verify_captured_transition_parity(serial, candidate)
    assert evidence == {
        "exact_canonical_state_parity": True,
        "exact_predecessor_state_parity": True,
        "exact_prepared_artifact_parity": True,
        "exact_resolved_node_parity": True,
        "source_sequence": 2,
        "stream_id": "history-1",
        "successor_version": 2,
    }


def test_captured_transition_parity_fails_closed_on_state_or_version_drift() -> None:
    serial = _transition()
    changed_state = _transition(successor_state={"nodes": [], "edges": []})
    with pytest.raises(GraphitiV31AdapterError, match="captured_state_parity_failure"):
        verify_captured_transition_parity(serial, changed_state)

    wrong_version = replace(
        _transition(),
        predecessor_version=0,
        transition_sha256=_sha(61),
    )
    with pytest.raises(GraphitiV31AdapterError, match="captured_transition_invalid"):
        verify_captured_transition_parity(serial, wrong_version)
