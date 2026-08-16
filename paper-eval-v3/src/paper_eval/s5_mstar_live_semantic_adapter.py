"""Production-shape Graphiti source adapter for the S5 M*(C=2) lane.

The adapter owns only the Native ``add_episode`` input construction and the
two previous-episode reads.  The shared semantic runtime still executes the
qualified extraction, latest-state resolution, invalidation, and commit path.
No environment or service client is constructed in this module.
"""

from __future__ import annotations

import inspect
import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from .s5_graphiti_mstar_semantics import (
    GraphitiEpisodeInput,
    GraphitiPreparedBundle,
    S5GraphitiMStarSemanticRuntime,
    logical_ns_to_datetime,
)
from .s5_graphiti_semantic_binding import S5GraphitiSemanticBinding
from .s5_mstar_pipeline import MStarSource


RELEVANT_SCHEMA_LIMIT = 10
EXPECTED_SOURCE_COUNT = 49
MIN_EPOCH_NS = 1_000_000_000_000_000_000
MIN_LOGICAL_STEP_NS = 1_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE = re.compile(r"^pev3-s5-mstar-[0-9]{8}-[0-9]{3}$")
_EPISODE_KWARGS = {
    "name",
    "episode_body",
    "source_description",
    "reference_time",
    "source",
    "group_id",
}


class S5MStarLiveSemanticAdapterError(ValueError):
    """The live source projection or Native retrieval shape drifted."""


def _fail(code: str) -> S5MStarLiveSemanticAdapterError:
    return S5MStarLiveSemanticAdapterError(code)


def _namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.fullmatch(value) is None:
        raise _fail("namespace_invalid")
    return value


def materialize_s5_mstar_sources(
    episodes: Sequence[object],
    *,
    namespace: str,
    epoch_clock_ns: Callable[[], int] = time.time_ns,
) -> tuple[MStarSource, ...]:
    """Bind the exact 49-source workload to epoch logical operation times."""

    selected_namespace = _namespace(namespace)
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("source_inventory_invalid")
    selected = tuple(episodes)
    if len(selected) != EXPECTED_SOURCE_COUNT:
        raise _fail("source_count_invalid")
    if not callable(epoch_clock_ns):
        raise _fail("epoch_clock_invalid")

    result: list[MStarSource] = []
    prior_logical_time = -1
    for index, episode in enumerate(selected):
        source_sequence = getattr(episode, "source_sequence", None)
        source_sha256 = getattr(episode, "source_hash", None)
        group_id = getattr(episode, "group_id", None)
        if source_sequence != index:
            raise _fail("source_sequence_invalid")
        if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
            raise _fail("source_sha256_invalid")
        if group_id != selected_namespace:
            raise _fail("namespace_binding_invalid")
        try:
            tick = epoch_clock_ns()
        except Exception:
            raise _fail("epoch_clock_failed") from None
        if (
            isinstance(tick, bool)
            or not isinstance(tick, int)
            or tick < MIN_EPOCH_NS
        ):
            raise _fail("epoch_clock_invalid")
        # Graphiti materializes this value as a microsecond-resolution datetime.
        # Keep operation times distinct after that conversion.
        logical_time = max(tick, prior_logical_time + MIN_LOGICAL_STEP_NS)
        prior_logical_time = logical_time
        result.append(
            MStarSource(
                source_sequence=index,
                source_sha256=source_sha256,
                opaque_source=episode,
                logical_time_ns=logical_time,
            )
        )
    return tuple(result)


class S5MStarLiveSemanticAdapter:
    """Compose Native episode projection with the qualified M* semantic runtime."""

    def __init__(
        self,
        *,
        graphiti: object,
        semantic_binding: S5GraphitiSemanticBinding,
        graphiti_episode_kwargs: Callable[[object], Mapping[str, object]],
        episodic_node_type: Callable[..., object],
    ) -> None:
        if graphiti is None:
            raise _fail("graphiti_missing")
        if not callable(graphiti_episode_kwargs):
            raise _fail("episode_kwargs_missing")
        if not callable(episodic_node_type):
            raise _fail("episodic_node_type_missing")
        self.graphiti = graphiti
        self.graphiti_episode_kwargs = graphiti_episode_kwargs
        self.episodic_node_type = episodic_node_type
        self.semantic_runtime = S5GraphitiMStarSemanticRuntime(
            graphiti=graphiti,
            binding=semantic_binding,
            latest_state_retriever=self._retrieve_latest,
            require_native_commit_shape=True,
        )

    def _route_group(self, group_id: str) -> None:
        driver = getattr(self.graphiti, "driver", None)
        clients = getattr(self.graphiti, "clients", None)
        if driver is None or clients is None:
            raise _fail("graphiti_driver_missing")
        if getattr(driver, "_database", None) == group_id:
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

    async def _retrieve(self, source: GraphitiEpisodeInput) -> list[object]:
        retrieve = getattr(self.graphiti, "retrieve_episodes", None)
        if not callable(retrieve):
            raise _fail("retrieve_episodes_missing")
        reference_time = getattr(source.episode_node, "valid_at", None)
        episode_source = getattr(source.episode_node, "source", None)
        try:
            pending = retrieve(
                reference_time,
                last_n=RELEVANT_SCHEMA_LIMIT,
                group_ids=[source.group_id],
                source=episode_source,
            )
            if not inspect.isawaitable(pending):
                raise TypeError("retrieve_episodes must be async")
            rows = await pending
        except S5MStarLiveSemanticAdapterError:
            raise
        except Exception:
            raise _fail("retrieve_episodes_failed") from None
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise _fail("retrieve_episodes_shape_invalid")
        return list(rows)

    async def _retrieve_latest(self, source: GraphitiEpisodeInput) -> list[object]:
        self._route_group(source.group_id)
        return await self._retrieve(source)

    def _project_source(
        self,
        episode: object,
        *,
        logical_time_ns: int,
        previous_episodes: Sequence[object],
    ) -> GraphitiEpisodeInput:
        try:
            raw = self.graphiti_episode_kwargs(episode)
        except Exception:
            raise _fail("episode_kwargs_failed") from None
        if not isinstance(raw, Mapping) or set(raw) != _EPISODE_KWARGS:
            raise _fail("episode_kwargs_shape_invalid")
        values = dict(raw)
        group_id = _namespace(values.get("group_id"))
        reference_time = values.get("reference_time")
        if (
            not isinstance(reference_time, datetime)
            or reference_time.tzinfo is None
            or reference_time.utcoffset() is None
        ):
            raise _fail("reference_time_invalid")
        reference_time = reference_time.astimezone(timezone.utc)
        for field in ("name", "episode_body", "source_description"):
            if not isinstance(values.get(field), str) or not values[field]:
                raise _fail(f"{field}_invalid")
        if values.get("source") is None:
            raise _fail("episode_source_invalid")
        created_at = logical_ns_to_datetime(logical_time_ns)
        try:
            episode_node = self.episodic_node_type(
                name=values["name"],
                group_id=group_id,
                labels=[],
                source=values["source"],
                content=values["episode_body"],
                source_description=values["source_description"],
                created_at=created_at,
                valid_at=reference_time,
            )
        except Exception:
            raise _fail("episodic_node_construction_failed") from None
        return GraphitiEpisodeInput(
            episode_node=episode_node,
            previous_episodes=tuple(previous_episodes),
            group_id=group_id,
        )

    async def prepare(
        self, episode: object, logical_time_ns: int
    ) -> GraphitiPreparedBundle:
        """Capture the Native previous-episode snapshot, then run extraction."""

        provisional = self._project_source(
            episode,
            logical_time_ns=logical_time_ns,
            previous_episodes=(),
        )
        self._route_group(provisional.group_id)
        previous = await self._retrieve(provisional)
        source = GraphitiEpisodeInput(
            episode_node=provisional.episode_node,
            previous_episodes=tuple(previous),
            group_id=provisional.group_id,
        )
        return await self.semantic_runtime.prepare(source, logical_time_ns)

    async def bind(
        self,
        prepared: GraphitiPreparedBundle,
        logical_time_ns: int,
        source_sequence: int,
        visible_publication_prefix: tuple[int, ...],
    ) -> object:
        """Resolve against a fresh latest-state read and commit in source order."""

        return await self.semantic_runtime.bind(
            prepared,
            logical_time_ns,
            source_sequence,
            visible_publication_prefix,
        )


__all__ = [
    "EXPECTED_SOURCE_COUNT",
    "MIN_EPOCH_NS",
    "MIN_LOGICAL_STEP_NS",
    "RELEVANT_SCHEMA_LIMIT",
    "S5MStarLiveSemanticAdapter",
    "S5MStarLiveSemanticAdapterError",
    "materialize_s5_mstar_sources",
]
