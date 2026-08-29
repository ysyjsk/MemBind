"""V7-FRESH Graphiti qualification adapter.

The adapter makes the V7-B boundary executable without changing the frozen
V6 implementation.  Source-local extraction receives an intentionally empty
previous-episode collection.  Only after that stage completes does the adapter
read authoritative state for node/edge resolution and attribute materializing.
Publication is a separate, source-ordered call to Graphiti's native
``_process_episode_data`` seam.

Graphiti is imported lazily by :func:`default_bindings`; importing this module
alone cannot contact a provider or database.  The adapter is a qualification
candidate, not an authorization to run a live treatment.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence


class V7FreshError(RuntimeError):
    """Raised when the V7-FRESH stage or publication contract is violated."""


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True, slots=True)
class V7FreshBindings:
    """Provider-specific operations injected at the adapter boundary."""

    now: Callable[[], Any]
    make_episode: Callable[[Any, Mapping[str, Any], Any], Any]
    retrieve_previous: Callable[[Any, Mapping[str, Any]], Awaitable[Sequence[Any]]]
    extract_source_nodes: Callable[[Any, Any, Mapping[str, Any]], Awaitable[tuple[list[Any], Mapping[str, list[int]]]]]
    extract_source_edges: Callable[[Any, Any, list[Any], Mapping[str, Any]], Awaitable[list[Any]]]
    resolve_nodes: Callable[[Any, list[Any], Any, Sequence[Any], Mapping[str, Any]], Awaitable[tuple[list[Any], Mapping[str, str], list[Any]]]]
    resolve_edges: Callable[[Any, list[Any], Any, Sequence[Any], list[Any], Mapping[str, str], Mapping[str, Any]], Awaitable[tuple[list[Any], list[Any], list[Any]]]]
    extract_attributes: Callable[[Any, list[Any], Any, Sequence[Any], list[Any], Mapping[str, Any]], Awaitable[list[Any]]]
    continuation_k: Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class V7FreshBuildResult:
    """The complete fresh build, ending immediately before publication."""

    episode: Any
    group_id: str
    previous_episodes: tuple[Any, ...]
    extracted_nodes: tuple[Any, ...]
    extracted_edges: tuple[Any, ...]
    nodes: tuple[Any, ...]
    entity_edges: tuple[Any, ...]
    hydrated_nodes: tuple[Any, ...]
    node_episode_index_map: Mapping[str, list[int]]
    continuation_k: Mapping[str, Any]
    now: Any
    publication_frontier: int
    stage_events: tuple[str, ...]


def _validate_kwargs(episode_kwargs: Mapping[str, Any], publication_frontier: int) -> str:
    group_id = episode_kwargs.get("group_id")
    if not isinstance(group_id, str) or not group_id:
        raise V7FreshError("explicit isolated group_id is required")
    if episode_kwargs.get("saga") is not None or episode_kwargs.get("saga_previous_episode_uuid") is not None:
        raise V7FreshError("V7-FRESH qualification does not support saga")
    if episode_kwargs.get("update_communities", False) is not False:
        raise V7FreshError("V7-FRESH qualification does not support communities")
    if isinstance(publication_frontier, bool) or not isinstance(publication_frontier, int) or publication_frontier < 0:
        raise V7FreshError("publication frontier is invalid")
    return group_id


async def build_v7_fresh_to_seam_async(
    graphiti: Any,
    episode_kwargs: Mapping[str, Any],
    *,
    publication_frontier: int,
    backend_epoch: str,
    bindings: V7FreshBindings,
) -> V7FreshBuildResult:
    """Run source-local extraction, then stateful reconciliation.

    The ordering is intentional and tested: extraction gets no previous state;
    the first mutable-state read occurs only after both source-local operators
    finish.  No database write is performed by this function.
    """

    if not isinstance(bindings, V7FreshBindings):
        raise V7FreshError("V7-FRESH bindings are invalid")
    kwargs = dict(episode_kwargs)
    group_id = _validate_kwargs(kwargs, publication_frontier)
    driver = getattr(graphiti, "driver", None)
    if driver is not None and getattr(driver, "_database", group_id) != group_id:
        clone = getattr(driver, "clone", None)
        if not callable(clone):
            raise V7FreshError("isolated publication database cannot be selected")
        graphiti.driver = clone(database=group_id)
        clients = getattr(graphiti, "clients", None)
        if clients is not None:
            clients.driver = graphiti.driver
    now = bindings.now()
    episode = bindings.make_episode(graphiti, kwargs, now)
    events: list[str] = ["SOURCE_LOCAL_START"]

    # Stage A is deliberately state-free.  A provider implementation must not
    # smuggle retrieval or graph candidates through either extraction callback.
    extracted_nodes, index_map = await _maybe_await(
        bindings.extract_source_nodes(graphiti, episode, kwargs)
    )
    extracted_edges = await _maybe_await(
        bindings.extract_source_edges(graphiti, episode, list(extracted_nodes), kwargs)
    )
    events.append("SOURCE_LOCAL_COMPLETE")

    # Stage B is the first point where mutable authoritative memory is allowed.
    previous = tuple(await _maybe_await(bindings.retrieve_previous(graphiti, kwargs)))
    events.append("STATEFUL_RECONCILIATION_START")
    nodes, uuid_map, _duplicates = await _maybe_await(
        bindings.resolve_nodes(graphiti, list(extracted_nodes), episode, previous, kwargs)
    )
    resolved_edges, invalidated_edges, _new_edges = await _maybe_await(
        bindings.resolve_edges(
            graphiti,
            list(extracted_edges),
            episode,
            previous,
            list(nodes),
            dict(uuid_map),
            kwargs,
        )
    )
    entity_edges = list(resolved_edges) + list(invalidated_edges)
    hydrated_nodes = await _maybe_await(
        bindings.extract_attributes(
            graphiti,
            list(nodes),
            episode,
            previous,
            list(_new_edges),
            kwargs,
        )
    )
    events.append("STATEFUL_RECONCILIATION_COMPLETE")

    continuation = bindings.continuation_k(
        episodes=[episode],
        nodes=list(hydrated_nodes),
        entity_edges=entity_edges,
        node_episode_index_map=dict(index_map),
        now=now,
        group_id=group_id,
        backend_epoch=backend_epoch,
        publication_frontier=publication_frontier,
        source_local_previous_count=0,
        stateful_previous_count=len(previous),
    )
    if not isinstance(continuation, Mapping):
        raise V7FreshError("continuation contract must be a mapping")
    return V7FreshBuildResult(
        episode=episode,
        group_id=group_id,
        previous_episodes=previous,
        extracted_nodes=tuple(extracted_nodes),
        extracted_edges=tuple(extracted_edges),
        nodes=tuple(nodes),
        entity_edges=tuple(entity_edges),
        hydrated_nodes=tuple(hydrated_nodes),
        node_episode_index_map=dict(index_map),
        continuation_k=dict(continuation),
        now=now,
        publication_frontier=publication_frontier,
        stage_events=tuple(events),
    )


async def publish_v7_fresh_async(
    graphiti: Any,
    build: V7FreshBuildResult,
    *,
    expected_frontier: int,
    saga: Any = None,
    saga_previous_episode_uuid: str | None = None,
) -> Any:
    """Publish one fresh build through the native ordered seam."""

    if not isinstance(build, V7FreshBuildResult):
        raise V7FreshError("V7-FRESH build result is invalid")
    if expected_frontier != build.publication_frontier:
        raise V7FreshError("publication frontier/source order mismatch")
    process = getattr(graphiti, "_process_episode_data", None)
    if not callable(process):
        raise V7FreshError("native publication seam is unavailable")
    result = await _maybe_await(
        process(
            build.episode,
            list(build.hydrated_nodes),
            list(build.entity_edges),
            build.now,
            build.group_id,
            saga,
            saga_previous_episode_uuid,
            dict(build.node_episode_index_map),
        )
    )
    return result


@dataclass(slots=True)
class OrderedPublicationGate:
    """Minimal source-order guard for a sequence of fresh publications."""

    frontier: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.frontier, bool) or not isinstance(self.frontier, int) or self.frontier < 0:
            raise ValueError("frontier must be a non-negative integer")

    async def publish(self, graphiti: Any, source_sequence: int, build: V7FreshBuildResult) -> Any:
        if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 0:
            raise V7FreshError("source sequence is invalid")
        if source_sequence != self.frontier:
            raise V7FreshError("ordered publication source sequence mismatch")
        result = await publish_v7_fresh_async(
            graphiti,
            build,
            expected_frontier=self.frontier,
        )
        self.frontier += 1
        return result


def default_bindings() -> V7FreshBindings:
    """Construct Graphiti 0.29.3 bindings lazily for qualification runs."""

    from graphiti_core.graphiti import extract_attributes_from_nodes, resolve_edge_pointers
    from graphiti_core.nodes import EpisodeType, EpisodicNode
    from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT
    from graphiti_core.utils.datetime_utils import utc_now
    from graphiti_core.utils.maintenance.edge_operations import (
        extract_edges,
        resolve_extracted_edges,
    )
    from graphiti_core.utils.maintenance.node_operations import (
        extract_nodes,
        resolve_extracted_nodes,
    )

    async def retrieve(graphiti: Any, kwargs: Mapping[str, Any]) -> Sequence[Any]:
        explicit = kwargs.get("previous_episode_uuids")
        if explicit is not None:
            return await EpisodicNode.get_by_uuids(graphiti.driver, list(explicit))
        return await graphiti.retrieve_episodes(
            kwargs["reference_time"],
            last_n=RELEVANT_SCHEMA_LIMIT,
            group_ids=[kwargs["group_id"]],
            source=kwargs.get("source"),
        )

    def make_episode(_graphiti: Any, kwargs: Mapping[str, Any], now: Any) -> Any:
        return EpisodicNode(
            name=kwargs["name"],
            group_id=kwargs["group_id"],
            labels=[],
            source=kwargs.get("source", EpisodeType.message),
            content=kwargs["episode_body"],
            source_description=kwargs["source_description"],
            created_at=now,
            valid_at=kwargs["reference_time"],
            **({"uuid": kwargs["uuid"]} if kwargs.get("uuid") is not None else {}),
        )

    async def source_nodes(graphiti: Any, episode: Any, kwargs: Mapping[str, Any]) -> tuple[list[Any], Mapping[str, list[int]]]:
        return await extract_nodes(
            graphiti.clients,
            episode,
            [],
            kwargs.get("entity_types"),
            kwargs.get("excluded_entity_types"),
            kwargs.get("custom_extraction_instructions"),
        )

    async def resolve_nodes_bound(graphiti: Any, nodes: list[Any], episode: Any, previous: Sequence[Any], kwargs: Mapping[str, Any]) -> tuple[list[Any], Mapping[str, str], list[Any]]:
        return await resolve_extracted_nodes(
            graphiti.clients,
            nodes,
            episode,
            list(previous),
            kwargs.get("entity_types"),
        )

    async def resolve_edges_bound(graphiti: Any, edges: list[Any], episode: Any, previous: Sequence[Any], nodes: list[Any], uuid_map: Mapping[str, str], kwargs: Mapping[str, Any]) -> tuple[list[Any], list[Any], list[Any]]:
        edge_types = kwargs.get("edge_types")
        edge_map = kwargs.get("edge_type_map") or (
            {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
        )
        pointed = resolve_edge_pointers(edges, dict(uuid_map))
        return await resolve_extracted_edges(
            graphiti.clients,
            pointed,
            episode,
            nodes,
            edge_types or {},
            edge_map,
        )

    async def attributes_bound(graphiti: Any, nodes: list[Any], episode: Any, previous: Sequence[Any], new_edges: list[Any], kwargs: Mapping[str, Any]) -> list[Any]:
        return await extract_attributes_from_nodes(
            graphiti.clients,
            nodes,
            episode,
            list(previous),
            kwargs.get("entity_types"),
            edges=new_edges,
        )

    def continuation_k(**kwargs: Any) -> Mapping[str, Any]:
        return {"schema_version": "membind.v7b.fresh-continuation.v1", **kwargs}

    # Keep the group id in the per-invocation kwargs so a single binding object
    # remains safe to reuse across isolated namespaces.
    def source_edges_with_group(graphiti: Any, episode: Any, nodes: list[Any], kwargs: Mapping[str, Any]) -> Awaitable[list[Any]]:
        async def run() -> list[Any]:
            edge_types = kwargs.get("edge_types")
            edge_map = kwargs.get("edge_type_map") or (
                {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
            )
            return await extract_edges(
                graphiti.clients,
                episode,
                nodes,
                [],
                edge_map,
                str(kwargs["group_id"]),
                edge_types,
                kwargs.get("custom_extraction_instructions"),
            )
        return run()

    return V7FreshBindings(
        now=utc_now,
        make_episode=make_episode,
        retrieve_previous=retrieve,
        extract_source_nodes=source_nodes,
        extract_source_edges=source_edges_with_group,
        resolve_nodes=resolve_nodes_bound,
        resolve_edges=resolve_edges_bound,
        extract_attributes=attributes_bound,
        continuation_k=continuation_k,
    )


__all__ = [
    "OrderedPublicationGate",
    "V7FreshBindings",
    "V7FreshBuildResult",
    "V7FreshError",
    "build_v7_fresh_to_seam_async",
    "default_bindings",
    "publish_v7_fresh_async",
]
