from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


WRAPPERS = (
    "preflight.sh",
    "run_qualification.sh",
    "run_main.sh",
    "run_qa.sh",
    "build_report.sh",
)


@pytest.mark.parametrize("name", WRAPPERS)
def test_live_wrappers_preserve_active_external_stop_without_writes(
    repository_root: Path, name: str
) -> None:
    protocol_root = repository_root / "saturated_fixed_work_baseline_v1_2"
    run_root = protocol_root / "artifacts/sfwb-v1-2-dev-20260821-001"
    script = protocol_root / "scripts" / name
    assert script.is_file()
    assert script.stat().st_mode & 0o111
    before = sorted(
        str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
    )

    result = subprocess.run(
        [str(script), "--run-root", str(run_root)],
        cwd=protocol_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    assert '"status": "BLOCKED_EXTERNAL_RESOURCE_IDENTITY"' in result.stdout
    after = sorted(
        str(path.relative_to(run_root)) for path in run_root.rglob("*") if path.is_file()
    )
    assert after == before
    assert not (run_root / "preflight").exists()
    assert not (run_root / "qualification").exists()
    assert not (run_root / "blocks").exists()
    assert not (run_root / "qa").exists()
    assert not (run_root / "FINAL_SEAL.json").exists()


def test_wrappers_use_only_protocol_cli_and_stage_appropriate_venv(
    repository_root: Path,
) -> None:
    scripts = repository_root / "saturated_fixed_work_baseline_v1_2/scripts"
    for name in WRAPPERS:
        source = (scripts / name).read_text(encoding="utf-8")
        expected = (
            "paper-eval-v3/.venv/bin/python"
            if name == "build_report.sh"
            else "membind-validation/.venv/bin/python"
        )
        assert expected in source
        assert "saturated_fixed_work_baseline_v1_2.cli" in source
        assert "8002" not in source and "8003" not in source
        assert "membind_v5_oracle" not in source
