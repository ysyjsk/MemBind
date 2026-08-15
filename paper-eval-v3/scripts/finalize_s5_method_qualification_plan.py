#!/usr/bin/env python3
"""Seal the S5 offline method registry from real files and TDD evidence."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from paper_eval.artifacts import atomic_write_json, sha256_file
from paper_eval.s5_method_qualification_plan import (
    build_s5_method_qualification_plan,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
VALIDATION = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"

PARENT = ROOT / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
GATE = NATIVE / "S4_REVISED_OFFLINE_GATE.json"
CURRENT = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
FREEZE = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
ROLES = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
OUTPUT = NATIVE / "S5_METHOD_QUALIFICATION_PLAN.json"

SOURCE_PATHS = {
    "common_runtime": VALIDATION / "src/native_characterization_runtime.py",
    "native_entrypoint": VALIDATION / "src/graphiti_native.py",
    "a0_scheduler": VALIDATION / "src/native_characterization_c4_async.py",
    "a0_live_adapter": VALIDATION / "src/native_characterization_c4_live.py",
    "a0_durable_store": VALIDATION / "src/native_characterization_c4_artifacts.py",
    "p_scheduler": VALIDATION / "src/native_characterization_c5_live_core.py",
    "p_live_adapter": VALIDATION / "src/native_characterization_c5_live.py",
    "p_invariant_checker": VALIDATION / "src/native_characterization_c5.py",
    "m_candidate_core": VALIDATION / "src/graphiti_membind.py",
    "m_ordered_binder": VALIDATION / "src/latest_state_bind.py",
    "m_semantic_compile": VALIDATION / "src/semantic_compile.py",
    "fx0_harness": PROJECT / "src/paper_eval/fx0_mechanism_fixture.py",
    "s5_plan_source": PROJECT / "src/paper_eval/s5_method_qualification_plan.py",
    "s5_plan_test": PROJECT / "tests/test_s5_method_qualification_plan.py",
    "s5_plan_finalizer": Path(__file__),
    "s5_workplan": PROJECT / "S5_PRODUCTION_METHOD_QUALIFICATION_WORKPLAN_v1.0.md",
}

RED = PROJECT / "logs/TDD_RED_S5_METHOD_QUALIFICATION_PLAN_20260815.xml"
FOCUSED = PROJECT / "logs/TDD_FOCUSED_GREEN_S5_METHOD_QUALIFICATION_PLAN_20260815.xml"
FULL = PROJECT / "logs/TDD_FULL_GREEN_S5_METHOD_QUALIFICATION_PLAN_20260815.xml"


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
    artifact = build_s5_method_qualification_plan(
        parent_protocol_file_sha256=sha256_file(PARENT),
        s4_gate_artifact=_load(GATE),
        s4_gate_file_sha256=sha256_file(GATE),
        current_stage_pointer=_load(CURRENT),
        current_stage_pointer_file_sha256=sha256_file(CURRENT),
        native_baseline_freeze=_load(FREEZE),
        native_baseline_freeze_file_sha256=sha256_file(FREEZE),
        role_registry=_load(ROLES),
        role_registry_file_sha256=sha256_file(ROLES),
        source_file_sha256={
            name: sha256_file(path) for name, path in SOURCE_PATHS.items()
        },
        offline_evidence={
            "red": _junit(RED),
            "focused_green": _junit(FOCUSED),
            "full_green": _junit(FULL),
        },
        git_commit=git_commit,
    )
    atomic_write_json(OUTPUT, artifact)
    print(
        json.dumps(
            {
                "artifact_file_sha256": sha256_file(OUTPUT),
                "methods": artifact["payload"]["methods"],
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
