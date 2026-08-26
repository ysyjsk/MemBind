from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.selected_development_runtime import (
    SELECTED_CONSTRUCTION_MODEL,
    SelectedBailianChatCompletions,
    SelectedDevelopmentRuntimeError,
    load_selected_development_runtime_freeze,
    normalize_selected_bailian_request,
)


PROJECT = Path(__file__).resolve().parents[1]
FREEZE = PROJECT / "v7/BAILIAN_122B_SILICONFLOW_DEVELOPMENT_RUNTIME_FREEZE.json"


def test_selected_runtime_freeze_binds_candidate_evidence_and_split_providers() -> None:
    frozen = load_selected_development_runtime_freeze(FREEZE)

    assert frozen["provider_identity_kind"] == (
        "COMPOSITE_DEVELOPMENT_SELECTED_TEMPORARY"
    )
    assert frozen["construction"]["model"] == "qwen3.5-122b-a10b"
    assert frozen["construction"]["selection_rule"] == (
        "FIRST_FULL_PASS_IN_FROZEN_ORDER"
    )
    assert frozen["construction"]["candidate_artifact_sha256"] == (
        "d263d08746bc8fc801c650af16586ea48c8f6b42c94309aef02cc33fccff0783"
    )
    assert frozen["embedding"]["authority"] == "siliconflow-openai-compatible-v1"
    assert frozen["embedding"]["dimension"] == 1024
    assert frozen["construction"]["authority"] != frozen["embedding"]["authority"]
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["live_treatment_authorized"] is False
    assert frozen["provider_swap_requires_new_formal_campaign"] is True


def test_selected_request_normalization_enforces_122b_json_object_contract() -> None:
    request = normalize_selected_bailian_request(
        {
            "model": SELECTED_CONSTRUCTION_MODEL,
            "messages": [{"role": "user", "content": "schema already injected"}],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 16_384,
            "response_format": {"type": "json_object"},
        }
    )

    assert request["model"] == "qwen3.5-122b-a10b"
    assert request["response_format"] == {"type": "json_object"}
    assert request["extra_body"] == {"enable_thinking": False}
    assert "max_tokens" not in request

    with pytest.raises(SelectedDevelopmentRuntimeError, match="model"):
        normalize_selected_bailian_request(
            {
                "model": "qwen3.5-35b-a3b",
                "response_format": {"type": "json_object"},
            }
        )
    with pytest.raises(SelectedDevelopmentRuntimeError, match="JSON Object"):
        normalize_selected_bailian_request(
            {
                "model": SELECTED_CONSTRUCTION_MODEL,
                "response_format": {"type": "json_schema"},
            }
        )


@pytest.mark.asyncio
async def test_selected_transport_is_single_attempt_and_observes_content_free_metadata() -> None:
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
    transport = SelectedBailianChatCompletions(
        endpoint,
        response_observer=observations.append,
    )

    response = await transport.create(
        model=SELECTED_CONSTRUCTION_MODEL,
        messages=[{"role": "user", "content": "schema already injected"}],
        temperature=0.0,
        max_tokens=16_384,
        response_format={"type": "json_object"},
    )

    assert response.choices[0].finish_reason == "stop"
    assert len(endpoint.calls) == 1
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
