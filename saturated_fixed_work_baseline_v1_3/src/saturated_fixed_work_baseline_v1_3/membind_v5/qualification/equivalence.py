"""Small scripted native-path equivalence qualification."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..runtime.adapters.client_proxy import V5LLMClientProxy
from ..runtime.core.binder import NativeBindingScope
from ..runtime.core.transcript import TranscriptStore


@dataclass(frozen=True, slots=True)
class ScriptedEpisode:
    source_sequence: int
    body: str


class ScriptedOracleClient:
    def __init__(self) -> None:
        self.provider_calls = 0

    async def generate_response(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        self.provider_calls += 1
        prompt_name = kwargs.get("prompt_name")
        body = messages[0].get("content", "")
        digest = hashlib.sha256(f"{prompt_name}|{body}".encode()).hexdigest()[:10]
        if prompt_name == "extract_nodes.extract_message":
            return {"nodes": [{"name": f"entity-{digest}"}]}
        if prompt_name == "extract_edges.edge":
            return {"edges": [{"fact": f"edge-{digest}"}]}
        return {"attributes": []}


class ScriptedNativePath:
    """A minimal native-shaped continuation; no production Graphiti logic is copied."""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client
        self.graph: list[dict[str, Any]] = []

    async def add_episode(self, episode: ScriptedEpisode) -> dict[str, Any]:
        messages = [{"role": "user", "content": episode.body}]
        nodes = await self.llm_client.generate_response(messages, prompt_name="extract_nodes.extract_message")
        edges = await self.llm_client.generate_response(messages, prompt_name="extract_edges.edge")
        record = {"source_sequence": episode.source_sequence, "nodes": nodes["nodes"], "edges": edges["edges"]}
        self.graph.append(copy.deepcopy(record))
        return record


@dataclass(frozen=True, slots=True)
class EquivalenceResult:
    status: str
    canonical_equal: bool
    native_graph: tuple[dict[str, Any], ...]
    v5_graph: tuple[dict[str, Any], ...]
    provider_calls_native: int
    provider_calls_v5_capture: int
    provider_calls_v5_replay: int
    logical_captured: int
    logical_consumed: int
    local_capture_calls: int
    diff: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v5.scripted-equivalence.v1",
            "status": self.status,
            "canonical_equal": self.canonical_equal,
            "provider_calls": {
                "native": self.provider_calls_native,
                "v5_capture": self.provider_calls_v5_capture,
                "v5_replay": self.provider_calls_v5_replay,
            },
            "logical_work": {
                "captured": self.logical_captured,
                "consumed": self.logical_consumed,
                "local_capture_calls": self.local_capture_calls,
            },
            "diff": self.diff,
        }


def _diff(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    left_value = list(left)
    right_value = list(right)
    return {"exact": left_value == right_value, "left_count": len(left_value), "right_count": len(right_value), "first_difference": next(((index, l, r) for index, (l, r) in enumerate(zip(left_value, right_value)) if l != r), None)}


async def run_scripted_serial_equivalence_async(episodes: Iterable[ScriptedEpisode]) -> EquivalenceResult:
    selected = tuple(episodes)
    native_client = ScriptedOracleClient()
    native_path = ScriptedNativePath(native_client)
    for episode in selected:
        await native_path.add_episode(episode)

    store = TranscriptStore()
    capture_client = ScriptedOracleClient()
    capture_proxy = V5LLMClientProxy(capture_client, store, source_sequence=0, mode="capture", client_identity={"class": "Scripted", "source_hash": "scripted"})
    capture_path = ScriptedNativePath(capture_proxy)
    for episode in selected:
        capture_proxy.source_sequence = episode.source_sequence
        await capture_path.add_episode(episode)
    capture_graph = list(capture_path.graph)
    capture_path.graph.clear()

    replay_client = ScriptedOracleClient()
    replay_proxy = V5LLMClientProxy(replay_client, store, source_sequence=0, mode="replay", client_identity={"class": "Scripted", "source_hash": "scripted"})
    v5_path = ScriptedNativePath(replay_proxy)
    for episode in selected:
        replay_proxy.source_sequence = episode.source_sequence
        with NativeBindingScope(store, source_sequence=episode.source_sequence):
            await v5_path.add_episode(episode)

    diff = _diff(native_path.graph, v5_path.graph)
    summary = store.summary()
    status = "PASS" if diff["exact"] and summary["unconsumed"] == 0 else "FAIL"
    return EquivalenceResult(
        status=status,
        canonical_equal=bool(diff["exact"]),
        native_graph=tuple(native_path.graph),
        v5_graph=tuple(v5_path.graph),
        provider_calls_native=native_client.provider_calls,
        provider_calls_v5_capture=capture_client.provider_calls,
        provider_calls_v5_replay=replay_client.provider_calls,
        logical_captured=summary["logical_captured"],
        logical_consumed=summary["logical_consumed"],
        local_capture_calls=len(capture_graph) * 2,
        diff=diff,
    )


def run_scripted_serial_equivalence(episodes: Iterable[ScriptedEpisode]) -> EquivalenceResult:
    return asyncio.run(run_scripted_serial_equivalence_async(episodes))


__all__ = ["EquivalenceResult", "ScriptedEpisode", "run_scripted_serial_equivalence", "run_scripted_serial_equivalence_async"]

