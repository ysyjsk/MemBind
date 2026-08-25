"""Method-independent fixed-work contracts for the v1.3 campaign.

The workload object is deliberately smaller than the benchmark record.  It
contains only the public conversation projection and event-time metadata; QA
labels never participate in construction or in the canonical workload hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import MABContext, MABSession, canonical_sha256


class WorkloadContractError(ValueError):
    """The fixed-work or manifest identity contract is invalid."""


_SHA256_LENGTH = 64
_FORBIDDEN_KEYS = frozenset(
    {
        "answer",
        "answers",
        "reference_answer",
        "reference_answers",
        "has_answer",
        "gold_session_ids",
        "gold_sessions",
        "question_type",
        "qa_pair_id",
    }
)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkloadContractError(f"{field} must be a non-empty string")
    return value


def _digest(value: Any, field: str) -> str:
    result = _text(value, field).lower()
    if len(result) != _SHA256_LENGTH or any(c not in "0123456789abcdef" for c in result):
        raise WorkloadContractError(f"{field} must be a SHA-256 digest")
    return result


def _assert_no_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise WorkloadContractError(f"GOLD_LEAK_DETECTED:{path}.{key}")
            _assert_no_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_forbidden_keys(child, f"{path}[{index}]")


def canonical_episode_body(session: MABSession) -> str:
    """Serialize exactly one public session for the construction method."""

    if not isinstance(session, MABSession):
        raise TypeError("session must be an MABSession")
    lines: list[str] = []
    for turn in session.turns:
        lines.append(f"[{turn['role'].upper()}]")
        lines.append(turn["content"])
    body = "\n".join(lines)
    if not body.strip():
        raise WorkloadContractError("session body is empty")
    return body


def stable_episode_id(*, dataset_revision: str, context_id: str, source_sequence: int) -> str:
    """Derive a method/namespace-independent episode identity."""

    if isinstance(source_sequence, bool) or not isinstance(source_sequence, int) or source_sequence < 0:
        raise WorkloadContractError("source_sequence is invalid")
    payload = f"{dataset_revision}\0{context_id}\0{source_sequence}".encode("utf-8")
    return "episode-" + hashlib.sha256(payload).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class EpisodeInput:
    context_id: str
    source_sequence: int
    episode_id: str
    reference_time: str
    body: str
    arrival_offset_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _text(self.context_id, "context_id"))
        if isinstance(self.source_sequence, bool) or not isinstance(self.source_sequence, int) or self.source_sequence < 0:
            raise WorkloadContractError("source_sequence is invalid")
        object.__setattr__(self, "episode_id", _text(self.episode_id, "episode_id"))
        object.__setattr__(self, "reference_time", _text(self.reference_time, "reference_time"))
        object.__setattr__(self, "body", _text(self.body, "body"))
        if isinstance(self.arrival_offset_s, bool) or not isinstance(self.arrival_offset_s, (int, float)):
            raise WorkloadContractError("arrival_offset_s is invalid")
        if float(self.arrival_offset_s) != 0.0:
            raise WorkloadContractError("v1.3 saturated workload requires zero arrival offsets")
        # This catches a caller passing a private mapping instead of the frozen
        # string renderer while allowing natural-language occurrences of words
        # such as "question" in a conversation.
        _assert_no_forbidden_keys(self._public_payload())

    def _public_payload(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "source_sequence": self.source_sequence,
            "episode_id": self.episode_id,
            "reference_time": self.reference_time,
            "body": self.body,
            "arrival_offset_s": float(self.arrival_offset_s),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._public_payload()


@dataclass(frozen=True, slots=True)
class WorkloadManifest:
    dataset_revision: str
    dataset_file_sha256: str
    context_id: str
    episodes: tuple[EpisodeInput, ...]
    scope: str = "FORMAL"
    expected_episode_count: int | None = None
    manifest_sha256: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_revision", _text(self.dataset_revision, "dataset_revision"))
        object.__setattr__(self, "dataset_file_sha256", _digest(self.dataset_file_sha256, "dataset_file_sha256"))
        object.__setattr__(self, "context_id", _text(self.context_id, "context_id"))
        episodes = tuple(self.episodes)
        if not episodes or any(not isinstance(item, EpisodeInput) for item in episodes):
            raise WorkloadContractError("episodes must contain EpisodeInput values")
        if any(item.context_id != self.context_id for item in episodes):
            raise WorkloadContractError("episode context identity mismatch")
        sequences = [item.source_sequence for item in episodes]
        if sequences != list(range(len(episodes))):
            raise WorkloadContractError("source_sequence must be contiguous and ordered")
        if len({item.episode_id for item in episodes}) != len(episodes):
            raise WorkloadContractError("episode IDs must be unique")
        if self.scope not in {"FORMAL", "ENGINEERING_DIAGNOSTIC"}:
            raise WorkloadContractError("scope is invalid")
        expected = self.expected_episode_count
        if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0):
            raise WorkloadContractError("expected_episode_count is invalid")
        if self.scope == "FORMAL" and expected is not None and expected != len(episodes):
            raise WorkloadContractError("formal manifest is a prefix workload")
        computed = canonical_sha256(self._identity_payload())
        if self.manifest_sha256 and self.manifest_sha256 != computed:
            raise WorkloadContractError("manifest hash mismatch")
        object.__setattr__(self, "manifest_sha256", computed)
        object.__setattr__(self, "episodes", episodes)

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v1.3.workload-manifest.v1",
            "dataset_revision": self.dataset_revision,
            "dataset_file_sha256": self.dataset_file_sha256,
            "context_id": self.context_id,
            "episode_count": len(self.episodes),
            "episodes": [
                {
                    "source_sequence": item.source_sequence,
                    "episode_id": item.episode_id,
                    "reference_time": item.reference_time,
                    "body": item.body,
                    "arrival_offset_s": float(item.arrival_offset_s),
                }
                for item in self.episodes
            ],
        }

    @classmethod
    def from_episodes(
        cls,
        *,
        context_id: str,
        episodes: Sequence[EpisodeInput],
        dataset_revision: str,
        dataset_file_sha256: str,
        scope: str = "FORMAL",
        expected_episode_count: int | None = None,
    ) -> "WorkloadManifest":
        return cls(
            dataset_revision=dataset_revision,
            dataset_file_sha256=dataset_file_sha256,
            context_id=context_id,
            episodes=tuple(episodes),
            scope=scope,
            expected_episode_count=expected_episode_count,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._identity_payload(),
            "scope": self.scope,
            "expected_episode_count": self.expected_episode_count,
            "manifest_sha256": self.manifest_sha256,
        }

    def jsonl(self) -> str:
        return "".join(
            json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for item in self.episodes
        )

    def require_formal(self, *, expected_episode_count: int | None = None) -> None:
        expected = expected_episode_count if expected_episode_count is not None else self.expected_episode_count
        if self.scope != "FORMAL":
            raise WorkloadContractError("prefix/diagnostic manifest cannot enter formal reducer")
        if expected is not None and len(self.episodes) != expected:
            raise WorkloadContractError("formal manifest is a prefix workload")


__all__ = [
    "EpisodeInput",
    "WorkloadContractError",
    "WorkloadManifest",
    "canonical_episode_body",
    "stable_episode_id",
]
