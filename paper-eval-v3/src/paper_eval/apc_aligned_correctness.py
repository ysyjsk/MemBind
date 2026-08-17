"""Independent post-construction correctness observation for APC baselines."""

from __future__ import annotations

import inspect
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from paper_eval.apc_aligned_baseline import (
    summarize_direct_violations,
    verify_apc_aligned_baseline_plan,
)
from paper_eval.membind_v1.aligned_artifacts import inspect_aligned_block_artifacts


GRAPH_COUNT_FIELDS = (
    "lost_episodic_count",
    "duplicate_episodic_count",
    "unexpected_episodic_count",
    "episodic_namespace_escape_count",
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
)


async def _query(
    driver: object, statement: str, params: Mapping[str, object]
) -> list[dict[str, object]]:
    execute = getattr(driver, "execute_query", None)
    if not callable(execute):
        raise ValueError("correctness query driver unavailable")
    result = execute(statement, params=dict(params))
    if not inspect.isawaitable(result):
        raise ValueError("correctness query must be async")
    result = await result
    records = getattr(result, "records", None)
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("correctness query result invalid")
    selected: list[dict[str, object]] = []
    for row in records:
        if isinstance(row, Mapping):
            selected.append(dict(row))
            continue
        items = getattr(row, "items", None)
        if not callable(items):
            raise ValueError("correctness query result invalid")
        selected.append(dict(items()))
    return selected


async def observe_graph_correctness_counts(
    *, driver: object, namespace: str, expected_episode_names: Sequence[str]
) -> dict[str, int]:
    """Observe source and hard namespace/provenance invariants.

    Entity UUID uniqueness is intentionally not an invariant: multiple
    extracted entities may legally resolve to one canonical Graphiti entity.
    """

    names = tuple(expected_episode_names)
    if (
        not isinstance(namespace, str)
        or not namespace
        or not names
        or any(not isinstance(value, str) or not value for value in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("correctness observation identity invalid")
    episode_rows = await _query(
        driver,
        """
        MATCH (episode:Episodic)
        WHERE episode.group_id = $namespace OR episode.name IN $expected_names
        RETURN episode.name AS name, episode.group_id AS group_id
        """,
        {"namespace": namespace, "expected_names": list(names)},
    )
    expected = Counter(names)
    in_namespace = Counter(
        str(row.get("name"))
        for row in episode_rows
        if row.get("group_id") == namespace
    )
    lost = sum(max(0, expected[name] - in_namespace[name]) for name in expected)
    duplicate = sum(max(0, in_namespace[name] - expected[name]) for name in expected)
    unexpected = sum(count for name, count in in_namespace.items() if name not in expected)
    escape = sum(
        1
        for row in episode_rows
        if row.get("name") in expected and row.get("group_id") != namespace
    )
    hard_rows = await _query(
        driver,
        """
        CALL {
          MATCH (entity:Entity)
          WHERE entity.group_id = $namespace
             OR EXISTS {
               MATCH (left:Entity)-[relation:RELATES_TO]->(right:Entity)
               WHERE (left = entity OR right = entity)
                 AND (relation.group_id = $namespace OR left.group_id = $namespace OR right.group_id = $namespace)
             }
          RETURN sum(CASE WHEN entity.group_id <> $namespace THEN 1 ELSE 0 END) AS entity_namespace_escape_count
        }
        CALL {
          MATCH (source:Entity)-[relation:RELATES_TO]->(target:Entity)
          WHERE relation.group_id = $namespace OR source.group_id = $namespace OR target.group_id = $namespace
          OPTIONAL MATCH (episode:Episodic)
          WHERE episode.uuid IN coalesce(relation.episodes, [])
          WITH source, relation, target, collect(episode) AS resolved
          RETURN
            sum(CASE WHEN relation.group_id <> $namespace THEN 1 ELSE 0 END) AS relation_namespace_escape_count,
            sum(CASE WHEN source.group_id <> $namespace OR target.group_id <> $namespace THEN 1 ELSE 0 END) AS endpoint_escape_count,
            sum(size(coalesce(relation.episodes, [])) - size(resolved)) AS provenance_dangling_count,
            sum(size([episode IN resolved WHERE episode.group_id <> $namespace])) AS provenance_cross_namespace_count,
            sum(CASE WHEN relation.valid_at IS NOT NULL AND relation.invalid_at IS NOT NULL
                           AND relation.invalid_at < relation.valid_at THEN 1 ELSE 0 END) AS valid_invalid_reversal_count
        }
        RETURN entity_namespace_escape_count, relation_namespace_escape_count,
               endpoint_escape_count, provenance_dangling_count,
               provenance_cross_namespace_count, valid_invalid_reversal_count
        """,
        {"namespace": namespace},
    )
    if len(hard_rows) != 1:
        raise ValueError("correctness aggregate query invalid")
    hard = hard_rows[0]
    result = {
        "lost_episodic_count": lost,
        "duplicate_episodic_count": duplicate,
        "unexpected_episodic_count": unexpected,
        "episodic_namespace_escape_count": escape,
        **{field: int(hard.get(field) or 0) for field in GRAPH_COUNT_FIELDS[4:]},
    }
    if set(result) != set(GRAPH_COUNT_FIELDS) or any(value < 0 for value in result.values()):
        raise ValueError("correctness graph observation invalid")
    return result


async def measure_apc_aligned_direct_violations(
    root: Path,
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    driver: object,
    expected_episode_names: Sequence[str],
) -> dict[str, object]:
    plan = verify_apc_aligned_baseline_plan(verified_plan)
    blocks = plan["blocks"]
    if (
        isinstance(block_index, bool)
        or not isinstance(block_index, int)
        or not 0 <= block_index < len(blocks)
    ):
        raise ValueError("block index invalid")
    inspected = inspect_aligned_block_artifacts(Path(root))
    if (
        inspected["checkpoint"].get("terminal_status") != "COMPLETED"
        or inspected["manifest"].get("plan_payload_sha256") != plan["payload_sha256"]
        or inspected["manifest"].get("block_index") != block_index
    ):
        raise ValueError("complete aligned block required")
    publications = [
        event
        for event in inspected["events"]
        if event.get("event_type") == "PUBLICATION_DURABLE"
    ]
    visibility: dict[int, bool] = {}
    for event in publications:
        source = event.get("source_sequence")
        telemetry = event.get("telemetry")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or not isinstance(telemetry, Mapping)
            or not isinstance(telemetry.get("visibility_confirmed"), bool)
            or source in visibility
        ):
            raise ValueError("publication visibility evidence incomplete")
        visibility[source] = bool(telemetry["visibility_confirmed"])
    block = blocks[block_index]
    graph = await observe_graph_correctness_counts(
        driver=driver,
        namespace=str(block["namespace"]),
        expected_episode_names=expected_episode_names,
    )
    return summarize_direct_violations(
        expected_source_count=int(block["source_count"]),
        publication_source_sequences=tuple(
            int(event["source_sequence"]) for event in publications
        ),
        visibility_by_source=visibility,
        graph_counts=graph,
    )


__all__ = [
    "GRAPH_COUNT_FIELDS",
    "measure_apc_aligned_direct_violations",
    "observe_graph_correctness_counts",
]
