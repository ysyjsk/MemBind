"""Outcome-independent qualification contracts for Native Reader v2."""

from __future__ import annotations

import copy

import pytest

from paper_eval.native_reader_v2 import (
    OfficialConSessionReader,
    common_method_reader_bindings,
)
from paper_eval.native_reader_v2_qualification import (
    ReaderV2QualificationError,
    build_reader_v2_contract,
    classify_reader_v2_canary,
    select_reader_v2_canary,
    verify_reader_v2_contract,
)
from paper_eval.s2_completion_chain import BoundedCompletionResult
from paper_eval.s2_session_policy import SessionRetrievalMetrics


class _Transport:
    public_config = {
        "implementation": "openai_compatible_chat_completions",
        "served_model_name": "qwen3-32b-fp8",
        "endpoint_identity_sha256": "1" * 64,
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
    }
    config_sha256 = "2" * 64


def _hashes(*names: str) -> dict[str, str]:
    return {
        name: format(index % 16, "x") * 64
        for index, name in enumerate(names, start=1)
    }


def _contract() -> dict[str, object]:
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
            "workplan",
            "parent_workplan",
            "reader_source",
            "reader_test",
            "qualification_source",
            "qualification_test",
            "historical_result",
        ),
    )


def _result(*, qa_accuracy: float) -> BoundedCompletionResult:
    label = qa_accuracy == 1.0
    parse_status = "YES" if label else "NO"
    return BoundedCompletionResult(
        metrics=SessionRetrievalMetrics(
            retrieved_session_count=10,
            gold_session_count=2,
            covered_gold_session_count=2,
            session_recall_any_at_10=1.0,
            session_recall_all_at_10=1.0,
            session_gold_coverage_fraction_at_10=1.0,
            evidence_recall_at_10=1.0,
            gold_ranks=(1, 2),
        ),
        qa_accuracy=qa_accuracy,
        reference_sanity_status="PASS" if label else "REVIEW_REQUIRED",
        reader_evidence={
            "status": "SUCCESS",
            "model": "qwen3-32b-fp8",
            "config_sha256": "a" * 64,
            "prompt_sha256": "b" * 64,
            "prompt_character_count": 100,
            "prompt_byte_count": 100,
            "output_sha256": "c" * 64,
            "output_character_count": 20,
            "output_byte_count": 20,
            "prompt_tokens": 50,
            "completion_tokens": 5,
            "truncation_count": 0,
        },
        judge_evidence={
            "status": "SUCCESS",
            "label": label,
            "model": "qwen3-32b-fp8",
            "prompt_sha256": "d" * 64,
            "config_sha256": "e" * 64,
            "output_sha256": "f" * 64,
            "output_character_count": 3,
            "output_byte_count": 3,
            "parse_status": parse_status,
            "retry_count": 0,
            "error_class": None,
        },
        counters={
            "graphiti_search_calls": 1,
            "neo4j_read_requests": 2,
            "reader_requests": 1,
            "judge_requests": 1,
            "construction_llm_requests": 0,
            "embedding_requests": 0,
            "cross_encoder_requests": 0,
            "database_mutation_attempts": 0,
            "database_mutations": 0,
            "cleanup_calls": 0,
            "retry_count": 0,
        },
        retrieved_session_ids=tuple(f"s{index}" for index in range(10)),
        gold_session_ids=("s0", "s1"),
        history_id="b6019101",
        namespace="nc-e1e2-1deef863d4241064",
    )


def test_canary_selection_uses_frozen_order_and_excludes_observed_item() -> None:
    selected = select_reader_v2_canary(
        frozen_calibration_ids=(
            "07741c45",
            "b6019101",
            "6071bd76",
            "a2f3aa27",
        ),
        development_exposed_ids={
            "07741c45",
            "b6019101",
            "6071bd76",
            "a2f3aa27",
            "c6853660",
        },
        excluded_observed_history_id="07741c45",
    )

    assert selected == "b6019101"


def test_canary_selection_rejects_unexposed_or_missing_exclusion() -> None:
    with pytest.raises(ReaderV2QualificationError):
        select_reader_v2_canary(
            frozen_calibration_ids=("07741c45", "b6019101"),
            development_exposed_ids={"07741c45"},
            excluded_observed_history_id="07741c45",
        )
    with pytest.raises(ReaderV2QualificationError):
        select_reader_v2_canary(
            frozen_calibration_ids=("b6019101",),
            development_exposed_ids={"b6019101"},
            excluded_observed_history_id="07741c45",
        )


def test_contract_discloses_post_observation_change_and_freezes_common_reader() -> None:
    contract = verify_reader_v2_contract(_contract())

    assert contract["schema_version"] == (
        "membind.paper-eval-v3.native-reader-v2-contract.v1"
    )
    assert contract["reader"]["reading_method"] == "con"
    assert contract["reader"]["cot"] is True
    assert contract["reader"]["con"] is False
    assert contract["reader"]["max_tokens"] == 800
    assert contract["reader"]["reader_requests_per_question"] == 1
    assert len(set(contract["method_reader_bindings"].values())) == 1
    assert contract["disclosure"]["reader_v2_selection_not_blinded"] is True
    assert contract["canary_selection"]["canary_use"] == (
        "ADAPTER_COMPATIBILITY_ONLY"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("reader", "cot"), False),
        (("reader", "con"), True),
        (("reader", "max_tokens"), 500),
        (("reader", "reader_requests_per_question"), 11),
        (("disclosure", "prior_direct_failure_observed"), False),
        (("disclosure", "retrieval_or_top_k_candidate_search"), True),
        (("canary_selection", "selected_before_reader_v2_outcome"), False),
        (("canary_selection", "canary_use"), "NATIVE_QUALITY"),
        (("method_reader_bindings", "M*"), "0" * 64),
    ],
)
def test_contract_rejects_semantic_or_fairness_drift(
    path: tuple[str, str], value: object
) -> None:
    contract = copy.deepcopy(_contract())
    contract[path[0]][path[1]] = value
    contract.pop("contract_sha256")

    with pytest.raises(ReaderV2QualificationError):
        verify_reader_v2_contract(contract)


@pytest.mark.parametrize("qa_accuracy", [0.0, 1.0])
def test_canary_compatibility_pass_is_independent_of_qa_label(
    qa_accuracy: float,
) -> None:
    classification = classify_reader_v2_canary(_result(qa_accuracy=qa_accuracy))

    assert classification["compatibility_status"] == "PASS"
    assert classification["quality_gate_used"] is False
    assert classification["qa_accuracy_diagnostic"] == qa_accuracy
    assert classification["native_quality_mergeable"] is False
    assert classification["pilot_or_final_mergeable"] is False
    assert classification["s3_authorized"] is False


@pytest.mark.parametrize(
    ("surface", "field", "value"),
    [
        ("counters", "reader_requests", 2),
        ("counters", "judge_requests", 0),
        ("counters", "construction_llm_requests", 1),
        ("counters", "retry_count", 1),
        ("reader_evidence", "truncation_count", 1),
        ("reader_evidence", "status", "FAILED"),
        ("judge_evidence", "parse_status", "INVALID"),
    ],
)
def test_canary_rejects_budget_transport_or_parse_failure(
    surface: str, field: str, value: object
) -> None:
    result = _result(qa_accuracy=1.0)
    target = getattr(result, surface)
    target[field] = value

    with pytest.raises(ReaderV2QualificationError):
        classify_reader_v2_canary(result)


def test_contract_rejects_raw_or_secret_fields_recursively() -> None:
    contract = copy.deepcopy(_contract())
    contract["source_sha256"]["raw_prompt"] = "private prompt"
    contract.pop("contract_sha256")

    with pytest.raises(ReaderV2QualificationError, match="unsafe"):
        verify_reader_v2_contract(contract)
