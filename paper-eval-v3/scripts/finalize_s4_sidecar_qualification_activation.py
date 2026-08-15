#!/usr/bin/env python3
"""Strictly verify retry-007 PASS and activate the sealed fixed-four plan."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_sidecar_qualification_activation import (
    build_s4_sidecar_qualification_activation,
    finalize_s4_sidecar_qualification_activation,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
PLAN = NATIVE / "S4_D0_QUALIFICATION_PLAN.json"
SMOKE = NATIVE / "S4_D0_SIDECAR_SMOKE_RESULT_RETRY_007.json"
AUTHORITY = NATIVE / "S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_007.json"
CONSUMPTION = (
    NATIVE
    / "runs/s4-sidecar-smoke-retry-007/S4_SIDECAR_AUTHORITY_CONSUMPTION.json"
)
CAPTURE = NATIVE / "runs/s4-d0-capture-20260815-007/phase_result.json"
REPLAY = NATIVE / "runs/s4-d0-replay-20260815-007/phase_result.json"
SIDECAR = (
    PROJECT
    / "runtime/private/s4-d0-sidecar-07741c45-20260815-007/candidate-sidecar.jsonl"
)
OUTPUT = NATIVE / "S4_D0_QUALIFICATION_ACTIVATION_SIDECAR_V2.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = build_s4_sidecar_qualification_activation(
        qualification_plan=_load(PLAN),
        qualification_plan_file_sha256=sha256_file(PLAN),
        smoke_result=_load(SMOKE),
        smoke_result_file_sha256=sha256_file(SMOKE),
        authority=_load(AUTHORITY),
        authority_file_sha256=sha256_file(AUTHORITY),
        consumption=_load(CONSUMPTION),
        consumption_file_sha256=sha256_file(CONSUMPTION),
        capture_result=_load(CAPTURE),
        capture_result_file_sha256=sha256_file(CAPTURE),
        replay_result=_load(REPLAY),
        replay_result_file_sha256=sha256_file(REPLAY),
        candidate_sidecar_file_sha256=sha256_file(SIDECAR),
        source_sha256={
            "activation": sha256_file(
                PROJECT / "src/paper_eval/s4_sidecar_qualification_activation.py"
            ),
            "test": sha256_file(
                PROJECT / "tests/test_s4_sidecar_qualification_activation.py"
            ),
        },
        git_commit=git_commit,
    )
    finalized = finalize_s4_sidecar_qualification_activation(
        path=OUTPUT,
        artifact=artifact,
    )
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "payload_sha256": finalized["payload_sha256"],
                "authority": finalized["payload"]["authority"],
                "live_history_ids": finalized["payload"][
                    "activated_projection"
                ]["live_history_ids"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
