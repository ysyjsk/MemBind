"""Versioned, content-safe identity for the v3.1 provider execution envelope.

This module is deliberately offline.  It does not probe vLLM, read a startup
log, load ``.env`` files, or retain prompts/responses.  An operator (or a
read-only preflight) supplies an observed identity to :func:`build...`; the
builder accepts it only when it is exactly the currently frozen serving
configuration.  The sealed result can then be bound to a live run by its
``payload_sha256``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256


PROVIDER_EXECUTION_ENVELOPE_SCHEMA = (
    "membind.paper-eval-v3.provider-execution-envelope.v2"
)
EMBEDDING_DEPLOYMENT_FINGERPRINT = (
    "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
)


class ProviderExecutionEnvelopeError(ValueError):
    """An observed provider identity or sealed artifact is unsafe."""


def _fail(code: str) -> ProviderExecutionEnvelopeError:
    return ProviderExecutionEnvelopeError(code)


# These values are the operator-confirmed 2026-08-18 serving envelope.  Keep
# them in one immutable projection so a partial/missing observation cannot be
# silently accepted as a different experiment.
_EXPECTED_CONSTRUCTION: dict[str, object] = {
    "base_url": "http://10.87.5.247:8000/v1",
    "vllm_version": "0.26.0",
    "model": "qwen3-32b-fp8",
    "max_model_len": 65536,
    "yarn": {
        "factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
    },
    "structured_backend": "xgrammar",
    "structured_output_mode": "json_schema",
    "scheduler": "fcfs",
    "prefix_caching": True,
    "chunked_prefill": True,
    "flashinfer_sampler": False,
    "gpu_memory_utilization": 0.75,
    "kv_cache_tokens": 127280,
    "requested_max_tokens": 16384,
    "chat_template_kwargs": {"enable_thinking": False},
    "generation_config": {
        "policy": "per_field_request_or_model_default",
        "request": {
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 20260806,
            "max_tokens": 16384,
            "enable_thinking": False,
        },
        # Startup logs expose these model defaults.  They are retained only
        # as a safe summary; request-time values above remain authoritative.
        "model_defaults": {"temperature": 0.6, "top_k": 20, "top_p": 0.95},
        "model_defaults_source": "server-startup-log-summary",
        "effective_sources": {
            "enable_thinking": "explicit_request",
            "max_tokens": "explicit_request",
            "seed": "explicit_request",
            "temperature": "explicit_request",
            "top_k": "inherited_model_default",
            "top_p": "explicit_request",
        },
    },
}

_EXPECTED_EMBEDDING: dict[str, object] = {
    "base_url": "http://10.87.5.247:8001/v1",
    "model": "qwen3-embedding-0.6b",
    "runner": "pooling",
    "dtype": "bfloat16",
    "max_model_len": 32768,
    "gpu_memory_utilization": 0.15,
    "max_num_batched_tokens": 32768,
    "max_num_seqs": 128,
    "dimension": 1024,
    "pooling": "last_token",
    "normalization": True,
    "deployment_fingerprint": EMBEDDING_DEPLOYMENT_FINGERPRINT,
}

_PRIVATE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "prompt",
    "raw_log",
    "raw_prompt",
    "raw_response",
    "response",
    "secret",
    "token",
}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "status",
    "provider",
    "construction",
    "embedding",
    "startup_evidence",
    "payload_sha256",
}


def _content_safe(value: object) -> None:
    """Reject credentials and prompt/log bodies recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_KEYS:
                raise _fail("content_safe_violation")
            _content_safe(child)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _content_safe(child)
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise _fail("content_safe_violation")


def _section(value: object, expected: Mapping[str, object], code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail("provider_identity_missing")
    observed = deepcopy(dict(value))
    expected_keys = set(expected)
    if set(observed) != expected_keys:
        missing = expected_keys.difference(observed)
        raise _fail("provider_identity_missing" if missing else code)
    if observed != dict(expected):
        raise _fail(code)
    return observed


def _body(*, construction: Mapping[str, object], embedding: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": PROVIDER_EXECUTION_ENVELOPE_SCHEMA,
        "status": "PASS",
        "provider": {
            "kind": "vllm",
            "protocol": "openai-compatible-chat-completions",
        },
        "construction": deepcopy(dict(construction)),
        "embedding": deepcopy(dict(embedding)),
    }


def build_provider_execution_envelope(
    *,
    construction: Mapping[str, object] | None = None,
    embedding: Mapping[str, object] | None = None,
    startup_evidence: Mapping[str, object] | None = None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a sealed envelope after exact identity validation.

    The serving projection is pinned locally, but startup-log evidence is
    mandatory.  Observed mappings must contain every field and match it
    exactly, preventing a missing backend, scheduler, GPU setting, or
    embedding fingerprint from becoming an implicit default.
    """

    if startup_evidence is None:
        raise _fail("startup_evidence_missing")
    if not isinstance(startup_evidence, Mapping):
        raise _fail("startup_evidence_invalid")
    evidence = deepcopy(dict(startup_evidence))
    expected_evidence_keys = {
        "observation_transport",
        "construction_startup_log_sha256",
        "embedding_startup_log_sha256",
    }
    if set(evidence) != expected_evidence_keys:
        raise _fail("startup_evidence_invalid")
    if evidence.get("observation_transport") != "restricted-ssh-read":
        raise _fail("startup_evidence_invalid")
    for key in ("construction_startup_log_sha256", "embedding_startup_log_sha256"):
        value = evidence.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise _fail("startup_evidence_invalid")

    selected_construction = (
        deepcopy(_EXPECTED_CONSTRUCTION)
        if construction is None
        else _section(construction, _EXPECTED_CONSTRUCTION, "provider_identity_mismatch")
    )
    selected_embedding = (
        deepcopy(_EXPECTED_EMBEDDING)
        if embedding is None
        else _section(embedding, _EXPECTED_EMBEDDING, "provider_identity_mismatch")
    )
    if extra is not None:
        if not isinstance(extra, Mapping):
            raise _fail("content_safe_violation")
        _content_safe(extra)
        # Extensions are intentionally not accepted: identity fields must be
        # reviewed and versioned rather than smuggled into a frozen artifact.
        raise _fail("provider_identity_unknown_field")
    evidence["construction_identity_projection_sha256"] = payload_sha256(
        selected_construction
    )
    evidence["embedding_identity_projection_sha256"] = payload_sha256(
        selected_embedding
    )
    artifact = _body(construction=selected_construction, embedding=selected_embedding)
    artifact["startup_evidence"] = evidence
    _content_safe(artifact)
    artifact["payload_sha256"] = payload_sha256(artifact)
    return artifact


def verify_provider_execution_envelope(value: Mapping[str, object]) -> dict[str, object]:
    """Verify schema, public-content policy, identity, and seal fail-closed."""

    if not isinstance(value, Mapping):
        raise _fail("artifact_invalid")
    artifact = deepcopy(dict(value))
    # Permit a missing seal long enough to classify an altered identity as
    # provider drift; unknown fields remain rejected immediately.
    if set(artifact).difference({"payload_sha256"}) != _TOP_LEVEL_KEYS.difference(
        {"payload_sha256"}
    ):
        raise _fail("artifact_inventory_invalid")
    _content_safe(artifact)
    if artifact.get("schema_version") != PROVIDER_EXECUTION_ENVELOPE_SCHEMA:
        raise _fail("artifact_schema_invalid")
    if artifact.get("status") != "PASS":
        raise _fail("artifact_status_invalid")
    provider = artifact.get("provider")
    if provider != {"kind": "vllm", "protocol": "openai-compatible-chat-completions"}:
        raise _fail("provider_identity_mismatch")
    stored = artifact.get("payload_sha256")
    if stored is not None:
        if not isinstance(stored, str) or len(stored) != 64 or any(
            character not in "0123456789abcdef" for character in stored
        ):
            raise _fail("artifact_hash_invalid")
        body = {key: child for key, child in artifact.items() if key != "payload_sha256"}
        if stored != payload_sha256(body):
            raise _fail("artifact_hash_mismatch")
    _section(artifact.get("construction"), _EXPECTED_CONSTRUCTION, "provider_identity_mismatch")
    _section(artifact.get("embedding"), _EXPECTED_EMBEDDING, "provider_identity_mismatch")
    evidence = artifact.get("startup_evidence")
    if not isinstance(evidence, Mapping):
        raise _fail("startup_evidence_invalid")
    expected_evidence_keys = {
        "observation_transport",
        "construction_startup_log_sha256",
        "embedding_startup_log_sha256",
        "construction_identity_projection_sha256",
        "embedding_identity_projection_sha256",
    }
    if set(evidence) != expected_evidence_keys:
        raise _fail("startup_evidence_invalid")
    if evidence.get("observation_transport") != "restricted-ssh-read":
        raise _fail("startup_evidence_invalid")
    for key in (
        "construction_startup_log_sha256",
        "embedding_startup_log_sha256",
    ):
        value = evidence.get(key)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise _fail("startup_evidence_invalid")
    if evidence.get("construction_identity_projection_sha256") != payload_sha256(
        _EXPECTED_CONSTRUCTION
    ) or evidence.get("embedding_identity_projection_sha256") != payload_sha256(
        _EXPECTED_EMBEDDING
    ):
        raise _fail("startup_evidence_invalid")
    if stored is None:
        raise _fail("artifact_hash_invalid")
    return artifact


def write_provider_execution_envelope(path: Path, artifact: Mapping[str, object]) -> dict[str, object]:
    """Verify and atomically persist one public envelope."""

    verified = verify_provider_execution_envelope(artifact)
    atomic_write_json(Path(path), verified)
    return verified


def read_provider_execution_envelope(path: Path) -> dict[str, object]:
    """Read and verify a previously persisted envelope."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail("artifact_unreadable") from None
    if not isinstance(raw, Mapping):
        raise _fail("artifact_invalid")
    return verify_provider_execution_envelope(raw)


__all__ = [
    "EMBEDDING_DEPLOYMENT_FINGERPRINT",
    "PROVIDER_EXECUTION_ENVELOPE_SCHEMA",
    "ProviderExecutionEnvelopeError",
    "build_provider_execution_envelope",
    "read_provider_execution_envelope",
    "verify_provider_execution_envelope",
    "write_provider_execution_envelope",
]
