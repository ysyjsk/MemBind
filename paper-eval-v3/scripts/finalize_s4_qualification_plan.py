#!/usr/bin/env python3
"""Seal the non-authorizing fixed-four-history S4 qualification plan."""

import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_qualification_plan import (
    build_s4_qualification_plan,
    finalize_s4_qualification_plan,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
ROLE_REGISTRY = PROJECT / "artifacts/paper_eval/DEVELOPMENT_EXPOSED_IDS.json"
SPLIT = ROOT / "membind-validation/artifacts/dataset/frozen_split.json"
S3_FREEZE = PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
WORKPLAN = PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"
OUTPUT = PROJECT / "artifacts/paper_eval/native/S4_D0_QUALIFICATION_PLAN.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    plan = build_s4_qualification_plan(
        role_registry=load(ROLE_REGISTRY),
        role_registry_file_sha256=sha256_file(ROLE_REGISTRY),
        split=load(SPLIT),
        split_file_sha256=sha256_file(SPLIT),
        s3_freeze=load(S3_FREEZE),
        s3_freeze_file_sha256=sha256_file(S3_FREEZE),
        s4_workplan_sha256=sha256_file(WORKPLAN),
        source_sha256={
            "plan": sha256_file(PROJECT / "src/paper_eval/s4_qualification_plan.py"),
            "test": sha256_file(PROJECT / "tests/test_s4_qualification_plan.py"),
        },
    )
    finalize_s4_qualification_plan(path=OUTPUT, plan=plan)
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "plan_sha256": plan["plan_sha256"],
                "history_ids": plan["history_ids"],
                "authority": plan["authority"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
