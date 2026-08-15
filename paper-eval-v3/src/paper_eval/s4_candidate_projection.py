"""Production-facing logical edge projection for the S4 candidate sidecar."""

from __future__ import annotations

import contextvars
import copy
import hashlib
import inspect
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from .artifacts import payload_sha256
from .s4_candidate_sidecar_runtime import (
    CandidateSidecarRuntimeError,
    install_candidate_sidecar_hook,
)
from .s4_edge_identity_diagnosis import edge_identity_projection


PROJECTION_SCHEMA = {
    "candidate_identity": [
        "fact",
        "relation",
        "directed_source_endpoint",
        "directed_target_endpoint",
        "semantic_time",
        "expired_boolean",
        "semantic_attributes",
        "stable_provenance",
    ],
    "excluded_identity": [
        "candidate_position",
        "created_at",
        "group_id",
        "neo4j_id",
        "rank",
        "runtime_uuid",
    ],
    "partition_is_structural": True,
    "schema_version": "membind.paper-eval-v3.s4-candidate-projection.v1",
}
PROJECTION_SCHEMA_SHA256 = payload_sha256(PROJECTION_SCHEMA)


_CURRENT_ENTITIES: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("s4_resolution_entities", default=None)
)


def _value(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _sha(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CandidateSidecarRuntimeError(f"{field} is not a SHA256")
    return value


def _endpoint(value: Any) -> tuple[str, dict[str, Any]]:
    uuid = _value(value, "uuid")
    name = _value(value, "name")
    labels = _value(value, "labels")
    summary = _value(value, "summary")
    attributes = _value(value, "attributes") or {}
    group_id = _value(value, "group_id")
    if (
        not isinstance(uuid, str)
        or not uuid
        or not isinstance(name, str)
        or not name
        or not isinstance(labels, Sequence)
        or isinstance(labels, (str, bytes))
        or not isinstance(summary, str)
        or not isinstance(attributes, Mapping)
        or not isinstance(group_id, str)
        or not group_id
    ):
        raise CandidateSidecarRuntimeError("endpoint projection is incomplete")
    return uuid, {
        "attributes": copy.deepcopy(dict(attributes)),
        "group_id": group_id,
        "labels": list(labels),
        "normalized_name": name,
        "summary": summary,
    }


@contextmanager
def activate_resolution_entities(values: Sequence[Any]) -> Iterator[None]:
    """Expose current resolved nodes to concurrent inner edge tasks."""

    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise CandidateSidecarRuntimeError("resolution entities are malformed")
    selected: dict[str, dict[str, Any]] = {}
    for value in values:
        uuid, endpoint = _endpoint(value)
        if uuid in selected:
            raise CandidateSidecarRuntimeError("resolution entity UUID is duplicated")
        selected[uuid] = endpoint
    token = _CURRENT_ENTITIES.set(selected)
    try:
        yield
    finally:
        _CURRENT_ENTITIES.reset(token)


def _manifest(episodes: Sequence[Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(episodes, Sequence) or isinstance(episodes, (str, bytes)):
        raise CandidateSidecarRuntimeError("episode manifest is malformed")
    selected: dict[str, dict[str, Any]] = {}
    for expected_sequence, episode in enumerate(episodes):
        source_sequence = _value(episode, "source_sequence")
        source_hash = _value(episode, "source_hash")
        name = _value(episode, "name")
        body = _value(episode, "body")
        if (
            source_sequence != expected_sequence
            or not isinstance(name, str)
            or not name
            or name in selected
            or not isinstance(body, str)
        ):
            raise CandidateSidecarRuntimeError("episode manifest identity drift")
        selected[name] = {
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "source_hash": _sha(source_hash, field="source hash"),
            "source_sequence": source_sequence,
        }
    if not selected:
        raise CandidateSidecarRuntimeError("episode manifest is empty")
    return selected


def _edge(value: Any, *, namespace: str) -> dict[str, Any]:
    fields = (
        "attributes",
        "episodes",
        "expired_at",
        "fact",
        "invalid_at",
        "name",
        "reference_time",
        "source_node_uuid",
        "target_node_uuid",
        "valid_at",
    )
    selected = {field: _value(value, field) for field in fields}
    if _value(value, "group_id") != namespace:
        raise CandidateSidecarRuntimeError("edge group identity is foreign")
    return selected


class GraphitiCandidateProjector:
    """Resolve physical Graphiti candidates to stable, hash-only identities."""

    def __init__(
        self,
        *,
        driver: Any,
        namespace: str,
        episodes: Sequence[Any],
        entity_loader: Any | None = None,
        episode_loader: Any | None = None,
    ) -> None:
        if not isinstance(namespace, str) or not namespace.startswith("pev3-s4-"):
            raise CandidateSidecarRuntimeError("candidate namespace escaped S4")
        self.driver = driver
        self.namespace = namespace
        self.manifest = _manifest(episodes)
        self.episode_manifest_sha256 = payload_sha256(self.manifest)
        self.entity_loader = entity_loader
        self.episode_loader = episode_loader

    def _source(self, episode: Any) -> tuple[str, dict[str, Any]]:
        uuid = _value(episode, "uuid")
        name = _value(episode, "name")
        content = _value(episode, "content")
        expected = self.manifest.get(name) if isinstance(name, str) else None
        if (
            not isinstance(uuid, str)
            or not uuid
            or _value(episode, "group_id") != self.namespace
            or expected is None
            or not isinstance(content, str)
            or hashlib.sha256(content.encode("utf-8")).hexdigest()
            != expected["body_sha256"]
        ):
            raise CandidateSidecarRuntimeError(
                "runtime episode does not match frozen source"
            )
        return uuid, copy.deepcopy(expected)

    @staticmethod
    def _requested_ids(edges: Sequence[Any], fields: tuple[str, ...]) -> set[str]:
        selected: set[str] = set()
        for edge in edges:
            for field in fields:
                value = _value(edge, field)
                values = value if field == "episodes" else [value]
                if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                    raise CandidateSidecarRuntimeError("edge join identity is malformed")
                for item in values:
                    if not isinstance(item, str) or not item:
                        raise CandidateSidecarRuntimeError("edge join identity is missing")
                    selected.add(item)
        return selected

    async def _endpoints(self, edges: Sequence[Any]) -> dict[str, dict[str, Any]]:
        current = _CURRENT_ENTITIES.get()
        if current is None:
            raise CandidateSidecarRuntimeError("resolution entity context is missing")
        requested = self._requested_ids(
            edges, ("source_node_uuid", "target_node_uuid")
        )
        selected = {
            uuid: copy.deepcopy(value)
            for uuid, value in current.items()
            if uuid in requested
        }
        missing = requested - set(selected)
        if missing:
            loader = self.entity_loader
            if loader is None:
                from graphiti_core.nodes import EntityNode

                loader = EntityNode.get_by_uuids
            loaded = loader(
                self.driver,
                sorted(missing),
                group_id=self.namespace,
            )
            if inspect.isawaitable(loaded):
                loaded = await loaded
            for value in loaded:
                uuid, endpoint = _endpoint(value)
                if uuid in selected or endpoint["group_id"] != self.namespace:
                    raise CandidateSidecarRuntimeError(
                        "endpoint join is duplicate or foreign"
                    )
                selected[uuid] = endpoint
        if set(selected) != requested or any(
            value.pop("group_id", None) != self.namespace
            for value in selected.values()
        ):
            raise CandidateSidecarRuntimeError("endpoint join is incomplete")
        return selected

    async def _provenance(
        self,
        edges: Sequence[Any],
        current_episode: Any,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        current_uuid, source = self._source(current_episode)
        requested = self._requested_ids(edges, ("episodes",))
        selected = {current_uuid: source} if current_uuid in requested else {}
        missing = requested - set(selected)
        if missing:
            loader = self.episode_loader
            if loader is None:
                from graphiti_core.nodes import EpisodicNode

                loader = EpisodicNode.get_by_uuids
            loaded = loader(self.driver, sorted(missing))
            if inspect.isawaitable(loaded):
                loaded = await loaded
            for value in loaded:
                uuid, resolved = self._source(value)
                if uuid in selected:
                    raise CandidateSidecarRuntimeError(
                        "provenance join is duplicated"
                    )
                selected[uuid] = resolved
        if set(selected) != requested:
            raise CandidateSidecarRuntimeError("provenance join is incomplete")
        return selected, source

    def _candidate_entries(
        self,
        edges: Sequence[Any],
        *,
        offset: int,
        endpoints: Mapping[str, Mapping[str, Any]],
        provenance: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for ordinal, edge in enumerate(edges):
            mapped = _edge(edge, namespace=self.namespace)
            fact = mapped.get("fact")
            if not isinstance(fact, str) or not fact:
                raise CandidateSidecarRuntimeError("candidate fact is missing")
            try:
                identity = payload_sha256(
                    edge_identity_projection(mapped, endpoints, provenance)
                )
            except (TypeError, ValueError) as error:
                raise CandidateSidecarRuntimeError(
                    "candidate logical identity is incomplete"
                ) from error
            selected.append(
                {
                    "candidate_id": offset + ordinal,
                    "fact_sha256": hashlib.sha256(fact.encode("utf-8")).hexdigest(),
                    "logical_identity_sha256": identity,
                }
            )
        return selected

    async def project(
        self,
        *,
        extracted_edge: Any,
        related_edges: Sequence[Any],
        invalidation_edges: Sequence[Any],
        episode: Any,
    ) -> dict[str, Any]:
        all_edges = [extracted_edge, *related_edges, *invalidation_edges]
        if any(_value(edge, "group_id") != self.namespace for edge in all_edges):
            raise CandidateSidecarRuntimeError("edge group identity is foreign")
        endpoints = await self._endpoints(all_edges)
        provenance, source = await self._provenance(all_edges, episode)
        related = self._candidate_entries(
            related_edges,
            offset=0,
            endpoints=endpoints,
            provenance=provenance,
        )
        invalidation = self._candidate_entries(
            invalidation_edges,
            offset=len(related),
            endpoints=endpoints,
            provenance=provenance,
        )
        try:
            new_edge_identity = payload_sha256(
                edge_identity_projection(
                    _edge(extracted_edge, namespace=self.namespace),
                    endpoints,
                    provenance,
                )
            )
        except (TypeError, ValueError) as error:
            raise CandidateSidecarRuntimeError(
                "new edge logical identity is incomplete"
            ) from error
        membership = {
            "related": sorted(
                value["logical_identity_sha256"] for value in related
            ),
            "invalidation": sorted(
                value["logical_identity_sha256"] for value in invalidation
            ),
        }
        logical_call = payload_sha256(
            {
                "candidate_membership_sha256": payload_sha256(membership),
                "new_edge_identity_sha256": new_edge_identity,
                "source_hash": source["source_hash"],
                "source_sequence": source["source_sequence"],
            }
        )
        return {
            "source_sequence": source["source_sequence"],
            "source_hash": source["source_hash"],
            "logical_call_sha256": logical_call,
            "related": related,
            "invalidation": invalidation,
        }


def _argument(
    args: tuple[Any, ...], kwargs: Mapping[str, Any], index: int, name: str
) -> Any:
    if len(args) > index:
        return args[index]
    if name in kwargs:
        return kwargs[name]
    raise CandidateSidecarRuntimeError(f"outer hook argument {name} is missing")


@contextmanager
def install_graphiti_candidate_sidecar_hooks(
    edge_operations_module: Any,
    *,
    projector: GraphitiCandidateProjector,
    phase_owner: Any | None = None,
    replay_binder: Any | None = None,
) -> Iterator[None]:
    """Install outer entity context plus the inner pre-prompt projection hook."""

    owner = phase_owner or edge_operations_module
    attribute = (
        "_extract_and_resolve_edges" if phase_owner is not None else "resolve_extracted_edges"
    )
    original = getattr(owner, attribute, None)
    if not callable(original):
        raise CandidateSidecarRuntimeError("outer edge resolution hook is unavailable")
    entity_index = 6 if phase_owner is not None else 3
    entity_name = "nodes" if phase_owner is not None else "entities"

    async def outer(*args: Any, **kwargs: Any) -> Any:
        entities = _argument(args, kwargs, entity_index, entity_name)
        with activate_resolution_entities(entities):
            selected = original(*args, **kwargs)
            return await selected if inspect.isawaitable(selected) else selected

    original_publication = None
    if replay_binder is not None:
        if phase_owner is None:
            raise CandidateSidecarRuntimeError(
                "replay publication guard requires a Graphiti phase owner"
            )
        original_publication = getattr(phase_owner, "_process_episode_data", None)
        if not callable(original_publication):
            raise CandidateSidecarRuntimeError(
                "Graphiti publication guard is unavailable"
            )

        async def guarded_publication(*args: Any, **kwargs: Any) -> Any:
            episode = _argument(args, kwargs, 0, "episode")
            if isinstance(episode, Sequence) and not isinstance(
                episode, (str, bytes)
            ):
                if len(episode) != 1:
                    raise CandidateSidecarRuntimeError(
                        "S4 publication episode batch is unsupported"
                    )
                episode = episode[0]
            _, source = projector._source(episode)
            if replay_binder.prepared_count != 0:
                raise CandidateSidecarRuntimeError(
                    "sidecar replay has a prepared call before publication"
                )
            if replay_binder.remaining_for_source(source["source_sequence"]) != 0:
                raise CandidateSidecarRuntimeError(
                    "sidecar replay has unconsumed capture calls before publication"
                )
            selected = original_publication(*args, **kwargs)
            return await selected if inspect.isawaitable(selected) else selected

    setattr(owner, attribute, outer)
    if original_publication is not None:
        setattr(phase_owner, "_process_episode_data", guarded_publication)
    try:
        with install_candidate_sidecar_hook(
            edge_operations_module,
            projector=projector.project,
        ):
            yield
    finally:
        if original_publication is not None:
            setattr(phase_owner, "_process_episode_data", original_publication)
        setattr(owner, attribute, original)
