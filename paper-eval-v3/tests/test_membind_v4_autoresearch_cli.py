"""Offline admission checks for the v4 autoresearch CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
PYTHON = PROJECT / ".venv/bin/python"
SCRIPT = PROJECT / "scripts/run_membind_v4_autoresearch.py"


def test_cli_rejects_direct_twelve_source_before_creating_candidate(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "candidate"

    result = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPT),
            "--mode",
            "fixture",
            "--candidate",
            "c01",
            "--history-id",
            "07741c45",
            "--source-count",
            "12",
            "--output-root",
            str(output_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "prior_six_reduction_required" in result.stderr
    assert not (output_root / "candidates").exists()


def test_cli_rejects_policy_drift_before_creating_candidate(tmp_path: Path) -> None:
    output_root = tmp_path / "candidate"

    result = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPT),
            "--mode",
            "fixture",
            "--candidate",
            "c01",
            "--policy",
            "DRIFTED_POLICY",
            "--source-count",
            "6",
            "--output-root",
            str(output_root),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "candidate_policy_drift" in result.stderr
    assert not (output_root / "candidates").exists()
