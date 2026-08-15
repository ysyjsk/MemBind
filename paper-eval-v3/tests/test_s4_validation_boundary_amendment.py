"""TDD contract for retiring candidate-level D0 as the S4 boundary.

The amendment preserves retry-008 exactly as observed.  It authorizes only
offline design of the replacement validation lanes; model calls, Neo4j
mutation, S5 live work, PILOT, and formal evaluation remain closed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import finalize_envelope, sha256_file
from paper_eval.s4_validation_boundary_amendment import (
    AMENDMENT_RUN_ID,
    build_s4_validation_boundary_amendment,
    verify_s4_validation_boundary_amendment,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
CAPTURE = NATIVE / "runs/s4-d0-capture-20260815-008"
REPLAY = NATIVE / "runs/s4-d0-replay-20260815-008"
LOG = PROJECT / "logs/S4_D0_SIDECAR_SMOKE_20260815_008.log"
AMENDMENT = PROJECT / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md"
EXECUTION_PLAN = PROJECT / "EXECUTION_PLAN.md"
PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
LEGACY_WORKPLAN = PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"
CURRENT_STAGE = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
FINAL_SMOKE = NATIVE / "S4_D0_SIDECAR_SMOKE_RESULT_RETRY_008.json"
ACTIVATION_V3 = NATIVE / "S4_D0_QUALIFICATION_ACTIVATION_SIDECAR_V3.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_sha256() -> dict[str, str]:
    return {
        "amendment_source": sha256_file(
            PROJECT / "src/paper_eval/s4_validation_boundary_amendment.py"
        ),
        "amendment_test": sha256_file(Path(__file__)),
        "finalizer": sha256_file(
            PROJECT / "scripts/finalize_s4_validation_boundary_amendment.py"
        ),
    }


def _build() -> dict:
    return build_s4_validation_boundary_amendment(
        capture_result=_load(CAPTURE / "phase_result.json"),
        capture_result_file_sha256=sha256_file(CAPTURE / "phase_result.json"),
        capture_checkpoint=_load(CAPTURE / "checkpoint.json"),
        capture_checkpoint_file_sha256=sha256_file(CAPTURE / "checkpoint.json"),
        capture_events_file_sha256=sha256_file(CAPTURE / "events.jsonl"),
        replay_result=_load(REPLAY / "phase_result.json"),
        replay_result_file_sha256=sha256_file(REPLAY / "phase_result.json"),
        replay_checkpoint=_load(REPLAY / "checkpoint.json"),
        replay_checkpoint_file_sha256=sha256_file(REPLAY / "checkpoint.json"),
        replay_events_file_sha256=sha256_file(REPLAY / "events.jsonl"),
        execution_log=LOG.read_text(encoding="utf-8"),
        execution_log_file_sha256=sha256_file(LOG),
        final_smoke_result_exists=FINAL_SMOKE.exists(),
        activation_v3_exists=ACTIVATION_V3.exists(),
        parent_protocol_sha256=sha256_file(PARENT),
        legacy_workplan_sha256=sha256_file(LEGACY_WORKPLAN),
        current_stage_pointer_sha256=sha256_file(CURRENT_STAGE),
        amendment_document_sha256=sha256_file(AMENDMENT),
        offline_evidence={
            "red_junit_sha256": "a" * 64,
            "red_failure_or_error_count": 1,
            "focused_green_junit_sha256": "b" * 64,
            "focused_green_pass_count": 9,
            "full_green_junit_sha256": "c" * 64,
            "full_green_pass_count": 855,
        },
        source_sha256=_source_sha256(),
        git_commit="deadbeef",
    )


def test_retry_008_is_preserved_and_old_d0_is_retired_without_becoming_pass() -> None:
    artifact = verify_s4_validation_boundary_amendment(_build())
    payload = artifact["payload"]

    assert artifact["run_id"] == AMENDMENT_RUN_ID
    assert payload["decision"] == (
        "FULL_INTERNAL_D0_REPLAY_RETIRED_AS_QUALIFICATION_BOUNDARY"
    )
    assert payload["historical_retry_008"]["capture"] == {
        "run_id": "s4-d0-capture-20260815-008",
        "status": "PASS",
        "mergeable": True,
        "completed_episode_count": 49,
        "expected_episode_count": 49,
        "live_llm_calls": 532,
        "live_embedding_calls": 67,
        "candidate_sidecar_append_count": 178,
        "candidate_sidecar_record_count": 178,
        "candidate_sidecar_rejection_count": 0,
        "unexpected_prompt_count": 0,
        "unexpected_embedding_count": 0,
        "live_fallback_count": 0,
        "canonical_graph_sha256": (
            "ab076234fabef2b94bbd6d8a1815aa4aa8f97f0509086f53675689fe16c24e09"
        ),
        "post_cleanup_node_count": 0,
        "post_cleanup_relationship_count": 0,
        "phase_result_file_sha256": (
            "a11cf71a2e0086fc0783b319d34a59471bc15b103e89525b1fbd6fdd281e8da5"
        ),
        "checkpoint_file_sha256": (
            "5ad6bf25fb5f018484d2d6703ae3cc765303bc558913bb586d23713546c3246b"
        ),
        "events_file_sha256": (
            "4ff12870ae5b2591123aff7fae056518ad394b8198c6821680961f58c868447c"
        ),
    }
    replay = payload["historical_retry_008"]["replay"]
    assert replay["status"] == "INCOMPLETE"
    assert replay["mergeable"] is False
    assert replay["completed_episode_count"] == 7
    assert replay["failed_source_sequence"] == 7
    assert replay["error_class"] == "CandidateSidecarError"
    assert replay["error_code"] == "SIDECAR_CALL_CORRELATION_MISSING"
    assert replay["live_llm_calls"] == 0
    assert replay["live_embedding_calls"] == 0
    assert replay["namespace_cleanup_recorded"] is False
    assert replay["persisted_namespace_state_declared"] is True
    assert replay["live_namespace_state_attested"] is False
    assert replay["preservation_policy"] == "DO_NOT_CLEAN_OR_RESUME"
    assert replay["preserved_node_count"] == 32
    assert replay["preserved_relationship_count"] == 48
    assert replay["phase_result_file_sha256"] == (
        "246fd8224ea27b1855d34f2c25f9963d844abe63cfc87fee62069a16f739ee45"
    )
    assert replay["execution_log_file_sha256"] == (
        "16764523fa62b59c0fd304ce8c6afa1a36191b5289d647701abf305839d71d53"
    )
    assert payload["historical_retry_008"]["downstream"] == {
        "final_smoke_result_exists": False,
        "activation_v3_exists": False,
        "retry_008_qualified_d0": False,
    }


def test_replacement_lanes_keep_performance_correctness_and_quality_separate() -> None:
    payload = verify_s4_validation_boundary_amendment(_build())["payload"]
    lanes = payload["validation_lanes"]

    assert lanes["RX0_NATIVE_REAL_EXECUTION"]["real_system_execution"] is True
    assert lanes["RX0_NATIVE_REAL_EXECUTION"]["headline_performance_source"] is True
    assert lanes["RX0_NATIVE_REAL_EXECUTION"]["retry_008_role"] == (
        "ONE_HISTORY_OPERATIONAL_CANARY_ONLY"
    )

    tr0 = lanes["TR0_SCHEDULING_TRACE_REPLAY"]
    assert tr0["supporting_control_only"] is True
    assert tr0["headline_performance_source"] is False
    assert tr0["semantic_correctness_oracle"] is False
    assert tr0["fixed_wall_clock_demand_is_counterfactual_only"] is True
    assert tr0["requires_real_system_calibration"] is True
    assert tr0["calibration_policies"] == ["NATIVE", "CHANGED_POLICY"]
    assert tr0["calibration_load_regions"] == ["LOW", "NEAR_SATURATION"]
    assert tr0["calibration_threshold_status"] == "MUST_PREREGISTER_BEFORE_RESULTS"

    fx0 = lanes["FX0_DETERMINISTIC_MECHANISM_FIXTURE"]
    assert fx0["production_mechanism_path_required"] is True
    assert fx0["fixture_count_policy"] == "TRANSITION_COVERAGE_NOT_FIXED_COUNT"
    assert fx0["exact_canonical_state_parity_required"] is True
    assert fx0["exact_publication_history_parity_required"] is True
    assert {
        "ENTITY_ALIAS_CANONICAL_MERGE",
        "COMPATIBLE_DUPLICATE_UUID_COALESCING",
        "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
        "RELATION_RESOLUTION",
        "TEMPORAL_INVALIDATION_UPDATE",
        "PREPARE_TO_BIND_STATE_CHANGE",
        "SOURCE_ORDERED_PUBLICATION",
        "RETRY_IDEMPOTENCE",
        "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
    } <= set(fx0["required_transitions"])

    real = lanes["REAL_WORKLOAD_CORRECTNESS"]
    assert real["all_methods_execute_real_graphiti"] is True
    assert real["episode_source_coverage"] == 1.0
    assert real["lost"] == 0
    assert real["duplicate"] == 0
    assert real["source_publication_order_violations"] == 0
    assert real["temporal_provenance_violations"] == 0
    assert real["aggregate_graph_counts_are_descriptive_only"] is True
    assert real["quality_margins_status"] == "MUST_PREREGISTER_BEFORE_RESULTS"


def test_amendment_authorizes_only_offline_design_and_forbids_retry_009() -> None:
    payload = verify_s4_validation_boundary_amendment(_build())["payload"]

    assert payload["historical_retry_008"]["disposition"] == {
        "resume_authorized": False,
        "cleanup_authorized": False,
        "rewrite_authorized": False,
        "retry_009_authorized": False,
        "candidate_level_replay_is_main_path": False,
        "legacy_infrastructure_status": "PRESERVED_NON_MAIN_PATH_NON_AUTHORIZING",
    }
    legacy = payload["legacy_d0_replay_infrastructure"]
    assert legacy["inheritance_allowed"] is False
    assert legacy["authority_reuse_allowed"] is False
    assert legacy["evidence_only_artifact_classes"] == [
        "SMOKE_AUTHORITIES",
        "REMAP_AUTHORITIES",
        "SIDECAR_AUTHORITIES",
        "FIXED_THREE_AUTHORITIES",
        "AUTHORITY_CONSUMPTIONS",
        "SMOKE_AND_FIXED_THREE_RESULTS",
        "QUALIFICATION_ACTIVATIONS",
    ]
    assert "CONTIGUOUS_PREFIX_CHECKPOINT" in legacy[
        "mechanisms_requiring_new_lane_qualification"
    ]
    assert "EXTERNAL_EVIDENCE_RECOMPUTATION" in legacy[
        "mechanisms_requiring_new_lane_qualification"
    ]
    assert payload["offline_evidence"]["red_failure_or_error_count"] == 1
    assert payload["offline_evidence"]["focused_green_pass_count"] == 9
    assert payload["offline_evidence"]["full_green_pass_count"] == 855
    assert payload["authority"] == {
        "revised_s4_offline_design_authorized": True,
        "tr0_offline_design_authorized": True,
        "fx0_offline_design_authorized": True,
        "s5_offline_design_authorized": True,
        "model_call_authorized": False,
        "neo4j_mutation_authorized": False,
        "tr0_live_execution_authorized": False,
        "fx0_live_execution_authorized": False,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["historical_retry_008"]["replay"].update(
            status="PASS"
        ),
        lambda value: value["payload"]["validation_lanes"][
            "TR0_SCHEDULING_TRACE_REPLAY"
        ].update(headline_performance_source=True),
        lambda value: value["payload"]["validation_lanes"][
            "FX0_DETERMINISTIC_MECHANISM_FIXTURE"
        ].update(fixture_count_policy="FIXED_3_TO_5_EPISODES"),
        lambda value: value["payload"]["authority"].update(
            pilot_execution_authorized=True
        ),
    ],
)
def test_resealed_semantic_or_authority_drift_fails_closed(mutate) -> None:
    artifact = _build()
    mutate(artifact)
    resealed = finalize_envelope(
        payload=artifact["payload"],
        protocol_version=artifact["protocol_version"],
        git_commit=artifact["git_commit"],
        run_id=artifact["run_id"],
    )
    with pytest.raises(ValueError):
        verify_s4_validation_boundary_amendment(resealed)


def test_document_is_additive_cites_methodology_and_execution_plan_points_to_it() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")
    plan = EXECUTION_PLAN.read_text(encoding="utf-8")

    assert "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e" in text
    assert "bab29baec9d83dcb2ce4310e9694774d9efc0278f23a069a0a2716dea26d5c62" in text
    assert "do not redefine historical D0" in text
    assert "TR0_SCHEDULING_TRACE_REPLAY" in text
    assert "fixed-demand scheduling counterfactual" in text
    assert "not headline performance evidence" in text
    assert "transition coverage" in text
    assert "FoundationDB" in text
    assert "AlpaServe" in text
    assert "Firmament" in text
    assert "Sparrow" in text
    assert "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md" in plan
    assert "controlling S4 interpretation overlay" in plan


def test_current_pointer_remains_s3_and_documents_contain_no_runtime_secrets() -> None:
    pointer = _load(CURRENT_STAGE)
    assert pointer["payload"]["current_stage"] == "S3_CONFIGURATION_FROZEN"
    assert pointer["payload"]["s4_live_execution_authorized"] is False
    combined = (
        AMENDMENT.read_text(encoding="utf-8")
        + EXECUTION_PLAN.read_text(encoding="utf-8")
    ).lower()
    for forbidden in ("api-key", "api_key", "10.87.5.247", "127.0.0.1:17897"):
        assert forbidden not in combined
