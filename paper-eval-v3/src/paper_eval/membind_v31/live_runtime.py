"""Pinned shared Graphiti runtime with the v3.1 request-level gate installed."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.admission import RequestAdmission
from paper_eval.membind_v1.live_runtime import (
    RuntimeComponents,
    build_membind_v1_runtime,
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


class MemBindV31LiveRuntimeError(ValueError):
    """The shared envelope or v3.1 admission installation failed."""


def _fail(code: str) -> MemBindV31LiveRuntimeError:
    return MemBindV31LiveRuntimeError(code)


def _trace_hmac_key(env: Mapping[str, str]) -> bytes:
    value = env.get("MEMBIND_V31_TRACE_HMAC_KEY")
    if not isinstance(value, str) or not value:
        raise _fail("trace_hmac_key_missing")
    try:
        selected = bytes.fromhex(value)
    except ValueError:
        raise _fail("trace_hmac_key_invalid") from None
    if len(selected) != 32 or value.casefold() != value or len(value) != 64:
        raise _fail("trace_hmac_key_invalid")
    return selected


@dataclass(slots=True)
class MemBindV31LiveRuntime:
    graphiti: Any
    raw_llm: Any
    admitted_llm: AdmittedLLMClientV31
    shared_public_identity: dict[str, object]
    shared_execution_envelope_sha256: str
    method_public_identity: dict[str, object]
    method_execution_identity_sha256: str
    transport_admission_installed: bool


def build_membind_v31_runtime(
    *,
    env: Mapping[str, str],
    policy: AdmissionPolicy,
    request_id_prefix: str,
    observer: Callable[[dict[str, object]], object] | None = None,
    admission_observer: Callable[[dict[str, object]], object] | None = None,
    response_observer: Callable[[dict[str, object]], object] | None = None,
    components: RuntimeComponents | None = None,
    prefix_encoder: Callable[..., object] | None = None,
) -> MemBindV31LiveRuntime:
    """Reuse the exact baseline envelope, replacing only its request policy."""

    if not isinstance(policy, AdmissionPolicy):
        raise _fail("admission_policy_invalid")
    try:
        shared = build_membind_v1_runtime(
            env=env,
            admission=RequestAdmission(limit=2),
            request_id_prefix=f"{request_id_prefix}:bootstrap",
            components=components,
        )
    except ValueError as error:
        raise _fail(f"shared_runtime_invalid:{error}") from None
    graphiti = shared.graphiti
    clients = getattr(graphiti, "clients", None)
    if clients is None:
        raise _fail("graphiti_clients_missing")
    selected_prefix_encoder = prefix_encoder
    if selected_prefix_encoder is None:
        cache_salt = env.get("CONSTRUCTION_CACHE_SALT")
        if not isinstance(cache_salt, str) or not 1 <= len(cache_salt) <= 64:
            raise _fail("construction_cache_salt_missing")
        trace_hmac_key = _trace_hmac_key(env)
        cache_identity = payload_sha256(
            {
                "schema_version": "membind.paper-eval-v3.cache-identity.v1",
                "base_url": shared.public_identity["construction"]["base_url"],
                "served_model_id": shared.public_identity["construction"][
                    "served_model_id"
                ],
                "tokenizer_revision": TOKENIZER_REVISION,
                "chat_template_kwargs": {"enable_thinking": False},
                "prefix_match_unit": DEFAULT_PREFIX_MATCH_UNIT,
                "cache_salt_identity_sha256": hashlib.sha256(
                    cache_salt.encode("utf-8")
                ).hexdigest(),
            }
        )
        selected_prefix_encoder = build_production_qwen_prefix_encoder(
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
        prefix_encoder=selected_prefix_encoder,
    )
    transport = getattr(shared.raw_llm, "client", None)
    chat = getattr(transport, "chat", None)
    completions = getattr(chat, "completions", None)
    if completions is None or not callable(getattr(completions, "create", None)):
        raise _fail("v31_transport_boundary_missing")
    try:
        chat.completions = AdmittedChatCompletionsV31(
            inner=completions,
            admission=admitted,
            response_observer=response_observer,
            structured_backend_identity="xgrammar",
        )
        graphiti.llm_client = shared.raw_llm
        clients.llm_client = shared.raw_llm
    except Exception:
        raise _fail("v31_admission_installation_failed") from None
    if (
        graphiti.llm_client is not shared.raw_llm
        or clients.llm_client is not shared.raw_llm
        or not isinstance(chat.completions, AdmittedChatCompletionsV31)
    ):
        raise _fail("v31_admission_installation_failed")
    method_identity = {
        "schema_version": "membind.paper-eval-v3.membind-v31-live-runtime.v1",
        "shared_execution_envelope_sha256": shared.execution_envelope_sha256,
        "request_admission": {
            "global_llm_admission_k": 2,
            "policy": policy.value,
            "semantics": "non-preemptive-frontier-first",
            "unscoped_request_policy": "fail-closed",
            "prefix_encoder": getattr(selected_prefix_encoder, "public_identity", None),
            "boundary": "openai_chat_completions_create",
            "transport_admission_installed": True,
        },
    }
    return MemBindV31LiveRuntime(
        graphiti=graphiti,
        raw_llm=shared.raw_llm,
        admitted_llm=admitted,
        shared_public_identity=shared.public_identity,
        shared_execution_envelope_sha256=shared.execution_envelope_sha256,
        method_public_identity=method_identity,
        method_execution_identity_sha256=payload_sha256(method_identity),
        transport_admission_installed=True,
    )


__all__ = [
    "MemBindV31LiveRuntime",
    "MemBindV31LiveRuntimeError",
    "build_membind_v31_runtime",
]
