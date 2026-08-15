"""Offline TDD for the additive S4 retry-004 execution identity."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_retry_contract import (
    build_s4_retry_contract,
    finalize_s4_retry_contract,
    verify_s4_retry_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = NATIVE / "S4_D0_CONTRACT.json"
INVALIDATION = (
    NATIVE / "runs/s4-d0-capture-20260814-001/INVALIDATION.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build() -> dict:
    return build_s4_retry_contract(
        parent_contract=_load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        invalidation=_load(INVALIDATION),
        invalidation_file_sha256=sha256_file(INVALIDATION),
        attempt_number=4,
        source_sha256={"retry_contract": "1" * 64, "test": "2" * 64},
    )


def test_retry_changes_only_attempt_scoped_execution_identity() -> None:
    contract = verify_s4_retry_contract(_build())

    assert contract["attempt_id"] == "004"
    assert contract["history"] == _load(PARENT)["history"]
    assert contract["execution_order"] == _load(PARENT)["execution_order"]
    assert contract["common_method_policy_sha256"] == _load(PARENT)[
        "common_method_policy_sha256"
    ]
    assert contract["runs"] == {
        "U0_CAPTURE": {
            "cache_id": "s4-d0-07741c45-20260814-004",
            "method": "U0",
            "mode": "capture",
            "namespace": "pev3-s4-u0-capture-20260814-004",
            "run_id": "s4-d0-capture-20260814-004",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": "s4-d0-07741c45-20260814-004",
            "method": "D0",
            "mode": "replay",
            "namespace": "pev3-s4-d0-replay-20260814-004",
            "run_id": "s4-d0-replay-20260814-004",
        },
    }
    assert contract["private_cache"] == {
        "prompt_relpath": (
            "runtime/private/s4-d0-07741c45-20260814-004/prompt.jsonl"
        ),
        "embedding_relpath": (
            "runtime/private/s4-d0-07741c45-20260814-004/embedding.jsonl"
        ),
        "reportable_contents": False,
    }
    assert contract["authority"] == {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_retry_requires_the_sealed_nonmergeable_invalidation() -> None:
    invalidation = _load(INVALIDATION)
    invalidation["payload"]["mergeable"] = True
    with pytest.raises(Exception):
        build_s4_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            invalidation=invalidation,
            invalidation_file_sha256=sha256_file(INVALIDATION),
            attempt_number=4,
            source_sha256={"retry_contract": "1" * 64, "test": "2" * 64},
        )

    with pytest.raises(ValueError, match="attempt"):
        build_s4_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            invalidation=_load(INVALIDATION),
            invalidation_file_sha256=sha256_file(INVALIDATION),
            attempt_number=3,
            source_sha256={"retry_contract": "1" * 64, "test": "2" * 64},
        )


def test_retry_contract_hash_tamper_and_exclusive_finalization(
    tmp_path: Path,
) -> None:
    contract = _build()
    output = tmp_path / "S4_D0_RETRY_004_CONTRACT.json"
    assert finalize_s4_retry_contract(path=output, contract=contract) == contract
    with pytest.raises(FileExistsError):
        finalize_s4_retry_contract(path=output, contract=contract)

    altered = copy.deepcopy(contract)
    altered["runs"]["U0_CAPTURE"]["namespace"] = "pev3-s4-wrong"
    with pytest.raises(ValueError):
        verify_s4_retry_contract(altered)

