"""Pure, outcome-independent contract for the Native Reader-v2 canary."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence, Set
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256
from .s2_completion_chain import BoundedCompletionResult


READER_V2_CONTRACT_SCHEMA = (
    "membind.paper-eval-v3.native-reader-v2-contract.v1"
)
CANARY_HISTORY_ID = "b6019101"
CANARY_NAMESPACE = "nc-e1e2-1deef863d4241064"
EXCLUDED_DIRECT_HISTORY_ID = "07741c45"
HISTORICAL_DIRECT_RESULT_SHA256 = (
    "d9fc42a6479e3071fce56b8670a583aaa9ad76ce24687f4b6de957173064733d"
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = ("U0", "A0", "P*", "M*")
_SOURCE_NAMES = {
    "workplan",
    "parent_workplan",
    "reader_source",
    "reader_test",
    "qualification_source",
    "qualification_test",
    "historical_result",
}
_UNSAFE_KEYS = {
    "api_key",
    "password",
    "secret",
    "authorization_header",
    "raw_prompt",
    "raw_output",
    "raw_question",
    "raw_answer",
    "content",
    "has_answer",
}


class ReaderV2QualificationError(ValueError):
    """The Reader-v2 contract or compatibility evidence is invalid."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReaderV2QualificationError(f"{field} is not a SHA256")
    return value


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReaderV2QualificationError(f"{field} must be a mapping")
    return deepcopy(dict(value))


def _reject_unsafe(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _UNSAFE_KEYS:
                raise ReaderV2QualificationError("Reader-v2 contract is unsafe")
            _reject_unsafe(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_unsafe(child)


def select_reader_v2_canary(
    *,
    frozen_calibration_ids: Sequence[str],
    development_exposed_ids: Set[str],
    excluded_observed_history_id: str,
) -> str:
    """Select the first remaining exposed calibration ID without outcomes."""

    if (
        isinstance(frozen_calibration_ids, (str, bytes))
        or not isinstance(frozen_calibration_ids, Sequence)
        or not frozen_calibration_ids
        or len(set(frozen_calibration_ids)) != len(frozen_calibration_ids)
    ):
        raise ReaderV2QualificationError("frozen calibration IDs are invalid")
    if (
        not isinstance(development_exposed_ids, Set)
        or not development_exposed_ids
        or excluded_observed_history_id != EXCLUDED_DIRECT_HISTORY_ID
        or frozen_calibration_ids[0] != excluded_observed_history_id
    ):
        raise ReaderV2QualificationError("canary exclusion contract is invalid")
    remaining = [
        item
        for item in frozen_calibration_ids
        if item != excluded_observed_history_id
        and item in development_exposed_ids
    ]
    if not remaining:
        raise ReaderV2QualificationError("no exposed canary remains")
    if remaining[0] != CANARY_HISTORY_ID:
        raise ReaderV2QualificationError("canary selection drift")
    return remaining[0]


def _validate_reader(value: object) -> dict[str, Any]:
    reader = _mapping(value, field="reader")
    expected = {
        "implementation": "longmemeval_official_con_session_reader_v2",
        "upstream_repository": "xiaowu0162/LongMemEval",
        "upstream_commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
        "upstream_source_path": "src/generation/run_generation.py",
        "upstream_source_sha256": (
            "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672"
        ),
        "upstream_runner_path": "src/generation/run_generation.sh",
        "upstream_runner_sha256": (
            "6602147b866eca4a80acdf5e6689389586086216c9198fce7b8380b7495c5422"
        ),
        "upstream_readme_sha256": (
            "c4ff45676683d9e2f7cf7d9099d26426f14635ec110dbb1da818d1019a142573"
        ),
        "input_representation": "longmemeval_flat_session_item",
        "retriever_type": "flat-session",
        "topk_context": 10,
        "history_format": "json",
        "useronly": False,
        "reading_method": "con",
        "cot": True,
        "con": False,
        "separate_note_extraction": False,
        "reader_requests_per_question": 1,
        "max_tokens": 800,
        "merge_key_expansion_into_value": "none",
        "session_value_source": "frozen_dataset_haystack_sessions",
        "has_answer_label_removed": True,
        "presentation_order": "chronological_after_top_k_rank_stable_ties",
        "messages": ["user"],
        "system_prompt": None,
        "temperature": 0,
        "n": 1,
        "thinking_control": "client_request",
        "effective_enable_thinking": False,
        "thinking_parameter_sent": True,
        "truncation_policy": "FAIL_CLOSED_IF_CONTEXT_EXCEEDED",
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
        "retry_delays_seconds": [],
        "model": "qwen3-32b-fp8",
    }
    for key, expected_value in expected.items():
        if reader.get(key) != expected_value:
            raise ReaderV2QualificationError(f"Reader-v2 semantic drift: {key}")
    if not isinstance(reader.get("prompt_template_sha256"), str):
        raise ReaderV2QualificationError("Reader-v2 prompt identity is missing")
    _sha(reader["prompt_template_sha256"], field="prompt template")
    return reader


def _validate_transport(value: object) -> dict[str, Any]:
    transport = _mapping(value, field="reader transport")
    required = {
        "implementation": "openai_compatible_chat_completions",
        "served_model_name": "qwen3-32b-fp8",
        "max_attempts": 1,
        "sdk_hidden_retries": 0,
    }
    if any(transport.get(key) != expected for key, expected in required.items()):
        raise ReaderV2QualificationError("Reader-v2 transport identity drift")
    _sha(transport.get("endpoint_identity_sha256"), field="endpoint identity")
    return transport


def _validate_contract_body(value: Mapping[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "reader",
        "reader_config_sha256",
        "reader_transport",
        "reader_transport_config_sha256",
        "method_reader_bindings",
        "retrieval_policy_file_sha256",
        "judge_identity_sha256",
        "historical_direct_result_sha256",
        "canary_history_id",
        "canary_namespace_sha256",
        "canary_selection",
        "disclosure",
        "source_sha256",
    }
    if set(value) != expected_fields:
        raise ReaderV2QualificationError("Reader-v2 contract shape is invalid")
    body = deepcopy(dict(value))
    _reject_unsafe(body)
    if body.get("schema_version") != READER_V2_CONTRACT_SCHEMA:
        raise ReaderV2QualificationError("Reader-v2 contract schema drift")
    reader = _validate_reader(body.get("reader"))
    reader_hash = _sha(body.get("reader_config_sha256"), field="reader config")
    if reader_hash != payload_sha256(reader):
        raise ReaderV2QualificationError("Reader-v2 config hash mismatch")
    body["reader"] = reader
    body["reader_transport"] = _validate_transport(
        body.get("reader_transport")
    )
    for field in (
        "reader_transport_config_sha256",
        "retrieval_policy_file_sha256",
        "judge_identity_sha256",
        "canary_namespace_sha256",
    ):
        _sha(body.get(field), field=field)
    if (
        body.get("historical_direct_result_sha256")
        != HISTORICAL_DIRECT_RESULT_SHA256
    ):
        raise ReaderV2QualificationError("historical direct result drift")

    bindings = _mapping(body.get("method_reader_bindings"), field="bindings")
    if set(bindings) != set(_METHODS) or any(
        bindings.get(method) != reader_hash for method in _METHODS
    ):
        raise ReaderV2QualificationError("common Reader-v2 binding drift")
    body["method_reader_bindings"] = bindings

    selection = _mapping(body.get("canary_selection"), field="canary selection")
    expected_selection = {
        "data_role": "DEVELOPMENT_EXPOSED",
        "selection_rule": "first_remaining_frozen_calibration_id",
        "excluded_observed_history_id": EXCLUDED_DIRECT_HISTORY_ID,
        "selected_before_reader_v2_outcome": True,
        "canary_construction_revision_matches_current_u0": False,
        "canary_use": "ADAPTER_COMPATIBILITY_ONLY",
    }
    if selection != expected_selection or body.get("canary_history_id") != CANARY_HISTORY_ID:
        raise ReaderV2QualificationError("canary selection semantic drift")
    body["canary_selection"] = selection

    disclosure = _mapping(body.get("disclosure"), field="disclosure")
    expected_disclosure = {
        "prior_direct_failure_observed": True,
        "reader_v2_selection_not_blinded": True,
        "change_motivated_by_observed_failure": True,
        "recipe_source": "upstream_recommended",
        "direct_path_was_officially_supported": True,
        "retrieval_or_top_k_candidate_search": False,
    }
    if disclosure != expected_disclosure:
        raise ReaderV2QualificationError("Reader-v2 disclosure drift")
    body["disclosure"] = disclosure

    sources = _mapping(body.get("source_sha256"), field="source hashes")
    if set(sources) != _SOURCE_NAMES:
        raise ReaderV2QualificationError("Reader-v2 source set is incomplete")
    for name, source_hash in sources.items():
        _sha(source_hash, field=f"source {name}")
    body["source_sha256"] = dict(sorted(sources.items()))
    return body


def build_reader_v2_contract(
    *,
    reader_public_config: Mapping[str, Any],
    reader_config_sha256: str,
    reader_transport_public_config: Mapping[str, Any],
    reader_transport_config_sha256: str,
    method_reader_bindings: Mapping[str, str],
    retrieval_policy_file_sha256: str,
    judge_identity_sha256: str,
    historical_direct_result_sha256: str,
    canary_history_id: str,
    canary_namespace: str,
    canary_selection: Mapping[str, Any],
    disclosure: Mapping[str, Any],
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Build a self-hashed contract without raw prompts, data, or endpoint."""

    if not isinstance(canary_namespace, str) or not canary_namespace:
        raise ReaderV2QualificationError("canary namespace is invalid")
    body = _validate_contract_body(
        {
            "schema_version": READER_V2_CONTRACT_SCHEMA,
            "reader": dict(reader_public_config),
            "reader_config_sha256": reader_config_sha256,
            "reader_transport": dict(reader_transport_public_config),
            "reader_transport_config_sha256": reader_transport_config_sha256,
            "method_reader_bindings": dict(method_reader_bindings),
            "retrieval_policy_file_sha256": retrieval_policy_file_sha256,
            "judge_identity_sha256": judge_identity_sha256,
            "historical_direct_result_sha256": historical_direct_result_sha256,
            "canary_history_id": canary_history_id,
            "canary_namespace_sha256": hashlib.sha256(
                canary_namespace.encode("utf-8")
            ).hexdigest(),
            "canary_selection": dict(canary_selection),
            "disclosure": dict(disclosure),
            "source_sha256": dict(source_sha256),
        }
    )
    return {**body, "contract_sha256": payload_sha256(body)}


def verify_reader_v2_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = _mapping(value, field="Reader-v2 contract")
    stored = artifact.pop("contract_sha256", None)
    body = _validate_contract_body(artifact)
    if _sha(stored, field="contract hash") != payload_sha256(body):
        raise ReaderV2QualificationError("Reader-v2 contract hash mismatch")
    return {**body, "contract_sha256": stored}


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReaderV2QualificationError(f"invalid {field}")
    return value


def _classify_counters(value: object) -> dict[str, int]:
    counters = _mapping(value, field="canary counters")
    expected_fields = {
        "graphiti_search_calls",
        "neo4j_read_requests",
        "reader_requests",
        "judge_requests",
        "construction_llm_requests",
        "embedding_requests",
        "cross_encoder_requests",
        "database_mutation_attempts",
        "database_mutations",
        "cleanup_calls",
        "retry_count",
    }
    if set(counters) != expected_fields:
        raise ReaderV2QualificationError("canary counter shape is invalid")
    parsed = {
        name: _nonnegative_int(raw, field=name) for name, raw in counters.items()
    }
    if (
        parsed["graphiti_search_calls"] != 1
        or parsed["neo4j_read_requests"] < 1
        or parsed["reader_requests"] != 1
        or parsed["judge_requests"] != 1
        or any(
            parsed[name] != 0
            for name in (
                "construction_llm_requests",
                "embedding_requests",
                "cross_encoder_requests",
                "database_mutation_attempts",
                "database_mutations",
                "cleanup_calls",
                "retry_count",
            )
        )
    ):
        raise ReaderV2QualificationError("canary live budget was violated")
    return parsed


def _validate_reader_evidence(value: object) -> dict[str, Any]:
    evidence = _mapping(value, field="Reader-v2 evidence")
    if evidence.get("status") != "SUCCESS" or evidence.get("truncation_count") != 0:
        raise ReaderV2QualificationError("Reader-v2 response was incomplete")
    for field in ("config_sha256", "prompt_sha256", "output_sha256"):
        _sha(evidence.get(field), field=f"Reader-v2 {field}")
    for field in (
        "prompt_character_count",
        "prompt_byte_count",
        "output_character_count",
        "output_byte_count",
        "prompt_tokens",
        "completion_tokens",
    ):
        _nonnegative_int(evidence.get(field), field=f"Reader-v2 {field}")
    _reject_unsafe(evidence)
    return evidence


def _validate_judge_evidence(value: object, *, qa_accuracy: float) -> dict[str, Any]:
    evidence = _mapping(value, field="Judge evidence")
    expected_label = qa_accuracy == 1.0
    expected_parse = "YES" if expected_label else "NO"
    if (
        evidence.get("status") != "SUCCESS"
        or evidence.get("label") is not expected_label
        or evidence.get("parse_status") != expected_parse
        or evidence.get("retry_count") != 0
        or evidence.get("error_class") is not None
    ):
        raise ReaderV2QualificationError("Judge evidence is invalid")
    for field in ("config_sha256", "prompt_sha256", "output_sha256"):
        _sha(evidence.get(field), field=f"Judge {field}")
    _reject_unsafe(evidence)
    return evidence


def classify_reader_v2_canary(
    result: BoundedCompletionResult,
) -> dict[str, Any]:
    """Classify transport/contract compatibility without using QA as a gate."""

    if not isinstance(result, BoundedCompletionResult):
        raise ReaderV2QualificationError("canary result type is invalid")
    if result.history_id != CANARY_HISTORY_ID or result.namespace != CANARY_NAMESPACE:
        raise ReaderV2QualificationError("canary result identity drift")
    if result.metrics.retrieved_session_count != 10:
        raise ReaderV2QualificationError("canary did not materialize top 10")
    if result.qa_accuracy not in {0.0, 1.0}:
        raise ReaderV2QualificationError("canary QA diagnostic is invalid")
    counters = _classify_counters(result.counters)
    reader = _validate_reader_evidence(result.reader_evidence)
    judge = _validate_judge_evidence(
        result.judge_evidence,
        qa_accuracy=result.qa_accuracy,
    )
    return {
        "compatibility_status": "PASS",
        "canary_history_id": CANARY_HISTORY_ID,
        "canary_namespace_sha256": hashlib.sha256(
            CANARY_NAMESPACE.encode("utf-8")
        ).hexdigest(),
        "quality_gate_used": False,
        "qa_accuracy_diagnostic": result.qa_accuracy,
        "evidence_recall_at_10_diagnostic": result.metrics.evidence_recall_at_10,
        "retrieved_session_count": result.metrics.retrieved_session_count,
        "reader_config_sha256": reader["config_sha256"],
        "reader_prompt_sha256": reader["prompt_sha256"],
        "reader_output_sha256": reader["output_sha256"],
        "judge_config_sha256": judge["config_sha256"],
        "judge_output_sha256": judge["output_sha256"],
        "counters": counters,
        "qualification_mergeable": True,
        "native_quality_mergeable": False,
        "pilot_or_final_mergeable": False,
        "s3_authorized": False,
    }
