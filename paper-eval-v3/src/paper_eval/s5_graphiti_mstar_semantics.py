"""Graphiti semantic callback implementation for the M* production path.

This layer contains the upstream Graphiti extraction/resolution/invalidation
sequence, while the shared M* scheduler remains responsible for concurrency
and source-order publication.  Construction is dependency-injected so every
unit test can run without a model or Neo4j; the default production factories
are supplied by the future live preflight.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    S5GraphitiSemanticBindingError,
)


class S5GraphitiMStarSemanticError(ValueError):
    """Sanitized semantic callback or upstream API failure."""


def _fail(code: str) -> S5GraphitiMStarSemanticError:
    return S5GraphitiMStarSemanticError(code)


def logical_ns_to_datetime(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("logical_time_invalid")
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


@dataclass(frozen=True)
class GraphitiEpisodeInput:
    """Opaque upstream episode context supplied by the pinned Native loader."""

    episode_node: object
    previous_episodes: Sequence[object]
    group_id: str
    entity_types: Mapping[str, object] | None = None
    excluded_entity_types: Sequence[str] | None = None
    edge_types: Mapping[str, object] | None = None
    edge_type_map: Mapping[tuple[str, str], Sequence[str]] | None = None
    custom_extraction_instructions: str | None = None

    def __post_init__(self) -> None:
        if self.episode_node is None:
            raise _fail("episode_node_missing")
        if isinstance(self.previous_episodes, (str, bytes)):
            raise _fail("previous_episodes_invalid")
        if not isinstance(self.group_id, str) or not self.group_id:
            raise _fail("group_id_invalid")


@dataclass(frozen=True)
class GraphitiPreparedBundle:
    """Opaque result of Native extraction, before latest-state binding."""

    source: GraphitiEpisodeInput
    extracted_nodes: Sequence[object]
    extracted_edges: Sequence[object]
    node_episode_index_map: Mapping[str, Sequence[int]]


@dataclass(frozen=True)
class GraphitiBindObservation:
    """Sanitized commit observation returned to the M* scheduler."""

    source_sequence: int
    logical_time_ns: int
    resolved_node_count: int
    resolved_edge_count: int
    invalidated_edge_count: int
    commit_result_type: str


AwaitableResult = Awaitable[object]
LatestStateRetriever = Callable[
    [GraphitiEpisodeInput], Awaitable[Sequence[object]]
]


class S5GraphitiMStarSemanticRuntime:
    """Run Graphiti's native semantic operations behind explicit callbacks."""

    def __init__(
        self,
        *,
        graphiti: object,
        binding: S5GraphitiSemanticBinding,
        latest_state_retriever: LatestStateRetriever,
    ) -> None:
        if graphiti is None:
            raise _fail("graphiti_missing")
        if not isinstance(binding, S5GraphitiSemanticBinding):
            raise _fail("semantic_binding_invalid")
        if not callable(latest_state_retriever):
            raise _fail("latest_state_retriever_invalid")
        self.graphiti = graphiti
        self.binding = binding
        self.latest_state_retriever = latest_state_retriever

    async def _await(self, value: object, code: str) -> object:
        if not inspect.isawaitable(value):
            raise _fail(code)
        try:
            return await value
        except S5GraphitiMStarSemanticError:
            raise
        except Exception:
            raise _fail(code) from None

    async def prepare(
        self,
        source: GraphitiEpisodeInput,
        logical_time_ns: int,
        _providers: object | None = None,
    ) -> GraphitiPreparedBundle:
        """Execute Native extraction only; no canonical graph lookup occurs."""

        if not isinstance(source, GraphitiEpisodeInput):
            raise _fail("semantic_source_invalid")
        # Validate the clock even though extraction itself is state-independent;
        # the same logical time is later reused by bind/commit.
        logical_ns_to_datetime(logical_time_ns)
        try:
            extracted_nodes, node_map = await self._await(
                self.binding.extract_nodes(
                    self.graphiti.clients,
                    source.episode_node,
                    list(source.previous_episodes),
                    source.entity_types,
                    list(source.excluded_entity_types or ()),
                    source.custom_extraction_instructions,
                ),
                "extract_nodes_failed",
            )
            extracted_edges = await self._await(
                self.binding.extract_edges(
                    self.graphiti.clients,
                    source.episode_node,
                    list(extracted_nodes),
                    list(source.previous_episodes),
                    {
                        key: list(value)
                        for key, value in (source.edge_type_map or {}).items()
                    },
                    source.group_id,
                    source.edge_types,
                    source.custom_extraction_instructions,
                ),
                "extract_edges_failed",
            )
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        if isinstance(extracted_nodes, (str, bytes)) or not isinstance(
            extracted_nodes, Sequence
        ):
            raise _fail("extracted_nodes_invalid")
        if isinstance(extracted_edges, (str, bytes)) or not isinstance(
            extracted_edges, Sequence
        ):
            raise _fail("extracted_edges_invalid")
        if not isinstance(node_map, Mapping):
            raise _fail("node_episode_index_map_invalid")
        return GraphitiPreparedBundle(
            source=source,
            extracted_nodes=list(extracted_nodes),
            extracted_edges=list(extracted_edges),
            node_episode_index_map=dict(node_map),
        )

    async def bind(
        self,
        prepared: GraphitiPreparedBundle,
        logical_time_ns: int,
        source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
        _providers: object | None = None,
    ) -> GraphitiBindObservation:
        """Resolve against latest state, apply invalidation, and commit Native."""

        if not isinstance(prepared, GraphitiPreparedBundle):
            raise _fail("prepared_bundle_invalid")
        if isinstance(source_sequence, bool) or source_sequence < 0:
            raise _fail("source_sequence_invalid")
        logical_time = logical_ns_to_datetime(logical_time_ns)
        source = prepared.source
        retrieved = await self._await(
            self.latest_state_retriever(source), "latest_state_retrieval_failed"
        )
        if isinstance(retrieved, (str, bytes)) or not isinstance(retrieved, Sequence):
            raise _fail("latest_state_retrieval_shape_invalid")
        previous_episodes = list(retrieved)
        try:
            nodes, uuid_map, _duplicates = await self._await(
                self.binding.resolve_extracted_nodes(
                    self.graphiti.clients,
                    list(prepared.extracted_nodes),
                    source.episode_node,
                    previous_episodes,
                    source.entity_types,
                ),
                "resolve_nodes_failed",
            )
            try:
                edges = self.binding.resolve_edge_pointers(
                    list(prepared.extracted_edges), uuid_map
                )
            except Exception:
                raise _fail("resolve_edge_pointers_failed") from None
            if isinstance(edges, (str, bytes)) or not isinstance(edges, Sequence):
                raise _fail("resolved_edge_pointers_invalid")
            resolved_edges, invalidated_edges, new_edges = await self._await(
                self.binding.resolve_extracted_edges(
                    self.graphiti.clients,
                    edges,
                    source.episode_node,
                    list(nodes),
                    dict(source.edge_types or {}),
                    {
                        key: list(value)
                        for key, value in (source.edge_type_map or {}).items()
                    },
                ),
                "resolve_edges_failed",
            )
            hydrated_nodes = await self._await(
                self.binding.extract_attributes_from_nodes(
                    self.graphiti.clients,
                    list(nodes),
                    source.episode_node,
                    previous_episodes,
                    source.entity_types,
                    edges=list(new_edges),
                ),
                "extract_attributes_failed",
            )
            committed = await self._await(
                self.binding.process_episode_data(
                    self.graphiti,
                    source.episode_node,
                    list(hydrated_nodes),
                    list(resolved_edges) + list(invalidated_edges),
                    logical_time,
                    source.group_id,
                    None,
                    None,
                    dict(prepared.node_episode_index_map),
                ),
                "process_episode_data_failed",
            )
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        return GraphitiBindObservation(
            source_sequence=source_sequence,
            logical_time_ns=logical_time_ns,
            resolved_node_count=len(nodes),
            resolved_edge_count=len(resolved_edges),
            invalidated_edge_count=len(invalidated_edges),
            commit_result_type=type(committed).__qualname__,
        )


__all__ = [
    "GraphitiBindObservation",
    "GraphitiEpisodeInput",
    "GraphitiPreparedBundle",
    "S5GraphitiMStarSemanticError",
    "S5GraphitiMStarSemanticRuntime",
    "logical_ns_to_datetime",
]
