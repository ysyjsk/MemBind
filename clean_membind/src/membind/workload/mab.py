"""Lossless role-aware 8,192-character workload adapter.

This is a deliberately small extraction of the frozen adapter contract.  The
limit is characters, not tokens; Graphiti receives one episode per transport
chunk and source/session order is retained.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence

MAB8192_ADAPTER_VERSION = "MAB_ROLE_AWARE_LOSSLESS_8192_V1"
MAB8192_CHUNK_SIZE = 8192


def canonical_episode_body(session: "MABSession") -> str:
    lines = []
    for turn in session.turns:
        lines.extend((f"[{turn['role'].upper()}]", turn["content"]))
    body = "\n".join(lines)
    if not body.strip():
        raise ValueError("session body is empty")
    return body


def split_lossless_body(body: str, *, chunk_size: int = MAB8192_CHUNK_SIZE) -> tuple[str, ...]:
    if not isinstance(body, str) or not body:
        raise ValueError("body must be non-empty text")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    chunks = tuple(body[offset : offset + chunk_size] for offset in range(0, len(body), chunk_size))
    if "".join(chunks) != body or any(len(chunk) > chunk_size for chunk in chunks):
        raise ValueError("chunking must be lossless and bounded")
    return chunks


@dataclass(frozen=True, slots=True)
class MABSession:
    session_id: str
    source_sequence: int
    timestamp: str
    turns: tuple[dict[str, str], ...]
    source_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.session_id or not self.timestamp or not self.turns:
            raise ValueError("session identity and turns are required")
        if isinstance(self.source_sequence, bool) or not isinstance(self.source_sequence, int) or self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        for turn in self.turns:
            if turn.get("role", "").lower() not in {"user", "assistant"} or not turn.get("content", "").strip():
                raise ValueError("turns must contain user/assistant text")
        expected = hashlib.sha256(canonical_episode_body(self).encode("utf-8")).hexdigest()
        if self.source_sha256 and self.source_sha256 != expected:
            raise ValueError("source_sha256 mismatch")
        object.__setattr__(self, "source_sha256", expected)


@dataclass(frozen=True, slots=True)
class MABContext:
    context_id: str
    sessions: tuple[MABSession, ...]

    def __post_init__(self) -> None:
        if not self.context_id or not self.sessions:
            raise ValueError("context requires sessions")
        sequences = [item.source_sequence for item in self.sessions]
        if sequences != sorted(sequences) or len(set(sequences)) != len(sequences):
            raise ValueError("sessions must be ordered with unique source_sequence")

    @property
    def context_sha256(self) -> str:
        payload = {
            "context_id": self.context_id,
            "sessions": [
                {
                    "session_id": session.session_id,
                    "source_sequence": session.source_sequence,
                    "timestamp": session.timestamp,
                    "turns": [dict(turn) for turn in session.turns],
                    "source_sha256": session.source_sha256,
                }
                for session in self.sessions
            ],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MAB8192Chunk:
    dataset_revision: str
    context_id: str
    session_id: str
    source_sequence: int
    chunk_ordinal: int
    chunk_count: int
    body: str
    reference_time: str
    previous_chunk_id: str | None = None

    def __post_init__(self) -> None:
        if not self.dataset_revision or not self.context_id or not self.session_id:
            raise ValueError("chunk identity is required")
        if self.source_sequence < 0 or self.chunk_ordinal < 0 or self.chunk_count < 1 or self.chunk_ordinal >= self.chunk_count:
            raise ValueError("chunk ordinal/count is invalid")
        if not self.body or len(self.body) > MAB8192_CHUNK_SIZE:
            raise ValueError("chunk body is empty or exceeds 8192 characters")
        if self.chunk_ordinal == 0 and self.previous_chunk_id is not None:
            raise ValueError("first chunk cannot have a predecessor")

    @property
    def chunk_sha256(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def chunk_id(self) -> str:
        return "chunk-" + hashlib.sha256(
            f"{self.dataset_revision}\0{self.context_id}\0{self.session_id}\0{self.source_sequence}\0{self.chunk_ordinal}\0{self.chunk_sha256}".encode()
        ).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class MAB8192Manifest:
    dataset_revision: str
    context_id: str
    chunks: tuple[MAB8192Chunk, ...]
    adapter_version: str = MAB8192_ADAPTER_VERSION

    @classmethod
    def from_context(cls, context: MABContext, *, dataset_revision: str = "UNSPECIFIED", chunk_size: int = MAB8192_CHUNK_SIZE) -> "MAB8192Manifest":
        chunks: list[MAB8192Chunk] = []
        for session in context.sessions:
            pieces = split_lossless_body(canonical_episode_body(session), chunk_size=chunk_size)
            for ordinal, body in enumerate(pieces):
                predecessor = chunks[-1].chunk_id if ordinal else None
                chunks.append(MAB8192Chunk(dataset_revision, context.context_id, session.session_id, session.source_sequence, ordinal, len(pieces), body, session.timestamp, predecessor))
        return cls(dataset_revision, context.context_id, tuple(chunks))

    def reconstruct_session(self, session_id: str) -> str:
        values = [item for item in self.chunks if item.session_id == session_id]
        if not values:
            raise KeyError(session_id)
        return "".join(item.body for item in sorted(values, key=lambda item: item.chunk_ordinal))
