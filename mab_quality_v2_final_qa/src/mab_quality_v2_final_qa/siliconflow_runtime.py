"""Isolated Graphiti runtime for the user-authorized SiliconFlow MAB lane.

The historical MemBind runtime factories deliberately pin the original vLLM
deployment URLs and model aliases.  This compatibility runtime leaves those
files untouched while composing the same Graphiti/Qwen/admission surfaces with
the explicitly versioned SiliconFlow provider identity used only by this lane.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import canonical_sha256
from .live_adapters import SiliconFlowOpenAITransport
from .runtime_gate import RuntimeTopology, SILICONFLOW_PROVIDER


REQUESTED_MAX_TOKENS = 16_384
# SiliconFlow's hosted Qwen-32B can spend several minutes on a large
# structured extraction request.  Keep the request single-attempt, but allow
# the provider enough time to finish instead of turning slow generation into
# a false construction failure.
SILICONFLOW_HTTP_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    graphiti_type: Any
    llm_config_type: Any
    qwen_client_type: Any
    embedder_config_type: Any
    embedder_type: Any
    reranker_type: Any
    admitted_client_type: Any
    request_admission_type: Any
    openai_client_factory: Any


@dataclass(slots=True)
class SiliconFlowRuntime:
    graphiti: Any
    raw_llm: Any
    admitted_llm: Any
    public_identity: dict[str, object]
    execution_envelope_sha256: str
    shared_public_identity: dict[str, object]
    shared_execution_envelope_sha256: str
    method_public_identity: dict[str, object]
    method_execution_identity_sha256: str
    transport_admission_installed: bool


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}_MISSING")
    return value


def _production_components() -> RuntimeComponents:
    import httpx
    from openai import AsyncOpenAI
    from graphiti_core import Graphiti
    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.config import LLMConfig
    from graphiti_native import QwenVLLMClient
    from paper_eval.membind_v1.admission import AdmittedLLMClient
    from paper_eval.membind_v1.admission import RequestAdmission

    def openai_client_factory(*, api_key: str, base_url: str) -> Any:
        timeout = httpx.Timeout(
            connect=10.0,
            read=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
            write=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
            pool=SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        )
        http_client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
        client._platform = "Linux"  # type: ignore[attr-defined]
        return client

    return RuntimeComponents(
        graphiti_type=Graphiti,
        llm_config_type=LLMConfig,
        qwen_client_type=QwenVLLMClient,
        embedder_config_type=OpenAIEmbedderConfig,
        embedder_type=OpenAIEmbedder,
        reranker_type=OpenAIRerankerClient,
        admitted_client_type=AdmittedLLMClient,
        request_admission_type=RequestAdmission,
        openai_client_factory=openai_client_factory,
    )


def _public_identity(topology: RuntimeTopology) -> dict[str, object]:
    if topology.provider != SILICONFLOW_PROVIDER:
        raise ValueError("SILICONFLOW_RUNTIME_PROVIDER_MISMATCH")
    return {
        "schema_version": "mab-quality-v2-final-qa.siliconflow-runtime.v1",
        "provider": topology.provider,
        "construction": {
            "base_url": topology.construction.base_url,
            "served_model_id": topology.construction.model,
            "requested_max_tokens": REQUESTED_MAX_TOKENS,
            "structured_output_mode": "json_schema",
            "enable_thinking": False,
        },
        "quality": {
            "base_url": topology.quality.base_url,
            "served_model_id": topology.quality.model,
            "enable_thinking": False,
        },
        "embedding": {
            "base_url": topology.embedding.base_url,
            "served_model_id": topology.embedding.model,
            "dimension": topology.embedding_dimension,
        },
        "neo4j": {"uri": topology.neo4j_uri},
        "graphiti_max_coroutines": 8,
        "global_llm_admission_k": 2,
        "provider_cache_salt_sent": False,
        "http_timeout_seconds": SILICONFLOW_HTTP_TIMEOUT_SECONDS,
    }


def _build_shared(
    *,
    env: Mapping[str, str],
    request_id_prefix: str,
    components: RuntimeComponents | None,
) -> SiliconFlowRuntime:
    topology = RuntimeTopology.from_env(env)
    public_identity = _public_identity(topology)
    construction_key = _required(env, "CONSTRUCTION_LLM_API_KEY")
    embedding_key = _required(env, "EMBEDDING_API_KEY")
    neo4j_user = _required(env, "NEO4J_USER")
    neo4j_password = _required(env, "NEO4J_PASSWORD")
    try:
        max_coroutines = int(env.get("GRAPHITI_MAX_COROUTINES", "8"))
    except ValueError:
        raise ValueError("GRAPHITI_MAX_COROUTINES_INVALID") from None
    if max_coroutines != 8:
        raise ValueError("GRAPHITI_MAX_COROUTINES_DRIFT")

    selected = components or _production_components()
    llm_config = selected.llm_config_type(
        api_key=construction_key,
        model=topology.construction.model,
        small_model=topology.construction.model,
        base_url=topology.construction.base_url,
        temperature=0.0,
        max_tokens=REQUESTED_MAX_TOKENS,
    )
    raw_llm = selected.qwen_client_type(
        config=llm_config,
        max_tokens=REQUESTED_MAX_TOKENS,
        structured_output_mode="json_schema",
        vllm_options_enabled=False,
        client=selected.openai_client_factory(
            api_key=construction_key, base_url=topology.construction.base_url
        ),
    )
    raw_transport = getattr(raw_llm, "client", None)
    if raw_transport is None:
        raise ValueError("SILICONFLOW_CONSTRUCTION_TRANSPORT_UNAVAILABLE")
    raw_llm.client = SiliconFlowOpenAITransport(raw_transport)

    embedder_config = selected.embedder_config_type(
        api_key=embedding_key,
        base_url=topology.embedding.base_url,
        embedding_model=topology.embedding.model,
        embedding_dim=topology.embedding_dimension,
    )
    embedder = selected.embedder_type(
        embedder_config,
        client=selected.openai_client_factory(
            api_key=embedding_key, base_url=topology.embedding.base_url
        ),
    )
    reranker = selected.reranker_type(llm_config, client=raw_llm.client)
    graphiti = selected.graphiti_type(
        uri=topology.neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=raw_llm,
        embedder=embedder,
        cross_encoder=reranker,
        max_coroutines=max_coroutines,
    )
    admitted = selected.admitted_client_type(
        inner=raw_llm,
        admission=selected.request_admission_type(limit=2),
        request_id_prefix=request_id_prefix,
    )
    graphiti.llm_client = admitted
    graphiti.clients.llm_client = admitted
    identity_hash = canonical_sha256(public_identity)
    method_identity = {
        "schema_version": "mab-quality-v2-final-qa.siliconflow-u0-runtime.v1",
        "method": "U0",
        "shared_execution_envelope_sha256": identity_hash,
        "request_admission": {"policy": "FIFO", "limit": 2},
    }
    return SiliconFlowRuntime(
        graphiti=graphiti,
        raw_llm=raw_llm,
        admitted_llm=admitted,
        public_identity=public_identity,
        execution_envelope_sha256=identity_hash,
        shared_public_identity=public_identity,
        shared_execution_envelope_sha256=identity_hash,
        method_public_identity=method_identity,
        method_execution_identity_sha256=canonical_sha256(method_identity),
        transport_admission_installed=False,
    )


def build_siliconflow_u0_runtime(
    *,
    env: Mapping[str, str],
    request_id_prefix: str,
    components: RuntimeComponents | None = None,
) -> SiliconFlowRuntime:
    """Build the U0 Graphiti runtime against exact SiliconFlow model IDs."""

    return _build_shared(
        env=env, request_id_prefix=request_id_prefix, components=components
    )


def build_siliconflow_v31_runtime(
    *,
    env: Mapping[str, str],
    policy: Any,
    request_id_prefix: str,
    observer: Any | None = None,
    admission_observer: Any | None = None,
    response_observer: Any | None = None,
    components: RuntimeComponents | None = None,
    prefix_encoder: Any | None = None,
) -> SiliconFlowRuntime:
    """Install the frozen v3.1 CACHE_AFFINE controller on SiliconFlow transport."""

    shared = _build_shared(
        env=env,
        request_id_prefix=f"{request_id_prefix}:bootstrap",
        components=components,
    )
    from paper_eval.membind_v31.admission import AdmissionPolicy
    from paper_eval.membind_v31.prefix_affinity import (
        DEFAULT_PREFIX_MATCH_UNIT,
        TOKENIZER_REVISION,
        build_production_qwen_prefix_encoder,
    )
    from paper_eval.membind_v31.request_runtime import (
        AdmittedChatCompletionsV31,
        AdmittedLLMClientV31,
    )

    if not isinstance(policy, AdmissionPolicy):
        raise ValueError("SILICONFLOW_V31_ADMISSION_POLICY_INVALID")
    cache_salt = _required(env, "CONSTRUCTION_CACHE_SALT")
    if not 1 <= len(cache_salt) <= 64:
        raise ValueError("SILICONFLOW_CACHE_IDENTITY_INVALID")
    trace_value = _required(env, "MEMBIND_V31_TRACE_HMAC_KEY")
    try:
        trace_hmac_key = bytes.fromhex(trace_value)
    except ValueError:
        raise ValueError("SILICONFLOW_TRACE_KEY_INVALID") from None
    if len(trace_hmac_key) != 32 or trace_value.casefold() != trace_value:
        raise ValueError("SILICONFLOW_TRACE_KEY_INVALID")
    topology = RuntimeTopology.from_env(env)
    cache_identity = canonical_sha256(
        {
            "schema_version": "mab-quality-v2-final-qa.siliconflow-cache-identity.v1",
            "provider": topology.provider,
            "base_url": topology.construction.base_url,
            "served_model_id": topology.construction.model,
            "tokenizer_revision": TOKENIZER_REVISION,
            "chat_template_kwargs": {"enable_thinking": False},
            "prefix_match_unit": DEFAULT_PREFIX_MATCH_UNIT,
            "cache_salt_identity_sha256": hashlib.sha256(
                cache_salt.encode("utf-8")
            ).hexdigest(),
            "provider_cache_salt_sent": False,
            "http_timeout_seconds": SILICONFLOW_HTTP_TIMEOUT_SECONDS,
        }
    )
    selected_encoder = prefix_encoder or build_production_qwen_prefix_encoder(
        inner=shared.raw_llm,
        trace_hmac_key=trace_hmac_key,
        cache_identity_sha256=cache_identity,
    )
    admitted = AdmittedLLMClientV31(
        inner=shared.raw_llm,
        limit=2,
        policy=policy,
        request_id_prefix=request_id_prefix,
        observer=observer,
        admission_observer=admission_observer,
        prefix_encoder=selected_encoder,
    )
    transport = getattr(shared.raw_llm, "client", None)
    completions = getattr(getattr(transport, "chat", None), "completions", None)
    if completions is None or not callable(getattr(completions, "create", None)):
        raise ValueError("SILICONFLOW_V31_TRANSPORT_BOUNDARY_MISSING")
    transport.chat.completions = AdmittedChatCompletionsV31(
        inner=completions,
        admission=admitted,
        response_observer=response_observer,
        structured_backend_identity="siliconflow-json-schema",
    )
    shared.graphiti.llm_client = shared.raw_llm
    shared.graphiti.clients.llm_client = shared.raw_llm
    method_identity = {
        "schema_version": "mab-quality-v2-final-qa.siliconflow-v31-runtime.v1",
        "method": "MEMBIND_V31",
        "shared_execution_envelope_sha256": shared.execution_envelope_sha256,
        "request_admission": {
            "policy": policy.value,
            "limit": 2,
            "semantics": "non-preemptive-frontier-first",
            "boundary": "openai_chat_completions_create",
            "transport_admission_installed": True,
            "prefix_encoder": getattr(selected_encoder, "public_identity", None),
        },
        "cache_identity_sha256": cache_identity,
        "provider_cache_salt_sent": False,
    }
    shared.admitted_llm = admitted
    shared.method_public_identity = method_identity
    shared.method_execution_identity_sha256 = canonical_sha256(method_identity)
    shared.transport_admission_installed = True
    return shared


__all__ = [
    "RuntimeComponents",
    "SiliconFlowRuntime",
    "build_siliconflow_u0_runtime",
    "build_siliconflow_v31_runtime",
]
