"""Offline TDD for retry-006's single-use bilateral-sidecar authority."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s4_candidate_projection import PROJECTION_SCHEMA_SHA256
from paper_eval.s4_preflight import finalize_s4_preflight
from paper_eval.s4_sidecar_authority import (
    build_s4_sidecar_authority,
    consume_s4_sidecar_authority,
    finalize_s4_sidecar_authority,
    verify_s4_sidecar_authority,
    verify_s4_sidecar_authority_consumption,
)
from paper_eval.s4_sidecar_retry_contract import build_s4_sidecar_retry_contract


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _contract(attempt_number: int = 6) -> dict:
    parent = NATIVE / "S4_D0_CONTRACT.json"
    prior = NATIVE / "S4_D0_REMAP_RETRY_005_CONTRACT.json"
    diagnosis = NATIVE / "S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json"
    amendment = PROJECT / "S4_BILATERAL_LOGICAL_EDGE_SIDECAR_AMENDMENT_v1.0.md"
    sources = {
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
        sources["edge_identity"] = "b" * 64
    return build_s4_sidecar_retry_contract(
        parent_contract=_load(parent),
        parent_contract_file_sha256=sha256_file(parent),
        prior_retry_contract=_load(prior),
        prior_retry_contract_file_sha256=sha256_file(prior),
        diagnosis=_load(diagnosis),
        diagnosis_file_sha256=sha256_file(diagnosis),
        amendment_file_sha256=sha256_file(amendment),
        projection_schema_sha256=PROJECTION_SCHEMA_SHA256,
        offline_evidence={
            "focused_junit_sha256": "1" * 64,
            "focused_pass_count": 119,
            "full_junit_sha256": "2" * 64,
            "full_pass_count": 773,
        },
        source_sha256=sources,
        attempt_number=attempt_number,
    )


def _preflight(tmp_path: Path, contract: dict, contract_path: Path) -> tuple[dict, Path]:
    attempt = contract["attempt_id"]
    path = tmp_path / f"S4_PREFLIGHT_SIDECAR_RETRY_{attempt}.json"
    artifact = finalize_s4_preflight(
        output_path=path,
        evaluation={
            "schema_version": "membind.paper-eval-v3.s4-preflight-evaluation.v1",
            "verdict": "PASS",
            "failures": [],
            "authority": {
                "s4_authority_creation_authorized": True,
                "s4_live_execution_authorized": False,
                "pilot_execution_authorized": False,
            },
        },
        s4_contract_file_sha256=sha256_file(contract_path),
        s4_contract_sha256=contract["contract_sha256"],
        s1_checkpoint_file_sha256="3" * 64,
        source_sha256={
            "preflight": "4" * 64,
            "production": "5" * 64,
            "test": "6" * 64,
        },
        git_commit="deadbeef",
        run_id=f"s4-preflight-sidecar-retry-{attempt}",
    )
    return artifact, path


def _sources(attempt_number: int = 6) -> dict[str, str]:
    selected = {
        "authority": "1" * 64,
        "candidate_oracle": "2" * 64,
        "candidate_projection": "3" * 64,
        "candidate_sidecar": "4" * 64,
        "candidate_sidecar_runtime": "5" * 64,
        "controller": "6" * 64,
        "production": "7" * 64,
        "result": "8" * 64,
        "runner": "9" * 64,
        "test": "a" * 64,
    }
    if attempt_number >= 7:
        selected["edge_identity"] = "b" * 64
    return selected


def _build(tmp_path: Path, *, attempt_number: int = 6) -> dict:
    contract = _contract(attempt_number)
    contract_path = tmp_path / "contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    preflight, preflight_path = _preflight(tmp_path, contract, contract_path)
    return build_s4_sidecar_authority(
        contract=contract,
        contract_file_sha256=sha256_file(contract_path),
        preflight=preflight,
        preflight_file_sha256=sha256_file(preflight_path),
        source_sha256=_sources(attempt_number),
    )


def test_authority_binds_retry_006_sidecar_policy_and_no_later_stage(
    tmp_path: Path,
) -> None:
    authority = verify_s4_sidecar_authority(_build(tmp_path))

    assert authority["payload"]["runs"] == _contract()["runs"]
    assert authority["payload"]["candidate_oracle"] == _contract()[
        "candidate_oracle"
    ]
    assert authority["payload"]["authority"] == {
        "single_use": True,
        "s4_sidecar_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_authority_binds_retry_007_identity_and_edge_projection_source(
    tmp_path: Path,
) -> None:
    authority = verify_s4_sidecar_authority(
        _build(tmp_path, attempt_number=7)
    )

    assert authority["payload"]["runs"] == _contract(7)["runs"]
    assert authority["payload"]["source_sha256"]["edge_identity"] == "b" * 64


def test_authority_rejects_contract_preflight_or_source_drift(tmp_path: Path) -> None:
    authority = _build(tmp_path)
    altered = copy.deepcopy(authority)
    altered["payload"]["sidecar_hard_gates"]["sidecar_rejection_count"] = 1
    altered["payload_sha256"] = payload_sha256(altered["payload"])
    with pytest.raises(ValueError):
        verify_s4_sidecar_authority(altered)

    contract = _contract()
    contract_path = tmp_path / "other-contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    preflight, preflight_path = _preflight(
        tmp_path / "other", contract, contract_path
    )
    preflight["payload"]["s4_contract_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        build_s4_sidecar_authority(
            contract=contract,
            contract_file_sha256=sha256_file(contract_path),
            preflight=preflight,
            preflight_file_sha256=sha256_file(preflight_path),
            source_sha256=_sources(),
        )

    sources = _sources()
    del sources["candidate_projection"]
    with pytest.raises(ValueError, match="source"):
        build_s4_sidecar_authority(
            contract=contract,
            contract_file_sha256=sha256_file(contract_path),
            preflight=_preflight(tmp_path / "third", contract, contract_path)[0],
            preflight_file_sha256="f" * 64,
            source_sha256=sources,
        )


def test_authority_finalization_and_consumption_are_single_use(tmp_path: Path) -> None:
    authority_path = tmp_path / "authority.json"
    authority = finalize_s4_sidecar_authority(
        output_path=authority_path,
        authority=_build(tmp_path / "build")["payload"],
        git_commit="deadbeef",
        run_id="s4-sidecar-smoke-authority-20260815-006",
    )
    assert verify_s4_sidecar_authority(authority) == authority
    with pytest.raises(FileExistsError):
        finalize_s4_sidecar_authority(
            output_path=authority_path,
            authority=authority["payload"],
            git_commit="deadbeef",
            run_id="s4-sidecar-smoke-authority-20260815-006",
        )

    consumption_path = tmp_path / "consumption.json"
    consumption = consume_s4_sidecar_authority(
        authority=authority,
        authority_file_sha256=sha256_file(authority_path),
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s4-sidecar-authority-consumption-20260815-006",
    )
    assert verify_s4_sidecar_authority_consumption(consumption) == consumption
    assert consumption["payload"]["consumed_action"] == (
        "S4_BILATERAL_SIDECAR_SMOKE_PIPELINE"
    )
    with pytest.raises(FileExistsError):
        consume_s4_sidecar_authority(
            authority=authority,
            authority_file_sha256=sha256_file(authority_path),
            output_path=consumption_path,
            git_commit="deadbeef",
            run_id="s4-sidecar-authority-consumption-20260815-006",
        )
