#!/usr/bin/env python3
"""Seal the additive P* real-workload role clarification from real inputs."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, sha256_file
from paper_eval.p_star_real_workload_role_amendment import (
    build_p_star_real_workload_role_amendment,
    verify_p_star_real_workload_role_amendment,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"

PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
S4_GATE = NATIVE / "S4_REVISED_OFFLINE_GATE.json"
S5_PLAN = NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json"
CURRENT = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
DOCUMENT = PROJECT / "P_STAR_REAL_WORKLOAD_CORRECTNESS_ROLE_AMENDMENT_v1.0.md"
TEST = PROJECT / "tests/test_p_star_real_workload_role_amendment.py"
SOURCE = PROJECT / "src/paper_eval/p_star_real_workload_role_amendment.py"
RED = PROJECT / "logs/TDD_RED_P_STAR_REAL_WORKLOAD_ROLE_AMENDMENT_20260815.xml"
FOCUSED = (
    PROJECT
    / "logs/TDD_FOCUSED_GREEN_P_STAR_REAL_WORKLOAD_ROLE_AMENDMENT_20260815.xml"
)
OUTPUT = (
    PROJECT
    / "artifacts/paper_eval/methods/"
    / "P_STAR_REAL_WORKLOAD_CORRECTNESS_ROLE_AMENDMENT.json"
)

SOURCE_PATHS = {
    "amendment_source": SOURCE,
    "amendment_test": TEST,
    "amendment_document": DOCUMENT,
    "amendment_finalizer": Path(__file__),
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


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite sealed artifact: {OUTPUT.name}")
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = build_p_star_real_workload_role_amendment(
        parent_protocol_file_sha256=sha256_file(PARENT),
        s4_revised_gate=_load(S4_GATE),
        s4_revised_gate_file_sha256=sha256_file(S4_GATE),
        s5_method_plan=_load(S5_PLAN),
        s5_method_plan_file_sha256=sha256_file(S5_PLAN),
        current_stage_pointer=_load(CURRENT),
        current_stage_pointer_file_sha256=sha256_file(CURRENT),
        source_file_sha256={
            name: sha256_file(path) for name, path in SOURCE_PATHS.items()
        },
        red_evidence=_junit(RED),
        focused_green_evidence=_junit(FOCUSED),
        git_commit=git_commit,
    )
    atomic_write_json(OUTPUT, artifact)

    persisted = verify_p_star_real_workload_role_amendment(_load(OUTPUT))
    print(
        json.dumps(
            {
                "artifact_file_sha256": sha256_file(OUTPUT),
                "current_stage": persisted["payload"]["current_stage"],
                "decision": persisted["payload"]["decision"],
                "output": str(OUTPUT),
                "payload_sha256": persisted["payload_sha256"],
                "result_generation_or_inspection_authorized": persisted[
                    "payload"
                ]["authority"]["result_generation_or_inspection_authorized"],
                "s5_live_execution_authorized": persisted["payload"]["authority"][
                    "s5_live_execution_authorized"
                ],
                "status": persisted["payload"]["status"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
