#!/usr/bin/env python3
"""Run the single sealed S2 completion authority and print a safe summary."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.s2_completion_controller import (
    CompletionControllerDependencies,
    run_s2_completion_controller,
)
from paper_eval.s2_completion_production import (
    LEGACY,
    build_production_live_executor,
    load_completion_env_file,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
RUN_ID = "s2-completion-20260814-001"


def main() -> int:
    env = load_completion_env_file(LEGACY / ".env")
    outcome = run_s2_completion_controller(
        authorization_path=NATIVE / "S2_COMPLETION_AUTHORIZATION.json",
        qualification_path=NATIVE / "S2_COMPLETION_OFFLINE_QUALIFICATION.json",
        policy_freeze_path=NATIVE / "S2_COMPLETION_POLICY_FREEZE.json",
        adapter_identity_path=NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json",
        dependencies=CompletionControllerDependencies(
            build_live=lambda: build_production_live_executor(
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
        )
    )
    return 0 if outcome.status in {"PASS", "REVIEW_REQUIRED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
