from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.strict_development_runtime import (
    STRICT_CONSTRUCTION_MODEL,
    StrictBailianChatCompletions,
    StrictDevelopmentRuntimeError,
    load_strict_development_runtime_freeze,
    normalize_strict_bailian_request,
)


PROJECT = Path(__file__).resolve().parents[1]
FREEZE = PROJECT / "v7/BAILIAN_QWEN3_MAX_STRICT_DEVELOPMENT_RUNTIME_FREEZE.json"


def _response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ExtractedEdges",
            "schema": {
                "type": "object",
                "properties": {"edges": {"type": "array", "items": {"type": "object"}}},
                "required": ["edges"],
            },
        },
    }


def test_strict_runtime_freeze_binds_unique_candidate_and_embedding_lane() -> None:
    frozen = load_strict_development_runtime_freeze(FREEZE)

    assert frozen["provider_identity_kind"] == (
        "COMPOSITE_DEVELOPMENT_STRICT_SCHEMA_TEMPORARY"
    )
    assert frozen["construction"]["model"] == "qwen3-max-2026-01-23"
    assert frozen["construction"]["structured_output_mode"] == "json_schema"
    assert frozen["construction"]["strict_json_schema"] is True
    assert frozen["construction"]["candidate_artifact_sha256"] == (
        "3e6c163908b6b0abddaf9217d50b6bf55624823878de3a91bfb541ef788eac93"
    )
    assert frozen["embedding"]["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert frozen["embedding"]["dimension"] == 1024
    assert frozen["construction"]["authority"] != frozen["embedding"]["authority"]
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["live_treatment_authorized"] is False


def test_strict_request_normalization_forces_strict_true_at_http_boundary() -> None:
    normalized = normalize_strict_bailian_request(
        {
            "model": STRICT_CONSTRUCTION_MODEL,
            "messages": [{"role": "user", "content": "extract"}],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 16_384,
            "response_format": _response_format(),
        }
    )

    assert normalized["model"] == "qwen3-max-2026-01-23"
    assert normalized["response_format"]["json_schema"]["strict"] is True
    assert normalized["extra_body"] == {"enable_thinking": False}
    assert "max_tokens" not in normalized

    with pytest.raises(StrictDevelopmentRuntimeError, match="model"):
        normalize_strict_bailian_request(
            {"model": "qwen3.5-122b-a10b", "response_format": _response_format()}
        )
    with pytest.raises(StrictDevelopmentRuntimeError, match="JSON schema"):
        normalize_strict_bailian_request(
            {
                "model": STRICT_CONSTRUCTION_MODEL,
                "response_format": {"type": "json_object"},
            }
        )


@pytest.mark.asyncio
async def test_strict_transport_is_one_attempt_and_content_free_observed() -> None:
    class Endpoint:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> object:
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"edges":[]}'),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4),
            )

    endpoint = Endpoint()
    observations: list[dict[str, object]] = []
    transport = StrictBailianChatCompletions(
        endpoint,
        response_observer=observations.append,
    )

    result = await transport.create(
        model=STRICT_CONSTRUCTION_MODEL,
        messages=[{"role": "user", "content": "extract"}],
        temperature=0.0,
        max_tokens=16_384,
        response_format=_response_format(),
    )

    assert result.choices[0].finish_reason == "stop"
    assert len(endpoint.calls) == 1
    assert endpoint.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert "max_tokens" not in endpoint.calls[0]
    assert observations == [
        {
            "lane": "construction",
            "structured": True,
            "finish_reason": "stop",
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "content_bytes": 12,
            "content_sha256": hashlib.sha256(b'{"edges":[]}').hexdigest(),
        }
    ]
