#!/usr/bin/env python3
"""Run the bounded read-only preflight for S4 sidecar retry-006."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_preflight import finalize_s4_preflight
from paper_eval.s4_preflight_production import (
    execute_production_preflight,
    load_s4_preflight_env,
)
from paper_eval.s4_sidecar_retry_contract import verify_s4_sidecar_retry_contract


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
CONTRACT = NATIVE / "S4_D0_SIDECAR_RETRY_006_CONTRACT.json"
CHECKPOINT = NATIVE / "runs/s1-20260814-001/checkpoint.json"
OUTPUT = NATIVE / "S4_PREFLIGHT_SIDECAR_RETRY_006.json"


async def run() -> None:
    contract = verify_s4_sidecar_retry_contract(
        json.loads(CONTRACT.read_text(encoding="utf-8"))
    )
    evaluation = await execute_production_preflight(
        env=load_s4_preflight_env(LEGACY / ".env"),
        s1_checkpoint_path=CHECKPOINT,
        capture_namespace=contract["runs"]["U0_CAPTURE"]["namespace"],
        replay_namespace=contract["runs"]["D0_READ_ONLY_REPLAY"]["namespace"],
    )
    if evaluation["verdict"] != "PASS":
        print(json.dumps({"verdict": "FAIL", "failures": evaluation["failures"]}))
        raise SystemExit(2)
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = finalize_s4_preflight(
        output_path=OUTPUT,
        evaluation=evaluation,
        s4_contract_file_sha256=sha256_file(CONTRACT),
        s4_contract_sha256=contract["contract_sha256"],
        s1_checkpoint_file_sha256=sha256_file(CHECKPOINT),
        source_sha256={
            "preflight": sha256_file(PROJECT / "src/paper_eval/s4_preflight.py"),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_preflight_production.py"
            ),
            "test": sha256_file(PROJECT / "tests/test_s4_preflight.py"),
        },
        git_commit=git_commit,
        run_id="s4-preflight-sidecar-retry-006",
    )
    print(
        json.dumps(
            {
                "verdict": evaluation["verdict"],
                "namespace_checks": evaluation["namespace_checks"],
                "artifact_file_sha256": sha256_file(OUTPUT),
                "payload_sha256": artifact["payload_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
