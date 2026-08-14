from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_completion_identity import (
    CompletionIdentityError,
    build_s2_completion_adapter_identity,
    validate_s2_completion_adapter_identity,
)


def _component(public: dict[str, object], config_hash: str) -> object:
    return SimpleNamespace(public_config=public, config_sha256=config_hash)


def _identity() -> dict[str, object]:
    transport = _component(
        {
            "implementation": "openai_compatible_chat_completions",
            "served_model_name": "qwen3-32b-fp8",
            "endpoint_identity_sha256": "1" * 64,
            "timeout_seconds": 180.0,
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
        },
        "2" * 64,
    )
    reader = _component(
        {
            "implementation": "longmemeval_official_session_reader",
            "input_representation": "longmemeval_flat_session_item",
            "official_flat_session_item_semantics": True,
            "thinking_control": "client_request",
            "effective_enable_thinking": False,
            "thinking_parameter_sent": True,
            "messages": ["user"],
            "max_attempts": 1,
            "sdk_hidden_retries": 0,
        },
        "3" * 64,
    )
    judge = _component(
        {
            "implementation": "qualified_legacy_longmemeval_adapter",
            "judge_model": "qwen3-32b-fp8",
            "judge_config_sha256": "4" * 64,
            "backend_public_config": {
                "thinking_control": "client_request",
                "effective_enable_thinking": False,
                "max_attempts": 1,
                "sdk_hidden_retries": 0,
            },
        },
        "5" * 64,
    )
    return build_s2_completion_adapter_identity(
        retrieval_policy_contract_sha256="6" * 64,
        reader_transport=transport,
        reader=reader,
        judge=judge,
        judge_qualification_artifact_sha256="7" * 64,
        source_sha256={
            "retrieval": "8" * 64,
            "reader": "9" * 64,
            "judge": "a" * 64,
            "chain": "b" * 64,
        },
    )


def test_builds_hash_bound_session_adapter_identity_without_secrets() -> None:
    identity = _identity()

    assert identity["schema_version"] == (
        "membind.paper-eval-v3.s2-completion-adapter-identity.v1"
    )
    assert identity["retrieval_policy_contract_sha256"] == "6" * 64
    assert identity["reader"]["official_flat_session_item_semantics"] is True
    assert identity["reader"]["input_representation"] == (
        "longmemeval_flat_session_item"
    )
    assert identity["identity_sha256"] == payload_sha256(
        {key: value for key, value in identity.items() if key != "identity_sha256"}
    )
    assert validate_s2_completion_adapter_identity(identity) == identity
    encoded = json.dumps(identity, sort_keys=True).lower()
    for forbidden in ("api_key", "base_url", "password", "raw_prompt"):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("component", "field", "value"),
    [
        ("reader", "input_representation", "EntityEdge.fact"),
        ("reader", "official_flat_session_item_semantics", False),
        ("reader", "effective_enable_thinking", True),
        ("judge", "implementation", "unqualified_judge"),
    ],
)
def test_rejects_edge_reader_or_unqualified_component(
    component: str, field: str, value: object
) -> None:
    identity = _identity()
    identity[component][field] = value
    body = {key: item for key, item in identity.items() if key != "identity_sha256"}
    identity["identity_sha256"] = payload_sha256(body)

    with pytest.raises(CompletionIdentityError, match="reader|judge"):
        validate_s2_completion_adapter_identity(identity)


def test_rejects_component_or_source_hash_drift() -> None:
    identity = _identity()
    identity["source_sha256"]["reader"] = "f" * 64

    with pytest.raises(CompletionIdentityError, match="identity hash"):
        validate_s2_completion_adapter_identity(identity)


def test_rejects_secret_or_raw_content_even_if_resealed() -> None:
    identity = _identity()
    identity["reader_transport"]["api_key"] = "secret"
    body = {key: item for key, item in identity.items() if key != "identity_sha256"}
    identity["identity_sha256"] = payload_sha256(body)

    with pytest.raises(CompletionIdentityError, match="secret|raw"):
        validate_s2_completion_adapter_identity(identity)
