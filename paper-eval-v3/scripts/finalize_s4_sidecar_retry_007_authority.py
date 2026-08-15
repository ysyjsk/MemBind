#!/usr/bin/env python3
"""Seal the single-use S4 sidecar retry-007 authority after preflight."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_sidecar_authority import (
    build_s4_sidecar_authority,
    finalize_s4_sidecar_authority,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
CONTRACT = NATIVE / "S4_D0_SIDECAR_RETRY_007_CONTRACT.json"
PREFLIGHT = NATIVE / "S4_PREFLIGHT_SIDECAR_RETRY_007.json"
OUTPUT = NATIVE / "S4_SIDECAR_SMOKE_AUTHORIZATION_RETRY_007.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    draft = build_s4_sidecar_authority(
        contract=_load(CONTRACT),
        contract_file_sha256=sha256_file(CONTRACT),
        preflight=_load(PREFLIGHT),
        preflight_file_sha256=sha256_file(PREFLIGHT),
        source_sha256={
            "authority": sha256_file(
                PROJECT / "src/paper_eval/s4_sidecar_authority.py"
            ),
            "candidate_oracle": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_oracle.py"
            ),
            "candidate_projection": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_projection.py"
            ),
            "candidate_sidecar": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_sidecar.py"
            ),
            "candidate_sidecar_runtime": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_sidecar_runtime.py"
            ),
            "controller": sha256_file(
                PROJECT / "src/paper_eval/s4_sidecar_controller.py"
            ),
            "edge_identity": sha256_file(
                PROJECT / "src/paper_eval/s4_edge_identity_diagnosis.py"
            ),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_d0_production.py"
            ),
            "result": sha256_file(PROJECT / "src/paper_eval/s4_sidecar_result.py"),
            "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
            "test": sha256_file(PROJECT / "tests/test_s4_sidecar_controller.py"),
        },
    )
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    artifact = finalize_s4_sidecar_authority(
        output_path=OUTPUT,
        authority=draft["payload"],
        git_commit=git_commit,
        run_id="s4-sidecar-smoke-authority-20260815-007",
    )
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "payload_sha256": artifact["payload_sha256"],
                "runs": artifact["payload"]["runs"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
