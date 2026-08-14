#!/usr/bin/env python3
"""Consume the sealed authority and execute exactly one read-only S2-R0 probe."""

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
    DEFAULT_AUTHORIZATION,
    DEFAULT_CONSUMPTION,
    DEFAULT_FAILURE,
    DEFAULT_RUN_ID,
    execute_s2r0_once,
    git_commit,
    production_binding_paths,
    production_dependencies,
)


def main() -> int:
    """Run the already-authorized action without creating new authority."""

    try:
        outcome = execute_s2r0_once(
            authorization_path=DEFAULT_AUTHORIZATION,
            consumption_path=DEFAULT_CONSUMPTION,
            failure_path=DEFAULT_FAILURE,
            binding_paths=production_binding_paths(),
            dependencies=production_dependencies(),
            git_commit=git_commit(),
            expected_run_id=DEFAULT_RUN_ID,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "error_class": type(error).__name__,
                    "run_id": DEFAULT_RUN_ID,
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
