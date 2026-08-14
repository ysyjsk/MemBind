#!/usr/bin/env python3
"""Consume and execute the sealed S2-R0 replacement attempt 002 once."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import sha256_file
from paper_eval.s2_r0_controller import (
    RETRY_002_AUTHORIZATION,
    RETRY_002_CONSUMPTION,
    RETRY_002_FAILURE,
    RETRY_002_RUN_ID,
    execute_s2r0_once,
    git_commit,
    production_dependencies,
    retry_002_binding_paths,
)


EXPECTED_RUN_ID = "s2r0-20260814-002"


def main() -> int:
    if RETRY_002_RUN_ID != EXPECTED_RUN_ID:
        raise RuntimeError("S2-R0 retry 002 run identity drift")
    try:
        outcome = execute_s2r0_once(
            authorization_path=RETRY_002_AUTHORIZATION,
            consumption_path=RETRY_002_CONSUMPTION,
            failure_path=RETRY_002_FAILURE,
            binding_paths=retry_002_binding_paths(),
            dependencies=production_dependencies(),
            git_commit=git_commit(),
            expected_run_id=RETRY_002_RUN_ID,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "error_class": type(error).__name__,
                    "run_id": RETRY_002_RUN_ID,
                    "status": "BLOCKED_BEFORE_LIVE_IO",
                },
                sort_keys=True,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "artifact_path": str(outcome.artifact_path),
                "artifact_sha256": sha256_file(outcome.artifact_path),
                "run_id": outcome.run_id,
                "status": outcome.status,
            },
            sort_keys=True,
        )
    )
    return 0 if outcome.status == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
