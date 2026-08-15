"""Offline TDD for the single-use S4 smoke-pipeline authority."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_authority import (
    build_s4_smoke_authority,
    consume_s4_smoke_authority,
    finalize_s4_smoke_authority,
    verify_s4_authority_consumption,
    verify_s4_smoke_authority,
)


PROJECT = Path(__file__).resolve().parents[1]
NATIVE = PROJECT / "artifacts/paper_eval/native"
CONTRACT = NATIVE / "S4_D0_CONTRACT.json"
PREFLIGHT = NATIVE / "S4_PREFLIGHT.json"
RETRY_CONTRACT = NATIVE / "S4_D0_RETRY_004_CONTRACT.json"
RETRY_PREFLIGHT = NATIVE / "S4_PREFLIGHT_RETRY_004.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources() -> dict[str, str]:
    return {
        "authority": "1" * 64,
        "controller": "2" * 64,
        "production": "3" * 64,
        "runner": "4" * 64,
        "test": "5" * 64,
    }


def _build() -> dict:
    return build_s4_smoke_authority(
        contract=_load(CONTRACT),
        contract_file_sha256=sha256_file(CONTRACT),
        preflight=_load(PREFLIGHT),
        preflight_file_sha256=sha256_file(PREFLIGHT),
        source_sha256=_sources(),
    )


def test_authority_binds_exact_pipeline_and_remains_pre_pilot() -> None:
    authority = verify_s4_smoke_authority(_build())

    assert authority["payload"]["execution_order"] == [
        "U0_CAPTURE",
        "D0_READ_ONLY_REPLAY",
    ]
    assert authority["payload"]["runs"] == _load(CONTRACT)["runs"]
    assert authority["payload"]["private_cache"] == {
        "embedding_relpath": (
            "runtime/private/s4-d0-07741c45-20260814-001/embedding.jsonl"
        ),
        "prompt_relpath": (
            "runtime/private/s4-d0-07741c45-20260814-001/prompt.jsonl"
        ),
        "reportable_contents": False,
    }
    assert authority["payload"]["authority"] == {
        "single_use": True,
        "s4_smoke_pipeline_authorized": True,
        "d0_replay_requires_capture_pass": True,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


def test_authority_accepts_only_the_sealed_retry_004_identity() -> None:
    authority = build_s4_smoke_authority(
        contract=_load(RETRY_CONTRACT),
        contract_file_sha256=sha256_file(RETRY_CONTRACT),
        preflight=_load(RETRY_PREFLIGHT),
        preflight_file_sha256=sha256_file(RETRY_PREFLIGHT),
        source_sha256=_sources(),
    )

    assert authority["payload"]["runs"] == _load(RETRY_CONTRACT)["runs"]
    assert authority["payload"]["private_cache"] == _load(RETRY_CONTRACT)[
        "private_cache"
    ]
    assert authority["payload"]["s4_contract_sha256"] == _load(RETRY_CONTRACT)[
        "contract_sha256"
    ]
    assert verify_s4_smoke_authority(authority) == authority


def test_authority_rejects_contract_preflight_and_source_drift() -> None:
    contract = _load(CONTRACT)
    contract["runs"]["U0_CAPTURE"]["namespace"] = "pev3-s4-wrong"
    with pytest.raises(Exception):
        build_s4_smoke_authority(
            contract=contract,
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=_load(PREFLIGHT),
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=_sources(),
        )

    preflight = _load(PREFLIGHT)
    preflight["payload"]["authority"]["s4_authority_creation_authorized"] = False
    with pytest.raises(Exception):
        build_s4_smoke_authority(
            contract=_load(CONTRACT),
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=preflight,
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=_sources(),
        )

    sources = _sources()
    del sources["runner"]
    with pytest.raises(ValueError, match="source"):
        build_s4_smoke_authority(
            contract=_load(CONTRACT),
            contract_file_sha256=sha256_file(CONTRACT),
            preflight=_load(PREFLIGHT),
            preflight_file_sha256=sha256_file(PREFLIGHT),
            source_sha256=sources,
        )


def test_authority_finalization_and_consumption_are_exclusive(tmp_path: Path) -> None:
    authority_path = tmp_path / "S4_SMOKE_AUTHORIZATION.json"
    authority = finalize_s4_smoke_authority(
        output_path=authority_path,
        authority=_build()["payload"],
        git_commit="deadbeef",
        run_id="s4-smoke-authority-20260814-001",
    )
    assert verify_s4_smoke_authority(authority) == authority
    with pytest.raises(FileExistsError):
        finalize_s4_smoke_authority(
            output_path=authority_path,
            authority=_build()["payload"],
            git_commit="deadbeef",
            run_id="s4-smoke-authority-20260814-001",
        )

    consumption_path = tmp_path / "S4_SMOKE_AUTHORIZATION_CONSUMPTION.json"
    consumption = consume_s4_smoke_authority(
        authority=authority,
        authority_file_sha256=sha256_file(authority_path),
        output_path=consumption_path,
        git_commit="deadbeef",
        run_id="s4-smoke-authority-consumption-20260814-001",
    )
    assert verify_s4_authority_consumption(consumption) == consumption
    assert consumption["payload"]["consumed_action"] == "S4_SMOKE_PIPELINE"
    with pytest.raises(FileExistsError):
        consume_s4_smoke_authority(
            authority=authority,
            authority_file_sha256=sha256_file(authority_path),
            output_path=consumption_path,
            git_commit="deadbeef",
            run_id="s4-smoke-authority-consumption-20260814-001",
        )


def test_verifiers_reject_hash_shape_and_live_scope_tamper(tmp_path: Path) -> None:
    authority = finalize_s4_smoke_authority(
        output_path=tmp_path / "authority.json",
        authority=_build()["payload"],
        git_commit="deadbeef",
        run_id="s4-smoke-authority-20260814-001",
    )
    for mutate in (
        lambda value: value["payload"]["authority"].update(
            pilot_execution_authorized=True
        ),
        lambda value: value["payload"].update(extra="drift"),
        lambda value: value.update(extra="drift"),
    ):
        altered = copy.deepcopy(authority)
        mutate(altered)
        with pytest.raises(ValueError):
            verify_s4_smoke_authority(altered)
