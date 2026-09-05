"""Method-independent Graphiti episode adapter.

The live path is intentionally a direct call to ``Graphiti.add_episode``.
There is no prompt rewrite, JSON repair, retry loop, finite-pair patch, or
custom deduplication in this boundary.
"""

from __future__ import annotations

import inspect
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


def parse_reference_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reference_time must be an ISO-8601 string or datetime")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        # The frozen LongMemEval source uses this human-readable timestamp.
        return datetime.strptime(value.strip(), "%Y/%m/%d (%a) %H:%M")


@dataclass(frozen=True, slots=True)
class GraphitiEpisode:
    name: str
    body: str
    source_description: str
    reference_time: str | datetime
    uuid: str | None = None
    group_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("name", "body", "source_description"):
            if not isinstance(getattr(self, field_name), str) or not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must be non-empty")


class GraphitiNative:
    """Direct upstream Native wrapper used by Serial and Async baselines."""

    def __init__(self, graphiti: Any) -> None:
        if not hasattr(graphiti, "add_episode") or not callable(graphiti.add_episode):
            raise TypeError("graphiti must expose add_episode")
        self.graphiti = graphiti

    async def add_episode(self, episode: GraphitiEpisode) -> Any:
        if not isinstance(episode, GraphitiEpisode):
            raise TypeError("episode must be GraphitiEpisode")
        # Graphiti treats a supplied UUID as an existing episode identity and
        # performs a lookup before writing.  MAB chunk IDs are provenance keys,
        # not UUIDs in Graphiti's namespace; retain them in the name instead.
        episode_uuid = None
        if episode.uuid:
            try:
                episode_uuid = str(uuid.UUID(episode.uuid))
            except ValueError:
                episode_uuid = None
        result = self.graphiti.add_episode(
            name=episode.name,
            episode_body=episode.body,
            source_description=episode.source_description,
            reference_time=parse_reference_time(episode.reference_time),
            group_id=episode.group_id,
            uuid=episode_uuid,
        )
        return await result if inspect.isawaitable(result) else result
