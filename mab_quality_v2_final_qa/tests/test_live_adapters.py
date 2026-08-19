from __future__ import annotations

from types import SimpleNamespace

import pytest

from mab_quality_v2_final_qa.live_adapters import (
    LiveReaderTransport,
    declared_arrival_offsets_ns,
    render_public_episodes,
)


class _Completions:
    async def create(self, **_request: object) -> object:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="concise answer"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
        )


class _Client:
    max_retries = 0
    chat = SimpleNamespace(completions=_Completions())


@pytest.mark.asyncio
async def test_reader_transport_preserves_finish_reason_for_quality_v1() -> None:
    transport = LiveReaderTransport(
        model="qwen3-32b-fp8",
        base_url="http://10.87.5.247:8000/v1",
        api_key="test-key",
        client=_Client(),
    )
    result = await transport.complete(
        {
            "model": "qwen3-32b-fp8",
            "messages": [{"role": "user", "content": "question"}],
        }
    )
    assert result.content == "concise answer"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 3


def test_public_episode_rendering_binds_session_identity_and_namespace() -> None:
    episodes = render_public_episodes(
        {
            "context_id": "ctx",
            "sessions": [
                {
                    "session_id": "s0",
                    "source_sequence": 0,
                    "timestamp": "2024/01/01 (Mon) 12:00",
                    "turns": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "hi"},
                    ],
                }
            ],
        },
        namespace="pev3-mabqv2final-run-U0-ctx",
    )
    assert len(episodes) == 1
    assert episodes[0].session_id == "s0"
    assert episodes[0].group_id == "pev3-mabqv2final-run-U0-ctx"
    assert episodes[0].name.endswith("episode::0000")
    assert "[USER] hello" in episodes[0].body
    assert declared_arrival_offsets_ns(3) == (0, 0, 0)
