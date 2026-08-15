"""TDD contract for the additive P* real-workload role amendment.

The amendment resolves only the method-role conflict between the sealed
real-workload hard-invariant contract and the parent protocol's deliberately
unsafe P* baseline.  It cannot authorize execution or rewrite sealed inputs.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.p_star_real_workload_role_amendment import (
    PStarRoleAmendmentError,
    build_p_star_real_workload_role_amendment,
    verify_p_star_real_workload_role_amendment,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
S4_GATE = NATIVE / "S4_REVISED_OFFLINE_GATE.json"
S5_PLAN = NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json"
CURRENT = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"

SOURCE_NAMES = {
    "amendment_source",
    "amendment_test",
    "amendment_document",
    "amendment_finalizer",
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _evidence(*, tests: int, failures: int = 0, errors: int = 0) -> dict:
    return {
        "junit_file_sha256": "e" * 64,
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": 0,
    }


def _kwargs() -> dict:
    return {
        "parent_protocol_file_sha256": sha256_file(PARENT),
        "s4_revised_gate": _load(S4_GATE),
        "s4_revised_gate_file_sha256": sha256_file(S4_GATE),
        "s5_method_plan": _load(S5_PLAN),
        "s5_method_plan_file_sha256": sha256_file(S5_PLAN),
        "current_stage_pointer": _load(CURRENT),
        "current_stage_pointer_file_sha256": sha256_file(CURRENT),
        "source_file_sha256": {name: "a" * 64 for name in SOURCE_NAMES},
        "red_evidence": _evidence(tests=1, errors=1),
        "focused_green_evidence": _evidence(tests=12),
        "git_commit": "deadbeef",
    }


def _build() -> dict:
    return build_p_star_real_workload_role_amendment(**_kwargs())


def test_amendment_is_additive_and_preserves_sealed_inputs() -> None:
    payload = verify_p_star_real_workload_role_amendment(_build())["payload"]
    assert payload["status"] == "ADDITIVE_ROLE_CLARIFICATION_FROZEN"
    assert payload["decision"] == (
        "P_STAR_PERFORMANCE_RECORD_RETENTION_WITH_MANDATORY_VIOLATION_DISCLOSURE"
    )
    assert payload["scope"] == {
        "existing_artifacts_rewritten": False,
        "sealed_s4_gate_preserved": True,
        "sealed_real_workload_contract_preserved": True,
        "supersedes_only": [
            "P_STAR_DIRECT_INVARIANT_ZERO_AS_PERFORMANCE_RECORD_MERGE_GATE",
            "P_STAR_TREATMENT_VIOLATION_AS_INFRASTRUCTURE_FAILURE",
        ],
    }


def test_u0_a0_and_m_star_keep_hard_zero_merge_gate() -> None:
    policy = verify_p_star_real_workload_role_amendment(_build())["payload"]
    hard = policy["hard_zero_merge_gate"]
    assert hard["methods"] == ["U0", "A0", "M*"]
    assert hard["required"] == {
        "episode_source_coverage": 1.0,
        "lost_episode_or_source_count": 0,
        "duplicate_episode_or_source_count": 0,
        "source_publication_order_violation_count": 0,
        "visibility_publication_violation_count": 0,
        "temporal_provenance_violation_count": 0,
    }
    assert hard["violation_effect"] == "RESULT_NON_MERGEABLE_FOR_METHOD"


def test_p_star_requires_complete_accounting_and_telemetry() -> None:
    role = verify_p_star_real_workload_role_amendment(_build())["payload"][
        "p_star_role"
    ]
    assert role["input_accounting_coverage"] == 1.0
    assert role["telemetry_coverage"] == 1.0
    assert role["all_scheduled_sources_require_terminal_classification"] is True
    assert role["required_accounting"] == [
        "SCHEDULED_SOURCE_COUNT",
        "TERMINAL_SOURCE_CLASSIFICATION",
        "PUBLISHED_SOURCE_COUNT",
        "TREATMENT_FAILURE_COUNT",
        "LOST_SOURCE_COUNT",
        "DUPLICATE_SOURCE_COUNT",
        "WORK_VOLUME",
        "RETRY_AND_TRANSACTION_COUNTS",
        "EVENT_AND_CHECKPOINT_INTEGRITY",
    ]


def test_p_star_treatment_violations_are_retained_and_disclosed() -> None:
    treatment = verify_p_star_real_workload_role_amendment(_build())["payload"][
        "p_star_role"
    ]["treatment_induced_violation_policy"]
    assert treatment["performance_record_retained"] is True
    assert treatment["silent_deletion_allowed"] is False
    assert treatment["reclassified_as_infrastructure_failure"] is False
    assert treatment["required_disclosure_metrics"] == [
        "LOST_SOURCE_COUNT",
        "DUPLICATE_SOURCE_COUNT",
        "SOURCE_PUBLICATION_ORDER_VIOLATION_COUNT",
        "VISIBILITY_PUBLICATION_VIOLATION_COUNT",
        "TEMPORAL_PROVENANCE_VIOLATION_COUNT",
        "SEMANTIC_GRAPH_DIFFERENCE_METRICS",
        "TRANSACTION_AND_METHOD_FAILURE_COUNT",
        "DRAIN_OR_CENSORING_STATUS",
    ]


def test_p_star_incomplete_evidence_remains_non_mergeable() -> None:
    p_star = verify_p_star_real_workload_role_amendment(_build())["payload"][
        "p_star_role"
    ]
    assert p_star["evidence_failure_policy"] == {
        "incomplete_accounting_or_telemetry": "NON_MERGEABLE_INFRASTRUCTURE_FAILURE",
        "corrupt_or_unverifiable_artifact": "NON_MERGEABLE_INFRASTRUCTURE_FAILURE",
        "treatment_induced_failure_with_complete_accounting": (
            "RETAIN_AS_SCIENTIFIC_OUTCOME"
        ),
    }


def test_p_star_cannot_claim_semantics_or_quality_equivalence() -> None:
    claims = verify_p_star_real_workload_role_amendment(_build())["payload"][
        "p_star_role"
    ]["claim_boundary"]
    assert claims["performance_baseline_authorized"] is True
    assert claims["semantics_preserving_claim_authorized"] is False
    assert claims["correctness_equivalence_claim_authorized"] is False
    assert claims["quality_equivalence_claim_authorized"] is False
    assert claims["quality_non_inferiority_claim_authorized"] is False
    assert claims["quality_measurements_if_executed"] == "DESCRIPTIVE_WITH_FULL_DISCLOSURE"


def test_all_live_and_result_authority_stays_false() -> None:
    payload = verify_p_star_real_workload_role_amendment(_build())["payload"]
    assert payload["current_stage"] == "S3_CONFIGURATION_FROZEN"
    assert payload["authority"] == {
        "offline_additive_amendment_authorized": True,
        "result_generation_or_inspection_authorized": False,
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
        "neo4j_mutation_authorized": False,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


def test_bindings_cover_parent_s4_s5_and_current_pointer() -> None:
    bindings = verify_p_star_real_workload_role_amendment(_build())["payload"][
        "input_bindings"
    ]
    assert set(bindings) == {
        "parent_protocol",
        "s4_revised_offline_gate",
        "s5_method_qualification_plan",
        "current_stage_pointer",
        "sealed_real_workload_correctness_contract",
    }
    assert bindings["parent_protocol"]["file_sha256"] == sha256_file(PARENT)
    assert bindings["s4_revised_offline_gate"]["file_sha256"] == sha256_file(
        S4_GATE
    )
    assert bindings["s5_method_qualification_plan"]["file_sha256"] == sha256_file(
        S5_PLAN
    )
    assert bindings["current_stage_pointer"]["current_stage"] == (
        "S3_CONFIGURATION_FROZEN"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["hard_zero_merge_gate"]["methods"].append(
            "P*"
        ),
        lambda value: value["payload"]["p_star_role"][
            "treatment_induced_violation_policy"
        ].update(performance_record_retained=False),
        lambda value: value["payload"]["p_star_role"]["claim_boundary"].update(
            semantics_preserving_claim_authorized=True
        ),
        lambda value: value["payload"]["authority"].update(
            s5_live_execution_authorized=True
        ),
    ],
)
def test_verifier_rejects_role_or_authority_drift(mutate) -> None:
    artifact = copy.deepcopy(_build())
    mutate(artifact)
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(PStarRoleAmendmentError):
        verify_p_star_real_workload_role_amendment(artifact)


def test_builder_rejects_cross_artifact_binding_drift() -> None:
    kwargs = _kwargs()
    kwargs["parent_protocol_file_sha256"] = "f" * 64
    with pytest.raises(PStarRoleAmendmentError):
        build_p_star_real_workload_role_amendment(**kwargs)


def test_builder_rejects_missing_source_identity() -> None:
    kwargs = _kwargs()
    kwargs["source_file_sha256"].pop("amendment_document")
    with pytest.raises(PStarRoleAmendmentError):
        build_p_star_real_workload_role_amendment(**kwargs)


def test_verifier_rejects_private_runtime_fields() -> None:
    artifact = copy.deepcopy(_build())
    artifact["payload"]["p_star_role"]["api_key"] = "forbidden"
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(PStarRoleAmendmentError):
        verify_p_star_real_workload_role_amendment(artifact)
