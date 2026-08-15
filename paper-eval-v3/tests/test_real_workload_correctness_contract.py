"""Offline contract tests for real-workload correctness evaluation.

This lane defines what future U0/A0/P*/M* executions must prove.  It does
not authorize those executions or inspect any result.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.real_workload_correctness_contract import (
    RealWorkloadCorrectnessError,
    build_real_workload_correctness_contract,
    verify_real_workload_correctness_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"

PATHS = {
    "parent_protocol": (
        ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
    ),
    "s4_amendment_document": PROJECT / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md",
    "s4_amendment_artifact": NATIVE / "S4_VALIDATION_BOUNDARY_AMENDMENT.json",
    "current_stage_pointer": PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
    "role_registry": PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
    "dataset_parity": NATIVE / "DATASET_PARITY.json",
    "evaluator_parity": NATIVE / "EVALUATOR_PARITY.json",
    "native_baseline_freeze": NATIVE / "NATIVE_BASELINE_V2_FREEZE.json",
}


def _load(name: str) -> dict:
    return json.loads(PATHS[name].read_text(encoding="utf-8"))


def _build() -> dict:
    return build_real_workload_correctness_contract(
        parent_protocol_file_sha256=sha256_file(PATHS["parent_protocol"]),
        s4_amendment_document_file_sha256=sha256_file(
            PATHS["s4_amendment_document"]
        ),
        s4_amendment_artifact=_load("s4_amendment_artifact"),
        s4_amendment_artifact_file_sha256=sha256_file(
            PATHS["s4_amendment_artifact"]
        ),
        current_stage_pointer=_load("current_stage_pointer"),
        current_stage_pointer_file_sha256=sha256_file(
            PATHS["current_stage_pointer"]
        ),
        role_registry=_load("role_registry"),
        role_registry_file_sha256=sha256_file(PATHS["role_registry"]),
        dataset_parity=_load("dataset_parity"),
        dataset_parity_file_sha256=sha256_file(PATHS["dataset_parity"]),
        evaluator_parity=_load("evaluator_parity"),
        evaluator_parity_file_sha256=sha256_file(PATHS["evaluator_parity"]),
        native_baseline_freeze=_load("native_baseline_freeze"),
        native_baseline_freeze_file_sha256=sha256_file(
            PATHS["native_baseline_freeze"]
        ),
        git_commit="deadbeef",
        run_id="real-workload-correctness-contract-test-001",
    )


def test_contract_requires_real_graphiti_and_exact_direct_invariants() -> None:
    payload = verify_real_workload_correctness_contract(_build())["payload"]

    assert payload["methods"] == ["U0", "A0", "P*", "M*"]
    execution = payload["execution_contract"]
    assert execution["all_methods_execute_real_graphiti"] is True
    assert execution["synthetic_graphiti_substitution_allowed"] is False
    assert execution["per_method_per_history_accounting_required"] is True
    assert execution["result_merge_requires_all_direct_invariants"] is True

    invariants = payload["direct_invariants"]
    assert invariants == {
        "episode_source_coverage": 1.0,
        "lost_episode_or_source_count": 0,
        "duplicate_episode_or_source_count": 0,
        "source_publication_order_violation_count": 0,
        "visibility_publication_violation_count": 0,
        "temporal_provenance_violation_count": 0,
        "scope": "PER_METHOD_PER_HISTORY_AND_AGGREGATE",
        "failure_policy": "FAIL_CLOSED_NON_MERGEABLE",
    }


def test_semantic_oracle_is_preregistered_and_counts_are_only_descriptive() -> None:
    semantic = verify_real_workload_correctness_contract(_build())["payload"][
        "semantic_graph_contract"
    ]

    assert semantic["matching_oracle_status"] == (
        "MUST_FREEZE_BEFORE_RESULT_GENERATION_OR_INSPECTION"
    )
    assert semantic["matching_oracle_identity_required"] is True
    assert semantic["matching_oracle_may_not_be_tuned_after_results"] is True
    assert semantic["required_metrics"] == [
        "NODE_PRECISION",
        "NODE_RECALL",
        "EDGE_PRECISION",
        "EDGE_RECALL",
        "UNMATCHED_NODE_COUNT",
        "UNMATCHED_EDGE_COUNT",
        "TEMPORAL_DIFFERENCE_COUNT",
    ]
    assert semantic["required_oracle_freeze_fields"] == [
        "NODE_CANONICALIZATION",
        "NODE_SIMILARITY",
        "NODE_MATCH_THRESHOLD",
        "NODE_ASSIGNMENT_AND_TIE_BREAK",
        "EDGE_ENDPOINT_AND_TYPE_MATCHING",
        "EDGE_ATTRIBUTE_MATCHING",
        "TEMPORAL_FIELD_COMPARISON",
        "MISSING_AND_EXTRA_ITEM_POLICY",
    ]
    assert semantic["aggregate_graph_counts_are_descriptive_only"] is True
    assert semantic["aggregate_counts_can_establish_parity"] is False


def test_quality_is_paired_per_history_with_frozen_ci_and_margins() -> None:
    quality = verify_real_workload_correctness_contract(_build())["payload"][
        "quality_contract"
    ]

    assert quality["metrics"] == ["EVIDENCE_RECALL_AT_10", "QA_ACCURACY"]
    assert quality["paired_per_history_analysis"] is True
    assert quality["same_history_question_set_across_methods"] is True
    assert quality["confidence_intervals_required"] is True
    assert quality["freeze_timing"] == (
        "BEFORE_RESULT_GENERATION_OR_INSPECTION"
    )
    assert quality["required_preregistered_fields"] == [
        "ESTIMAND_PER_METRIC",
        "PAIRING_UNIT",
        "CONFIDENCE_LEVEL",
        "CI_METHOD",
        "RESAMPLING_UNIT_IF_APPLICABLE",
        "MULTIPLICITY_POLICY",
        "NON_INFERIORITY_MARGIN_PER_METRIC",
        "EQUIVALENCE_MARGIN_PER_METRIC",
        "MISSING_RESULT_POLICY",
    ]
    assert quality["non_inferiority_and_equivalence_are_distinct"] is True
    assert quality["post_result_margin_selection_allowed"] is False


def test_bindings_are_cross_checked_and_legacy_d0_cannot_authorize() -> None:
    payload = verify_real_workload_correctness_contract(_build())["payload"]
    bindings = payload["input_bindings"]

    assert bindings["parent_protocol"]["file_sha256"] == sha256_file(
        PATHS["parent_protocol"]
    )
    assert bindings["s4_amendment_artifact"]["file_sha256"] == sha256_file(
        PATHS["s4_amendment_artifact"]
    )
    assert bindings["s4_amendment_artifact"]["payload_sha256"] == _load(
        "s4_amendment_artifact"
    )["payload_sha256"]
    assert bindings["current_stage_pointer"]["current_stage"] == (
        "S3_CONFIGURATION_FROZEN"
    )
    assert bindings["native_baseline_freeze"]["baseline_id"] == (
        "native-graphiti-u0-reader-v2"
    )
    assert bindings["role_registry"]["roles"] == [
        "DEVELOPMENT_EXPOSED",
        "PILOT",
        "FINAL_PAPER_TEST",
    ]
    assert payload["legacy_d0"] == {
        "authority_inheritance_allowed": False,
        "authority_reuse_allowed": False,
        "result_merge_allowed": False,
        "historical_evidence_may_be_cited_only_with_original_status": True,
    }


def test_authority_allows_only_offline_s5_design() -> None:
    authority = verify_real_workload_correctness_contract(_build())["payload"][
        "authority"
    ]
    assert authority == {
        "offline_s5_design_authorized": True,
        "result_generation_or_inspection_authorized": False,
        "model_call_authorized": False,
        "neo4j_mutation_authorized": False,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["execution_contract"].update(
            all_methods_execute_real_graphiti=False
        ),
        lambda value: value["payload"]["direct_invariants"].update(
            episode_source_coverage=0.99
        ),
        lambda value: value["payload"]["semantic_graph_contract"].update(
            aggregate_counts_can_establish_parity=True
        ),
        lambda value: value["payload"]["quality_contract"].update(
            post_result_margin_selection_allowed=True
        ),
        lambda value: value["payload"]["legacy_d0"].update(
            authority_inheritance_allowed=True
        ),
        lambda value: value["payload"]["authority"].update(
            s5_live_execution_authorized=True
        ),
        lambda value: value["payload"]["input_bindings"][
            "native_baseline_freeze"
        ].update(baseline_id="different-baseline"),
    ],
)
def test_verifier_fails_closed_on_scientific_or_authority_drift(mutate) -> None:
    changed = copy.deepcopy(_build())
    mutate(changed)
    changed["payload_sha256"] = payload_sha256(changed["payload"])
    with pytest.raises(RealWorkloadCorrectnessError):
        verify_real_workload_correctness_contract(changed)


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "amendment",
            lambda value: value["payload"]["authority"].update(
                formal_execution_authorized=True
            ),
        ),
        (
            "current",
            lambda value: value["payload"].update(current_stage="S4_PASSED"),
        ),
        (
            "freeze",
            lambda value: value["payload"]["authority"].update(
                pilot_execution_authorized=True
            ),
        ),
    ],
)
def test_builder_rejects_input_identity_or_authority_drift(name, mutate) -> None:
    amendment = _load("s4_amendment_artifact")
    current = _load("current_stage_pointer")
    freeze = _load("native_baseline_freeze")
    selected = {
        "amendment": amendment,
        "current": current,
        "freeze": freeze,
    }[name]
    mutate(selected)

    kwargs = {
        "parent_protocol_file_sha256": sha256_file(PATHS["parent_protocol"]),
        "s4_amendment_document_file_sha256": sha256_file(
            PATHS["s4_amendment_document"]
        ),
        "s4_amendment_artifact": amendment,
        "s4_amendment_artifact_file_sha256": sha256_file(
            PATHS["s4_amendment_artifact"]
        ),
        "current_stage_pointer": current,
        "current_stage_pointer_file_sha256": sha256_file(
            PATHS["current_stage_pointer"]
        ),
        "role_registry": _load("role_registry"),
        "role_registry_file_sha256": sha256_file(PATHS["role_registry"]),
        "dataset_parity": _load("dataset_parity"),
        "dataset_parity_file_sha256": sha256_file(PATHS["dataset_parity"]),
        "evaluator_parity": _load("evaluator_parity"),
        "evaluator_parity_file_sha256": sha256_file(
            PATHS["evaluator_parity"]
        ),
        "native_baseline_freeze": freeze,
        "native_baseline_freeze_file_sha256": sha256_file(
            PATHS["native_baseline_freeze"]
        ),
        "git_commit": "deadbeef",
        "run_id": "real-workload-correctness-contract-test-001",
    }
    with pytest.raises(RealWorkloadCorrectnessError):
        build_real_workload_correctness_contract(**kwargs)
