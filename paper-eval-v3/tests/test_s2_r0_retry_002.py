from __future__ import annotations

from pathlib import Path

from paper_eval.s2_r0_authorization import REQUIRED_BINDINGS
from paper_eval.s2_r0_controller import (
    DEFAULT_AUTHORIZATION,
    DEFAULT_CONSUMPTION,
    DEFAULT_FAILURE,
    DEFAULT_QUALIFICATION,
    DEFAULT_RESULT,
    DEFAULT_RUN_ID,
    RETRY_002_AUTHORIZATION,
    RETRY_002_CONSUMPTION,
    RETRY_002_FAILURE,
    RETRY_002_QUALIFICATION,
    RETRY_002_RESULT,
    RETRY_002_RUN_ID,
    retry_002_binding_paths,
)


ROOT = Path(__file__).resolve().parents[1]
LINEAGE_BINDINGS = {
    "prior_s2r0_authorization",
    "prior_s2r0_consumption",
    "prior_s2r0_failure",
    "s2r0_failure_root_cause",
    "retry_execution_plan",
    "repair_red",
    "repair_targeted_green",
    "repair_focused_green",
    "repair_full_green",
}


def test_retry_002_paths_are_disjoint_from_terminal_attempt_001() -> None:
    assert DEFAULT_RUN_ID == "s2r0-20260814-001"
    assert RETRY_002_RUN_ID == "s2r0-20260814-002"
    old = {
        DEFAULT_QUALIFICATION.resolve(),
        DEFAULT_AUTHORIZATION.resolve(),
        DEFAULT_CONSUMPTION.resolve(),
        DEFAULT_RESULT.resolve(),
        DEFAULT_FAILURE.resolve(),
    }
    replacement = {
        RETRY_002_QUALIFICATION.resolve(),
        RETRY_002_AUTHORIZATION.resolve(),
        RETRY_002_CONSUMPTION.resolve(),
        RETRY_002_RESULT.resolve(),
        RETRY_002_FAILURE.resolve(),
    }
    assert old.isdisjoint(replacement)
    assert all(
        "s2r0-20260814-002" in str(path)
        for path in replacement
        - {RETRY_002_QUALIFICATION.resolve(), RETRY_002_AUTHORIZATION.resolve()}
    )


def test_retry_002_binding_manifest_contains_terminal_lineage_and_new_evidence() -> None:
    bindings = retry_002_binding_paths()
    assert set(bindings) == set(REQUIRED_BINDINGS)
    assert LINEAGE_BINDINGS <= set(bindings)
    assert all(
        path.is_file()
        for name, path in bindings.items()
        if name
        not in {"focused_green", "full_green", "finalize_script", "run_script"}
    )
    assert bindings["prior_s2r0_authorization"] == DEFAULT_AUTHORIZATION
    assert bindings["prior_s2r0_consumption"] == DEFAULT_CONSUMPTION
    assert bindings["prior_s2r0_failure"] == DEFAULT_FAILURE
    assert bindings["retry_execution_plan"] == (
        ROOT / "S2_R0_RETRY_002_EXECUTION_PLAN_20260814.md"
    )
    assert bindings["s2r0_failure_root_cause"] == (
        ROOT / "S2_R0_FAILURE_ROOT_CAUSE_20260814.md"
    )


def test_retry_002_scripts_separate_offline_authority_from_live_execution() -> None:
    finalize_script = (ROOT / "scripts/finalize_s2_r0_retry_002.py").read_text(
        encoding="utf-8"
    )
    run_script = (ROOT / "scripts/run_s2_r0_retry_002.py").read_text(
        encoding="utf-8"
    )
    assert "finalize_s2r0_offline_qualification" in finalize_script
    assert "finalize_s2r0_authorization" in finalize_script
    assert "execute_s2r0_once" not in finalize_script
    assert "execute_s2r0_once" in run_script
    assert "finalize_s2r0_offline_qualification" not in run_script
    assert "finalize_s2r0_authorization" not in run_script
    assert DEFAULT_RUN_ID not in run_script
    assert RETRY_002_RUN_ID in run_script
