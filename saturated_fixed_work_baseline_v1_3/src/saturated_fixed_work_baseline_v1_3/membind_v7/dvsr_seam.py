"""Minimal Frozen-V6 prepared/no-reuse seam for DVSR Phase 1.

The seam is intentionally provider- and Graphiti-free.  It gives the future
observer a stable identity boundary without implementing speculative reuse:
prepared extraction is materialized once, every stateful resolution is fresh
on the supplied authoritative state, and publication is source ordered.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping


SEAM_IDENTITY = "V6_PREPARED_NOREUSE_CONTROL"
SEAM_SCHEMA = "membind.dvsr.v6-prepared-noreuse.v1"


def canonical_digest(value: Any) -> str:
    """Hash a JSON-compatible logical value with stable ordering."""

    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedExtractionArtifact:
    source_sequence: int
    source_hash: str
    v6_identity: str
    payload: Mapping[str, Any]
    provider_transcript_digest: str
    previous_context_policy: str

    def __post_init__(self) -> None:
        if isinstance(self.source_sequence, bool) or self.source_sequence < 0:
            raise ValueError("source_sequence must be non-negative")
        if not self.source_hash or not self.v6_identity or not self.provider_transcript_digest:
            raise ValueError("prepared artifact identity is incomplete")
        if not self.previous_context_policy:
            raise ValueError("previous context policy is required")
        object.__setattr__(self, "payload", copy.deepcopy(dict(self.payload)))

    @property
    def artifact_digest(self) -> str:
        return canonical_digest(
            {
                "source_sequence": self.source_sequence,
                "source_hash": self.source_hash,
                "v6_identity": self.v6_identity,
                "payload": self.payload,
                "provider_transcript_digest": self.provider_transcript_digest,
                "previous_context_policy": self.previous_context_policy,
            }
        )

    def clone(self) -> "PreparedExtractionArtifact":
        """Return an isolated logical clone without rematerializing extraction."""

        return PreparedExtractionArtifact(
            source_sequence=self.source_sequence,
            source_hash=self.source_hash,
            v6_identity=self.v6_identity,
            payload=copy.deepcopy(dict(self.payload)),
            provider_transcript_digest=self.provider_transcript_digest,
            previous_context_policy=self.previous_context_policy,
        )


@dataclass(frozen=True, slots=True)
class NoReuseResolution:
    prepared_artifact_digest: str
    read_epoch: str
    authoritative_state_digest: str
    output: Any
    provider_calls: int
    database_writes: int

    def __post_init__(self) -> None:
        if not self.prepared_artifact_digest or not self.read_epoch or not self.authoritative_state_digest:
            raise ValueError("resolution identity is incomplete")
        if self.provider_calls < 0 or self.database_writes < 0:
            raise ValueError("work counts must be non-negative")


class FrozenV6PreparedNoReuseControl:
    """A differential control over a Frozen-V6 extraction/resolve pair."""

    def __init__(self, *, v6_identity: str, previous_context_policy: str) -> None:
        if not v6_identity or not previous_context_policy:
            raise ValueError("Frozen V6 seam identity is required")
        self.v6_identity = str(v6_identity)
        self.previous_context_policy = str(previous_context_policy)
        self._prepared: dict[int, PreparedExtractionArtifact] = {}
        self._materialize_count = 0
        self._resolve_count = 0

    @property
    def materialize_count(self) -> int:
        return self._materialize_count

    @property
    def resolve_count(self) -> int:
        return self._resolve_count

    def prepare(
        self,
        source_sequence: int,
        source: Any,
        extract: Callable[[Any], Mapping[str, Any]],
        *,
        source_hash: str | None = None,
        provider_transcript_digest: str | None = None,
    ) -> PreparedExtractionArtifact:
        """Materialize extraction once for a source and return an isolated clone."""

        if source_sequence in self._prepared:
            return self._prepared[source_sequence].clone()
        payload = extract(source)
        if not isinstance(payload, Mapping):
            raise TypeError("Frozen V6 extraction must return a mapping")
        artifact = PreparedExtractionArtifact(
            source_sequence=int(source_sequence),
            source_hash=source_hash or canonical_digest(source),
            v6_identity=self.v6_identity,
            payload=payload,
            provider_transcript_digest=provider_transcript_digest or canonical_digest(payload),
            previous_context_policy=self.previous_context_policy,
        )
        self._prepared[source_sequence] = artifact
        self._materialize_count += 1
        return artifact.clone()

    def resolve_fresh(
        self,
        artifact: PreparedExtractionArtifact,
        authoritative_state: Mapping[str, Any],
        resolve: Callable[[Mapping[str, Any], Mapping[str, Any]], Any],
        *,
        read_epoch: str,
    ) -> NoReuseResolution:
        """Resolve on the current state; this control never consumes reuse."""

        if artifact.v6_identity != self.v6_identity:
            raise ValueError("prepared artifact belongs to a different Frozen V6 identity")
        state_copy = copy.deepcopy(dict(authoritative_state))
        output = resolve(copy.deepcopy(dict(artifact.payload)), state_copy)
        self._resolve_count += 1
        return NoReuseResolution(
            prepared_artifact_digest=artifact.artifact_digest,
            read_epoch=str(read_epoch),
            authoritative_state_digest=canonical_digest(authoritative_state),
            output=copy.deepcopy(output),
            provider_calls=1,
            database_writes=0,
        )

    @staticmethod
    def ordered_publication(
        resolutions: Mapping[int, NoReuseResolution],
        publish: Callable[[int, Any], None],
    ) -> tuple[int, ...]:
        """Publish only in source order; preparation completion order is ignored."""

        order = tuple(sorted(resolutions))
        if order and order != tuple(range(order[-1] + 1)):
            raise ValueError("publication source sequences must be contiguous from zero")
        for sequence in order:
            publish(sequence, resolutions[sequence].output)
        return order


__all__ = [
    "FrozenV6PreparedNoReuseControl",
    "NoReuseResolution",
    "PreparedExtractionArtifact",
    "SEAM_IDENTITY",
    "SEAM_SCHEMA",
    "canonical_digest",
]
