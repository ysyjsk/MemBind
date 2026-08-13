"""Shared offline fixture for sealed Judge test-evidence reports."""

from __future__ import annotations

import unittest
from pathlib import Path

from evaluation.judge_qualification import canonical_json_bytes
from evaluation.judge_qualification_live import build_judge_test_evidence_report


Q3_SCENARIOS = (
    "full_pass",
    "invalid_stop",
    "service_error_stop",
    "tamper",
    "ambiguous_inflight",
)


def _module_name(root: Path, path: Path) -> str:
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _test_count(root: Path, paths: list[Path]) -> int:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames([_module_name(root, path) for path in paths])
    if loader.errors:
        raise AssertionError(f"test evidence fixture did not load: {loader.errors!r}")
    return suite.countTestCases()


def write_test_evidence_reports(directory: Path, root: Path) -> dict[str, Path]:
    """Create canonical reports backed by exact-count synthetic unittest logs."""

    sources = {
        "focused": sorted(
            path
            for path in root.joinpath("tests").glob("test_judge*.py")
            if path.is_file() and not path.is_symlink()
        ),
        "impact": [
            root / "tests/test_evaluator_registry.py",
            root / "tests/test_longmemeval_adapter.py",
            root / "tests/test_qwen3_judge_backend.py",
        ],
        "q3": [
            root / "src/evaluation/judge_qualification_q3.py",
            root / "tests/test_judge_qualification_q3_dry_run.py",
        ],
    }
    reports: dict[str, Path] = {}
    for suite_id, suite_sources in sources.items():
        test_sources = suite_sources[1:] if suite_id == "q3" else suite_sources
        count = _test_count(root, test_sources)
        raw_log = directory / f"{suite_id}.log"
        raw_log.write_text(
            "machine-evidence suite completed\n"
            "----------------------------------------------------------------------\n"
            f"Ran {count} tests in 0.001s\n\n"
            "OK\n",
            encoding="ascii",
        )
        q3_summary = None
        if suite_id == "q3":
            q3_summary = {
                "schema_version": "membind.judge-q3-dry-run-summary.v1",
                "status": "GREEN",
                "scenarios": list(Q3_SCENARIOS),
                "real_external_requests": 0,
                "live_authorization_created": False,
            }
        report = build_judge_test_evidence_report(
            validation_root=root,
            suite_id=suite_id,
            status="GREEN",
            exit_code=0,
            test_count=count,
            source_paths=[path.relative_to(root) for path in suite_sources],
            raw_log_path=raw_log.relative_to(root),
            q3_summary=q3_summary,
        )
        report_path = directory / f"final-{suite_id}-evidence.json"
        report_path.write_bytes(canonical_json_bytes(report) + b"\n")
        reports[suite_id] = report_path
    return reports
