#!/usr/bin/env python3
"""Seal the additive S4 retry-004 execution-identity contract."""

import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_retry_contract import (
    build_s4_retry_contract,
    finalize_s4_retry_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = NATIVE / "S4_D0_CONTRACT.json"
INVALIDATION = NATIVE / "runs/s4-d0-capture-20260814-001/INVALIDATION.json"
OUTPUT = NATIVE / "S4_D0_RETRY_004_CONTRACT.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    contract = build_s4_retry_contract(
        parent_contract=load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        invalidation=load(INVALIDATION),
        invalidation_file_sha256=sha256_file(INVALIDATION),
        attempt_number=4,
        source_sha256={
            "retry_contract": sha256_file(
                PROJECT / "src/paper_eval/s4_retry_contract.py"
            ),
            "test": sha256_file(PROJECT / "tests/test_s4_retry_contract.py"),
        },
    )
    finalize_s4_retry_contract(path=OUTPUT, contract=contract)
    print(
        json.dumps(
            {
                "path": str(OUTPUT),
                "file_sha256": sha256_file(OUTPUT),
                "contract_sha256": contract["contract_sha256"],
                "runs": contract["runs"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
