"""Live Graphiti graph export and retrieval guardrail adapters."""

from __future__ import annotations

from typing import Any, Iterable

from canonicalize_graph import canonical_graph_hash, canonicalize_graph
from dataset import Episode
from retrieval_eval import retrieval_metrics


ENTITY_QUERY = """
MATCH (n:Entity)
WHERE n.group_id = $group_id
RETURN n.uuid AS uuid,
       n.group_id AS group_id,
       n.name AS name,
       labels(n) AS labels,
       n.summary AS summary,
       properties(n) AS attributes
"""

EDGE_QUERY = """
MATCH (source:Entity)-[edge:RELATES_TO]->(target:Entity)
WHERE edge.group_id = $group_id
RETURN edge.uuid AS uuid,
       edge.group_id AS group_id,
       source.name AS source_name,
       target.name AS target_name,
       edge.name AS relation_type,
       edge.fact AS fact,
       edge.valid_at AS valid_at,
       edge.invalid_at AS invalid_at,
       edge.expired_at AS expired_at,
       edge.episodes AS episode_uuids,
       properties(edge) AS attributes
"""

EPISODE_QUERY = """
MATCH (ep:Episodic)
WHERE ep.group_id = $group_id
RETURN ep.uuid AS uuid, ep.name AS name
"""

_ENTITY_CORE = {"uuid", "group_id", "name", "labels", "summary", "name_embedding"}
_EDGE_CORE = {
    "uuid",
    "group_id",
    "name",
    "fact",
    "episodes",
    "valid_at",
    "invalid_at",
    "expired_at",
    "created_at",
    "fact_embedding",
    "reference_time",
}


async def export_canonical_graph(
    graphiti: Any,
    episodes: list[Episode],
    group_id: str,
) -> dict[str, Any]:
    driver = graphiti.driver
    params = {"group_id": group_id}
    entity_rows = _records(await driver.execute_query(ENTITY_QUERY, params=params))
    edge_rows = _records(await driver.execute_query(EDGE_QUERY, params=params))
    episode_rows = _records(await driver.execute_query(EPISODE_QUERY, params=params))

    expected_by_name = {episode.name: episode for episode in episodes}
    persisted_by_uuid: dict[str, Episode] = {}
    episode_mappings = []
    for raw_record in episode_rows:
        record = _record_dict(raw_record)
        expected = expected_by_name.get(str(record.get("name") or ""))
        if expected is None:
            episode_mappings.append(
                {
                    "source_sequence": None,
                    "source_hash": f"unexpected:{record.get('name')}",
                    "session_id": None,
                }
            )
            continue
        persisted_by_uuid[str(record.get("uuid") or "")] = expected
        episode_mappings.append(
            {
                "source_sequence": expected.source_sequence,
                "source_hash": expected.source_hash,
                "session_id": expected.session_id,
            }
        )

    entities = []
    for raw_record in entity_rows:
        record = _record_dict(raw_record)
        attributes = _semantic_attributes(record.get("attributes"), _ENTITY_CORE)
        entities.append(
            {
                "group_id": record.get("group_id"),
                "name": record.get("name"),
                "labels": record.get("labels") or [],
                "summary": record.get("summary") or "",
                "attributes": attributes,
            }
        )

    edges = []
    for raw_record in edge_rows:
        record = _record_dict(raw_record)
        source_sequences = sorted(
            {
                persisted_by_uuid[str(uuid)].source_sequence
                for uuid in (record.get("episode_uuids") or [])
                if str(uuid) in persisted_by_uuid
            }
        )
        if len(source_sequences) == 1:
            source_mapping: int | list[int] | None = source_sequences[0]
        else:
            source_mapping = source_sequences or None
        edges.append(
            {
                "source_entity_key": record.get("source_name"),
                "target_entity_key": record.get("target_name"),
                "relation_type": record.get("relation_type"),
                "fact": record.get("fact"),
                "valid_at": _plain(record.get("valid_at")),
                "invalid_at": _plain(record.get("invalid_at")),
                "expired_at": _plain(record.get("expired_at")),
                "attributes": _semantic_attributes(record.get("attributes"), _EDGE_CORE),
                "source_episode_sequence": source_mapping,
            }
        )

    canonical = canonicalize_graph(
        {"entities": entities, "edges": edges, "episodes": episode_mappings}
    )
    canonical["canonical_graph_hash"] = canonical_graph_hash(canonical)
    return canonical


async def evaluate_retrieval(
    graphiti: Any,
    instance: dict[str, Any],
    episodes: list[Episode],
    reference_episode_ids: list[str] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    if top_k != 10:
        raise ValueError("the frozen protocol requires top_k=10")
    group_id = episodes[0].group_id if episodes else str(instance["question_id"])
    episode_rows = _records(
        await graphiti.driver.execute_query(EPISODE_QUERY, params={"group_id": group_id})
    )
    expected_by_name = {episode.name: episode for episode in episodes}
    session_by_uuid = {}
    for raw_record in episode_rows:
        record = _record_dict(raw_record)
        expected = expected_by_name.get(str(record.get("name") or ""))
        if expected is not None:
            session_by_uuid[str(record.get("uuid") or "")] = expected.session_id

    query = str(instance["question"])
    results = await graphiti.search(query, group_ids=[group_id], num_results=top_k)
    retrieved_episode_ids: list[str] = []
    seen: set[str] = set()
    serialized_results = []
    for rank, result in enumerate(results, start=1):
        episode_uuids = _value(result, "episodes") or []
        result_session_ids = []
        for episode_uuid in episode_uuids:
            session_id = session_by_uuid.get(str(episode_uuid))
            if session_id is None:
                continue
            result_session_ids.append(session_id)
            if session_id not in seen:
                seen.add(session_id)
                retrieved_episode_ids.append(session_id)
        serialized_results.append(
            {
                "rank": rank,
                "edge_uuid": _value(result, "uuid"),
                "fact": _value(result, "fact"),
                "source_episode_ids": result_session_ids,
            }
        )

    metrics = retrieval_metrics(
        retrieved_episode_ids,
        instance.get("answer_session_ids") or [],
        reference_episode_ids=reference_episode_ids,
    )
    return {
        "question_id": str(instance["question_id"]) if "question_id" in instance else group_id,
        "query": query,
        "top_k": top_k,
        "gold_episode_ids": [str(value) for value in instance.get("answer_session_ids") or []],
        "retrieved_episode_ids": retrieved_episode_ids,
        "results": serialized_results,
        "metrics": metrics,
    }


def _semantic_attributes(value: Any, excluded: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _plain(child)
        for key, child in value.items()
        if str(key).lower() not in excluded and not str(key).lower().endswith("_uuid")
    }


def _records(result: Any) -> Iterable[Any]:
    records = getattr(result, "records", None)
    if records is not None:
        return records
    if isinstance(result, tuple) and result:
        return result[0]
    if isinstance(result, list):
        return result
    raise RuntimeError(f"unsupported query result: {type(result).__name__}")


def _record_dict(record: Any) -> dict[str, Any]:
    return record if isinstance(record, dict) else dict(record)


def _value(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
