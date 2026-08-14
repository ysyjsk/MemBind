"""Pure contract for completing S2 without selecting policy from S2-R0 scores.

The module validates identities and metric semantics only.  It performs no
file, environment, database, model, Reader, or Judge I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256


S2_COMPLETION_SCHEMA = "membind.paper-eval-v3.s2-completion-contract.v1"
_HEX = frozenset("0123456789abcdef")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "retrieval_policy",
    "selection_basis",
    "reader_identity",
    "judge_identity",
    "role_binding",
    "metric_surfaces",
    "failure_policy",
    "source_hashes",
}
_FORBIDDEN_FIELDS = {
    "answer_session_ids",
    "api_key",
    "candidate_surface_scores",
    "credential",
    "development_accuracy",
    "has_answer",
    "raw_answer",
    "raw_episode",
    "raw_hypothesis",
    "raw_prompt",
    "raw_question",
    "raw_reference_answer",
    "raw_response",
}

_RETRIEVAL_POLICY_KEYS = {
    "policy_name",
    "policy_version",
    "retrieval_surface",
    "retrieval_method",
    "search_recipe",
    "native_result_type",
    "evaluation_result_unit",
    "top_k",
    "candidate_limit",
    "top_k_unit",
    "gold_unit",
    "metric_name",
    "aggregate_metric",
    "reader_input_representation",
    "official_longmemeval_session_metric",
    "episode_to_session_mapping",
    "edge_search_enabled",
    "node_search_enabled",
    "community_search_enabled",
    "query_embedding_used",
    "cross_encoder_used",
    "search_filters",
    "group_scope",
    "question_date_used_for_retrieval",
    "retrieval_temporal_filter",
    "custom_fusion_sort_or_dedup",
    "implementation_source_sha256",
    "configuration_sha256",
}

_READER_IDENTITY_KEYS = {
    "implementation",
    "input_representation",
    "official_flat_session_item_semantics",
    "upstream_repository",
    "upstream_commit",
    "upstream_source_path",
    "upstream_file_sha256",
    "prompt_template_sha256",
    "implementation_source_sha256",
    "model_identity_sha256",
    "transport_identity_sha256",
    "retriever_type",
    "topk_context",
    "history_format",
    "useronly",
    "cot",
    "con",
    "merge_key_expansion_into_value",
    "session_value_source",
    "has_answer_label_removed",
    "episode_content_hash_verified",
    "presentation_order",
    "truncation_policy",
    "qualification_truncation_count",
    "messages",
    "system_prompt",
    "temperature",
    "max_tokens",
    "thinking_control",
    "effective_enable_thinking",
    "thinking_parameter_sent",
    "max_attempts",
    "sdk_hidden_retries",
    "retry_delays_seconds",
}

_JUDGE_IDENTITY_KEYS = {
    "implementation",
    "rubric_sha256",
    "parser_sha256",
    "qualification_artifact_sha256",
    "qualification_status",
    "implementation_source_sha256",
    "model_identity_sha256",
    "transport_identity_sha256",
    "question_type",
    "abstention",
    "rubric",
    "headline_parser",
    "audit_parser",
    "messages",
    "system_prompt",
    "temperature",
    "max_tokens",
    "n",
    "thinking_control",
    "effective_enable_thinking",
    "thinking_parameter_sent",
    "max_attempts",
    "sdk_hidden_retries",
    "retry_delays_seconds",
    "invalid_output_policy",
    "protocol_alignment",
    "judge_backend_difference_disclosed",
}


class S2CompletionContractError(ValueError):
    """The future S2 completion contract is ambiguous or unsafe."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise S2CompletionContractError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and value == value.lower()
        and all(character in _HEX for character in value)
    )


def _require_sha_fields(value: Mapping[str, Any], fields: Sequence[str], *, label: str) -> None:
    for field in fields:
        if not _sha(value.get(field)):
            raise S2CompletionContractError(f"{label} identity invalid: {field}")


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], *, label: str
) -> None:
    if set(value) != expected:
        raise S2CompletionContractError(f"{label} fields are invalid")


def _reject_forbidden_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_FIELDS:
                raise S2CompletionContractError(
                    f"forbidden field in S2 completion contract: {key}"
                )
            _reject_forbidden_fields(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_forbidden_fields(child)


def _validate_retrieval_policy(value: object) -> dict[str, Any]:
    policy = _mapping(value, label="retrieval policy")
    _require_exact_fields(
        policy, _RETRIEVAL_POLICY_KEYS, label="retrieval policy"
    )
    required_strings = (
        "policy_name",
        "policy_version",
        "retrieval_surface",
        "retrieval_method",
        "search_recipe",
        "native_result_type",
        "evaluation_result_unit",
        "top_k_unit",
        "gold_unit",
        "metric_name",
        "aggregate_metric",
        "reader_input_representation",
        "episode_to_session_mapping",
        "search_filters",
        "group_scope",
        "retrieval_temporal_filter",
    )
    for field in required_strings:
        if not isinstance(policy.get(field), str) or not policy[field]:
            raise S2CompletionContractError(
                f"retrieval policy {field} must be explicit"
            )
    expected = {
        "policy_name": "graphiti-0.29.3-episode-bm25-session-v1",
        "policy_version": "v1",
        "retrieval_surface": "graphiti_episode_bm25",
        "retrieval_method": "Graphiti.search_",
        "search_recipe": "EPISODE_BM25_RRF",
        "native_result_type": "EpisodicNode",
        "evaluation_result_unit": "LongMemEvalSession",
        "top_k_unit": "unique_session",
        "gold_unit": "LongMemEvalSession",
        "top_k": 10,
        "candidate_limit": 20,
        "metric_name": "per_question_session_recall_all_at_10",
        "aggregate_metric": "mean_per_question_session_recall_all_at_10",
        "episode_to_session_mapping": "frozen_one_to_one_fail_closed",
        "reader_input_representation": "longmemeval_flat_session_item",
        "search_filters": "empty",
        "group_scope": "exactly_one_history_namespace",
        "question_date_used_for_retrieval": False,
        "retrieval_temporal_filter": "none",
    }
    for field, expected_value in expected.items():
        if policy.get(field) != expected_value:
            raise S2CompletionContractError(
                f"retrieval policy {field} must equal {expected_value!r}"
            )
    if policy.get("official_longmemeval_session_metric") is not True:
        raise S2CompletionContractError(
            "retrieval policy must declare the official session metric"
        )
    if type(policy.get("question_date_used_for_retrieval")) is not bool:
        raise S2CompletionContractError(
            "retrieval policy question-date behavior must be explicit"
        )
    disabled_features = (
        "edge_search_enabled",
        "node_search_enabled",
        "community_search_enabled",
        "query_embedding_used",
        "cross_encoder_used",
        "custom_fusion_sort_or_dedup",
    )
    if any(policy.get(field) is not False for field in disabled_features):
        raise S2CompletionContractError(
            "retrieval policy contains an unfrozen or enabled auxiliary path"
        )
    _require_sha_fields(
        policy,
        ("implementation_source_sha256", "configuration_sha256"),
        label="retrieval policy",
    )
    return policy


def _validate_selection_basis(value: object) -> dict[str, Any]:
    basis = _mapping(value, label="selection basis")
    expected_keys = {
        "kind",
        "frozen_before_numeric_execution",
        "r0_outcome_previously_observed",
        "selection_not_blinded",
        "r0_numeric_score_used_for_policy_choice",
        "candidate_score_search_performed",
        "reasons",
        "evidence_sha256",
    }
    if set(basis) != expected_keys:
        raise S2CompletionContractError(
            "selection basis may contain only outcome-independent evidence"
        )
    if basis.get("kind") != "architecture_and_benchmark_semantics_not_blinded":
        raise S2CompletionContractError(
            "selection basis must use architecture and benchmark semantics"
        )
    if basis.get("frozen_before_numeric_execution") is not True:
        raise S2CompletionContractError(
            "selection basis must be frozen before numeric execution"
        )
    if (
        basis.get("r0_outcome_previously_observed") is not True
        or basis.get("selection_not_blinded") is not True
        or basis.get("r0_numeric_score_used_for_policy_choice") is not False
        or basis.get("candidate_score_search_performed") is not False
    ):
        raise S2CompletionContractError(
            "selection basis must disclose R0 exposure without using scores"
        )
    if basis.get("reasons") != [
        "BENCHMARK_RESULT_UNIT_ALIGNMENT",
        "UPSTREAM_NATIVE_API",
        "NO_CUSTOM_CROSS_SURFACE_FUSION",
        "SAME_POLICY_FOR_ALL_METHODS",
    ]:
        raise S2CompletionContractError(
            "selection basis reasons must be outcome-independent and frozen"
        )
    if not _sha(basis.get("evidence_sha256")):
        raise S2CompletionContractError("selection basis evidence hash is invalid")
    return basis


def _validate_reader_identity(
    value: object, *, expected_representation: str
) -> dict[str, Any]:
    reader = _mapping(value, label="Reader identity")
    _require_exact_fields(reader, _READER_IDENTITY_KEYS, label="Reader identity")
    if reader.get("implementation") != "longmemeval_official_session_reader":
        raise S2CompletionContractError("Reader implementation is not frozen")
    if (
        reader.get("input_representation") != expected_representation
        or reader.get("official_flat_session_item_semantics") is not True
    ):
        raise S2CompletionContractError(
            "Reader representation does not match the retrieval result"
        )
    required_strings = (
        "upstream_repository",
        "upstream_commit",
        "upstream_source_path",
        "thinking_control",
    )
    if any(not isinstance(reader.get(field), str) or not reader[field] for field in required_strings):
        raise S2CompletionContractError("Reader source/request identity is incomplete")
    if (
        reader.get("upstream_repository") != "xiaowu0162/LongMemEval"
        or reader.get("upstream_commit")
        != "9e0b455f4ef0e2ab8f2e582289761153549043fc"
        or reader.get("upstream_source_path") != "src/generation/run_generation.py"
    ):
        raise S2CompletionContractError("Reader upstream identity drift")
    _require_sha_fields(
        reader,
        (
            "upstream_file_sha256",
            "prompt_template_sha256",
            "implementation_source_sha256",
            "model_identity_sha256",
            "transport_identity_sha256",
        ),
        label="Reader",
    )
    expected_session_contract = {
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
    }
    if any(reader.get(key) != expected for key, expected in expected_session_contract.items()):
        raise S2CompletionContractError("Reader flat-session materialization drift")
    if reader.get("messages") != ["user"] or reader.get("system_prompt") is not None:
        raise S2CompletionContractError("Reader message/system-prompt contract drift")
    if reader.get("temperature") != 0:
        raise S2CompletionContractError("Reader temperature must equal zero")
    if (
        isinstance(reader.get("max_tokens"), bool)
        or not isinstance(reader.get("max_tokens"), int)
        or reader["max_tokens"] < 1
    ):
        raise S2CompletionContractError("Reader max_tokens must be positive")
    if (
        reader.get("thinking_control") != "client_request"
        or type(reader.get("effective_enable_thinking")) is not bool
        or reader.get("thinking_parameter_sent") is not True
    ):
        raise S2CompletionContractError("Reader thinking policy is not explicit")
    if (
        reader.get("max_attempts") != 1
        or reader.get("sdk_hidden_retries") != 0
        or reader.get("retry_delays_seconds") != []
    ):
        raise S2CompletionContractError("Reader retry policy is not single-attempt")
    return reader


def _validate_judge_identity(value: object) -> dict[str, Any]:
    judge = _mapping(value, label="Judge identity")
    _require_exact_fields(judge, _JUDGE_IDENTITY_KEYS, label="Judge identity")
    if judge.get("implementation") != "qualified_legacy_longmemeval_adapter":
        raise S2CompletionContractError("Judge implementation is not frozen")
    _require_sha_fields(
        judge,
        (
            "rubric_sha256",
            "parser_sha256",
            "qualification_artifact_sha256",
            "implementation_source_sha256",
            "model_identity_sha256",
            "transport_identity_sha256",
        ),
        label="Judge",
    )
    if judge.get("qualification_status") != "PASS":
        raise S2CompletionContractError("Judge qualification must be PASS")
    expected_judge_contract = {
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
        "protocol_alignment": "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
        "judge_backend_difference_disclosed": True,
    }
    if any(judge.get(key) != expected for key, expected in expected_judge_contract.items()):
        raise S2CompletionContractError("Judge rubric/request identity drift")
    if (
        judge.get("thinking_control") != "client_request"
        or type(judge.get("effective_enable_thinking")) is not bool
        or judge.get("thinking_parameter_sent") is not True
    ):
        raise S2CompletionContractError("Judge thinking policy is not explicit")
    if (
        judge.get("max_attempts") != 1
        or judge.get("sdk_hidden_retries") != 0
        or judge.get("retry_delays_seconds") != [0.0]
    ):
        raise S2CompletionContractError("Judge retry policy is not single-attempt")
    if judge.get("invalid_output_policy") != "SEAL_FAILURE_AND_STOP":
        raise S2CompletionContractError("Judge invalid-output policy is not frozen")
    return judge


def _validated_id_list(value: object, *, label: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise S2CompletionContractError(f"{label} must be a list")
    result = list(value)
    if any(not isinstance(item, str) or not item for item in result):
        raise S2CompletionContractError(f"{label} contains an invalid ID")
    if len(result) != len(set(result)):
        raise S2CompletionContractError(f"{label} contains duplicate IDs")
    return result


def _validate_role_binding(value: object) -> dict[str, Any]:
    binding = _mapping(value, label="role binding")
    _require_exact_fields(
        binding,
        {
            "role_artifact_sha256",
            "role_payload_sha256",
            "evaluation_role",
            "selected_history_ids",
            "registry",
        },
        label="role binding",
    )
    _require_sha_fields(
        binding,
        ("role_artifact_sha256", "role_payload_sha256"),
        label="role binding",
    )
    if binding.get("evaluation_role") != "DEVELOPMENT_EXPOSED":
        raise S2CompletionContractError("S2 completion must use DEVELOPMENT_EXPOSED")
    registry = _mapping(binding.get("registry"), label="role registry")
    role_names = ("DEVELOPMENT_EXPOSED", "PILOT", "FINAL_PAPER_TEST")
    if set(registry) != set(role_names):
        raise S2CompletionContractError("role registry is incomplete")
    role_ids = {
        role: _validated_id_list(registry[role], label=f"role {role}")
        for role in role_names
    }
    for index, left in enumerate(role_names):
        for right in role_names[index + 1 :]:
            if set(role_ids[left]) & set(role_ids[right]):
                raise S2CompletionContractError("benchmark data-role overlap detected")
    selected = _validated_id_list(
        binding.get("selected_history_ids"), label="selected history IDs"
    )
    if not selected or not set(selected).issubset(role_ids["DEVELOPMENT_EXPOSED"]):
        raise S2CompletionContractError(
            "selected history IDs are not DEVELOPMENT_EXPOSED"
        )
    binding["registry"] = role_ids
    binding["selected_history_ids"] = selected
    return binding


def _validate_metric_surfaces(value: object) -> dict[str, Any]:
    surfaces = _mapping(value, label="metric surfaces")
    expected_names = {
        "retrieval": ("evidence_recall_at_10", "question", "ranked_retrieval"),
        "qa": ("qa_accuracy", "question", "qualified_judge"),
        "graph_correctness": (
            "graph_sensitive_construction_correctness",
            "history",
            "separate_graph_oracle",
        ),
    }
    if set(surfaces) != set(expected_names):
        raise S2CompletionContractError(
            "metric surfaces must separate retrieval, QA, and graph correctness"
        )
    for surface, (name, unit, source) in expected_names.items():
        identity = _mapping(surfaces[surface], label=f"{surface} metric")
        expected_fields = {
            "name",
            "unit",
            "value_source",
            "substitutable_by_other_metric",
        }
        if surface == "retrieval":
            expected_fields.update({"definition", "aggregation", "top_k"})
        _require_exact_fields(
            identity, expected_fields, label=f"{surface} metric"
        )
        if identity.get("name") != name:
            label = "QA" if surface == "qa" else surface
            raise S2CompletionContractError(f"{label} metric identity mismatch")
        if identity.get("unit") != unit or identity.get("value_source") != source:
            raise S2CompletionContractError(f"{surface} metric semantics mismatch")
        if identity.get("substitutable_by_other_metric") is not False:
            raise S2CompletionContractError(
                f"{surface} metric must be non-substitutable"
            )
        if surface == "retrieval" and identity.get("top_k") != 10:
            raise S2CompletionContractError("retrieval metric top_k mismatch")
        if surface == "retrieval" and (
            identity.get("definition") != "per_question_session_recall_all_at_10"
            or identity.get("aggregation") != "mean"
        ):
            raise S2CompletionContractError("retrieval metric definition mismatch")
        if surface != "retrieval" and "top_k" in identity:
            raise S2CompletionContractError(f"{surface} metric has an invalid top_k")
        surfaces[surface] = identity
    return surfaces


def _validate_failure_policy(value: object) -> dict[str, Any]:
    policy = _mapping(value, label="failure policy")
    expected = {
        "max_live_attempts": 1,
        "automatic_retry": False,
        "on_transport_failure": "SEAL_FAILURE_AND_STOP",
        "on_invalid_reader_output": "SEAL_FAILURE_AND_STOP",
        "on_invalid_judge_output": "SEAL_FAILURE_AND_STOP",
        "partial_results_mergeable": False,
    }
    if policy != expected:
        raise S2CompletionContractError("failure policy is not the frozen contract")
    return policy


def _validate_source_hashes(value: object) -> dict[str, str]:
    sources = _mapping(value, label="source hashes")
    required = {"completion_contract", "retrieval", "reader", "judge", "roles"}
    if not required.issubset(sources) or any(
        not isinstance(key, str) or not key or not _sha(item)
        for key, item in sources.items()
    ):
        raise S2CompletionContractError("source hashes are incomplete or invalid")
    return dict(sorted(sources.items()))


def _validated_body(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != _TOP_LEVEL_KEYS:
        raise S2CompletionContractError("S2 completion contract fields are invalid")
    if value.get("schema_version") != S2_COMPLETION_SCHEMA:
        raise S2CompletionContractError("S2 completion contract schema mismatch")
    _reject_forbidden_fields(value)
    retrieval = _validate_retrieval_policy(value.get("retrieval_policy"))
    return {
        "schema_version": S2_COMPLETION_SCHEMA,
        "retrieval_policy": retrieval,
        "selection_basis": _validate_selection_basis(value.get("selection_basis")),
        "reader_identity": _validate_reader_identity(
            value.get("reader_identity"),
            expected_representation=retrieval["reader_input_representation"],
        ),
        "judge_identity": _validate_judge_identity(value.get("judge_identity")),
        "role_binding": _validate_role_binding(value.get("role_binding")),
        "metric_surfaces": _validate_metric_surfaces(value.get("metric_surfaces")),
        "failure_policy": _validate_failure_policy(value.get("failure_policy")),
        "source_hashes": _validate_source_hashes(value.get("source_hashes")),
    }


def build_s2_completion_contract(
    *,
    retrieval_policy: Mapping[str, Any],
    selection_basis: Mapping[str, Any],
    reader_identity: Mapping[str, Any],
    judge_identity: Mapping[str, Any],
    role_binding: Mapping[str, Any],
    metric_surfaces: Mapping[str, Any],
    failure_policy: Mapping[str, Any],
    source_hashes: Mapping[str, str],
) -> dict[str, Any]:
    """Build a canonical, hash-bound contract after fail-closed validation."""

    body = _validated_body(
        {
            "schema_version": S2_COMPLETION_SCHEMA,
            "retrieval_policy": retrieval_policy,
            "selection_basis": selection_basis,
            "reader_identity": reader_identity,
            "judge_identity": judge_identity,
            "role_binding": role_binding,
            "metric_surfaces": metric_surfaces,
            "failure_policy": failure_policy,
            "source_hashes": source_hashes,
        }
    )
    return {**body, "contract_sha256": payload_sha256(body)}


def validate_s2_completion_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a materialized contract and its self-hash without live I/O."""

    artifact = _mapping(value, label="S2 completion contract")
    stored_hash = artifact.pop("contract_sha256", None)
    body = _validated_body(artifact)
    if not _sha(stored_hash) or stored_hash != payload_sha256(body):
        raise S2CompletionContractError("S2 completion contract hash mismatch")
    return {**body, "contract_sha256": stored_hash}
