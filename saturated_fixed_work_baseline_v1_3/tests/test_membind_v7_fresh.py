from __future__ import annotations

import asyncio

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.v7_fresh import (
    OrderedPublicationGate,
    V7FreshBindings,
    V7FreshError,
    build_v7_fresh_to_seam_async,
    publish_v7_fresh_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    install_local_extraction_chunking_policy,
)


class FakeGraphiti:
    def __init__(self, events: list[tuple[str, object]]) -> None:
        self.events = events

    async def _process_episode_data(self, *args: object) -> dict[str, object]:
        self.events.append(("publish", args[0]))
        return {"published": True}


def _bindings(events: list[tuple[str, object]], previous: tuple[str, ...] = ("old",)) -> V7FreshBindings:
    async def source_nodes(_graphiti: object, _episode: object, kwargs: dict[str, object]):
        assert kwargs["group_id"] == "isolated"
        events.append(("source_nodes", ()))
        return (["node"], {"node": [0]})

    async def source_edges(_graphiti: object, _episode: object, nodes: list[object], _kwargs: dict[str, object]):
        assert nodes == ["node"]
        events.append(("source_edges", ()))
        return ["edge"]

    async def retrieve(_graphiti: object, _kwargs: dict[str, object]):
        events.append(("retrieve_previous", previous))
        return previous

    async def resolve_nodes(_graphiti: object, nodes: list[object], _episode: object, got_previous: tuple[str, ...], _kwargs: dict[str, object]):
        assert got_previous == previous
        events.append(("resolve_nodes", got_previous))
        return (["resolved-node"], {"node": "uuid"}, [])

    async def resolve_edges(_graphiti: object, edges: list[object], _episode: object, got_previous: tuple[str, ...], nodes: list[object], _uuid_map: dict[str, str], _kwargs: dict[str, object]):
        assert edges == ["edge"] and nodes == ["resolved-node"] and got_previous == previous
        events.append(("resolve_edges", got_previous))
        return (["resolved-edge"], [], ["resolved-edge"])

    async def attributes(_graphiti: object, nodes: list[object], _episode: object, got_previous: tuple[str, ...], new_edges: list[object], _kwargs: dict[str, object]):
        assert nodes == ["resolved-node"] and got_previous == previous and new_edges == ["resolved-edge"]
        events.append(("attributes", got_previous))
        return ["hydrated-node"]

    return V7FreshBindings(
        now=lambda: "now",
        make_episode=lambda _graphiti, kwargs, now: (kwargs["name"], now),
        retrieve_previous=retrieve,
        extract_source_nodes=source_nodes,
        extract_source_edges=source_edges,
        resolve_nodes=resolve_nodes,
        resolve_edges=resolve_edges,
        extract_attributes=attributes,
        continuation_k=lambda **kwargs: {"schema_version": "test", **kwargs},
    )


def _kwargs() -> dict[str, object]:
    return {
        "name": "episode-0",
        "episode_body": "Alice met Bob.",
        "source_description": "test",
        "reference_time": "2025-01-01T00:00:00+00:00",
        "group_id": "isolated",
        "update_communities": False,
    }


def test_fresh_stage_a_is_state_free_and_stage_b_reads_previous_afterward() -> None:
    events: list[tuple[str, object]] = []
    graphiti = FakeGraphiti(events)
    result = asyncio.run(
        build_v7_fresh_to_seam_async(
            graphiti,
            _kwargs(),
            publication_frontier=0,
            backend_epoch="db-v1",
            bindings=_bindings(events),
        )
    )
    assert result.stage_events == (
        "SOURCE_LOCAL_START",
        "SOURCE_LOCAL_COMPLETE",
        "STATEFUL_RECONCILIATION_START",
        "STATEFUL_RECONCILIATION_COMPLETE",
    )
    assert [name for name, _value in events] == [
        "source_nodes",
        "source_edges",
        "retrieve_previous",
        "resolve_nodes",
        "resolve_edges",
        "attributes",
    ]
    assert result.previous_episodes == ("old",)
    assert result.entity_edges == ("resolved-edge",)


def test_publication_uses_native_seam_and_order_gate() -> None:
    events: list[tuple[str, object]] = []
    graphiti = FakeGraphiti(events)
    build = asyncio.run(
        build_v7_fresh_to_seam_async(
            graphiti,
            _kwargs(),
            publication_frontier=0,
            backend_epoch="db-v1",
            bindings=_bindings(events),
        )
    )
    gate = OrderedPublicationGate()
    output = asyncio.run(gate.publish(graphiti, 0, build))
    assert output == {"published": True}
    assert gate.frontier == 1
    with pytest.raises(V7FreshError, match="source sequence"):
        asyncio.run(gate.publish(graphiti, 2, build))


def test_stale_frontier_and_unsupported_publication_fail_closed() -> None:
    events: list[tuple[str, object]] = []
    graphiti = FakeGraphiti(events)
    build = asyncio.run(
        build_v7_fresh_to_seam_async(
            graphiti,
            _kwargs(),
            publication_frontier=2,
            backend_epoch="db-v1",
            bindings=_bindings(events),
        )
    )
    with pytest.raises(V7FreshError, match="frontier"):
        asyncio.run(publish_v7_fresh_async(graphiti, build, expected_frontier=1))
    with pytest.raises(V7FreshError, match="seam"):
        asyncio.run(publish_v7_fresh_async(object(), build, expected_frontier=2))


def test_build_rejects_saga_and_community_work() -> None:
    events: list[tuple[str, object]] = []
    graphiti = FakeGraphiti(events)
    for extra in ({"saga": "s"}, {"update_communities": True}):
        kwargs = _kwargs()
        kwargs.update(extra)
        with pytest.raises(V7FreshError):
            asyncio.run(
                build_v7_fresh_to_seam_async(
                    graphiti,
                    kwargs,
                    publication_frontier=0,
                    backend_epoch="db-v1",
                    bindings=_bindings(events),
                )
            )


def test_dedupe_candidate_pages_union_global_ids_without_dropping_resolution() -> None:
    calls: list[list[dict[str, object]]] = []

    class Client:
        max_tokens = 128

        async def generate_response(self, messages: list[dict[str, str]], **kwargs: object):
            del kwargs
            body = next(
                item["content"]
                for item in messages
                if "<EXISTING ENTITIES>" in item["content"]
            )
            start = body.index("<EXISTING ENTITIES>") + len("<EXISTING ENTITIES>")
            end = body.index("</EXISTING ENTITIES>", start)
            import json

            values = json.loads(body[start:end])
            calls.append(values)
            # Only the second page contains the matching global candidate 3.
            candidate = next(
                (int(item["candidate_id"]) for item in values if int(item["candidate_id"]) == 3),
                -1,
            )
            return {
                "entity_resolutions": [
                    {"id": 0, "name": "Alice", "duplicate_candidate_id": candidate}
                ]
            }

    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 10,
        dedupe_candidate_page_capacity=2,
    )
    messages = [
        {
            "role": "user",
            "content": (
                "<ENTITIES>\n[{\"id\": 0, \"name\": \"Alice\"}]\n</ENTITIES>\n"
                "<EXISTING ENTITIES>\n"
                "[{\"candidate_id\": 0, \"name\": \"Bob\"}, "
                "{\"candidate_id\": 1, \"name\": \"Carol\"}, "
                "{\"candidate_id\": 2, \"name\": \"Dan\"}, "
                "{\"candidate_id\": 3, \"name\": \"Alice\"}]\n"
                "</EXISTING ENTITIES>"
            ),
        }
    ]
    result = asyncio.run(
        client.generate_response(
            messages,
            response_model=object,
            max_tokens=128,
            prompt_name="dedupe_nodes.nodes",
        )
    )
    assert len(calls) == 2
    assert [len(page) for page in calls] == [2, 2]
    assert result["entity_resolutions"][0]["duplicate_candidate_id"] == 3
