"""Lossless role-aware 8,192-character chunks for MemoryAgentBench sessions.

The adapter changes only the transport unit presented to Graphiti.  Chunk
payloads are contiguous slices of the canonical role-marked session body, so
concatenating them is byte-for-byte identical to the source session.  Chunk
identity and provenance live in the manifest fields rather than in the
payload, avoiding any mutation of benchmark text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import MABContext, MABSession, canonical_sha256
from .workload_contract import canonical_episode_body


MAB8192_ADAPTER_VERSION = "MAB_ROLE_AWARE_LOSSLESS_8192_V1"
MAB8192_CHUNK_SIZE = 8192
_TURN_MARKER = re.compile(r"^\[(?:USER|ASSISTANT)\]\n", re.MULTILINE)


def adapter_identity() -> dict[str, Any]:
    """Return the immutable adapter contract and its deterministic hash."""

    payload = {
        "adapter_version": MAB8192_ADAPTER_VERSION,
        "chunk_size_characters": MAB8192_CHUNK_SIZE,
        "body_encoding": "utf-8",
        "split_policy": "turn_boundary_then_whitespace_or_codepoint",
        "lossless": True,
        "session_order": "source_sequence_ascending",
        "session_dependency": "strict_chunk_ordinal_chain",
        "identity_location": "manifest_metadata_only",
    }
    return {**payload, "adapter_sha256": canonical_sha256(payload)}


class MAB8192AdapterError(ValueError):
    """Raised when a lossless chunk manifest cannot be constructed."""


def _safe_boundary(text: str, start: int, limit: int) -> int:
    """Choose a whitespace boundary without ever splitting a Unicode code point."""

    if limit >= len(text):
        return len(text)
    boundary = max(
        text.rfind("\n", start, limit + 1),
        text.rfind(" ", start, limit + 1),
        text.rfind("\t", start, limit + 1),
    )
    if boundary > start:
        return boundary + 1
    return limit


def split_lossless_body(body: str, *, chunk_size: int = MAB8192_CHUNK_SIZE) -> tuple[str, ...]:
    """Split a canonical role-marked body into contiguous bounded chunks."""

    if not isinstance(body, str) or not body:
        raise MAB8192AdapterError("session body must be non-empty text")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise MAB8192AdapterError("chunk_size must be a positive integer")
    chunks: list[str] = []
    offset = 0
    markers = [match.start() for match in _TURN_MARKER.finditer(body)]
    marker_set = set(markers)
    while offset < len(body):
        limit = min(len(body), offset + chunk_size)
        # Prefer a boundary immediately before the next role marker.  The
        # marker itself remains wholly in the following chunk.
        turn_boundary = max((position for position in markers if offset < position <= limit), default=-1)
        if turn_boundary > offset:
            end = turn_boundary
        else:
            end = _safe_boundary(body, offset, limit)
        if end <= offset:
            end = limit
        chunk = body[offset:end]
        if not chunk:
            raise MAB8192AdapterError("chunking produced an empty chunk")
        chunks.append(chunk)
        offset = end
    if any(len(chunk) > chunk_size for chunk in chunks):
        raise MAB8192AdapterError("chunk exceeds the fixed 8192-character bound")
    if "".join(chunks) != body:
        raise MAB8192AdapterError("chunk concatenation is not lossless")
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class MAB8192Chunk:
    """One immutable transport chunk and its original-session provenance."""

    dataset_revision: str
    context_id: str
    session_id: str
    source_sequence: int
    session_episode_id: str
    chunk_ordinal: int
    chunk_count: int
    body: str
    reference_time: str
    chunk_sha256: str
    global_sequence: int = 0
    previous_chunk_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_sequence, int) or isinstance(self.source_sequence, bool) or self.source_sequence < 0:
            raise MAB8192AdapterError("source_sequence is invalid")
        if not isinstance(self.chunk_ordinal, int) or isinstance(self.chunk_ordinal, bool) or self.chunk_ordinal < 0:
            raise MAB8192AdapterError("chunk_ordinal is invalid")
        if not isinstance(self.chunk_count, int) or isinstance(self.chunk_count, bool) or self.chunk_count < 1:
            raise MAB8192AdapterError("chunk_count is invalid")
        if self.chunk_ordinal >= self.chunk_count:
            raise MAB8192AdapterError("chunk_ordinal is outside chunk_count")
        if not isinstance(self.global_sequence, int) or isinstance(self.global_sequence, bool) or self.global_sequence < 0:
            raise MAB8192AdapterError("global_sequence is invalid")
        if self.chunk_ordinal == 0 and self.previous_chunk_id is not None:
            raise MAB8192AdapterError("first session chunk cannot have a predecessor")
        if self.chunk_ordinal > 0 and (
            not isinstance(self.previous_chunk_id, str) or not self.previous_chunk_id
        ):
            raise MAB8192AdapterError("non-first session chunk requires a predecessor")
        if not isinstance(self.body, str) or not self.body:
            raise MAB8192AdapterError("chunk body is empty")
        digest = hashlib.sha256(self.body.encode("utf-8")).hexdigest()
        if self.chunk_sha256 != digest:
            raise MAB8192AdapterError("chunk_sha256 does not match body")

    @property
    def chunk_id(self) -> str:
        payload = "\0".join(
            (
                self.dataset_revision,
                self.context_id,
                self.session_id,
                str(self.source_sequence),
                str(self.chunk_ordinal),
                self.chunk_sha256,
            )
        )
        return "chunk-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def to_dict(self, *, global_sequence: int | None = None) -> dict[str, Any]:
        sequence = self.global_sequence if global_sequence is None else global_sequence
        return {
            "adapter_version": MAB8192_ADAPTER_VERSION,
            "dataset_revision": self.dataset_revision,
            "context_id": self.context_id,
            "session_id": self.session_id,
            "source_sequence": self.source_sequence,
            "global_sequence": sequence,
            "session_episode_id": self.session_episode_id,
            "chunk_id": self.chunk_id,
            "chunk_ordinal": self.chunk_ordinal,
            "chunk_count": self.chunk_count,
            "chunk_sha256": self.chunk_sha256,
            "previous_chunk_id": self.previous_chunk_id,
            "reference_time": self.reference_time,
            "body": self.body,
        }


@dataclass(frozen=True, slots=True)
class MAB8192Manifest:
    """Immutable ordered chunk manifest consumed identically by A, B and C."""

    dataset_revision: str
    context_id: str
    chunks: tuple[MAB8192Chunk, ...]
    canonical_session_sha256: tuple[tuple[str, str], ...]
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        chunks = tuple(self.chunks)
        if not chunks or any(not isinstance(chunk, MAB8192Chunk) for chunk in chunks):
            raise MAB8192AdapterError("manifest must contain chunks")
        if any(chunk.context_id != self.context_id for chunk in chunks):
            raise MAB8192AdapterError("chunk context identity mismatch")
        expected_sequence = list(range(len(chunks)))
        if [chunk.global_sequence for chunk in chunks] != expected_sequence:
            raise MAB8192AdapterError("chunk global sequence is not contiguous")
        for index, chunk in enumerate(chunks):
            if chunk.chunk_ordinal == 0:
                if chunk.previous_chunk_id is not None:
                    raise MAB8192AdapterError("session chain starts with a predecessor")
            else:
                predecessor = chunks[index - 1] if index > 0 else None
                if predecessor is None or predecessor.session_id != chunk.session_id:
                    raise MAB8192AdapterError("session chunks are interleaved")
                if chunk.previous_chunk_id != predecessor.chunk_id:
                    raise MAB8192AdapterError("session dependency chain is not adjacent")
        session_ids = {chunk.session_id for chunk in chunks}
        supplied = dict(self.canonical_session_sha256)
        if session_ids != set(supplied):
            raise MAB8192AdapterError("canonical session inventory does not match chunks")
        computed = canonical_sha256(self._identity_payload())
        if self.manifest_sha256 and self.manifest_sha256 != computed:
            raise MAB8192AdapterError("manifest hash mismatch")
        object.__setattr__(self, "chunks", chunks)
        object.__setattr__(self, "manifest_sha256", computed)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.mab8192.manifest.v1",
            "adapter_version": MAB8192_ADAPTER_VERSION,
            "adapter_identity": adapter_identity(),
            "dataset_revision": self.dataset_revision,
            "context_id": self.context_id,
            "canonical_session_sha256": [list(item) for item in self.canonical_session_sha256],
            "chunks": [chunk.to_dict() for chunk in self.chunks],
        }

    @classmethod
    def from_context(
        cls,
        context: MABContext,
        *,
        dataset_revision: str,
        chunk_size: int = MAB8192_CHUNK_SIZE,
    ) -> "MAB8192Manifest":
        if not isinstance(context, MABContext):
            raise TypeError("context must be an MABContext")
        chunks: list[MAB8192Chunk] = []
        session_digests: list[tuple[str, str]] = []
        for session in context.sessions:
            body = canonical_episode_body(session)
            pieces = split_lossless_body(body, chunk_size=chunk_size)
            session_digests.append((session.session_id, hashlib.sha256(body.encode("utf-8")).hexdigest()))
            episode_id = "episode-" + hashlib.sha256(
                f"{dataset_revision}\0{context.context_id}\0{session.source_sequence}".encode("utf-8")
            ).hexdigest()[:32]
            for ordinal, piece in enumerate(pieces):
                global_sequence = len(chunks)
                predecessor = chunks[-1].chunk_id if ordinal > 0 else None
                chunks.append(
                    MAB8192Chunk(
                        dataset_revision=dataset_revision,
                        context_id=context.context_id,
                        session_id=session.session_id,
                        source_sequence=session.source_sequence,
                        session_episode_id=episode_id,
                        chunk_ordinal=ordinal,
                        chunk_count=len(pieces),
                        body=piece,
                        reference_time=session.timestamp,
                        chunk_sha256=hashlib.sha256(piece.encode("utf-8")).hexdigest(),
                        global_sequence=global_sequence,
                        previous_chunk_id=predecessor,
                    )
                )
        # Sessions are already ordered by MABContext.  Preserve each session's
        # chunk chain and never interleave chunks from different sessions.
        return cls(
            dataset_revision=dataset_revision,
            context_id=context.context_id,
            chunks=tuple(chunks),
            canonical_session_sha256=tuple(session_digests),
        )

    def session_chunks(self, session_id: str) -> tuple[MAB8192Chunk, ...]:
        values = tuple(chunk for chunk in self.chunks if chunk.session_id == session_id)
        return tuple(sorted(values, key=lambda chunk: chunk.chunk_ordinal))

    def reconstruct_session(self, session_id: str) -> str:
        chunks = self.session_chunks(session_id)
        if not chunks:
            raise MAB8192AdapterError(f"unknown session: {session_id}")
        if [chunk.chunk_ordinal for chunk in chunks] != list(range(len(chunks))):
            raise MAB8192AdapterError("session chunk dependency is not contiguous")
        return "".join(chunk.body for chunk in chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity_payload(),
            "chunk_count": len(self.chunks),
            "session_count": len(self.canonical_session_sha256),
            "manifest_sha256": self.manifest_sha256,
        }

    def jsonl(self) -> str:
        return "".join(
            json.dumps(chunk.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for chunk in self.chunks
        )


__all__ = [
    "MAB8192_ADAPTER_VERSION",
    "MAB8192_CHUNK_SIZE",
    "MAB8192AdapterError",
    "MAB8192Chunk",
    "MAB8192Manifest",
    "adapter_identity",
    "split_lossless_body",
]
