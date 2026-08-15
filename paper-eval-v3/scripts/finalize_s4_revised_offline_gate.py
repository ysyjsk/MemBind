#!/usr/bin/env python3
"""Seal the revised S4 offline gate from real files and JUnit evidence."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, sha256_file
from paper_eval.real_workload_correctness_contract import (
    build_real_workload_correctness_contract,
)
from paper_eval.s4_revised_offline_gate import build_revised_s4_offline_gate


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"

PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
AMENDMENT_DOCUMENT = PROJECT / "S4_VALIDATION_BOUNDARY_AMENDMENT_v2.0.md"
AMENDMENT_ARTIFACT = NATIVE / "S4_VALIDATION_BOUNDARY_AMENDMENT.json"
CURRENT_POINTER = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
ROLES = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
DATASET = NATIVE / "DATASET_PARITY.json"
EVALUATOR = NATIVE / "EVALUATOR_PARITY.json"
NATIVE_FREEZE = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
OUTPUT = NATIVE / "S4_REVISED_OFFLINE_GATE.json"

FOCUSED_LOGS = {
    "TR0_SCHEDULING_TRACE_REPLAY": (
        PROJECT / "logs/TDD_FOCUSED_GREEN_S4_TR0_TRACE_REPLAY_20260815.xml"
    ),
    "FX0_DETERMINISTIC_MECHANISM_FIXTURE": (
        PROJECT / "logs/TDD_FOCUSED_GREEN_FX0_MECHANISM_FIXTURE_V2_20260815.xml"
    ),
    "REAL_WORKLOAD_CORRECTNESS": (
        PROJECT / "logs/TDD_FOCUSED_GREEN_REAL_WORKLOAD_CORRECTNESS_20260815.xml"
    ),
    "S4_REVISED_OFFLINE_GATE": (
        PROJECT / "logs/TDD_FOCUSED_GREEN_S4_REVISED_OFFLINE_GATE_20260815.xml"
    ),
}
RED_LOGS = {
    "TR0_SCHEDULING_TRACE_REPLAY": (
        PROJECT / "logs/TDD_RED_S4_TR0_TRACE_REPLAY_20260815.xml"
    ),
    "FX0_DETERMINISTIC_MECHANISM_FIXTURE": (
        PROJECT / "logs/TDD_INTERMEDIATE_RED_FX0_ORACLE_ISOLATION_20260815.xml"
    ),
    "REAL_WORKLOAD_CORRECTNESS": (
        PROJECT / "logs/TDD_RED_REAL_WORKLOAD_CORRECTNESS_20260815.xml"
    ),
    "S4_REVISED_OFFLINE_GATE": (
        PROJECT / "logs/TDD_RED_S4_REVISED_OFFLINE_GATE_20260815.xml"
    ),
}
FULL_LOG = PROJECT / "logs/TDD_FULL_GREEN_S4_REVISED_OFFLINE_GATE_20260815.xml"

SOURCE_PATHS = {
    "tr0_source": PROJECT / "src/paper_eval/s4_tr0_trace_replay.py",
    "tr0_test": PROJECT / "tests/test_s4_tr0_trace_replay.py",
    "fx0_source": PROJECT / "src/paper_eval/fx0_mechanism_fixture.py",
    "fx0_test": PROJECT / "tests/test_fx0_mechanism_fixture.py",
    "fx0_document": PROJECT / "FX0_DETERMINISTIC_MECHANISM_FIXTURE_FRAMEWORK_v1.0.md",
    "real_workload_source": (
        PROJECT / "src/paper_eval/real_workload_correctness_contract.py"
    ),
    "real_workload_test": (
        PROJECT / "tests/test_real_workload_correctness_contract.py"
    ),
    "gate_source": PROJECT / "src/paper_eval/s4_revised_offline_gate.py",
    "gate_test": PROJECT / "tests/test_s4_revised_offline_gate.py",
    "gate_finalizer": Path(__file__),
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _junit(path: Path) -> dict[str, int | str]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        name: sum(int(suite.attrib.get(name, "0")) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }
    return {"junit_file_sha256": sha256_file(path), **counts}


def _real_contract(*, git_commit: str) -> dict:
    return build_real_workload_correctness_contract(
        parent_protocol_file_sha256=sha256_file(PARENT),
        s4_amendment_document_file_sha256=sha256_file(AMENDMENT_DOCUMENT),
        s4_amendment_artifact=_load(AMENDMENT_ARTIFACT),
        s4_amendment_artifact_file_sha256=sha256_file(AMENDMENT_ARTIFACT),
        current_stage_pointer=_load(CURRENT_POINTER),
        current_stage_pointer_file_sha256=sha256_file(CURRENT_POINTER),
        role_registry=_load(ROLES),
        role_registry_file_sha256=sha256_file(ROLES),
        dataset_parity=_load(DATASET),
        dataset_parity_file_sha256=sha256_file(DATASET),
        evaluator_parity=_load(EVALUATOR),
        evaluator_parity_file_sha256=sha256_file(EVALUATOR),
        native_baseline_freeze=_load(NATIVE_FREEZE),
        native_baseline_freeze_file_sha256=sha256_file(NATIVE_FREEZE),
        git_commit=git_commit,
        run_id="real-workload-correctness-contract-20260815-001",
    )


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite sealed artifact: {OUTPUT.name}")
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = build_revised_s4_offline_gate(
        amendment_artifact=_load(AMENDMENT_ARTIFACT),
        amendment_artifact_file_sha256=sha256_file(AMENDMENT_ARTIFACT),
        real_workload_contract=_real_contract(git_commit=git_commit),
        source_file_sha256={
            name: sha256_file(path) for name, path in SOURCE_PATHS.items()
        },
        focused_green_evidence={
            lane: _junit(path) for lane, path in FOCUSED_LOGS.items()
        },
        red_evidence={lane: _junit(path) for lane, path in RED_LOGS.items()},
        full_regression_evidence=_junit(FULL_LOG),
        git_commit=git_commit,
    )
    atomic_write_json(OUTPUT, artifact)
    print(
        json.dumps(
            {
                "artifact_file_sha256": sha256_file(OUTPUT),
                "current_stage": artifact["payload"]["current_stage"],
                "next_action": artifact["payload"]["next_action"],
                "output": str(OUTPUT),
                "s5_live_execution_authorized": artifact["payload"]["authority"][
                    "s5_live_execution_authorized"
                ],
                "status": artifact["payload"]["status"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
