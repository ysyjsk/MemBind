#!/usr/bin/env python3
"""Exclusively seal the offline-only S4 validation-boundary amendment."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, sha256_file
from paper_eval.s4_validation_boundary_amendment import (
    build_s4_validation_boundary_amendment,
    verify_s4_validation_boundary_amendment,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
CAPTURE = NATIVE / "runs/s4-d0-capture-20260815-008"
REPLAY = NATIVE / "runs/s4-d0-replay-20260815-008"
LOG = PROJECT / "logs/S4_D0_SIDECAR_SMOKE_20260815_008.log"
PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
LEGACY_WORKPLAN = PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"
CURRENT_STAGE = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
AMENDMENT = PROJECT / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md"
FINAL_SMOKE = NATIVE / "S4_D0_SIDECAR_SMOKE_RESULT_RETRY_008.json"
ACTIVATION_V3 = NATIVE / "S4_D0_QUALIFICATION_ACTIVATION_SIDECAR_V3.json"
RED = PROJECT / "logs/TDD_RED_S4_VALIDATION_BOUNDARY_AMENDMENT_20260815.xml"
FOCUSED = (
    PROJECT / "logs/TDD_FOCUSED_GREEN_S4_VALIDATION_BOUNDARY_AMENDMENT_20260815.xml"
)
FULL = PROJECT / "logs/TDD_FULL_GREEN_S4_VALIDATION_BOUNDARY_AMENDMENT_20260815.xml"
OUTPUT = NATIVE / "S4_VALIDATION_BOUNDARY_AMENDMENT.json"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path.name}")
    return value


def _junit_counts(path: Path) -> tuple[int, int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return tuple(
        sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    )


def _offline_evidence() -> dict[str, int | str]:
    red_tests, red_failures, red_errors, _red_skipped = _junit_counts(RED)
    focused_tests, focused_failures, focused_errors, focused_skipped = (
        _junit_counts(FOCUSED)
    )
    full_tests, full_failures, full_errors, full_skipped = _junit_counts(FULL)
    if red_tests <= 0 or red_failures + red_errors <= 0:
        raise RuntimeError("RED evidence does not contain an expected failure")
    if (
        focused_tests < 9
        or focused_failures
        or focused_errors
        or focused_skipped
    ):
        raise RuntimeError("focused GREEN evidence is not a complete pass")
    if full_tests < 800 or full_failures or full_errors or full_skipped:
        raise RuntimeError("full GREEN evidence is not a complete pass")
    return {
        "red_junit_sha256": sha256_file(RED),
        "red_failure_or_error_count": red_failures + red_errors,
        "focused_green_junit_sha256": sha256_file(FOCUSED),
        "focused_green_pass_count": focused_tests,
        "full_green_junit_sha256": sha256_file(FULL),
        "full_green_pass_count": full_tests,
    }


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite sealed artifact: {OUTPUT.name}")
    artifact = build_s4_validation_boundary_amendment(
        capture_result=_load(CAPTURE / "phase_result.json"),
        capture_result_file_sha256=sha256_file(CAPTURE / "phase_result.json"),
        capture_checkpoint=_load(CAPTURE / "checkpoint.json"),
        capture_checkpoint_file_sha256=sha256_file(CAPTURE / "checkpoint.json"),
        capture_events_file_sha256=sha256_file(CAPTURE / "events.jsonl"),
        replay_result=_load(REPLAY / "phase_result.json"),
        replay_result_file_sha256=sha256_file(REPLAY / "phase_result.json"),
        replay_checkpoint=_load(REPLAY / "checkpoint.json"),
        replay_checkpoint_file_sha256=sha256_file(REPLAY / "checkpoint.json"),
        replay_events_file_sha256=sha256_file(REPLAY / "events.jsonl"),
        execution_log=LOG.read_text(encoding="utf-8"),
        execution_log_file_sha256=sha256_file(LOG),
        final_smoke_result_exists=FINAL_SMOKE.exists(),
        activation_v3_exists=ACTIVATION_V3.exists(),
        parent_protocol_sha256=sha256_file(PARENT),
        legacy_workplan_sha256=sha256_file(LEGACY_WORKPLAN),
        current_stage_pointer_sha256=sha256_file(CURRENT_STAGE),
        amendment_document_sha256=sha256_file(AMENDMENT),
        offline_evidence=_offline_evidence(),
        source_sha256={
            "amendment_source": sha256_file(
                PROJECT / "src/paper_eval/s4_validation_boundary_amendment.py"
            ),
            "amendment_test": sha256_file(
                PROJECT / "tests/test_s4_validation_boundary_amendment.py"
            ),
            "finalizer": sha256_file(Path(__file__)),
        },
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
    )
    verify_s4_validation_boundary_amendment(artifact)
    atomic_write_json(OUTPUT, artifact)
    print(
        json.dumps(
            {
                "artifact_file_sha256": sha256_file(OUTPUT),
                "decision": artifact["payload"]["decision"],
                "model_call_authorized": artifact["payload"]["authority"][
                    "model_call_authorized"
                ],
                "neo4j_mutation_authorized": artifact["payload"]["authority"][
                    "neo4j_mutation_authorized"
                ],
                "output": str(OUTPUT),
                "run_id": artifact["run_id"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
