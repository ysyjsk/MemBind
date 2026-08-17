"""RED-first contract for the development-only graph-quality input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.baseline_suite import DEVELOPMENT_HISTORIES
from paper_eval.development_graph_quality_input import (
    DEVELOPMENT_EPISODE_COUNTS,
    DEVELOPMENT_GRAPH_QUALITY_INPUT_FILE_SHA256,
    DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH,
    DevelopmentGraphQualityInputError,
    load_development_graph_quality_records,
)


PROJECT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT / "scripts/run_three_baseline_graph_quality.py"


def _record(history_id: str, count: int) -> dict[str, object]:
    return {
        "question_id": history_id,
        "question": f"question-{history_id}",
        "question_date": "2025-01-01",
        "question_type": "knowledge-update",
        "answer": f"answer-{history_id}",
        "answer_session_ids": [f"session-{history_id}-0000"],
        "haystack_session_ids": [
            f"session-{history_id}-{index:04d}" for index in range(count)
        ],
        "haystack_dates": ["2024-01-01"] * count,
        "haystack_sessions": [
            [{"role": "user", "content": f"episode-{index}"}]
            for index in range(count)
        ],
    }


def _artifact(
    *,
    order: tuple[str, ...] = DEVELOPMENT_HISTORIES,
    counts: dict[str, int] | None = None,
    records: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    episode_counts = counts or dict(DEVELOPMENT_EPISODE_COUNTS)
    body: dict[str, object] = {
        "schema_version": (
            "membind.paper-eval-v3.development-graph-quality-input.v1"
        ),
        "data_role": "DEVELOPMENT_EXPOSED",
        "source_dataset_sha256": (
            "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
        ),
        "selection_policy": "EXACT_FROZEN_DEVELOPMENT_HISTORIES_ONLY",
        "history_order": list(order),
        "episode_counts": episode_counts,
        "records": records
        if records is not None
        else [_record(value, episode_counts[value]) for value in order],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _write(path: Path, value: dict[str, object]) -> str:
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def test_loader_accepts_only_the_exact_frozen_development_inventory(
    tmp_path: Path,
) -> None:
    path = tmp_path / "development.json"
    file_sha256 = _write(path, _artifact())

    records = load_development_graph_quality_records(
        path,
        expected_file_sha256=file_sha256,
    )

    assert tuple(records) == DEVELOPMENT_HISTORIES
    assert {
        history_id: len(record["haystack_sessions"])
        for history_id, record in records.items()
    } == DEVELOPMENT_EPISODE_COUNTS


@pytest.mark.parametrize("drift", ["extra_id", "order", "episode_count"])
def test_loader_fails_closed_on_inventory_or_episode_drift(
    tmp_path: Path,
    drift: str,
) -> None:
    artifact = _artifact()
    if drift == "extra_id":
        artifact["history_order"] = [*artifact["history_order"], "heldout-id"]
        artifact["episode_counts"] = {
            **artifact["episode_counts"],
            "heldout-id": 1,
        }
        artifact["records"] = [
            *artifact["records"],
            _record("heldout-id", 1),
        ]
    elif drift == "order":
        artifact["history_order"] = list(reversed(artifact["history_order"]))
        artifact["records"] = list(reversed(artifact["records"]))
    else:
        artifact["episode_counts"] = {
            **artifact["episode_counts"],
            DEVELOPMENT_HISTORIES[0]: DEVELOPMENT_EPISODE_COUNTS[
                DEVELOPMENT_HISTORIES[0]
            ]
            - 1,
        }
    artifact["payload_sha256"] = payload_sha256(
        {key: value for key, value in artifact.items() if key != "payload_sha256"}
    )
    path = tmp_path / "development.json"
    file_sha256 = _write(path, artifact)

    with pytest.raises(DevelopmentGraphQualityInputError, match="inventory|episode"):
        load_development_graph_quality_records(
            path,
            expected_file_sha256=file_sha256,
        )


def test_loader_fails_closed_on_raw_file_hash_drift(tmp_path: Path) -> None:
    path = tmp_path / "development.json"
    _write(path, _artifact())

    with pytest.raises(DevelopmentGraphQualityInputError, match="file SHA256"):
        load_development_graph_quality_records(
            path,
            expected_file_sha256="0" * 64,
        )


def test_production_artifact_is_exactly_hash_bound_and_development_only() -> None:
    records = load_development_graph_quality_records(
        DEVELOPMENT_GRAPH_QUALITY_INPUT_PATH,
        expected_file_sha256=DEVELOPMENT_GRAPH_QUALITY_INPUT_FILE_SHA256,
    )

    assert tuple(records) == DEVELOPMENT_HISTORIES
    assert {
        history_id: len(record["haystack_sessions"])
        for history_id, record in records.items()
    } == DEVELOPMENT_EPISODE_COUNTS


def test_live_runner_cannot_open_the_full_longmemeval_dataset() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "longmemeval_s_cleaned.json" not in source
    assert "load_json_records" not in source
    assert "load_development_graph_quality_records" in source

