"""Regression checks for the offline within-version MEG oracle.

The test reads only the sealed artifact generated from the existing real
capture.  It does not start Graphiti/Neo4j or invoke a provider.
"""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256


ROOT = (
    Path(__file__).resolve().parents[1]
    / "artifacts/paper_eval/membind_v4/meg_runtime_oracle"
    / "meg-runtime-oracle-20260821-011"
)


def _load(name: str) -> dict[str, object]:
    value = json.loads((ROOT / name).read_text(encoding="utf-8"))
    declared = value.pop("payload_sha256")
    assert declared == payload_sha256(value)
    value["payload_sha256"] = declared
    return value


def test_oracle_is_sealed_and_uses_only_the_certified_capture() -> None:
    oracle = _load("MEG_WITHIN_VERSION_ORACLE.json")
    assert oracle["status"] == "PASS_OFFLINE_WITHIN_VERSION_MEG_ORACLE"
    assert oracle["run_id"] == "membind-v31-opt-w4-meg-runtime-observe-20260821-011"
    assert oracle["history_id"] == "07741c45"
    assert oracle["mode"] == "OBSERVE_ONLY"
    assert oracle["operator_count"] == 390
    assert oracle["production_request_count"] == 236
    assert oracle["task_count"] == 487
    assert oracle["input_capture_payload_sha256"]


def test_resource_model_keeps_k2_as_the_only_certified_shared_gate() -> None:
    oracle = _load("MEG_WITHIN_VERSION_ORACLE.json")
    capacities = oracle["resource_capacities"]
    assert capacities["LLM"]["capacity"] == 2
    assert capacities["DB"]["capacity"] >= 1_000_000
    assert capacities["CPU"]["capacity"] >= 1_000_000
    assert capacities["OPAQUE"]["capacity"] >= 1_000_000
    duration_definition = oracle["service_duration_definition"]
    assert "request_span" in duration_definition["request_span_duration_ns"]
    assert "llm.jsonl" in duration_definition["active_service_duration_ns"]
    resource_classes = {node["resource_class"] for node in oracle["nodes"]}
    assert {"LLM", "DB", "CPU", "OPAQUE"} <= resource_classes


def test_each_source_has_required_latency_work_lower_bound_and_schedule_fields() -> None:
    oracle = _load("MEG_WITHIN_VERSION_ORACLE.json")
    comparison = _load("MEG_PUBLICATION_SCHEDULE_COMPARISON.json")
    assert set(oracle["per_source"]) == {str(index) for index in range(12)}
    assert set(comparison["per_source"]) == {str(index) for index in range(12)}
    assert comparison["policies"] == ["CACHE_AFFINE", "FIFO", "PUBLICATION_CRITICALITY_FIRST"]
    for source, row in oracle["per_source"].items():
        assert row["observed_publication_latency_ns"] > 0
        assert row["dependency_critical_path_ns"] > 0
        assert row["llm_total_work_ns"] >= 0
        assert row["llm_k2_resource_lower_bound_ns"] >= 0
        assert row["combined_lower_bound_ns"] >= row["llm_k2_resource_lower_bound_ns"]
        for policy in comparison["policies"]:
            scheduled = comparison["per_source"][source][policy]
            assert "publication_latency_ns" in scheduled
            assert "absolute_gap_vs_observed_ns" in scheduled
            assert "relative_gap_vs_observed" in scheduled


def test_choice_sets_are_real_operator_ready_sets_not_state_width_or_queue_depth() -> None:
    choice = _load("MEG_LLM_ADMISSION_CHOICE_SET.json")
    assert choice["choice_set_count"] == 129
    assert choice["choice_affecting_count"] == 128
    assert choice["global_choice_set_count"] >= choice["choice_set_count"]
    assert choice["global_choice_affecting_count"] >= choice["choice_affecting_count"]
    assert "OPERATOR_READY" in choice["definition"]
    assert "STATE ready width" in choice["definition"]
    assert "Queue depth" in choice["definition"]
    assert "active request count" in choice["definition"]
    for decision in choice["decisions"]:
        assert decision["within_version_candidate_count"] <= decision["candidate_count"]
        assert decision["choice_set_ge_2"] == (decision["within_version_candidate_count"] >= 2)


def test_inversion_and_decision_gate_are_backend_dominated() -> None:
    inversion = _load("MEG_PUBLICATION_CRITICALITY_INVERSION.json")
    decision = _load("MEG_WITHIN_VERSION_DECISION.json")
    assert inversion["inversion_count"] == 116
    assert inversion["duration_stats_ns"]["sum"] > 0
    assert inversion["involved_service_stats_ns"]["sum"] > 0
    assert inversion["penalty_stats_ns"]["sum"] > 0
    assert decision["decision"] == "STOP_LLM_ADMISSION_NOT_CAUSAL"
    headroom = decision["headroom"]
    assert headroom["theoretical_headroom_ns"] > headroom["admission_controllable_headroom_ns"]
    assert headroom["backend_or_uncontrollable_headroom_ns"] > headroom["admission_controllable_headroom_ns"]
    assert headroom["cache_locality_vs_publication_criticality_conflict_count"] == inversion["inversion_count"]
    assert "scheduler implementation" in decision["prohibited_next_actions"]


def test_nodes_retain_dependencies_lineage_and_same_version_publication_relation() -> None:
    oracle = _load("MEG_WITHIN_VERSION_ORACLE.json")
    nodes = oracle["nodes"]
    assert len(nodes) == oracle["task_count"]
    assert all("direct_dependency_task_ids" in node for node in nodes)
    assert all("descendant_publication_source_sequences" in node for node in nodes)
    llm_nodes = [node for node in nodes if node["resource_class"] == "LLM"]
    assert len(llm_nodes) == oracle["production_request_count"]
    assert all(node["production_request_lineage"] for node in llm_nodes)
    # Criticality is within-version: a source-8 node must not list source-9
    # publication merely because the global exact publication chain exists.
    for node in nodes:
        assert set(node["descendant_publication_source_sequences"]) <= {node["source_sequence"]}
