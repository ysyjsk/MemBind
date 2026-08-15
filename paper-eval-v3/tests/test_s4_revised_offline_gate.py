"""Offline qualification gate for the revised S4 validation boundary.

The gate qualifies contracts and test frameworks only.  It deliberately does
not turn a synthetic TR0 trace or the unfrozen FX0 M* adapter into evidence.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.real_workload_correctness_contract import (
    build_real_workload_correctness_contract,
)
from paper_eval.s4_revised_offline_gate import (
    RevisedS4OfflineGateError,
    build_revised_s4_offline_gate,
    verify_revised_s4_offline_gate,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"

PATHS = {
    "parent": ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md",
    "amendment_document": PROJECT / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md",
    "amendment_artifact": NATIVE / "S4_VALIDATION_BOUNDARY_AMENDMENT.json",
    "current_pointer": PROJECT / "runtime/CURRENT_STAGE_STATUS.json",
    "roles": PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json",
    "dataset": NATIVE / "DATASET_PARITY.json",
    "evaluator": NATIVE / "EVALUATOR_PARITY.json",
    "native_freeze": NATIVE / "NATIVE_BASELINE_V2_FREEZE.json",
}

SOURCE_PATHS = {
    "tr0_source": PROJECT / "src/paper_eval/s4_tr0_trace_replay.py",
    "tr0_test": PROJECT / "tests/test_s4_tr0_trace_replay.py",
    "fx0_source": PROJECT / "src/paper_eval/fx0_mechanism_fixture.py",
    "fx0_test": PROJECT / "tests/test_fx0_mechanism_fixture.py",
    "fx0_document": PROJECT / "FX0_DETERMINISTIC_MECHANISM_FIXTURE_FRAMEWORK_v1.0.md",
    "real_workload_source": PROJECT / "src/paper_eval/real_workload_correctness_contract.py",
    "real_workload_test": PROJECT / "tests/test_real_workload_correctness_contract.py",
    "gate_source": PROJECT / "src/paper_eval/s4_revised_offline_gate.py",
    "gate_test": PROJECT / "tests/test_s4_revised_offline_gate.py",
    "gate_finalizer": PROJECT / "scripts/finalize_s4_revised_offline_gate.py",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _real_contract() -> dict:
    return build_real_workload_correctness_contract(
        parent_protocol_file_sha256=sha256_file(PATHS["parent"]),
        s4_amendment_document_file_sha256=sha256_file(
            PATHS["amendment_document"]
        ),
        s4_amendment_artifact=_load(PATHS["amendment_artifact"]),
        s4_amendment_artifact_file_sha256=sha256_file(
            PATHS["amendment_artifact"]
        ),
        current_stage_pointer=_load(PATHS["current_pointer"]),
        current_stage_pointer_file_sha256=sha256_file(PATHS["current_pointer"]),
        role_registry=_load(PATHS["roles"]),
        role_registry_file_sha256=sha256_file(PATHS["roles"]),
        dataset_parity=_load(PATHS["dataset"]),
        dataset_parity_file_sha256=sha256_file(PATHS["dataset"]),
        evaluator_parity=_load(PATHS["evaluator"]),
        evaluator_parity_file_sha256=sha256_file(PATHS["evaluator"]),
        native_baseline_freeze=_load(PATHS["native_freeze"]),
        native_baseline_freeze_file_sha256=sha256_file(PATHS["native_freeze"]),
        git_commit="deadbeef",
        run_id="real-workload-correctness-contract-gate-test-001",
    )


def _green(tests: int, seed: str) -> dict:
    return {
        "junit_file_sha256": seed * 64,
        "tests": tests,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }


def _red(seed: str) -> dict:
    return {
        "junit_file_sha256": seed * 64,
        "tests": 1,
        "failures": 0,
        "errors": 1,
        "skipped": 0,
    }


def _build() -> dict:
    return build_revised_s4_offline_gate(
        amendment_artifact=_load(PATHS["amendment_artifact"]),
        amendment_artifact_file_sha256=sha256_file(
            PATHS["amendment_artifact"]
        ),
        real_workload_contract=_real_contract(),
        source_file_sha256={
            name: (sha256_file(path) if path.exists() else "a" * 64)
            for name, path in SOURCE_PATHS.items()
        },
        focused_green_evidence={
            "TR0_SCHEDULING_TRACE_REPLAY": _green(17, "b"),
            "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _green(17, "c"),
            "REAL_WORKLOAD_CORRECTNESS": _green(15, "d"),
            "S4_REVISED_OFFLINE_GATE": _green(11, "1"),
        },
        red_evidence={
            "TR0_SCHEDULING_TRACE_REPLAY": _red("2"),
            "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _red("3"),
            "REAL_WORKLOAD_CORRECTNESS": _red("4"),
            "S4_REVISED_OFFLINE_GATE": _red("5"),
        },
        full_regression_evidence=_green(950, "e"),
        git_commit="deadbeef",
    )


def test_gate_preserves_four_lane_claim_boundaries() -> None:
    payload = verify_revised_s4_offline_gate(_build())["payload"]

    assert payload["status"] == "OFFLINE_FRAMEWORKS_QUALIFIED_ONLY"
    assert payload["historical_rx0"]["real_native_episode_coverage"] == "49/49"
    assert payload["historical_rx0"]["headline_performance_evidence"] is False
    assert payload["tr0"] == {
        "framework_status": "IMPLEMENTATION_QUALIFIED_ONLY",
        "measured_trace_status": "NOT_SEALED",
        "replay_result_status": "NOT_EXECUTED",
        "supporting_control_only": True,
        "headline_performance_evidence": False,
        "semantic_correctness_evidence": False,
        "real_system_calibration_status": "NOT_SATISFIED",
    }
    assert payload["fx0"] == {
        "framework_status": "HARNESS_QUALIFIED_WITH_TEST_DOUBLE_ONLY",
        "production_m_star_identity_status": "NOT_FROZEN",
        "exact_parity_status": "NOT_EXECUTED",
        "performance_evidence": False,
        "semantic_correctness_evidence": False,
        "adapter_receives_expected_oracle": False,
        "s5_method_qualification_required": True,
    }
    assert payload["real_workload_correctness"]["contract_status"] == (
        "FROZEN_OFFLINE"
    )
    assert payload["real_workload_correctness"]["result_status"] == (
        "NOT_EXECUTED"
    )
    assert payload["real_workload_correctness"]["matching_oracle_status"] == (
        "NOT_FROZEN"
    )
    assert payload["real_workload_correctness"]["quality_margins_status"] == (
        "NOT_FROZEN"
    )


def test_gate_embeds_and_verifies_real_workload_contract() -> None:
    payload = verify_revised_s4_offline_gate(_build())["payload"]
    contract = payload["real_workload_correctness"]["contract"]

    assert contract["payload"]["execution_contract"][
        "all_methods_execute_real_graphiti"
    ] is True
    assert contract["payload"]["direct_invariants"][
        "episode_source_coverage"
    ] == 1.0
    assert contract["payload"]["semantic_graph_contract"][
        "aggregate_counts_can_establish_parity"
    ] is False
    assert contract["payload"]["authority"]["s5_live_execution_authorized"] is False


def test_gate_binds_complete_source_and_green_evidence_inventory() -> None:
    payload = verify_revised_s4_offline_gate(_build())["payload"]

    assert set(payload["source_file_sha256"]) == set(SOURCE_PATHS)
    expected_lanes = {
        "TR0_SCHEDULING_TRACE_REPLAY",
        "FX0_DETERMINISTIC_MECHANISM_FIXTURE",
        "REAL_WORKLOAD_CORRECTNESS",
        "S4_REVISED_OFFLINE_GATE",
    }
    assert set(payload["focused_green_evidence"]) == expected_lanes
    assert set(payload["red_evidence"]) == expected_lanes
    assert payload["full_regression_evidence"]["tests"] >= 900


def test_gate_authorizes_only_s5_offline_design() -> None:
    payload = verify_revised_s4_offline_gate(_build())["payload"]

    assert payload["next_action"] == "S5_PRODUCTION_METHOD_QUALIFICATION_OFFLINE_DESIGN"
    assert payload["authority"] == {
        "s5_offline_design_authorized": True,
        "revised_s4_offline_design_authorized": True,
        "result_generation_or_inspection_authorized": False,
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
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
        lambda value: value["payload"]["tr0"].update(
            headline_performance_evidence=True
        ),
        lambda value: value["payload"]["fx0"].update(exact_parity_status="PASS"),
        lambda value: value["payload"]["real_workload_correctness"].update(
            result_status="PASS"
        ),
        lambda value: value["payload"]["authority"].update(
            s5_live_execution_authorized=True
        ),
    ],
)
def test_resealed_claim_or_authority_drift_fails_closed(mutate) -> None:
    artifact = _build()
    mutate(artifact)
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(RevisedS4OfflineGateError):
        verify_revised_s4_offline_gate(artifact)


def test_resealed_nested_correctness_contract_drift_fails_closed() -> None:
    artifact = _build()
    contract = artifact["payload"]["real_workload_correctness"]["contract"]
    contract["payload"]["direct_invariants"]["episode_source_coverage"] = 0.99
    contract["payload_sha256"] = payload_sha256(contract["payload"])
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(RevisedS4OfflineGateError):
        verify_revised_s4_offline_gate(artifact)


def test_builder_rejects_non_green_or_incomplete_evidence() -> None:
    evidence = {
        "TR0_SCHEDULING_TRACE_REPLAY": _green(17, "b"),
        "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _green(17, "c"),
        "REAL_WORKLOAD_CORRECTNESS": _green(15, "d"),
        "S4_REVISED_OFFLINE_GATE": _green(11, "1"),
    }
    evidence["FX0_DETERMINISTIC_MECHANISM_FIXTURE"]["failures"] = 1
    with pytest.raises(RevisedS4OfflineGateError):
        build_revised_s4_offline_gate(
            amendment_artifact=_load(PATHS["amendment_artifact"]),
            amendment_artifact_file_sha256=sha256_file(
                PATHS["amendment_artifact"]
            ),
            real_workload_contract=_real_contract(),
            source_file_sha256={name: "f" * 64 for name in SOURCE_PATHS},
            focused_green_evidence=evidence,
            red_evidence={
                "TR0_SCHEDULING_TRACE_REPLAY": _red("2"),
                "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _red("3"),
                "REAL_WORKLOAD_CORRECTNESS": _red("4"),
                "S4_REVISED_OFFLINE_GATE": _red("5"),
            },
            full_regression_evidence=_green(950, "e"),
            git_commit="deadbeef",
        )


def test_gate_contains_no_private_runtime_data_and_pointer_stays_s3() -> None:
    artifact = verify_revised_s4_offline_gate(_build())
    serialized = json.dumps(artifact, sort_keys=True).casefold()

    for forbidden in ("api_key", "password", "raw_response", "messages"):
        assert forbidden not in serialized
    assert artifact["payload"]["current_stage"] == "S3_CONFIGURATION_FROZEN"


def test_builder_rejects_contract_parent_or_pointer_not_bound_to_amendment() -> None:
    contract = _real_contract()
    contract["payload"]["input_bindings"]["parent_protocol"]["file_sha256"] = (
        "f" * 64
    )
    contract["payload_sha256"] = payload_sha256(contract["payload"])
    with pytest.raises(RevisedS4OfflineGateError, match="current S4 amendment"):
        build_revised_s4_offline_gate(
            amendment_artifact=_load(PATHS["amendment_artifact"]),
            amendment_artifact_file_sha256=sha256_file(
                PATHS["amendment_artifact"]
            ),
            real_workload_contract=contract,
            source_file_sha256={name: "f" * 64 for name in SOURCE_PATHS},
            focused_green_evidence={
                "TR0_SCHEDULING_TRACE_REPLAY": _green(17, "b"),
                "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _green(17, "c"),
                "REAL_WORKLOAD_CORRECTNESS": _green(15, "d"),
                "S4_REVISED_OFFLINE_GATE": _green(11, "1"),
            },
            red_evidence={
                "TR0_SCHEDULING_TRACE_REPLAY": _red("2"),
                "FX0_DETERMINISTIC_MECHANISM_FIXTURE": _red("3"),
                "REAL_WORKLOAD_CORRECTNESS": _red("4"),
                "S4_REVISED_OFFLINE_GATE": _red("5"),
            },
            full_regression_evidence=_green(950, "e"),
            git_commit="deadbeef",
        )
