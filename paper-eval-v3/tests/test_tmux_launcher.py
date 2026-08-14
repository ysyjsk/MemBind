from __future__ import annotations

import subprocess
from pathlib import Path


def test_tmux_launcher_rejects_path_traversal_ids(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_s1_tmux.sh"
    result = subprocess.run(
        ["bash", str(script), "../escape", "safe"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid run id" in result.stderr


def test_tmux_launcher_isolated_command_does_not_source_or_print_secrets() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "run_s1_tmux.sh").read_text()
    assert "source .env" not in script
    assert "API_KEY" not in script
    assert "membind-validation/.venv/bin/python" in script
    assert "paper_eval.s1_controller" in script

