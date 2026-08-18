"""TDD contract for the isolated NodeResolve speculation probe.

These tests exercise only pure replay logic.  They do not construct Graphiti,
open Neo4j, or contact an LLM service.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from paper_eval.membind_v31.node_resolve_speculation import (
    NodeResolveSpeculationError,
    SemanticCall,
    analyze_replay,
    audit_graphiti_node_resolve_source,
    audit_trace_fields,
    evaluate_replay_effectiveness,
    validate_speculation,
)


def _call(*, state_version: int = 0, candidate_order: list[str] | None = None) -> SemanticCall:
    order = candidate_order or ["c0", "c1"]
    bindings = {
        "c0": {"candidate_id": "c0", "uuid": "u0", "projection": {"name": "Alice"}},
        "c1": {"candidate_id": "c1", "uuid": "u1", "projection": {"name": "Bob"}},
    }
    return SemanticCall.create(
        source_sequence=3,
        state_version=state_version,
        rendered_request_sha256="a" * 64,
        token_sequence_sha256="b" * 64,
        response_schema={"type": "object", "properties": {"uuid": {"type": "string"}}},
        model_identity={"model": "qwen3-32b-fp8", "revision": "pinned"},
        decoding_identity={"temperature": 0, "top_p": 1, "seed": 7},
        operator_revision="graphiti-0.29.3-node-resolve-v1",
        candidate_order=order,
        candidate_bindings=[bindings[candidate_id] for candidate_id in order],
        extracted_node_mapping={"0": "c0", "1": "c1"},
        binding_context={"entity_types": ["Person"], "previous_episode_limit": 10},
        prompt_tokens=100,
        service_span_ns=1_000,
    )


def test_same_semantic_request_across_versions_is_reusable() -> None:
    stale = _call(state_version=2)
    exact = _call(state_version=3)

    decision = validate_speculation(stale, exact)

    assert decision.decision == "REUSE"
    assert decision.speculative_fingerprint == decision.exact_fingerprint
    assert decision.avoided_exact_service_span_ns == 1_000


def test_candidate_order_or_projection_drift_falls_back_closed() -> None:
    stale = _call(state_version=2)
    exact = _call(state_version=3, candidate_order=["c1", "c0"])

    decision = validate_speculation(stale, exact)

    assert decision.decision == "REEXECUTE"
    assert decision.reason == "SEMANTIC_CALL_FINGERPRINT_MISMATCH"

    drifted_calls = [
        replace(
            exact,
            candidate_bindings=(
                {"candidate_id": "c0", "uuid": "u0", "projection": {"name": "Alicia"}},
                exact.candidate_bindings[1],
            ),
        ),
        replace(exact, extracted_node_mapping={"0": "c1", "1": "c0"}),
        replace(exact, response_schema={"type": "array"}),
        replace(exact, decoding_identity={"temperature": 0, "top_p": 1, "seed": 8}),
    ]
    assert all(validate_speculation(stale, call).decision == "REEXECUTE" for call in drifted_calls)


def test_invalid_pairing_and_tamper_fail_closed() -> None:
    stale = _call(state_version=3)
    exact = _call(state_version=2)
    with pytest.raises(NodeResolveSpeculationError, match="state_order_invalid"):
        validate_speculation(stale, exact)

    document = stale.to_record()
    document["fingerprint"] = "0" * 64
    with pytest.raises(NodeResolveSpeculationError, match="fingerprint_mismatch"):
        SemanticCall.from_record(document)

    binding_mismatch = _call().to_record()
    binding_mismatch["candidate_bindings"][0]["candidate_id"] = "wrong"
    with pytest.raises(NodeResolveSpeculationError, match="candidate_binding_order_mismatch"):
        SemanticCall.from_record(binding_mismatch)


def test_replay_reports_count_and_service_weighted_reuse() -> None:
    records = [
        {"speculative": _call(state_version=0).to_record(), "exact": _call(state_version=1).to_record()},
        {
            "speculative": _call(state_version=1).to_record(),
            "exact": _call(state_version=2, candidate_order=["c1", "c0"]).to_record(),
        },
    ]

    result = analyze_replay(records)

    assert result["eligible_count"] == 2
    assert result["reuse_count"] == 1
    assert result["reexecute_count"] == 1
    assert result["call_weighted_reuse_rate"] == pytest.approx(0.5)
    assert result["service_weighted_reuse_rate"] == pytest.approx(0.5)
    assert result["avoided_exact_service_span_ns"] == 1_000


def test_existing_transport_trace_is_not_claimed_as_node_resolve_evidence(tmp_path: Path) -> None:
    path = tmp_path / "llm.jsonl"
    row = {
        "record": {
            "schema_version": "membind.paper-eval-v3.membind-v31-pilot-llm.v1",
            "row": {
                "event_type": "llm_request_submitted",
                "request_kind": "FRONTIER",
                "source_sequence": 0,
                "token_count": 100,
            },
        }
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = audit_trace_fields([path])

    assert result["verdict"] == "D2_DATA_INSUFFICIENT"
    assert "semantic_call_fingerprint" in result["missing_fields"]
    assert "candidate_order" in result["missing_fields"]


def test_graphiti_source_boundary_supports_materialize_then_validate(tmp_path: Path) -> None:
    source = tmp_path / "node_operations.py"
    source.write_text(
        """
async def _collect_candidate_nodes(clients, nodes, override):
    return await _semantic_candidate_search(clients, nodes)

async def _resolve_with_llm(llm_client, nodes, indexes, state, episode, previous, types):
    response = await llm_client.generate_response(nodes)
    state.uuid_map.update(response)

async def resolve_extracted_nodes(clients, nodes):
    candidates = await _collect_candidate_nodes(clients, nodes, None)
    state = object()
    await _resolve_with_llm(clients.llm_client, nodes, candidates, state, None, None, None)
    return state
""",
        encoding="utf-8",
    )

    result = audit_graphiti_node_resolve_source(source)

    assert result["verdict"] == "NODE_RESOLVE_BOUNDARY_FEASIBLE"
    assert result["candidate_materialization_separate"] is True
    assert result["llm_execution_separate"] is True
    assert result["llm_stage_persistent_effect_free"] is True


def test_graphiti_source_boundary_rejects_persistent_effect_in_llm_stage(tmp_path: Path) -> None:
    source = tmp_path / "node_operations.py"
    source.write_text(
        """
async def _collect_candidate_nodes(clients, nodes, override):
    return []

async def _resolve_with_llm(llm_client, nodes, indexes, state, episode, previous, types):
    await llm_client.generate_response(nodes)
    await state.save()

async def resolve_extracted_nodes(clients, nodes):
    candidates = await _collect_candidate_nodes(clients, nodes, None)
    return await _resolve_with_llm(clients.llm_client, nodes, candidates, object(), None, None, None)
""",
        encoding="utf-8",
    )

    result = audit_graphiti_node_resolve_source(source)

    assert result["verdict"] == "NODE_RESOLVE_BOUNDARY_NOT_FEASIBLE"
    assert result["llm_stage_persistent_effect_free"] is False


def test_effectiveness_requires_parity_and_reports_net_saved_service_work() -> None:
    records = [
        {
            "speculative": replace(_call(state_version=0), service_span_ns=300).to_record(),
            "exact": _call(state_version=1).to_record(),
            "state_parity": True,
            "validation_overhead_ns": 100,
        }
    ]

    result = evaluate_replay_effectiveness(records, overlap_exposed_ns=500)

    assert result["decision"] == "D2_REUSE_POTENTIAL_SUPPORTED"
    assert result["correctness_gate"] == "PASS"
    assert result["service_weighted_reuse_rate"] == pytest.approx(1.0)
    assert result["net_saved_service_work_ns"] == 600
    assert result["overlap_exposed_ns"] == 500


def test_effectiveness_fails_closed_on_semantic_parity_violation() -> None:
    records = [
        {
            "speculative": _call(state_version=0).to_record(),
            "exact": _call(state_version=1).to_record(),
            "state_parity": False,
            "validation_overhead_ns": 0,
        }
    ]

    result = evaluate_replay_effectiveness(records, overlap_exposed_ns=1_000)

    assert result["decision"] == "D2_UNSAFE"
    assert result["correctness_gate"] == "FAIL"
    assert result["net_saved_service_work_ns"] is None


def test_effectiveness_does_not_claim_gain_when_overhead_exceeds_avoided_work() -> None:
    records = [
        {
            "speculative": _call(state_version=0).to_record(),
            "exact": _call(state_version=1).to_record(),
            "state_parity": True,
            "validation_overhead_ns": 2_000,
        }
    ]

    result = evaluate_replay_effectiveness(records, overlap_exposed_ns=0)

    assert result["decision"] == "D2_LOW_REUSE_POTENTIAL"
    assert result["net_saved_service_work_ns"] == -2_000


def test_effectiveness_separates_semantic_gain_from_missing_scheduler_overlap() -> None:
    records = [
        {
            "speculative": replace(_call(state_version=0), service_span_ns=300).to_record(),
            "exact": _call(state_version=1).to_record(),
            "state_parity": True,
            "validation_overhead_ns": 100,
        }
    ]

    result = evaluate_replay_effectiveness(records, overlap_exposed_ns=0)

    assert result["decision"] == "D2_REUSE_POTENTIAL_HIGH_BUT_NO_OVERLAP"
    assert result["correctness_gate"] == "PASS"


def test_effectiveness_requires_explicit_pair_metrics() -> None:
    records = [
        {
            "speculative": _call(state_version=0).to_record(),
            "exact": _call(state_version=1).to_record(),
        }
    ]

    result = evaluate_replay_effectiveness(records, overlap_exposed_ns=0)

    assert result["decision"] == "D2_DATA_INSUFFICIENT"
    assert "state_parity" in result["missing_fields"]
    assert "validation_overhead_ns" in result["missing_fields"]
