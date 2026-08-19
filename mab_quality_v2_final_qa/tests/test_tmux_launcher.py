from __future__ import annotations

from pathlib import Path

import pytest

from mab_quality_v2_final_qa.tmux_launcher import build_tmux_launch


def test_tmux_launch_is_detached_and_artifact_root_is_isolated(tmp_path: Path) -> None:
    owned = tmp_path / "mab_quality_v2_final_qa" / "artifacts" / "run"
    launch = build_tmux_launch(
        session_name="mabqv2-4hist-20260819",
        project_root=tmp_path / "mab_quality_v2_final_qa",
        artifact_root=owned,
        dataset_path=tmp_path / "official.json",
        run_id="mabqv2-4hist-20260819",
        history_limit=4,
    )
    assert launch.argv[:4] == ("tmux", "new-session", "-d", "-s")
    assert launch.session_name == "mabqv2-4hist-20260819"
    assert str(owned) in launch.command
    assert "paper-eval-v3/artifacts" not in launch.command


def test_tmux_launch_rejects_historical_artifact_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ARTIFACT_ROOT_NOT_OWNED"):
        build_tmux_launch(
            session_name="mabqv2-run",
            project_root=tmp_path / "mab_quality_v2_final_qa",
            artifact_root=tmp_path / "paper-eval-v3" / "artifacts" / "run",
            dataset_path=tmp_path / "official.json",
            run_id="mabqv2-run",
            history_limit=4,
        )
