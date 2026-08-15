"""Offline TDD for the bilateral-sidecar retry-006 execution contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_sidecar_retry_contract import (
    build_s4_sidecar_retry_contract,
    finalize_s4_sidecar_retry_contract,
    verify_s4_sidecar_retry_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
PARENT = NATIVE / "S4_D0_CONTRACT.json"
PRIOR = NATIVE / "S4_D0_REMAP_RETRY_005_CONTRACT.json"
DIAGNOSIS = NATIVE / "S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json"
AMENDMENT = PROJECT / "S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources(attempt_number: int = 6) -> dict[str, str]:
    selected = {
        "candidate_oracle": "1" * 64,
        "candidate_projection": "2" * 64,
        "candidate_sidecar": "3" * 64,
        "candidate_sidecar_runtime": "4" * 64,
        "contract": "5" * 64,
        "production": "6" * 64,
        "runner": "7" * 64,
        "test": "8" * 64,
    }
    if attempt_number >= 7:
        selected["edge_identity"] = "b" * 64
    return selected


def _offline_evidence() -> dict[str, object]:
    return {
        "focused_junit_sha256": "9" * 64,
        "focused_pass_count": 119,
        "full_junit_sha256": "a" * 64,
        "full_pass_count": 773,
    }


def _contract(attempt_number: int = 6) -> dict:
    return build_s4_sidecar_retry_contract(
        parent_contract=_load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        prior_retry_contract=_load(PRIOR),
        prior_retry_contract_file_sha256=sha256_file(PRIOR),
        diagnosis=_load(DIAGNOSIS),
        diagnosis_file_sha256=sha256_file(DIAGNOSIS),
        amendment_file_sha256=sha256_file(AMENDMENT),
        projection_schema_sha256=PROJECTION_SCHEMA_SHA256,
        offline_evidence=_offline_evidence(),
        source_sha256=_sources(attempt_number),
        attempt_number=attempt_number,
    )


def _rehash(value: dict) -> dict:
    selected = copy.deepcopy(value)
    selected.pop("contract_sha256", None)
    selected["contract_sha256"] = payload_sha256(selected)
    return selected


def test_contract_freezes_fresh_retry_006_and_bilateral_policy() -> None:
    contract = _contract()

    assert verify_s4_sidecar_retry_contract(contract) == contract
    assert contract["attempt_id"] == "006"
    assert contract["runs"] == {
        "U0_CAPTURE": {
            "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
            "method": "U0",
            "mode": "capture",
            "namespace": "pev3-s4-u0-capture-20260815-006",
            "run_id": "s4-d0-capture-20260815-006",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": "s4-d0-sidecar-07741c45-20260815-006",
            "method": "D0",
            "mode": "replay",
            "namespace": "pev3-s4-d0-replay-20260815-006",
            "run_id": "s4-d0-replay-20260815-006",
        },
    }
    assert contract["private_cache"]["candidate_sidecar_relpath"].endswith(
        "/candidate-sidecar.jsonl"
    )
    assert contract["candidate_oracle"]["edge_translation_kind"] == (
        "BILATERAL_UUID_INDEPENDENT_LOGICAL_EDGE_BIJECTION"
    )
    assert contract["candidate_oracle"]["capture_only_sidecar_allowed"] is False
    assert contract["sidecar_hard_gates"] == {
        "cache_and_sidecar_mutation_during_replay": False,
        "candidate_remap_rejection_count": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "edge_sidecar_resolution_accounting": "EXACT",
        "sidecar_consumed_equals_record_count": True,
        "sidecar_prepared_count": 0,
        "sidecar_rejection_count": 0,
        "sidecar_remaining_count": 0,
    }
    assert contract["authority"] == {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_contract_allocates_retry_007_and_binds_edge_identity_source() -> None:
    contract = _contract(attempt_number=7)

    assert verify_s4_sidecar_retry_contract(contract) == contract
    assert contract["attempt_id"] == "007"
    assert contract["runs"]["U0_CAPTURE"]["namespace"] == (
        "pev3-s4-u0-capture-20260815-007"
    )
    assert contract["runs"]["D0_READ_ONLY_REPLAY"]["run_id"] == (
        "s4-d0-replay-20260815-007"
    )
    assert contract["private_cache"]["candidate_sidecar_relpath"].startswith(
        "runtime/private/s4-d0-sidecar-07741c45-20260815-007/"
    )
    assert contract["source_sha256"]["edge_identity"] == "b" * 64


def test_contract_allocates_fresh_retry_008_identity() -> None:
    contract = _contract(attempt_number=8)

    assert verify_s4_sidecar_retry_contract(contract) == contract
    assert contract["attempt_id"] == "008"
    assert contract["runs"]["U0_CAPTURE"]["run_id"] == (
        "s4-d0-capture-20260815-008"
    )
    assert contract["runs"]["D0_READ_ONLY_REPLAY"]["namespace"] == (
        "pev3-s4-d0-replay-20260815-008"
    )
    assert contract["private_cache"]["candidate_sidecar_relpath"] == (
        "runtime/private/s4-d0-sidecar-07741c45-20260815-008/"
        "candidate-sidecar.jsonl"
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(attempt_id="005"),
        lambda value: value["runs"]["U0_CAPTURE"].update(
            namespace="pev3-s4-u0-capture-20260815-005"
        ),
        lambda value: value["candidate_oracle"].update(
            capture_only_sidecar_allowed=True
        ),
        lambda value: value["candidate_oracle"].update(
            wrapper_order=list(reversed(value["candidate_oracle"]["wrapper_order"]))
        ),
        lambda value: value["sidecar_hard_gates"].update(
            sidecar_rejection_count=1
        ),
        lambda value: value["authority"].update(live_execution_authorized=True),
        lambda value: value.update(extra="drift"),
    ],
)
def test_contract_rejects_identity_policy_gate_or_scope_drift(mutate) -> None:
    altered = copy.deepcopy(_contract())
    mutate(altered)

    with pytest.raises(ValueError):
        verify_s4_sidecar_retry_contract(_rehash(altered))


def test_builder_rejects_non_authorizing_diagnosis_or_incomplete_offline_gate() -> None:
    diagnosis = _load(DIAGNOSIS)
    diagnosis["verdict"] = "LOGICAL_IDENTITY_STILL_AMBIGUOUS_STOP"
    diagnosis.pop("artifact_sha256")
    diagnosis["artifact_sha256"] = payload_sha256(diagnosis)

    with pytest.raises(ValueError, match="diagnosis"):
        build_s4_sidecar_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            prior_retry_contract=_load(PRIOR),
            prior_retry_contract_file_sha256=sha256_file(PRIOR),
            diagnosis=diagnosis,
            diagnosis_file_sha256=sha256_file(DIAGNOSIS),
            amendment_file_sha256=sha256_file(AMENDMENT),
            projection_schema_sha256=PROJECTION_SCHEMA_SHA256,
            offline_evidence=_offline_evidence(),
            source_sha256=_sources(),
        )

    evidence = _offline_evidence()
    evidence["full_pass_count"] = 0
    with pytest.raises(ValueError, match="offline"):
        build_s4_sidecar_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            prior_retry_contract=_load(PRIOR),
            prior_retry_contract_file_sha256=sha256_file(PRIOR),
            diagnosis=_load(DIAGNOSIS),
            diagnosis_file_sha256=sha256_file(DIAGNOSIS),
            amendment_file_sha256=sha256_file(AMENDMENT),
            projection_schema_sha256=PROJECTION_SCHEMA_SHA256,
            offline_evidence=evidence,
            source_sha256=_sources(),
        )


def test_contract_finalizer_is_exclusive(tmp_path: Path) -> None:
    target = tmp_path / "S4_D0_SIDECAR_RETRY_006_CONTRACT.json"
    contract = _contract()

    assert finalize_s4_sidecar_retry_contract(path=target, contract=contract) == contract
    assert json.loads(target.read_text(encoding="ascii")) == contract
    with pytest.raises(FileExistsError):
        finalize_s4_sidecar_retry_contract(path=target, contract=contract)
