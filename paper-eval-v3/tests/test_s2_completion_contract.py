from __future__ import annotations

import copy

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_completion_contract import (
    S2_COMPLETION_SCHEMA,
    S2CompletionContractError,
    build_s2_completion_contract,
    validate_s2_completion_contract,
)


SHA = "a" * 64


def _inputs() -> dict[str, object]:
    return {
        "retrieval_policy": {
            "policy_name": "graphiti-0.29.3-episode-bm25-session-v1",
            "policy_version": "v1",
            "retrieval_surface": "graphiti_episode_bm25",
            "retrieval_method": "Graphiti.search_",
            "search_recipe": "EPISODE_BM25_RRF",
            "native_result_type": "EpisodicNode",
            "evaluation_result_unit": "LongMemEvalSession",
            "top_k": 10,
            "candidate_limit": 20,
            "top_k_unit": "unique_session",
            "gold_unit": "LongMemEvalSession",
            "metric_name": "per_question_session_recall_all_at_10",
            "aggregate_metric": "mean_per_question_session_recall_all_at_10",
            "reader_input_representation": "longmemeval_flat_session_item",
            "official_longmemeval_session_metric": True,
            "episode_to_session_mapping": "frozen_one_to_one_fail_closed",
            "edge_search_enabled": False,
            "node_search_enabled": False,
            "community_search_enabled": False,
            "query_embedding_used": False,
            "cross_encoder_used": False,
            "search_filters": "empty",
            "group_scope": "exactly_one_history_namespace",
            "question_date_used_for_retrieval": False,
            "retrieval_temporal_filter": "none",
            "custom_fusion_sort_or_dedup": False,
            "implementation_source_sha256": SHA,
            "configuration_sha256": "b" * 64,
        },
        "selection_basis": {
            "kind": "architecture_and_benchmark_semantics_not_blinded",
            "frozen_before_numeric_execution": True,
            "r0_outcome_previously_observed": True,
            "selection_not_blinded": True,
            "r0_numeric_score_used_for_policy_choice": False,
            "candidate_score_search_performed": False,
            "reasons": [
                "BENCHMARK_RESULT_UNIT_ALIGNMENT",
                "UPSTREAM_NATIVE_API",
                "NO_CUSTOM_CROSS_SURFACE_FUSION",
                "SAME_POLICY_FOR_ALL_METHODS",
            ],
            "evidence_sha256": "c" * 64,
        },
        "reader_identity": {
            "implementation": "longmemeval_official_session_reader",
            "input_representation": "longmemeval_flat_session_item",
            "official_flat_session_item_semantics": True,
            "upstream_repository": "xiaowu0162/LongMemEval",
            "upstream_commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
            "upstream_source_path": "src/generation/run_generation.py",
            "upstream_file_sha256": "d" * 64,
            "prompt_template_sha256": "e" * 64,
            "implementation_source_sha256": "f" * 64,
            "model_identity_sha256": "1" * 64,
            "transport_identity_sha256": "2" * 64,
            "retriever_type": "flat-session",
            "topk_context": 10,
            "history_format": "json",
            "useronly": False,
            "cot": False,
            "con": False,
            "merge_key_expansion_into_value": "none",
            "session_value_source": "frozen_dataset_haystack_sessions",
            "has_answer_label_removed": True,
            "episode_content_hash_verified": True,
            "presentation_order": "chronological_after_top_k_rank_stable_ties",
            "truncation_policy": "FAIL_CLOSED_IF_CONTEXT_EXCEEDED",
            "qualification_truncation_count": 0,
            "messages": ["user"],
            "system_prompt": None,
            "temperature": 0,
            "max_tokens": 500,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "thinking_parameter_sent": True,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "retry_delays_seconds": [],
        },
        "judge_identity": {
            "implementation": "qualified_legacy_longmemeval_adapter",
            "rubric_sha256": "3" * 64,
            "parser_sha256": "4" * 64,
            "qualification_artifact_sha256": "5" * 64,
            "qualification_status": "PASS",
            "implementation_source_sha256": "6" * 64,
            "model_identity_sha256": "7" * 64,
            "transport_identity_sha256": "8" * 64,
            "question_type": "knowledge-update",
            "abstention": False,
            "rubric": "official_get_anscheck_prompt",
            "headline_parser": "substring_yes_case_insensitive",
            "audit_parser": "strict_yes_no_invalid",
            "messages": ["user"],
            "system_prompt": None,
            "temperature": 0,
            "max_tokens": 10,
            "n": 1,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "thinking_parameter_sent": True,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
            "retry_delays_seconds": [0.0],
            "invalid_output_policy": "SEAL_FAILURE_AND_STOP",
            "protocol_alignment": "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
            "judge_backend_difference_disclosed": True,
        },
        "role_binding": {
            "role_artifact_sha256": "9" * 64,
            "role_payload_sha256": "0" * 64,
            "evaluation_role": "DEVELOPMENT_EXPOSED",
            "selected_history_ids": ["history-dev"],
            "registry": {
                "DEVELOPMENT_EXPOSED": ["history-dev"],
                "PILOT": ["history-pilot"],
                "FINAL_PAPER_TEST": ["history-final"],
            },
        },
        "metric_surfaces": {
            "retrieval": {
                "name": "evidence_recall_at_10",
                "definition": "per_question_session_recall_all_at_10",
                "aggregation": "mean",
                "unit": "question",
                "top_k": 10,
                "value_source": "ranked_retrieval",
                "substitutable_by_other_metric": False,
            },
            "qa": {
                "name": "qa_accuracy",
                "unit": "question",
                "value_source": "qualified_judge",
                "substitutable_by_other_metric": False,
            },
            "graph_correctness": {
                "name": "graph_sensitive_construction_correctness",
                "unit": "history",
                "value_source": "separate_graph_oracle",
                "substitutable_by_other_metric": False,
            },
        },
        "failure_policy": {
            "max_live_attempts": 1,
            "automatic_retry": False,
            "on_transport_failure": "SEAL_FAILURE_AND_STOP",
            "on_invalid_reader_output": "SEAL_FAILURE_AND_STOP",
            "on_invalid_judge_output": "SEAL_FAILURE_AND_STOP",
            "partial_results_mergeable": False,
        },
        "source_hashes": {
            "completion_contract": "a" * 64,
            "retrieval": "b" * 64,
            "reader": "c" * 64,
            "judge": "d" * 64,
            "roles": "e" * 64,
        },
    }


def _build(**updates: object) -> dict[str, object]:
    inputs = _inputs()
    inputs.update(updates)
    return build_s2_completion_contract(**inputs)


def test_builds_hash_bound_outcome_independent_contract() -> None:
    artifact = _build()

    assert artifact["schema_version"] == S2_COMPLETION_SCHEMA
    assert artifact["contract_sha256"] == payload_sha256(
        {key: value for key, value in artifact.items() if key != "contract_sha256"}
    )
    assert artifact["selection_basis"]["selection_not_blinded"] is True
    assert (
        artifact["selection_basis"]["r0_numeric_score_used_for_policy_choice"]
        is False
    )
    assert artifact["retrieval_policy"]["top_k"] == 10
    assert validate_s2_completion_contract(artifact) == artifact


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("native_result_type", None, "native_result_type"),
        ("native_result_type", "EntityEdge", "native_result_type"),
        ("evaluation_result_unit", "EntityEdge", "evaluation_result_unit"),
        ("top_k_unit", "edge", "top_k_unit"),
        ("gold_unit", "episode", "gold_unit"),
        ("top_k", 20, "top_k"),
        ("candidate_limit", 10, "candidate_limit"),
        ("metric_name", "edge_coverage_at_10", "metric_name"),
    ],
)
def test_rejects_retrieval_metric_and_unit_mismatch(
    field: str, value: object, reason: str
) -> None:
    inputs = _inputs()
    policy = copy.deepcopy(inputs["retrieval_policy"])
    policy[field] = value

    with pytest.raises(S2CompletionContractError, match=reason):
        _build(retrieval_policy=policy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_name", "best-of-development-grid"),
        ("policy_version", "latest"),
        ("retrieval_surface", "graphiti_combined"),
        ("retrieval_method", "custom_search"),
        ("search_recipe", "COMBINED_HYBRID_SEARCH_RRF"),
        ("reader_input_representation", "EntityEdge.fact"),
        ("episode_to_session_mapping", "silent_dedup"),
        ("edge_search_enabled", True),
        ("node_search_enabled", True),
        ("community_search_enabled", True),
        ("query_embedding_used", True),
        ("cross_encoder_used", True),
        ("search_filters", "custom"),
        ("group_scope", "all_namespaces"),
        ("question_date_used_for_retrieval", True),
        ("retrieval_temporal_filter", "before_question_date"),
        ("custom_fusion_sort_or_dedup", True),
    ],
)
def test_rejects_drift_from_the_semantics_selected_episode_policy(
    field: str, value: object
) -> None:
    inputs = _inputs()
    policy = copy.deepcopy(inputs["retrieval_policy"])
    policy[field] = value

    with pytest.raises(S2CompletionContractError, match="retrieval policy"):
        _build(retrieval_policy=policy)


@pytest.mark.parametrize(
    ("component", "field", "value"),
    [
        ("retrieval_policy", "candidate_surface_scores", {"episode": 1.0}),
        ("retrieval_policy", "answer_session_ids", ["gold-leak"]),
        ("reader_identity", "answer_session_ids", ["gold-leak"]),
        ("reader_identity", "has_answer", True),
        ("judge_identity", "development_accuracy", 1.0),
    ],
)
def test_rejects_outcome_fields_and_gold_label_leakage(
    component: str, field: str, value: object
) -> None:
    inputs = _inputs()
    identity = copy.deepcopy(inputs[component])
    identity[field] = value

    with pytest.raises(S2CompletionContractError, match="forbidden field|fields"):
        _build(**{component: identity})


@pytest.mark.parametrize(
    "mutation",
    [
        {"r0_numeric_score_used_for_policy_choice": True},
        {"r0_recall_all": 1.0},
        {"notes": "Selected because S2-R0 scored Recall_all@10 = 1.0"},
        {"frozen_before_numeric_execution": False},
        {"kind": "best_development_score"},
    ],
)
def test_rejects_score_driven_retrieval_policy_selection(
    mutation: dict[str, object],
) -> None:
    inputs = _inputs()
    basis = copy.deepcopy(inputs["selection_basis"])
    basis.update(mutation)

    with pytest.raises(S2CompletionContractError, match="selection basis"):
        _build(selection_basis=basis)


def test_rejects_reader_representation_mismatch() -> None:
    inputs = _inputs()
    reader = copy.deepcopy(inputs["reader_identity"])
    reader["input_representation"] = "EntityEdge.fact"
    reader["official_flat_session_item_semantics"] = False

    with pytest.raises(S2CompletionContractError, match="Reader representation"):
        _build(reader_identity=reader)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("thinking_control", None),
        ("effective_enable_thinking", None),
        ("thinking_parameter_sent", False),
        ("max_attempts", 2),
        ("sdk_hidden_retries", 1),
        ("retry_delays_seconds", [1]),
        ("system_prompt", "hidden"),
        ("has_answer_label_removed", False),
        ("episode_content_hash_verified", False),
        ("qualification_truncation_count", 1),
    ],
)
def test_rejects_unfrozen_reader_request_or_retry_identity(
    field: str, value: object
) -> None:
    inputs = _inputs()
    reader = copy.deepcopy(inputs["reader_identity"])
    reader[field] = value

    with pytest.raises(S2CompletionContractError, match="Reader"):
        _build(reader_identity=reader)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("qualification_status", "FAIL"),
        ("rubric_sha256", "missing"),
        ("parser_sha256", "missing"),
        ("thinking_parameter_sent", False),
        ("max_attempts", 2),
        ("sdk_hidden_retries", 1),
    ],
)
def test_rejects_unqualified_or_unfrozen_judge_identity(
    field: str, value: object
) -> None:
    inputs = _inputs()
    judge = copy.deepcopy(inputs["judge_identity"])
    judge[field] = value

    with pytest.raises(S2CompletionContractError, match="Judge"):
        _build(judge_identity=judge)


def test_rejects_post_build_reader_or_judge_identity_drift() -> None:
    artifact = _build()
    artifact["reader_identity"]["model_identity_sha256"] = "f" * 64

    with pytest.raises(S2CompletionContractError, match="contract hash"):
        validate_s2_completion_contract(artifact)


def test_rejects_data_role_overlap_and_unregistered_selection() -> None:
    inputs = _inputs()
    binding = copy.deepcopy(inputs["role_binding"])
    binding["registry"]["PILOT"] = ["history-dev"]
    with pytest.raises(S2CompletionContractError, match="role overlap"):
        _build(role_binding=binding)

    binding = copy.deepcopy(inputs["role_binding"])
    binding["selected_history_ids"] = ["unknown-history"]
    with pytest.raises(S2CompletionContractError, match="selected history"):
        _build(role_binding=binding)


@pytest.mark.parametrize("missing_surface", ["retrieval", "qa", "graph_correctness"])
def test_requires_three_non_substitutable_quality_surfaces(
    missing_surface: str,
) -> None:
    inputs = _inputs()
    surfaces = copy.deepcopy(inputs["metric_surfaces"])
    del surfaces[missing_surface]

    with pytest.raises(S2CompletionContractError, match="metric surfaces"):
        _build(metric_surfaces=surfaces)


def test_rejects_metric_substitution_or_name_drift() -> None:
    inputs = _inputs()
    surfaces = copy.deepcopy(inputs["metric_surfaces"])
    surfaces["qa"]["name"] = "evidence_recall_at_10"
    with pytest.raises(S2CompletionContractError, match="QA metric"):
        _build(metric_surfaces=surfaces)

    surfaces = copy.deepcopy(inputs["metric_surfaces"])
    surfaces["graph_correctness"]["substitutable_by_other_metric"] = True
    with pytest.raises(S2CompletionContractError, match="substitut"):
        _build(metric_surfaces=surfaces)


def test_rejects_automatic_retry_or_mergeable_partial_results() -> None:
    inputs = _inputs()
    policy = copy.deepcopy(inputs["failure_policy"])
    policy["automatic_retry"] = True
    with pytest.raises(S2CompletionContractError, match="failure policy"):
        _build(failure_policy=policy)

    policy = copy.deepcopy(inputs["failure_policy"])
    policy["partial_results_mergeable"] = True
    with pytest.raises(S2CompletionContractError, match="failure policy"):
        _build(failure_policy=policy)


def test_rejects_raw_content_credentials_and_unbound_sources() -> None:
    inputs = _inputs()
    reader = copy.deepcopy(inputs["reader_identity"])
    reader["raw_prompt"] = "private benchmark question"
    with pytest.raises(S2CompletionContractError, match="forbidden field"):
        _build(reader_identity=reader)

    sources = copy.deepcopy(inputs["source_hashes"])
    del sources["judge"]
    with pytest.raises(S2CompletionContractError, match="source hashes"):
        _build(source_hashes=sources)
