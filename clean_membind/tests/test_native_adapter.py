from datetime import datetime, timezone

import pytest

from membind.native import AsyncNative, GraphitiEpisode, GraphitiNative, parse_reference_time


class FakeGraphiti:
    def __init__(self):
        self.calls = []

    async def add_episode(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["name"]


@pytest.mark.asyncio
async def test_native_calls_upstream_add_episode_without_mutating_body():
    graphiti = FakeGraphiti()
    native = GraphitiNative(graphiti)
    episode = GraphitiEpisode("e0", "[USER]\nhello", "mab", "2025-01-01T00:00:00Z", uuid="u0", group_id="g")
    assert await native.add_episode(episode) == "e0"
    assert graphiti.calls[0]["episode_body"] == episode.body
    assert graphiti.calls[0]["reference_time"] == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert graphiti.calls[0]["uuid"] is None


@pytest.mark.asyncio
async def test_async_native_uses_same_boundary():
    graphiti = FakeGraphiti()
    runner = AsyncNative(GraphitiNative(graphiti), max_concurrency=2)
    episodes = [GraphitiEpisode(f"e{i}", f"body {i}", "mab", "2025-01-01T00:00:00+00:00") for i in range(3)]
    assert await runner.run(episodes) == ("e0", "e1", "e2")
    assert [call["name"] for call in graphiti.calls] == ["e0", "e1", "e2"]


def test_reference_time_rejects_malformed_values():
    with pytest.raises(ValueError):
        parse_reference_time("not-a-date")


def test_reference_time_accepts_frozen_longmemeval_timestamp():
    assert parse_reference_time("2022/11/17 (Thu) 12:04").year == 2022
