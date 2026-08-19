"""Focused tests for the isolated prepared-artifact v4 runner."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v4.live_adapter import V4LiveNodeResolveBridge
from paper_eval.membind_v4.live_runner import (
    V4PreparedSource,
    run_v4_live_prepared_stream,
)
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.semantic_call import SemanticCall


def _sha(value: int) -> str:
    return f"{value:064x}"


def _prepared(sequence: int) -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=sequence,
        source_sha256=_sha(sequence + 1),
        evidence_sha256=_sha(sequence + 2),
        certification_sha256=_sha(sequence + 3),
        raw_nodes=({"uuid": f"raw-{sequence}"},),
        pure_intermediates={"sequence": sequence},
    )


def _call(sequence: int, state: int, marker: str = "same") -> SemanticCall:
    return SemanticCall.create(
        source_sequence=sequence,
        state_version=state,
        operator_identity={"adapter": "runner-fixture"},
        model_identity={"model": "fixture"},
        decoding_identity={"temperature": 0},
        response_schema={"type": "object"},
        rendered_request_sha256=_sha(10),
        token_sequence_sha256=_sha(11),
        prompt_tokens=10,
        extracted_nodes=({"marker": marker},),
        candidate_order=(),
        candidate_bindings=(),
        previous_episodes=(),
        episode_context={"sequence": sequence},
        entity_types=("Entity",),
        operator_revision="runner-v1",
    )


@pytest.mark.asyncio
async def test_runner_overlaps_next_speculation_and_seals_result() -> None:
    started: list[tuple[int, int]] = []
    continued: list[int] = []

    async def materialize(_input, prepared, state_version):
        return {"call": _call(prepared.source_sequence, state_version), "request": (prepared.source_sequence, state_version)}

    async def execute(request):
        started.append(request)
        await asyncio.sleep(0)
        return request

    async def interpret(response, _exact):
        return response

    async def continue_bind(_input, prepared, result, *, logical_time_ns):
        assert logical_time_ns == prepared.source_sequence
        continued.append(prepared.source_sequence)
        return result.interpreted

    bridge = V4LiveNodeResolveBridge(
        materialize_request=materialize,
        execute_request=execute,
        interpret_response=interpret,
        continue_native_bind=continue_bind,
    )
    result = await run_v4_live_prepared_stream(
        stream_id="fixture",
        sources=[
            V4PreparedSource({}, _prepared(0)),
            V4PreparedSource({}, _prepared(1)),
            V4PreparedSource({}, _prepared(2)),
        ],
        bridge=bridge,
        logical_time_ns=lambda sequence: sequence,
    )
    assert result["status"] == "PASS"
    assert result["publication_source_sequences"] == [0, 1, 2]
    assert continued == [0, 1, 2]
    assert result["telemetry"]["semantic_hit_count"] == 2
    assert result["telemetry"]["active_speculation_count"] == 0
    assert len(result["payload_sha256"]) == 64
    # The two future requests were started before their exact validation.
    assert started == [(1, 0), (0, 0), (2, 1)]


def test_runner_rejects_noncontiguous_prepared_sources() -> None:
    with pytest.raises(ValueError, match="source_sequence_invalid"):
        asyncio.run(
            run_v4_live_prepared_stream(
                stream_id="fixture",
                sources=[V4PreparedSource({}, _prepared(1))],
                bridge=V4LiveNodeResolveBridge(
                    materialize_request=lambda *_: None,
                    execute_request=lambda value: value,
                    interpret_response=lambda value, _call: value,
                    continue_native_bind=lambda *_args, **_kwargs: None,
                ),
            )
        )
