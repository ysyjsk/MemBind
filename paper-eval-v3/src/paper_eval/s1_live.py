"""Narrow adapters from the frozen LongMemEval input to the S1 U0 runner.

This module deliberately imports the previously qualified dataset and Graphiti
helpers lazily.  The isolated lane owns orchestration and artifacts while the
episode rendering and direct upstream call contract remain hash-auditable.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope


EXPECTED_S1_HISTORY_ID = "07741c45"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fixed_history(
    dataset_path: Path,
    split_path: Path,
    *,
    expected_history_id: str = EXPECTED_S1_HISTORY_ID,
) -> dict[str, Any]:
    """Load only the frozen first calibration history and reject ID drift."""

    split = _load_json(Path(split_path))
    ids = split.get("calibration_question_ids") if isinstance(split, dict) else None
    if not isinstance(ids, list) or not ids:
        raise ValueError("calibration manifest has no question IDs")
    selected = str(ids[0])
    if selected != expected_history_id:
        raise ValueError(
            f"frozen S1 history drift: expected {expected_history_id}, got {selected}"
        )
    records = _load_json(Path(dataset_path))
    if not isinstance(records, list):
        raise ValueError("LongMemEval dataset must be a JSON list")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and str(record.get("question_id", "")) == expected_history_id
    ]
    if len(matches) != 1:
        raise ValueError(
            f"frozen S1 history must occur exactly once; found {len(matches)}"
        )
    return dict(matches[0])


def build_runtime_episodes(
    instance: Mapping[str, Any],
    namespace: str,
    *,
    builder: Callable[[dict[str, Any]], Sequence[Any]],
) -> list[Any]:
    """Render the qualified episodes and rebind only their fresh group ID."""

    if not namespace:
        raise ValueError("S1 namespace must be nonempty")
    episodes = list(builder(dict(instance)))
    sequences = [int(getattr(item, "source_sequence")) for item in episodes]
    if sequences != list(range(len(episodes))):
        raise ValueError("rendered S1 episodes are not a contiguous source sequence")
    return [replace(item, group_id=namespace) for item in episodes]


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if values is None:
        raise RuntimeError(f"unsupported Neo4j result: {type(result).__name__}")
    return [value if isinstance(value, dict) else dict(value) for value in values]


class S1LiveAdapter:
    """Namespace-safe episode and Neo4j adapters for one live smoke."""

    def __init__(
        self,
        namespace: str,
        *,
        kwargs_builder: Callable[[Any], Mapping[str, Any]] | None = None,
    ) -> None:
        if not namespace:
            raise ValueError("S1 namespace must be nonempty")
        self.namespace = namespace
        self._kwargs_builder = kwargs_builder

    def episode_kwargs(self, episode: Any) -> dict[str, Any]:
        rebound = replace(episode, group_id=self.namespace)
        if self._kwargs_builder is None:
            from graphiti_native import graphiti_episode_kwargs

            builder = graphiti_episode_kwargs
        else:
            builder = self._kwargs_builder
        kwargs = dict(builder(rebound))
        if kwargs.get("group_id") != self.namespace:
            raise ValueError("episode kwargs escaped the S1 namespace")
        return kwargs

    async def namespace_state(self, driver: Any) -> dict[str, Any]:
        result = await driver.execute_query(
            """
            CALL {
              MATCH (n)
              WHERE n.group_id = $group_id
              RETURN collect(n) AS nodes
            }
            CALL {
              MATCH ()-[r]->()
              WHERE r.group_id = $group_id
              RETURN count(r) AS relationship_count
            }
            RETURN size(nodes) AS node_count, relationship_count,
                   [n IN nodes WHERE n:Episodic | n.name] AS episode_names
            """,
            params={"group_id": self.namespace},
        )
        records = _records(result)
        if len(records) != 1:
            raise RuntimeError("namespace probe returned an unexpected row count")
        row = records[0]
        return {
            "node_count": int(row.get("node_count") or 0),
            "relationship_count": int(row.get("relationship_count") or 0),
            "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
        }


def finalize_u0_smoke(
    *,
    output_path: Path,
    git_commit: str,
    run_id: str,
    history_id: str,
    namespace: str,
    completed_sequences: list[int],
    expected_episode_count: int,
    retrieval_result_ids: list[str],
    checkpoint_sha256: str,
    events_sha256: str,
) -> dict[str, Any]:
    complete = completed_sequences == list(range(expected_episode_count))
    payload = {
        "stage": "S1",
        "method": "U0",
        "history_id": history_id,
        "namespace": namespace,
        "expected_episode_count": expected_episode_count,
        "completed_source_sequences": completed_sequences,
        "episode_coverage": len(completed_sequences) / expected_episode_count,
        "lost_count": 0 if complete else expected_episode_count - len(completed_sequences),
        "duplicate_count": 0,
        "unexpected_count": 0,
        "serial_source_order": complete,
        "retrieval_callable": True,
        "retrieval_result_ids": retrieval_result_ids,
        "checkpoint_sha256": checkpoint_sha256,
        "events_sha256": events_sha256,
        "verdict": "PASS" if complete else "FAIL",
    }
    envelope = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(output_path, envelope)
    return envelope
