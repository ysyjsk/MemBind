from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s1_controller import run_s1
from paper_eval.s1_controller import ensure_runtime_ready


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    group_id: str = "source"


class Graph:
    def __init__(self) -> None:
        self.driver = object()
        self.calls: list[int] = []
        self.closed = False

    async def add_episode(self, **kwargs: object) -> None:
        self.calls.append(int(kwargs["source_sequence"]))

    async def search(self, **_: object) -> list[dict[str, str]]:
        return [{"uuid": "edge-1"}]

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_controller_finalizes_complete_fixed_history(tmp_path: Path) -> None:
    graph = Graph()
    state = {"node_count": 0, "relationship_count": 0, "episode_names": []}

    async def namespace_probe(_: object) -> dict[str, object]:
        return dict(state)

    def kwargs_builder(episode: Episode) -> dict[str, object]:
        state["node_count"] = int(state["node_count"]) + 1
        state["episode_names"] = [*state["episode_names"], f"ep-{episode.source_sequence}"]
        return {"source_sequence": episode.source_sequence, "group_id": episode.group_id}

    result = await run_s1(
        run_id="s1-test",
        namespace="pev3-s1-test",
        artifact_root=tmp_path / "runs",
        final_output=tmp_path / "U0_SMOKE.json",
        git_commit="deadbeef",
        instance={"question_id": "07741c45", "question": "private query"},
        episodes=[Episode(0), Episode(1)],
        runtime=SimpleNamespace(graphiti=graph),
        kwargs_builder=kwargs_builder,
        namespace_probe=namespace_probe,
        expected_episode_count=2,
        event_sink=lambda _: None,
    )

    assert result.status == "completed"
    assert graph.calls == [0, 1]
    assert graph.closed is True
    final = json.loads((tmp_path / "U0_SMOKE.json").read_text())
    assert final["status"] == "finalized"
    assert final["payload"]["verdict"] == "PASS"
    assert "private query" not in json.dumps(final)


@pytest.mark.asyncio
async def test_controller_rejects_episode_count_drift_before_live_call(tmp_path: Path) -> None:
    graph = Graph()
    with pytest.raises(ValueError, match="episode count drift"):
        await run_s1(
            run_id="s1-test",
            namespace="pev3-s1-test",
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "U0_SMOKE.json",
            git_commit="deadbeef",
            instance={"question_id": "07741c45", "question": "q"},
            episodes=[Episode(0)],
            runtime=SimpleNamespace(graphiti=graph),
            kwargs_builder=lambda episode: {"source_sequence": episode.source_sequence},
            namespace_probe=lambda _: None,
            expected_episode_count=49,
            event_sink=lambda _: None,
        )
    assert graph.calls == []


@pytest.mark.asyncio
async def test_controller_awaits_graphiti_driver_initialization() -> None:
    calls: list[str] = []

    class Driver:
        async def init(self) -> None:
            calls.append("init")

    runtime = SimpleNamespace(graphiti=SimpleNamespace(driver=Driver()))
    await ensure_runtime_ready(runtime)
    assert calls == ["init"]


@pytest.mark.asyncio
async def test_controller_persists_sanitized_attempt_when_readiness_fails(
    tmp_path: Path,
) -> None:
    class Driver:
        async def init(self) -> None:
            raise ConnectionError("secret connection details")

    graph = Graph()
    graph.driver = Driver()
    with pytest.raises(ConnectionError):
        await run_s1(
            run_id="s1-readiness-failure",
            namespace="pev3-s1-readiness-failure",
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "U0_SMOKE.json",
            git_commit="deadbeef",
            instance={"question_id": "07741c45", "question": "private query"},
            episodes=[Episode(0)],
            runtime=SimpleNamespace(graphiti=graph),
            kwargs_builder=lambda episode: {
                "source_sequence": episode.source_sequence,
                "group_id": episode.group_id,
            },
            namespace_probe=lambda _: {},
            expected_episode_count=1,
            event_sink=lambda _: None,
        )
    attempt = json.loads(
        (tmp_path / "runs/s1-readiness-failure/attempt_summary.json").read_text()
    )
    assert attempt["status"] == "incomplete"
    assert attempt["error_class"] == "ConnectionError"
    assert "secret" not in json.dumps(attempt).lower()
    assert graph.closed is True


@pytest.mark.asyncio
async def test_controller_persists_attempt_when_namespace_probe_fails(
    tmp_path: Path,
) -> None:
    graph = Graph()

    async def failed_probe(_: object) -> dict[str, object]:
        raise RuntimeError("private database response")

    with pytest.raises(RuntimeError):
        await run_s1(
            run_id="s1-probe-failure",
            namespace="pev3-s1-probe-failure",
            artifact_root=tmp_path / "runs",
            final_output=tmp_path / "U0_SMOKE.json",
            git_commit="deadbeef",
            instance={"question_id": "07741c45", "question": "private query"},
            episodes=[Episode(0)],
            runtime=SimpleNamespace(graphiti=graph),
            kwargs_builder=lambda episode: {
                "source_sequence": episode.source_sequence,
                "group_id": episode.group_id,
            },
            namespace_probe=failed_probe,
            expected_episode_count=1,
            event_sink=lambda _: None,
        )
    attempt = json.loads(
        (tmp_path / "runs/s1-probe-failure/attempt_summary.json").read_text()
    )
    assert attempt["status"] == "incomplete"
    assert attempt["completed_episode_count"] == 0
    assert attempt["error_class"] == "RuntimeError"
    assert "private" not in json.dumps(attempt).lower()
    assert graph.closed is True
