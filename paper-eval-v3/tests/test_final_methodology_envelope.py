"""TDD contracts for the final, documentation-only methodology envelope."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.final_methodology_envelope import (
    FinalMethodologyEnvelopeError,
    build_final_methodology_envelope,
    finalize_final_methodology_envelope,
)

PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
REAL_INPUTS = {
    "baseline": PROJECT
    / "artifacts/paper_eval/baseline_suite/runs/bs-dev-20260816-001/THREE_BASELINE_RESULTS.json",
    "overlay": PROJECT
    / "artifacts/paper_eval/graph_quality_overlay/runs/gq-dev-20260817-001/GRAPH_QUALITY_RESULTS.json",
    "report": PROJECT
    / "artifacts/paper_eval/development_report/runs/report-dev-20260817-001/REPORT.json",
    "decision": PROJECT
    / "artifacts/paper_eval/methodology_finalization/runs/methodology-dev-20260817-001/METHODOLOGY_DECISION.json",
    "document": REPOSITORY / "主methodology设计.md",
}


def _inputs(root: Path) -> dict[str, Path]:
    baseline = root / "THREE_BASELINE_RESULTS.json"
    overlay = root / "GRAPH_QUALITY_RESULTS.json"
    report = root / "REPORT.json"
    decision = root / "METHODOLOGY_DECISION.json"
    document = root / "主methodology设计.md"
    junit = root / "final.xml"
    source_code = root / "impl.py"
    for key, destination in (
        ("baseline", baseline),
        ("overlay", overlay),
        ("report", report),
        ("decision", decision),
        ("document", document),
    ):
        shutil.copyfile(REAL_INPUTS[key], destination)
    junit.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="12" '
        'errors="0" failures="0" skipped="0" /></testsuites>\n',
        encoding="utf-8",
    )
    source_code.write_text("VALUE = 1\n", encoding="utf-8")
    return {
        "baseline": baseline,
        "overlay": overlay,
        "report": report,
        "decision": decision,
        "document": document,
        "junit": junit,
        "source_code": source_code,
    }


def test_envelope_binds_all_sources_and_is_sealed(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    envelope = build_final_methodology_envelope(
        repository_root=tmp_path,
        baseline_path=paths["baseline"],
        overlay_path=paths["overlay"],
        report_path=paths["report"],
        decision_path=paths["decision"],
        methodology_path=paths["document"],
        junit_path=paths["junit"],
        source_code_paths=(paths["source_code"],),
    )

    assert envelope["status"] == "PASS"
    assert envelope["authority_effect"] == (
        "DOCUMENTATION_ONLY_NO_LIVE_AUTHORITY"
    )
    assert envelope["scope"] == "DEVELOPMENT_EXPOSED_DESCRIPTIVE_ONLY"
    assert envelope["tdd"]["tests"] == 12
    assert envelope["tdd"]["passed"] == 12
    assert envelope["sources"]["three_baselines"]["run_id"] == (
        "bs-dev-20260816-001"
    )
    assert envelope["sources"]["three_baselines"]["schema_version"] == (
        "membind.paper-eval-v3.three-baseline-report.v1"
    )
    assert set(envelope["source_code_sha256"]) == {"impl.py"}
    assert envelope["payload_sha256"] == payload_sha256(
        {key: value for key, value in envelope.items() if key != "payload_sha256"}
    )


def test_envelope_rejects_source_payload_tamper(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    paths["overlay"].write_text(
        paths["overlay"].read_text(encoding="utf-8").replace(
            '"status": "PASS"', '"status": "FAIL"', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(FinalMethodologyEnvelopeError, match="overlay payload seal"):
        build_final_methodology_envelope(
            repository_root=tmp_path,
            baseline_path=paths["baseline"],
            overlay_path=paths["overlay"],
            report_path=paths["report"],
            decision_path=paths["decision"],
            methodology_path=paths["document"],
            junit_path=paths["junit"],
            source_code_paths=(paths["source_code"],),
        )


def test_file_finalizer_is_idempotent_and_rejects_output_drift(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    output = tmp_path / "FINAL_METHODOLOGY_ENVELOPE.json"
    first = finalize_final_methodology_envelope(
        repository_root=tmp_path,
        baseline_path=paths["baseline"],
        overlay_path=paths["overlay"],
        report_path=paths["report"],
        decision_path=paths["decision"],
        methodology_path=paths["document"],
        junit_path=paths["junit"],
        source_code_paths=(paths["source_code"],),
        output_path=output,
    )
    original = output.read_bytes()
    second = finalize_final_methodology_envelope(
        repository_root=tmp_path,
        baseline_path=paths["baseline"],
        overlay_path=paths["overlay"],
        report_path=paths["report"],
        decision_path=paths["decision"],
        methodology_path=paths["document"],
        junit_path=paths["junit"],
        source_code_paths=(paths["source_code"],),
        output_path=output,
    )
    assert second == first
    assert output.read_bytes() == original

    tampered = json.loads(original)
    tampered["authority_effect"] = "LIVE_AUTHORITY"
    output.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(FinalMethodologyEnvelopeError, match="existing envelope"):
        finalize_final_methodology_envelope(
            repository_root=tmp_path,
            baseline_path=paths["baseline"],
            overlay_path=paths["overlay"],
            report_path=paths["report"],
            decision_path=paths["decision"],
            methodology_path=paths["document"],
            junit_path=paths["junit"],
            source_code_paths=(paths["source_code"],),
            output_path=output,
        )


def test_envelope_rejects_failed_junit(tmp_path: Path) -> None:
    paths = _inputs(tmp_path)
    paths["junit"].write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="12" '
        'errors="0" failures="1" skipped="0" /></testsuites>\n',
        encoding="utf-8",
    )
    with pytest.raises(FinalMethodologyEnvelopeError, match="JUnit"):
        build_final_methodology_envelope(
            repository_root=tmp_path,
            baseline_path=paths["baseline"],
            overlay_path=paths["overlay"],
            report_path=paths["report"],
            decision_path=paths["decision"],
            methodology_path=paths["document"],
            junit_path=paths["junit"],
            source_code_paths=(paths["source_code"],),
        )
