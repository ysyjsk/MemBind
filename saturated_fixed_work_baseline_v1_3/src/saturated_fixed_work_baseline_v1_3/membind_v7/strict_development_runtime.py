"""Strict-schema Bailian + SiliconFlow runtime for V7 development only."""

from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .development_campaign import verify_development_source_bindings
from .engineering_observer_runtime import (
    EMBEDDING_AUTHORITY,
    EMBEDDING_BASE_URL,
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    LLM_ADMISSION_LIMIT,
    MAX_COROUTINES,
    NEO4J_URI,
    CompositeEngineeringRuntime,
    CompositeRuntimeComponents,
    ExactDimensionEmbedder,
    ResponseModelValidatingLLMClient,
    _production_components,
)


class StrictDevelopmentRuntimeError(RuntimeError):
    """The frozen strict development runtime contract failed closed."""


STRICT_CONSTRUCTION_AUTHORITY = (
    "alibaba-bailian-openai-compatible-strict-schema-selected-v1"
)
STRICT_CONSTRUCTION_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
STRICT_CONSTRUCTION_MODEL = "qwen3-max-2026-01-23"
STRICT_PROVIDER_IDENTITY_KIND = "COMPOSITE_DEVELOPMENT_STRICT_SCHEMA_TEMPORARY"
_CANDIDATE_ARTIFACT_SHA256 = (
    "3e6c163908b6b0abddaf9217d50b6bf55624823878de3a91bfb541ef788eac93"
)
_CANDIDATE_PROTOCOL_SHA256 = (
    "79c19f223fbaccbe054c4b7a1821b5a90513a1a5492deba3c9460732403b270c"
)
_SCIENTIFIC_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)


def _fail(message: str) -> StrictDevelopmentRuntimeError:
    return StrictDevelopmentRuntimeError(message)


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


def _object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"{label} is unreadable") from error
    if not isinstance(value, dict):
        raise _fail(f"{label} is invalid")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label} is missing")
    return value


def _require(value: Any, expected: Any, *, label: str) -> None:
    if value != expected:
        raise _fail(f"strict development runtime freeze drifted: {label}")


def load_strict_development_runtime_freeze(
    path: str | Path, *, verify_references: bool = True
) -> dict[str, Any]:
    """Load the strict-schema runtime and verify its candidate/source bindings."""

    selected = Path(path).resolve()
    value = _object(selected, label="strict development runtime freeze")
    for field, expected in {
        "schema_version": "membind.v7.strict-development-runtime-freeze.v1",
        "status": "FROZEN_AFTER_STRICT_SCHEMA_CANDIDATE_GATE_BEFORE_R1_R3",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": STRICT_PROVIDER_IDENTITY_KIND,
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "scientific_method_selection_update_allowed": False,
        "provider_swap_requires_new_formal_campaign": True,
        "old_read_return_allowed": False,
        "native_demand_skip_allowed": False,
        "repair_apply_allowed": False,
        "response_replay_allowed": False,
        "raw_request_persistence_allowed": False,
        "raw_response_persistence_allowed": False,
        "raw_embedding_persistence_allowed": False,
        "credential_persistence_allowed": False,
    }.items():
        _require(value.get(field), expected, label=field)

    construction = _mapping(value.get("construction"), label="construction identity")
    for field, expected in {
        "authority": STRICT_CONSTRUCTION_AUTHORITY,
        "base_url": STRICT_CONSTRUCTION_BASE_URL,
        "model": STRICT_CONSTRUCTION_MODEL,
        "api_key_env": "DASHSCOPE_API_KEY",
        "temperature": 0.0,
        "top_p": 1.0,
        "structured_output_mode": "json_schema",
        "strict_json_schema": True,
        "prompt_schema_injection": False,
        "response_validation": "pydantic-v2",
        "max_tokens_sent_for_structured_output": False,
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
        "selection_rule": "FIRST_ALL_LANES_FULL_PASS_IN_FROZEN_ORDER",
        "candidate_protocol_path": "BAILIAN_V7_STRICT_SCHEMA_CANDIDATE_PROTOCOL.json",
        "candidate_protocol_sha256": _CANDIDATE_PROTOCOL_SHA256,
        "candidate_artifact_path": (
            "artifacts/v7-bailian-strict-schema-candidates-20260826-001.json"
        ),
        "candidate_artifact_sha256": _CANDIDATE_ARTIFACT_SHA256,
    }.items():
        _require(construction.get(field), expected, label=f"construction.{field}")

    embedding = _mapping(value.get("embedding"), label="embedding identity")
    for field, expected in {
        "authority": EMBEDDING_AUTHORITY,
        "base_url": EMBEDDING_BASE_URL,
        "model": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIMENSION,
        "api_key_env": "SILICONFLOW_API_KEY",
        "dimension_policy": "EXACT_NO_TRUNCATION",
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
    }.items():
        _require(embedding.get(field), expected, label=f"embedding.{field}")
    if construction["authority"] == embedding["authority"]:
        raise _fail("strict development provider authorities were mixed")

    scientific = _mapping(
        value.get("scientific_method_selection"),
        label="scientific method selection",
    )
    for field, expected in {
        "path": "METHOD_SELECTION.json",
        "sha256": _SCIENTIFIC_METHOD_SELECTION_SHA256,
        "update_allowed": False,
    }.items():
        _require(scientific.get(field), expected, label=f"scientific.{field}")

    if verify_references:
        v7_root = selected.parent
        verify_development_source_bindings(
            v7_root,
            {
                str(construction["candidate_protocol_path"]): str(
                    construction["candidate_protocol_sha256"]
                ),
                str(construction["candidate_artifact_path"]): str(
                    construction["candidate_artifact_sha256"]
                ),
                str(scientific["path"]): str(scientific["sha256"]),
            },
        )
        candidate = _object(
            v7_root / str(construction["candidate_artifact_path"]),
            label="strict-schema candidate artifact",
        )
        selection = _mapping(candidate.get("selection"), label="candidate selection")
        if (
            candidate.get("status") != "PASS"
            or candidate.get("protocol_sha256") != _CANDIDATE_PROTOCOL_SHA256
            or candidate.get("raw_request_persisted") is not False
            or candidate.get("raw_response_persisted") is not False
            or candidate.get("response_hash_persisted") is not False
            or selection.get("status") != "SELECTED"
            or selection.get("selected_model") != STRICT_CONSTRUCTION_MODEL
            or selection.get("selection_rule")
            != "FIRST_ALL_LANES_FULL_PASS_IN_FROZEN_ORDER"
            or selection.get("structured_output_mode") != "json_schema"
            or selection.get("strict_json_schema") is not True
            or selection.get("formal_r1_r3_eligible") is not False
            or selection.get("live_treatment_authorized") is not False
        ):
            raise _fail("strict-schema candidate evidence is invalid")
        sources = _mapping(value.get("source_sha256"), label="runtime source hashes")
        verify_development_source_bindings(selected.parents[2], sources)
    return value


def normalize_strict_bailian_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce provider-native strict JSON schema at the HTTP boundary."""

    normalized = dict(request)
    if normalized.get("model") != STRICT_CONSTRUCTION_MODEL:
        raise _fail("strict Bailian construction model identity mismatch")
    response_format = normalized.get("response_format")
    wrapper = (
        response_format.get("json_schema")
        if isinstance(response_format, Mapping)
        else None
    )
    name = wrapper.get("name") if isinstance(wrapper, Mapping) else None
    schema = wrapper.get("schema") if isinstance(wrapper, Mapping) else None
    if (
        not isinstance(response_format, Mapping)
        or response_format.get("type") != "json_schema"
        or not isinstance(wrapper, Mapping)
        or not isinstance(name, str)
        or not name
        or not isinstance(schema, Mapping)
        or not schema
    ):
        raise _fail("strict Bailian construction requires a nonempty JSON schema")
    selected_wrapper = deepcopy(dict(wrapper))
    selected_wrapper["name"] = name
    selected_wrapper["schema"] = deepcopy(dict(schema))
    selected_wrapper["strict"] = True
    normalized["response_format"] = {
        "type": "json_schema",
        "json_schema": selected_wrapper,
    }
    temperature = normalized.get("temperature", 0.0)
    if isinstance(temperature, bool) or float(temperature) != 0.0:
        raise _fail("strict Bailian construction temperature drifted")
    normalized["temperature"] = 0.0
    top_p = normalized.get("top_p", 1.0)
    if isinstance(top_p, bool) or float(top_p) != 1.0:
        raise _fail("strict Bailian construction top_p drifted")
    normalized["top_p"] = 1.0
    normalized.pop("max_tokens", None)
    extra = normalized.get("extra_body")
    selected_extra = dict(extra) if isinstance(extra, Mapping) else {}
    if selected_extra.get("enable_thinking") not in {None, False}:
        raise _fail("strict Bailian construction thinking policy drifted")
    if "chat_template_kwargs" in selected_extra:
        raise _fail("strict Bailian construction received template extension")
    selected_extra["enable_thinking"] = False
    normalized["extra_body"] = selected_extra
    return normalized


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _usage_value(usage: Any, name: str) -> int:
    value = _field(usage, name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("strict Bailian response usage metadata is invalid")
    return value


class StrictBailianChatCompletions:
    """One-attempt completion transport with content-free in-memory observations."""

    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        if inner is None or not callable(getattr(inner, "create", None)):
            raise _fail("strict Bailian completion endpoint is invalid")
        if response_observer is not None and not callable(response_observer):
            raise _fail("strict Bailian response observer is invalid")
        self._inner = inner
        self._response_observer = response_observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        request = normalize_strict_bailian_request(kwargs)
        pending = self._inner.create(*args, **request)
        if not inspect.isawaitable(pending):
            raise _fail("strict Bailian completion operation must be async")
        result = await pending
        choices = _field(result, "choices", [])
        if not isinstance(choices, list) or len(choices) != 1:
            raise _fail("strict Bailian response choice shape is invalid")
        choice = choices[0]
        finish_reason = _field(choice, "finish_reason")
        content = _field(_field(choice, "message"), "content")
        if not isinstance(content, str) or not content.strip():
            raise _fail("strict Bailian structured response content is invalid")
        encoded = content.encode("utf-8")
        usage = _field(result, "usage")
        observation = {
            "lane": "construction",
            "structured": True,
            "finish_reason": None if finish_reason is None else str(finish_reason),
            "prompt_tokens": _usage_value(usage, "prompt_tokens"),
            "completion_tokens": _usage_value(usage, "completion_tokens"),
            "content_bytes": len(encoded),
            "content_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        if self._response_observer is not None:
            observed = self._response_observer(dict(observation))
            if inspect.isawaitable(observed):
                await observed
        if finish_reason != "stop":
            raise _fail("strict Bailian structured response finish reason is not stop")
        return result


class _StrictChat:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.completions = StrictBailianChatCompletions(
            getattr(inner, "completions", None),
            response_observer=response_observer,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class StrictBailianTransport:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.chat = _StrictChat(
            getattr(inner, "chat", None),
            response_observer=response_observer,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value:
        raise _fail(f"required strict runtime environment is missing: {name}")
    return value


def build_strict_development_runtime(
    *,
    env: Mapping[str, str],
    request_id_prefix: str,
    strict_freeze_path: str | Path,
    response_observer: Any | None = None,
    components: CompositeRuntimeComponents | None = None,
) -> CompositeEngineeringRuntime:
    """Build Graphiti with strict construction and the unchanged embedding lane."""

    frozen = load_strict_development_runtime_freeze(strict_freeze_path)
    if not request_id_prefix:
        raise _fail("strict runtime request identity is missing")
    construction_key = _required(env, "DASHSCOPE_API_KEY")
    embedding_key = _required(env, "SILICONFLOW_API_KEY")
    neo4j_user = _required(env, "NEO4J_USER")
    neo4j_password = _required(env, "NEO4J_PASSWORD")
    neo4j_uri = str(env.get("NEO4J_URI", NEO4J_URI))
    if neo4j_uri != NEO4J_URI:
        raise _fail("strict runtime Neo4j endpoint drifted")
    try:
        max_coroutines = int(env.get("GRAPHITI_MAX_COROUTINES", str(MAX_COROUTINES)))
    except (TypeError, ValueError):
        raise _fail("strict runtime concurrency is invalid") from None
    if max_coroutines != MAX_COROUTINES:
        raise _fail("strict runtime concurrency drifted")
    timeout_seconds = float(frozen["runtime"]["http_timeout_seconds"])
    selected = components or _production_components()

    raw_construction_transport = selected.openai_client_factory(
        api_key=construction_key,
        base_url=STRICT_CONSTRUCTION_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    construction_transport = StrictBailianTransport(
        raw_construction_transport,
        response_observer=response_observer,
    )
    llm_config = selected.llm_config_type(
        api_key=construction_key,
        model=STRICT_CONSTRUCTION_MODEL,
        small_model=STRICT_CONSTRUCTION_MODEL,
        base_url=STRICT_CONSTRUCTION_BASE_URL,
        temperature=0.0,
        max_tokens=16_384,
    )
    raw_llm = selected.qwen_client_type(
        config=llm_config,
        max_tokens=16_384,
        structured_output_mode="json_schema",
        vllm_options_enabled=False,
        client=construction_transport,
    )
    native_generate_response = raw_llm._generate_response

    async def _single_attempt_generate_response(
        messages: Any,
        response_model: Any = None,
        max_tokens: int = 4096,
        model_size: Any = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "response_model": response_model,
            "max_tokens": max_tokens,
        }
        if model_size is not None:
            kwargs["model_size"] = model_size
        return await native_generate_response(**kwargs)

    raw_llm._generate_response_with_retry = _single_attempt_generate_response
    validated_llm = ResponseModelValidatingLLMClient(raw_llm)
    admitted_llm = selected.admitted_client_type(
        inner=validated_llm,
        admission=selected.request_admission_type(limit=LLM_ADMISSION_LIMIT),
        request_id_prefix=request_id_prefix,
    )
    embedding_transport = selected.openai_client_factory(
        api_key=embedding_key,
        base_url=EMBEDDING_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    embedder = ExactDimensionEmbedder(
        getattr(embedding_transport, "embeddings", None),
        model=EMBEDDING_MODEL,
        dimension=EMBEDDING_DIMENSION,
    )
    reranker = selected.reranker_type(llm_config, client=raw_llm.client)
    graphiti = selected.graphiti_type(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=validated_llm,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=max_coroutines,
    )
    graphiti.llm_client = admitted_llm
    graphiti.clients.llm_client = admitted_llm
    graphiti.embedder = embedder
    graphiti.clients.embedder = embedder
    public_identity = {
        "schema_version": "membind.v7.strict-development-runtime.v1",
        "provider_identity_kind": STRICT_PROVIDER_IDENTITY_KIND,
        "construction": {
            "authority": STRICT_CONSTRUCTION_AUTHORITY,
            "base_url": STRICT_CONSTRUCTION_BASE_URL,
            "served_model_id": STRICT_CONSTRUCTION_MODEL,
            "structured_output_mode": "json_schema",
            "strict_json_schema": True,
            "prompt_schema_injection": False,
            "response_validation": "pydantic-v2",
            "max_tokens_sent_for_structured_output": False,
            "enable_thinking": False,
            "sdk_max_retries": 0,
        },
        "embedding": {
            "authority": EMBEDDING_AUTHORITY,
            "base_url": EMBEDDING_BASE_URL,
            "served_model_id": EMBEDDING_MODEL,
            "dimension": EMBEDDING_DIMENSION,
            "dimension_policy": "EXACT_NO_TRUNCATION",
            "sdk_max_retries": 0,
        },
        "backend": {"provider": "neo4j", "uri": neo4j_uri},
        "graphiti_max_coroutines": max_coroutines,
        "global_llm_admission": LLM_ADMISSION_LIMIT,
        "logical_retry_policy": "single_attempt_direct_no_tenacity",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "provider_swap_requires_new_formal_campaign": True,
    }
    identity_hash = _canonical_sha256(public_identity)
    return CompositeEngineeringRuntime(
        graphiti=graphiti,
        raw_llm=raw_llm,
        validated_llm=validated_llm,
        admitted_llm=admitted_llm,
        embedder=embedder,
        construction_transport=raw_construction_transport,
        embedding_transport=embedding_transport,
        public_identity=public_identity,
        execution_envelope_sha256=identity_hash,
        shared_public_identity=public_identity,
        shared_execution_envelope_sha256=identity_hash,
    )


__all__ = [
    "STRICT_CONSTRUCTION_AUTHORITY",
    "STRICT_CONSTRUCTION_MODEL",
    "STRICT_PROVIDER_IDENTITY_KIND",
    "StrictBailianChatCompletions",
    "StrictBailianTransport",
    "StrictDevelopmentRuntimeError",
    "build_strict_development_runtime",
    "load_strict_development_runtime_freeze",
    "normalize_strict_bailian_request",
]
