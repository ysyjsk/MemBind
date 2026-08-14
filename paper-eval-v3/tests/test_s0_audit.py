from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.s0_audit import build_s0_artifacts


def test_s0_writes_three_finalized_artifacts_without_secrets(tmp_path: Path) -> None:
    result = build_s0_artifacts(
        repo_root=tmp_path / "repo",
        protocol_path=tmp_path / "workplan.md",
        output_root=tmp_path / "artifacts",
        dataset_path=tmp_path / "dataset.json",
        exposed_ids={"calibration-1"},
        git_commit="deadbeef",
        working_tree_status="dirty",
        generated_at="2026-08-13T00:00:00Z",
        planned_or_identity_only_ids={"planned-1"},
        exposure_evidence={"calibration-1": ["old/result.json"]},
    )
    assert set(result) == {
        "S0_CURRENT_STATE.json",
        "S0_REUSE_AUDIT.json",
        "DEVELOPMENT_EXPOSED_IDS.json",
    }
    for name in result:
        payload = json.loads((tmp_path / "artifacts" / name).read_text())
        assert payload["status"] == "finalized"
        assert payload["protocol_version"] == "paper-eval-v3"
        assert payload["git_commit"] == "deadbeef"
        assert len(payload["payload_sha256"]) == 64
        serialized = json.dumps(payload, sort_keys=True).lower()
        assert "api_key" not in serialized
        assert "authorization" not in serialized
        assert "prompt" not in serialized

    reuse = result["S0_REUSE_AUDIT.json"]["payload"]
    assert reuse["c2_exact_u0_reuse_decision"] == "DEFERRED_TO_S2"
    assert reuse["c6_scheduled"] is False
    current = result["S0_CURRENT_STATE.json"]["payload"]
    assert current["generated_at"] == "2026-08-13T00:00:00Z"
    roles = result["DEVELOPMENT_EXPOSED_IDS.json"]["payload"]
    assert roles["actual_outcome_exposed_ids"] == ["calibration-1"]
    assert roles["planned_or_identity_only_seen_ids"] == ["planned-1"]
    assert roles["exposure_evidence"] == {"calibration-1": ["old/result.json"]}


def test_s0_rejects_overlapping_data_roles(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="overlap"):
        build_s0_artifacts(
            repo_root=tmp_path / "repo",
            protocol_path=tmp_path / "workplan.md",
            output_root=tmp_path / "artifacts",
            dataset_path=tmp_path / "dataset.json",
            exposed_ids={"same"},
            pilot_ids={"same"},
            git_commit="deadbeef",
        )
