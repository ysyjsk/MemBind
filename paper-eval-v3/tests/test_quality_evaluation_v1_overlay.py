"""TDD contracts for one resumable Quality Evaluation v1 question."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from paper_eval.graph_quality_stages import GraphQualityStageStore
from paper_eval.quality_evaluation_v1 import (
    CONTEXT_POLICY_SHA256,
    RetrievedEpisode,
    RetrievedFact,
)
from paper_eval.quality_evaluation_v1_overlay import (
    QualityV1Inputs,
    run_quality_v1_question,
)
from paper_eval.quality_evaluation_v1_reader import (
    QualityEvaluationV1ReaderInvalidOutput,
    render_quality_v1_prompt,
)
from paper_eval.quality_evaluation_v1_retrieval import QualityV1RetrievalBundle
from paper_eval.temporal_fact_reader import TemporalFactReaderResult


def _record() -> dict:
    return {
        "question": "What is the current ratio?",
        "question_date": "2023-03-01T00:00:00+00:00",
        "question_type": "knowledge-update",
        "answer": "5 ounces",
        "answer_session_ids": ["s-old", "s-new"],
        "haystack_session_ids": ["s-old", "s-new", "noise"],
        "haystack_dates": [
            "2023-01-01T00:00:00+00:00",
            "2023-02-01T00:00:00+00:00",
            "2023-03-01T00:00:00+00:00",
        ],
        "haystack_sessions": [
            [{"role": "user", "content": "The ratio was 6 ounces."}],
            [{"role": "user", "content": "The ratio is now 5 ounces."}],
            [{"role": "user", "content": "Noise."}],
        ],
    }


def _retrieval() -> QualityV1RetrievalBundle:
    facts = (
        RetrievedFact(
            1,
            "edge-new",
            "user",
            "ratio",
            "HAS_VALUE",
            "The ratio is now 5 ounces.",
            ("s-new",),
            "2023-02-01T00:00:00+00:00",
            None,
            None,
            "2023-02-01T00:00:00+00:00",
        ),
    )
    episodes = (
        RetrievedEpisode(1, "ep-noise", "noise"),
        RetrievedEpisode(2, "ep-new", "s-new"),
        RetrievedEpisode(3, "ep-old", "s-old"),
    )
    return QualityV1RetrievalBundle(facts, episodes, "1" * 64, 1, 2)


@dataclass
class _Reader:
    config_sha256: str = "2" * 64
    calls: int = 0
    invalid: bool = False

    async def answer(self, *, context_json, question_date, question):
        self.calls += 1
        if self.invalid:
            raise QualityEvaluationV1ReaderInvalidOutput(
                "finish_reason is not stop"
            )
        prompt = render_quality_v1_prompt(
            context_json=context_json,
            question_date=question_date,
            question=question,
        )
        return TemporalFactReaderResult(
            answer="5 ounces",
            prompt_for_test=prompt,
            prompt_tokens=90,
            completion_tokens=3,
            finish_reason="stop",
            model="qwen3-32b-fp8",
            config_sha256=self.config_sha256,
        )


@dataclass
class _Judge:
    config_sha256: str = "3" * 64
    calls: int = 0

    def exact_prompt_sha256(self, *, hypothesis, inputs):
        return hashlib.sha256(
            f"{inputs.question}|{inputs.reference_answer}|{hypothesis}".encode()
        ).hexdigest()

    async def evaluate(self, *, hypothesis, inputs):
        self.calls += 1
        prompt = self.exact_prompt_sha256(hypothesis=hypothesis, inputs=inputs)
        return {
            "status": "SUCCESS",
            "label": True,
            "model": "qwen3-32b-fp8",
            "prompt_sha256": prompt,
            "config_sha256": "4" * 64,
            "output_sha256": hashlib.sha256(b"yes").hexdigest(),
            "output_character_count": 3,
            "output_byte_count": 3,
            "parse_status": "YES",
            "retry_count": 0,
            "error_class": None,
            "raw_output": "yes",
        }


def _inputs() -> QualityV1Inputs:
    return QualityV1Inputs(
        overlay_run_id="qev1-dev-20260817-001",
        method="U0",
        history_id="07741c45",
        namespace="namespace-a",
        construction_result_sha256="5" * 64,
        record=_record(),
    )


@pytest.mark.asyncio
async def test_success_emits_metrics_private_answer_and_content_free_public() -> None:
    reader = _Reader()
    judge = _Judge()

    async def retrieve(**_kwargs):
        return _retrieval()

    result = await run_quality_v1_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=reader,
        judge=judge,
        retrieve=retrieve,
        runtime_identity_sha256="6" * 64,
    )

    assert result.public_artifact["qa_accuracy"] == 1.0
    assert result.public_artifact["session_metrics"]["recall_at_1"] == 0.0
    assert result.public_artifact["session_metrics"]["recall_at_3"] == 1.0
    assert result.public_artifact["failure_category"] == "SUCCESS"
    assert result.public_artifact["context"]["context_policy_sha256"] == (
        CONTEXT_POLICY_SHA256
    )
    assert result.private_artifact["predicted_answer"] == "5 ounces"
    assert "5 ounces" not in repr(result.public_artifact)


@pytest.mark.asyncio
async def test_reader_invalid_is_excluded_and_does_not_call_judge() -> None:
    reader = _Reader(invalid=True)
    judge = _Judge()

    async def retrieve(**_kwargs):
        return _retrieval()

    result = await run_quality_v1_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=reader,
        judge=judge,
        retrieve=retrieve,
        runtime_identity_sha256="6" * 64,
    )

    assert result.public_artifact["qa_accuracy"] is None
    assert result.public_artifact["judge_valid_denominator"] == 0
    assert result.public_artifact["failure_category"] == "READER_INVALID"
    assert judge.calls == 0


@pytest.mark.asyncio
async def test_completed_reader_and_judge_stages_are_reused(tmp_path) -> None:
    reader = _Reader()
    judge = _Judge()

    async def retrieve(**_kwargs):
        return _retrieval()

    store = GraphQualityStageStore(tmp_path / "attempt-001")
    first = await run_quality_v1_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=reader,
        judge=judge,
        retrieve=retrieve,
        stage_store=store,
        runtime_identity_sha256="6" * 64,
    )
    second = await run_quality_v1_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=reader,
        judge=judge,
        retrieve=retrieve,
        stage_store=store,
        runtime_identity_sha256="6" * 64,
    )

    assert reader.calls == 1
    assert judge.calls == 1
    assert first.public_artifact["reader"]["output_sha256"] == second.public_artifact[
        "reader"
    ]["output_sha256"]
    assert first.public_artifact["qa_accuracy"] == second.public_artifact[
        "qa_accuracy"
    ]
