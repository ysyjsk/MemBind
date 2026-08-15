"""TDD contracts for the additive Native-v2 S3 configuration freeze.

The historical Gate-C implementation remains untouched.  These tests bind the
already sealed Native construction/retrieval evidence to Reader-v2 without
turning the development canary's QA score into a selection gate.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s3_native_v2_freeze import (
    NativeBaselineV2FreezeError,
    build_native_baseline_v2_freeze,
    finalize_native_baseline_v2_freeze,
    verify_native_baseline_v2_freeze,
)


PROJECT = Path(__file__).resolve().parents[1]
ARTIFACTS = PROJECT / "artifacts/paper_eval"
NATIVE = ARTIFACTS / "native"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixture() -> dict:
    paths = {
        "s0_current_state": ARTIFACTS / "S0_CURRENT_STATE.json",
        "s1_u0_smoke": NATIVE / "U0_SMOKE.json",
        "u0_qualification": NATIVE / "U0_QUALIFICATION.json",
        "dataset_parity": NATIVE / "DATASET_PARITY.json",
        "evaluator_parity": NATIVE / "EVALUATOR_PARITY.json",
        "direct_add_episode_contract": (
            NATIVE / "U0_DIRECT_ADD_EPISODE_CONTRACT.json"
        ),
        "completion_adapter_identity": (
            NATIVE / "S2_COMPLETION_ADAPTER_IDENTITY.json"
        ),
        "retrieval_contract": NATIVE / "S2_COMPLETION_CONTRACT.json",
        "retrieval_policy_freeze": NATIVE / "S2_COMPLETION_POLICY_FREEZE.json",
        "role_registry": ARTIFACTS / "DEVELOPMENT_EXPOSED_IDS.json",
        "reader_v2_contract": NATIVE / "NATIVE_READER_V2_CONTRACT.json",
        "reader_v2_freeze": NATIVE / "NATIVE_READER_V2_FREEZE.json",
    }
    loaded = {name: _load(path) for name, path in paths.items()}
    return {
        **loaded,
        "input_file_sha256": {
            "parent_workplan": loaded["s0_current_state"]["payload"][
                "source_hashes"
            ]["protocol"],
            "reader_v2_workplan": loaded["reader_v2_freeze"]["payload"][
                "source_sha256"
            ]["workplan"],
            **{name: sha256_file(path) for name, path in paths.items()},
        },
        "source_sha256": {
            "finalize_script": "3" * 64,
            "focused_green_preseal": "4" * 64,
            "freeze_source": "5" * 64,
            "freeze_test": "6" * 64,
            "full_offline_green_preseal": "7" * 64,
        },
    }


def _build(fixture: dict | None = None) -> dict:
    selected = fixture or _fixture()
    return build_native_baseline_v2_freeze(
        s0_current_state=selected["s0_current_state"],
        s1_u0_smoke=selected["s1_u0_smoke"],
        u0_qualification=selected["u0_qualification"],
        dataset_parity=selected["dataset_parity"],
        evaluator_parity=selected["evaluator_parity"],
        direct_add_episode_contract=selected["direct_add_episode_contract"],
        completion_adapter_identity=selected["completion_adapter_identity"],
        retrieval_contract=selected["retrieval_contract"],
        retrieval_policy_freeze=selected["retrieval_policy_freeze"],
        role_registry=selected["role_registry"],
        reader_v2_contract=selected["reader_v2_contract"],
        reader_v2_freeze=selected["reader_v2_freeze"],
        input_file_sha256=selected["input_file_sha256"],
        source_sha256=selected["source_sha256"],
        git_commit="deadbeef",
        run_id="native-baseline-v2-freeze-test-001",
    )


def _reseal(envelope: dict) -> None:
    envelope["payload_sha256"] = payload_sha256(envelope["payload"])


def _serialized_sha256(value: dict) -> str:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _all_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_all_keys(child))
    return keys


def test_builds_additive_native_v2_freeze_without_live_authority() -> None:
    freeze = verify_native_baseline_v2_freeze(_build())
    payload = freeze["payload"]

    assert payload["schema_version"] == (
        "membind.paper-eval-v3.native-baseline-v2-freeze.v1"
    )
    assert payload["stage"] == "S3"
    assert payload["status"] == "PASS"
    assert payload["baseline_id"] == "native-graphiti-u0-reader-v2"
    assert payload["historical_gate_c_implementation_untouched"] is True
    assert payload["configuration_change_scope"] == (
        "READER_ONLY_RELATIVE_TO_S2_COMPLETION_POLICY_FREEZE"
    )
    assert payload["configuration_freeze_only"] is True
    assert payload["s2_quality_pass_claimed"] is False
    assert payload["quality_estimate_status"] == "NOT_ESTIMATED"
    assert payload["authority"] == {
        "native_configuration_frozen": True,
        "next_offline_stage": "S4",
        "pilot_execution_authorized": False,
        "s4_live_execution_authorized": False,
    }


def test_binds_one_common_dataset_retrieval_reader_and_judge_policy() -> None:
    payload = _build()["payload"]
    common = payload["common_evaluation_policy"]

    assert common["retrieval_policy_name"] == (
        "graphiti-0.29.3-episode-bm25-session-v1"
    )
    assert common["retrieval_surface"] == "graphiti_episode_bm25"
    assert common["retrieval_method"] == "Graphiti.search_"
    assert common["retrieval_top_k"] == 10
    assert common["retrieval_top_k_unit"] == "unique_session"
    assert common["dataset_sha256"] == (
        "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
    )
    assert common["reader_config_sha256"] == (
        "35cda64f27664f1901b2bf129cc95b5d77e8c51cac90abfbbe1c4118dd92737b"
    )
    assert common["judge_transport_config_sha256"] == (
        "97fc7c64f9ce991383e68269054254dcd36790dc493d9795418f58b763d27d6d"
    )
    assert common["judge_component_config_sha256"] == (
        "bfdef9ccfc25938153473056962e4f91d3a7924e56b6a6f7672dcbdc6877acdd"
    )
    assert set(payload["method_policy_bindings"]) == {"U0", "A0", "P*", "M*"}
    assert len(set(payload["method_policy_bindings"].values())) == 1
    assert next(iter(payload["method_policy_bindings"].values())) == (
        payload_sha256(common)
    )


def test_freeze_projects_runtime_and_s1_integrity_without_raw_content() -> None:
    payload = _build()["payload"]
    construction = payload["native_construction"]

    assert construction["graphiti"] == {
        "repository_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "version": "0.29.3",
    }
    assert construction["history_id"] == "07741c45"
    assert construction["episode_count"] == 49
    assert construction["serial_source_order"] is True
    assert construction["s1_run_id"] == "s1-20260814-001"
    assert construction["runtime_identity_evidence_scope"] == (
        "DECLARED_EXPECTED_CONFIGURATION_NOT_CURRENT_LIVE_ATTESTATION"
    )
    assert construction["construction_revision_evidence"] == (
        "CONFLICT_DISCLOSED"
    )
    assert construction["s4_live_preflight_required"] is True
    assert payload["critical_source_sha256"]["u0_runtime"] == (
        "4d6bad43289d7cbf9557aed05571601bdfa560855ed1403b0e4a72770ae57ca1"
    )
    assert payload["critical_source_sha256"]["dataset_builder"] == (
        "0dc97963f4e6143b555853d6061967b6e7606d36e0cba66acc70e27ba0a4d163"
    )
    assert payload["critical_source_sha256"]["reader_v2"] == (
        "c4e6cabc6c685bfbed9ced68f669ffadfb59726420814e350b8b3afc991fea76"
    )
    assert not (
        _all_keys(payload)
        & {
            "answer",
            "api_key",
            "content",
            "messages",
            "password",
            "prompt",
            "question",
            "raw_output",
            "secret",
        }
    )


@pytest.mark.parametrize("qa_accuracy", [0.0, 1.0])
def test_reader_canary_qa_is_accepted_but_never_a_freeze_gate(
    qa_accuracy: float,
) -> None:
    fixture = _fixture()
    reader = fixture["reader_v2_freeze"]
    reader["payload"]["qa_accuracy_diagnostic"] = qa_accuracy
    _reseal(reader)
    fixture["input_file_sha256"]["reader_v2_freeze"] = _serialized_sha256(
        reader
    )

    payload = _build(fixture)["payload"]

    assert payload["methodology"]["canary_qa_used_as_selection_gate"] is False
    assert payload["methodology"]["compatibility_only_evidence"] is True
    assert "qa_accuracy_diagnostic" not in _all_keys(payload)
    assert "recall" not in _all_keys(payload)
    assert set(payload["method_policy_bindings"].values()) == set(
        _build()["payload"]["method_policy_bindings"].values()
    )


@pytest.mark.parametrize(
    "artifact_name,mutation,error",
    [
        ("s1_u0_smoke", ("verdict", "FAIL"), "S1 U0 smoke"),
        ("dataset_parity", ("verdict", "FAIL"), "dataset parity"),
        ("evaluator_parity", ("verdict", "FAIL"), "evaluator parity"),
    ],
)
def test_rejects_failed_native_or_alignment_evidence(
    artifact_name: str, mutation: tuple[str, object], error: str
) -> None:
    fixture = _fixture()
    artifact = fixture[artifact_name]
    artifact["payload"][mutation[0]] = mutation[1]
    _reseal(artifact)

    with pytest.raises(NativeBaselineV2FreezeError, match=error):
        _build(fixture)


def test_rejects_incomplete_or_nonserial_s1_coverage() -> None:
    fixture = _fixture()
    smoke = fixture["s1_u0_smoke"]
    smoke["payload"]["coverage"]["published"] = 48
    _reseal(smoke)

    with pytest.raises(NativeBaselineV2FreezeError, match="coverage"):
        _build(fixture)


@pytest.mark.parametrize(
    "field,value",
    [
        ("top_k", 5),
        ("retrieval_surface", "custom_surface"),
        ("retrieval_method", "custom_search"),
    ],
)
def test_rejects_retrieval_policy_drift(field: str, value: object) -> None:
    fixture = _fixture()
    fixture["retrieval_contract"]["retrieval_policy"][field] = value

    with pytest.raises(Exception, match="retrieval policy"):
        _build(fixture)


def test_rejects_outcome_selected_retrieval_policy() -> None:
    fixture = _fixture()
    policy = fixture["retrieval_policy_freeze"]
    policy["payload"]["r0_numeric_score_used_for_policy_choice"] = True
    _reseal(policy)

    with pytest.raises(NativeBaselineV2FreezeError, match="retrieval policy freeze"):
        _build(fixture)


def test_rejects_reader_contract_or_retrieval_policy_file_drift() -> None:
    fixture = _fixture()
    fixture["reader_v2_contract"]["retrieval_policy_file_sha256"] = "0" * 64
    fixture["reader_v2_contract"]["contract_sha256"] = payload_sha256(
        {
            key: value
            for key, value in fixture["reader_v2_contract"].items()
            if key != "contract_sha256"
        }
    )
    with pytest.raises(Exception, match="retrieval policy"):
        _build(fixture)

    fixture = _fixture()
    fixture["reader_v2_contract"]["reader_config_sha256"] = "0" * 64
    with pytest.raises(Exception, match="contract hash|Reader-v2 contract"):
        _build(fixture)


def test_rejects_judge_binding_drift() -> None:
    fixture = _fixture()
    reader = fixture["reader_v2_freeze"]
    reader["payload"]["method_judge_bindings"]["M*"] = "0" * 64
    _reseal(reader)

    with pytest.raises(Exception, match="Reader-v2 freeze"):
        _build(fixture)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("quality_gate", "Reader-v2 freeze"),
        ("reader_binding", "Reader-v2 freeze"),
        ("s3_config_authority", "Reader-v2 freeze"),
    ],
)
def test_rejects_reader_v2_semantic_or_binding_drift(
    mutation: str, error: str
) -> None:
    fixture = _fixture()
    reader = fixture["reader_v2_freeze"]
    if mutation == "quality_gate":
        reader["payload"]["quality_gate_used"] = True
    elif mutation == "reader_binding":
        reader["payload"]["method_reader_bindings"]["M*"] = "9" * 64
    else:
        reader["payload"]["s3_configuration_update_authorized"] = False
    _reseal(reader)

    with pytest.raises(Exception, match=error):
        _build(fixture)


def test_rejects_role_overlap_and_role_drift() -> None:
    fixture = _fixture()
    roles = fixture["role_registry"]
    roles["payload"]["roles"]["PILOT"] = ["07741c45"]
    _reseal(roles)

    with pytest.raises(NativeBaselineV2FreezeError, match="role registry"):
        _build(fixture)

    fixture = _fixture()
    roles = fixture["role_registry"]
    roles["payload"]["roles"]["DEVELOPMENT_EXPOSED"].append("new-id")
    _reseal(roles)
    with pytest.raises(NativeBaselineV2FreezeError, match="role binding drift"):
        _build(fixture)


def test_rejects_missing_runtime_identity_and_invalid_hash_inventory() -> None:
    fixture = _fixture()
    del fixture["s0_current_state"]["payload"]["runtime_identities"]["embedding"]
    _reseal(fixture["s0_current_state"])
    with pytest.raises(NativeBaselineV2FreezeError, match="runtime identities"):
        _build(fixture)

    fixture = _fixture()
    fixture["u0_qualification"]["payload"]["checks"][
        "runtime_identity_current"
    ] = False
    _reseal(fixture["u0_qualification"])
    with pytest.raises(NativeBaselineV2FreezeError, match="qualification"):
        _build(fixture)


def test_rejects_parent_or_reader_workplan_hash_drift() -> None:
    fixture = _fixture()
    fixture["input_file_sha256"]["parent_workplan"] = "0" * 64
    with pytest.raises(NativeBaselineV2FreezeError, match="parent workplan"):
        _build(fixture)

    fixture = _fixture()
    fixture["input_file_sha256"]["reader_v2_workplan"] = "0" * 64
    with pytest.raises(NativeBaselineV2FreezeError, match="Reader-v2 workplan"):
        _build(fixture)

    fixture = _fixture()
    fixture["source_sha256"]["freeze_source"] = "not-a-sha"
    with pytest.raises(NativeBaselineV2FreezeError, match="SHA256"):
        _build(fixture)


def test_rejects_claimed_file_hash_that_does_not_match_serialized_input() -> None:
    fixture = _fixture()
    fixture["reader_v2_freeze"]["git_commit"] = "different-but-benign"

    with pytest.raises(NativeBaselineV2FreezeError, match="input file hash mismatch"):
        _build(fixture)


def test_verifier_rejects_tampering_authority_and_policy_hash() -> None:
    freeze = _build()
    freeze["payload"]["authority"]["pilot_execution_authorized"] = True
    _reseal(freeze)
    with pytest.raises(NativeBaselineV2FreezeError):
        verify_native_baseline_v2_freeze(freeze)

    freeze = _build()
    freeze["payload"]["method_policy_bindings"]["M*"] = "0" * 64
    _reseal(freeze)
    with pytest.raises(NativeBaselineV2FreezeError, match="method policy"):
        verify_native_baseline_v2_freeze(freeze)

    freeze = _build()
    freeze["payload"]["native_construction"][
        "construction_revision_evidence"
    ] = "CURRENT_LIVE_ATTESTED"
    _reseal(freeze)
    with pytest.raises(NativeBaselineV2FreezeError, match="construction"):
        verify_native_baseline_v2_freeze(freeze)


def test_finalization_is_exclusive_and_never_overwrites(tmp_path: Path) -> None:
    freeze = _build()
    path = tmp_path / "NATIVE_BASELINE_V2_FREEZE.json"

    finalized = finalize_native_baseline_v2_freeze(path=path, artifact=freeze)

    assert finalized == verify_native_baseline_v2_freeze(_load(path))
    original = path.read_bytes()
    with pytest.raises(NativeBaselineV2FreezeError, match="already exists"):
        finalize_native_baseline_v2_freeze(path=path, artifact=freeze)
    assert path.read_bytes() == original
