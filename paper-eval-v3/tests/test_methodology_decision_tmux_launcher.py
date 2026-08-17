"""RED-first contract for deferred methodology-decision finalization."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_methodology_decision_after_report_tmux.sh"


def _fake_tmux(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger = tmp_path / "tmux-ledger.txt"
    executable = bin_dir / "tmux"
    executable.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_TMUX_LEDGER\"\n"
        "if [[ \"${1:-}\" == 'has-session' ]]; then exit 1; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return bin_dir, ledger


def test_launcher_waits_for_report_in_one_secret_free_tmux(tmp_path: Path) -> None:
    bin_dir, ledger = _fake_tmux(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_TMUX_LEDGER": str(ledger),
        "OPENAI_API_KEY": "sentinel-secret-must-not-be-printed",
    }
    completed = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "methodology-dev-20260817-001",
            "report-dev-20260817-001",
        ],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout + completed.stderr
    assert "sentinel-secret-must-not-be-printed" not in output
    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.count("tmux new-session -d") == 1
    assert "REPORT.json" in source
    assert "finalize_methodology_decision.py" in source
    assert "METHODOLOGY_DECISION.json" in source
    assert "tee -a" in source
    assert "rm -" not in source
    calls = ledger.read_text(encoding="utf-8").splitlines()
    detached = [value for value in calls if value.startswith("new-session -d")]
    assert len(detached) == 1
    assert "membind-methodology-decision-methodology-dev-20260817-001" in (
        detached[0]
    )


def test_launcher_rejects_unsafe_identity_before_tmux(tmp_path: Path) -> None:
    bin_dir, ledger = _fake_tmux(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_TMUX_LEDGER": str(ledger),
    }
    completed = subprocess.run(
        ["bash", str(LAUNCHER), "../unsafe", "report-dev-20260817-001"],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid" in completed.stderr.lower()
    assert not ledger.exists()
