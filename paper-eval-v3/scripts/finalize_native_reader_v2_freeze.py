#!/usr/bin/env python3
"""Finalize the common Reader-v2 policy after the canary and post-live tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.native_reader_v2_freeze import (
    build_reader_v2_freeze,
    finalize_reader_v2_freeze,
)


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "native-reader-v2-freeze-20260814-001"
RESULT = (
    NATIVE
    / "runs/native-reader-v2-canary-20260814-001/NATIVE_READER_V2_RESULT.json"
)
TARGET = NATIVE / "NATIVE_READER_V2_FREEZE.json"


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path.name}")
    return value


def main() -> None:
    contract_path = NATIVE / "NATIVE_READER_V2_CONTRACT.json"
    qualification_path = NATIVE / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json"
    postlive = PROJECT / "logs/TDD_FULL_OFFLINE_GREEN_NATIVE_READER_V2_POSTLIVE_20260814.xml"
    contract = _load(contract_path)
    qualification = _load(qualification_path)
    result = _load(RESULT)
    classification = result.get("payload", {}).get("classification", {})
    artifact = build_reader_v2_freeze(
        contract=contract,
        qualification_payload_sha256=str(qualification["payload_sha256"]),
        result=result,
        result_file_sha256=sha256_file(RESULT),
        judge_config_sha256=str(classification["judge_config_sha256"]),
        source_sha256={
            "workplan": sha256_file(
                PROJECT / "NATIVE_READER_V2_QUALIFICATION_WORKPLAN_v1.0.md"
            ),
            "reader_source": sha256_file(
                PROJECT / "src/paper_eval/native_reader_v2.py"
            ),
            "contract_file": sha256_file(contract_path),
            "qualification_file": sha256_file(qualification_path),
            "result_file": sha256_file(RESULT),
            "postlive_tests": sha256_file(postlive),
        },
        git_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        run_id=RUN_ID,
    )
    finalize_reader_v2_freeze(path=TARGET, artifact=artifact)
    print(
        json.dumps(
            {
                "status": artifact["payload"]["status"],
                "reader_config_sha256": artifact["payload"][
                    "reader_config_sha256"
                ],
                "qa_accuracy_diagnostic": artifact["payload"][
                    "qa_accuracy_diagnostic"
                ],
                "pilot_execution_authorized": artifact["payload"][
                    "pilot_execution_authorized"
                ],
                "path": str(TARGET),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
