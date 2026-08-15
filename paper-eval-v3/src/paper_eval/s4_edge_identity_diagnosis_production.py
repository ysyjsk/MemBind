"""Production-facing, hash-only adapters for the S4 edge diagnosis.

Imports of Graphiti classes remain lazy so the complete adapter can be tested
offline.  This module provides no cleanup or retry path.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256
from .s4_edge_identity_diagnosis import diagnose_candidate_partition
from .s4_edge_identity_dry_run import EdgeCandidateBarrier


NODE_SNAPSHOT_QUERY = """
/* D2_NODE_SNAPSHOT */
MATCH (n)
WHERE n.group_id = $namespace
RETURN n.uuid AS uuid, labels(n) AS labels, properties(n) AS properties
ORDER BY n.uuid
"""

RELATIONSHIP_SNAPSHOT_QUERY = """
/* D2_RELATIONSHIP_SNAPSHOT */
MATCH (source)-[relationship]->(target)
WHERE source.group_id = $namespace AND target.group_id = $namespace
RETURN source.uuid AS source_uuid,
       target.uuid AS target_uuid,
       type(relationship) AS type,
       properties(relationship) AS properties
ORDER BY source.uuid, target.uuid, type(relationship), relationship.uuid
"""


class DiagnosisProductionError(RuntimeError):
    """The production adapter could not prove its read-only evidence."""


def _tagged(prompt: str, opening: str, closing: str) -> str:
    if (
        not isinstance(prompt, str)
        or prompt.count(opening) != 1
        or prompt.count(closing) != 1
    ):
        raise DiagnosisProductionError("private prompt tag structure drift")
    start = prompt.index(opening) + len(opening)
    end = prompt.index(closing, start)
    return prompt[start:end].strip()


def _private_prompt_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").split("\n")
        for line in lines:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise DiagnosisProductionError("private prompt record is not an object")
            records.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DiagnosisProductionError("private prompt cache is unreadable") from error
    return records


def persisted_evidence_diagnosis(
    *,
    episodes: Sequence[Any],
    prompt_cache_path: Path,
    canonical_graph_path: Path,
    source_sequence: int,
) -> dict[str, Any]:
    """Recompute D1 locally and return only fixed counts and SHA256 values."""

    if source_sequence != 7 or len(episodes) != 49:
        raise DiagnosisProductionError("persisted diagnosis source binding drift")
    body = _value(episodes[source_sequence], "body")
    if not isinstance(body, str):
        raise DiagnosisProductionError("source-7 body is unavailable")
    records = _private_prompt_records(prompt_cache_path)

    extraction_records = []
    for record in records:
        parts = record.get("prompt_parts")
        if not isinstance(parts, Mapping):
            continue
        config = parts.get("decoding_config")
        prompt = parts.get("user_prompt")
        if (
            isinstance(config, Mapping)
            and config.get("prompt_name") == "extract_edges.edge"
            and isinstance(prompt, str)
            and body in prompt
        ):
            extraction_records.append(record)
    if len(extraction_records) != 1:
        raise DiagnosisProductionError("source-7 edge extraction record is not unique")
    parsed = extraction_records[0].get("parsed_response")
    edges = parsed.get("edges") if isinstance(parsed, Mapping) else None
    if not isinstance(edges, list) or len(edges) != 10:
        raise DiagnosisProductionError("source-7 extracted-edge count drift")
    extracted_facts = []
    for edge in edges:
        fact = edge.get("fact") if isinstance(edge, Mapping) else None
        if not isinstance(fact, str) or not fact:
            raise DiagnosisProductionError("source-7 extracted fact is malformed")
        extracted_facts.append(fact)
    if len(set(extracted_facts)) != 10:
        raise DiagnosisProductionError("source-7 extracted facts are not unique")

    resolution_records: list[tuple[list[dict[str, Any]], Mapping[str, Any]]] = []
    for record in records:
        parts = record.get("prompt_parts")
        if not isinstance(parts, Mapping):
            continue
        config = parts.get("decoding_config")
        prompt = parts.get("user_prompt")
        if (
            not isinstance(config, Mapping)
            or config.get("prompt_name") != "dedupe_edges.resolve_edge"
            or not isinstance(prompt, str)
        ):
            continue
        new_fact = _tagged(prompt, "<NEW FACT>", "</NEW FACT>")
        if new_fact not in extracted_facts:
            continue
        try:
            candidates = ast.literal_eval(
                _tagged(
                    prompt,
                    "<FACT INVALIDATION CANDIDATES>",
                    "</FACT INVALIDATION CANDIDATES>",
                )
            )
        except (SyntaxError, ValueError) as error:
            raise DiagnosisProductionError(
                "source-7 invalidation candidates are malformed"
            ) from error
        response = record.get("parsed_response")
        if not isinstance(candidates, list) or not isinstance(response, Mapping):
            raise DiagnosisProductionError("source-7 resolution record drift")
        resolution_records.append((candidates, response))
    if len(resolution_records) != 10:
        raise DiagnosisProductionError("source-7 edge resolution coverage drift")

    ambiguous_hashes: list[str] = []
    for candidates, response in resolution_records:
        if len(candidates) != 10:
            raise DiagnosisProductionError("source-7 invalidation width drift")
        fact_hashes = []
        for expected_index, candidate in enumerate(candidates):
            if (
                not isinstance(candidate, Mapping)
                or candidate.get("idx") != expected_index
                or not isinstance(candidate.get("fact"), str)
            ):
                raise DiagnosisProductionError("source-7 candidate record drift")
            fact_hashes.append(
                hashlib.sha256(candidate["fact"].encode("utf-8")).hexdigest()
            )
        multiplicities: dict[str, int] = {}
        for fact_hash in fact_hashes:
            multiplicities[fact_hash] = multiplicities.get(fact_hash, 0) + 1
        duplicated = [
            fact_hash for fact_hash, count in multiplicities.items() if count > 1
        ]
        if duplicated:
            if len(duplicated) != 1 or multiplicities[duplicated[0]] != 2:
                raise DiagnosisProductionError("source-7 duplicate multiplicity drift")
            ambiguous_hashes.append(duplicated[0])
        if response.get("duplicate_facts") != [] or response.get(
            "contradicted_facts"
        ) != []:
            raise DiagnosisProductionError("source-7 cached decision drift")
    if len(ambiguous_hashes) != 9 or len(set(ambiguous_hashes)) != 1:
        raise DiagnosisProductionError("source-7 duplicate fact class drift")
    duplicate_fact_sha256 = ambiguous_hashes[0]

    try:
        graph = json.loads(Path(canonical_graph_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DiagnosisProductionError("capture canonical graph is unreadable") from error
    graph_edges = graph.get("edges") if isinstance(graph, Mapping) else None
    if not isinstance(graph_edges, list):
        raise DiagnosisProductionError("capture canonical graph edge shape drift")
    matching = [
        edge
        for edge in graph_edges
        if isinstance(edge, Mapping)
        and isinstance(edge.get("fact"), str)
        and hashlib.sha256(edge["fact"].encode("utf-8")).hexdigest()
        == duplicate_fact_sha256
    ]
    endpoint_pairs = {
        (edge.get("source_entity_key"), edge.get("target_entity_key"))
        for edge in matching
    }
    if len(matching) != 2 or len(endpoint_pairs) != 2:
        raise DiagnosisProductionError("capture duplicate edge endpoint evidence drift")
    return {
        "classification": (
            "NON_INJECTIVE_FACT_ONLY_EDGE_CANDIDATE_IDENTITY_CONFIRMED"
        ),
        "source_sequence": 7,
        "edge_extraction_record_count": 1,
        "extracted_edge_count": 10,
        "edge_resolution_prompt_count": 10,
        "ambiguous_prompt_count": 9,
        "duplicate_fact_sha256": duplicate_fact_sha256,
        "duplicate_fact_multiplicity": 2,
        "matching_capture_graph_edge_count": 2,
        "matching_edges_directed_endpoints_distinct": True,
        "capture_replay_bijection_proved": False,
    }


def _value(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    data = getattr(value, "data", None)
    if callable(data):
        selected = data()
        if isinstance(selected, Mapping):
            return dict(selected)
    try:
        return dict(value)
    except (TypeError, ValueError) as error:
        raise DiagnosisProductionError("Neo4j record is not mapping-like") from error


def _records(result: Any) -> list[dict[str, Any]]:
    selected = getattr(result, "records", None)
    if selected is None and isinstance(result, tuple):
        selected = result[0]
    if selected is None:
        try:
            selected, _, _ = result
        except (TypeError, ValueError) as error:
            raise DiagnosisProductionError("Neo4j result shape drift") from error
    return [_record(value) for value in selected]


def _snapshot_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _snapshot_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_snapshot_value(child) for child in value]
    iso_format = getattr(value, "iso_format", None)
    if callable(iso_format):
        return str(iso_format())
    return str(value)


def build_episode_manifest(
    episodes: Sequence[Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    """Bind every frozen episode while retaining body hashes only."""

    if len(episodes) != 49:
        raise DiagnosisProductionError("D2 requires the frozen 49-episode history")
    manifest: dict[str, dict[str, Any]] = {}
    for expected_sequence, episode in enumerate(episodes):
        sequence = _value(episode, "source_sequence")
        name = _value(episode, "name")
        source_hash = _value(episode, "source_hash")
        body = _value(episode, "body")
        if (
            sequence != expected_sequence
            or not isinstance(name, str)
            or not name
            or name in manifest
            or not isinstance(source_hash, str)
            or len(source_hash) != 64
            or any(character not in "0123456789abcdef" for character in source_hash)
            or not isinstance(body, str)
        ):
            raise DiagnosisProductionError("frozen episode manifest drift")
        manifest[name] = {
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_hash": source_hash,
            "source_sequence": sequence,
        }
    return manifest, payload_sha256(manifest)


async def namespace_snapshot(driver: Any, namespace: str) -> dict[str, Any]:
    """Hash a complete namespace snapshot without returning graph contents."""

    if not isinstance(namespace, str) or not namespace.startswith("pev3-s4-"):
        raise DiagnosisProductionError("D2 namespace escaped the S4 prefix")
    node_rows = _records(
        await driver.execute_query(
            NODE_SNAPSHOT_QUERY, namespace=namespace, routing_="r"
        )
    )
    relationship_rows = _records(
        await driver.execute_query(
            RELATIONSHIP_SNAPSHOT_QUERY,
            namespace=namespace,
            routing_="r",
        )
    )
    canonical_nodes = sorted(
        (_snapshot_value(row) for row in node_rows),
        key=payload_sha256,
    )
    canonical_relationships = sorted(
        (_snapshot_value(row) for row in relationship_rows),
        key=payload_sha256,
    )
    episode_names: list[str] = []
    for row in node_rows:
        labels = row.get("labels")
        properties = row.get("properties")
        if (
            isinstance(labels, Sequence)
            and not isinstance(labels, (str, bytes))
            and "Episodic" in labels
            and isinstance(properties, Mapping)
            and isinstance(properties.get("name"), str)
        ):
            episode_names.append(properties["name"])
    return {
        "canonical_snapshot_sha256": payload_sha256(
            {
                "nodes": canonical_nodes,
                "relationships": canonical_relationships,
            }
        ),
        "episode_count": len(episode_names),
        "episode_names_sha256": payload_sha256(sorted(episode_names)),
        "node_count": len(node_rows),
        "relationship_count": len(relationship_rows),
    }


def _edge_mapping(edge: Any) -> dict[str, Any]:
    fields = (
        "attributes",
        "created_at",
        "episodes",
        "expired_at",
        "fact",
        "fact_embedding",
        "group_id",
        "invalid_at",
        "name",
        "reference_time",
        "source_node_uuid",
        "target_node_uuid",
        "uuid",
        "valid_at",
    )
    return {name: _value(edge, name) for name in fields}


def _exact_join(
    *,
    requested: set[str],
    returned: Sequence[Any],
    namespace: str,
    label: str,
) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for value in returned:
        uuid = _value(value, "uuid")
        if (
            not isinstance(uuid, str)
            or uuid in selected
            or _value(value, "group_id") != namespace
        ):
            raise DiagnosisProductionError(f"{label} join is duplicate or foreign")
        selected[uuid] = value
    if set(selected) != requested:
        raise DiagnosisProductionError(f"{label} join is incomplete")
    return selected


async def candidate_call_diagnoses(
    *,
    records: Sequence[Mapping[str, Any]],
    driver: Any,
    namespace: str,
    episodes: Sequence[Any],
    entity_loader: Callable[..., Awaitable[Sequence[Any]]] | None = None,
    episode_loader: Callable[..., Awaitable[Sequence[Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve all UUID joins in one batch, then emit hash-only call evidence."""

    if len(records) != 10:
        raise DiagnosisProductionError("D2 requires exactly ten edge-call records")
    manifest, _ = build_episode_manifest(episodes)
    candidate_edges = [
        edge
        for record in records
        for partition in ("related_edges", "invalidation_edges")
        for edge in record.get(partition, ())
    ]
    endpoint_ids = {
        str(endpoint)
        for edge in candidate_edges
        for endpoint in (
            _value(edge, "source_node_uuid"),
            _value(edge, "target_node_uuid"),
        )
        if isinstance(endpoint, str) and endpoint
    }
    provenance_ids = {
        str(uuid)
        for edge in candidate_edges
        for uuid in (_value(edge, "episodes") or [])
        if isinstance(uuid, str) and uuid
    }
    if not endpoint_ids or not provenance_ids:
        raise DiagnosisProductionError("candidate endpoint/provenance evidence is empty")

    if entity_loader is None or episode_loader is None:
        from graphiti_core.nodes import EntityNode, EpisodicNode

        entity_loader = EntityNode.get_by_uuids
        episode_loader = EpisodicNode.get_by_uuids
    entities = await entity_loader(
        driver, sorted(endpoint_ids), group_id=namespace
    )
    persisted_episodes = await episode_loader(driver, sorted(provenance_ids))
    entity_by_uuid = _exact_join(
        requested=endpoint_ids,
        returned=entities,
        namespace=namespace,
        label="endpoint",
    )
    episode_by_uuid = _exact_join(
        requested=provenance_ids,
        returned=persisted_episodes,
        namespace=namespace,
        label="provenance",
    )
    endpoint_lookup = {
        uuid: {
            "normalized_name": _value(node, "name"),
            "labels": _value(node, "labels") or [],
            "summary": _value(node, "summary") or "",
            "attributes": _value(node, "attributes") or {},
        }
        for uuid, node in entity_by_uuid.items()
    }
    provenance_lookup: dict[str, dict[str, Any]] = {}
    for uuid, episode in episode_by_uuid.items():
        name = _value(episode, "name")
        content = _value(episode, "content")
        expected = manifest.get(name) if isinstance(name, str) else None
        if (
            expected is None
            or not isinstance(content, str)
            or hashlib.sha256(content.encode("utf-8")).hexdigest()
            != expected["body_sha256"]
        ):
            raise DiagnosisProductionError("provenance does not match frozen source")
        provenance_lookup[uuid] = {
            "source_hash": expected["source_hash"],
            "source_sequence": expected["source_sequence"],
        }

    calls: list[dict[str, Any]] = []
    correlations: set[str] = set()
    for record in records:
        extracted = record.get("extracted_edge")
        fact = _value(extracted, "fact")
        relation = _value(extracted, "name") or ""
        if not isinstance(fact, str) or not fact:
            raise DiagnosisProductionError("new-edge call identity is incomplete")
        correlation = hashlib.sha256(
            json.dumps(
                {"fact": fact, "relation": relation},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if correlation in correlations:
            raise DiagnosisProductionError("new-edge call identity is not unique")
        correlations.add(correlation)
        partitions = {
            "related": diagnose_candidate_partition(
                candidates=[_edge_mapping(edge) for edge in record.get("related_edges", ())],
                endpoint_by_uuid=endpoint_lookup,
                provenance_by_uuid=provenance_lookup,
            ),
            "invalidation": diagnose_candidate_partition(
                candidates=[
                    _edge_mapping(edge)
                    for edge in record.get("invalidation_edges", ())
                ],
                endpoint_by_uuid=endpoint_lookup,
                provenance_by_uuid=provenance_lookup,
            ),
        }
        calls.append(
            {
                "call_correlation_sha256": correlation,
                "partitions": partitions,
            }
        )
    return sorted(calls, key=lambda value: value["call_correlation_sha256"])


def validate_d2_runtime(runtime: Any) -> Any:
    """Reject construction-time schema scheduling and driver-reference escape."""

    graph = getattr(runtime, "graph", None)
    driver = getattr(graph, "driver", None)
    clients = getattr(graph, "clients", None)
    if graph is None or driver is None or clients is None:
        raise DiagnosisProductionError("D2 Graphiti runtime is incomplete")
    if getattr(driver, "_init_task", None) is not None:
        raise DiagnosisProductionError("D2 driver scheduled schema initialization")
    if getattr(clients, "driver", None) is not driver:
        raise DiagnosisProductionError("D2 Graphiti driver reference escaped")
    return driver


@contextmanager
def install_edge_resolution_hook(
    edge_operations_module: Any,
    barrier: EdgeCandidateBarrier,
):
    """Observe exact candidates at Graphiti's pre-prompt boundary."""

    original = getattr(edge_operations_module, "resolve_extracted_edge", None)
    if not callable(original):
        raise DiagnosisProductionError("Graphiti edge-resolution hook is unavailable")

    async def observe(
        llm_client: Any,
        extracted_edge: Any,
        related_edges: Sequence[Any],
        existing_edges: Sequence[Any],
        episode: Any,
        edge_type_candidates: Any = None,
    ) -> Any:
        del llm_client, episode, edge_type_candidates
        return await barrier.observe(
            extracted_edge=extracted_edge,
            related_edges=related_edges,
            invalidation_edges=existing_edges,
            original=original,
        )

    setattr(edge_operations_module, "resolve_extracted_edge", observe)
    try:
        yield
    finally:
        setattr(edge_operations_module, "resolve_extracted_edge", original)
