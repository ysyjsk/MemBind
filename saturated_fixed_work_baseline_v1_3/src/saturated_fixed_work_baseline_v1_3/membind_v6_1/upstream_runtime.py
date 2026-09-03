"""Formal Graphiti runtime with a transparent Qwen deployment facade.

This module deliberately does not import the finite-pair or structured-output
recovery implementations.  The only object graph mutation is construction of
the upstream Graphiti 0.29.3 objects; the transport facade adds deployment
fields and read-only telemetry at the OpenAI completion boundary.
"""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from native_characterization_runtime import U0Config, U0Runtime

from .routing import (
    EndpointSpec,
    RoutedOpenAIClient,
    install_routing_prompt_context,
)
from .runtime import (
    LOCAL_HTTP_TIMEOUT_SECONDS,
    LOCAL_MAX_COROUTINES,
    LOCAL_SDK_MAX_RETRIES,
    LocalRuntimeConfigurationError,
    _normalized_url,
    build_local_openai_transport,
    close_local_u0_runtime,
    install_local_single_attempt_policy,
)


FORMAL_ARM_A = "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_B = "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_C = "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_NAMES = (FORMAL_ARM_A, FORMAL_ARM_B, FORMAL_ARM_C)
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
GRAPHITI_VERSION = "0.29.3"
P0_MODEL = "qwen3-8b-awq"
P0_SAMPLING: dict[str, Any] = {
    "enable_thinking": False,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
    "structured_output_backend": "xgrammar",
}


_LOGICAL_IDENTITY: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "membind_formal_logical_request_identity", default=None
)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump())
    if hasattr(value, "__dict__") and not isinstance(value, type):
        return _canonical(vars(value))
    return value


def request_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(_canonical(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def logical_request_seed(identity: Mapping[str, Any]) -> int:
    """Derive a stable uint32 seed from logical, never physical, identity."""

    required = (
        "dataset_revision",
        "context_id",
        "source_sequence",
        "chunk_ordinal",
        "prompt_name",
        "canonical_messages_hash",
    )
    missing = [name for name in required if name not in identity]
    if missing:
        raise LocalRuntimeConfigurationError(
            "logical request identity is missing: " + ",".join(missing)
        )
    payload = "\0".join(str(identity[name]) for name in required).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


class logical_request_context:
    """Explicitly bind request identity at task creation, including async workers."""

    def __init__(self, identity: Mapping[str, Any]):
        self.identity = dict(identity)
        self._token: contextvars.Token[dict[str, Any] | None] | None = None

    def __enter__(self) -> "logical_request_context":
        self._token = _LOGICAL_IDENTITY.set(dict(self.identity))
        return self

    def __exit__(self, *_args: object) -> None:
        if self._token is not None:
            _LOGICAL_IDENTITY.reset(self._token)
            self._token = None


def current_logical_request_identity() -> dict[str, Any] | None:
    value = _LOGICAL_IDENTITY.get()
    return dict(value) if value is not None else None


def _message_payload(messages: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, Mapping):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
        result.append({"role": role, "content": content})
    return result


def install_logical_llm_context(llm_client: Any) -> Callable[[], None]:
    """Bind the upstream prompt name without changing its call arguments.

    OpenAIGenericClient appends its multilingual instruction after this seam.
    The canonical message hash is therefore completed at the transport seam,
    where the exact wire messages are available.
    """

    original = getattr(llm_client, "generate_response", None)
    if not callable(original):
        raise LocalRuntimeConfigurationError("upstream logical LLM seam is unavailable")

    async def generate_response(messages: Any, **kwargs: Any) -> Any:
        base = current_logical_request_identity()
        if base is None:
            raise LocalRuntimeConfigurationError(
                "Graphiti request was created without chunk provenance"
            )
        identity = {
            **base,
            "prompt_name": str(kwargs.get("prompt_name") or "UNNAMED_UPSTREAM_PROMPT"),
        }
        with logical_request_context(identity):
            return await original(messages, **kwargs)

    setattr(llm_client, "generate_response", generate_response)

    def restore() -> None:
        setattr(llm_client, "generate_response", original)

    return restore


@dataclass
class _TransportTelemetry:
    rows: list[dict[str, Any]]


class _TransparentCompletions:
    def __init__(self, owner: "_TransparentEndpointClient") -> None:
        self._owner = owner

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return await self._owner.create(*args, **kwargs)


class _TransparentEndpointClient:
    """Endpoint client that only adds documented deployment fields."""

    def __init__(self, client: Any, *, endpoint_id: str, telemetry: _TransportTelemetry):
        self._client = client
        self.endpoint_id = endpoint_id
        self.telemetry = telemetry
        self.chat = SimpleNamespace(completions=_TransparentCompletions(self))

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        before = dict(kwargs)
        base_identity = current_logical_request_identity()
        if base_identity is None:
            raise LocalRuntimeConfigurationError(
                "formal transport requires explicit logical request identity"
            )
        if "messages" not in before:
            raise LocalRuntimeConfigurationError(
                "formal transport requires wire messages for stable seed derivation"
            )
        identity = {
            **base_identity,
            "canonical_messages_hash": request_hash(
                {"messages": _message_payload(before["messages"])}
            ),
        }
        seed = logical_request_seed(identity)
        after = dict(kwargs)
        if "temperature" in after and after["temperature"] != P0_SAMPLING["temperature"]:
            raise LocalRuntimeConfigurationError("wire temperature differs from frozen P0")
        if "top_p" in after and after["top_p"] != P0_SAMPLING["top_p"]:
            raise LocalRuntimeConfigurationError("wire top_p differs from frozen P0")
        if "presence_penalty" in after and after["presence_penalty"] != P0_SAMPLING["presence_penalty"]:
            raise LocalRuntimeConfigurationError(
                "wire presence_penalty differs from frozen P0"
            )
        if "seed" in after and after["seed"] != seed:
            raise LocalRuntimeConfigurationError("wire seed differs from logical identity")
        after.setdefault("temperature", P0_SAMPLING["temperature"])
        after.setdefault("top_p", P0_SAMPLING["top_p"])
        after.setdefault("presence_penalty", P0_SAMPLING["presence_penalty"])
        after.setdefault("seed", seed)
        extra_body = dict(after.get("extra_body") or {})
        required_extra = {
            "top_k": P0_SAMPLING["top_k"],
            "min_p": P0_SAMPLING["min_p"],
            "chat_template_kwargs": {"enable_thinking": False},
        }
        for field, expected in required_extra.items():
            if field in extra_body and extra_body[field] != expected:
                raise LocalRuntimeConfigurationError(
                    f"wire extra_body.{field} differs from frozen P0"
                )
            extra_body.setdefault(field, expected)
        after["extra_body"] = extra_body
        before_hash = request_hash(before)
        after_hash = request_hash(after)
        semantic_fields = ("model", "messages", "max_tokens", "response_format")
        before_semantic = {field: before.get(field) for field in semantic_fields}
        after_semantic = {field: after.get(field) for field in semantic_fields}
        if request_hash(before_semantic) != request_hash(after_semantic):
            raise LocalRuntimeConfigurationError("transport changed a Graphiti semantic field")
        row = {
                "endpoint_id": self.endpoint_id,
                "before_request_sha256": before_hash,
                "after_request_sha256": after_hash,
                "semantic_request_sha256": request_hash(before_semantic),
                "wire_messages_sha256": identity["canonical_messages_hash"],
                "seed": seed,
                "logical_identity": dict(identity),
                "allowed_added_fields": [
                    "temperature",
                    "top_p",
                    "presence_penalty",
                    "seed",
                    "extra_body.top_k",
                    "extra_body.min_p",
                    "extra_body.chat_template_kwargs.enable_thinking",
                ],
                "status": "started",
            }
        self.telemetry.rows.append(row)
        try:
            response = await self._client.chat.completions.create(*args, **after)
        except BaseException as exc:
            row["status"] = "failure"
            row["exception_type"] = f"{type(exc).__module__}.{type(exc).__qualname__}"
            raise
        row["status"] = "success"
        row["response_sha256"] = request_hash({"response": _canonical(response)})
        choices = getattr(response, "choices", None)
        row["finish_reason"] = (
            getattr(choices[0], "finish_reason", None) if choices else None
        )
        return response

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise LocalRuntimeConfigurationError(f"{name} is required for formal upstream runtime")
    return value


def build_formal_upstream_runtime(
    *,
    routing_contract: Mapping[str, Any],
    route_event_sink: Callable[[dict[str, Any]], None] | None = None,
    arm: str = FORMAL_ARM_A,
) -> U0Runtime:
    """Construct all formal arms from the same upstream Graphiti core."""

    if arm not in FORMAL_ARM_NAMES:
        raise LocalRuntimeConfigurationError(f"unknown formal arm: {arm}")
    policy = str(routing_contract.get("router", {}).get("policy", ""))
    if not policy:
        raise LocalRuntimeConfigurationError("formal routing policy is missing")
    endpoint_values = routing_contract.get("endpoint_set")
    if not isinstance(endpoint_values, list) or not endpoint_values:
        raise LocalRuntimeConfigurationError("formal endpoint set is missing")
    specs = tuple(EndpointSpec.from_mapping(value) for value in endpoint_values)
    key = _required("CONSTRUCTION_LLM_API_KEY")
    telemetry = _TransportTelemetry([])
    clients = {
        spec.endpoint_id: _TransparentEndpointClient(
            build_local_openai_transport(
                api_key=key,
                base_url=spec.base_url,
                timeout_seconds=LOCAL_HTTP_TIMEOUT_SECONDS,
                max_retries=LOCAL_SDK_MAX_RETRIES,
            ),
            endpoint_id=spec.endpoint_id,
            telemetry=telemetry,
        )
        for spec in specs
    }
    router = RoutedOpenAIClient(
        policy=policy,
        endpoints=specs,
        endpoint_clients=clients,
        event_sink=route_event_sink,
    )
    native_url = next(spec.base_url for spec in specs if spec.endpoint_id == "native-replica")
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

    llm_config = LLMConfig(
        api_key=key,
        model=P0_MODEL,
        small_model=P0_MODEL,
        base_url=native_url,
        temperature=P0_SAMPLING["temperature"],
        max_tokens=int(os.environ.get("CONSTRUCTION_MAX_TOKENS", "16384")),
    )
    llm_client = OpenAIGenericClient(
        config=llm_config,
        client=router,
        max_tokens=llm_config.max_tokens,
        structured_output_mode="json_schema",
    )
    # OpenAIGenericClient's tenacity wrapper is an SDK-level retry, distinct
    # from the transport retry count.  Formal requests are single-attempt.
    install_local_single_attempt_policy(llm_client)
    logical_context_restore = install_logical_llm_context(llm_client)
    routing_context_restore = install_routing_prompt_context(llm_client)
    embedder = OpenAIEmbedder(
        OpenAIEmbedderConfig(
            api_key=_required("EMBEDDING_API_KEY"),
            base_url=_normalized_url(_required("EMBEDDING_BASE_URL")),
            embedding_model=_required("EMBEDDING_MODEL"),
            embedding_dim=int(_required("EMBEDDING_DIM")),
        ),
        client=build_local_openai_transport(
            api_key=_required("EMBEDDING_API_KEY"),
            base_url=_normalized_url(_required("EMBEDDING_BASE_URL")),
            timeout_seconds=LOCAL_HTTP_TIMEOUT_SECONDS,
            max_retries=LOCAL_SDK_MAX_RETRIES,
        ),
    )
    reranker = OpenAIRerankerClient(config=llm_config, client=router)
    graphiti = Graphiti(
        uri=_required("NEO4J_URI"),
        user=_required("NEO4J_USER"),
        password=_required("NEO4J_PASSWORD"),
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=LOCAL_MAX_COROUTINES,
    )
    runtime = U0Runtime(
        graphiti=graphiti,
        llm_client=llm_client,
        embedder=embedder,
        reranker=reranker,
        config=U0Config(
            construction_base_url=native_url,
            construction_model=P0_MODEL,
            construction_model_revision=os.environ.get("CONSTRUCTION_MODEL_REVISION", "UNPINNED"),
            embedding_base_url=_normalized_url(_required("EMBEDDING_BASE_URL")),
            embedding_model=_required("EMBEDDING_MODEL"),
            embedding_dimension=int(_required("EMBEDDING_DIM")),
            neo4j_uri=_required("NEO4J_URI"),
            max_coroutines=LOCAL_MAX_COROUTINES,
            structured_output_mode="json_schema",
            requested_max_tokens=llm_config.max_tokens,
            context_limit=int(os.environ.get("CONSTRUCTION_MIN_CONTEXT_TOKENS", "40960")),
            safety_margin_tokens=int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "256")),
        ),
        classification=arm,
    )
    runtime._membind_formal_arm = arm
    runtime._membind_graphiti_version = GRAPHITI_VERSION
    runtime._membind_graphiti_commit = GRAPHITI_COMMIT
    runtime._membind_transport_telemetry = telemetry.rows
    runtime._membind_logical_context_restore = logical_context_restore
    runtime._membind_routing_context_restore = routing_context_restore
    runtime._membind_route_client = router
    runtime._membind_patch_inventory = {
        "strict_upstream_core": True,
        "graphiti_algorithm_mutated": False,
        "prohibited_algorithm_patches": [],
        "transport_only_fields": sorted(
            ["temperature", "top_p", "presence_penalty", "seed", "extra_body.top_k", "extra_body.min_p", "extra_body.chat_template_kwargs.enable_thinking"]
        ),
    }
    runtime._membind_owned_transports = (router, embedder.client)
    runtime._membind_runtime_closed = False
    return runtime


async def close_formal_upstream_runtime(runtime: U0Runtime) -> None:
    routing_restore = getattr(runtime, "_membind_routing_context_restore", None)
    if callable(routing_restore):
        routing_restore()
        runtime._membind_routing_context_restore = None
    restore = getattr(runtime, "_membind_logical_context_restore", None)
    if callable(restore):
        restore()
        runtime._membind_logical_context_restore = None
    await close_local_u0_runtime(runtime)


__all__ = [
    "FORMAL_ARM_A",
    "FORMAL_ARM_B",
    "FORMAL_ARM_C",
    "FORMAL_ARM_NAMES",
    "GRAPHITI_COMMIT",
    "GRAPHITI_VERSION",
    "P0_MODEL",
    "P0_SAMPLING",
    "build_formal_upstream_runtime",
    "close_formal_upstream_runtime",
    "current_logical_request_identity",
    "logical_request_context",
    "logical_request_seed",
    "install_logical_llm_context",
    "request_hash",
]
