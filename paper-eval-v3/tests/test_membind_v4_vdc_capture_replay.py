"""TDD contracts for the MemBind-VDC captured Bind replay harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.graphiti_factorization import CapturedGraphitiRequest
from paper_eval.membind_v4.vdc.capture import (
    CapturedBindReplay,
    VDCReplayCaptureError,
)
from paper_eval.membind_v4.vdc.replay import (
    VDCDeterministicReplayError,
    replay_captured_node_resolve,
)


@dataclass
class _Node:
    uuid: str
    name: str
    labels: list[str] = field(default_factory=lambda: ["Entity"])
    summary: str = ""
    attributes: dict[str, object] = field(default_factory=dict)
    group_id: str = "vdc-test"

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        assert mode in {"python", "json"}
        return {
            "attributes": dict(self.attributes),
            "group_id": self.group_id,
            "labels": list(self.labels),
            "name": self.name,
            "summary": self.summary,
            "uuid": self.uuid,
        }


@dataclass
class _Episode:
    uuid: str
    content: str
    group_id: str = "vdc-test"
    source: str = "message"
    valid_at: datetime = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        assert mode in {"python", "json"}
        return {
            "content": self.content,
            "group_id": self.group_id,
            "source": self.source,
            "uuid": self.uuid,
            "valid_at": self.valid_at.isoformat(),
        }


class _ResponseModel:
    @classmethod
    def model_json_schema(cls) -> dict[str, object]:
        return {
            "properties": {"entity_resolutions": {"type": "array"}},
            "required": ["entity_resolutions"],
            "type": "object",
        }


@dataclass
class _State:
    resolved_nodes: list[object | None]
    uuid_map: dict[str, str]
    unresolved_indices: list[int]
    duplicate_pairs: list[tuple[object, object]] = field(default_factory=list)


class _NodeOperations:
    DedupResolutionState = _State

    def __init__(self) -> None:
        self.generated_requests = 0

    @staticmethod
    def _merge_candidate_nodes(candidate_nodes, existing_nodes_override):
        merged = list(candidate_nodes) + list(existing_nodes_override or ())
        return list({node.uuid: node for node in merged}.values())

    @staticmethod
    def _build_candidate_indexes(candidates):
        return SimpleNamespace(existing_nodes=list(candidates))

    @staticmethod
    def _resolve_with_similarity(extracted_nodes, indexes, state):
        assert len(extracted_nodes) == len(state.resolved_nodes) == 1
        assert indexes.existing_nodes

    async def _resolve_with_llm(
        self,
        llm_client,
        extracted_nodes,
        indexes,
        state,
        episode,
        previous_episodes,
        entity_types,
    ):
        self.generated_requests += 1
        request = [
            {"role": "system", "content": "dedupe_nodes.nodes"},
            {
                "role": "user",
                "content": {
                    "candidates": [node.uuid for node in indexes.existing_nodes],
                    "entity_types": list((entity_types or {}).keys()),
                    "episode": episode.content,
                    "extracted": [node.name for node in extracted_nodes],
                    "previous": [item.content for item in previous_episodes],
                },
            },
        ]
        response = await llm_client.generate_response(
            request,
            response_model=_ResponseModel,
            prompt_name="dedupe_nodes.nodes",
        )
        for resolution in response["entity_resolutions"]:
            relative_id = resolution["id"]
            original_index = state.unresolved_indices[relative_id]
            extracted = extracted_nodes[original_index]
            candidate_id = resolution["duplicate_candidate_id"]
            resolved = extracted if candidate_id < 0 else indexes.existing_nodes[candidate_id]
            state.resolved_nodes[original_index] = resolved
            state.uuid_map[extracted.uuid] = resolved.uuid
            if extracted.uuid != resolved.uuid:
                state.duplicate_pairs.append((extracted, resolved))


def _prepared() -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=1,
        source_sha256="1" * 64,
        evidence_sha256="2" * 64,
        certification_sha256="3" * 64,
        raw_nodes=(
            {
                "attributes": {},
                "group_id": "vdc-test",
                "labels": ["Entity"],
                "name": "Alice",
                "summary": "",
                "uuid": "raw-alice",
            },
        ),
        raw_edges=(),
        pure_intermediates={"node_episode_index_map": {"raw-alice": [0]}},
    )


def _runtime_values(duplicate_candidate_id: int = 0):
    extracted = _Node(uuid="raw-alice", name="Alice")
    candidate = _Node(uuid="existing-alice", name="Alice A.")
    episode = _Episode(uuid="episode-1", content="Alice joined Example Corp.")
    previous = [_Episode(uuid="episode-0", content="Alice studied systems.")]
    request = CapturedGraphitiRequest(
        args=(
            [
                {"role": "system", "content": "dedupe_nodes.nodes"},
                {
                    "role": "user",
                    "content": {
                        "candidates": ["existing-alice"],
                        "entity_types": [],
                        "episode": episode.content,
                        "extracted": ["Alice"],
                        "previous": [previous[0].content],
                    },
                },
            ],
        ),
        kwargs={
            "response_model": _ResponseModel,
            "prompt_name": "dedupe_nodes.nodes",
        },
    )
    response = {
        "entity_resolutions": [
            {"id": 0, "duplicate_candidate_id": duplicate_candidate_id}
        ]
    }
    resolved = extracted if duplicate_candidate_id < 0 else candidate
    interpreted = (
        [resolved],
        {"raw-alice": resolved.uuid},
        [] if duplicate_candidate_id < 0 else [(extracted, candidate)],
    )
    return extracted, candidate, episode, previous, request, response, interpreted


def _capture(*, duplicate_candidate_id: int = 0, interpreted_override=None):
    extracted, candidate, episode, previous, request, response, interpreted = _runtime_values(
        duplicate_candidate_id
    )
    return CapturedBindReplay.create(
        prepared_artifact=_prepared(),
        state_version=1,
        group_id="vdc-test",
        episode=episode,
        previous_episodes=previous,
        extracted_nodes=[extracted],
        candidate_nodes_by_extracted=[[candidate]],
        captured_request=request,
        llm_response=response,
        interpreted=interpreted if interpreted_override is None else interpreted_override,
        node_resolve_service_ns=50_000_000,
    )


@pytest.mark.asyncio
async def test_same_capture_replays_twice_without_database_or_provider() -> None:
    capture = _capture()
    module = _NodeOperations()

    first = await replay_captured_node_resolve(
        capture,
        node_factory=lambda value: _Node(**value),
        episode_factory=lambda value: _Episode(
            **{
                **value,
                "valid_at": datetime.fromisoformat(str(value["valid_at"])),
            }
        ),
        node_operations=module,
    )
    second = await replay_captured_node_resolve(
        CapturedBindReplay.from_document(capture.to_document()),
        node_factory=lambda value: _Node(**value),
        episode_factory=lambda value: _Episode(
            **{
                **value,
                "valid_at": datetime.fromisoformat(str(value["valid_at"])),
            }
        ),
        node_operations=module,
    )

    assert first == second
    assert first.request_identity_match is True
    assert first.effect_identity_match is True
    assert first.external_database_read_count == 0
    assert first.external_provider_call_count == 0
    assert module.generated_requests == 2


def test_capture_round_trip_rejects_candidate_tampering() -> None:
    document = _capture().to_document()
    document["candidate_nodes_by_extracted"][0][0]["uuid"] = "tampered"

    with pytest.raises(VDCReplayCaptureError, match="capture_hash_mismatch"):
        CapturedBindReplay.from_document(document)


@pytest.mark.asyncio
async def test_replay_fails_closed_when_captured_response_and_effect_disagree() -> None:
    _, candidate, _, _, _, _, _ = _runtime_values()
    extracted = _Node(uuid="raw-alice", name="Alice")
    inconsistent_effect = (
        [candidate],
        {"raw-alice": "existing-alice"},
        [(extracted, candidate)],
    )
    capture = _capture(
        duplicate_candidate_id=-1,
        interpreted_override=inconsistent_effect,
    )

    with pytest.raises(VDCDeterministicReplayError, match="effect_identity_mismatch"):
        await replay_captured_node_resolve(
            capture,
            node_factory=lambda value: _Node(**value),
            episode_factory=lambda value: _Episode(
                **{
                    **value,
                    "valid_at": datetime.fromisoformat(str(value["valid_at"])),
                }
            ),
            node_operations=_NodeOperations(),
        )

