"""Offline TDD for the fixed four-history S4 qualification plan."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_qualification_plan import (
    build_s4_qualification_plan,
    finalize_s4_qualification_plan,
    verify_s4_qualification_plan,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
ROLE_REGISTRY = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
SPLIT = ROOT / "membind-validation/artifacts/dataset/frozen_split.json"
S3_FREEZE = PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
S4_WORKPLAN = PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build(*, roles: dict | None = None, split: dict | None = None) -> dict:
    return build_s4_qualification_plan(
        role_registry=roles or _load(ROLE_REGISTRY),
        role_registry_file_sha256=sha256_file(ROLE_REGISTRY),
        split=split or _load(SPLIT),
        split_file_sha256=sha256_file(SPLIT),
        s3_freeze=_load(S3_FREEZE),
        s3_freeze_file_sha256=sha256_file(S3_FREEZE),
        s4_workplan_sha256=sha256_file(S4_WORKPLAN),
        source_sha256={"plan": "1" * 64, "test": "2" * 64},
    )


def test_plan_is_fixed_four_histories_and_reuses_only_the_smoke_block() -> None:
    plan = verify_s4_qualification_plan(_build())

    assert plan["history_ids"] == [
        "07741c45",
        "b6019101",
        "6071bd76",
        "a2f3aa27",
    ]
    assert plan["blocks"][0] == {
        "history_id": "07741c45",
        "mode": "REUSE_SEALED_S4_SMOKE_PASS",
        "required_artifact": "artifacts/paper_eval/native/S4_D0_SMOKE_RESULT.json",
        "live_execution": False,
    }
    assert [block["history_id"] for block in plan["blocks"][1:]] == [
        "b6019101",
        "6071bd76",
        "a2f3aa27",
    ]
    assert all(block["mode"] == "NEW_CAPTURE_REPLAY_BLOCK" for block in plan["blocks"][1:])
    assert len({block["cache_id"] for block in plan["blocks"][1:]}) == 3
    assert len(
        {
            run["namespace"]
            for block in plan["blocks"][1:]
            for run in block["runs"].values()
        }
    ) == 6


def test_plan_preserves_common_policy_and_uses_quality_only_descriptively() -> None:
    plan = _build()
    freeze = _load(S3_FREEZE)

    assert plan["common_method_policy_sha256"] == next(
        iter(freeze["payload"]["method_policy_bindings"].values())
    )
    assert plan["fixed_common_evaluation"] == {
        "dataset_unchanged": True,
        "retrieval": "Graphiti Episode BM25/RRF",
        "top_k": 10,
        "reader": "Native Reader-v2 JSON USERONLY=false con max_tokens=800",
        "judge_unchanged": True,
    }
    assert plan["quality_interpretation"] == {
        "retrieval_and_qa": "PAIRED_DESCRIPTIVE_ONLY",
        "qa_hard_gate": False,
        "d0_minus_u0_one_pp_rule": False,
    }
    assert plan["hard_gates"]["canonical_graph_parity"] == "EXACT_100_PERCENT"
    assert plan["hard_gates"]["oracle_miss_or_fallback"] == 0
    assert plan["work_volume_guardrail"] == {
        "interval": [0.95, 1.05],
        "status": "PROJECT_SPECIFIC_FAIRNESS_GUARDRAIL_NOT_FIELD_STANDARD",
    }
    assert plan["authority"] == {
        "qualification_live_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_plan_rejects_split_role_or_inventory_drift() -> None:
    split = _load(SPLIT)
    split["calibration_question_ids"] = split["calibration_question_ids"][:3]
    with pytest.raises(ValueError, match="four"):
        _build(split=split)

    roles = _load(ROLE_REGISTRY)
    roles["payload"]["roles"]["DEVELOPMENT_EXPOSED"].remove("6071bd76")
    with pytest.raises(Exception):
        _build(roles=roles)


def test_plan_hash_and_attempt_identity_tamper_fail_closed() -> None:
    plan = _build()
    altered = copy.deepcopy(plan)
    altered["blocks"][1]["runs"]["U0_CAPTURE"]["namespace"] = "wrong"
    with pytest.raises(ValueError):
        verify_s4_qualification_plan(altered)


def test_plan_finalization_is_exclusive(tmp_path: Path) -> None:
    plan = _build()
    output = tmp_path / "S4_D0_QUALIFICATION_PLAN.json"
    assert finalize_s4_qualification_plan(path=output, plan=plan) == plan
    with pytest.raises(FileExistsError):
        finalize_s4_qualification_plan(path=output, plan=plan)
