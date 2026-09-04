"""Formal Graphiti runtime with a transparent Qwen deployment facade.

This module constructs the upstream Graphiti 0.29.3 object graph and installs
the same arm-agnostic bounded structured-output compatibility seam for every
formal arm.  The seam is an explicit shared substrate; it does not inspect an
arm identity or change Graphiti's episode/state/publication semantics.
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
from .shared_structured_output import adapter_identity
from .runtime import (
    LOCAL_HTTP_TIMEOUT_SECONDS,
    LOCAL_MAX_COROUTINES,
    LOCAL_SDK_MAX_RETRIES,
    LocalRuntimeConfigurationError,
    _normalized_url,
    build_local_openai_transport,
    close_local_u0_runtime,
    install_local_extraction_chunking_policy,
    install_local_single_attempt_policy,
)


# The strict unbounded upstream path is retained only as an A0 compatibility
# characterization.  The executable formal arms share the bounded substrate
# because the pinned ExtractedEdges schema has an unbounded array.
FORMAL_ARM_A = "GRAPHITI_SERIAL_SHARED_BOUNDED_SO"
FORMAL_ARM_B = "RELAXED_ORDER_SHARED_BOUNDED_SO"
FORMAL_ARM_C = "MEMBIND_V6_1_SHARED_BOUNDED_SO"
FORMAL_ARM_NAMES = (FORMAL_ARM_A, FORMAL_ARM_B, FORMAL_ARM_C)
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
GRAPHITI_VERSION = "0.29.3"
P0_DEPLOYMENT_POLICY_ID = "P0_QWEN3_8B_AWQ"
P1_DEPLOYMENT_POLICY_ID = "P1_QWEN25_7B_AWQ"
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
P1_MODEL = "qwen2.5-7b-instruct-awq"
P1_SAMPLING: dict[str, Any] = {
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "repetition_penalty": 1.05,
    "structured_output_backend": "xgrammar",
}


@dataclass(frozen=True)
class DeploymentPolicy:
    policy_id: str
    profile_id: str
    source_model: str
    served_model: str
    revision: str
    sampling: Mapping[str, Any]
    transport_only_fields: tuple[str, ...]


P0_DEPLOYMENT_POLICY = DeploymentPolicy(
    policy_id=P0_DEPLOYMENT_POLICY_ID,
    profile_id="local-qwen3-8b-awq-dualreplica-v1",
    source_model="Qwen/Qwen3-8B-AWQ",
    served_model=P0_MODEL,
    revision="4da05a8edb55c6046cce958586c33b61da07bb79",
    sampling=P0_SAMPLING,
    transport_only_fields=(
        "temperature",
        "top_p",
        "presence_penalty",
        "seed",
        "extra_body.top_k",
        "extra_body.min_p",
        "extra_body.chat_template_kwargs.enable_thinking",
    ),
)
P1_DEPLOYMENT_POLICY = DeploymentPolicy(
    policy_id=P1_DEPLOYMENT_POLICY_ID,
    profile_id="local-qwen25-7b-awq-dualreplica-v1",
    source_model="Qwen/Qwen2.5-7B-Instruct-AWQ",
    served_model=P1_MODEL,
    revision="b25037543e9394b818fdfca67ab2a00ecc7dd641",
    sampling=P1_SAMPLING,
    transport_only_fields=(
        "temperature",
        "top_p",
        "seed",
        "extra_body.top_k",
        "extra_body.repetition_penalty",
    ),
)
DEPLOYMENT_POLICIES = {
    policy.policy_id: policy
    for policy in (P0_DEPLOYMENT_POLICY, P1_DEPLOYMENT_POLICY)
}


def resolve_deployment_policy(
    environment: Mapping[str, str] | None = None,
) -> DeploymentPolicy:
    env = os.environ if environment is None else environment
    policy_id = env.get("MEMBIND_DEPLOYMENT_POLICY_ID", P0_DEPLOYMENT_POLICY_ID)
    try:
        policy = DEPLOYMENT_POLICIES[policy_id]
    except KeyError as exc:
        raise LocalRuntimeConfigurationError(
            f"unknown deployment policy: {policy_id}"
        ) from exc
    profile_id = env.get("MEMBIND_PROFILE_ID")
    if profile_id is not None and profile_id != policy.profile_id:
        raise LocalRuntimeConfigurationError(
            "deployment profile identity does not match the frozen policy"
        )
    served_model = env.get("MEMBIND_LLM_MODEL_NAME")
    if served_model is not None and served_model != policy.served_model:
        raise LocalRuntimeConfigurationError(
            "deployment model identity does not match the frozen policy"
        )
    revision = env.get("MEMBIND_LLM_MODEL_REVISION")
    if revision is not None and revision != policy.revision:
        raise LocalRuntimeConfigurationError(
            "deployment model revision does not match the frozen policy"
        )
    return policy


def deployment_wire_fields(policy: DeploymentPolicy, *, seed: int) -> dict[str, Any]:
    sampling = policy.sampling
    fields: dict[str, Any] = {
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "seed": seed,
    }
    if policy.policy_id == P0_DEPLOYMENT_POLICY_ID:
        fields["presence_penalty"] = sampling["presence_penalty"]
        fields["extra_body"] = {
            "top_k": sampling["top_k"],
            "min_p": sampling["min_p"],
            "chat_template_kwargs": {"enable_thinking": False},
        }
    elif policy.policy_id == P1_DEPLOYMENT_POLICY_ID:
        fields["extra_body"] = {
            "top_k": sampling["top_k"],
            "repetition_penalty": sampling["repetition_penalty"],
        }
    else:  # pragma: no cover - DeploymentPolicy construction is internal.
        raise LocalRuntimeConfigurationError(
            f"unsupported deployment policy: {policy.policy_id}"
        )
    return fields


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

    def __init__(
        self,
        client: Any,
        *,
        endpoint_id: str,
        telemetry: _TransportTelemetry,
        deployment_policy: DeploymentPolicy = P0_DEPLOYMENT_POLICY,
    ):
        self._client = client
        self.endpoint_id = endpoint_id
        self.telemetry = telemetry
        self.deployment_policy = deployment_policy
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
        expected_fields = deployment_wire_fields(self.deployment_policy, seed=seed)
        expected_extra = dict(expected_fields.pop("extra_body"))
        for field, expected in expected_fields.items():
            if field in after and after[field] != expected:
                raise LocalRuntimeConfigurationError(
                    f"wire {field} differs from frozen {self.deployment_policy.policy_id}"
                )
            after.setdefault(field, expected)
        extra_body = dict(after.get("extra_body") or {})
        required_extra = expected_extra
        for field, expected in required_extra.items():
            if field in extra_body and extra_body[field] != expected:
                raise LocalRuntimeConfigurationError(
                    f"wire extra_body.{field} differs from frozen "
                    f"{self.deployment_policy.policy_id}"
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
                "deployment_policy_id": self.deployment_policy.policy_id,
                "served_model": self.deployment_policy.served_model,
                "logical_identity": dict(identity),
                "allowed_added_fields": list(
                    self.deployment_policy.transport_only_fields
                ),
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
        message = getattr(choices[0], "message", None) if choices else None
        content = getattr(message, "content", None)
        if isinstance(content, str):
            row["response_characters"] = len(content)
            row["response_bytes"] = len(content.encode("utf-8"))
            row["response_content_sha256"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
            try:
                json.loads(content)
            except (TypeError, ValueError) as exc:
                row["response_json_valid"] = False
                row["response_json_error"] = str(exc)[:500]
            else:
                row["response_json_valid"] = True
        usage = getattr(response, "usage", None)
        if usage is not None:
            row["usage"] = {
                field: getattr(usage, field, None)
                for field in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                )
            }
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
    deployment = resolve_deployment_policy()
    policy = str(routing_contract.get("router", {}).get("policy", ""))
    if not policy:
        raise LocalRuntimeConfigurationError("formal routing policy is missing")
    endpoint_values = routing_contract.get("endpoint_set")
    if not isinstance(endpoint_values, list) or not endpoint_values:
        raise LocalRuntimeConfigurationError("formal endpoint set is missing")
    specs = tuple(EndpointSpec.from_mapping(value) for value in endpoint_values)
    if any(spec.served_model != deployment.served_model for spec in specs):
        raise LocalRuntimeConfigurationError(
            "routing endpoint model differs from frozen deployment policy"
        )
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
            deployment_policy=deployment,
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
        model=deployment.served_model,
        small_model=deployment.served_model,
        base_url=native_url,
        temperature=float(deployment.sampling["temperature"]),
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
    # The executable formal arms share one disclosed local-LLM compatibility
    # substrate.  It bounds evidence/edge extraction without changing the
    # Graphiti episode/state/publication call graph or database semantics.
    install_local_extraction_chunking_policy(
        llm_client,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        summary_entity_page_capacity=1,
        dedupe_candidate_page_capacity=1,
        node_partition_concurrency=2,
        edge_partition_concurrency=2,
        edge_physical_concurrency=2,
        edge_frontier_priority=True,
        edge_duplicate_recovery=True,
        edge_endpoint_schema_grounding=True,
        shared_bounded_structured_output=True,
    )
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
            construction_model=deployment.served_model,
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
    runtime._membind_deployment_policy = deployment
    runtime._membind_transport_telemetry = telemetry.rows
    runtime._membind_logical_context_restore = logical_context_restore
    runtime._membind_routing_context_restore = routing_context_restore
    runtime._membind_route_client = router
    runtime._membind_shared_bounded_structured_output = True
    runtime._membind_shared_structured_output_identity = adapter_identity()
    runtime._membind_patch_inventory = {
        "strict_upstream_core": False,
        "graphiti_algorithm_mutated": False,
        "shared_compatibility_substrate": True,
        "compatibility_patches": [
            "lossless_evidence_partitioning",
            "bounded_edge_page_schema",
            "endpoint_grounding",
            "single_deterministic_duplicate_recovery",
        ],
        "prohibited_algorithm_patches": [],
        "deployment_policy_id": deployment.policy_id,
        "transport_only_fields": sorted(deployment.transport_only_fields),
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
    "DeploymentPolicy",
    "P0_DEPLOYMENT_POLICY_ID",
    "P1_DEPLOYMENT_POLICY_ID",
    "P0_DEPLOYMENT_POLICY",
    "P1_DEPLOYMENT_POLICY",
    "P0_MODEL",
    "P0_SAMPLING",
    "P1_MODEL",
    "P1_SAMPLING",
    "build_formal_upstream_runtime",
    "close_formal_upstream_runtime",
    "current_logical_request_identity",
    "logical_request_context",
    "logical_request_seed",
    "install_logical_llm_context",
    "request_hash",
    "deployment_wire_fields",
    "resolve_deployment_policy",
]
