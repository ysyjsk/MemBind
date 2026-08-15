"""Post-seal checks for the Native-v2 freeze and additive current pointer."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s3_native_v2_freeze import verify_native_baseline_v2_freeze


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
ARTIFACTS = PROJECT / "artifacts/paper_eval"
NATIVE = ARTIFACTS / "native"
FREEZE_PATH = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
CURRENT_POINTER = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_sealed_freeze_matches_every_bound_file_and_source() -> None:
    freeze = verify_native_baseline_v2_freeze(_load(FREEZE_PATH))
    payload = freeze["payload"]
    paths = {
        "s0_current_state": ARTIFACTS / "S0_CURRENT_STATE.json",
        "s1_u0_smoke": NATIVE / "U0_SMOKE.json",
        "u0_qualification": NATIVE / "U0_QUALIFICATION.json",
        "dataset_parity": NATIVE / "DATASET_PARITY.json",
        "evaluator_parity": NATIVE / "EVALUATOR_PARITY.json",
        "direct_add_episode_contract": (
            NATIVE / "U0_DIRECT_ADD_EPISODE_CONTRACT.json"
        ),
        "completion_adapter_identity": (
            NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json"
        ),
        "retrieval_contract": NATIVE / "S2_COMPLETION_CONTRACT.json",
        "retrieval_policy_freeze": (
            NATIVE / "S2_COMPLETION_POLICY_FREEZE.json"
        ),
        "role_registry": ARTIFACTS / "DEVELOPMENT_EXPOSED_IDS.json",
        "reader_v2_contract": NATIVE / "NATIVE_READER_V2_CONTRACT.json",
        "reader_v2_freeze": NATIVE / "NATIVE_READER_V2_FREEZE.json",
        "parent_workplan": (
            ROOT
            / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
        ),
        "reader_v2_workplan": (
            PROJECT / "NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md"
        ),
    }
    assert payload["input_file_sha256"] == {
        name: sha256_file(path) for name, path in sorted(paths.items())
    }
    source_paths = {
        "finalize_script": PROJECT / "scripts/finalize_s3_native_v2_freeze.py",
        "focused_green_preseal": (
            PROJECT
            / "logs/TDD_FOCUSED_GREEN_S3_NATIVE_V2_FREEZE_FINAL_20260814.xml"
        ),
        "freeze_source": PROJECT / "src/paper_eval/s3_native_v2_freeze.py",
        "freeze_test": PROJECT / "tests/test_s3_native_v2_freeze.py",
        "full_offline_green_preseal": (
            PROJECT
            / "logs/TDD_FULL_OFFLINE_GREEN_S3_NATIVE_V2_FREEZE_PRESEAL_20260814.xml"
        ),
    }
    assert payload["source_sha256"] == {
        name: sha256_file(path) for name, path in sorted(source_paths.items())
    }


def test_current_pointer_advances_only_to_s4_offline() -> None:
    pointer = _load(CURRENT_POINTER)
    payload = pointer["payload"]

    assert pointer["status"] == "finalized"
    assert pointer["payload_sha256"] == payload_sha256(payload)
    assert payload == {
        "schema_version": "membind.paper-eval-v3.current-stage-pointer.v2",
        "current_stage": "S3_CONFIGURATION_FROZEN",
        "status": "PASS_CONFIGURATION_FREEZE_ONLY",
        "baseline_id": "native-graphiti-u0-reader-v2",
        "native_baseline_v2_freeze_file_sha256": sha256_file(FREEZE_PATH),
        "native_baseline_v2_freeze_payload_sha256": _load(FREEZE_PATH)[
            "payload_sha256"
        ],
        "historical_stage_status_file_sha256": sha256_file(
            PROJECT / "runtime/STAGE_STATUS.json"
        ),
        "historical_stage_status_preserved": True,
        "passed_execution_stages": ["S0", "S1"],
        "completed_configuration_stages": ["NATIVE_READER_V2", "S3_NATIVE_V2"],
        "next_authorized_action": "S4_OFFLINE_GATE_DESIGN_AND_TESTS",
        "quality_estimate_status": "NOT_ESTIMATED",
        "live_preflight_required": True,
        "s4_live_execution_authorized": False,
        "pilot_execution_authorized": False,
    }
