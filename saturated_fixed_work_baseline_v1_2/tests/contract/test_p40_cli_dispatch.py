from __future__ import annotations

from pathlib import Path

from saturated_fixed_work_baseline_v1_2.cli import _guarded_stage


def test_unblocked_cli_dispatches_real_stage_workflow(
    tmp_path: Path, capsys
) -> None:
    calls: list[Path] = []

    def workflow(root: Path) -> dict[str, object]:
        calls.append(root)
        return {"status": "PASS", "stage": "L3", "valid_construction_blocks": 8}

    code = _guarded_stage("run-main", tmp_path, workflows={"run-main": workflow})

    assert code == 0
    assert calls == [tmp_path.resolve()]
    output = capsys.readouterr().out
    assert '"status": "PASS"' in output
    assert '"valid_construction_blocks": 8' in output


def test_unblocked_cli_reports_stage_failure_without_false_completion(
    tmp_path: Path, capsys
) -> None:
    def workflow(root: Path) -> dict[str, object]:
        del root
        raise ValueError("QUALIFICATION_NOT_VERIFIED")

    code = _guarded_stage(
        "run-main", tmp_path, workflows={"run-main": workflow}
    )

    assert code == 3
    output = capsys.readouterr().out
    assert '"status": "NOT_READY"' in output
    assert "QUALIFICATION_NOT_VERIFIED" in output
    assert '"status": "COMPLETE"' not in output


def test_live_wrappers_use_graphiti_runtime_venv_and_report_stays_isolated(
    repository_root: Path,
) -> None:
    scripts = repository_root / "saturated_fixed_work_baseline_v1_2/scripts"
    for name in ("preflight.sh", "run_qualification.sh", "run_main.sh", "run_qa.sh"):
        source = (scripts / name).read_text(encoding="utf-8")
        assert "membind-validation/.venv/bin/python" in source
        assert "paper-eval-v3/.venv/bin/python" not in source
    report = (scripts / "build_report.sh").read_text(encoding="utf-8")
    assert "paper-eval-v3/.venv/bin/python" in report
