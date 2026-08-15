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
