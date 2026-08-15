#!/usr/bin/env python3
"""Consume the one-shot Reader-v2 authority and run the frozen canary."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.native_reader_v2_controller import (
    ReaderV2ControllerDependencies,
    run_reader_v2_controller,
)
from paper_eval.native_reader_v2_production import build_reader_v2_live_executor
from paper_eval.s2_completion_production import load_completion_env_file


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "native-reader-v2-canary-20260814-001"


def main() -> int:
    env = load_completion_env_file(ROOT / "membind-validation/.env")
    outcome = run_reader_v2_controller(
        authorization_path=NATIVE / "NATIVE_READER_V2_AUTHORIZATION.json",
        qualification_path=NATIVE / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json",
        contract_path=NATIVE / "NATIVE_READER_V2_CONTRACT.json",
        dependencies=ReaderV2ControllerDependencies(
            build_live=lambda: build_reader_v2_live_executor(
                env=env,
                run_id=RUN_ID,
            )
        ),
    )
    print(
        json.dumps(
            {
                "status": outcome.status,
                "run_id": outcome.run_id,
                "artifact_path": str(outcome.artifact_path),
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if outcome.status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
