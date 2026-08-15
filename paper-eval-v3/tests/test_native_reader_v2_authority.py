"""One-shot authority contracts for the Reader-v2 compatibility canary."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import atomic_write_json, sha256_file
from paper_eval.native_reader_v2 import (
    OfficialConSessionReader,
    common_method_reader_bindings,
)
from paper_eval.native_reader_v2_authority import (
    READER_V2_EVIDENCE_NAMES,
    READER_V2_PREREQUISITE_STATUS,
    ReaderV2AuthorityError,
    build_reader_v2_authorization,
    build_reader_v2_offline_qualification,
    consume_reader_v2_authorization,
    verify_reader_v2_authorization,
    verify_reader_v2_offline_qualification,
)
from paper_eval.native_reader_v2_qualification import build_reader_v2_contract


class _Transport:
    public_config = {
        "implementation": "openai_compatible_chat_completions",
        "served_model_name": "qwen3-32b-fp8",
        "endpoint_identity_sha256": "1" * 64,
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
    }
    config_sha256 = "2" * 64


def _hashes(names) -> dict[str, str]:
    return {
        name: format(index % 16, "x") * 64
        for index, name in enumerate(sorted(names), start=1)
    }


def _contract() -> dict:
    reader = OfficialConSessionReader(
        model="qwen3-32b-fp8",
        transport=_Transport(),
    )
    return build_reader_v2_contract(
        reader_public_config=reader.public_config,
        reader_config_sha256=reader.config_sha256,
        reader_transport_public_config=_Transport.public_config,
        reader_transport_config_sha256=_Transport.config_sha256,
        method_reader_bindings=common_method_reader_bindings(
            reader.config_sha256
        ),
        retrieval_policy_file_sha256="3" * 64,
        judge_identity_sha256="4" * 64,
        historical_direct_result_sha256=(
            "d9fc42a6479e3071fce56b8670a583aaa9ad76ce24687f4b6de957173064733d"
        ),
        canary_history_id="b6019101",
        canary_namespace="nc-e1e2-1deef863d4241064",
        canary_selection={
            "data_role": "DEVELOPMENT_EXPOSED",
            "selection_rule": "first_remaining_frozen_calibration_id",
            "excluded_observed_history_id": "07741c45",
            "selected_before_reader_v2_outcome": True,
            "canary_construction_revision_matches_current_u0": False,
            "canary_use": "ADAPTER_COMPATIBILITY_ONLY",
        },
        disclosure={
            "prior_direct_failure_observed": True,
            "reader_v2_selection_not_blinded": True,
            "change_motivated_by_observed_failure": True,
            "recipe_source": "upstream_recommended",
            "direct_path_was_officially_supported": True,
            "retrieval_or_top_k_candidate_search": False,
        },
        source_sha256=_hashes(
            {
                "workplan",
                "parent_workplan",
                "reader_source",
                "reader_test",
                "qualification_source",
                "qualification_test",
                "historical_result",
            }
        ),
    )


def _prerequisites() -> dict[str, dict[str, str]]:
    return {
        name: {
            "file_sha256": format(index % 16, "x") * 64,
            "payload_sha256": format((index + 8) % 16, "x") * 64,
            "status": status,
        }
        for index, (name, status) in enumerate(
            sorted(READER_V2_PREREQUISITE_STATUS.items()), start=1
        )
    }


def _qualification(tmp_path: Path) -> tuple[dict, Path, Path]:
    contract = _contract()
    contract_path = tmp_path / "NATIVE_READER_V2_CONTRACT.json"
    atomic_write_json(contract_path, contract)
    qualification = build_reader_v2_offline_qualification(
        contract=contract,
        contract_file_sha256=sha256_file(contract_path),
        evidence_sha256=_hashes(READER_V2_EVIDENCE_NAMES),
        prerequisites=_prerequisites(),
        history_id="b6019101",
        namespace="nc-e1e2-1deef863d4241064",
        expected_session_count=49,
        git_commit="deadbeef",
        run_id="native-reader-v2-offline-test",
    )
    qualification_path = tmp_path / "NATIVE_READER_V2_OFFLINE_QUALIFICATION.json"
    atomic_write_json(qualification_path, qualification)
    return qualification, contract_path, qualification_path


def _authorization(tmp_path: Path) -> tuple[dict, dict[str, Path]]:
    qualification, contract_path, qualification_path = _qualification(tmp_path)
    run_id = "native-reader-v2-canary-test-001"
    run_dir = tmp_path / "runs" / run_id
    paths = {
        "qualification": qualification_path,
        "contract": contract_path,
        "consumption": run_dir / "NATIVE_READER_V2_AUTHORIZATION_CONSUMPTION.json",
        "result": run_dir / "NATIVE_READER_V2_RESULT.json",
        "failure": run_dir / "NATIVE_READER_V2_FAILURE.json",
        "run_dir": run_dir,
    }
    authorization = build_reader_v2_authorization(
        qualification=qualification,
        qualification_file_sha256=sha256_file(qualification_path),
        contract_file_sha256=sha256_file(contract_path),
        run_id=run_id,
        history_id="b6019101",
        namespace="nc-e1e2-1deef863d4241064",
        consumption_path=paths["consumption"],
        result_path=paths["result"],
        failure_path=paths["failure"],
        git_commit="deadbeef",
    )
    return authorization, paths


def test_offline_qualification_binds_contract_evidence_and_no_live_io(
    tmp_path: Path,
) -> None:
    qualification, contract_path, _ = _qualification(tmp_path)
    verified = verify_reader_v2_offline_qualification(qualification)

    assert verified["payload"]["status"] == "PASS"
    assert verified["payload"]["contract_file_sha256"] == sha256_file(
        contract_path
    )
    assert set(verified["payload"]["evidence_sha256"]) == (
        READER_V2_EVIDENCE_NAMES
    )
    assert verified["payload"]["live_io_performed"] is False
    assert verified["payload"]["quality_gate_used"] is False
    assert verified["payload"]["s3_authorized"] is False


@pytest.mark.parametrize(
    ("surface", "field", "value"),
    [
        ("prerequisites", "judge_qualification", "FAIL"),
        ("prerequisites", "c2_canary_manifest", "CURRENT_U0_MATCH"),
        ("payload", "quality_gate_used", True),
        ("payload", "live_io_performed", True),
    ],
)
def test_offline_qualification_rejects_prerequisite_or_semantic_drift(
    tmp_path: Path, surface: str, field: str, value: object
) -> None:
    qualification, _, _ = _qualification(tmp_path)
    changed = copy.deepcopy(qualification)
    if surface == "prerequisites":
        changed["payload"]["prerequisites"][field]["status"] = value
    else:
        changed["payload"][field] = value

    with pytest.raises(ReaderV2AuthorityError):
        verify_reader_v2_offline_qualification(changed)


def test_authorization_freezes_exact_one_call_budget_and_terminal_paths(
    tmp_path: Path,
) -> None:
    authorization, paths = _authorization(tmp_path)
    payload = verify_reader_v2_authorization(authorization)["payload"]

    assert payload["authorization"] == "RUN_NATIVE_READER_V2_CANARY_ONCE"
    assert payload["limits"] == {
        "graphiti_search_calls": 1,
        "reader_requests": 1,
        "judge_requests": 1,
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "database_mutation_attempts": 0,
        "cleanup_calls": 0,
        "retry_count": 0,
    }
    assert payload["automatic_retry"] is False
    assert payload["quality_gate_used"] is False
    assert Path(payload["consumption_path"]) == paths["consumption"].resolve()
    assert Path(payload["result_path"]) == paths["result"].resolve()
    assert Path(payload["failure_path"]) == paths["failure"].resolve()


def test_authorization_is_consumed_exclusively_before_live_io(
    tmp_path: Path,
) -> None:
    authorization, paths = _authorization(tmp_path)
    authorization_path = tmp_path / "NATIVE_READER_V2_AUTHORIZATION.json"
    atomic_write_json(authorization_path, authorization)

    consumed = consume_reader_v2_authorization(
        authorization=authorization,
        authorization_file_sha256=sha256_file(authorization_path),
        consumption_path=paths["consumption"],
    )

    assert paths["consumption"].is_file()
    assert consumed["payload"]["status"] == "CONSUMED_BEFORE_LIVE_IO"
    assert consumed["payload"]["live_io_performed_at_consumption"] is False
    with pytest.raises(ReaderV2AuthorityError, match="already consumed"):
        consume_reader_v2_authorization(
            authorization=authorization,
            authorization_file_sha256=sha256_file(authorization_path),
            consumption_path=paths["consumption"],
        )


def test_authorization_rejects_budget_path_or_identity_drift(tmp_path: Path) -> None:
    authorization, paths = _authorization(tmp_path)

    changed = copy.deepcopy(authorization)
    changed["payload"]["limits"]["reader_requests"] = 11
    with pytest.raises(ReaderV2AuthorityError):
        verify_reader_v2_authorization(changed)

    changed = copy.deepcopy(authorization)
    changed["payload"]["history_id"] = "07741c45"
    with pytest.raises(ReaderV2AuthorityError):
        verify_reader_v2_authorization(changed)

    changed = copy.deepcopy(authorization)
    changed["payload"]["result_path"] = str(
        (paths["run_dir"].parent / "other-run" / "result.json").resolve()
    )
    with pytest.raises(ReaderV2AuthorityError):
        verify_reader_v2_authorization(changed)


def test_authority_artifacts_never_persist_raw_content_or_credentials(
    tmp_path: Path,
) -> None:
    authorization, _ = _authorization(tmp_path)
    encoded = json.dumps(authorization, sort_keys=True).lower()

    for forbidden in (
        "api_key",
        "raw_prompt",
        "raw_output",
        "has_answer",
        "private question",
        "private answer",
    ):
        assert forbidden not in encoded
