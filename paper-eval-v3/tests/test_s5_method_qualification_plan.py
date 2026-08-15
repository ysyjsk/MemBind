"""TDD contract for the offline S5 method-qualification design.

The plan freezes identities and blockers; it never authorizes a model, Neo4j,
or method smoke run by itself.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s4_revised_offline_gate import verify_revised_s4_offline_gate
from paper_eval.s5_method_qualification_plan import (
    S5MethodQualificationError,
    build_s5_method_qualification_plan,
    verify_s5_method_qualification_plan,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
GATE = NATIVE / "S4_REVISED_OFFLINE_GATE.json"
CURRENT = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
FREEZE = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
ROLES = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"

SOURCE_NAMES = {
    "common_runtime",
    "native_entrypoint",
    "a0_scheduler",
    "a0_live_adapter",
    "a0_durable_store",
    "p_scheduler",
    "p_live_adapter",
    "p_invariant_checker",
    "m_candidate_core",
    "m_ordered_binder",
    "m_semantic_compile",
    "fx0_harness",
    "s5_plan_source",
    "s5_plan_test",
    "s5_plan_finalizer",
    "s5_workplan",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _gate() -> dict:
    return verify_revised_s4_offline_gate(_load(GATE))


def _green(tests: int, seed: str) -> dict:
    return {
        "junit_file_sha256": seed * 64,
        "tests": tests,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }


def _red() -> dict:
    return {
        "junit_file_sha256": "b" * 64,
        "tests": 1,
        "failures": 0,
        "errors": 1,
        "skipped": 0,
    }


def _build() -> dict:
    return build_s5_method_qualification_plan(
        parent_protocol_file_sha256=sha256_file(
            ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
        ),
        s4_gate_artifact=_load(GATE),
        s4_gate_file_sha256=sha256_file(GATE),
        current_stage_pointer=_load(CURRENT),
        current_stage_pointer_file_sha256=sha256_file(CURRENT),
        native_baseline_freeze=_load(FREEZE),
        native_baseline_freeze_file_sha256=sha256_file(FREEZE),
        role_registry=_load(ROLES),
        role_registry_file_sha256=sha256_file(ROLES),
        source_file_sha256={name: "a" * 64 for name in SOURCE_NAMES},
        offline_evidence={
            "red": _red(),
            "focused_green": _green(12, "c"),
            "full_green": _green(970, "d"),
        },
        git_commit="deadbeef",
    )


def test_s5_registry_is_offline_only_and_uses_new_method_identities() -> None:
    payload = verify_s5_method_qualification_plan(_build())["payload"]

    assert payload["status"] == "OFFLINE_DESIGN_ONLY"
    assert payload["methods"] == ["A0", "P*", "M*"]
    assert payload["execution_order"] == ["A0", "P*", "M*"]
    assert payload["method_id_aliases_forbidden"] == ["M0", "M1", "M2"]
    assert payload["current_stage"] == "S3_CONFIGURATION_FROZEN"
    assert payload["next_action"] == "S5_ADAPTER_IMPLEMENTATION_AND_OFFLINE_TESTS"


def test_s5_freezes_one_development_history_without_quality_claim() -> None:
    payload = verify_s5_method_qualification_plan(_build())["payload"]
    assert payload["history"] == {
        "data_role": "DEVELOPMENT_EXPOSED",
        "history_id": "07741c45",
        "episode_count": 49,
        "selection_rule": "PREDECLARED_DEVELOPMENT_EXPOSED_SMOKE_HISTORY",
        "quality_claim_authorized": False,
    }


def test_s5_method_statuses_do_not_promote_old_components() -> None:
    methods = verify_s5_method_qualification_plan(_build())["payload"]["method_registry"]

    assert methods["A0"]["candidate_status"] == (
        "REUSABLE_COMPONENTS_ENTRYPOINT_NOT_FROZEN"
    )
    assert methods["P*"]["candidate_status"] == (
        "C2_SMOKE_ENTRYPOINT_NOT_FROZEN"
    )
    assert methods["M*"]["candidate_status"] == (
        "EXPLORATORY_CORE_NOT_PRODUCTION"
    )
    assert methods["M*"]["production_identity_status"] == "NONE_FROZEN"
    assert methods["M*"]["fx0_exact_parity_required"] is True
    assert methods["M*"]["durable_publication_journal_required"] is True
    assert methods["P*"]["direct_invariant_violation_interpretation"] == (
        "SCIENTIFIC_OUTCOME_NOT_ADAPTER_FAILURE"
    )


def test_s5_requires_common_native_runtime_and_explicit_c2_smoke() -> None:
    payload = verify_s5_method_qualification_plan(_build())["payload"]
    assert payload["common_runtime"]["same_native_graphiti_construction_base"] is True
    assert payload["common_runtime"]["synthetic_graphiti_substitution_allowed"] is False
    assert payload["method_registry"]["A0"]["concurrency"] == 1
    assert payload["method_registry"]["P*"]["concurrency"] == 2
    assert payload["method_registry"]["M*"]["concurrency"] == 2


def test_s5_tdd_and_stop_rules_are_frozen() -> None:
    payload = verify_s5_method_qualification_plan(_build())["payload"]
    assert payload["tdd_sequence"] == [
        "RED_METHOD_REGISTRY",
        "FOCUSED_GREEN_METHOD_REGISTRY",
        "FULL_OFFLINE_GREEN",
        "FX0_PRODUCTION_IDENTITY_AND_ADAPTER_GREEN",
        "ONLY_THEN_METHOD_LIVE_AUTHORITY",
    ]
    assert payload["stop_rules"] == [
        "A0_SMOKE_FAILURE_STOPS_S5",
        "P_C2_INFRA_ADAPTER_TELEMETRY_OR_ACCOUNTING_FAILURE_STOPS_S5",
        "M_STAR_FX0_OR_DIRECT_INVARIANT_FAILURE_STOPS_S5",
        "NO_C_SWEEP_BEFORE_M_STAR_SMOKE_PASS",
        "NO_LIVE_ACTION_FROM_THIS_OFFLINE_PLAN",
    ]
    assert payload["offline_evidence"]["red"]["errors"] == 1
    assert payload["offline_evidence"]["focused_green"]["failures"] == 0
    assert payload["offline_evidence"]["full_green"]["tests"] >= 900


def test_s5_authority_and_legacy_boundary_are_closed() -> None:
    payload = verify_s5_method_qualification_plan(_build())["payload"]
    assert payload["authority"] == {
        "s5_offline_design_authorized": True,
        "s5_adapter_implementation_authorized": True,
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
        "neo4j_mutation_authorized": False,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    assert payload["legacy_boundary"] == {
        "legacy_d0_authority_inheritance_allowed": False,
        "legacy_d0_result_merge_allowed": False,
        "legacy_s4_namespace_reuse_allowed": False,
        "retry_008_resume_allowed": False,
        "retry_009_allowed": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["payload"]["method_registry"]["M*"] .update(
            candidate_status="PRODUCTION_QUALIFIED"
        ),
        lambda value: value["payload"]["method_registry"]["P*"] .update(
            concurrency=8
        ),
        lambda value: value["payload"]["authority"].update(
            s5_live_execution_authorized=True
        ),
        lambda value: value["payload"]["history"].update(
            data_role="PILOT"
        ),
    ],
)
def test_s5_verifier_fails_closed_on_identity_or_authority_drift(mutate) -> None:
    artifact = copy.deepcopy(_build())
    mutate(artifact)
    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(S5MethodQualificationError):
        verify_s5_method_qualification_plan(artifact)


def test_s5_rejects_missing_source_identity_and_private_data() -> None:
    kwargs = {
        "parent_protocol_file_sha256": "a" * 64,
        "s4_gate_artifact": _load(GATE),
        "s4_gate_file_sha256": sha256_file(GATE),
        "current_stage_pointer": _load(CURRENT),
        "current_stage_pointer_file_sha256": sha256_file(CURRENT),
        "native_baseline_freeze": _load(FREEZE),
        "native_baseline_freeze_file_sha256": sha256_file(FREEZE),
        "role_registry": _load(ROLES),
        "role_registry_file_sha256": sha256_file(ROLES),
        "source_file_sha256": {name: "a" * 64 for name in SOURCE_NAMES - {"m_candidate_core"}},
        "offline_evidence": {
            "red": _red(),
            "focused_green": _green(12, "c"),
            "full_green": _green(970, "d"),
        },
        "git_commit": "deadbeef",
    }
    with pytest.raises(S5MethodQualificationError):
        build_s5_method_qualification_plan(**kwargs)


def test_s5_plan_has_no_runtime_secrets() -> None:
    text = json.dumps(_build(), ensure_ascii=False, sort_keys=True).casefold()
    for forbidden in ("api_key", "password", "raw_response", "messages"):
        assert forbidden not in text
