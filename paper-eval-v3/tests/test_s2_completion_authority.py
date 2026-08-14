from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s2_completion_authority import (
    AUTHORIZATION_ACTION,
    CompletionAuthorityError,
    build_completion_authorization,
    build_completion_offline_qualification,
    build_completion_policy_freeze,
    consume_completion_authorization,
    verify_completion_authorization,
    verify_completion_offline_qualification,
    verify_completion_policy_freeze,
)


RUN_ID = "s2-completion-20260814-001"
HISTORY_ID = "07741c45"
NAMESPACE = "pev3-s1-20260814-001"


def _policy_bindings() -> dict[str, str]:
    names = (
        "parent_protocol",
        "retrieval_amendment",
        "execution_workplan",
        "research_basis",
        "completion_contract_source",
        "completion_contract_test",
        "session_policy_source",
        "session_policy_test",
        "session_reader_source",
        "session_reader_test",
        "completion_chain_source",
        "completion_chain_test",
        "completion_identity_source",
        "completion_identity_test",
        "formal_retrieval_source",
        "formal_retrieval_test",
        "completion_authority_source",
        "completion_authority_test",
        "completion_controller_source",
        "completion_controller_test",
        "completion_production_source",
        "completion_production_test",
        "finalize_script",
        "run_script",
        "focused_green",
        "full_offline_green",
    )
    return {
        name: format(index % 16, "x") * 64
        for index, name in enumerate(names, start=1)
    }


def _policy() -> dict:
    return build_completion_policy_freeze(
        contract_file_sha256="a" * 64,
        contract_sha256="b" * 64,
        adapter_identity_file_sha256="c" * 64,
        adapter_identity_sha256="d" * 64,
        evidence_sha256=_policy_bindings(),
        git_commit="deadbeef",
        run_id="s2-completion-policy-001",
    )


def _prerequisites() -> dict[str, dict[str, str]]:
    statuses = {
        "s1_smoke": "PASS",
        "u0_qualification": "PASS",
        "dataset_parity": "PASS",
        "evaluator_parity": "PASS",
        "development_roles": "PASS",
        "current_state": "VERIFIED",
        "judge_qualification": "PASS",
        "s2r0_chain": "VERIFIED",
    }
    return {
        name: {
            "file_sha256": format(index % 16, "x") * 64,
            "payload_sha256": format((index + 8) % 16, "x") * 64,
            "status": status,
        }
        for index, (name, status) in enumerate(statuses.items(), start=1)
    }


def _qualification() -> dict:
    policy = _policy()
    return build_completion_offline_qualification(
        policy_freeze=policy,
        policy_freeze_file_sha256="e" * 64,
        prerequisites=_prerequisites(),
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        expected_session_count=49,
        expected_gold_count=2,
        git_commit="deadbeef",
        run_id="s2-completion-qualification-001",
    )


def _authorization(tmp_path: Path) -> dict:
    qualification = _qualification()
    run_dir = tmp_path / RUN_ID
    return build_completion_authorization(
        qualification=qualification,
        qualification_file_sha256="f" * 64,
        policy_freeze_file_sha256="e" * 64,
        adapter_identity_file_sha256="c" * 64,
        adapter_identity_sha256="d" * 64,
        run_id=RUN_ID,
        history_id=HISTORY_ID,
        namespace=NAMESPACE,
        consumption_path=run_dir / "S2_COMPLETION_AUTHORIZATION_CONSUMPTION.json",
        result_path=run_dir / "S2_COMPLETION_RESULT.json",
        failure_path=run_dir / "S2_COMPLETION_FAILURE.json",
        git_commit="deadbeef",
    )


def test_policy_freeze_is_hash_bound_and_honestly_discloses_r0_exposure() -> None:
    policy = _policy()

    payload = policy["payload"]
    assert payload["retrieval_policy_selected"] is True
    assert payload["policy_name"] == "graphiti-0.29.3-episode-bm25-session-v1"
    assert payload["r0_outcome_previously_observed"] is True
    assert payload["selection_not_blinded"] is True
    assert payload["r0_numeric_score_used_for_policy_choice"] is False
    assert payload["candidate_score_search_performed"] is False
    assert payload["live_authorized"] is False
    assert payload["s3_authorized"] is False
    assert verify_completion_policy_freeze(policy) == policy


def test_policy_freeze_rejects_missing_binding_or_resealed_score_field() -> None:
    bindings = _policy_bindings()
    del bindings["session_reader_test"]
    with pytest.raises(CompletionAuthorityError, match="evidence"):
        build_completion_policy_freeze(
            contract_file_sha256="a" * 64,
            contract_sha256="b" * 64,
            adapter_identity_file_sha256="c" * 64,
            adapter_identity_sha256="d" * 64,
            evidence_sha256=bindings,
            git_commit="deadbeef",
            run_id="policy-invalid",
        )

    policy = _policy()
    policy["payload"]["r0_recall_all_at_10"] = 1.0
    policy["payload_sha256"] = payload_sha256(policy["payload"])
    with pytest.raises(CompletionAuthorityError, match="score|shape"):
        verify_completion_policy_freeze(policy)


def test_offline_qualification_requires_every_passed_prerequisite() -> None:
    qualification = _qualification()
    assert qualification["payload"]["verdict"] == "PASS"
    assert qualification["payload"]["live_authorized"] is False
    assert verify_completion_offline_qualification(qualification) == qualification

    prerequisites = _prerequisites()
    prerequisites["judge_qualification"]["status"] = "FAIL"
    with pytest.raises(CompletionAuthorityError, match="prerequisite"):
        build_completion_offline_qualification(
            policy_freeze=_policy(),
            policy_freeze_file_sha256="e" * 64,
            prerequisites=prerequisites,
            history_id=HISTORY_ID,
            namespace=NAMESPACE,
            expected_session_count=49,
            expected_gold_count=2,
            git_commit="deadbeef",
            run_id="qualification-invalid",
        )


def test_authorization_binds_exact_one_shot_paths_and_budget(tmp_path: Path) -> None:
    authorization = _authorization(tmp_path)

    payload = authorization["payload"]
    assert payload["authorization"] == AUTHORIZATION_ACTION
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
    assert payload["s3_authorized"] is False
    assert verify_completion_authorization(authorization) == authorization


def test_authorization_rejects_tamper_even_when_payload_is_resealed(
    tmp_path: Path,
) -> None:
    authorization = _authorization(tmp_path)
    authorization["payload"]["limits"]["reader_requests"] = 2
    authorization["payload_sha256"] = payload_sha256(authorization["payload"])

    with pytest.raises(CompletionAuthorityError, match="budget"):
        verify_completion_authorization(authorization)


def test_consumption_is_exclusive_hash_bound_and_precedes_live_io(
    tmp_path: Path,
) -> None:
    authorization = _authorization(tmp_path)
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(
        json.dumps(authorization, sort_keys=True) + "\n", encoding="utf-8"
    )
    consumption_path = Path(authorization["payload"]["consumption_path"])

    consumption = consume_completion_authorization(
        authorization=authorization,
        authorization_file_sha256=sha256_file(authorization_path),
        consumption_path=consumption_path,
    )

    assert consumption["payload"]["status"] == "CONSUMED_BEFORE_LIVE_IO"
    assert consumption["payload"]["live_io_performed_at_consumption"] is False
    assert consumption["payload"]["authorization_sha256"] == sha256_file(
        authorization_path
    )
    assert consumption_path.is_file()
    with pytest.raises(CompletionAuthorityError, match="already consumed"):
        consume_completion_authorization(
            authorization=authorization,
            authorization_file_sha256=sha256_file(authorization_path),
            consumption_path=consumption_path,
        )


def test_authority_artifacts_contain_no_raw_content_or_credentials(
    tmp_path: Path,
) -> None:
    values = [_policy(), _qualification(), _authorization(tmp_path)]
    encoded = json.dumps(values, sort_keys=True).lower()
    for forbidden in (
        "api_key",
        "password",
        "raw_prompt",
        "raw_answer",
        "bearer ",
    ):
        assert forbidden not in encoded
