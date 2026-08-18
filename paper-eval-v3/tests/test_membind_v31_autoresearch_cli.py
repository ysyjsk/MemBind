"""CLI smoke tests for offline autoresearch materialization."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
PYTHON = PROJECT / ".venv/bin/python"
SCRIPT = PROJECT / "scripts/run_membind_v31_autoresearch.py"
FORMAL = PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json"
BASELINE = (
    PROJECT
    / "artifacts/paper_eval/apc_aligned_baseline/runs/"
    "apc-baseline-dev-20260817-001/blocks/block-00/APC_ALIGNED_BLOCK_RESULT.json"
)


def test_cli_plan_is_offline_and_materializes_non_mergeable_probe(tmp_path: Path) -> None:
    probe_root = tmp_path / "probe"
    result = subprocess.run(
        [
            str(PYTHON),
            str(SCRIPT),
            "plan",
            "--formal-plan",
            str(FORMAL),
            "--baseline-result",
            str(BASELINE),
            "--probe-root",
            str(probe_root),
            "--probe-run-id",
            "membind-v31-ar-test-c00",
            "--candidate-id",
            "c00",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    output = json.loads(result.stdout)
    assert output["status"] == "PASS"
    assert json.loads((probe_root / "PROGRAM.json").read_text())["merge_authority"] == (
        "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"
    )
    assert json.loads((probe_root / "PROBE_AUTHORIZATION.json").read_text())[
        "heldout_data_accessed"
    ] is False
    assert not (probe_root / "results.tsv").exists()
