"""Offline TDD for the additive S4 candidate-remap retry contract."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s4_remap_retry_contract import (
    build_s4_remap_retry_contract,
    finalize_s4_remap_retry_contract,
    verify_s4_remap_retry_contract,
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


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources() -> dict[str, str]:
    return {
        "candidate_oracle": sha256_file(
            PROJECT / "src/paper_eval/s4_candidate_oracle.py"
        ),
        "contract": "1" * 64,
        "production": sha256_file(PROJECT / "src/paper_eval/s4_d0_production.py"),
        "runner": sha256_file(PROJECT / "src/paper_eval/s4_d0_runner.py"),
        "test": sha256_file(Path(__file__)),
    }


def _contract() -> dict:
    return build_s4_remap_retry_contract(
        parent_contract=_load(PARENT),
        parent_contract_file_sha256=sha256_file(PARENT),
        prior_retry_contract=_load(PRIOR_RETRY),
        prior_retry_contract_file_sha256=sha256_file(PRIOR_RETRY),
        diagnosis=_load(DIAGNOSIS),
        diagnosis_file_sha256=sha256_file(DIAGNOSIS),
        amendment_file_sha256=sha256_file(AMENDMENT),
        attempt_number=5,
        source_sha256=_sources(),
    )


def _rehash(value: dict) -> dict:
    selected = copy.deepcopy(value)
    selected.pop("contract_sha256", None)
    selected["contract_sha256"] = payload_sha256(selected)
    return selected


def test_contract_allocates_fresh_attempt_and_freezes_semantic_translation() -> None:
    contract = _contract()

    assert verify_s4_remap_retry_contract(contract) == contract
    assert contract["attempt_id"] == "005"
    assert contract["execution_order"] == ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
    assert contract["runs"] == {
        "U0_CAPTURE": {
            "cache_id": "s4-d0-remap-07741c45-20260815-005",
            "method": "U0",
            "mode": "capture",
            "namespace": "pev3-s4-u0-capture-20260815-005",
            "run_id": "s4-d0-capture-20260815-005",
        },
        "D0_READ_ONLY_REPLAY": {
            "cache_id": "s4-d0-remap-07741c45-20260815-005",
            "method": "D0",
            "mode": "replay",
            "namespace": "pev3-s4-d0-replay-20260815-005",
            "run_id": "s4-d0-replay-20260815-005",
        },
    }
    assert contract["candidate_oracle"] == {
        "exact_lookup_first": True,
        "persistent_cache_mutation": False,
        "translation_kind": "VERIFIED_CANDIDATE_ID_BIJECTION",
        "supported_prompt_names": [
            "dedupe_edges.resolve_edge",
            "dedupe_nodes.nodes",
        ],
        "node_response_fields": [
            "entity_resolutions[].duplicate_candidate_id"
        ],
        "edge_response_fields": ["contradicted_facts[]", "duplicate_facts[]"],
        "fail_closed_on_membership_or_identity_drift": True,
        "raw_or_parsed_cache_write": False,
    }
    assert contract["remap_hard_gates"] == {
        "candidate_oracle_resolution_accounting": "EXACT_PLUS_REMAP_EQUALS_RESOLVED",
        "candidate_remap_breakdown": "NODE_PLUS_EDGE_EQUALS_TOTAL",
        "candidate_remap_rejection_count": 0,
        "canonical_graph_parity": "EXACT_100_PERCENT",
        "cache_mutation_during_replay": False,
    }
    assert contract["authority"] == {
        "preflight_authorized": True,
        "live_execution_authorized": False,
        "s4_four_history_qualification_authorized": False,
        "s5_authorized": False,
        "pilot_execution_authorized": False,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(attempt_id="004"),
        lambda value: value["runs"]["D0_READ_ONLY_REPLAY"].update(
            namespace="pev3-s4-d0-replay-20260814-004"
        ),
        lambda value: value["candidate_oracle"].update(exact_lookup_first=False),
        lambda value: value["candidate_oracle"]["supported_prompt_names"].append(
            "dedupe_nodes.node"
        ),
        lambda value: value["candidate_oracle"].update(
            persistent_cache_mutation=True
        ),
        lambda value: value["remap_hard_gates"].update(
            candidate_remap_rejection_count=1
        ),
        lambda value: value["authority"].update(live_execution_authorized=True),
        lambda value: value.update(extra="drift"),
    ],
)
def test_contract_rejects_identity_semantics_or_authority_drift(mutate) -> None:
    altered = copy.deepcopy(_contract())
    mutate(altered)
    altered = _rehash(altered)

    with pytest.raises(ValueError):
        verify_s4_remap_retry_contract(altered)


def test_builder_rejects_a_tampered_diagnosis_or_prior_retry() -> None:
    diagnosis = _load(DIAGNOSIS)
    diagnosis["payload"]["diagnosis"]["candidate_set_equal"] = False
    diagnosis["payload_sha256"] = payload_sha256(diagnosis["payload"])

    with pytest.raises(ValueError, match="diagnosis"):
        build_s4_remap_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            prior_retry_contract=_load(PRIOR_RETRY),
            prior_retry_contract_file_sha256=sha256_file(PRIOR_RETRY),
            diagnosis=diagnosis,
            diagnosis_file_sha256=sha256_file(DIAGNOSIS),
            amendment_file_sha256=sha256_file(AMENDMENT),
            attempt_number=5,
            source_sha256=_sources(),
        )

    prior = _load(PRIOR_RETRY)
    prior["authority"]["live_execution_authorized"] = True
    prior["contract_sha256"] = payload_sha256(
        {key: value for key, value in prior.items() if key != "contract_sha256"}
    )
    with pytest.raises(ValueError):
        build_s4_remap_retry_contract(
            parent_contract=_load(PARENT),
            parent_contract_file_sha256=sha256_file(PARENT),
            prior_retry_contract=prior,
            prior_retry_contract_file_sha256=sha256_file(PRIOR_RETRY),
            diagnosis=_load(DIAGNOSIS),
            diagnosis_file_sha256=sha256_file(DIAGNOSIS),
            amendment_file_sha256=sha256_file(AMENDMENT),
            attempt_number=5,
            source_sha256=_sources(),
        )


def test_contract_finalizer_is_exclusive(tmp_path: Path) -> None:
    target = tmp_path / "S4_D0_REMAP_RETRY_005_CONTRACT.json"
    contract = _contract()

    assert finalize_s4_remap_retry_contract(path=target, contract=contract) == contract
    assert json.loads(target.read_text(encoding="ascii")) == contract
    with pytest.raises(FileExistsError):
        finalize_s4_remap_retry_contract(path=target, contract=contract)
