#!/usr/bin/env python3
"""Seal the one-shot S4 smoke authority after all offline gates pass."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_authority import (
    build_s4_smoke_authority,
    finalize_s4_smoke_authority,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
DEFAULT_CONTRACT = NATIVE / "S4_D0_CONTRACT.json"
DEFAULT_PREFLIGHT = NATIVE / "S4_PREFLIGHT.json"
DEFAULT_OUTPUT = NATIVE / "S4_SMOKE_AUTHORIZATION.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    value.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    value.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--run-id", default="s4-smoke-authority-20260814-001")
    return value


def main() -> None:
    args = parser().parse_args()
    draft = build_s4_smoke_authority(
        contract=_load(args.contract),
        contract_file_sha256=sha256_file(args.contract),
        preflight=_load(args.preflight),
        preflight_file_sha256=sha256_file(args.preflight),
        source_sha256={
            "authority": sha256_file(PROJECT / "src/paper_eval/s4_authority.py"),
            "controller": sha256_file(PROJECT / "src/paper_eval/s4_controller.py"),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_d0_production.py"
            ),
            "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
            "test": sha256_file(PROJECT / "tests/test_s4_controller.py"),
        },
    )
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = finalize_s4_smoke_authority(
        output_path=args.output,
        authority=draft["payload"],
        git_commit=git_commit,
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "path": str(args.output),
                "file_sha256": sha256_file(args.output),
                "payload_sha256": artifact["payload_sha256"],
                "execution_order": artifact["payload"]["execution_order"],
                "authority": artifact["payload"]["authority"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
