"""TDD for the frozen fixed-three S4 qualification dataset projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from paper_eval.artifacts import sha256_file
from paper_eval.s4_sidecar_qualification_data import (
    EXPECTED_EPISODE_COUNTS,
    LIVE_HISTORY_IDS,
    load_s4_qualification_block,
)


@dataclass(frozen=True)
class Episode:
    source_sequence: int
    source_hash: str
    name: str
    body: str


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    dataset = tmp_path / "dataset.json"
    records = [
        {
            "question_id": history_id,
            "question_type": "knowledge-update",
            "episode_count_for_test": count,
        }
        for history_id, count in EXPECTED_EPISODE_COUNTS.items()
    ]
    dataset.write_text(json.dumps(records), encoding="utf-8")
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps(
            {
                "source_sha256": sha256_file(dataset),
                "calibration_question_ids": [
                    "07741c45",
                    *LIVE_HISTORY_IDS,
                ],
            }
        ),
        encoding="utf-8",
    )
    return dataset, split


def _builder(record: dict) -> list[Episode]:
    return [
        Episode(
            source_sequence=index,
            source_hash=hashlib.sha256(
                f"{record['question_id']}:{index}".encode()
            ).hexdigest(),
            name=f"{record['question_id']}::episode::{index:04d}",
            body=f"body-{index}",
        )
        for index in range(record["episode_count_for_test"])
    ]


@pytest.mark.parametrize(
    ("history_id", "episode_count"),
    [("b6019101", 49), ("6071bd76", 46), ("a2f3aa27", 44)],
)
def test_load_block_binds_exact_split_record_count_and_hash_only_manifest(
    tmp_path: Path, history_id: str, episode_count: int
) -> None:
    dataset, split = _write_fixture(tmp_path)

    block = load_s4_qualification_block(
        dataset_path=dataset,
        split_path=split,
        history_id=history_id,
        episode_builder=_builder,
    )

    assert block.history_id == history_id
    assert block.episode_count == episode_count
    assert len(block.episodes) == episode_count
    assert block.dataset_file_sha256 == sha256_file(dataset)
    assert block.split_file_sha256 == sha256_file(split)
    assert len(block.episode_manifest_sha256) == 64
    assert list(block.episode_manifest) == [episode.name for episode in block.episodes]
    assert set(block.episode_manifest[block.episodes[0].name]) == {
        "body_sha256",
        "source_hash",
        "source_sequence",
    }
    assert "body-0" not in json.dumps(block.episode_manifest)


def test_load_block_rejects_nonfixed_history_count_and_dataset_drift(
    tmp_path: Path,
) -> None:
    dataset, split = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="fixed-three"):
        load_s4_qualification_block(
            dataset_path=dataset,
            split_path=split,
            history_id="07741c45",
            episode_builder=_builder,
        )

    records = json.loads(dataset.read_text(encoding="utf-8"))
    records[0]["episode_count_for_test"] = 48
    dataset.write_text(json.dumps(records), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset SHA256"):
        load_s4_qualification_block(
            dataset_path=dataset,
            split_path=split,
            history_id="b6019101",
            episode_builder=_builder,
        )


def test_load_block_rejects_noncontiguous_episode_projection(tmp_path: Path) -> None:
    dataset, split = _write_fixture(tmp_path)

    def invalid(record: dict) -> list[Episode]:
        episodes = _builder(record)
        return [Episode(7, item.source_hash, item.name, item.body) for item in episodes]

    with pytest.raises(ValueError, match="contiguous"):
        load_s4_qualification_block(
            dataset_path=dataset,
            split_path=split,
            history_id="6071bd76",
            episode_builder=invalid,
        )
