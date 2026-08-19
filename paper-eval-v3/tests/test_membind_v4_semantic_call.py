"""TDD contracts for the v4 exact NodeResolve semantic-call identity."""

from __future__ import annotations

from dataclasses import replace

import pytest

from paper_eval.membind_v4.semantic_call import (
    SemanticCall,
    SemanticCallError,
    validate_semantic_call_pair,
)


_SHA = "a" * 64


def _call(
    *,
    state_version: int = 1,
    execution_mode: str = "LLM",
    candidate_order: tuple[str, ...] = ("c0", "c1"),
    previous_episodes: tuple[dict[str, object], ...] = (
        {"sequence": 0, "timestamp": 10, "text": "old"},
    ),
) -> SemanticCall:
    return SemanticCall.create(
        source_sequence=2,
        state_version=state_version,
        operator_identity={"graphiti_version": "0.29.3", "adapter": "v4-node-v1"},
        model_identity={"model": "qwen3-32b-fp8", "revision": "pinned"},
        decoding_identity={"temperature": 0, "top_p": 1, "max_tokens": 16384},
        response_schema={"type": "object", "properties": {"uuid": {"type": "string"}}},
        rendered_request_sha256=None if execution_mode == "NO_LLM" else _SHA,
        token_sequence_sha256=None if execution_mode == "NO_LLM" else "b" * 64,
        prompt_tokens=None if execution_mode == "NO_LLM" else 256,
        extracted_nodes=(
            {"runtime_uuid": "raw-0", "name": "Ada"},
            {"runtime_uuid": "raw-1", "name": "Engine"},
        ),
        candidate_order=candidate_order,
        candidate_bindings=tuple(
            {"candidate_id": item, "uuid": f"uuid-{item}", "projection": {"name": item}}
            for item in candidate_order
        ),
        previous_episodes=previous_episodes,
        episode_context={"episode_type": "message", "session": 1},
        entity_types=("Person", "Entity"),
        execution_mode=execution_mode,
        operator_revision="node-resolve-v4-1",
    )


def test_exact_semantic_call_fingerprint_reuses_across_state_versions() -> None:
    decision = validate_semantic_call_pair(_call(state_version=1), _call(state_version=2))
    assert decision.decision == "REUSE"
    assert decision.speculative_fingerprint == decision.exact_fingerprint


@pytest.mark.parametrize(
    "change",
    [
        lambda c: replace(c, candidate_order=("c1", "c0"), candidate_bindings=tuple(reversed(c.candidate_bindings))),
        lambda c: replace(c, previous_episodes=({"sequence": 0, "timestamp": 11, "text": "old"},)),
        lambda c: replace(c, token_sequence_sha256="c" * 64),
        lambda c: replace(c, response_schema={"type": "array"}),
        lambda c: replace(c, model_identity={"model": "other"}),
        lambda c: replace(c, decoding_identity={"temperature": 1}),
        lambda c: replace(c, extracted_nodes=({"runtime_uuid": "raw-0", "name": "Ada"},)),
    ],
)
def test_semantic_identity_drift_fails_closed_to_exact(change) -> None:
    stale = _call(state_version=1)
    exact = change(_call(state_version=2))
    decision = validate_semantic_call_pair(stale, exact)
    assert decision.decision == "REEXECUTE"
    assert decision.reason == "SEMANTIC_CALL_FINGERPRINT_MISMATCH"


def test_no_llm_is_explicit_and_llm_mode_change_is_a_miss() -> None:
    no_llm = _call(execution_mode="NO_LLM")
    assert no_llm.fingerprint_payload()["execution_mode"] == "NO_LLM"
    with pytest.raises(SemanticCallError, match="no_llm_prompt_hash_forbidden"):
        replace(no_llm, rendered_request_sha256=_SHA).verify()

    decision = validate_semantic_call_pair(no_llm, _call(state_version=2))
    assert decision.decision == "REEXECUTE"


def test_no_llm_cannot_fake_an_empty_prompt_hash() -> None:
    with pytest.raises(SemanticCallError, match="no_llm_prompt_hash_forbidden"):
        SemanticCall.create(
            source_sequence=2,
            state_version=1,
            operator_identity={"graphiti_version": "0.29.3"},
            model_identity={"model": "qwen3-32b-fp8"},
            decoding_identity={"temperature": 0},
            response_schema={"type": "object"},
            rendered_request_sha256="0" * 64,
            token_sequence_sha256=None,
            prompt_tokens=None,
            extracted_nodes=({"runtime_uuid": "raw", "name": "Ada"},),
            candidate_order=(),
            candidate_bindings=(),
            execution_mode="NO_LLM",
        )


def test_record_round_trip_and_tampering_are_detected() -> None:
    call = _call()
    assert SemanticCall.from_record(call.to_record()) == call
    tampered = call.to_record()
    tampered["candidate_order"] = ["c1", "c0"]
    with pytest.raises(SemanticCallError, match="candidate_binding_order_mismatch|fingerprint_mismatch"):
        SemanticCall.from_record(tampered)


def test_state_order_and_source_mismatch_fail_closed() -> None:
    with pytest.raises(SemanticCallError, match="state_order_invalid"):
        validate_semantic_call_pair(_call(state_version=2), _call(state_version=1))
    with pytest.raises(SemanticCallError, match="source_sequence_mismatch"):
        validate_semantic_call_pair(_call(), replace(_call(state_version=2), source_sequence=3))
