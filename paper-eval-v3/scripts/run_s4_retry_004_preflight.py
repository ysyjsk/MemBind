#!/usr/bin/env python3
"""Run and seal the bounded preflight for the S4 retry-004 namespaces."""

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
from paper_eval.s4_retry_contract import verify_s4_retry_contract


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
CONTRACT = NATIVE / "S4_D0_RETRY_004_CONTRACT.json"
CHECKPOINT = NATIVE / "runs/s1-20260814-001/checkpoint.json"
OUTPUT = NATIVE / "S4_PREFLIGHT_RETRY_004.json"


async def run() -> None:
    contract = verify_s4_retry_contract(
        json.loads(CONTRACT.read_text(encoding="utf-8"))
    )
    capture = contract["runs"]["U0_CAPTURE"]["namespace"]
    replay = contract["runs"]["D0_READ_ONLY_REPLAY"]["namespace"]
    evaluation = await execute_production_preflight(
        env=load_s4_preflight_env(LEGACY / ".env"),
        s1_checkpoint_path=CHECKPOINT,
        capture_namespace=capture,
        replay_namespace=replay,
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
        run_id="s4-preflight-retry-004",
    )
    print(
        json.dumps(
            {
                "verdict": evaluation["verdict"],
                "construction": evaluation["construction"],
                "embedding": evaluation["embedding"],
                "namespace_checks": evaluation["namespace_checks"],
                "artifact_file_sha256": sha256_file(OUTPUT),
                "payload_sha256": artifact["payload_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
