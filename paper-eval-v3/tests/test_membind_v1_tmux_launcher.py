"""TDD safety contract for the isolated MemBind-v1 tmux launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


PROJECT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT / "scripts/run_membind_v1_tmux.sh"
SENTINEL = "sentinel-secret-must-not-be-printed"


def _fixture(tmp_path: Path, *, existing_session: bool = False) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ledger = tmp_path / "tmux-ledger.txt"
    tmux = bin_dir / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_TMUX_LEDGER\"\n"
        "if [[ \"${1:-}\" == 'has-session' ]]; then\n"
        "  [[ \"${FAKE_TMUX_HAS_SESSION:-0}\" == '1' ]] && exit 0\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    python = tmp_path / "fake-python"
    python.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_TMUX_LEDGER": str(ledger),
        "FAKE_TMUX_HAS_SESSION": "1" if existing_session else "0",
        "MEMBIND_V1_PYTHON": str(python),
        "OPENAI_API_KEY": SENTINEL,
        "QWEN_API_KEY": SENTINEL,
    }
    return env, ledger


def _run(
    tmp_path: Path,
    aligned_run_id: str,
    main_table_run_id: str,
    *,
    existing_session: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    env, ledger = _fixture(tmp_path, existing_session=existing_session)
    completed = subprocess.run(
        ["bash", str(LAUNCHER), aligned_run_id, main_table_run_id],
        cwd=PROJECT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, ledger


def test_launcher_starts_one_detached_secret_free_append_logged_runner(tmp_path: Path) -> None:
    aligned_run_id = "aligned-dev-20260817-001"
    main_table_run_id = "main-table-dev-20260817-001"

    completed, ledger = _run(tmp_path, aligned_run_id, main_table_run_id)

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout + completed.stderr
    calls = ledger.read_text(encoding="utf-8").splitlines()
    detached = [call for call in calls if call.startswith("new-session -d")]
    assert len(detached) == 1
    command = detached[0]
    assert f"membind-v1-{aligned_run_id}" in command
    assert "PYTHONUNBUFFERED=1" in command
    assert "set -o pipefail" in command
    assert "scripts/run_membind_v1.py" in command
    assert f"--aligned-run-id '{aligned_run_id}'" in command
    assert f"--main-table-run-id '{main_table_run_id}'" in command
    assert "tee -a" in command
    assert SENTINEL not in output
    assert SENTINEL not in "\n".join(calls)

    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.count("tmux new-session -d") == 1
    assert source.count("scripts/run_membind_v1.py") == 1
    assert "tmux has-session" in source
    assert "PYTHONUNBUFFERED=1" in source
    assert "tee -a" in source
    assert "eval " not in source
    assert "printenv" not in source
    assert "api-key" not in source.casefold()
    assert "OPENAI_API_KEY" not in source
    assert "QWEN_API_KEY" not in source


@pytest.mark.parametrize(
    "aligned_run_id, main_table_run_id",
    [
        ("../aligned-dev-001", "main-table-dev-001"),
        ("aligned-dev-001", "../main-table-dev-001"),
        ("aligned-dev-001;touch-x", "main-table-dev-001"),
        ("aligned-dev-001", "main-table-dev-001/child"),
        ("ALIGNED-dev-001", "main-table-dev-001"),
    ],
)
def test_launcher_rejects_path_unsafe_ids_before_tmux(
    tmp_path: Path,
    aligned_run_id: str,
    main_table_run_id: str,
) -> None:
    completed, ledger = _run(tmp_path, aligned_run_id, main_table_run_id)

    assert completed.returncode != 0
    assert "invalid" in completed.stderr.casefold()
    assert not ledger.exists()
    assert SENTINEL not in completed.stdout + completed.stderr


def test_launcher_rejects_existing_session_without_starting_a_second_parent(
    tmp_path: Path,
) -> None:
    completed, ledger = _run(
        tmp_path,
        "aligned-dev-20260817-001",
        "main-table-dev-20260817-001",
        existing_session=True,
    )

    assert completed.returncode != 0
    assert "session already exists" in completed.stderr.casefold()
    calls = ledger.read_text(encoding="utf-8").splitlines()
    assert any(call.startswith("has-session") for call in calls)
    assert not any(call.startswith("new-session") for call in calls)
    assert SENTINEL not in completed.stdout + completed.stderr
    assert SENTINEL not in "\n".join(calls)
