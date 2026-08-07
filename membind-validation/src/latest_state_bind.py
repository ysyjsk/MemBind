"""Source-ordered latest-state binding and commit helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from semantic_compile import CompiledArtifact


class DuplicatePublishError(RuntimeError):
    pass


class SourceOrderViolationError(RuntimeError):
    pass


BindAndCommit = Callable[[CompiledArtifact], Awaitable[Any]]


class SourceOrderedCommitter:
    def __init__(self, total_episodes: int, bind_and_commit: BindAndCommit):
        self.total_episodes = total_episodes
        self.bind_and_commit = bind_and_commit
        self.next_sequence = 0
        self.pending: dict[int, CompiledArtifact] = {}
        self.published: set[int] = set()
        self._lock = asyncio.Lock()

    async def submit(self, artifact: CompiledArtifact) -> None:
        async with self._lock:
            seq = artifact.source_sequence
            if seq in self.published or seq in self.pending:
                raise DuplicatePublishError(f"source_sequence {seq} submitted more than once")
            if seq < self.next_sequence:
                raise SourceOrderViolationError(f"source_sequence {seq} is behind next_sequence {self.next_sequence}")
            self.pending[seq] = artifact
            await self._drain_locked()

    async def _drain_locked(self) -> None:
        while self.next_sequence in self.pending:
            artifact = self.pending.pop(self.next_sequence)
            await self.bind_and_commit(artifact)
            self.published.add(artifact.source_sequence)
            self.next_sequence += 1

    @property
    def is_complete(self) -> bool:
        return len(self.published) == self.total_episodes

    @property
    def published_sequences(self) -> list[int]:
        return sorted(self.published)


class LatestStateBinder:
    """Bind compiled artifacts against the runtime's latest committed state."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def bind_and_commit(self, artifact: CompiledArtifact) -> Any:
        if hasattr(self.runtime, "bind_compiled_artifact"):
            return await self.runtime.bind_compiled_artifact(artifact)
        if hasattr(self.runtime, "add_episode_from_artifact"):
            return await self.runtime.add_episode_from_artifact(artifact)
        raise TypeError("runtime must implement bind_compiled_artifact or add_episode_from_artifact")
