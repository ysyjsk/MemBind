"""Offline TDD for the single-use S4 remap retry-005 authority."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_remap_authority import (
    build_s4_remap_authority,
    consume_s4_remap_authority,
    finalize_s4_remap_authority,
    verify_s4_remap_authority,
    verify_s4_remap_authority_consumption,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
CONTRACT = NATIVE / "S4_D0_REMAP_RETRY_005_CONTRACT.json"
PREFLIGHT = NATIVE / "S4_PREFLIGHT_REMAP_RETRY_005.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources() -> dict[str, str]:
    return {
        "authority": "1" * 64,
        "candidate_oracle": "2" * 64,
        "controller": "3" * 64,
        "production": "4" * 64,
        "runner": "5" * 64,
        "test": "6" * 64,
    }


def _build() -> dict:
    return build_s4_remap_authority(
        contract=_load(CONTRACT),
        contract_file_sha256=sha256_file(CONTRACT),
        preflight=_load(PREFLIGHT),
        preflight_file_sha256=sha256_file(PREFLIGHT),
        source_sha256=_sources(),
    )


def test_authority_binds_new_runs_candidate_policy_and_no_later_stage() -> None:
    authority = verify_s4_remap_authority(_build())
    contract = _load(CONTRACT)

    assert authority["payload"]["runs"] == contract["runs"]
    assert authority["payload"]["private_cache"] == contract["private_cache"]
    assert authority["payload"]["candidate_oracle"] == contract[
        "candidate_oracle"
    ]
    assert authority["payload"]["remap_hard_gates"] == contract[
        "remap_hard_gates"
    ]
    assert authority["payload"]["authority"] == {
        "single_use": True,
        "s4_remap_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_authority_rejects_contract_preflight_or_source_drift() -> None:
    contract = _load(CONTRACT)
    contract["candidate_oracle"]["exact_lookup_first"] = False
    with pytest.raises(ValueError):
        build_s4_remap_authority(
            contract=contract,
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=_load(PREFLIGHT),
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=_sources(),
        )

    preflight = _load(PREFLIGHT)
    preflight["payload"]["s4_contract_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        build_s4_remap_authority(
            contract=_load(CONTRACT),
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=preflight,
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=_sources(),
        )

    sources = _sources()
    del sources["candidate_oracle"]
    with pytest.raises(ValueError, match="source"):
        build_s4_remap_authority(
            contract=_load(CONTRACT),
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=_load(PREFLIGHT),
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=sources,
        )


def test_authority_finalization_and_consumption_are_single_use(
    tmp_path: Path,
) -> None:
    authority_path = tmp_path / "S4_REMAP_SMOKE_AUTHORIZATION.json"
    authority = finalize_s4_remap_authority(
        output_path=authority_path,
        authority=_build()["payload"],
        git_commit="deadbeef",
        run_id="s4-remap-smoke-authority-20260815-005",
    )
    assert verify_s4_remap_authority(authority) == authority
    with pytest.raises(FileExistsError):
        finalize_s4_remap_authority(
            output_path=authority_path,
            authority=_build()["payload"],
            git_commit="deadbeef",
            run_id="s4-remap-smoke-authority-20260815-005",
        )

    consumption_path = tmp_path / "S4_REMAP_AUTHORITY_CONSUMPTION.json"
    consumption = consume_s4_remap_authority(
        authority=authority,
        authority_file_sha256=sha256_file(authority_path),
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s4-remap-authority-consumption-20260815-005",
    )
    assert verify_s4_remap_authority_consumption(consumption) == consumption
    assert consumption["payload"]["consumed_action"] == (
        "S4_REMAP_SMOKE_PIPELINE"
    )
    with pytest.raises(FileExistsError):
        consume_s4_remap_authority(
            authority=authority,
            authority_file_sha256=sha256_file(authority_path),
            output_path=consumption_path,
            git_commit="deadbeef",
            run_id="s4-remap-authority-consumption-20260815-005",
        )


def test_verifiers_reject_hash_shape_scope_or_run_tamper(tmp_path: Path) -> None:
    authority = finalize_s4_remap_authority(
        output_path=tmp_path / "authority.json",
        authority=_build()["payload"],
        git_commit="deadbeef",
        run_id="s4-remap-smoke-authority-20260815-005",
    )
    for mutate in (
        lambda value: value["payload"]["authority"].update(
            s5_authorized=True
        ),
        lambda value: value["payload"]["runs"]["D0_READ_ONLY_REPLAY"].update(
            run_id="s4-d0-replay-20260814-004"
        ),
        lambda value: value["payload"].update(extra="drift"),
        lambda value: value.update(extra="drift"),
    ):
        altered = copy.deepcopy(authority)
        mutate(altered)
        with pytest.raises(ValueError):
            verify_s4_remap_authority(altered)
