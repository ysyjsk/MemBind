"""TDD contracts for exact capture plus read-only stale Probe observation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.graphiti_factorization import CapturedGraphitiRequest
from paper_eval.membind_v4.node_resolve_adapter import PreparedSemanticCall
from paper_eval.membind_v4.semantic_call import SemanticCall
from paper_eval.membind_v4.vdc.observation_adapter import (
    VDCObservationAdapter,
    VDCStaleProbeObservation,
)


@dataclass
class _Node:
    uuid: str
    name: str
    group_id: str = "vdc-observation"
    labels: list[str] = field(default_factory=lambda: ["Entity"])
    summary: str = ""
    attributes: dict[str, object] = field(default_factory=dict)

    def model_dump(self, *, mode="python"):
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
    group_id: str = "vdc-observation"
    valid_at: datetime = datetime(2026, 8, 20, tzinfo=timezone.utc)

    def model_dump(self, *, mode="python"):
        assert mode in {"python", "json"}
        return {
            "content": self.content,
            "group_id": self.group_id,
            "uuid": self.uuid,
            "valid_at": self.valid_at.isoformat(),
        }


class _ResponseModel:
    @classmethod
    def model_json_schema(cls):
        return {"type": "object"}


@dataclass
class _Request:
    captured_request: CapturedGraphitiRequest
    extracted_nodes: list[object]
    candidate_nodes_by_extracted: list[list[object]]
    episode: object
    previous: list[object]


def _prepared(sequence: int) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=sequence,
        source_sha256=str(sequence + 1) * 64,
        evidence_sha256="e" * 64,
        certification_sha256="c" * 64,
        raw_nodes=(
            {
                "attributes": {},
                "group_id": "vdc-observation",
                "labels": ["Entity"],
                "name": f"Entity {sequence}",
                "summary": "",
                "uuid": f"raw-{sequence}",
            },
        ),
        raw_edges=(),
        pure_intermediates={"node_episode_index_map": {f"raw-{sequence}": [0]}},
    )


def _compile_input(sequence: int):
    return SimpleNamespace(
        source=SimpleNamespace(
            source_sequence=sequence,
            group_id="vdc-observation",
        )
    )


class _Factorized:
    def __init__(self) -> None:
        self.provider_started = asyncio.Event()
        self.release_provider = asyncio.Event()
        self.provider_calls = 0
        self.continuations = 0
        self.monolithic_binds = 0
        self.materializations: list[tuple[int, int]] = []

    async def prepare(self, compile_input):
        return _prepared(compile_input.source.source_sequence)

    async def bind(self, *_args, **_kwargs):
        self.monolithic_binds += 1
        raise AssertionError("monolithic bind must not be used")

    def v4_node_resolve_callbacks(self):
        return {
            "materialize_request": self.materialize_request,
            "execute_request": self.execute_request,
            "interpret_response": self.interpret_response,
            "continue_native_bind": self.continue_native_bind,
        }

    async def materialize_request(self, compile_input, prepared, state_version):
        sequence = compile_input.source.source_sequence
        self.materializations.append((sequence, state_version))
        extracted = _Node(uuid=f"raw-{sequence}", name=f"Entity {sequence}")
        candidate = _Node(
            uuid=f"candidate-{sequence}",
            name=f"Existing {sequence}",
        )
        previous = [
            _Episode(uuid=f"episode-{index}", content=f"previous {index}")
            for index in range(state_version)
        ]
        messages = [
            {"role": "system", "content": "dedupe_nodes.nodes"},
            {
                "role": "user",
                "content": {
                    "candidate": candidate.uuid,
                    "previous": [item.uuid for item in previous],
                },
            },
        ]
        request = _Request(
            captured_request=CapturedGraphitiRequest(
                args=(messages,),
                kwargs={
                    "response_model": _ResponseModel,
                    "prompt_name": "dedupe_nodes.nodes",
                },
            ),
            extracted_nodes=[extracted],
            candidate_nodes_by_extracted=[[candidate]],
            episode=_Episode(
                uuid=f"episode-{sequence}",
                content=f"current {sequence}",
            ),
            previous=previous,
        )
        call = SemanticCall.create(
            source_sequence=sequence,
            state_version=state_version,
            operator_identity={"graphiti_version": "0.29.3"},
            model_identity={"model": "fixture"},
            decoding_identity={"temperature": 0},
            response_schema=_ResponseModel.model_json_schema(),
            rendered_request_sha256=("a" if state_version == sequence else "b") * 64,
            token_sequence_sha256=("c" if state_version == sequence else "d") * 64,
            prompt_tokens=100,
            extracted_nodes=(extracted.model_dump(mode="json"),),
            candidate_order=(0,),
            candidate_bindings=(
                {
                    "candidate_id": 0,
                    "uuid": candidate.uuid,
                    "projection": candidate.model_dump(mode="json"),
                    "extracted_node_indices": [0],
                },
            ),
            previous_episodes=tuple(item.model_dump(mode="json") for item in previous),
            episode_context=request.episode.model_dump(mode="json"),
            entity_types=("Entity",),
            execution_mode="LLM",
            operator_revision="fixture-vdc",
        )
        return PreparedSemanticCall(call=call, request=request)

    async def execute_request(self, request):
        self.provider_calls += 1
        self.provider_started.set()
        await self.release_provider.wait()
        return {"entity_resolutions": [{"id": 0, "duplicate_candidate_id": 0}]}

    async def interpret_response(self, _response, exact_call):
        request = exact_call.request
        extracted = request.extracted_nodes[0]
        candidate = request.candidate_nodes_by_extracted[0][0]
        return ([candidate], {extracted.uuid: candidate.uuid}, [(extracted, candidate)])

    async def continue_native_bind(
        self, _compile_input, _prepared, _result, *, logical_time_ns
    ):
        assert logical_time_ns >= 0
        self.continuations += 1
        return {"committed": True}


@pytest.mark.asyncio
async def test_exact_bind_is_captured_only_after_native_suffix_succeeds() -> None:
    factorized = _Factorized()
    captures = []
    adapter = VDCObservationAdapter(
        factorized_adapter=factorized,
        capture_observer=captures.append,
    )
    prepared = await adapter.prepare(_compile_input(1))
    bind_task = asyncio.create_task(
        adapter.bind(_compile_input(1), prepared, logical_time_ns=100)
    )
    await factorized.provider_started.wait()
    assert captures == []
    factorized.release_provider.set()

    result = await bind_task

    assert result == {"committed": True}
    assert factorized.provider_calls == 1
    assert factorized.continuations == 1
    assert factorized.monolithic_binds == 0
    assert len(captures) == 1
    assert captures[0].prepared_artifact.source_sequence == 1
    assert captures[0].state_version == 1


@pytest.mark.asyncio
async def test_future_prepare_during_frontier_launches_read_only_stale_probe() -> None:
    factorized = _Factorized()
    captures = []
    stale: list[VDCStaleProbeObservation] = []
    adapter = VDCObservationAdapter(
        factorized_adapter=factorized,
        capture_observer=captures.append,
        stale_probe_observer=stale.append,
    )
    frontier_prepared = await adapter.prepare(_compile_input(1))
    bind_task = asyncio.create_task(
        adapter.bind(_compile_input(1), frontier_prepared, logical_time_ns=100)
    )
    await factorized.provider_started.wait()

    future_prepared = await adapter.prepare(_compile_input(2))
    await adapter.wait_for_observation_tasks()

    assert future_prepared.source_sequence == 2
    assert factorized.provider_calls == 1
    assert (2, 1) in factorized.materializations
    assert len(stale) == 1
    assert stale[0].source_sequence == 2
    assert stale[0].state_version == 1
    assert stale[0].certificate.semantic_call.source_sequence == 2
    assert stale[0].certificate.semantic_call.state_version == 1

    factorized.release_provider.set()
    await bind_task
    assert factorized.continuations == 1

