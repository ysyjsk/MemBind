from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.s1_u0_smoke import DurableRun, NamespaceMismatch


class FakeGraph:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[int] = []
        self.fail_at = fail_at

    async def add_episode(self, *, source_sequence: int, **_: object) -> None:
        if self.fail_at == source_sequence:
            raise RuntimeError("synthetic service failure")
        self.calls.append(source_sequence)

    async def search(self, **_: object) -> list[dict[str, str]]:
        return [{"uuid": "retrieval-1"}]

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_resume_runs_only_unpublished_prefix(tmp_path: Path) -> None:
    graph = FakeGraph(fail_at=2)
    run = DurableRun(tmp_path, "run-1", "07741c45", "ns-1")
    result = await run.execute(graph, list(range(5)), query="q")
    assert result.status == "incomplete"
    assert graph.calls == [0, 1]
    checkpoint = json.loads((tmp_path / "run-1" / "checkpoint.json").read_text())
    assert checkpoint["completed_source_sequences"] == [0, 1]

    graph.fail_at = None
    resumed = DurableRun(tmp_path, "run-1", "07741c45", "ns-1")
    result = await resumed.execute(graph, list(range(5)), query="q")
    assert result.status == "completed"
    assert graph.calls == [0, 1, 2, 3, 4]


def test_nonempty_namespace_without_matching_checkpoint_fails_closed(tmp_path: Path) -> None:
    run = DurableRun(tmp_path, "run-2", "07741c45", "ns-2")
    (tmp_path / "run-2").mkdir(parents=True)
    (tmp_path / "run-2" / "namespace.nonempty").write_text("1")
    with pytest.raises(NamespaceMismatch):
        run.load_checkpoint(namespace_nonempty=True)


@pytest.mark.asyncio
async def test_completed_run_is_idempotent_and_does_not_repeat_retrieval(tmp_path: Path) -> None:
    graph = FakeGraph()
    run = DurableRun(tmp_path, "run-3", "07741c45", "ns-3")
    first = await run.execute(graph, [0, 1], query="q")
    assert first.status == "completed"
    first_events = (tmp_path / "run-3" / "events.jsonl").read_text()

    graph_again = FakeGraph()
    second = await run.execute(graph_again, [0, 1], query="q")
    assert second.status == "completed"
    assert graph_again.calls == []
    assert (tmp_path / "run-3" / "events.jsonl").read_text() == first_events
