#!/usr/bin/env python3
"""Exclusively finalize the configuration-only Native-v2 S3 freeze."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from paper_eval.artifacts import sha256_file
from paper_eval.s3_native_v2_freeze import (
    build_native_baseline_v2_freeze,
    finalize_native_baseline_v2_freeze,
)


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
ARTIFACTS = PROJECT / "artifacts/paper_eval"
NATIVE = ARTIFACTS / "native"
TARGET = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
RUN_ID = "native-baseline-v2-freeze-20260814-001"
FOCUSED_GREEN = (
    PROJECT / "logs/TDD_FOCUSED_GREEN_S3_NATIVE_V2_FREEZE_FINAL_20260814.xml"
)
FULL_GREEN = (
    PROJECT
    / "logs/TDD_FULL_OFFLINE_GREEN_S3_NATIVE_V2_FREEZE_PRESEAL_20260814.xml"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path.name}")
    return value


def _junit_pass(path: Path, *, minimum_tests: int) -> None:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    if tests < minimum_tests or failures or errors or skipped:
        raise RuntimeError(f"JUnit evidence is not fully green: {path.name}")


def main() -> None:
    if TARGET.exists():
        raise RuntimeError(f"refusing to overwrite sealed artifact: {TARGET.name}")
    _junit_pass(FOCUSED_GREEN, minimum_tests=24)
    _junit_pass(FULL_GREEN, minimum_tests=475)

    paths = {
        "s0_current_state": ARTIFACTS / "S0_CURRENT_STATE.json",
        "s1_u0_smoke": NATIVE / "U0_SMOKE.json",
        "u0_qualification": NATIVE / "U0_QUALIFICATION.json",
        "dataset_parity": NATIVE / "DATASET_PARITY.json",
        "evaluator_parity": NATIVE / "EVALUATOR_PARITY.json",
        "direct_add_episode_contract": (
            NATIVE / "U0_DIRECT_ADD_EPISODE_CONTRACT.json"
        ),
        "completion_adapter_identity": (
            NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json"
        ),
        "retrieval_contract": NATIVE / "S2_COMPLETION_CONTRACT.json",
        "retrieval_policy_freeze": (
            NATIVE / "S2_COMPLETION_POLICY_FREEZE.json"
        ),
        "role_registry": ARTIFACTS / "DEVELOPMENT_EXPOSED_IDS.json",
        "reader_v2_contract": NATIVE / "NATIVE_READER_V2_CONTRACT.json",
        "reader_v2_freeze": NATIVE / "NATIVE_READER_V2_FREEZE.json",
    }
    values = {name: _load(path) for name, path in paths.items()}
    artifact = build_native_baseline_v2_freeze(
        **values,
        input_file_sha256={
            "parent_workplan": sha256_file(
                ROOT
                / "（主实验）MemBind_PAPER_EVALUATION_WORKPLAN_v3_SMALL_FIRST_FINAL.md"
            ),
            "reader_v2_workplan": sha256_file(
                PROJECT / "NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md"
            ),
            **{name: sha256_file(path) for name, path in paths.items()},
        },
        source_sha256={
            "finalize_script": sha256_file(Path(__file__)),
            "focused_green_preseal": sha256_file(FOCUSED_GREEN),
            "freeze_source": sha256_file(
                PROJECT / "src/paper_eval/s3_native_v2_freeze.py"
            ),
            "freeze_test": sha256_file(
                PROJECT / "tests/test_s3_native_v2_freeze.py"
            ),
            "full_offline_green_preseal": sha256_file(FULL_GREEN),
        },
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        run_id=RUN_ID,
    )
    finalized = finalize_native_baseline_v2_freeze(path=TARGET, artifact=artifact)
    payload = finalized["payload"]
    print(
        json.dumps(
            {
                "artifact_sha256": sha256_file(TARGET),
                "baseline_id": payload["baseline_id"],
                "configuration_freeze_only": payload[
                    "configuration_freeze_only"
                ],
                "method_policy_sha256": next(
                    iter(payload["method_policy_bindings"].values())
                ),
                "pilot_execution_authorized": payload["authority"][
                    "pilot_execution_authorized"
                ],
                "s4_live_execution_authorized": payload["authority"][
                    "s4_live_execution_authorized"
                ],
                "status": payload["status"],
                "target": str(TARGET),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
