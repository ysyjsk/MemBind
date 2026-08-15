"""Offline TDD contracts for the minimal S4 U0-capture/D0-replay smoke."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_d0_contract import (
    S4D0ContractError,
    build_s4_d0_contract,
    finalize_s4_d0_contract,
    verify_s4_d0_contract,
)


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
NATIVE = PROJECT / "artifacts/paper_eval/native"
FREEZE_PATH = NATIVE / "NATIVE_BASELINE_V2_FREEZE.json"
POINTER_PATH = PROJECT / "runtime/CURRENT_STAGE_STATUS.json"
WORKPLAN = PROJECT / "S4_D0_EXECUTION_WORKPLAN_v1.0.md"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sources() -> dict[str, str]:
    return {
        "canonicalizer": sha256_file(
            ROOT / "membind-validation/src/canonicalize_graph.py"
        ),
        "embedding_oracle": sha256_file(
            ROOT / "membind-validation/src/embedding_cache.py"
        ),
        "graphiti_d0_factory": sha256_file(
            ROOT / "membind-validation/src/graphiti_native.py"
        ),
        "native_u0_runtime": sha256_file(
            ROOT / "membind-validation/src/native_characterization_runtime.py"
        ),
        "prompt_oracle": sha256_file(
            ROOT / "membind-validation/src/response_cache.py"
        ),
        "s1_namespace_adapter": sha256_file(PROJECT / "src/paper_eval/s1_live.py"),
        "s4_contract_source": sha256_file(
            PROJECT / "src/paper_eval/s4_d0_contract.py"
        ),
        "s4_contract_test": sha256_file(PROJECT / "tests/test_s4_d0_contract.py"),
    }


def _build(
    *, freeze: dict | None = None, pointer: dict | None = None
) -> dict:
    return build_s4_d0_contract(
        native_baseline_v2_freeze=freeze or _load(FREEZE_PATH),
        native_baseline_v2_freeze_file_sha256=sha256_file(FREEZE_PATH),
        current_pointer=pointer or _load(POINTER_PATH),
        s4_workplan_sha256=sha256_file(WORKPLAN),
        source_sha256=_sources(),
    )


def _all_keys(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            result.add(str(key).lower())
            result.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_all_keys(child))
    return result


def test_contract_freezes_one_history_capture_then_read_only_replay() -> None:
    contract = verify_s4_d0_contract(_build())

    assert contract["schema_version"] == "membind.paper-eval-v3.s4-d0-contract.v1"
    assert contract["history"] == {
        "data_role": "DEVELOPMENT_EXPOSED",
        "episode_count": 49,
        "history_id": "07741c45",
    }
    assert contract["execution_order"] == ["U0_CAPTURE", "D0_READ_ONLY_REPLAY"]
    assert contract["runs"] == {
        "D0_READ_ONLY_REPLAY": {
            "cache_id": "s4-d0-07741c45-20260814-001",
            "method": "D0",
            "mode": "replay",
            "namespace": "pev3-s4-d0-replay-20260814-001",
            "run_id": "s4-d0-replay-20260814-001",
        },
        "U0_CAPTURE": {
            "cache_id": "s4-d0-07741c45-20260814-001",
            "method": "U0",
            "mode": "capture",
            "namespace": "pev3-s4-u0-capture-20260814-001",
            "run_id": "s4-d0-capture-20260814-001",
        },
    }


def test_u0_is_native_and_d0_declares_only_deterministic_controls() -> None:
    contract = _build()

    assert contract["u0_capture"] == {
        "candidate_order_stabilization": False,
        "embedding_oracle_mode": "capture_empty_new",
        "live_embedding_calls_required": True,
        "live_llm_calls_required": True,
        "prior_cache_allowed": False,
        "prompt_oracle_mode": "capture_empty_new",
        "serial_add_episode": True,
    }
    assert contract["d0_replay"] == {
        "candidate_order_stabilizers": [
            "edge_search",
            "node_resolution",
            "edge_query",
            "node_query",
        ],
        "embedding_oracle_mode": "read_only",
        "live_embedding_fallback": False,
        "live_llm_fallback": False,
        "prompt_oracle_mode": "read_only",
        "serial_add_episode": True,
    }


def test_hard_gates_require_exact_parity_and_namespace_scoped_cleanup() -> None:
    contract = _build()
    gates = contract["hard_gates"]

    assert gates["capture_episode_coverage"] == "49/49_exactly_once_source_order"
    assert gates["replay_episode_coverage"] == "49/49_exactly_once_source_order"
    assert gates["canonical_graph_parity"] == "EXACT_100_PERCENT"
    assert gates["replay_oracle_miss_count"] == 0
    assert gates["replay_live_fallback_count"] == 0
    assert gates["replay_cross_encoder_call_count"] == 0
    assert gates["cache_mutation_during_replay"] is False
    assert contract["canonical_comparison"] == {
        "artifact_only": True,
        "entity_group_id_projection": "__S4_ISOLATED_NAMESPACE__",
        "other_fields_normalized_beyond_existing_canonicalizer": False,
    }
    assert contract["namespace_policy"] == {
        "capture_and_replay_distinct": True,
        "cleanup_scope": "EXACT_GROUP_ID_ONLY",
        "fresh_before_first_mutation": True,
        "global_database_cleanup_allowed": False,
        "historical_s1_namespace_read_only": True,
    }
    assert not any("f1" in key for key in _all_keys(contract))


def test_contract_preserves_s3_common_policy_and_has_no_live_authority() -> None:
    freeze = _load(FREEZE_PATH)
    contract = _build(freeze=freeze)

    assert contract["native_baseline_v2_freeze_file_sha256"] == sha256_file(
        FREEZE_PATH
    )
    assert contract["native_baseline_v2_freeze_payload_sha256"] == freeze[
        "payload_sha256"
    ]
    assert contract["common_method_policy_sha256"] == next(
        iter(freeze["payload"]["method_policy_bindings"].values())
    )
    assert contract["authority"] == {
        "pilot_execution_authorized": False,
        "s4_live_execution_authorized": False,
        "s4_preflight_authorized": True,
    }
    assert not _all_keys(contract) & {
        "answer",
        "api_key",
        "content",
        "messages",
        "prompt",
        "question",
        "raw_output",
        "secret",
    }


def test_preflight_is_required_for_disclosed_revision_conflict() -> None:
    preflight = _build()["preflight"]

    assert preflight == {
        "construction_model": "qwen3-32b-fp8",
        "construction_revision_conflict_disclosed": True,
        "embedding_model": "qwen3-embedding-0.6b",
        "max_model_len_minimum": 65536,
        "neo4j_connectivity_required": True,
        "required_before_live_authority": True,
        "vllm_version": "0.26.0",
    }


def test_rejects_s3_or_current_pointer_drift() -> None:
    freeze = _load(FREEZE_PATH)
    freeze["payload"]["authority"]["s4_live_execution_authorized"] = True
    with pytest.raises(Exception):
        _build(freeze=freeze)

    pointer = _load(POINTER_PATH)
    pointer["payload"]["next_authorized_action"] = "S4_LIVE"
    with pytest.raises(S4D0ContractError, match="current pointer"):
        _build(pointer=pointer)


def test_rejects_role_history_or_episode_count_drift() -> None:
    freeze = _load(FREEZE_PATH)
    freeze["payload"]["role_registry_snapshot"]["DEVELOPMENT_EXPOSED"].remove(
        "07741c45"
    )
    with pytest.raises(Exception):
        _build(freeze=freeze)

    freeze = _load(FREEZE_PATH)
    freeze["payload"]["native_construction"]["episode_count"] = 48
    with pytest.raises(Exception):
        _build(freeze=freeze)


def test_rejects_missing_or_invalid_source_inventory() -> None:
    sources = _sources()
    del sources["prompt_oracle"]
    with pytest.raises(S4D0ContractError, match="source inventory"):
        build_s4_d0_contract(
            native_baseline_v2_freeze=_load(FREEZE_PATH),
            native_baseline_v2_freeze_file_sha256=sha256_file(FREEZE_PATH),
            current_pointer=_load(POINTER_PATH),
            s4_workplan_sha256=sha256_file(WORKPLAN),
            source_sha256=sources,
        )

    sources = _sources()
    sources["prompt_oracle"] = "invalid"
    with pytest.raises(S4D0ContractError, match="SHA256"):
        build_s4_d0_contract(
            native_baseline_v2_freeze=_load(FREEZE_PATH),
            native_baseline_v2_freeze_file_sha256=sha256_file(FREEZE_PATH),
            current_pointer=_load(POINTER_PATH),
            s4_workplan_sha256=sha256_file(WORKPLAN),
            source_sha256=sources,
        )


def test_contract_hash_tamper_and_exclusive_finalization(tmp_path: Path) -> None:
    contract = _build()
    tampered = copy.deepcopy(contract)
    tampered["authority"]["s4_live_execution_authorized"] = True
    with pytest.raises(S4D0ContractError):
        verify_s4_d0_contract(tampered)

    target = tmp_path / "S4_D0_CONTRACT.json"
    finalized = finalize_s4_d0_contract(path=target, contract=contract)
    assert finalized == verify_s4_d0_contract(_load(target))
    original = target.read_bytes()
    with pytest.raises(S4D0ContractError, match="already exists"):
        finalize_s4_d0_contract(path=target, contract=contract)
    assert target.read_bytes() == original
