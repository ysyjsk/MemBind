"""Frozen-V6 certified extraction adapter and no-reuse stateful suffix.

This module makes the Phase-1 boundary executable without changing Frozen V6:
node/edge extraction is materialized exactly once, while each stateful branch
receives a deep clone and stops before authoritative publication.
"""

from __future__ import annotations

import copy
import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .graphiti_observer import canonical_digest


AsyncCallable = Callable[..., Awaitable[Any] | Any]


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True, slots=True)
class PreparedExtractionBindings:
    extract_nodes: AsyncCallable
    extract_edges: AsyncCallable


@dataclass(frozen=True, slots=True)
class StatefulSuffixBindings:
    resolve_nodes: AsyncCallable
    resolve_edges: AsyncCallable
    hydrate_nodes: AsyncCallable
    build_continuation: Callable[..., Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class FrozenV6PreparedArtifact:
    source_sequence: int
    source_workload_digest: str
    episode: Any
    previous_episodes: tuple[Any, ...]
    extracted_nodes: tuple[Any, ...]
    extracted_edges: tuple[Any, ...]
    node_episode_index_map: Mapping[str, list[int]]
    provider_transcript_digest: str
    request_sequence_digest: str
    previous_context_policy: str
    previous_context_digest: str
    logical_extraction_call_count: int = 2
    physical_extraction_call_count: int = 2

    def __post_init__(self) -> None:
        if isinstance(self.source_sequence, bool) or self.source_sequence < 0:
            raise ValueError("prepared source sequence is invalid")
        required = (
            self.source_workload_digest,
            self.provider_transcript_digest,
            self.request_sequence_digest,
            self.previous_context_policy,
            self.previous_context_digest,
        )
        if not all(isinstance(value, str) and value for value in required):
            raise ValueError("prepared semantic identity is incomplete")
        if self.logical_extraction_call_count != 2:
            raise ValueError("Frozen V6 Prepared extraction must contain exactly two logical calls")
        if (
            isinstance(self.physical_extraction_call_count, bool)
            or not isinstance(self.physical_extraction_call_count, int)
            or self.physical_extraction_call_count < self.logical_extraction_call_count
        ):
            raise ValueError("Frozen V6 physical extraction call count is invalid")
        object.__setattr__(self, "episode", copy.deepcopy(self.episode))
        object.__setattr__(self, "previous_episodes", tuple(copy.deepcopy(self.previous_episodes)))
        object.__setattr__(self, "extracted_nodes", tuple(copy.deepcopy(self.extracted_nodes)))
        object.__setattr__(self, "extracted_edges", tuple(copy.deepcopy(self.extracted_edges)))
        object.__setattr__(self, "node_episode_index_map", copy.deepcopy(dict(self.node_episode_index_map)))

    @property
    def semantic_output_digest(self) -> str:
        return canonical_digest(
            {
                "episode": self.episode,
                "previous_episodes": self.previous_episodes,
                "extracted_nodes": self.extracted_nodes,
                "extracted_edges": self.extracted_edges,
                "node_episode_index_map": self.node_episode_index_map,
            }
        )

    @property
    def uuid_time_randomness_digest(self) -> str:
        return canonical_digest(
            {
                "episode_uuid": _field(self.episode, "uuid"),
                "episode_created_at": _field(self.episode, "created_at"),
                "node_uuids": [_field(node, "uuid") for node in self.extracted_nodes],
                "node_created_at": [_field(node, "created_at") for node in self.extracted_nodes],
                "edge_uuids": [_field(edge, "uuid") for edge in self.extracted_edges],
                "edge_created_at": [_field(edge, "created_at") for edge in self.extracted_edges],
            }
        )

    @property
    def artifact_digest(self) -> str:
        return canonical_digest(
            {
                "source_sequence": self.source_sequence,
                "source_workload_digest": self.source_workload_digest,
                "semantic_output_digest": self.semantic_output_digest,
                "provider_transcript_digest": self.provider_transcript_digest,
                "request_sequence_digest": self.request_sequence_digest,
                "previous_context_policy": self.previous_context_policy,
                "previous_context_digest": self.previous_context_digest,
                "uuid_time_randomness_digest": self.uuid_time_randomness_digest,
                "logical_extraction_call_count": self.logical_extraction_call_count,
                "physical_extraction_call_count": self.physical_extraction_call_count,
            }
        )

    def clone(self) -> "FrozenV6PreparedArtifact":
        return copy.deepcopy(self)


@dataclass(frozen=True, slots=True)
class PreparedNoReuseResolution:
    prepared_artifact_digest: str
    read_epoch: str
    nodes: tuple[Any, ...]
    entity_edges: tuple[Any, ...]
    continuation_k: Mapping[str, Any]
    database_writes: int
    started_ns: int
    ended_ns: int

    @property
    def duration_ns(self) -> int:
        return max(0, self.ended_ns - self.started_ns)


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _semantic_without_randomness(value: Any) -> Any:
    """Project extraction output while excluding only bound random identity."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_without_randomness(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if key not in {"uuid", "created_at"}
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_without_randomness(child) for child in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _semantic_without_randomness(dump(mode="python"))
    namespace = getattr(value, "__dict__", None)
    if isinstance(namespace, Mapping):
        return _semantic_without_randomness(
            {key: child for key, child in namespace.items() if not str(key).startswith("_")}
        )
    return value


def install_prepared_randomness_binding(module: Any, artifact: FrozenV6PreparedArtifact):
    """Bind replay UUID/time to the once-materialized Prepared objects.

    The original node/edge extraction functions still execute, including
    Frozen-V6 transcript replay.  Their semantic outputs are checked before
    replacing only the freshly sampled object identity with Prepared values.
    """

    original_nodes = module.extract_nodes
    original_edges = module.extract_edges
    original_episode = module.EpisodicNode
    original_now = module.utc_now

    async def nodes(*args: Any, **kwargs: Any):
        actual_nodes, actual_index = await _maybe_await(original_nodes(*args, **kwargs))
        if _semantic_without_randomness(actual_nodes) != _semantic_without_randomness(
            artifact.extracted_nodes
        ):
            raise ValueError("Frozen V6 replay node semantic output changed")
        if sorted(len(value) for value in actual_index.values()) != sorted(
            len(value) for value in artifact.node_episode_index_map.values()
        ):
            raise ValueError("Frozen V6 replay node index semantics changed")
        return copy.deepcopy(list(artifact.extracted_nodes)), copy.deepcopy(
            dict(artifact.node_episode_index_map)
        )

    async def edges(*args: Any, **kwargs: Any):
        actual_edges = await _maybe_await(original_edges(*args, **kwargs))
        if _semantic_without_randomness(actual_edges) != _semantic_without_randomness(
            artifact.extracted_edges
        ):
            raise ValueError("Frozen V6 replay edge semantic output changed")
        return copy.deepcopy(list(artifact.extracted_edges))

    def episode(*args: Any, **kwargs: Any):
        values = dict(kwargs)
        values["uuid"] = _field(artifact.episode, "uuid")
        values["created_at"] = _field(artifact.episode, "created_at")
        return original_episode(*args, **values)

    module.extract_nodes = nodes
    module.extract_edges = edges
    module.EpisodicNode = episode
    module.utc_now = lambda: copy.deepcopy(_field(artifact.episode, "created_at"))
    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        module.extract_nodes = original_nodes
        module.extract_edges = original_edges
        module.EpisodicNode = original_episode
        module.utc_now = original_now

    return restore


def _default_prepared_bindings() -> PreparedExtractionBindings:
    from graphiti_core.utils.maintenance.edge_operations import extract_edges
    from graphiti_core.utils.maintenance.node_operations import extract_nodes

    async def nodes(clients: Any, episode: Any, previous: Sequence[Any], kwargs: Mapping[str, Any]):
        return await extract_nodes(
            clients,
            episode,
            list(previous),
            kwargs.get("entity_types"),
            kwargs.get("excluded_entity_types"),
            kwargs.get("custom_extraction_instructions"),
        )

    async def edges(clients: Any, episode: Any, extracted: Sequence[Any], previous: Sequence[Any], kwargs: Mapping[str, Any]):
        edge_types = kwargs.get("edge_types")
        edge_map = kwargs.get("edge_type_map") or (
            {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
        )
        return await extract_edges(
            clients,
            episode,
            list(extracted),
            list(previous),
            edge_map,
            str(kwargs["group_id"]),
            edge_types,
            kwargs.get("custom_extraction_instructions"),
        )

    return PreparedExtractionBindings(nodes, edges)


def _default_suffix_bindings() -> StatefulSuffixBindings:
    from graphiti_core.graphiti import extract_attributes_from_nodes, resolve_extracted_nodes
    from graphiti_core.utils.bulk_utils import resolve_edge_pointers
    from graphiti_core.utils.maintenance.edge_operations import resolve_extracted_edges

    async def nodes(clients: Any, extracted: list[Any], episode: Any, previous: Sequence[Any], kwargs: Mapping[str, Any]):
        return await resolve_extracted_nodes(
            clients,
            extracted,
            episode,
            list(previous),
            kwargs.get("entity_types"),
        )

    async def edges(
        clients: Any,
        extracted: list[Any],
        episode: Any,
        resolved_nodes: list[Any],
        uuid_map: Mapping[str, str],
        kwargs: Mapping[str, Any],
    ):
        edge_types = kwargs.get("edge_types")
        edge_map = kwargs.get("edge_type_map") or (
            {("Entity", "Entity"): list(edge_types)} if edge_types else {("Entity", "Entity"): []}
        )
        pointed = resolve_edge_pointers(extracted, dict(uuid_map))
        return await resolve_extracted_edges(
            clients,
            pointed,
            episode,
            resolved_nodes,
            edge_types or {},
            edge_map,
        )

    async def hydrate(
        clients: Any,
        resolved_nodes: list[Any],
        episode: Any,
        previous: Sequence[Any],
        new_edges: Sequence[Any],
        kwargs: Mapping[str, Any],
    ):
        return await extract_attributes_from_nodes(
            clients,
            resolved_nodes,
            episode,
            list(previous),
            kwargs.get("entity_types"),
            edges=list(new_edges),
        )

    def continuation(**kwargs: Any) -> Mapping[str, Any]:
        return {
            "schema_version": "membind.v7.continuation-k.v1",
            "seam": "BEFORE_NATIVE_PROCESS_EPISODE_DATA",
            **kwargs,
        }

    return StatefulSuffixBindings(nodes, edges, hydrate, continuation)


async def prepare_frozen_v6_artifact_async(
    *,
    clients: Any,
    source_sequence: int,
    source_workload_digest: str,
    episode: Any,
    previous_episodes: Sequence[Any],
    episode_kwargs: Mapping[str, Any],
    provider_transcript_digest: str,
    request_sequence_digest: str,
    previous_context_policy: str,
    previous_context_digest: str,
    physical_extraction_call_count: int = 2,
    bindings: PreparedExtractionBindings | None = None,
) -> FrozenV6PreparedArtifact:
    """Execute only the two Frozen-V6 certified extraction operations."""

    selected = bindings or _default_prepared_bindings()
    previous = tuple(copy.deepcopy(list(previous_episodes)))
    prepared_episode = copy.deepcopy(episode)
    extracted_nodes, index_map = await _maybe_await(
        selected.extract_nodes(clients, prepared_episode, previous, dict(episode_kwargs))
    )
    extracted_edges = await _maybe_await(
        selected.extract_edges(
            clients,
            prepared_episode,
            list(extracted_nodes),
            previous,
            dict(episode_kwargs),
        )
    )
    return FrozenV6PreparedArtifact(
        source_sequence=int(source_sequence),
        source_workload_digest=str(source_workload_digest),
        episode=prepared_episode,
        previous_episodes=previous,
        extracted_nodes=tuple(extracted_nodes),
        extracted_edges=tuple(extracted_edges),
        node_episode_index_map=dict(index_map),
        provider_transcript_digest=str(provider_transcript_digest),
        request_sequence_digest=str(request_sequence_digest),
        previous_context_policy=str(previous_context_policy),
        previous_context_digest=str(previous_context_digest),
        physical_extraction_call_count=int(physical_extraction_call_count),
    )


async def resolve_prepared_no_reuse_async(
    *,
    clients: Any,
    artifact: FrozenV6PreparedArtifact,
    episode_kwargs: Mapping[str, Any],
    publication_frontier: int,
    backend_epoch: str,
    read_epoch: str,
    authoritative_previous_episodes: Sequence[Any] | None = None,
    bindings: StatefulSuffixBindings | None = None,
) -> PreparedNoReuseResolution:
    """Run the native stateful suffix on a clone and stop before publication."""

    if isinstance(publication_frontier, bool) or publication_frontier < 0:
        raise ValueError("publication frontier is invalid")
    if not backend_epoch or not read_epoch:
        raise ValueError("stateful suffix epoch identity is incomplete")
    selected = bindings or _default_suffix_bindings()
    clone = artifact.clone()
    episode = clone.episode
    previous = tuple(
        copy.deepcopy(
            list(
                clone.previous_episodes
                if authoritative_previous_episodes is None
                else authoritative_previous_episodes
            )
        )
    )
    extracted_nodes = list(clone.extracted_nodes)
    extracted_edges = list(clone.extracted_edges)
    kwargs = dict(episode_kwargs)
    started = time.monotonic_ns()
    nodes, uuid_map, _duplicates = await _maybe_await(
        selected.resolve_nodes(clients, extracted_nodes, episode, previous, kwargs)
    )
    resolved_edges, invalidated_edges, new_edges = await _maybe_await(
        selected.resolve_edges(
            clients,
            extracted_edges,
            episode,
            list(nodes),
            dict(uuid_map),
            kwargs,
        )
    )
    entity_edges = list(resolved_edges) + list(invalidated_edges)
    hydrated_nodes = await _maybe_await(
        selected.hydrate_nodes(
            clients,
            list(nodes),
            episode,
            previous,
            list(new_edges),
            kwargs,
        )
    )
    continuation = selected.build_continuation(
        episodes=[episode],
        nodes=list(hydrated_nodes),
        entity_edges=entity_edges,
        node_episode_index_map=copy.deepcopy(dict(clone.node_episode_index_map)),
        now=_field(episode, "created_at"),
        group_id=str(kwargs["group_id"]),
        backend_epoch=str(backend_epoch),
        publication_frontier=int(publication_frontier),
        saga=None,
        saga_previous_episode_uuid=None,
        update_communities=False,
    )
    ended = time.monotonic_ns()
    return PreparedNoReuseResolution(
        prepared_artifact_digest=artifact.artifact_digest,
        read_epoch=str(read_epoch),
        nodes=tuple(copy.deepcopy(list(hydrated_nodes))),
        entity_edges=tuple(copy.deepcopy(entity_edges)),
        continuation_k=copy.deepcopy(dict(continuation)),
        database_writes=0,
        started_ns=started,
        ended_ns=ended,
    )


__all__ = [
    "FrozenV6PreparedArtifact",
    "PreparedExtractionBindings",
    "PreparedNoReuseResolution",
    "StatefulSuffixBindings",
    "install_prepared_randomness_binding",
    "prepare_frozen_v6_artifact_async",
    "resolve_prepared_no_reuse_async",
]
