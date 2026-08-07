"""Opt-in evidence capture for correctness-search divergence diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from functools import wraps
from typing import Any, Awaitable, Callable, Iterable

from deterministic_search import stabilize_edge_search_query, stabilize_node_search_query
from instrumentation import current_episode_key


_FORENSIC_MARKER = "_membind_search_forensics"
_QUERY_EVENTS = "search_forensic_events"
_SOURCE_STATES = "source_state_events"
_SNAPSHOT_VECTORS = "_membind_source_state_vectors"
_CAPTURED_STATES = "_membind_captured_source_states"

SOURCE_STATE_QUERY = """
MATCH (n:Entity)
WHERE n.group_id = $group_id
RETURN n.name AS name,
       n.summary AS summary,
       labels(n) AS labels,
       n.name_embedding AS name_embedding
ORDER BY toLower(coalesce(n.name, '')) ASC,
         coalesce(n.name, '') ASC,
         toLower(coalesce(n.summary, '')) ASC,
         coalesce(n.summary, '') ASC,
         labels(n) ASC
"""

EDGE_SOURCE_STATE_QUERY = """
MATCH (source:Entity)-[e:RELATES_TO]->(target:Entity)
WHERE e.group_id = $group_id
RETURN e.uuid AS uuid,
       e.name AS name,
       e.fact AS fact,
       e.valid_at AS valid_at,
       e.invalid_at AS invalid_at,
       source.name AS source_name,
       target.name AS target_name,
       e.fact_embedding AS fact_embedding
ORDER BY toLower(coalesce(e.fact, '')) ASC,
         coalesce(e.fact, '') ASC,
         toLower(coalesce(e.name, '')) ASC,
         coalesce(e.name, '') ASC,
         coalesce(toString(e.valid_at), '') ASC,
         coalesce(toString(e.invalid_at), '') ASC,
         toLower(coalesce(source.name, '')) ASC,
         coalesce(source.name, '') ASC,
         toLower(coalesce(target.name, '')) ASC,
         coalesce(target.name, '') ASC
"""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _vector_hash(vector: Iterable[Any]) -> str:
    payload = b"".join(struct.pack("!d", float(value)) for value in vector)
    return hashlib.sha256(payload).hexdigest()


def _vector_norm(vector: Iterable[Any]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def _neo4j_cosine(left: list[float], right: list[float]) -> float | None:
    """Match Neo4j ``vector.similarity.cosine`` score semantics.

    Neo4j maps the conventional cosine range ``[-1, 1]`` to ``[0, 1]``
    using ``(1 + cosine) / 2``.  Keeping that mapping here is important for
    comparing the forensic ranking with the score threshold used by the
    backend query.
    """
    if len(left) != len(right) or not left:
        return None
    left_norm = _vector_norm(left)
    right_norm = _vector_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    raw_cosine = sum(x * y for x, y in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return (1.0 + raw_cosine) / 2.0


def _records(result: Any) -> list[Any]:
    records = getattr(result, "records", None)
    if records is not None:
        return list(records)
    if isinstance(result, tuple) and result:
        return list(result[0])
    if isinstance(result, list):
        return list(result)
    raise RuntimeError(f"unsupported query result: {type(result).__name__}")


def _record_dict(record: Any) -> dict[str, Any]:
    return record if isinstance(record, dict) else dict(record)


def _logical_entity(record: Any) -> dict[str, Any]:
    value = _record_dict(record)
    labels = sorted(str(label) for label in (value.get("labels") or []))
    return {
        "name": str(value.get("name") or ""),
        "summary": str(value.get("summary") or ""),
        "labels": labels,
    }


def _stable_text(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _logical_edge(
    record: Any,
    edge_lookup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = _record_dict(record)
    stored = (edge_lookup or {}).get(str(value.get("uuid") or ""), {})
    return {
        "name": _stable_text(value.get("name", stored.get("name"))),
        "fact": _stable_text(value.get("fact", stored.get("fact"))),
        "valid_at": _stable_text(value.get("valid_at", stored.get("valid_at"))),
        "invalid_at": _stable_text(
            value.get("invalid_at", stored.get("invalid_at"))
        ),
        "source_name": _stable_text(
            value.get("source_name", stored.get("source_name"))
        ),
        "target_name": _stable_text(
            value.get("target_name", stored.get("target_name"))
        ),
    }


def _logical_key(entity: dict[str, Any]) -> tuple[Any, ...]:
    name = entity["name"]
    summary = entity["summary"]
    labels = tuple(entity["labels"])
    return (name.casefold(), name, summary.casefold(), summary, labels)


def _edge_logical_key(edge: dict[str, Any]) -> tuple[Any, ...]:
    fields = (
        edge["fact"],
        edge["name"],
        edge["valid_at"],
        edge["invalid_at"],
        edge["source_name"],
        edge["target_name"],
    )
    return tuple(part for value in fields for part in (value.casefold(), value))


def _is_node_cosine_query(query: Any) -> bool:
    if not isinstance(query, str):
        return False
    lowered = query.casefold()
    return (
        "match (n:entity)" in lowered
        and "vector.similarity.cosine" in lowered
        and "n.name_embedding" in lowered
        and "limit $limit" in lowered
    )


def _is_edge_cosine_query(query: Any) -> bool:
    if not isinstance(query, str):
        return False
    lowered = query.casefold()
    return (
        "[e:relates_to" in lowered
        and "vector.similarity.cosine" in lowered
        and "e.fact_embedding" in lowered
        and "limit $limit" in lowered
    )


def _is_edge_fulltext_query(query: Any) -> bool:
    if not isinstance(query, str):
        return False
    lowered = query.casefold()
    return (
        "db.index.fulltext.queryrelationships" in lowered
        and "yield relationship as rel" in lowered
        and "[e:relates_to" in lowered
        and "limit $limit" in lowered
    )


def _query_kind(query: Any) -> str | None:
    if _is_node_cosine_query(query):
        return "node_cosine_search"
    if _is_edge_cosine_query(query):
        return "edge_cosine_search"
    if _is_edge_fulltext_query(query):
        return "edge_fulltext_search"
    return None


def _normalized_query(query: Any) -> Any:
    return stabilize_edge_search_query(stabilize_node_search_query(query))


def _parameter_summary(kwargs: dict[str, Any]) -> dict[str, Any]:
    vector = kwargs.get("search_vector")
    summary: dict[str, Any] = {
        "group_ids": [str(value) for value in (kwargs.get("group_ids") or [])],
        "limit": int(kwargs["limit"]) if kwargs.get("limit") is not None else None,
        "min_score": (
            float(kwargs["min_score"]) if kwargs.get("min_score") is not None else None
        ),
    }
    if isinstance(vector, (list, tuple)):
        summary["search_vector_sha256"] = _vector_hash(vector)
        summary["search_vector_dimension"] = len(vector)
        summary["search_vector_norm"] = _vector_norm(vector)
    query = kwargs.get("query")
    if isinstance(query, str):
        summary["query_sha256"] = hashlib.sha256(query.encode("utf-8")).hexdigest()
        summary["query_length"] = len(query)
    return summary


async def _capture_source_state(
    execute_query: Callable[..., Awaitable[Any]],
    *,
    group_id: str,
    run_id: str,
    source_sequence: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = await execute_query(
        SOURCE_STATE_QUERY,
        params={"group_id": group_id},
        routing_="r",
    )
    raw_entities: list[dict[str, Any]] = []
    sanitized: list[dict[str, Any]] = []
    for raw_record in _records(result):
        record = _record_dict(raw_record)
        logical = _logical_entity(record)
        vector = [float(value) for value in (record.get("name_embedding") or [])]
        raw_entities.append({**logical, "embedding": vector})
        sanitized.append(
            {
                **logical,
                "embedding_dimension": len(vector),
                "embedding_sha256": _vector_hash(vector),
                "embedding_norm": _vector_norm(vector),
            }
        )
    raw_entities.sort(key=_logical_key)
    sanitized.sort(key=_logical_key)
    state = {
        "episode_key": [run_id, source_sequence],
        "run_id": run_id,
        "source_sequence": source_sequence,
        "phase": "before_node_resolution",
        "group_id": group_id,
        "entity_count": len(sanitized),
        "entities": sanitized,
        "logical_graph_hash": _sha256_json(sanitized),
    }
    return state, raw_entities


async def _capture_edge_source_state(
    execute_query: Callable[..., Awaitable[Any]],
    *,
    group_id: str,
    run_id: str,
    source_sequence: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = await execute_query(
        EDGE_SOURCE_STATE_QUERY,
        params={"group_id": group_id},
        routing_="r",
    )
    raw_edges: list[dict[str, Any]] = []
    sanitized: list[dict[str, Any]] = []
    for raw_record in _records(result):
        record = _record_dict(raw_record)
        logical = _logical_edge(record)
        vector = [float(value) for value in (record.get("fact_embedding") or [])]
        raw_edges.append(
            {
                **logical,
                "uuid": str(record.get("uuid") or ""),
                "embedding": vector,
            }
        )
        sanitized.append(
            {
                **logical,
                "embedding_dimension": len(vector),
                "embedding_sha256": _vector_hash(vector),
                "embedding_norm": _vector_norm(vector),
            }
        )
    raw_edges.sort(key=_edge_logical_key)
    sanitized.sort(key=_edge_logical_key)
    state = {
        "episode_key": [run_id, source_sequence],
        "run_id": run_id,
        "source_sequence": source_sequence,
        "phase": "before_edge_resolution",
        "group_id": group_id,
        "edge_count": len(sanitized),
        "edges": sanitized,
        "logical_graph_hash": _sha256_json(sanitized),
    }
    return state, raw_edges


def _python_ranking(
    source_entities: list[dict[str, Any]],
    search_vector: Any,
    min_score: Any,
    limit: Any,
) -> list[dict[str, Any]]:
    if not isinstance(search_vector, (list, tuple)):
        return []
    query_vector = [float(value) for value in search_vector]
    threshold = float(min_score) if min_score is not None else 0.6
    cutoff = int(limit) if limit is not None else 10
    scored = []
    for entity in source_entities:
        score = _neo4j_cosine(query_vector, entity["embedding"])
        if score is not None and score > threshold:
            logical = {key: entity[key] for key in ("name", "summary", "labels")}
            scored.append((score, logical))
    scored.sort(key=lambda item: (-item[0], _logical_key(item[1])))
    return [
        {
            "rank": rank,
            "selected": rank <= cutoff,
            "score": score,
            **logical,
        }
        for rank, (score, logical) in enumerate(scored, start=1)
    ]


def _python_edge_ranking(
    source_edges: list[dict[str, Any]],
    search_vector: Any,
    min_score: Any,
    limit: Any,
) -> list[dict[str, Any]]:
    if not isinstance(search_vector, (list, tuple)):
        return []
    query_vector = [float(value) for value in search_vector]
    threshold = float(min_score) if min_score is not None else 0.6
    cutoff = int(limit) if limit is not None else 10
    scored = []
    for edge in source_edges:
        score = _neo4j_cosine(query_vector, edge["embedding"])
        if score is not None and score > threshold:
            logical = {
                key: edge[key]
                for key in (
                    "name",
                    "fact",
                    "valid_at",
                    "invalid_at",
                    "source_name",
                    "target_name",
                )
            }
            scored.append((score, logical))
    scored.sort(key=lambda item: (-item[0], _edge_logical_key(item[1])))
    return [
        {
            "rank": rank,
            "selected": rank <= cutoff,
            "score": score,
            **logical,
        }
        for rank, (score, logical) in enumerate(scored, start=1)
    ]


def install_search_forensics(
    driver: Any,
    *,
    snapshot_source_sequences: set[int] | None = None,
) -> bool:
    """Capture node and edge search inputs/results without changing semantics."""

    if getattr(driver, _FORENSIC_MARKER, False):
        return False

    current: Callable[..., Awaitable[Any]] = driver.execute_query
    target_sequences = {int(value) for value in (snapshot_source_sequences or set())}
    query_events: list[dict[str, Any]] = []
    source_states: list[dict[str, Any]] = []
    source_vectors: dict[tuple[str, int, str, str], list[dict[str, Any]]] = {}
    captured_states: set[tuple[str, int, str, str]] = set()

    @wraps(current)
    async def forensic_execute_query(
        cypher_query_: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        kind = _query_kind(cypher_query_)
        if kind is None:
            return await current(cypher_query_, *args, **kwargs)

        episode_key = current_episode_key()
        normalized_query = _normalized_query(cypher_query_)
        event: dict[str, Any] = {
            "episode_key": list(episode_key) if episode_key is not None else None,
            "kind": kind,
            "normalized_query": normalized_query,
            "normalized_query_sha256": hashlib.sha256(
                str(normalized_query).encode("utf-8")
            ).hexdigest(),
            "parameters": _parameter_summary(kwargs),
        }

        group_ids = [str(value) for value in (kwargs.get("group_ids") or [])]
        state_type = "node" if kind == "node_cosine_search" else "edge"
        state_key: tuple[str, int, str, str] | None = None
        if episode_key is not None and len(group_ids) == 1:
            state_key = (
                str(episode_key[0]),
                int(episode_key[1]),
                group_ids[0],
                state_type,
            )
        if (
            state_key is not None
            and state_key[1] in target_sequences
            and state_key not in captured_states
        ):
            captured_states.add(state_key)
            try:
                capture = (
                    _capture_source_state
                    if state_type == "node"
                    else _capture_edge_source_state
                )
                state, raw_values = await capture(
                    current,
                    group_id=state_key[2],
                    run_id=state_key[0],
                    source_sequence=state_key[1],
                )
                source_states.append(state)
                source_vectors[state_key] = raw_values
            except Exception as exc:
                source_states.append(
                    {
                        "episode_key": [state_key[0], state_key[1]],
                        "run_id": state_key[0],
                        "source_sequence": state_key[1],
                        "phase": (
                            "before_node_resolution"
                            if state_type == "node"
                            else "before_edge_resolution"
                        ),
                        "group_id": state_key[2],
                        "error": repr(exc),
                    }
                )

        if (
            kind == "node_cosine_search"
            and state_key is not None
            and state_key in source_vectors
        ):
            event["python_ranked"] = _python_ranking(
                source_vectors[state_key],
                kwargs.get("search_vector"),
                kwargs.get("min_score"),
                kwargs.get("limit"),
            )
        elif (
            kind == "edge_cosine_search"
            and state_key is not None
            and state_key in source_vectors
        ):
            event["python_ranked"] = _python_edge_ranking(
                source_vectors[state_key],
                kwargs.get("search_vector"),
                kwargs.get("min_score"),
                kwargs.get("limit"),
            )

        try:
            result = await current(cypher_query_, *args, **kwargs)
            if kind == "node_cosine_search":
                candidates = [_logical_entity(record) for record in _records(result)]
            else:
                edge_lookup = {
                    edge["uuid"]: edge
                    for edge in (
                        source_vectors.get(state_key, []) if state_key is not None else []
                    )
                    if edge.get("uuid")
                }
                candidates = [
                    _logical_edge(record, edge_lookup) for record in _records(result)
                ]
                event["rrf_source_membership"] = list(candidates)
            event["backend_candidates"] = candidates
            event["backend_candidate_count"] = len(event["backend_candidates"])
            return result
        except Exception as exc:
            event["error"] = repr(exc)
            raise
        finally:
            query_events.append(event)

    driver.execute_query = forensic_execute_query
    setattr(driver, _QUERY_EVENTS, query_events)
    setattr(driver, _SOURCE_STATES, source_states)
    setattr(driver, _SNAPSHOT_VECTORS, source_vectors)
    setattr(driver, _CAPTURED_STATES, captured_states)
    setattr(driver, _FORENSIC_MARKER, True)
    return True


def search_forensic_payload(driver: Any) -> dict[str, Any]:
    """Return only JSON-safe diagnostic data; raw embedding vectors stay private."""

    return {
        "query_events": list(getattr(driver, _QUERY_EVENTS, []) or []),
        "source_states": list(getattr(driver, _SOURCE_STATES, []) or []),
    }
