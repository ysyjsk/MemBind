"""Safe identity projection for the formal S2 session Reader/Judge chain."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256


IDENTITY_SCHEMA = "membind.paper-eval-v3.s2-completion-adapter-identity.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_SOURCES = {"retrieval", "reader", "judge", "chain"}
_FORBIDDEN_KEYS = {
    "api_key",
    "base_url",
    "password",
    "secret",
    "authorization",
    "question",
    "answer",
    "prompt",
    "raw_prompt",
    "raw_response",
    "raw_output",
    "content",
}


class CompletionIdentityError(ValueError):
    """The projected S2 completion identity is incomplete or unsafe."""


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CompletionIdentityError(f"{field} is not a SHA256")
    return value


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompletionIdentityError(f"{label} is not a public mapping")
    return deepcopy(dict(value))


def _public_config(component: object, *, label: str) -> dict[str, Any]:
    value = getattr(component, "public_config", None)
    if callable(value):
        value = value()
    return _mapping(value, label=label)


def _component_hash(component: object, *, label: str) -> str:
    return _sha(getattr(component, "config_sha256", None), field=f"{label} config")


def _walk_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.append(str(key).lower())
            keys.extend(_walk_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


def _reject_unsafe(value: Mapping[str, Any]) -> None:
    if set(_walk_keys(value)) & _FORBIDDEN_KEYS:
        raise CompletionIdentityError("completion identity contains secret or raw data")


def _validate_body(body: Mapping[str, Any]) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "retrieval_policy_contract_sha256",
        "reader_transport",
        "reader_transport_config_sha256",
        "reader",
        "reader_config_sha256",
        "judge",
        "judge_config_sha256",
        "judge_qualification_artifact_sha256",
        "source_sha256",
    }
    if set(body) != expected_keys or body.get("schema_version") != IDENTITY_SCHEMA:
        raise CompletionIdentityError("completion identity shape is invalid")
    value = deepcopy(dict(body))
    _reject_unsafe(value)
    for field in (
        "retrieval_policy_contract_sha256",
        "reader_transport_config_sha256",
        "reader_config_sha256",
        "judge_config_sha256",
        "judge_qualification_artifact_sha256",
    ):
        _sha(value.get(field), field=field)
    sources = _mapping(value.get("source_sha256"), label="source identity")
    if set(sources) != _REQUIRED_SOURCES or any(
        _SHA256.fullmatch(item) is None
        for item in sources.values()
        if isinstance(item, str)
    ) or any(not isinstance(item, str) for item in sources.values()):
        raise CompletionIdentityError("source identity is incomplete")

    transport = _mapping(value.get("reader_transport"), label="reader transport")
    if (
        transport.get("implementation") != "openai_compatible_chat_completions"
        or transport.get("served_model_name") != "qwen3-32b-fp8"
        or transport.get("max_attempts") != 1
        or transport.get("sdk_hidden_retries") != 0
    ):
        raise CompletionIdentityError("reader transport identity is invalid")
    _sha(transport.get("endpoint_identity_sha256"), field="reader endpoint identity")

    reader = _mapping(value.get("reader"), label="reader identity")
    if (
        reader.get("implementation") != "longmemeval_official_session_reader"
        or reader.get("input_representation")
        != "longmemeval_flat_session_item"
        or reader.get("official_flat_session_item_semantics") is not True
        or reader.get("thinking_control") != "client_request"
        or reader.get("effective_enable_thinking") is not False
        or reader.get("thinking_parameter_sent") is not True
        or reader.get("messages") != ["user"]
        or reader.get("max_attempts") != 1
        or reader.get("sdk_hidden_retries") != 0
    ):
        raise CompletionIdentityError("reader identity is invalid")

    judge = _mapping(value.get("judge"), label="judge identity")
    backend = judge.get("backend_public_config")
    if (
        judge.get("implementation") != "qualified_legacy_longmemeval_adapter"
        or not isinstance(backend, Mapping)
        or backend.get("thinking_control") != "client_request"
        or backend.get("effective_enable_thinking") is not False
        or backend.get("max_attempts") != 1
        or backend.get("sdk_hidden_retries") != 0
    ):
        raise CompletionIdentityError("judge identity is invalid")
    value["source_sha256"] = dict(sorted(sources.items()))
    value["reader_transport"] = transport
    value["reader"] = reader
    value["judge"] = judge
    return value


def build_s2_completion_adapter_identity(
    *,
    retrieval_policy_contract_sha256: str,
    reader_transport: object,
    reader: object,
    judge: object,
    judge_qualification_artifact_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Build a public, self-hashed identity without persisting endpoints."""

    body = _validate_body(
        {
            "schema_version": IDENTITY_SCHEMA,
            "retrieval_policy_contract_sha256": retrieval_policy_contract_sha256,
            "reader_transport": _public_config(
                reader_transport, label="reader transport"
            ),
            "reader_transport_config_sha256": _component_hash(
                reader_transport, label="reader transport"
            ),
            "reader": _public_config(reader, label="reader"),
            "reader_config_sha256": _component_hash(reader, label="reader"),
            "judge": _public_config(judge, label="judge"),
            "judge_config_sha256": _component_hash(judge, label="judge"),
            "judge_qualification_artifact_sha256": (
                judge_qualification_artifact_sha256
            ),
            "source_sha256": source_sha256,
        }
    )
    return {**body, "identity_sha256": payload_sha256(body)}


def validate_s2_completion_adapter_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = _mapping(value, label="completion identity")
    stored = artifact.pop("identity_sha256", None)
    body = _validate_body(artifact)
    if not _sha(stored, field="identity hash") or stored != payload_sha256(body):
        raise CompletionIdentityError("completion identity hash mismatch")
    return {**body, "identity_sha256": stored}
