"""RED-first contract for the isolated graph-derived temporal Reader."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from paper_eval.temporal_fact_reader import (
    GraphEntityEvidence,
    TemporalFactEvidence,
    TemporalFactReader,
    TemporalFactReaderError,
    render_temporal_fact_reader_prompt,
    render_zep_temporal_context,
)


class _Transport:
    config_sha256 = "a" * 64

    async def complete(self, _request: dict[str, object]) -> object:
        return SimpleNamespace(
            content="The shoes are on the closet shoe rack.",
            prompt_tokens=123,
            completion_tokens=12,
            finish_reason="stop",
        )


def _facts() -> tuple[TemporalFactEvidence, ...]:
    return (
        TemporalFactEvidence(
            retrieval_rank=1,
            edge_uuid="edge-old",
            fact="The shoes were under the bed.",
            source_session_ids=("session-old",),
            valid_at="2025-01-01T00:00:00+00:00",
            invalid_at="2025-02-01T00:00:00+00:00",
            expired_at="2025-02-01T00:00:00+00:00",
            reference_time="2025-01-01T00:00:00+00:00",
        ),
        TemporalFactEvidence(
            retrieval_rank=2,
            edge_uuid="edge-new",
            fact="The shoes are on the closet shoe rack.",
            source_session_ids=("session-new",),
            valid_at="2025-02-01T00:00:00+00:00",
            invalid_at=None,
            expired_at=None,
            reference_time="2025-02-01T00:00:00+00:00",
        ),
    )


def test_temporal_context_preserves_old_and_new_facts_and_all_time_fields() -> None:
    context = render_zep_temporal_context(
        _facts(),
        (
            GraphEntityEvidence(
                retrieval_rank=1,
                node_uuid="node-shoes",
                name="shoes",
                summary="The user's shoes have a current storage location.",
            ),
        ),
    )

    assert context.index("under the bed") < context.index("closet shoe rack")
    assert "valid_at=2025-01-01T00:00:00+00:00" in context
    assert "invalid_at=2025-02-01T00:00:00+00:00" in context
    assert "expired_at=2025-02-01T00:00:00+00:00" in context
    assert "reference_time=2025-01-01T00:00:00+00:00" in context
    assert "invalid_at=present" in context
    assert "expired_at=not-expired" in context
    assert "shoes: The user's shoes" in context


def test_exact_reader_prompt_is_deterministic_and_semantically_complete() -> None:
    first = render_temporal_fact_reader_prompt(
        _facts(),
        (),
        question_date="2025-03-01",
        question="Where are the shoes?",
    )
    second = render_temporal_fact_reader_prompt(
        _facts(),
        (),
        question_date="2025-03-01",
        question="Where are the shoes?",
    )

    assert first == second
    assert "2025-03-01" in first
    assert "Where are the shoes?" in first
    assert "The shoes were under the bed." in first
    assert render_temporal_fact_reader_prompt(
        _facts(),
        (),
        question_date="2025-03-02",
        question="Where are the shoes?",
    ) != first


@pytest.mark.asyncio
async def test_reader_is_one_attempt_gold_blind_and_rejects_length_finish() -> None:
    class Transport:
        config_sha256 = "a" * 64

        async def complete(self, request: dict[str, object]) -> object:
            assert request["messages"][0]["role"] == "system"  # type: ignore[index]
            assert request["messages"][1]["role"] == "user"  # type: ignore[index]
            encoded = str(request)
            assert "GOLD_ANSWER" not in encoded
            assert "answer_session_ids" not in encoded
            return SimpleNamespace(
                content="The shoes are on the closet shoe rack.",
                prompt_tokens=123,
                completion_tokens=500,
                finish_reason="length",
            )

    reader = TemporalFactReader(model="qwen3-32b-fp8", transport=Transport())
    with pytest.raises(TemporalFactReaderError, match="truncated"):
        await reader.answer(
            _facts(),
            (),
            question_date="2025-03-01",
            question="Where are the shoes?",
        )


@pytest.mark.asyncio
async def test_reader_success_artifact_records_finish_reason_and_fixed_identity() -> None:
    reader = TemporalFactReader(model="qwen3-32b-fp8", transport=_Transport())
    result = await reader.answer(
        _facts(),
        (),
        question_date="2025-03-01",
        question="Where are the shoes?",
    )

    artifact = result.to_public_artifact()
    assert artifact["status"] == "SUCCESS"
    assert artifact["finish_reason"] == "stop"
    assert artifact["truncation_count"] == 0
    assert reader.public_config["context_shape"] == "zep_facts_entities_with_validity"
    assert reader.public_config["top_k_edges"] == 20
    assert reader.public_config["top_k_nodes"] == 20
    assert reader.public_config["transport_config_sha256"] == "a" * 64


def test_reader_identity_changes_when_transport_identity_changes() -> None:
    first_transport = _Transport()
    second_transport = _Transport()
    second_transport.config_sha256 = "b" * 64

    first = TemporalFactReader(model="qwen3-32b-fp8", transport=first_transport)
    second = TemporalFactReader(model="qwen3-32b-fp8", transport=second_transport)

    assert first.config_sha256 != second.config_sha256
    assert second.public_config["transport_config_sha256"] == "b" * 64


@pytest.mark.parametrize("value", [None, "", "a" * 63, "A" * 64, "z" * 64])
def test_reader_rejects_missing_or_invalid_transport_identity(value: object) -> None:
    transport = _Transport()
    transport.config_sha256 = value  # type: ignore[assignment]

    with pytest.raises(TemporalFactReaderError, match="transport.*config"):
        TemporalFactReader(model="qwen3-32b-fp8", transport=transport)


def test_reader_rejects_transport_without_config_identity() -> None:
    with pytest.raises(TemporalFactReaderError, match="transport.*config"):
        TemporalFactReader(model="qwen3-32b-fp8", transport=SimpleNamespace())
