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
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    S5GraphitiSemanticBindingError,
)
from .artifacts import canonical_bytes


class S5GraphitiMStarSemanticError(ValueError):
    """Sanitized semantic callback or upstream API failure."""


def _fail(code: str) -> S5GraphitiMStarSemanticError:
    return S5GraphitiMStarSemanticError(code)


def _node_uuid(node: object) -> str:
    value = node.get("uuid") if isinstance(node, Mapping) else getattr(node, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("resolved_node_uuid_missing")
    return value


def _node_projection(node: object) -> bytes:
    if isinstance(node, Mapping):
        value: object = dict(node)
    elif hasattr(node, "model_dump") and callable(node.model_dump):
        try:
            value = node.model_dump(mode="json")
        except Exception:
            raise _fail("resolved_node_projection_invalid") from None
    elif hasattr(node, "dict") and callable(node.dict):
        try:
            value = node.dict()
        except Exception:
            raise _fail("resolved_node_projection_invalid") from None
    else:
        raise _fail("resolved_node_projection_invalid")
    try:
        return canonical_bytes(value)
    except (TypeError, ValueError):
        raise _fail("resolved_node_projection_invalid") from None


def coalesce_compatible_resolved_nodes(nodes: Sequence[object]) -> list[object]:
    """Merge identical UUID/projection duplicates and reject conflicts."""

    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[object] = []
    by_uuid: dict[str, bytes] = {}
    for node in nodes:
        uuid = _node_uuid(node)
        projection = _node_projection(node)
        prior = by_uuid.get(uuid)
        if prior is None:
            by_uuid[uuid] = projection
            selected.append(node)
        elif prior != projection:
            raise _fail("conflicting_duplicate_uuid")
    return selected


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
ControlledProviderScope = Callable[[object], AbstractContextManager[object]]


class S5GraphitiMStarSemanticRuntime:
    """Run Graphiti's native semantic operations behind explicit callbacks."""

    def __init__(
        self,
        *,
        graphiti: object,
        binding: S5GraphitiSemanticBinding,
        latest_state_retriever: LatestStateRetriever,
        controlled_provider_scope: ControlledProviderScope | None = None,
        call_observer: Callable[[str], object] | None = None,
        require_native_commit_shape: bool = False,
    ) -> None:
        if graphiti is None:
            raise _fail("graphiti_missing")
        if not isinstance(binding, S5GraphitiSemanticBinding):
            raise _fail("semantic_binding_invalid")
        if not callable(latest_state_retriever):
            raise _fail("latest_state_retriever_invalid")
        if controlled_provider_scope is not None and not callable(
            controlled_provider_scope
        ):
            raise _fail("controlled_provider_scope_invalid")
        if call_observer is not None and not callable(call_observer):
            raise _fail("call_observer_invalid")
        if not isinstance(require_native_commit_shape, bool):
            raise _fail("require_native_commit_shape_invalid")
        self.graphiti = graphiti
        self.binding = binding
        self.latest_state_retriever = latest_state_retriever
        self.controlled_provider_scope = controlled_provider_scope
        self.call_observer = call_observer
        self.require_native_commit_shape = require_native_commit_shape
        self.last_edge_type_map: dict[tuple[str, str], list[str]] = {}
        self.resolved_node_coalescing_observations: list[
            dict[str, int | None]
        ] = []

    def _observe(self, name: str) -> None:
        if self.call_observer is not None:
            try:
                self.call_observer(name)
            except Exception:
                raise _fail("call_observer_failed") from None

    def _route_group(self, group_id: str) -> None:
        """Match Native ``add_episode`` database routing before semantic calls."""

        driver = getattr(self.graphiti, "driver", None)
        clients = getattr(self.graphiti, "clients", None)
        if driver is None or clients is None:
            return
        current_database = getattr(driver, "_database", None)
        if current_database == group_id:
            return
        clone = getattr(driver, "clone", None)
        if not callable(clone):
            raise _fail("group_database_routing_unavailable")
        try:
            routed = clone(database=group_id)
            self.graphiti.driver = routed
            clients.driver = routed
        except Exception:
            raise _fail("group_database_routing_failed") from None

    @staticmethod
    def _edge_type_map(source: GraphitiEpisodeInput) -> dict[tuple[str, str], list[str]]:
        if source.edge_type_map is not None:
            return {key: list(value) for key, value in source.edge_type_map.items()}
        if source.edge_types is not None:
            return {("Entity", "Entity"): list(source.edge_types.keys())}
        return {("Entity", "Entity"): []}

    @staticmethod
    def _validate_native_commit_result(value: object) -> None:
        if not isinstance(value, tuple) or len(value) != 2:
            raise _fail("native_commit_result_shape_invalid")
        episodic_edges, primary_episode = value
        if not isinstance(episodic_edges, list):
            raise _fail("native_commit_result_shape_invalid")
        if not hasattr(primary_episode, "uuid"):
            raise _fail("native_commit_result_shape_invalid")

    async def _await(self, value: object, code: str) -> object:
        if not inspect.isawaitable(value):
            raise _fail(code)
        try:
            return await value
        except S5GraphitiMStarSemanticError:
            raise
        except Exception:
            raise _fail(code) from None

    def _provider_context(self, providers: object | None) -> AbstractContextManager[object]:
        if providers is None:
            return nullcontext()
        if self.controlled_provider_scope is None:
            raise _fail("controlled_provider_scope_missing")
        try:
            context = self.controlled_provider_scope(providers)
        except Exception:
            raise _fail("controlled_provider_scope_failed") from None
        if not hasattr(context, "__enter__") or not hasattr(context, "__exit__"):
            raise _fail("controlled_provider_scope_invalid")
        return context

    async def prepare(
        self,
        source: GraphitiEpisodeInput,
        logical_time_ns: int,
        _providers: object | None = None,
    ) -> GraphitiPreparedBundle:
        """Execute Native extraction only; no canonical graph lookup occurs."""

        with self._provider_context(_providers):
            if isinstance(source, GraphitiEpisodeInput):
                self._route_group(source.group_id)
            return await self._prepare_active(source, logical_time_ns)

    async def _prepare_active(
        self,
        source: GraphitiEpisodeInput,
        logical_time_ns: int,
    ) -> GraphitiPreparedBundle:
        """Execute extraction while an optional controlled scope is active."""

        if not isinstance(source, GraphitiEpisodeInput):
            raise _fail("semantic_source_invalid")
        # Validate the clock even though extraction itself is state-independent;
        # the same logical time is later reused by bind/commit.
        logical_ns_to_datetime(logical_time_ns)
        try:
            self._observe("extract_nodes")
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
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        if isinstance(extracted_nodes, (str, bytes)) or not isinstance(
            extracted_nodes, Sequence
        ):
            raise _fail("extracted_nodes_invalid")
        if not isinstance(node_map, Mapping):
            raise _fail("node_episode_index_map_invalid")
        return GraphitiPreparedBundle(
            source=source,
            extracted_nodes=list(extracted_nodes),
            extracted_edges=[],
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

        with self._provider_context(_providers):
            if isinstance(prepared, GraphitiPreparedBundle):
                self._route_group(prepared.source.group_id)
            return await self._bind_active(
                prepared,
                logical_time_ns,
                source_sequence,
                _visible_publication_prefix,
            )

    async def _bind_active(
        self,
        prepared: GraphitiPreparedBundle,
        logical_time_ns: int,
        source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
    ) -> GraphitiBindObservation:
        """Execute latest-state resolution while the provider scope is active."""

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
        edge_type_map = self._edge_type_map(source)
        self.last_edge_type_map = dict(edge_type_map)
        try:
            self._observe("resolve_extracted_nodes")
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
            coalescing = {"pre_count": len(nodes), "post_count": None}
            self.resolved_node_coalescing_observations.append(coalescing)
            nodes = coalesce_compatible_resolved_nodes(nodes)
            coalescing["post_count"] = len(nodes)
            # Match pinned Graphiti.add_episode(): edge extraction is invoked
            # after node resolution, but it receives the original extracted
            # nodes so the prompt/UUID attribution remains Native-compatible.
            self._observe("extract_edges")
            extracted_edges = await self._await(
                self.binding.extract_edges(
                    self.graphiti.clients,
                    source.episode_node,
                    list(prepared.extracted_nodes),
                    previous_episodes,
                    edge_type_map,
                    source.group_id,
                    source.edge_types,
                    source.custom_extraction_instructions,
                ),
                "extract_edges_failed",
            )
            if isinstance(extracted_edges, (str, bytes)) or not isinstance(
                extracted_edges, Sequence
            ):
                raise _fail("extracted_edges_invalid")
            try:
                self._observe("resolve_edge_pointers")
                edges = self.binding.resolve_edge_pointers(
                    list(extracted_edges), uuid_map
                )
            except Exception:
                raise _fail("resolve_edge_pointers_failed") from None
            if isinstance(edges, (str, bytes)) or not isinstance(edges, Sequence):
                raise _fail("resolved_edge_pointers_invalid")
            self._observe("resolve_extracted_edges")
            resolved_edges, invalidated_edges, new_edges = await self._await(
                self.binding.resolve_extracted_edges(
                    self.graphiti.clients,
                    edges,
                    source.episode_node,
                    list(nodes),
                    dict(source.edge_types or {}),
                    edge_type_map,
                ),
                "resolve_edges_failed",
            )
            self._observe("extract_attributes_from_nodes")
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
            self._observe("process_episode_data")
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
            if self.require_native_commit_shape:
                self._validate_native_commit_result(committed)
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
    "coalesce_compatible_resolved_nodes",
    "logical_ns_to_datetime",
]
