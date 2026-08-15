#!/usr/bin/env python3
"""Seal the additive S4 candidate-remap retry-005 contract."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_remap_retry_contract import (
    build_s4_remap_retry_contract,
    finalize_s4_remap_retry_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = NATIVE / "S4_D0_CONTRACT.json"
PRIOR_RETRY = NATIVE / "S4_D0_RETRY_004_CONTRACT.json"
DIAGNOSIS = (
    NATIVE
    / "runs/s4-d0-replay-20260814-004/DIAGNOSIS_AND_INVALIDATION.json"
)
AMENDMENT = PROJECT / "S4_CANDIDATE_INDEX_REMAP_AMENDMENT_v1.0.md"
OUTPUT = NATIVE / "S4_D0_REMAP_RETRY_005_CONTRACT.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    contract = build_s4_remap_retry_contract(
        parent_contract=_load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        prior_retry_contract=_load(PRIOR_RETRY),
        prior_retry_contract_file_sha256=sha256_file(PRIOR_RETRY),
        diagnosis=_load(DIAGNOSIS),
        diagnosis_file_sha256=sha256_file(DIAGNOSIS),
        amendment_file_sha256=sha256_file(AMENDMENT),
        attempt_number=5,
        source_sha256={
            "candidate_oracle": sha256_file(
                PROJECT / "src/paper_eval/s4_candidate_oracle.py"
            ),
            "contract": sha256_file(
                PROJECT / "src/paper_eval/s4_remap_retry_contract.py"
            ),
            "production": sha256_file(
                PROJECT / "src/paper_eval/s4_d0_production.py"
            ),
            "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
            "test": sha256_file(
                PROJECT / "tests/test_s4_remap_retry_contract.py"
            ),
        },
    )
    finalize_s4_remap_retry_contract(path=OUTPUT, contract=contract)
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "contract_sha256": contract["contract_sha256"],
                "runs": contract["runs"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
