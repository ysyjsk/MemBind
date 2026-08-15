"""TDD contracts for sealing the offline production FX0 qualification."""

from __future__ import annotations

from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s5_graphiti_fx0_finalizer import (
    S5GraphitiFx0FinalizerError,
    finalize_s5_graphiti_fx0_qualification,
    verify_s5_graphiti_fx0_qualification,
)
from paper_eval.s5_mstar_fx0_artifact import verify_s5_mstar_fx0_artifact


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent


def test_finalizer_seals_real_closure_without_changing_current_pointer(
    tmp_path: Path,
) -> None:
    pointer = ROOT / "runtime" / "CURRENT_STAGE_STATUS.json"
    pointer_before = sha256_file(pointer)
    paths = {
        "runtime_config_path": tmp_path / "runtime-config.json",
        "core_identity_path": tmp_path / "core-identity.json",
        "fx0_artifact_path": tmp_path / "fx0-parity.json",
        "qualification_path": tmp_path / "qualification.json",
    }

    result = finalize_s5_graphiti_fx0_qualification(
        paper_eval_root=ROOT,
        workspace_root=WORKSPACE,
        git_commit="deadbeef",
        run_id="s5-graphiti-fx0-finalizer-test",
        full_regression_log=ROOT
        / "logs"
        / "TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_FX0_PRODUCTION_PARITY_20260815.xml",
        expected_full_test_count=1150,
        **paths,
    )

    assert sha256_file(pointer) == pointer_before
    assert result["payload"]["verdict"] == "PRODUCTION_PATH_EXACT_PARITY_PASS"
    assert result["payload"]["fixture_count"] == 11
    assert result["payload"]["authority"] == {
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
        "neo4j_mutation_authorized": False,
        "s5_live_execution_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }
    verified = verify_s5_graphiti_fx0_qualification(
        result,
        expected_current_stage_pointer_sha256=pointer_before,
    )
    artifact = verified["artifacts"]["fx0_artifact"]
    verify_s5_mstar_fx0_artifact(
        artifact,
        expected_input_bindings=artifact["payload"]["input_bindings"],
        expected_fixture_manifest_sha256=artifact["payload"]["input_bindings"][
            "fx0_fixture_manifest_sha256"
        ],
    )
    assert all(path.is_file() for path in paths.values())

    with pytest.raises(S5GraphitiFx0FinalizerError, match="output_exists"):
        finalize_s5_graphiti_fx0_qualification(
            paper_eval_root=ROOT,
            workspace_root=WORKSPACE,
            git_commit="deadbeef",
            run_id="s5-graphiti-fx0-finalizer-test-retry",
            full_regression_log=ROOT
            / "logs"
            / "TDD_FULL_OFFLINE_GREEN_S5_GRAPHITI_FX0_PRODUCTION_PARITY_20260815.xml",
            expected_full_test_count=1150,
            **paths,
        )
