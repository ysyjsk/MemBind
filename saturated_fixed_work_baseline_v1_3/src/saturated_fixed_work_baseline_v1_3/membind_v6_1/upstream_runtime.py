"""Formal Graphiti runtime with a transparent Qwen deployment facade.

The formal builder constructs the upstream Graphiti 0.29.3 object graph.  Its
only wrappers provide endpoint routing, telemetry, a stable logical seed, and
the frozen single-attempt transport policy.  Historical bounded-output and
finite-pair compatibility code remains available elsewhere for ablations, but
is deliberately absent from this builder.
"""

from __future__ import annotations

import contextvars
import hashlib
import inspect
import json
import os
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
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


# Public formal identities. All three share the strict upstream Graphiti core;
# B changes only session scheduling and C changes only prepare/replay routing
# and authoritative publication scheduling.
FORMAL_ARM_A = "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_B = "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_C = "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192"
FORMAL_ARM_NAMES = (FORMAL_ARM_A, FORMAL_ARM_B, FORMAL_ARM_C)
GRAPHITI_COMMIT = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
GRAPHITI_VERSION = "0.29.3"
GRAPHITI_CLASS_IDENTITY = "graphiti_core.graphiti.Graphiti"
GRAPHITI_ADD_EPISODE_MODULE = "graphiti_core.graphiti"
GRAPHITI_ADD_EPISODE_QUALNAME = "Graphiti.add_episode"
OPENAI_GENERIC_CLIENT_IDENTITY = (
    "graphiti_core.llm_client.openai_generic_client.OpenAIGenericClient"
)
EXTRACTED_EDGES_MODULE = "graphiti_core.prompts.extract_edges"
EXTRACTED_EDGES_QUALNAME = "ExtractedEdges"
P0_DEPLOYMENT_POLICY_ID = "P0_QWEN3_8B_AWQ"
P1_DEPLOYMENT_POLICY_ID = "P1_QWEN25_7B_AWQ"
P2_DEPLOYMENT_POLICY_ID = "P2_QWEN3_14B_AWQ"
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
P2_MODEL = "qwen3-14b-awq"
P2_SAMPLING: dict[str, Any] = {
    "enable_thinking": False,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0,
    "presence_penalty": 1.5,
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
P2_DEPLOYMENT_POLICY = DeploymentPolicy(
    policy_id=P2_DEPLOYMENT_POLICY_ID,
    profile_id="local-qwen3-14b-awq-dualreplica-v1",
    source_model="Qwen/Qwen3-14B-AWQ",
    served_model=P2_MODEL,
    revision="31c69efc29464b6bb0aee1398b5a7b50a99340c3",
    sampling=P2_SAMPLING,
    transport_only_fields=(
        "temperature",
        "top_p",
        "seed",
        "extra_body.top_k",
        "extra_body.chat_template_kwargs.enable_thinking",
    ),
)
DEPLOYMENT_POLICIES = {
    policy.policy_id: policy
    for policy in (P0_DEPLOYMENT_POLICY, P1_DEPLOYMENT_POLICY, P2_DEPLOYMENT_POLICY)
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
    elif policy.policy_id == P2_DEPLOYMENT_POLICY_ID:
        fields["presence_penalty"] = sampling["presence_penalty"]
        fields["extra_body"] = {
            "top_k": sampling["top_k"],
            "min_p": sampling["min_p"],
            "chat_template_kwargs": {"enable_thinking": False},
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


def strict_formal_runtime_identity_errors(
    identity: Mapping[str, Any] | Any,
    *,
    expected_arm: str | None = None,
    expected_manifest_sha256: str | None = None,
    expected_deployment_policy: DeploymentPolicy | None = None,
) -> list[str]:
    """Return every fail-closed violation in a sealed formal runtime identity."""

    from graphiti_core.prompts import extract_edges, extract_nodes
    from graphiti_core.prompts.extract_edges import ExtractedEdges

    if not isinstance(identity, Mapping):
        return ["runtime identity is not a mapping"]
    errors: list[str] = []
    graphiti = identity.get("graphiti")
    graphiti = graphiti if isinstance(graphiti, Mapping) else {}
    edge_model = identity.get("edge_response_model")
    edge_model = edge_model if isinstance(edge_model, Mapping) else {}
    patch_inventory = identity.get("patch_inventory")
    patch_inventory = patch_inventory if isinstance(patch_inventory, Mapping) else {}
    edge_schema = edge_model.get("schema")
    edge_schema = edge_schema if isinstance(edge_schema, Mapping) else {}
    edge_array = edge_schema.get("properties", {})
    edge_array = edge_array if isinstance(edge_array, Mapping) else {}
    edge_array = edge_array.get("edges", {})
    edge_array = edge_array if isinstance(edge_array, Mapping) else {}
    schema_text = json.dumps(
        edge_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    actual_schema = ExtractedEdges.model_json_schema()
    actual_schema_text = json.dumps(
        actual_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    installed_version = distribution_version("graphiti-core")

    if identity.get("schema_version") != "membind.formal-runtime-identity.v1":
        errors.append("runtime identity schema mismatch")
    if identity.get("status") != "PASS" or identity.get("strict_upstream_core") is not True:
        errors.append("runtime identity is not strict PASS")
    if expected_arm is not None and identity.get("arm") != expected_arm:
        errors.append("formal arm identity mismatch")
    if graphiti.get("version") != GRAPHITI_VERSION:
        errors.append("Graphiti declared version mismatch")
    if graphiti.get("installed_version") != installed_version or installed_version != GRAPHITI_VERSION:
        errors.append("Graphiti installed version mismatch")
    if graphiti.get("commit") != GRAPHITI_COMMIT:
        errors.append("Graphiti commit identity mismatch")
    if (
        f"{graphiti.get('class_module')}.{graphiti.get('class_qualname')}"
        != GRAPHITI_CLASS_IDENTITY
    ):
        errors.append("Graphiti class identity mismatch")
    if (
        graphiti.get("add_episode_module") != GRAPHITI_ADD_EPISODE_MODULE
        or graphiti.get("add_episode_qualname") != GRAPHITI_ADD_EPISODE_QUALNAME
    ):
        errors.append("graphiti.add_episode identity mismatch")
    if identity.get("llm_client_class") != OPENAI_GENERIC_CLIENT_IDENTITY:
        errors.append("OpenAIGenericClient identity mismatch")
    if (
        edge_model.get("module") != EXTRACTED_EDGES_MODULE
        or edge_model.get("qualname") != EXTRACTED_EDGES_QUALNAME
    ):
        errors.append("ExtractedEdges identity mismatch")
    expected_schema_sha256 = hashlib.sha256(actual_schema_text.encode("utf-8")).hexdigest()
    if (
        edge_schema != actual_schema
        or edge_model.get("schema_sha256") != expected_schema_sha256
        or hashlib.sha256(schema_text.encode("utf-8")).hexdigest()
        != expected_schema_sha256
    ):
        errors.append("ExtractedEdges schema mismatch")
    lowered_schema = schema_text.casefold()
    if edge_model.get("edges_has_max_items") is not False or "maxItems" in edge_array:
        errors.append("ExtractedEdges contains maxItems")
    if any(
        marker in lowered_schema
        for marker in ("pairs_completed", "finite-pair-task", "finite_pair_task")
    ):
        errors.append("finite-pair field present in edge schema")
    expected_prompt_hashes = {
        "extract_nodes": _source_sha256(extract_nodes.extract_message),
        "extract_edges": _source_sha256(extract_edges.edge),
    }
    if identity.get("upstream_prompt_source_sha256") != expected_prompt_hashes:
        errors.append("upstream prompt source identity mismatch")

    policy = expected_deployment_policy
    if policy is None:
        policy = DEPLOYMENT_POLICIES.get(str(identity.get("deployment_policy_id")))
    if policy is None:
        errors.append("deployment policy identity is unknown")
    else:
        if identity.get("deployment_policy_id") != policy.policy_id:
            errors.append("deployment policy identity mismatch")
        if identity.get("model") != policy.served_model:
            errors.append("served model identity mismatch")
        if identity.get("model_revision") != policy.revision:
            errors.append("model revision identity mismatch")
        if identity.get("sampling") != dict(policy.sampling):
            errors.append("sampling identity mismatch")
        if patch_inventory.get("deployment_policy_id") != policy.policy_id:
            errors.append("patch inventory deployment policy mismatch")
    if identity.get("max_tokens") != 16384:
        errors.append("formal max_tokens mismatch")
    if identity.get("structured_output_mode") != "json_schema":
        errors.append("structured output mode mismatch")
    if identity.get("sdk_retries") != 0:
        errors.append("SDK retry policy mismatch")
    if identity.get("logical_seed_policy") != (
        "uint32_sha256_dataset_context_source_chunk_prompt_messages"
    ):
        errors.append("logical seed policy mismatch")
    if expected_manifest_sha256 is not None and (
        identity.get("mab8192_manifest_sha256") != expected_manifest_sha256
    ):
        errors.append("MAB8192 manifest identity mismatch")
    if identity.get("extraction_chunking_installed") is not False:
        errors.append("extraction chunking is installed")
    if identity.get("finite_pair_tasks_enabled") is not False:
        errors.append("finite-pair tasks are enabled")
    if identity.get("response_repair_enabled") is not False:
        errors.append("response repair is enabled")
    expected_patch_fields = {
        "strict_upstream_core": True,
        "graphiti_algorithm_mutated": False,
        "shared_compatibility_substrate": False,
        "algorithm_patches": [],
        "prohibited_algorithm_patches": [],
    }
    if any(patch_inventory.get(key) != value for key, value in expected_patch_fields.items()):
        errors.append("formal patch inventory mismatch")
    supplied_hash = identity.get("runtime_identity_sha256")
    unhashed = {key: value for key, value in identity.items() if key != "runtime_identity_sha256"}
    if supplied_hash != request_hash(unhashed):
        errors.append("runtime identity checksum mismatch")
    return errors


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


def _source_sha256(value: Any) -> str:
    return hashlib.sha256(inspect.getsource(value).encode("utf-8")).hexdigest()


def formal_runtime_identity(
    runtime: Any,
    *,
    mab8192_manifest_sha256: str,
) -> dict[str, Any]:
    """Derive formal identity from the instantiated runtime and pinned source.

    This intentionally does not trust runner booleans.  Compatibility features
    are detected on the live client, and the edge schema is read from the exact
    upstream Pydantic model used by Graphiti 0.29.3.
    """

    from graphiti_core.prompts import extract_edges, extract_nodes
    from graphiti_core.prompts.extract_edges import ExtractedEdges

    if (
        not isinstance(mab8192_manifest_sha256, str)
        or len(mab8192_manifest_sha256) != 64
    ):
        raise LocalRuntimeConfigurationError("MAB8192 manifest identity is invalid")
    arm = getattr(runtime, "_membind_formal_arm", None)
    if arm not in FORMAL_ARM_NAMES:
        raise LocalRuntimeConfigurationError("runtime has no formal arm identity")
    llm_client = getattr(runtime, "llm_client", None)
    graphiti = getattr(runtime, "graphiti", None)
    config = getattr(runtime, "config", None)
    deployment = getattr(runtime, "_membind_deployment_policy", None)
    patch_inventory = getattr(runtime, "_membind_patch_inventory", None)
    if (
        llm_client is None
        or graphiti is None
        or config is None
        or not isinstance(deployment, DeploymentPolicy)
        or not isinstance(patch_inventory, Mapping)
    ):
        raise LocalRuntimeConfigurationError("formal runtime object graph is incomplete")

    edge_schema = ExtractedEdges.model_json_schema()
    schema_text = json.dumps(
        edge_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    lowered_schema = schema_text.casefold()
    extraction_chunking_installed = any(
        hasattr(llm_client, name)
        for name in (
            "_membind_extraction_diagnostics",
            "_membind_entity_partition_hints",
            "_membind_entity_partition_hints_by_scope",
            "_membind_shared_structured_output",
        )
    )
    finite_pair_tasks_enabled = bool(
        getattr(llm_client, "_membind_shared_bounded_structured_output", False)
    ) or any(
        marker in lowered_schema
        for marker in ("pairs_completed", "finite-pair-task", "finite_pair_task")
    )
    response_repair_enabled = any(
        bool(getattr(llm_client, name, False))
        for name in (
            "structured_output_recovery_enabled",
            "managed_recovery_enabled",
            "_membind_response_repair_enabled",
        )
    )
    edge_array = edge_schema.get("properties", {}).get("edges", {})
    graphiti_class_identity = f"{type(graphiti).__module__}.{type(graphiti).__qualname__}"
    add_episode = getattr(graphiti, "add_episode", None)
    add_episode_module = getattr(add_episode, "__module__", None)
    add_episode_qualname = getattr(add_episode, "__qualname__", None)
    llm_client_class = f"{type(llm_client).__module__}.{type(llm_client).__qualname__}"
    installed_graphiti_version = distribution_version("graphiti-core")
    strict_upstream_core = (
        patch_inventory.get("strict_upstream_core") is True
        and patch_inventory.get("shared_compatibility_substrate") is False
        and patch_inventory.get("algorithm_patches") == []
        and patch_inventory.get("prohibited_algorithm_patches") == []
        and graphiti_class_identity == GRAPHITI_CLASS_IDENTITY
        and add_episode_module == GRAPHITI_ADD_EPISODE_MODULE
        and add_episode_qualname == GRAPHITI_ADD_EPISODE_QUALNAME
        and llm_client_class == OPENAI_GENERIC_CLIENT_IDENTITY
        and installed_graphiti_version == GRAPHITI_VERSION
        and getattr(runtime, "_membind_graphiti_commit", None) == GRAPHITI_COMMIT
        and not extraction_chunking_installed
        and not finite_pair_tasks_enabled
        and not response_repair_enabled
        and isinstance(edge_array, Mapping)
        and "maxItems" not in edge_array
    )
    identity: dict[str, Any] = {
        "schema_version": "membind.formal-runtime-identity.v1",
        "status": "PASS" if strict_upstream_core else "FAIL",
        "arm": arm,
        "strict_upstream_core": strict_upstream_core,
        "graphiti": {
            "version": getattr(runtime, "_membind_graphiti_version", None),
            "installed_version": installed_graphiti_version,
            "commit": getattr(runtime, "_membind_graphiti_commit", None),
            "class_module": type(graphiti).__module__,
            "class_qualname": type(graphiti).__qualname__,
            "add_episode_module": add_episode_module,
            "add_episode_qualname": add_episode_qualname,
        },
        "llm_client_class": llm_client_class,
        "edge_response_model": {
            "module": ExtractedEdges.__module__,
            "qualname": ExtractedEdges.__qualname__,
            "schema_sha256": hashlib.sha256(schema_text.encode("utf-8")).hexdigest(),
            "schema": edge_schema,
            "edges_has_max_items": "maxItems" in edge_array,
        },
        "upstream_prompt_source_sha256": {
            "extract_nodes": _source_sha256(extract_nodes.extract_message),
            "extract_edges": _source_sha256(extract_edges.edge),
        },
        "deployment_policy_id": deployment.policy_id,
        "model": getattr(config, "construction_model", None),
        "model_revision": getattr(config, "construction_model_revision", None),
        "sampling": dict(deployment.sampling),
        "max_tokens": getattr(config, "requested_max_tokens", None),
        "structured_output_mode": getattr(config, "structured_output_mode", None),
        "logical_seed_policy": (
            "uint32_sha256_dataset_context_source_chunk_prompt_messages"
        ),
        "sdk_retries": LOCAL_SDK_MAX_RETRIES,
        "mab8192_manifest_sha256": mab8192_manifest_sha256,
        "extraction_chunking_installed": extraction_chunking_installed,
        "finite_pair_tasks_enabled": finite_pair_tasks_enabled,
        "response_repair_enabled": response_repair_enabled,
        "patch_inventory": dict(patch_inventory),
    }
    identity["runtime_identity_sha256"] = request_hash(identity)
    errors = strict_formal_runtime_identity_errors(
        identity,
        expected_arm=arm,
        expected_manifest_sha256=mab8192_manifest_sha256,
        expected_deployment_policy=deployment,
    )
    if errors:
        raise LocalRuntimeConfigurationError(
            "formal runtime identity is invalid: " + "; ".join(errors)
        )
    return identity


def formal_builder_source_audit() -> dict[str, Any]:
    """Revalidate that the executable formal builder has no old algorithm seam."""

    source = inspect.getsource(build_formal_upstream_runtime)
    prohibited = (
        "install_local_extraction_chunking_policy",
        "partition_extraction_by_turns",
        "partition_edge_candidates",
        "edge_duplicate_recovery",
        "edge_endpoint_schema_grounding",
    )
    observed = [marker for marker in prohibited if marker in source]
    result = {
        "schema_version": "membind.formal-builder-source-audit.v1",
        "status": "PASS" if not observed else "FAIL",
        "builder_module": build_formal_upstream_runtime.__module__,
        "builder_qualname": build_formal_upstream_runtime.__qualname__,
        "builder_source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "prohibited_markers": list(prohibited),
        "observed_prohibited_markers": observed,
    }
    if observed:
        raise LocalRuntimeConfigurationError(
            "formal builder references a prohibited compatibility algorithm"
        )
    return result


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
    runtime._membind_shared_bounded_structured_output = False
    runtime._membind_shared_structured_output_identity = None
    runtime._membind_patch_inventory = {
        "strict_upstream_core": True,
        "graphiti_algorithm_mutated": False,
        "shared_compatibility_substrate": False,
        "algorithm_patches": [],
        "transport_adapters": [
            "single_attempt_policy",
            "logical_request_seed_and_telemetry",
            "endpoint_routing",
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
    "GRAPHITI_CLASS_IDENTITY",
    "GRAPHITI_ADD_EPISODE_MODULE",
    "GRAPHITI_ADD_EPISODE_QUALNAME",
    "OPENAI_GENERIC_CLIENT_IDENTITY",
    "EXTRACTED_EDGES_MODULE",
    "EXTRACTED_EDGES_QUALNAME",
    "DeploymentPolicy",
    "P0_DEPLOYMENT_POLICY_ID",
    "P1_DEPLOYMENT_POLICY_ID",
    "P2_DEPLOYMENT_POLICY_ID",
    "P0_DEPLOYMENT_POLICY",
    "P1_DEPLOYMENT_POLICY",
    "P2_DEPLOYMENT_POLICY",
    "P0_MODEL",
    "P0_SAMPLING",
    "P1_MODEL",
    "P1_SAMPLING",
    "P2_MODEL",
    "P2_SAMPLING",
    "build_formal_upstream_runtime",
    "close_formal_upstream_runtime",
    "current_logical_request_identity",
    "logical_request_context",
    "logical_request_seed",
    "install_logical_llm_context",
    "request_hash",
    "deployment_wire_fields",
    "formal_runtime_identity",
    "strict_formal_runtime_identity_errors",
    "formal_builder_source_audit",
    "resolve_deployment_policy",
]
