"""Variable-size S6 identity shim over the qualified Graphiti M* adapter."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone

from .s5_graphiti_mstar_semantics import GraphitiEpisodeInput, logical_ns_to_datetime
from .s5_mstar_live_semantic_adapter import S5MStarLiveSemanticAdapter
from .s5_mstar_pipeline import MStarSource
from .s6_calibration_contract import CONCURRENCIES, DEVELOPMENT_HISTORIES


MIN_EPOCH_NS = 1_000_000_000_000_000_000
MIN_LOGICAL_STEP_NS = 1_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_NAMESPACE = re.compile(
    rf"^pev3-s6-({'|'.join(DEVELOPMENT_HISTORIES)})-mstar-"
    rf"c({'|'.join(str(value) for value in CONCURRENCIES)})-001$"
)
_EPISODE_KWARGS = {
    "name",
    "episode_body",
    "source_description",
    "reference_time",
    "source",
    "group_id",
}


class S6GraphitiMStarAdapterError(ValueError):
    """The variable S6 source or namespace projection is invalid."""


def _fail(code: str) -> S6GraphitiMStarAdapterError:
    return S6GraphitiMStarAdapterError(code)


def _namespace(value: object) -> str:
    if not isinstance(value, str) or _NAMESPACE.fullmatch(value) is None:
        raise _fail("namespace_invalid")
    return value


def materialize_s6_mstar_sources(
    episodes: Sequence[object],
    *,
    namespace: str,
    epoch_clock_ns: Callable[[], int] = time.time_ns,
) -> tuple[MStarSource, ...]:
    """Bind any nonempty frozen S6 history to monotonic epoch logical times."""

    selected_namespace = _namespace(namespace)
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence):
        raise _fail("source_inventory_invalid")
    selected = tuple(episodes)
    if not selected:
        raise _fail("source_count_invalid")
    if not callable(epoch_clock_ns):
        raise _fail("epoch_clock_invalid")
    sources: list[MStarSource] = []
    prior = -1
    for index, episode in enumerate(selected):
        source_sequence = getattr(episode, "source_sequence", None)
        source_sha256 = getattr(episode, "source_hash", None)
        if source_sequence != index:
            raise _fail("source_sequence_invalid")
        if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
            raise _fail("source_sha256_invalid")
        if getattr(episode, "group_id", None) != selected_namespace:
            raise _fail("namespace_binding_invalid")
        try:
            tick = epoch_clock_ns()
        except Exception:
            raise _fail("epoch_clock_failed") from None
        if isinstance(tick, bool) or not isinstance(tick, int) or tick < MIN_EPOCH_NS:
            raise _fail("epoch_clock_invalid")
        logical_time = max(tick, prior + MIN_LOGICAL_STEP_NS)
        prior = logical_time
        sources.append(
            MStarSource(
                source_sequence=index,
                source_sha256=source_sha256,
                opaque_source=episode,
                logical_time_ns=logical_time,
            )
        )
    return tuple(sources)


class S6MStarLiveSemanticAdapter(S5MStarLiveSemanticAdapter):
    """Keep S5 retrieval/bind semantics while accepting only S6 M* namespaces."""

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
        try:
            node = self.episodic_node_type(
                name=values["name"],
                group_id=group_id,
                labels=[],
                source=values["source"],
                content=values["episode_body"],
                source_description=values["source_description"],
                created_at=logical_ns_to_datetime(logical_time_ns),
                valid_at=reference_time,
            )
        except Exception:
            raise _fail("episodic_node_construction_failed") from None
        return GraphitiEpisodeInput(
            episode_node=node,
            previous_episodes=tuple(previous_episodes),
            group_id=group_id,
        )


__all__ = [
    "MIN_EPOCH_NS",
    "MIN_LOGICAL_STEP_NS",
    "S6GraphitiMStarAdapterError",
    "S6MStarLiveSemanticAdapter",
    "materialize_s6_mstar_sources",
]
