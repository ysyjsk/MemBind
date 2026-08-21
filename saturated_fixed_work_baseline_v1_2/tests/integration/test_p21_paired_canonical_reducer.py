from __future__ import annotations

import copy
import json
from pathlib import Path

from saturated_fixed_work_baseline_v1_2.dataset import EXPECTED_EPISODE_COUNTS
from saturated_fixed_work_baseline_v1_2.reducer import attach_paired_canonical_diffs
from saturated_fixed_work_baseline_v1_2.schedules import Method


def _graph(namespace: str, history: str) -> dict[str, object]:
    return {
        "entities": [
            {
                "group_id": namespace,
                "name": "Alice",
                "labels": ["Entity"],
                "summary": f"summary-{history}",
                "attributes": {"kind": "person"},
            }
        ],
        "edges": [],
        "episodes": [
            {
                "source_sequence": 0,
                "source_hash": history * 8,
                "session_id": f"session-{history}",
            }
        ],
    }


def _rows(tmp_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for history in EXPECTED_EPISODE_COUNTS:
        for method in Method:
            namespace = f"sfwb-v1-2/{method.value}/{history}/attempt-001"
            attempt = tmp_path / method.value / history / "attempt-001"
            attempt.mkdir(parents=True)
            graph = _graph(namespace, history)
            if (
                history == "a2f3aa27"
                and method is Method.B1_NAIVE_WHOLE_UPDATE_ASYNC
            ):
                graph["entities"][0]["summary"] = "semantic-change"
            (attempt / "canonical_graph.json").write_text(
                json.dumps(graph) + "\n", encoding="utf-8"
            )
            rows.append(
                {
                    "method": method.value,
                    "history_id": history,
                    "namespace": namespace,
                    "attempt_root": str(attempt),
                    "valid": True,
                    "canonical_exact_match": None,
                }
            )
    return rows


def test_paired_reducer_normalizes_namespaces_and_preserves_sealed_rows(
    repository_root: Path, tmp_path: Path
) -> None:
    rows = _rows(tmp_path)
    before = copy.deepcopy(rows)
    result = attach_paired_canonical_diffs(
        rows,
        repository_root=repository_root,
        expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
    )
    assert rows == before
    assert len(result["rows"]) == 8
    assert len(result["diffs"]) == 4
    by_key = {
        (row["method"], row["history_id"]): row for row in result["rows"]
    }
    assert all(
        by_key[(Method.B0_NATIVE_SERIAL.value, history)]["canonical_exact_match"]
        is True
        for history in EXPECTED_EPISODE_COUNTS
    )
    assert sum(
        by_key[(Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value, history)][
            "canonical_exact_match"
        ]
        is True
        for history in EXPECTED_EPISODE_COUNTS
    ) == 3
    changed = next(
        row for row in result["diffs"] if row["history_id"] == "a2f3aa27"
    )
    assert changed["exact_match"] is False
    assert changed["difference_counts"]["attribute"] > 0
    assert all(
        row["namespace_projection"]["applied"] is True
        for row in result["diffs"]
    )
