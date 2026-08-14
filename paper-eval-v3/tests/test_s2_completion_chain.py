from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.s2_completion_chain import (
    BoundedRetrievalOutcome,
    CompletionChainError,
    execute_bounded_completion_chain,
)
from paper_eval.s2_live import S2LiveInputs
from paper_eval.s2_session_reader import OfficialSessionReader


def _record() -> dict[str, object]:
    session_ids = [f"s{index}" for index in range(12)]
    return {
        "question_id": "history-dev",
        "question_type": "knowledge-update",
        "question": "What is current?",
        "question_date": "2024/03/01",
        "answer": "current-value",
        "answer_session_ids": ["s1", "s2"],
        "haystack_session_ids": session_ids,
        "haystack_dates": [f"2024/01/{index + 1:02d}" for index in range(12)],
        "haystack_sessions": [
            [
                {"role": "user", "content": f"question-{index}"},
                {"role": "assistant", "content": f"answer-{index}"},
            ]
            for index in range(12)
        ],
    }


def _inputs() -> S2LiveInputs:
    return S2LiveInputs(
        run_id="s2-completion-test",
        history_id="history-dev",
        namespace="pev3-s1-namespace-dev",
        question="What is current?",
        question_date="2024/03/01",
        question_type="knowledge-update",
        reference_answer="current-value",
        answer_session_ids=("s1", "s2"),
    )


@dataclass
class _Transport:
    calls: int = 0

    async def complete(self, request: dict[str, object]) -> object:
        self.calls += 1
        return SimpleNamespace(
            content="current-value",
            prompt_tokens=100,
            completion_tokens=2,
        )


@dataclass
class _Judge:
    label: bool = True
    parse_status: str = "YES"
    calls: int = 0

    async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs) -> dict:
        self.calls += 1
        assert hypothesis == "current-value"
        assert inputs.question_type == "knowledge-update"
        return {
            "status": "SUCCESS",
            "label": self.label,
            "model": "qwen3-32b-fp8",
            "prompt_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            "output_sha256": "c" * 64,
            "output_character_count": 3,
            "output_byte_count": 3,
            "parse_status": self.parse_status,
            "retry_count": 0,
            "error_class": None,
        }


def _retrieval() -> BoundedRetrievalOutcome:
    return BoundedRetrievalOutcome(
        retrieved_session_ids=tuple(f"s{index}" for index in range(10)),
        graphiti_search_calls=1,
        neo4j_read_requests=2,
        construction_llm_requests=0,
        embedding_requests=0,
        cross_encoder_requests=0,
        database_mutation_attempts=0,
        database_mutations=0,
        cleanup_calls=0,
        retry_count=0,
    )


def test_synthetic_retrieval_reader_judge_chain_counts_once_and_is_safe() -> None:
    retrieval_calls: list[dict[str, str]] = []

    async def retrieve(*, question: str, namespace: str) -> BoundedRetrievalOutcome:
        retrieval_calls.append({"question": question, "namespace": namespace})
        return _retrieval()

    transport = _Transport()
    reader = OfficialSessionReader(model="qwen3-32b-fp8", transport=transport)
    judge = _Judge()

    result = asyncio.run(
        execute_bounded_completion_chain(
            inputs=_inputs(),
            dataset_record=_record(),
            retrieve=retrieve,
            reader=reader,
            judge=judge,
        )
    )

    assert retrieval_calls == [
        {
            "question": "What is current?",
            "namespace": "pev3-s1-namespace-dev",
        }
    ]
    assert transport.calls == 1
    assert judge.calls == 1
    assert result.metrics.session_recall_any_at_10 == 1.0
    assert result.metrics.session_recall_all_at_10 == 1.0
    assert result.qa_accuracy == 1.0
    assert result.counters == {
        "graphiti_search_calls": 1,
        "neo4j_read_requests": 2,
        "reader_requests": 1,
        "judge_requests": 1,
        "construction_llm_requests": 0,
        "embedding_requests": 0,
        "cross_encoder_requests": 0,
        "database_mutation_attempts": 0,
        "database_mutations": 0,
        "cleanup_calls": 0,
        "retry_count": 0,
    }
    artifact = result.to_artifact()
    encoded = json.dumps(artifact, sort_keys=True)
    for raw in (
        "What is current?",
        "current-value",
        "question-0",
        "answer-0",
        "pev3-s1-namespace-dev",
    ):
        assert raw not in encoded
    assert artifact["retrieved_session_count"] == 10
    assert artifact["gold_session_count"] == 2
    assert artifact["qa_accuracy"] == 1.0


def test_gold_labels_are_not_passed_to_retrieval_or_reader_context_selection() -> None:
    seen: dict[str, object] = {}

    async def retrieve(*, question: str, namespace: str) -> BoundedRetrievalOutcome:
        seen["retrieval_args"] = (question, namespace)
        return _retrieval()

    class InspectReader(OfficialSessionReader):
        async def answer(self, sessions, *, question_date: str, question: str):
            seen["reader_session_ids"] = tuple(item.session_id for item in sessions)
            seen["reader_text"] = repr(sessions)
            return await super().answer(
                sessions, question_date=question_date, question=question
            )

    asyncio.run(
        execute_bounded_completion_chain(
            inputs=_inputs(),
            dataset_record=_record(),
            retrieve=retrieve,
            reader=InspectReader(model="qwen3-32b-fp8", transport=_Transport()),
            judge=_Judge(),
        )
    )

    assert seen["retrieval_args"] == (
        "What is current?",
        "pev3-s1-namespace-dev",
    )
    assert seen["reader_session_ids"] == tuple(f"s{index}" for index in range(10))
    assert "answer_session_ids" not in seen["reader_text"]
    assert "current-value" not in seen["reader_text"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("graphiti_search_calls", 2),
        ("neo4j_read_requests", 0),
        ("construction_llm_requests", 1),
        ("embedding_requests", 1),
        ("cross_encoder_requests", 1),
        ("database_mutation_attempts", 1),
        ("database_mutations", 1),
        ("cleanup_calls", 1),
        ("retry_count", 1),
    ],
)
def test_chain_rejects_retrieval_budget_violation_before_reader(
    field: str, value: int
) -> None:
    retrieval = _retrieval()
    object.__setattr__(retrieval, field, value)
    transport = _Transport()

    async def retrieve(**_kwargs) -> BoundedRetrievalOutcome:
        return retrieval

    with pytest.raises(CompletionChainError, match="retrieval budget"):
        asyncio.run(
            execute_bounded_completion_chain(
                inputs=_inputs(),
                dataset_record=_record(),
                retrieve=retrieve,
                reader=OfficialSessionReader(
                    model="qwen3-32b-fp8", transport=transport
                ),
                judge=_Judge(),
            )
        )
    assert transport.calls == 0


@pytest.mark.parametrize(
    ("status", "parse_status", "label"),
    [
        ("INVALID_OUTPUT", "INVALID", False),
        ("SUCCESS", "INVALID", False),
        ("SUCCESS", "YES", None),
    ],
)
def test_chain_rejects_invalid_judge_output_as_incomplete_not_qa_miss(
    status: str, parse_status: str, label: object
) -> None:
    class InvalidJudge(_Judge):
        async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs) -> dict:
            value = await super().evaluate(hypothesis=hypothesis, inputs=inputs)
            value.update(status=status, parse_status=parse_status, label=label)
            return value

    async def retrieve(**_kwargs) -> BoundedRetrievalOutcome:
        return _retrieval()

    with pytest.raises(CompletionChainError, match="Judge output"):
        asyncio.run(
            execute_bounded_completion_chain(
                inputs=_inputs(),
                dataset_record=_record(),
                retrieve=retrieve,
                reader=OfficialSessionReader(
                    model="qwen3-32b-fp8", transport=_Transport()
                ),
                judge=InvalidJudge(),
            )
        )


def test_valid_negative_judge_output_is_a_real_zero_qa_value() -> None:
    async def retrieve(**_kwargs) -> BoundedRetrievalOutcome:
        return _retrieval()

    result = asyncio.run(
        execute_bounded_completion_chain(
            inputs=_inputs(),
            dataset_record=_record(),
            retrieve=retrieve,
            reader=OfficialSessionReader(
                model="qwen3-32b-fp8", transport=_Transport()
            ),
            judge=_Judge(label=False, parse_status="NO"),
        )
    )

    assert result.qa_accuracy == 0.0
    assert result.reference_sanity_status == "REVIEW_REQUIRED"
    assert result.s3_ready is False


def test_chain_emits_safe_stage_checkpoints_before_advancing() -> None:
    checkpoints: list[tuple[str, dict[str, object]]] = []

    async def retrieve(**_kwargs) -> BoundedRetrievalOutcome:
        return _retrieval()

    def checkpoint(stage: str, evidence: dict[str, object]) -> None:
        checkpoints.append((stage, evidence))

    asyncio.run(
        execute_bounded_completion_chain(
            inputs=_inputs(),
            dataset_record=_record(),
            retrieve=retrieve,
            reader=OfficialSessionReader(
                model="qwen3-32b-fp8", transport=_Transport()
            ),
            judge=_Judge(),
            on_checkpoint=checkpoint,
        )
    )

    assert [stage for stage, _ in checkpoints] == [
        "retrieval_complete",
        "reader_complete",
        "judge_complete",
    ]
    encoded = json.dumps(checkpoints, sort_keys=True)
    for raw in ("What is current?", "current-value", "question-0", "answer-0"):
        assert raw not in encoded
    assert checkpoints[0][1]["retrieved_session_count"] == 10
    assert checkpoints[1][1]["status"] == "SUCCESS"
    assert checkpoints[2][1]["parse_status"] == "YES"
