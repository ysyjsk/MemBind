"""Thin adapter preserving the current native Graphiti add_episode call shape."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .contracts import EpisodeInput


_LONGMEMEVAL_TIME = re.compile(
    r"(?P<date>\d{4}/\d{2}/\d{2}) \((?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\) "
    r"(?P<time>\d{2}:\d{2})"
)
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_reference_time(value: str) -> datetime:
    match = _LONGMEMEVAL_TIME.fullmatch(str(value).strip())
    if match is not None:
        parsed = datetime.strptime(
            f"{match['date']} {match['time']}", "%Y/%m/%d %H:%M"
        )
        if _WEEKDAYS[parsed.weekday()] != match["weekday"]:
            raise ValueError("LONGMEMEVAL_WEEKDAY_MISMATCH")
        return parsed.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return (
        parsed.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def build_graphiti_kwargs(episode: EpisodeInput, *, source: Any) -> dict[str, Any]:
    if not isinstance(episode, EpisodeInput):
        raise TypeError("EPISODE_INPUT_INVALID")
    return {
        "name": f"{episode.history_id}::episode::{episode.source_sequence:04d}",
        "episode_body": episode.body,
        "source_description": "LongMemEval-S haystack session",
        "reference_time": parse_reference_time(episode.reference_time),
        "source": source,
        "group_id": episode.namespace,
    }


class GraphitiNativeAdapter:
    def __init__(self, graphiti: Any, *, source: Any) -> None:
        add_episode = getattr(graphiti, "add_episode", None)
        if not callable(add_episode):
            raise TypeError("GRAPHITI_ADD_EPISODE_MISSING")
        self._graphiti = graphiti
        self._source = source

    async def add_episode(self, episode: EpisodeInput) -> Any:
        return await self._graphiti.add_episode(
            **build_graphiti_kwargs(episode, source=self._source)
        )


__all__ = [
    "GraphitiNativeAdapter",
    "build_graphiti_kwargs",
    "parse_reference_time",
]

