"""Selected 122B Bailian + SiliconFlow runtime for V7 development only."""

from __future__ import annotations

import hashlib
import inspect
import json
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


class SelectedDevelopmentRuntimeError(RuntimeError):
    pass


SELECTED_CONSTRUCTION_AUTHORITY = (
    "alibaba-bailian-openai-compatible-development-selected-v1"
)
SELECTED_CONSTRUCTION_BASE_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
SELECTED_CONSTRUCTION_MODEL = "qwen3.5-122b-a10b"
SELECTED_PROVIDER_IDENTITY_KIND = "COMPOSITE_DEVELOPMENT_SELECTED_TEMPORARY"
_CANDIDATE_ARTIFACT_SHA256 = (
    "d263d08746bc8fc801c650af16586ea48c8f6b42c94309aef02cc33fccff0783"
)
_CANDIDATE_PROTOCOL_SHA256 = (
    "d9d7608272eb616557011e6fd6ae1fe53ce88ac89419afe915042ba4ce3ad018"
)
_SCIENTIFIC_METHOD_SELECTION_SHA256 = (
    "0a2958aeeedc2b7b8762247d5d6cf15252e2b6b737b5807a51a127461a632c53"
)


def _fail(message: str) -> SelectedDevelopmentRuntimeError:
    return SelectedDevelopmentRuntimeError(message)


def _sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
        raise _fail(f"selected development runtime freeze drifted: {label}")


def load_selected_development_runtime_freeze(
    path: str | Path, *, verify_references: bool = True
) -> dict[str, Any]:
    """Load the candidate-selected runtime and verify all frozen local inputs."""

    selected = Path(path).resolve()
    value = _object(selected, label="selected development runtime freeze")
    for field, expected in {
        "schema_version": "membind.v7.selected-development-runtime-freeze.v1",
        "status": "FROZEN_AFTER_CANDIDATE_GATE_BEFORE_R1_R3_REPLACEMENT",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": SELECTED_PROVIDER_IDENTITY_KIND,
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
        "authority": SELECTED_CONSTRUCTION_AUTHORITY,
        "base_url": SELECTED_CONSTRUCTION_BASE_URL,
        "model": SELECTED_CONSTRUCTION_MODEL,
        "api_key_env": "DASHSCOPE_API_KEY",
        "temperature": 0.0,
        "top_p": 1.0,
        "structured_output_mode": "json_object",
        "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
        "response_validation": "pydantic-v2",
        "max_tokens_sent_for_structured_output": False,
        "enable_thinking": False,
        "sdk_max_retries": 0,
        "hard_attempt_limit_per_request": 1,
        "selection_rule": "FIRST_FULL_PASS_IN_FROZEN_ORDER",
        "candidate_protocol_path": "BAILIAN_V7_DEVELOPMENT_MODEL_CANDIDATE_PROTOCOL.json",
        "candidate_protocol_sha256": _CANDIDATE_PROTOCOL_SHA256,
        "candidate_artifact_path": "artifacts/v7-bailian-development-candidates-20260826-001.json",
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
        raise _fail("selected development provider authorities were mixed")
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
            label="selected candidate artifact",
        )
        selection = _mapping(candidate.get("selection"), label="candidate selection")
        if (
            candidate.get("status") != "PASS"
            or selection.get("status") != "SELECTED"
            or selection.get("selected_model") != SELECTED_CONSTRUCTION_MODEL
            or selection.get("selection_rule")
            != "FIRST_FULL_PASS_IN_FROZEN_ORDER"
            or selection.get("formal_r1_r3_eligible") is not False
            or selection.get("live_treatment_authorized") is not False
        ):
            raise _fail("selected candidate evidence is invalid")
        sources = _mapping(value.get("source_sha256"), label="runtime source hashes")
        verify_development_source_bindings(selected.parents[2], sources)
    return value


def normalize_selected_bailian_request(
    request: Mapping[str, Any]
) -> dict[str, Any]:
    """Enforce the selected construction contract at the HTTP boundary."""

    normalized = dict(request)
    if normalized.get("model") != SELECTED_CONSTRUCTION_MODEL:
        raise _fail("selected Bailian construction model identity mismatch")
    response_format = normalized.get("response_format")
    structured = response_format is not None
    if structured:
        if response_format != {"type": "json_object"}:
            raise _fail("selected Bailian construction requires JSON Object mode")
        temperature = normalized.get("temperature", 0.0)
        if isinstance(temperature, bool) or float(temperature) != 0.0:
            raise _fail("selected Bailian construction temperature drifted")
        normalized.pop("max_tokens", None)
    top_p = normalized.get("top_p", 1.0)
    if isinstance(top_p, bool) or float(top_p) != 1.0:
        raise _fail("selected Bailian construction top_p drifted")
    normalized["top_p"] = 1.0
    extra = normalized.get("extra_body")
    selected_extra = dict(extra) if isinstance(extra, Mapping) else {}
    if selected_extra.get("enable_thinking") not in {None, False}:
        raise _fail("selected Bailian construction thinking policy drifted")
    if "chat_template_kwargs" in selected_extra:
        raise _fail("selected Bailian construction received template extension")
    selected_extra["enable_thinking"] = False
    normalized["extra_body"] = selected_extra
    return normalized


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _usage_value(usage: Any, name: str) -> int:
    value = _field(usage, name, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("selected Bailian response usage metadata is invalid")
    return value


class SelectedBailianChatCompletions:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        if inner is None or not callable(getattr(inner, "create", None)):
            raise _fail("selected Bailian completion endpoint is invalid")
        if response_observer is not None and not callable(response_observer):
            raise _fail("selected Bailian response observer is invalid")
        self._inner = inner
        self._response_observer = response_observer

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        request = normalize_selected_bailian_request(kwargs)
        structured = request.get("response_format") == {"type": "json_object"}
        pending = self._inner.create(*args, **request)
        if not inspect.isawaitable(pending):
            raise _fail("selected Bailian completion operation must be async")
        result = await pending
        choices = _field(result, "choices", [])
        if not isinstance(choices, list) or len(choices) != 1:
            raise _fail("selected Bailian response choice shape is invalid")
        choice = choices[0]
        finish_reason = _field(choice, "finish_reason")
        content = _field(_field(choice, "message"), "content")
        if not isinstance(content, str) or (structured and not content.strip()):
            raise _fail("selected Bailian response content is invalid")
        encoded = content.encode("utf-8")
        usage = _field(result, "usage")
        observation = {
            "lane": "construction",
            "structured": structured,
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
        if structured and finish_reason != "stop":
            raise _fail("selected Bailian structured response finish reason is not stop")
        return result


class _SelectedChat:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.completions = SelectedBailianChatCompletions(
            getattr(inner, "completions", None),
            response_observer=response_observer,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class SelectedBailianTransport:
    def __init__(self, inner: Any, *, response_observer: Any | None = None) -> None:
        self._inner = inner
        self.chat = _SelectedChat(
            getattr(inner, "chat", None),
            response_observer=response_observer,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value:
        raise _fail(f"required selected runtime environment is missing: {name}")
    return value


def build_selected_development_runtime(
    *,
    env: Mapping[str, str],
    request_id_prefix: str,
    selected_freeze_path: str | Path,
    response_observer: Any | None = None,
    components: CompositeRuntimeComponents | None = None,
) -> CompositeEngineeringRuntime:
    """Build Graphiti with selected construction and unchanged embedding lane."""

    frozen = load_selected_development_runtime_freeze(selected_freeze_path)
    if not request_id_prefix:
        raise _fail("selected runtime request identity is missing")
    construction_key = _required(env, "DASHSCOPE_API_KEY")
    embedding_key = _required(env, "SILICONFLOW_API_KEY")
    neo4j_user = _required(env, "NEO4J_USER")
    neo4j_password = _required(env, "NEO4J_PASSWORD")
    neo4j_uri = str(env.get("NEO4J_URI", NEO4J_URI))
    if neo4j_uri != NEO4J_URI:
        raise _fail("selected runtime Neo4j endpoint drifted")
    try:
        max_coroutines = int(env.get("GRAPHITI_MAX_COROUTINES", str(MAX_COROUTINES)))
    except (TypeError, ValueError):
        raise _fail("selected runtime concurrency is invalid") from None
    if max_coroutines != MAX_COROUTINES:
        raise _fail("selected runtime concurrency drifted")
    timeout_seconds = float(frozen["runtime"]["http_timeout_seconds"])
    selected = components or _production_components()

    raw_construction_transport = selected.openai_client_factory(
        api_key=construction_key,
        base_url=SELECTED_CONSTRUCTION_BASE_URL,
        timeout_seconds=timeout_seconds,
    )
    construction_transport = SelectedBailianTransport(
        raw_construction_transport,
        response_observer=response_observer,
    )
    llm_config = selected.llm_config_type(
        api_key=construction_key,
        model=SELECTED_CONSTRUCTION_MODEL,
        small_model=SELECTED_CONSTRUCTION_MODEL,
        base_url=SELECTED_CONSTRUCTION_BASE_URL,
        temperature=0.0,
        max_tokens=16_384,
    )
    raw_llm = selected.qwen_client_type(
        config=llm_config,
        max_tokens=16_384,
        structured_output_mode="json_object",
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
        "schema_version": "membind.v7.selected-development-runtime.v1",
        "provider_identity_kind": SELECTED_PROVIDER_IDENTITY_KIND,
        "construction": {
            "authority": SELECTED_CONSTRUCTION_AUTHORITY,
            "base_url": SELECTED_CONSTRUCTION_BASE_URL,
            "served_model_id": SELECTED_CONSTRUCTION_MODEL,
            "structured_output_mode": "json_object",
            "prompt_schema_injection": "graphiti-json-object-constrained-pydantic-v1",
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
    "SELECTED_CONSTRUCTION_AUTHORITY",
    "SELECTED_CONSTRUCTION_MODEL",
    "SELECTED_PROVIDER_IDENTITY_KIND",
    "SelectedBailianChatCompletions",
    "SelectedBailianTransport",
    "SelectedDevelopmentRuntimeError",
    "build_selected_development_runtime",
    "load_selected_development_runtime_freeze",
    "normalize_selected_bailian_request",
]
