from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from paper_eval.s1_live import (
    EXPECTED_S1_HISTORY_ID,
    S1LiveAdapter,
    build_runtime_episodes,
    load_fixed_history,
)


def test_load_fixed_history_uses_manifest_first_id(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '[{"question_id":"second"},{"question_id":"first","question":"q"}]'
    )
    split = tmp_path / "split.json"
    split.write_text('{"calibration_question_ids":["first","second"]}')
    instance = load_fixed_history(dataset, split, expected_history_id="first")
    assert instance["question_id"] == "first"


def test_load_fixed_history_rejects_drift_from_frozen_s1_id(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset.json"
    dataset.write_text('[{"question_id":"other"}]')
    split = tmp_path / "split.json"
    split.write_text('{"calibration_question_ids":["other"]}')
    with pytest.raises(ValueError, match="frozen S1 history"):
        load_fixed_history(dataset, split, expected_history_id=EXPECTED_S1_HISTORY_ID)


def test_build_runtime_episodes_rebinds_fresh_namespace() -> None:
    @dataclass(frozen=True)
    class RuntimeEpisode:
        source_sequence: int
        group_id: str

    episodes = build_runtime_episodes(
        {"question_id": EXPECTED_S1_HISTORY_ID},
        "pev3-s1-fresh",
        builder=lambda _: [RuntimeEpisode(0, "old"), RuntimeEpisode(1, "old")],
    )
    assert [item.source_sequence for item in episodes] == [0, 1]
    assert [item.group_id for item in episodes] == ["pev3-s1-fresh"] * 2


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    group_id: str


def test_live_adapter_rebinds_only_group_namespace() -> None:
    seen: list[Episode] = []

    def kwargs_builder(episode: Episode) -> dict[str, object]:
        seen.append(episode)
        return {"source_sequence": episode.source_sequence, "group_id": episode.group_id}

    adapter = S1LiveAdapter("pev3-s1-namespace", kwargs_builder=kwargs_builder)
    kwargs = adapter.episode_kwargs(Episode(3, "original"))
    assert seen == [Episode(3, "pev3-s1-namespace")]
    assert kwargs == {"source_sequence": 3, "group_id": "pev3-s1-namespace"}


@pytest.mark.asyncio
async def test_namespace_probe_returns_safe_counts_and_episode_names() -> None:
    class Result:
        records = [
            {"node_count": 4, "relationship_count": 3, "episode_names": ["b", "a"]}
        ]

    class Driver:
        async def execute_query(self, query: str, *, params: dict[str, str]) -> Result:
            assert "Episodic" in query
            assert "r.group_id" in query
            assert params == {"group_id": "ns"}
            return Result()

    adapter = S1LiveAdapter("ns")
    state = await adapter.namespace_state(Driver())
    assert state == {
        "node_count": 4,
        "relationship_count": 3,
        "episode_names": ["a", "b"],
    }
