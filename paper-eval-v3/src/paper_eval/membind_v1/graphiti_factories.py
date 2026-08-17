"""Lazy pinned-Graphiti materialization for the MemBind-v1 live lane.

The source log is built from the exact LongMemEval episode renderer before a
Graphiti client is handed to the method.  Runtime node construction is then
injected as two tiny factories, keeping this module testable without importing
``graphiti_core`` or contacting any service.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from paper_eval.membind_v1.source_log import SourceLog, SourceRecord


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_DESCRIPTION = "LongMemEval-S haystack session"


class GraphitiFactoryError(ValueError):
    """A source hydration or lazy Graphiti node materialization failed."""


def _fail(code: str) -> GraphitiFactoryError:
    return GraphitiFactoryError(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(code)
    return value


def _ns(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("reference time invalid")
    return value


def _raw_episode_field(episode: object, name: str) -> object:
    value = getattr(episode, name, None)
    if value is None:
        raise _fail(f"episode {name} missing")
    return value


def _episode_uuid(*, namespace: str, source_hash: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"membind-v1:{namespace}:{source_hash}"))


ReferenceTimeToNs = Callable[[str], int]


def build_source_log_from_episodes(
    episodes: Sequence[object],
    *,
    namespace: str,
    reference_time_to_ns: ReferenceTimeToNs,
) -> tuple[SourceLog, tuple[str, ...]]:
    """Freeze one fresh namespace's rendered episodes and raw source hashes."""

    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence) or not episodes:
        raise _fail("episodes invalid")
    if not callable(reference_time_to_ns):
        raise _fail("reference time parser invalid")
    group_id = _text(namespace, "namespace invalid")
    records: list[SourceRecord] = []
    raw_hashes: list[str] = []
    for expected_sequence, episode in enumerate(episodes):
        sequence = _raw_episode_field(episode, "source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != expected_sequence:
            raise _fail("source sequence invalid")
        raw_hash = _text(_raw_episode_field(episode, "source_hash"), "raw source identity invalid")
        if _SHA256.fullmatch(raw_hash) is None or raw_hash in raw_hashes:
            raise _fail("raw source identity invalid")
        reference_time = _text(_raw_episode_field(episode, "reference_time"), "reference time invalid")
        name = _text(_raw_episode_field(episode, "name"), "episode name invalid")
        body = _text(_raw_episode_field(episode, "body"), "episode body invalid")
        try:
            timestamp_ns = _ns(reference_time_to_ns(reference_time))
        except GraphitiFactoryError:
            raise
        except Exception:
            raise _fail("reference time invalid") from None
        records.append(
            SourceRecord.create(
                source_sequence=sequence,
                episode_uuid=_episode_uuid(namespace=group_id, source_hash=raw_hash),
                group_id=group_id,
                reference_time_ns=timestamp_ns,
                source_filter="message",
                episode_projection={
                    "name": name,
                    "body": body,
                    "source_description": _SOURCE_DESCRIPTION,
                    "reference_time": reference_time,
                },
            )
        )
        raw_hashes.append(raw_hash)
    return SourceLog.create(records), tuple(raw_hashes)


@dataclass(frozen=True, slots=True)
class GraphitiNodeFactories:
    """The two adapter factories needed after a live runtime exists."""

    episode_factory: Callable[[SourceRecord], object]
    extracted_node_factory: Callable[[dict[str, object]], object]


def _datetime_from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=nanoseconds // 1_000
    )


def make_graphiti_node_factories(
    *,
    episodic_node_type: Callable[..., object],
    entity_node_type: Callable[..., object],
    message_source: object,
) -> GraphitiNodeFactories:
    """Build Graphiti-specific factories only after the caller imported types."""

    if not callable(episodic_node_type) or not callable(entity_node_type):
        raise _fail("graphiti node constructor invalid")
    if message_source is None:
        raise _fail("message source invalid")

    def episode_factory(record: SourceRecord) -> object:
        if not isinstance(record, SourceRecord):
            raise _fail("source record invalid")
        projection = record.episode_projection
        try:
            return episodic_node_type(
                uuid=record.episode_uuid,
                name=_text(projection.get("name"), "episode projection invalid"),
                group_id=record.group_id,
                labels=[],
                source=message_source,
                source_description=_text(
                    projection.get("source_description"), "episode projection invalid"
                ),
                content=_text(projection.get("body"), "episode projection invalid"),
                valid_at=_datetime_from_ns(record.reference_time_ns),
            )
        except GraphitiFactoryError:
            raise
        except Exception:
            raise _fail("episodic node materialization failed") from None

    def extracted_node_factory(projection: dict[str, object]) -> object:
        if not isinstance(projection, dict):
            raise _fail("extracted node projection invalid")
        try:
            return entity_node_type(**dict(projection))
        except Exception:
            raise _fail("entity node materialization failed") from None

    return GraphitiNodeFactories(
        episode_factory=episode_factory,
        extracted_node_factory=extracted_node_factory,
    )


__all__ = [
    "GraphitiFactoryError",
    "GraphitiNodeFactories",
    "build_source_log_from_episodes",
    "make_graphiti_node_factories",
]
