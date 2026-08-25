"""Guarded Graphiti native-continuation contract for T6b.

The selected seam is immediately before ``_process_episode_data``.  Under the
guards below, the pinned 0.29.3 tail has no provider, embedder, community, or
saga read: it builds episodic edges and delegates the four native writes to a
single driver transaction.  This module validates that closed observation
surface; it does not execute, replace, or skip any native work.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class ContinuationStatus(str, Enum):
    SUPPORTED_WITH_GUARD = "SUPPORTED_WITH_GUARD"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class ContinuationCheck:
    status: ContinuationStatus
    reason: str
    failed_guards: tuple[str, ...] = ()


CONTINUATION_K_SCHEMA = "membind.v7.graphiti-continuation-k.v1"
CONTINUATION_SEAM = "graphiti.add_episode.pre_process_episode_data"
REQUIRED_K_FIELDS = frozenset(
    {
        "schema_version",
        "seam",
        "episodes",
        "nodes",
        "entity_edges",
        "node_episode_index_map",
        "now",
        "group_id",
        "store_raw_episode_content",
        "driver_provider",
        "driver_database",
        "backend_epoch",
        "publication_frontier",
        "saga",
        "saga_previous_episode_uuid",
        "update_communities",
    }
)

PINNED_CONTINUATION_SOURCE_HASHES = {
    "graphiti.py": "7c65051a62982d8b510ebdbf37bae4d07020e74520e1f6d9bf8a0ffb26beeccb",
    "utils/bulk_utils.py": "6c7314f24801f0936454b3344788528500432ac5f12692eb36b7d3ef5269f601",
    "utils/maintenance/node_operations.py": "14fc92a462bf7f1dd9b70d10a88e27e36a0ddc1594dc18381888209de7137fb4",
    "utils/maintenance/edge_operations.py": "b773ff4489968af2a996d5074e679cab9806cc0904a7ff9f2aecc74382325abe",
    "utils/maintenance/community_operations.py": "537ec6782ae32c830ec117a3834453e7b517aa7d648254db9eb10c67f38d3a38",
    "models/nodes/node_db_queries.py": "1400e51df33fa4b10c49a4541b1dd3ab690b993a48fe01716e6b91a2356797db",
    "models/edges/edge_db_queries.py": "b967ffeae7da4cb7037e3ba1b14d099543c728e7004e176de81d4b34cf314d43",
}


def _records(value: Any) -> tuple[Mapping[str, Any], ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    records = tuple(value)
    if not all(isinstance(record, Mapping) for record in records):
        return None
    return records  # type: ignore[return-value]


def _missing_embedding(records: tuple[Mapping[str, Any], ...], field: str) -> bool:
    return any(record.get(field) is None for record in records)


def validate_continuation_k(value: Mapping[str, Any]) -> ContinuationCheck:
    """Validate the complete observation surface for the guarded native tail."""

    missing = tuple(sorted(REQUIRED_K_FIELDS - set(value)))
    if missing:
        return ContinuationCheck(ContinuationStatus.UNKNOWN, "continuation K is incomplete", missing)

    failed: list[str] = []
    if value.get("schema_version") != CONTINUATION_K_SCHEMA:
        failed.append("schema_version")
    if value.get("seam") != CONTINUATION_SEAM:
        failed.append("seam")
    episodes = _records(value.get("episodes"))
    nodes = _records(value.get("nodes"))
    edges = _records(value.get("entity_edges"))
    if episodes is None or not episodes:
        failed.append("episodes")
    if nodes is None:
        failed.append("nodes")
    if edges is None:
        failed.append("entity_edges")
    if nodes is not None and _missing_embedding(nodes, "name_embedding"):
        failed.append("missing_node_embedding")
    if edges is not None and _missing_embedding(edges, "fact_embedding"):
        failed.append("missing_edge_embedding")
    if value.get("saga") is not None or value.get("saga_previous_episode_uuid") is not None:
        failed.append("saga_disabled")
    if value.get("update_communities") is not False:
        failed.append("communities_disabled")
    if value.get("driver_provider") != "neo4j":
        failed.append("neo4j_provider")
    for field in ("now", "group_id", "driver_database", "backend_epoch"):
        if not isinstance(value.get(field), str) or not value.get(field):
            failed.append(field)
    if not isinstance(value.get("store_raw_episode_content"), bool):
        failed.append("store_raw_episode_content")
    frontier = value.get("publication_frontier")
    if isinstance(frontier, bool) or not isinstance(frontier, int) or frontier < 0:
        failed.append("publication_frontier")
    index_map = value.get("node_episode_index_map")
    if not isinstance(index_map, Mapping):
        failed.append("node_episode_index_map")
    elif episodes is not None:
        for node_id, indices in index_map.items():
            if not isinstance(node_id, str) or not isinstance(indices, Sequence):
                failed.append("node_episode_index_map")
                break
            if any(isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(episodes) for index in indices):
                failed.append("node_episode_index_map")
                break
    if edges is not None:
        for edge in edges:
            for endpoint in ("uuid", "source_node_uuid", "target_node_uuid"):
                if not isinstance(edge.get(endpoint), str) or not edge.get(endpoint):
                    failed.append(f"entity_edges.{endpoint}")
                    break

    unique_failed = tuple(dict.fromkeys(failed))
    if unique_failed:
        return ContinuationCheck(
            ContinuationStatus.UNKNOWN,
            "native continuation guard is not established",
            unique_failed,
        )
    return ContinuationCheck(
        ContinuationStatus.SUPPORTED_WITH_GUARD,
        "guarded tail is closed to deterministic bulk publication",
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _plain(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_plain(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_plain(item) for item in value), key=repr))
    return value


def continuation_k_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Compare every continuation-observable field, including IDs and order."""

    if validate_continuation_k(left).status != ContinuationStatus.SUPPORTED_WITH_GUARD:
        return False
    if validate_continuation_k(right).status != ContinuationStatus.SUPPORTED_WITH_GUARD:
        return False
    return all(_plain(left[field]) == _plain(right[field]) for field in REQUIRED_K_FIELDS)


def audit_continuation_source(source_root: str | Path) -> ContinuationCheck:
    """Bind the source-level T6b audit to the exact reviewed Graphiti files."""

    root = Path(source_root)
    failures: list[str] = []
    for relative, expected in PINNED_CONTINUATION_SOURCE_HASHES.items():
        path = root / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(f"hash:{relative}")
    if failures:
        return ContinuationCheck(
            ContinuationStatus.UNKNOWN,
            "pinned continuation source is missing or changed",
            tuple(failures),
        )
    return ContinuationCheck(
        ContinuationStatus.SUPPORTED_WITH_GUARD,
        "source hashes match the reviewed Graphiti continuation proof",
    )


__all__ = [
    "CONTINUATION_K_SCHEMA",
    "CONTINUATION_SEAM",
    "PINNED_CONTINUATION_SOURCE_HASHES",
    "REQUIRED_K_FIELDS",
    "ContinuationCheck",
    "ContinuationStatus",
    "audit_continuation_source",
    "continuation_k_equivalent",
    "validate_continuation_k",
]
