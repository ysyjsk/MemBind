"""RED contracts for the pinned Graphiti controlled offline fixture.

These tests intentionally target the installed Graphiti 0.29.3 call path.  The
fixture is allowed to replace only model, embedding, search, clock, and
transaction I/O providers; Graphiti's extraction/resolution/attribute/commit
functions must remain the functions executed by the test.
"""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.s5_graphiti_controlled_fixture import (
    ControlledGraphitiProviders,
    ControlledGraphitiFixtureError,
    build_controlled_graphiti_fixture,
)


def test_fixture_binds_the_pinned_graphiti_identity_and_native_call_order() -> None:
    fixture = build_controlled_graphiti_fixture()
    result = asyncio.run(fixture.run_episode())

    assert fixture.binding.loader_verified is True
    assert result.call_order == (
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_extracted_edges",
        "extract_attributes_from_nodes",
        "process_episode_data",
    )
    assert result.commit_completed is True
    assert result.publication_allowed is True
    assert result.transaction_attempts == 1


def test_transaction_failure_is_fail_closed_before_publication() -> None:
    fixture = build_controlled_graphiti_fixture(fail_transaction=True)

    with pytest.raises(ControlledGraphitiFixtureError, match="COMMIT"):
        asyncio.run(fixture.run_episode())

    assert any(event["event"] == "commit_failed" for event in fixture.events)
    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_native_default_edge_type_map_is_preserved() -> None:
    fixture = build_controlled_graphiti_fixture(edge_types=("WorksAt",))
    result = asyncio.run(fixture.run_episode())

    assert result.edge_type_map == {("Entity", "Entity"): ["WorksAt"]}


def test_real_graphiti_edge_path_reaches_resolution_and_bulk_commit() -> None:
    fixture = build_controlled_graphiti_fixture(
        edge_types=("WorksAt",), edge_fact="Alice works at Acme."
    )
    result = asyncio.run(fixture.run_episode())

    assert result.observation.resolved_edge_count == 1
    assert result.observation.invalidated_edge_count == 0
    assert result.call_order == (
        "extract_nodes",
        "resolve_extracted_nodes",
        "extract_edges",
        "resolve_edge_pointers",
        "resolve_extracted_edges",
        "extract_attributes_from_nodes",
        "process_episode_data",
    )
    assert any(event["event"] == "edge_save_bulk" for event in fixture.events)


def test_real_graphiti_resolution_can_merge_an_alias_into_existing_canonical_node() -> None:
    fixture = build_controlled_graphiti_fixture(canonical_candidate=True)
    result = asyncio.run(fixture.run_episode())

    node_save = next(event for event in fixture.events if event["event"] == "node_save_bulk")
    assert node_save["node_uuids"] == ["canonical-alice"]
    assert result.observation.resolved_node_count == 1


def test_real_graphiti_compatible_duplicate_uuid_is_coalesced() -> None:
    fixture = build_controlled_graphiti_fixture(
        canonical_candidate=True,
        duplicate_entity=True,
    )
    result = asyncio.run(fixture.run_episode())

    node_save = next(event for event in fixture.events if event["event"] == "node_save_bulk")
    assert node_save["node_uuids"] == ["canonical-alice"]
    assert result.observation.resolved_node_count == 1
    assert not any(event.get("event") == "commit_failed" for event in fixture.events)


def test_real_graphiti_conflicting_candidate_projection_fails_before_commit() -> None:
    fixture = build_controlled_graphiti_fixture(
        conflicting_candidate_projections=True,
    )

    with pytest.raises(ControlledGraphitiFixtureError, match="conflicting_duplicate"):
        asyncio.run(fixture.run_episode())

    assert fixture.call_order == ["extract_nodes", "resolve_extracted_nodes"]
    assert not any(event.get("event") == "node_save_bulk" for event in fixture.events)
    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_canonical_logical_state_excludes_random_identity_and_wall_clock() -> None:
    options = {
        "edge_types": ("WorksAt",),
        "edge_fact": "Alice works at Acme.",
        "invalidation_candidate": True,
    }
    first = build_controlled_graphiti_fixture(**options)
    second = build_controlled_graphiti_fixture(**options)
    asyncio.run(first.run_episode())
    asyncio.run(second.run_episode())

    first_state = first.canonical_logical_state()
    second_state = second.canonical_logical_state()

    assert first_state == second_state
    assert set(first_state) == {"nodes", "relationships"}
    assert {node["name"] for node in first_state["nodes"]} == {"Alice", "Acme"}
    assert {edge["fact"] for edge in first_state["relationships"]} == {
        "Alice works at Acme.",
        "Alice previously worked at Beta.",
    }
    assert all("uuid" not in row for rows in first_state.values() for row in rows)
    assert all("created_at" not in row for rows in first_state.values() for row in rows)
    assert all("expired_at" not in row for rows in first_state.values() for row in rows)


def test_real_graphiti_temporal_invalidation_is_observed_before_commit() -> None:
    fixture = build_controlled_graphiti_fixture(
        edge_types=("WorksAt",),
        edge_fact="Alice works at Acme.",
        invalidation_candidate=True,
    )
    result = asyncio.run(fixture.run_episode())

    assert result.observation.resolved_edge_count == 1
    assert result.observation.invalidated_edge_count == 1
    edge_save = next(event for event in fixture.events if event["event"] == "edge_save_bulk")
    assert edge_save["count"] == 2


def test_group_id_routes_all_graphiti_calls_to_the_cloned_database() -> None:
    fixture = build_controlled_graphiti_fixture(
        configured_database="default-db", group_id="experiment-db"
    )
    result = asyncio.run(fixture.run_episode())

    assert result.routed_database == "experiment-db"
    assert fixture.driver.clone_calls == ["experiment-db"]


def test_malformed_native_process_result_fails_closed() -> None:
    fixture = build_controlled_graphiti_fixture(malformed_commit_result=True)

    with pytest.raises(ControlledGraphitiFixtureError, match="COMMIT_RESULT"):
        asyncio.run(fixture.run_episode())

    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_transaction_callback_retry_is_not_claimed_idempotent() -> None:
    fixture = build_controlled_graphiti_fixture(retry_transaction_once=True)

    with pytest.raises(ControlledGraphitiFixtureError, match="RETRY_IDEMPOTENCE"):
        asyncio.run(fixture.run_episode())

    assert fixture.transaction_attempts == 2
    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_transaction_callback_retry_can_pass_only_with_durable_upsert_witness() -> None:
    fixture = build_controlled_graphiti_fixture(
        retry_transaction_once=True, idempotent_retry=True
    )
    result = asyncio.run(fixture.run_episode())

    assert result.retry_idempotence_proven is True
    assert result.publication_allowed is True
    assert fixture.transaction_attempts == 2


def test_retry_witness_rejects_same_uuid_with_changed_durable_projection() -> None:
    fixture = build_controlled_graphiti_fixture(
        retry_transaction_once=True,
        idempotent_retry=True,
        mutate_retry_payload=True,
    )

    with pytest.raises(ControlledGraphitiFixtureError, match="RETRY_IDEMPOTENCE"):
        asyncio.run(fixture.run_episode())

    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_controlled_provider_consumption_is_allowlisted_and_complete() -> None:
    fixture = build_controlled_graphiti_fixture()
    asyncio.run(fixture.run_episode())

    assert isinstance(fixture.providers, ControlledGraphitiProviders)
    assert fixture.active_providers is None
    assert set(fixture.provider_consumption) == {
        "provider_scope",
        "logical_time",
        "initial_state",
        "llm",
        "embedding",
        "candidate_query",
    }
    assert fixture.unexpected_provider_consumption == ()


def test_missing_controlled_llm_response_fails_closed_before_commit() -> None:
    fixture = build_controlled_graphiti_fixture(missing_llm_response="ExtractedEntities")

    with pytest.raises(ControlledGraphitiFixtureError, match="extract_nodes_failed"):
        asyncio.run(fixture.run_episode())

    assert not any(event.get("event") == "publication" for event in fixture.events)


def test_fixture_case_reset_clears_prior_events_and_transaction_attempts() -> None:
    fixture = build_controlled_graphiti_fixture()
    first = asyncio.run(fixture.run_episode())
    fixture.reset_case()
    second = asyncio.run(fixture.run_episode())

    assert first.call_order == second.call_order
    assert fixture.transaction_attempts == 1
    assert [event["event"] for event in fixture.events].count("publication") == 1


def test_real_graphiti_multi_source_commit_publishes_in_source_order() -> None:
    fixture = build_controlled_graphiti_fixture()
    observations, publication_order = asyncio.run(fixture.run_sources(2))

    assert len(observations) == 2
    assert publication_order == (0, 1)
    assert fixture.transaction_attempts == 2
    assert [event["event"] for event in fixture.events].count("commit_completed") == 2
