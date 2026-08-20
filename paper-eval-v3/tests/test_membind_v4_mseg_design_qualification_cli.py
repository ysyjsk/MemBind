from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_mseg_design_qualification.py"


def test_design_qualification_cli_is_offline_and_fail_closed(tmp_path: Path) -> None:
    output = tmp_path / "design-only"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output-root",
            str(output),
        ],
        cwd=PROJECT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    decision = json.loads((output / "MSEG_DESIGN_QUALIFICATION.json").read_text())
    assert decision["synthetic"]["decision"] == "GO_OFFLINE_CERTIFIED"
    assert decision["synthetic"]["reorder_counts"]["CERTIFIED"] == 1
    assert decision["real_trace"]["mseg_recovered"] is False
    assert decision["decision"]["status"] == (
        "STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY"
    )
    assert decision["decision"]["live_authorized"] is False
    assert decision["decision"]["new_scheduler_authorized"] is False
    assert "memory_version_evidence_missing" in decision["decision"]["reasons"]

    markdown = (output / "MSEG_DESIGN_QUALIFICATION.md").read_text()
    assert "STOP_REAL_TRACE_INSUFFICIENT_OBSERVABILITY" in markdown
    assert "No live service was contacted" in markdown

