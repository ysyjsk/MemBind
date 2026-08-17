"""RED-first tests for the isolated finish-reason-aware chat transport."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.graph_quality_transport import (
    GraphQualityTransport,
    GraphQualityTransportError,
)


class _Completions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    async def create(self, **request: object) -> object:
        self.requests.append(dict(request))
        return self.response


class _Client:
    def __init__(self, response: object, *, max_retries: int = 0) -> None:
        self.max_retries = max_retries
        self.completions = _Completions(response)
        self.chat = SimpleNamespace(completions=self.completions)


def _response(finish_reason: str) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content="answer"),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
    )


@pytest.mark.asyncio
async def test_transport_preserves_finish_reason_and_has_no_hidden_retry() -> None:
    client = _Client(_response("length"))
    transport = GraphQualityTransport(
        model="qwen3-32b-fp8",
        base_url="http://model.invalid/v1/",
        api_key="PRIVATE_KEY",
        client=client,
    )
    result = await transport.complete(
        {
            "model": "qwen3-32b-fp8",
            "messages": [{"role": "user", "content": "question"}],
            "max_tokens": 500,
        }
    )

    assert result.finish_reason == "length"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 3
    assert client.completions.requests[0]["max_tokens"] == 500
    assert "PRIVATE_KEY" not in str(transport.public_config)
    assert transport.public_config["sdk_hidden_retries"] == 0
    assert transport.public_config["finish_reason_preserved"] is True


def test_transport_rejects_injected_clients_with_hidden_retries() -> None:
    with pytest.raises(GraphQualityTransportError, match="retries"):
        GraphQualityTransport(
            model="qwen3-32b-fp8",
            base_url="http://model.invalid/v1/",
            api_key="key",
            client=_Client(_response("stop"), max_retries=2),
        )

