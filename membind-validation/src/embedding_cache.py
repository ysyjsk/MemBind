"""In-memory exact-input embedding cache with concurrency-safe accounting."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from graphiti_core.embedder.client import EmbedderClient
from instrumentation import current_episode_key


def _cache_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CachingCountingEmbedder(EmbedderClient):
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.config = getattr(inner, "config", None)
        self.api_call_count = 0
        self.text_count = 0
        self.cache_hit_count = 0
        self.call_events: list[dict[str, Any]] = []
        self._cache: dict[str, list[float]] = {}
        self._inflight: dict[str, asyncio.Future[list[float]]] = {}
        self._lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def create(self, input_data: Any) -> list[float]:
        key = _cache_key(input_data)
        owner = False
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self.cache_hit_count += 1
                return list(cached)
            future = self._inflight.get(key)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future
                owner = True
            else:
                self.cache_hit_count += 1

        if owner:
            self.api_call_count += 1
            self.text_count += 1
            self.call_events.append(
                {"episode_key": current_episode_key(), "text_count": 1}
            )
            try:
                vector = list(await self.inner.create(input_data))
            except BaseException as exc:
                await self._fail([key], [future], exc)
                raise
            await self._succeed(key, future, vector)

        return list(await future)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []

        keys = [_cache_key(value) for value in input_data_list]
        futures: list[asyncio.Future[list[float]]] = []
        owner_keys: list[str] = []
        owner_values: list[str] = []
        async with self._lock:
            loop = asyncio.get_running_loop()
            for key, value in zip(keys, input_data_list, strict=True):
                cached = self._cache.get(key)
                if cached is not None:
                    future = loop.create_future()
                    future.set_result(cached)
                    self.cache_hit_count += 1
                else:
                    future = self._inflight.get(key)
                    if future is None:
                        future = loop.create_future()
                        self._inflight[key] = future
                        owner_keys.append(key)
                        owner_values.append(value)
                    else:
                        self.cache_hit_count += 1
                futures.append(future)

        if owner_values:
            self.api_call_count += 1
            self.text_count += len(owner_values)
            self.call_events.append(
                {
                    "episode_key": current_episode_key(),
                    "text_count": len(owner_values),
                }
            )
            owner_futures = [self._inflight[key] for key in owner_keys]
            try:
                vectors = await self.inner.create_batch(owner_values)
                if len(vectors) != len(owner_values):
                    raise RuntimeError("embedding batch returned the wrong number of vectors")
            except BaseException as exc:
                await self._fail(owner_keys, owner_futures, exc)
                raise
            for key, future, vector in zip(owner_keys, owner_futures, vectors, strict=True):
                await self._succeed(key, future, list(vector))

        return [list(vector) for vector in await asyncio.gather(*futures)]

    async def _succeed(
        self,
        key: str,
        future: asyncio.Future[list[float]],
        vector: list[float],
    ) -> None:
        async with self._lock:
            self._cache[key] = vector
            self._inflight.pop(key, None)
            if not future.done():
                future.set_result(vector)

    async def _fail(
        self,
        keys: list[str],
        futures: list[asyncio.Future[list[float]]],
        exc: BaseException,
    ) -> None:
        async with self._lock:
            for key, future in zip(keys, futures, strict=True):
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_exception(exc)
                    future.exception()


def embedding_metrics(embedder: Any) -> dict[str, int]:
    return {
        "embedding_call_count": int(getattr(embedder, "api_call_count", 0)),
        "embedding_text_count": int(getattr(embedder, "text_count", 0)),
        "embedding_cache_hits": int(getattr(embedder, "cache_hit_count", 0)),
    }
