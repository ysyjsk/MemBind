"""RED safety and CLI contracts for the one-parent baseline tmux launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_baseline_suite_tmux.sh"


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


def _environment(bin_dir: Path, ledger: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "FAKE_TMUX_LEDGER": str(ledger),
            # A launcher may inherit this secret for the child runner, but it
            # must never interpolate it into its command or output.
            "OPENAI_API_KEY": "sentinel-secret-must-not-be-printed",
        }
    )
    return env


def test_launcher_is_one_detached_parent_session_with_append_only_log() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert source.count("tmux new-session -d") == 1
    assert "tmux has-session" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert "scripts/run_three_baselines.py" in source
    assert "--mode" not in source
    assert "--reuse-u0-run" in source
    assert "tee -a" in source
    assert "rm -" not in source
    assert "api-key" not in source.lower()
    assert "sentinel-secret" not in source


def test_launcher_forwards_required_u0_reference_to_small_runner(
    tmp_path: Path,
) -> None:
    bin_dir, ledger = _fake_tmux(tmp_path)
    completed = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "bs-20260816-001",
            "--reuse-u0-run",
            "nb-20260816-001",
        ],
        cwd=PROJECT,
        env=_environment(bin_dir, ledger),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "sentinel-secret-must-not-be-printed" not in (
        completed.stdout + completed.stderr
    )
    calls = ledger.read_text(encoding="utf-8").splitlines()
    detached = [line for line in calls if line.startswith("new-session -d")]
    assert len(detached) == 1
    assert "membind-three-baselines-bs-20260816-001" in detached[0]
    assert "scripts/run_three_baselines.py" in detached[0]
    assert "--reuse-u0-run nb-20260816-001" in detached[0]
    assert "tee -a" in detached[0]


def test_launcher_rejects_path_unsafe_run_id_before_calling_tmux(
    tmp_path: Path,
) -> None:
    bin_dir, ledger = _fake_tmux(tmp_path)
    completed = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "../unsafe",
            "--reuse-u0-run",
            "nb-20260816-001",
        ],
        cwd=PROJECT,
        env=_environment(bin_dir, ledger),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "invalid run id" in completed.stderr.lower()
    assert not ledger.exists()
    assert "sentinel-secret-must-not-be-printed" not in (
        completed.stdout + completed.stderr
    )
