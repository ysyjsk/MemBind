"""Evidence-fenced Semantic Compile primitives."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

from dataset import Episode


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b")
BLOCKED_UUID_KEYS = {"uuid", "entity_uuid", "edge_uuid", "source_node_uuid", "target_node_uuid"}


class EvidenceFenceError(RuntimeError):
    pass


class UUIDLeakError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledArtifact:
    question_id: str
    group_id: str
    session_id: str
    source_sequence: int
    source_hash: str
    reference_time: str
    episode_body: str
    candidate_entities: list[dict[str, Any]]
    candidate_relations: list[dict[str, Any]]
    candidate_temporal_fields: dict[str, Any]
    source_episode_mapping: dict[str, Any]
    prompt_hash: str
    response_hash: str
    raw_payload: dict[str, Any]

    @classmethod
    def from_episode(
        cls,
        episode: Episode,
        payload: dict[str, Any],
        prompt_hash: str,
        response_hash: str,
    ) -> "CompiledArtifact":
        assert_unbound_payload(payload)
        return cls(
            question_id=episode.question_id,
            group_id=episode.group_id,
            session_id=episode.session_id,
            source_sequence=episode.source_sequence,
            source_hash=episode.source_hash,
            reference_time=episode.reference_time,
            episode_body=episode.body,
            candidate_entities=list(payload.get("candidate_entities") or payload.get("entities") or []),
            candidate_relations=list(payload.get("candidate_relations") or payload.get("relations") or payload.get("facts") or []),
            candidate_temporal_fields=dict(payload.get("candidate_temporal_fields") or payload.get("temporal_fields") or {}),
            source_episode_mapping=dict(
                payload.get("source_episode_mapping")
                or {"source_sequence": episode.source_sequence, "source_hash": episode.source_hash}
            ),
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            raw_payload=payload,
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceFence:
    """Append-only source log visible to Semantic Compile."""

    def __init__(self) -> None:
        self._episodes: dict[int, Episode] = {}

    def append(self, episode: Episode) -> None:
        if episode.source_sequence in self._episodes:
            raise EvidenceFenceError(f"duplicate source_sequence {episode.source_sequence}")
        self._episodes[episode.source_sequence] = episode

    def get(self, source_sequence: int) -> Episode:
        return self._episodes[source_sequence]

    def prefix_before(self, source_sequence: int) -> list[Episode]:
        return [self._episodes[i] for i in sorted(self._episodes) if i < source_sequence]

    def all(self) -> list[Episode]:
        return [self._episodes[i] for i in sorted(self._episodes)]


LLMCallable = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class SemanticCompiler:
    def __init__(
        self,
        system_prompt: str,
        schema: dict[str, Any],
        llm: LLMCallable | None = None,
        model_revision: str = "Qwen/Qwen3-32B-FP8@6e2312b85c2ae9a31f629f24493b79d8b02eab1a",
        decoding_config: dict[str, Any] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.schema = schema
        self.llm = llm
        self.model_revision = model_revision
        self.decoding_config = decoding_config or {
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": 2048,
            "seed": 20260806,
        }

    def build_user_prompt(self, fence: EvidenceFence, episode: Episode) -> str:
        previous = [
            {
                "source_sequence": ep.source_sequence,
                "reference_time": ep.reference_time,
                "body": ep.body,
            }
            for ep in fence.prefix_before(episode.source_sequence)
        ]
        current = {
            "source_sequence": episode.source_sequence,
            "reference_time": episode.reference_time,
            "body": episode.body,
        }
        return json.dumps(
            {
                "task": "semantic_compile_unbound_graph_delta",
                "evidence_prefix": previous,
                "current_episode": current,
                "constraints": [
                    "Use only the evidence_prefix and current_episode.",
                    "Do not use graph state, canonical entity UUIDs, or edge UUIDs.",
                    "Return logical candidate entities, relations/facts, temporal fields, and source mapping.",
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )

    def prompt_hash(self, user_prompt: str) -> str:
        payload = {
            "model_revision": self.model_revision,
            "decoding_config": self.decoding_config,
            "schema": self.schema,
            "system_prompt": self.system_prompt,
            "user_prompt": user_prompt,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

    async def compile_episode(self, fence: EvidenceFence, episode: Episode) -> CompiledArtifact:
        user_prompt = self.build_user_prompt(fence, episode)
        prompt_hash = self.prompt_hash(user_prompt)
        if self.llm is None:
            payload: dict[str, Any] = {
                "candidate_entities": [],
                "candidate_relations": [],
                "candidate_temporal_fields": {},
                "source_episode_mapping": {
                    "source_sequence": episode.source_sequence,
                    "source_hash": episode.source_hash,
                },
            }
        else:
            result = self.llm(self.system_prompt, user_prompt, self.schema)
            payload = await result if inspect.isawaitable(result) else result
        assert_unbound_payload(payload)
        response_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        return CompiledArtifact.from_episode(episode, payload, prompt_hash, response_hash)


def assert_unbound_payload(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in BLOCKED_UUID_KEYS or lowered.endswith("_uuid"):
                raise UUIDLeakError(f"physical UUID field leaked at {path}.{key}")
            assert_unbound_payload(child, f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            assert_unbound_payload(child, f"{path}[{idx}]")
    elif isinstance(value, str) and UUID_RE.search(value):
        raise UUIDLeakError(f"physical UUID value leaked at {path}")


async def compile_all_parallel(
    compiler: SemanticCompiler,
    fence: EvidenceFence,
    episodes: list[Episode],
    max_concurrency: int = 8,
) -> list[CompiledArtifact]:
    sem = asyncio.Semaphore(max_concurrency)

    async def one(ep: Episode) -> CompiledArtifact:
        async with sem:
            return await compiler.compile_episode(fence, ep)

    return await asyncio.gather(*(one(ep) for ep in episodes))
