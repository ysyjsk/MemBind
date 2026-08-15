#!/usr/bin/env python3
"""Exclusively finalize the offline S4 D0 execution contract."""

from __future__ import annotations

import json
from pathlib import Path

from paper_eval.artifacts import sha256_file
from paper_eval.s4_d0_contract import (
    build_s4_d0_contract,
    finalize_s4_d0_contract,
)


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "paper-eval-v3"
LEGACY = ROOT / "membind-validation"
NATIVE = PROJECT / "artifacts/paper_eval/native"
TARGET = NATIVE / "S4_D0_CONTRACT.json"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path.name}")
    return value


def main() -> None:
    freeze_path = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
    contract = build_s4_d0_contract(
        native_baseline_v2_freeze=_load(freeze_path),
        native_baseline_v2_freeze_file_sha256=sha256_file(freeze_path),
        current_pointer=_load(PROJECT / "runtime/CURRENT_STAGE_STATUS.json"),
        s4_workplan_sha256=sha256_file(
            PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"
        ),
        source_sha256={
            "canonicalizer": sha256_file(LEGACY / "src/canonicalize_graph.py"),
            "embedding_oracle": sha256_file(LEGACY / "src/embedding_cache.py"),
            "graphiti_d0_factory": sha256_file(LEGACY / "src/graphiti_native.py"),
            "native_u0_runtime": sha256_file(
                LEGACY / "src/native_characterization_runtime.py"
            ),
            "prompt_oracle": sha256_file(LEGACY / "src/response_cache.py"),
            "s1_namespace_adapter": sha256_file(
                PROJECT / "src/paper_eval/s1_live.py"
            ),
            "s4_contract_source": sha256_file(
                PROJECT / "src/paper_eval/s4_d0_contract.py"
            ),
            "s4_contract_test": sha256_file(
                PROJECT / "tests/test_s4_d0_contract.py"
            ),
        },
    )
    finalized = finalize_s4_d0_contract(path=TARGET, contract=contract)
    print(
        json.dumps(
            {
                "authority": finalized["authority"],
                "contract_sha256": finalized["contract_sha256"],
                "history": finalized["history"],
                "path": str(TARGET),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
