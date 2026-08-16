"""Sanitized, read-only post-run observation for Native S5 attempts.

The Native scheduler journal proves that calls returned and publication events
were durably recorded.  It cannot prove what Neo4j ultimately contains.  This
module independently observes the completed namespace and emits only counts,
classifications, and hashes; graph UUIDs, names, facts, and namespace values
never enter the public artifact.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any

from .artifacts import payload_sha256


SCHEMA = "membind.paper-eval-v3.s5-native-post-observation.v1"
EPISODIC_OBSERVATION = "EPISODIC"
ENTITY_OBSERVATION = "ENTITY"
RELATES_TO_OBSERVATION = "RELATES_TO"

_OBSERVATIONS = (
    EPISODIC_OBSERVATION,
    ENTITY_OBSERVATION,
    RELATES_TO_OBSERVATION,
)
_METHODS = {"A0", "P*", "M*"}
_METHOD_SLUG = {"A0": "a0", "P*": "p-star", "M*": "mstar"}
_RUN_ID = re.compile(r"^s5-(?:a0|p-star|mstar)-[0-9]{8}-[0-9]{3}$")
_EXPECTED_EPISODES = 49
_HISTORY_ID = "07741c45"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRIVATE_FIELDS = {
    "answer",
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "fact",
    "group_id",
    "messages",
    "name",
    "namespace",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "record_id",
    "request",
    "response",
    "run_id",
    "secret",
    "token",
}
_COUNT_FIELDS = (
    "expected_source_count",
    "durable_publication_count",
    "episodic_count",
    "lost_episodic_count",
    "duplicate_episodic_count",
    "unexpected_episodic_count",
    "entity_count",
    "relates_to_count",
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
)
_VIOLATION_FIELDS = (
    "entity_namespace_escape_count",
    "relation_namespace_escape_count",
    "endpoint_escape_count",
    "provenance_dangling_count",
    "provenance_cross_namespace_count",
    "valid_invalid_reversal_count",
)
_ARTIFACT_FIELDS = {
    "schema_version",
    "method",
    "status",
    "run_id_sha256",
    "namespace_sha256",
    "execution_identity_sha256",
    "source_manifest_sha256",
    "durable_publication_manifest_sha256",
    "counts",
    "source_classifications",
    "per_source_violation_counts",
    "violation_classifications",
    "global_violation_total",
    "observation_sha256",
}


class S5PostObservationError(ValueError):
    """The independent namespace observation is incomplete or inconsistent."""


def _fail(code: str) -> S5PostObservationError:
    return S5PostObservationError(code)


def _identity_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_observation_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _source_inventory(
    expected_sources: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    if isinstance(expected_sources, (str, bytes)) or not isinstance(
        expected_sources, Sequence
    ):
        raise _fail("expected_source_count_invalid")
    selected = tuple(deepcopy(dict(row)) for row in expected_sources)
    if len(selected) != _EXPECTED_EPISODES:
        raise _fail("expected_source_count_invalid")
    for index, row in enumerate(selected):
        if (
            set(row) != {"source_sequence", "source_sha256"}
            or row.get("source_sequence") != index
            or not isinstance(row.get("source_sha256"), str)
            or _SHA256.fullmatch(str(row["source_sha256"])) is None
        ):
            raise _fail("expected_source_inventory_invalid")
    return selected


def _source_map(
    rows: Sequence[Mapping[str, object]], *, code: str
) -> Counter[tuple[int, str]]:
    observed: Counter[tuple[int, str]] = Counter()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise _fail(code)
        source = raw.get("source_sequence")
        digest = raw.get("source_sha256")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
        ):
            raise _fail(code)
        observed[(source, digest)] += 1
    return observed


def _require_exact_coverage(
    *,
    rows: Sequence[Mapping[str, object]],
    expected: tuple[dict[str, object], ...],
    code: str,
) -> None:
    expected_counter = Counter(
        (int(row["source_sequence"]), str(row["source_sha256"]))
        for row in expected
    )
    if _source_map(rows, code=code) != expected_counter:
        raise _fail(code)


def _records(result: object) -> list[dict[str, object]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if not isinstance(values, list):
        raise _fail("query_result_invalid")
    try:
        return [dict(row) for row in values]
    except (TypeError, ValueError):
        raise _fail("query_result_invalid") from None


class S5GraphitiPostQueryExecutor:
    """Execute the three bounded Graphiti/Neo4j observations.

    Episodic nodes do not persist the S5 source hash.  The adapter therefore
    maps the frozen episode-name convention back to the independently supplied
    source manifest before the sanitizer discards names and graph identifiers.
    """

    def __init__(self, *, expected_sources: Sequence[Mapping[str, object]]) -> None:
        inventory = _source_inventory(expected_sources)
        self._source_by_name = {
            f"{_HISTORY_ID}::episode::{int(row['source_sequence']):04d}": row
            for row in inventory
        }

    async def __call__(
        self, driver: object, observation: str, namespace: str
    ) -> list[dict[str, object]]:
        execute_query = getattr(driver, "execute_query", None)
        if not callable(execute_query):
            raise _fail("query_driver_invalid")
        if observation == EPISODIC_OBSERVATION:
            query = """
            MATCH (episode:Episodic)
            WHERE episode.group_id = $namespace
            RETURN episode.uuid AS record_id,
                   episode.name AS name,
                   episode.group_id AS group_id
            """
        elif observation == ENTITY_OBSERVATION:
            query = """
            MATCH (entity:Entity)
            WHERE entity.group_id = $namespace
               OR EXISTS {
                   MATCH (left:Entity)-[relation:RELATES_TO]->(right:Entity)
                   WHERE (left = entity OR right = entity)
                     AND (relation.group_id = $namespace
                          OR left.group_id = $namespace
                          OR right.group_id = $namespace)
               }
            RETURN entity.uuid AS record_id,
                   entity.group_id AS group_id
            """
        elif observation == RELATES_TO_OBSERVATION:
            query = """
            MATCH (source:Entity)-[relation:RELATES_TO]->(target:Entity)
            WHERE relation.group_id = $namespace
               OR source.group_id = $namespace
               OR target.group_id = $namespace
            OPTIONAL MATCH (episode:Episodic)
            WHERE episode.uuid IN coalesce(relation.episodes, [])
            WITH source, relation, target,
                 [item IN collect(episode) WHERE item IS NOT NULL |
                    {episode_id: item.uuid, group_id: item.group_id}]
                    AS resolved_provenance
            RETURN relation.uuid AS record_id,
                   relation.group_id AS group_id,
                   source.uuid AS source_entity_id,
                   target.uuid AS target_entity_id,
                   coalesce(relation.episodes, []) AS provenance_episode_ids,
                   resolved_provenance,
                   relation.valid_at AS valid_at,
                   relation.invalid_at AS invalid_at,
                   relation.expired_at AS expired_at
            """
        else:
            raise _fail("observation_type_invalid")
        try:
            rows = _records(
                await execute_query(
                    query,
                    parameters_={"namespace": namespace},
                    database_="neo4j",
                )
            )
        except S5PostObservationError:
            raise
        except Exception:
            raise _fail("query_execution_failed") from None
        if observation != EPISODIC_OBSERVATION:
            if observation == RELATES_TO_OBSERVATION:
                for row in rows:
                    resolved = row.get("resolved_provenance")
                    provenance_ids = row.get("provenance_episode_ids")
                    if not isinstance(resolved, list) or not isinstance(
                        provenance_ids, list
                    ):
                        raise _fail("query_result_invalid")
                    by_id = {
                        item.get("episode_id"): item
                        for item in resolved
                        if isinstance(item, Mapping)
                    }
                    row["provenance"] = [
                        {
                            "episode_id": episode_id,
                            "group_id": by_id.get(episode_id, {}).get("group_id"),
                            "exists": episode_id in by_id,
                        }
                        for episode_id in provenance_ids
                    ]
            return rows
        mapped: list[dict[str, object]] = []
        for row in rows:
            source = self._source_by_name.get(row.get("name"))
            if source is None:
                mapped.append(
                    {
                        **row,
                        "source_sequence": _EXPECTED_EPISODES,
                        "source_sha256": "0" * 64,
                    }
                )
            else:
                mapped.append({**row, **source})
        return mapped


def _time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    value_type = type(value)
    if (
        value_type.__module__ == "neo4j.time"
        and value_type.__qualname__ == "DateTime"
    ):
        to_native = getattr(value, "to_native", None)
        if not callable(to_native):
            raise _fail("temporal_value_invalid")
        try:
            converted = to_native()
        except Exception:
            raise _fail("temporal_value_invalid") from None
        if not isinstance(converted, datetime):
            raise _fail("temporal_value_invalid")
        return converted
    if not isinstance(value, str) or not value:
        raise _fail("temporal_value_invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _fail("temporal_value_invalid") from None


def _row_list(value: object, code: str) -> list[dict[str, object]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise _fail(code)
    try:
        return [deepcopy(dict(row)) for row in value]
    except (TypeError, ValueError):
        raise _fail(code) from None


async def observe_s5_native_post_namespace(
    *,
    driver: object,
    method: str,
    run_id: str,
    namespace: str,
    expected_sources: Sequence[Mapping[str, object]],
    durable_publication_events: Sequence[Mapping[str, object]],
    query_executor: Callable[
        [object, str, str],
        Awaitable[Sequence[Mapping[str, object]]],
    ],
) -> dict[str, object]:
    """Observe one completed namespace and return a public sealed artifact."""

    slug = _METHOD_SLUG.get(method)
    if (
        slug is None
        or not isinstance(run_id, str)
        or _RUN_ID.fullmatch(run_id) is None
        or not run_id.startswith(f"s5-{slug}-")
        or not isinstance(namespace, str)
        or namespace != f"pev3-{run_id}"
    ):
        raise _fail("execution_identity_invalid")
    if not callable(query_executor):
        raise _fail("query_executor_invalid")
    expected = _source_inventory(expected_sources)
    publications = _row_list(
        durable_publication_events, "durable_publication_coverage_invalid"
    )
    if any(row.get("event_type") != "publication" for row in publications):
        raise _fail("durable_publication_coverage_invalid")
    _require_exact_coverage(
        rows=publications,
        expected=expected,
        code="durable_publication_coverage_invalid",
    )

    observed: dict[str, list[dict[str, object]]] = {}
    for observation in _OBSERVATIONS:
        try:
            value = await query_executor(driver, observation, namespace)
            observed[observation] = _row_list(value, "query_result_invalid")
        except S5PostObservationError:
            raise
        except Exception:
            raise _fail("query_execution_failed") from None

    episodes = observed[EPISODIC_OBSERVATION]
    if any(row.get("group_id") != namespace for row in episodes):
        raise _fail("episodic_source_coverage_invalid")
    _require_exact_coverage(
        rows=episodes,
        expected=expected,
        code="episodic_source_coverage_invalid",
    )
    entities = observed[ENTITY_OBSERVATION]
    relations = observed[RELATES_TO_OBSERVATION]

    episode_ids: set[object] = set()
    source_by_episode_id: dict[object, int] = {}
    for row in episodes:
        record_id = row.get("record_id")
        if record_id is None or record_id in episode_ids:
            raise _fail("episodic_record_identity_invalid")
        episode_ids.add(record_id)
        source_by_episode_id[record_id] = int(row["source_sequence"])

    all_entity_ids: set[object] = set()
    namespace_entity_ids: set[object] = set()
    entity_namespace_escape_count = 0
    for row in entities:
        record_id = row.get("record_id")
        if record_id is None or record_id in all_entity_ids:
            raise _fail("entity_record_identity_invalid")
        all_entity_ids.add(record_id)
        if row.get("group_id") == namespace:
            namespace_entity_ids.add(record_id)
        else:
            entity_namespace_escape_count += 1

    counts = {
        "expected_source_count": len(expected),
        "durable_publication_count": len(publications),
        "episodic_count": len(episodes),
        "lost_episodic_count": 0,
        "duplicate_episodic_count": 0,
        "unexpected_episodic_count": 0,
        "entity_count": len(entities),
        "relates_to_count": len(relations),
        "entity_namespace_escape_count": entity_namespace_escape_count,
        "relation_namespace_escape_count": 0,
        "endpoint_escape_count": 0,
        "provenance_dangling_count": 0,
        "provenance_cross_namespace_count": 0,
        "valid_invalid_reversal_count": 0,
    }
    per_source = {int(row["source_sequence"]): 0 for row in expected}
    relation_ids: set[object] = set()
    for row in relations:
        record_id = row.get("record_id")
        if record_id is None or record_id in relation_ids:
            raise _fail("relation_record_identity_invalid")
        relation_ids.add(record_id)
        provenance = _row_list(row.get("provenance"), "provenance_shape_invalid")
        attributable_sources = {
            source_by_episode_id[item.get("episode_id")]
            for item in provenance
            if item.get("episode_id") in source_by_episode_id
        }
        relation_violations = 0
        if row.get("group_id") != namespace:
            counts["relation_namespace_escape_count"] += 1
            relation_violations += 1
        for endpoint in ("source_entity_id", "target_entity_id"):
            if row.get(endpoint) not in namespace_entity_ids:
                counts["endpoint_escape_count"] += 1
                relation_violations += 1
        if not provenance:
            counts["provenance_dangling_count"] += 1
            relation_violations += 1
        for item in provenance:
            if item.get("exists") is not True:
                counts["provenance_dangling_count"] += 1
                relation_violations += 1
            elif item.get("group_id") != namespace:
                counts["provenance_cross_namespace_count"] += 1
                relation_violations += 1
        valid_at = _time(row.get("valid_at"))
        invalid_at = _time(row.get("invalid_at"))
        if valid_at is not None and invalid_at is not None and invalid_at < valid_at:
            counts["valid_invalid_reversal_count"] += 1
            relation_violations += 1
        for source in attributable_sources:
            per_source[source] += relation_violations

    violation_classifications = [
        {"classification": field, "count": counts[field]}
        for field in _VIOLATION_FIELDS
    ]
    global_violation_total = sum(counts[field] for field in _VIOLATION_FIELDS)
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "method": method,
        "status": (
            "PASS" if global_violation_total == 0 else "INVARIANT_VIOLATIONS_OBSERVED"
        ),
        "run_id_sha256": _identity_sha256(run_id),
        "namespace_sha256": _identity_sha256(namespace),
        "execution_identity_sha256": payload_sha256(
            {"run_id": run_id, "namespace": namespace}
        ),
        "source_manifest_sha256": payload_sha256(list(expected)),
        "durable_publication_manifest_sha256": payload_sha256(
            [
                {
                    "source_sequence": row["source_sequence"],
                    "source_sha256": row["source_sha256"],
                }
                for row in publications
            ]
        ),
        "counts": counts,
        "source_classifications": [
            {
                "source_sequence": row["source_sequence"],
                "source_sha256": row["source_sha256"],
                "classification": "OBSERVED_EXACTLY_ONCE",
            }
            for row in expected
        ],
        "per_source_violation_counts": {
            str(source): count for source, count in sorted(per_source.items())
        },
        "violation_classifications": violation_classifications,
        "global_violation_total": global_violation_total,
    }
    payload["observation_sha256"] = payload_sha256(payload)
    _assert_public(payload)
    return payload


def verify_s5_native_post_observation(
    artifact: Mapping[str, object],
    *,
    expected_method: str | None = None,
    expected_run_id: str | None = None,
    expected_namespace: str | None = None,
) -> dict[str, object]:
    """Verify a persisted observation and return an integer-keyed projection."""

    if not isinstance(artifact, Mapping):
        raise _fail("observation_artifact_invalid")
    selected = deepcopy(dict(artifact))
    seal = selected.pop("observation_sha256", None)
    if (
        set(artifact) != _ARTIFACT_FIELDS
        or artifact.get("schema_version") != SCHEMA
        or artifact.get("method") not in _METHODS
        or artifact.get("status") not in {"PASS", "INVARIANT_VIOLATIONS_OBSERVED"}
        or _SHA256.fullmatch(str(artifact.get("run_id_sha256") or "")) is None
        or _SHA256.fullmatch(str(artifact.get("namespace_sha256") or "")) is None
        or not isinstance(seal, str)
        or seal != payload_sha256(selected)
    ):
        raise _fail("observation_artifact_invalid")
    expected_identity = (expected_method, expected_run_id, expected_namespace)
    if any(item is not None for item in expected_identity):
        slug = _METHOD_SLUG.get(str(expected_method))
        if (
            any(not isinstance(item, str) for item in expected_identity)
            or slug is None
            or _RUN_ID.fullmatch(str(expected_run_id)) is None
            or not str(expected_run_id).startswith(f"s5-{slug}-")
            or expected_namespace != f"pev3-{expected_run_id}"
            or artifact.get("method") != expected_method
            or artifact.get("run_id_sha256")
            != _identity_sha256(str(expected_run_id))
            or artifact.get("namespace_sha256")
            != _identity_sha256(str(expected_namespace))
        ):
            raise _fail("artifact_identity_invalid")
    counts = artifact.get("counts")
    if (
        not isinstance(counts, Mapping)
        or set(counts) != set(_COUNT_FIELDS)
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values())
    ):
        raise _fail("observation_counts_invalid")
    sources = artifact.get("source_classifications")
    if not isinstance(sources, list) or len(sources) != _EXPECTED_EPISODES:
        raise _fail("observation_source_coverage_invalid")
    expected = _source_inventory(
        [
            {
                "source_sequence": row.get("source_sequence"),
                "source_sha256": row.get("source_sha256"),
            }
            for row in sources
            if isinstance(row, Mapping)
        ]
    )
    if (
        any(
            not isinstance(row, Mapping)
            or set(row)
            != {"source_sequence", "source_sha256", "classification"}
            or row.get("classification") != "OBSERVED_EXACTLY_ONCE"
            for row in sources
        )
        or artifact.get("source_manifest_sha256") != payload_sha256(list(expected))
    ):
        raise _fail("observation_source_coverage_invalid")
    raw_per_source = artifact.get("per_source_violation_counts")
    if not isinstance(raw_per_source, Mapping) or set(raw_per_source) != {
        str(index) for index in range(_EXPECTED_EPISODES)
    }:
        raise _fail("observation_invariant_coverage_invalid")
    per_source: dict[int, int] = {}
    for index in range(_EXPECTED_EPISODES):
        value = raw_per_source[str(index)]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _fail("observation_invariant_coverage_invalid")
        per_source[index] = value
    violation_rows = artifact.get("violation_classifications")
    if violation_rows != [
        {"classification": field, "count": counts[field]}
        for field in _VIOLATION_FIELDS
    ]:
        raise _fail("observation_violation_summary_invalid")
    total = sum(int(counts[field]) for field in _VIOLATION_FIELDS)
    if (
        artifact.get("global_violation_total") != total
        or (artifact.get("status") == "PASS") != (total == 0)
        or counts.get("expected_source_count") != _EXPECTED_EPISODES
        or counts.get("durable_publication_count") != _EXPECTED_EPISODES
        or counts.get("episodic_count") != _EXPECTED_EPISODES
        or any(
            counts.get(field) != 0
            for field in (
                "lost_episodic_count",
                "duplicate_episodic_count",
                "unexpected_episodic_count",
            )
        )
    ):
        raise _fail("observation_summary_invalid")
    _assert_public(artifact)
    verified = deepcopy(dict(artifact))
    verified["per_source_violation_counts"] = per_source
    return verified


__all__ = [
    "ENTITY_OBSERVATION",
    "EPISODIC_OBSERVATION",
    "RELATES_TO_OBSERVATION",
    "S5GraphitiPostQueryExecutor",
    "S5PostObservationError",
    "observe_s5_native_post_namespace",
    "verify_s5_native_post_observation",
]
