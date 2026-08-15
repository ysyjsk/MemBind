#!/usr/bin/env python3
"""Seal retry-006 only after the focused and full offline JUnit gates pass."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_sidecar_retry_contract import (
    build_s4_sidecar_retry_contract,
    finalize_s4_sidecar_retry_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = NATIVE / "S4_D0_CONTRACT.json"
PRIOR = NATIVE / "S4_D0_REMAP_RETRY_005_CONTRACT.json"
DIAGNOSIS = NATIVE / "S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json"
AMENDMENT = PROJECT / "S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md"
FOCUSED = PROJECT / "logs/TDD_FOCUSED_GREEN_S4_SIDECAR_RETRY_006_20260815.xml"
FULL = PROJECT / "logs/TDD_FULL_OFFLINE_GREEN_S4_SIDECAR_RETRY_006_20260815.xml"
OUTPUT = NATIVE / "S4_D0_SIDECAR_RETRY_006_CONTRACT.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _passed_count(path: Path) -> int:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    if tests <= 0 or failures or errors or skipped:
        raise ValueError(f"offline JUnit gate is not a complete pass: {path.name}")
    return tests


def main() -> None:
    contract = build_s4_sidecar_retry_contract(
        parent_contract=_load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        prior_retry_contract=_load(PRIOR),
        prior_retry_contract_file_sha256=sha256_file(PRIOR),
        diagnosis=_load(DIAGNOSIS),
        diagnosis_file_sha256=sha256_file(DIAGNOSIS),
        amendment_file_sha256=sha256_file(AMENDMENT),
        projection_schema_sha256=PROJECTION_SCHEMA_SHA256,
        offline_evidence={
            "focused_junit_sha256": sha256_file(FOCUSED),
            "focused_pass_count": _passed_count(FOCUSED),
            "full_junit_sha256": sha256_file(FULL),
            "full_pass_count": _passed_count(FULL),
        },
        source_sha256={
            "candidate_oracle": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_oracle.py"
            ),
            "candidate_projection": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_projection.py"
            ),
            "candidate_sidecar": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_sidecar.py"
            ),
            "candidate_sidecar_runtime": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_sidecar_runtime.py"
            ),
            "contract": sha256_file(
                PROJECT / "src/paper_eval/s4_sidecar_retry_contract.py"
            ),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_d0_production.py"
            ),
            "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
            "test": sha256_file(PROJECT / "tests/test_s4_sidecar_retry_contract.py"),
        },
    )
    artifact = finalize_s4_sidecar_retry_contract(path=OUTPUT, contract=contract)
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "contract_sha256": artifact["contract_sha256"],
                "runs": artifact["runs"],
                "authority": artifact["authority"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
