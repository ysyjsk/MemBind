"""Parallel Native ceiling using the same Graphiti adapter and resources."""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

from .graphiti_adapter import GraphitiEpisode, GraphitiNative


class AsyncNative:
    def __init__(self, native: GraphitiNative, *, max_concurrency: int = 2) -> None:
        if isinstance(max_concurrency, bool) or not isinstance(max_concurrency, int) or max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.native = native
        self.max_concurrency = max_concurrency

    async def run(self, episodes: Sequence[GraphitiEpisode]) -> tuple[Any, ...]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def one(episode: GraphitiEpisode) -> Any:
            async with semaphore:
                return await self.native.add_episode(episode)

        return tuple(await asyncio.gather(*(one(item) for item in episodes)))
