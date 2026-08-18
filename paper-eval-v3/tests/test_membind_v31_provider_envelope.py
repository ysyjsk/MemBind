"""TDD contract tests for the isolated provider execution envelope."""

from __future__ import annotations

from copy import deepcopy

import pytest

from paper_eval.membind_v31.provider_envelope import (
    PROVIDER_EXECUTION_ENVELOPE_SCHEMA,
    ProviderExecutionEnvelopeError,
    build_provider_execution_envelope,
    verify_provider_execution_envelope,
)


def _startup_evidence() -> dict[str, object]:
    return {
        "observation_transport": "restricted-ssh-read",
        "construction_startup_log_sha256": "a" * 64,
        "embedding_startup_log_sha256": "b" * 64,
    }


def test_builds_sealed_public_xgrammar_envelope() -> None:
    artifact = build_provider_execution_envelope(startup_evidence=_startup_evidence())

    assert artifact["schema_version"] == PROVIDER_EXECUTION_ENVELOPE_SCHEMA
    assert artifact["status"] == "PASS"
    assert artifact["construction"]["vllm_version"] == "0.26.0"
    assert artifact["construction"]["structured_backend"] == "xgrammar"
    assert artifact["construction"]["max_model_len"] == 65536
    assert artifact["construction"]["yarn"] == {
        "factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
    }
    assert artifact["embedding"]["deployment_fingerprint"] == (
        "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
    )
    generation = artifact["construction"]["generation_config"]
    assert generation["model_defaults"] == {
        "temperature": 0.6,
        "top_k": 20,
        "top_p": 0.95,
    }
    assert generation["effective_sources"] == {
        "enable_thinking": "explicit_request",
        "max_tokens": "explicit_request",
        "seed": "explicit_request",
        "temperature": "explicit_request",
        "top_k": "inherited_model_default",
        "top_p": "explicit_request",
    }
    assert artifact["startup_evidence"]["construction_identity_projection_sha256"]
    assert artifact["payload_sha256"]
    assert verify_provider_execution_envelope(artifact) == artifact


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("construction", "structured_backend"), "auto"),
        (("construction", "gpu_memory_utilization"), 0.5),
        (("construction", "scheduler"), "priority"),
        (("construction", "max_model_len"), 40960),
        (("construction", "generation_config", "model_defaults", "top_k"), 40),
        (("embedding", "dtype"), "float16"),
        (("embedding", "max_model_len"), 2048),
        (("embedding", "deployment_fingerprint"), "0" * 64),
    ],
)
def test_verify_rejects_provider_drift(path: tuple[str, ...], value: object) -> None:
    artifact = deepcopy(
        build_provider_execution_envelope(startup_evidence=_startup_evidence())
    )
    target = artifact
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    # Re-sealing an altered body must not make an unapproved provider identity valid.
    artifact.pop("payload_sha256")
    with pytest.raises(ProviderExecutionEnvelopeError, match="provider_identity_mismatch"):
        verify_provider_execution_envelope(artifact)


def test_verify_rejects_tamper_even_when_identity_is_unchanged() -> None:
    artifact = build_provider_execution_envelope(startup_evidence=_startup_evidence())
    artifact["construction"]["scheduler"] = "priority"
    with pytest.raises(ProviderExecutionEnvelopeError, match="artifact_hash_mismatch"):
        verify_provider_execution_envelope(artifact)


@pytest.mark.parametrize("key", ["api_key", "authorization", "raw_prompt", "raw_log", "secret"])
def test_build_rejects_sensitive_extension_fields(key: str) -> None:
    with pytest.raises(ProviderExecutionEnvelopeError, match="content_safe_violation"):
        build_provider_execution_envelope(
            startup_evidence=_startup_evidence(), extra={key: "private"}
        )


def test_build_rejects_missing_or_incomplete_observed_identity() -> None:
    with pytest.raises(ProviderExecutionEnvelopeError, match="provider_identity_missing"):
        build_provider_execution_envelope(
            construction={"vllm_version": "0.26.0"},
            startup_evidence=_startup_evidence(),
        )


def test_build_requires_real_startup_evidence() -> None:
    with pytest.raises(ProviderExecutionEnvelopeError, match="startup_evidence_missing"):
        build_provider_execution_envelope()
    with pytest.raises(ProviderExecutionEnvelopeError, match="startup_evidence_invalid"):
        build_provider_execution_envelope(
            startup_evidence={
                "observation_transport": "restricted-ssh-read",
                "construction_startup_log_sha256": "not-a-sha",
                "embedding_startup_log_sha256": "b" * 64,
            }
        )
