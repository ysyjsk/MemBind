"""Service-free TDD for sanitized Native post-namespace observation."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Mapping
from datetime import datetime, timezone

import pytest

from paper_eval.s5_native_post_observation import (
    ENTITY_OBSERVATION,
    EPISODIC_OBSERVATION,
    RELATES_TO_OBSERVATION,
    S5GraphitiPostQueryExecutor,
    S5PostObservationError,
    observe_s5_native_post_namespace,
    verify_s5_native_post_observation,
)


RUN_ID = "s5-a0-20260816-001"
NAMESPACE = f"pev3-{RUN_ID}"
DRIVER = object()


def _sources() -> list[dict[str, object]]:
    return [
        {"source_sequence": index, "source_sha256": f"{index + 1:064x}"}
        for index in range(49)
    ]


def _publications() -> list[dict[str, object]]:
    return [
        {
            "event_type": "publication",
            "source_sequence": source["source_sequence"],
            "source_sha256": source["source_sha256"],
        }
        for source in _sources()
    ]


def _rows() -> dict[str, list[dict[str, object]]]:
    episodes = [
        {
            "record_id": f"private-episode-uuid-{source['source_sequence']}",
            "group_id": NAMESPACE,
            **source,
            "name": f"private-name-{source['source_sequence']}",
            "body": "private episode body must not enter the artifact",
        }
        for source in _sources()
    ]
    entities = [
        {
            "record_id": "private-entity-uuid-a",
            "group_id": NAMESPACE,
            "name": "private entity name",
        },
        {
            "record_id": "private-entity-uuid-b",
            "group_id": NAMESPACE,
            "name": "another private entity name",
        },
    ]
    relations = [
        {
            "record_id": "private-relation-uuid",
            "group_id": NAMESPACE,
            "source_entity_id": "private-entity-uuid-a",
            "target_entity_id": "private-entity-uuid-b",
            "provenance": [
                {
                    "episode_id": "private-episode-uuid-0",
                    "group_id": NAMESPACE,
                    "exists": True,
                }
            ],
            "valid_at": "2026-02-01T00:00:00Z",
            "invalid_at": None,
            # Deliberately precedes reference_time.  The contract must not
            # interpret that relationship as a temporal reversal.
            "expired_at": "2020-01-01T00:00:00Z",
            "reference_time": "2030-01-01T00:00:00Z",
            "fact": "private fact must not enter the artifact",
        }
    ]
    return {
        EPISODIC_OBSERVATION: episodes,
        ENTITY_OBSERVATION: entities,
        RELATES_TO_OBSERVATION: relations,
    }


class QueryExecutor:
    def __init__(self, rows: Mapping[str, list[dict[str, object]]]) -> None:
        self.rows = copy.deepcopy(dict(rows))
        self.calls: list[tuple[object, str, str]] = []

    async def __call__(
        self, driver: object, observation: str, namespace: str
    ) -> list[dict[str, object]]:
        self.calls.append((driver, observation, namespace))
        return copy.deepcopy(self.rows[observation])


def _observe(
    *,
    rows: Mapping[str, list[dict[str, object]]] | None = None,
    expected_sources: list[dict[str, object]] | None = None,
    publications: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], QueryExecutor]:
    executor = QueryExecutor(rows or _rows())
    artifact = asyncio.run(
        observe_s5_native_post_namespace(
            driver=DRIVER,
            method="A0",
            run_id=RUN_ID,
            namespace=NAMESPACE,
            expected_sources=expected_sources or _sources(),
            durable_publication_events=publications or _publications(),
            query_executor=executor,
        )
    )
    return artifact, executor


def test_exact_49_source_observation_is_sanitized_and_hash_sealed() -> None:
    artifact, executor = _observe()

    verified = verify_s5_native_post_observation(
        artifact,
        expected_method="A0",
        expected_run_id=RUN_ID,
        expected_namespace=NAMESPACE,
    )
    assert verified["status"] == "PASS"
    assert verified["counts"] == {
        "expected_source_count": 49,
        "durable_publication_count": 49,
        "episodic_count": 49,
        "lost_episodic_count": 0,
        "duplicate_episodic_count": 0,
        "unexpected_episodic_count": 0,
        "entity_count": 2,
        "relates_to_count": 1,
        "entity_namespace_escape_count": 0,
        "relation_namespace_escape_count": 0,
        "endpoint_escape_count": 0,
        "provenance_dangling_count": 0,
        "provenance_cross_namespace_count": 0,
        "valid_invalid_reversal_count": 0,
    }
    assert len(verified["source_classifications"]) == 49
    assert verified["method"] == "A0"
    assert verified["global_violation_total"] == 0
    assert verified["per_source_violation_counts"] == {
        index: 0 for index in range(49)
    }
    assert all(
        row["classification"] == "OBSERVED_EXACTLY_ONCE"
        for row in verified["source_classifications"]
    )
    assert executor.calls == [
        (DRIVER, EPISODIC_OBSERVATION, NAMESPACE),
        (DRIVER, ENTITY_OBSERVATION, NAMESPACE),
        (DRIVER, RELATES_TO_OBSERVATION, NAMESPACE),
    ]
    rendered = repr(verified)
    for private in (
        NAMESPACE,
        "private-episode-uuid",
        "private-entity-uuid",
        "private-relation-uuid",
        "private episode body",
        "private fact",
        "private-name",
        RUN_ID,
    ):
        assert private not in rendered


def test_observation_verifier_accepts_canonical_sorted_json_round_trip() -> None:
    artifact, _executor = _observe()
    persisted = json.loads(json.dumps(artifact, sort_keys=True))

    assert verify_s5_native_post_observation(persisted)["status"] == "PASS"


def test_graphiti_query_adapter_maps_exact_episode_name_without_db_source_fields() -> None:
    class Driver:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object], str]] = []

        async def execute_query(
            self,
            query: str,
            *,
            parameters_: dict[str, object],
            database_: str,
        ) -> list[dict[str, object]]:
            self.calls.append((query, dict(parameters_), database_))
            if "MATCH (source:Entity)-[relation:RELATES_TO]" in query:
                return [
                    {
                        "record_id": "private-relation",
                        "group_id": NAMESPACE,
                        "source_entity_id": "private-entity-a",
                        "target_entity_id": "private-entity-b",
                        "provenance_episode_ids": ["private-episode-0"],
                        "resolved_provenance": [
                            {
                                "episode_id": "private-episode-0",
                                "group_id": NAMESPACE,
                            }
                        ],
                        "valid_at": "2026-01-01T00:00:00Z",
                        "invalid_at": None,
                        "expired_at": "2020-01-01T00:00:00Z",
                        "reference_time": "2030-01-01T00:00:00Z",
                    }
                ]
            if "MATCH (episode:Episodic)" in query:
                return [
                    {
                        "record_id": f"private-episode-{index}",
                        "name": f"07741c45::episode::{index:04d}",
                        "group_id": NAMESPACE,
                    }
                    for index in range(49)
                ]
            if "MATCH (entity:Entity)" in query:
                return [
                    {"record_id": "private-entity-a", "group_id": NAMESPACE},
                    {"record_id": "private-entity-b", "group_id": NAMESPACE},
                ]
            raise AssertionError("unexpected query")

    driver = Driver()
    executor = S5GraphitiPostQueryExecutor(expected_sources=_sources())
    artifact = asyncio.run(
        observe_s5_native_post_namespace(
            driver=driver,
            method="A0",
            run_id=RUN_ID,
            namespace=NAMESPACE,
            expected_sources=_sources(),
            durable_publication_events=_publications(),
            query_executor=executor,
        )
    )

    assert artifact["status"] == "PASS"
    assert len(driver.calls) == 3
    assert all(
        params == {"namespace": NAMESPACE} and database == "neo4j"
        for _query, params, database in driver.calls
    )
    episode_query = next(
        query for query, _params, _database in driver.calls
        if "MATCH (episode:Episodic)" in query
    )
    assert "episode.group_id = $namespace" in episode_query
    assert "STARTS WITH" not in episode_query
    entity_query = next(
        query for query, _params, _database in driver.calls
        if "MATCH (entity:Entity)" in query
    )
    assert "EXISTS" in entity_query
    rendered = repr(artifact)
    assert "07741c45::episode" not in rendered
    assert "private-episode" not in rendered
    assert "private-entity" not in rendered
    assert "private-relation" not in rendered

    with pytest.raises(S5PostObservationError, match="artifact_identity_invalid"):
        verify_s5_native_post_observation(
            artifact,
            expected_method="A0",
            expected_run_id="s5-a0-20260816-002",
            expected_namespace=NAMESPACE,
        )


@pytest.mark.parametrize("delta", [-1, 1])
def test_expected_source_inventory_must_be_exactly_49(delta: int) -> None:
    expected = _sources()
    if delta < 0:
        expected.pop()
    else:
        expected.append({"source_sequence": 49, "source_sha256": "f" * 64})

    with pytest.raises(S5PostObservationError, match="expected_source_count_invalid"):
        _observe(expected_sources=expected)


@pytest.mark.parametrize("mutation", ["lost", "duplicate", "unexpected", "hash"])
def test_durable_publication_coverage_fails_closed(mutation: str) -> None:
    publications = _publications()
    if mutation == "lost":
        publications.pop()
    elif mutation == "duplicate":
        publications[-1] = dict(publications[0])
    elif mutation == "unexpected":
        publications[-1]["source_sequence"] = 49
    else:
        publications[-1]["source_sha256"] = "f" * 64

    with pytest.raises(
        S5PostObservationError, match="durable_publication_coverage_invalid"
    ):
        _observe(publications=publications)


@pytest.mark.parametrize("mutation", ["lost", "duplicate", "unexpected", "hash"])
def test_persisted_episodic_coverage_fails_closed(mutation: str) -> None:
    rows = _rows()
    episodes = rows[EPISODIC_OBSERVATION]
    if mutation == "lost":
        episodes.pop()
    elif mutation == "duplicate":
        episodes[-1].update(episodes[0])
    elif mutation == "unexpected":
        episodes[-1]["source_sequence"] = 49
    else:
        episodes[-1]["source_sha256"] = "f" * 64

    with pytest.raises(S5PostObservationError, match="episodic_source_coverage_invalid"):
        _observe(rows=rows)


def test_cross_namespace_episodic_cannot_satisfy_exact_source_coverage() -> None:
    rows = _rows()
    rows[EPISODIC_OBSERVATION][0]["group_id"] = "foreign-group"

    with pytest.raises(S5PostObservationError, match="episodic_source_coverage_invalid"):
        _observe(rows=rows)


def test_relation_without_provenance_is_classified_as_dangling() -> None:
    rows = _rows()
    rows[RELATES_TO_OBSERVATION][0]["provenance"] = []

    artifact, _executor = _observe(rows=rows)

    assert artifact["status"] == "INVARIANT_VIOLATIONS_OBSERVED"
    assert artifact["counts"]["provenance_dangling_count"] == 1
    assert artifact["global_violation_total"] == 1


def test_graph_escape_provenance_and_temporal_violations_are_counted() -> None:
    rows = _rows()
    rows[ENTITY_OBSERVATION][0]["group_id"] = "foreign-group"
    rows[RELATES_TO_OBSERVATION][0].update(
        {
            "group_id": "foreign-group",
            "source_entity_id": "missing-entity",
            "target_entity_id": "private-entity-uuid-a",
            "provenance": [
                {
                    "episode_id": "missing-episode",
                    "group_id": NAMESPACE,
                    "exists": False,
                },
                {
                    "episode_id": "foreign-episode",
                    "group_id": "foreign-group",
                    "exists": True,
                },
                {
                    "episode_id": "private-episode-uuid-0",
                    "group_id": NAMESPACE,
                    "exists": True,
                },
            ],
            "valid_at": "2026-03-01T00:00:00Z",
            "invalid_at": "2026-02-01T00:00:00Z",
        }
    )

    artifact, _executor = _observe(rows=rows)

    assert artifact["status"] == "INVARIANT_VIOLATIONS_OBSERVED"
    assert artifact["counts"]["entity_namespace_escape_count"] == 1
    assert artifact["counts"]["relation_namespace_escape_count"] == 1
    assert artifact["counts"]["endpoint_escape_count"] == 2
    assert artifact["counts"]["provenance_dangling_count"] == 1
    assert artifact["counts"]["provenance_cross_namespace_count"] == 1
    assert artifact["counts"]["valid_invalid_reversal_count"] == 1
    assert artifact["global_violation_total"] == 7
    assert artifact["per_source_violation_counts"]["0"] == 6
    assert set(artifact["per_source_violation_counts"].values()) == {0, 6}
    assert artifact["violation_classifications"] == [
        {"classification": key, "count": artifact["counts"][key]}
        for key in (
            "entity_namespace_escape_count",
            "relation_namespace_escape_count",
            "endpoint_escape_count",
            "provenance_dangling_count",
            "provenance_cross_namespace_count",
            "valid_invalid_reversal_count",
        )
    ]


def test_expired_at_is_not_compared_with_reference_time() -> None:
    rows = _rows()
    relation = rows[RELATES_TO_OBSERVATION][0]
    relation["expired_at"] = "1900-01-01T00:00:00Z"
    relation["reference_time"] = "2100-01-01T00:00:00Z"

    artifact, _executor = _observe(rows=rows)

    assert artifact["status"] == "PASS"
    assert artifact["counts"]["valid_invalid_reversal_count"] == 0


def test_neo4j_datetime_values_use_the_driver_native_conversion() -> None:
    class Neo4jDateTime:
        def __init__(self, value: datetime) -> None:
            self.value = value

        def to_native(self) -> datetime:
            return self.value

    Neo4jDateTime.__module__ = "neo4j.time"
    Neo4jDateTime.__qualname__ = "DateTime"

    rows = _rows()
    relation = rows[RELATES_TO_OBSERVATION][0]
    relation["valid_at"] = Neo4jDateTime(
        datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
    relation["invalid_at"] = Neo4jDateTime(
        datetime(2026, 3, 1, tzinfo=timezone.utc)
    )

    artifact, _executor = _observe(rows=rows)

    assert artifact["status"] == "PASS"
    assert artifact["counts"]["valid_invalid_reversal_count"] == 0


def test_query_failure_is_sanitized() -> None:
    async def broken_query(
        _driver: object, _observation: str, _namespace: str
    ) -> list[dict[str, object]]:
        raise RuntimeError("private URI and credentials")

    with pytest.raises(S5PostObservationError, match="query_execution_failed") as caught:
        asyncio.run(
            observe_s5_native_post_namespace(
                driver=DRIVER,
                method="A0",
                run_id=RUN_ID,
                namespace=NAMESPACE,
                expected_sources=_sources(),
                durable_publication_events=_publications(),
                query_executor=broken_query,
            )
        )
    assert "private URI" not in str(caught.value)


def test_artifact_digest_or_public_shape_tampering_fails_closed() -> None:
    artifact, _executor = _observe()
    artifact["counts"]["entity_count"] = 99
    artifact["namespace"] = NAMESPACE

    with pytest.raises(S5PostObservationError):
        verify_s5_native_post_observation(artifact)
