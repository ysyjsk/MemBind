from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.s1_u0_smoke import DurableRun


class TinyGraph:
    def __init__(self) -> None:
        self.published: list[int] = []

    async def add_episode(self, **kwargs: object) -> None:
        self.published.append(int(kwargs["source_sequence"]))

    async def search(self, **_: object) -> list[dict[str, str]]:
        return [{"uuid": "r1"}]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_s1_records_exactly_once_publication_and_retrieval(tmp_path: Path) -> None:
    graph = TinyGraph()
    emitted: list[dict[str, object]] = []
    run = DurableRun(
        tmp_path,
        "run-1",
        "07741c45",
        "namespace-1",
        event_sink=emitted.append,
    )
    result = await run.execute(graph, [0, 1, 2], query="find")
    assert result.status == "completed"
    assert graph.published == [0, 1, 2]
    events = [json.loads(line) for line in (tmp_path / "run-1" / "events.jsonl").read_text().splitlines()]
    assert [e["source_sequence"] for e in events if e["event_type"] == "publication"] == [0, 1, 2]
    assert sum(e["event_type"] == "retrieval" for e in events) == 1
    assert all("episode_body" not in e and "prompt" not in e for e in events)
    assert [e["event_type"] for e in emitted] == [
        "intent",
        "publication",
        "intent",
        "publication",
        "intent",
        "publication",
        "retrieval",
    ]
