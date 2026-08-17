"""TDD contracts for the public identity bound into node-only artifacts."""

from __future__ import annotations

import pytest

from paper_eval.membind_v1.execution_identity import (
    MemBindV1ExecutionIdentityError,
    build_node_artifact_identity,
)


def _runtime_identity() -> dict[str, object]:
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-live-runtime.v1",
        "construction": {
            "base_url": "http://example.invalid/v1",
            "served_model_id": "qwen3-32b-fp8",
            "requested_max_tokens": 16384,
            "structured_output_mode": "json_schema",
        },
        "embedding": {
            "base_url": "http://example.invalid/embedding/v1",
            "served_model_id": "qwen3-embedding-0.6b",
            "dimension": 1024,
        },
        "neo4j": {"uri": "bolt://localhost:7687"},
        "graphiti_max_coroutines": 8,
        "global_llm_admission_k": 2,
    }


def _implementation_hashes() -> dict[str, str]:
    return {
        "aligned_live": "1" * 64,
        "graphiti_adapter": "2" * 64,
        "graphiti_factories": "3" * 64,
        "semantic_trace_binding": "4" * 64,
    }


def test_node_artifact_identity_is_deterministic_and_separates_its_boundaries() -> None:
    first = build_node_artifact_identity(
        runtime_identity=_runtime_identity(),
        implementation_hashes=_implementation_hashes(),
    )
    second = build_node_artifact_identity(
        runtime_identity=_runtime_identity(),
        implementation_hashes=_implementation_hashes(),
    )

    assert first == second
    assert len(
        {
            first.operation_identity_sha256,
            first.model_identity_sha256,
            first.prompt_identity_sha256,
            first.schema_identity_sha256,
            first.config_identity_sha256,
        }
    ) == 5

    changed = _implementation_hashes()
    changed["graphiti_adapter"] = "f" * 64
    drifted = build_node_artifact_identity(
        runtime_identity=_runtime_identity(),
        implementation_hashes=changed,
    )
    assert drifted.operation_identity_sha256 != first.operation_identity_sha256
    assert drifted.prompt_identity_sha256 != first.prompt_identity_sha256
    assert drifted.model_identity_sha256 == first.model_identity_sha256


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda runtime, hashes: runtime["construction"].update(api_key="private"),  # type: ignore[index]
            "private",
        ),
        (
            lambda runtime, hashes: hashes.pop("graphiti_adapter"),
            "implementation",
        ),
        (
            lambda runtime, hashes: hashes.update(aligned_live="not-a-hash"),
            "implementation",
        ),
        (
            lambda runtime, hashes: runtime.update(global_llm_admission_k=3),
            "admission",
        ),
    ],
)
def test_node_artifact_identity_rejects_secret_or_execution_envelope_drift(
    mutate, message: str
) -> None:
    runtime = _runtime_identity()
    hashes = _implementation_hashes()
    mutate(runtime, hashes)

    with pytest.raises(MemBindV1ExecutionIdentityError, match=message):
        build_node_artifact_identity(
            runtime_identity=runtime,
            implementation_hashes=hashes,
        )
