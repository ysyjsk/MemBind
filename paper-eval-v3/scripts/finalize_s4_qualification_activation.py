#!/usr/bin/env python3
"""Seal the S4 fixed-four activation after retry-005 passes strict verification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_qualification_activation import (
    build_s4_qualification_activation,
    finalize_s4_qualification_activation,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PLAN = NATIVE / "S4_D0_QUALIFICATION_PLAN.json"
SMOKE_RESULT = NATIVE / "S4_D0_REMAP_SMOKE_RESULT.json"
AUTHORITY = NATIVE / "S4_REMAP_SMOKE_AUTHORIZATION_RETRY_005.json"
CONSUMPTION = (
    NATIVE
    / "runs/s4-remap-smoke-retry-005/S4_REMAP_AUTHORITY_CONSUMPTION.json"
)
CAPTURE_RESULT = (
    NATIVE / "runs/s4-d0-capture-20260815-005/phase_result.json"
)
REPLAY_RESULT = NATIVE / "runs/s4-d0-replay-20260815-005/phase_result.json"
OUTPUT = NATIVE / "S4_QUALIFICATION_ACTIVATION_OVERLAY.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT.parent, text=True
    ).strip()


def main() -> None:
    artifact = build_s4_qualification_activation(
        qualification_plan=_load(PLAN),
        qualification_plan_file_sha256=sha256_file(PLAN),
        smoke_result=_load(SMOKE_RESULT),
        smoke_result_file_sha256=sha256_file(SMOKE_RESULT),
        authority=_load(AUTHORITY),
        authority_file_sha256=sha256_file(AUTHORITY),
        consumption=_load(CONSUMPTION),
        consumption_file_sha256=sha256_file(CONSUMPTION),
        capture_result=_load(CAPTURE_RESULT),
        capture_result_file_sha256=sha256_file(CAPTURE_RESULT),
        replay_result=_load(REPLAY_RESULT),
        replay_result_file_sha256=sha256_file(REPLAY_RESULT),
        source_sha256={
            "activation": sha256_file(
                PROJECT / "src/paper_eval/s4_qualification_activation.py"
            ),
            "test": sha256_file(
                PROJECT / "tests/test_s4_qualification_activation.py"
            ),
        },
        git_commit=_git_commit(),
    )
    finalize_s4_qualification_activation(path=OUTPUT, artifact=artifact)
    print(f"path={OUTPUT}")
    print(f"file_sha256={sha256_file(OUTPUT)}")
    print(f"payload_sha256={artifact['payload_sha256']}")
    print("qualification_live_authorized=true")
    print("s5_authorized=false")
    print("pilot_execution_authorized=false")


if __name__ == "__main__":
    main()
