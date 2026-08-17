"""RED-first end-to-end contract for one graph-quality overlay question."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.graph_quality_overlay import (
    GraphQualityInputs,
    run_graph_quality_question,
    verify_common_graph_quality_identity,
)
from paper_eval.graphiti_longmemeval_quality import GraphQualityEvidence
from paper_eval.graph_quality_stages import (
    GraphQualityStageError,
    GraphQualityStageStore,
)
from paper_eval.temporal_fact_reader import (
    GraphEntityEvidence,
    TemporalFactEvidence,
    TemporalFactReaderResult,
    render_temporal_fact_reader_prompt,
)


def _inputs(method: str = "U0") -> GraphQualityInputs:
    return GraphQualityInputs(
        overlay_run_id="gq-dev-001",
        method=method,
        history_id="07741c45",
        namespace=f"namespace-{method}",
        question="PRIVATE_QUESTION",
        question_date="2025-03-01",
        question_type="knowledge-update",
        reference_answer="PRIVATE_REFERENCE",
        answer_session_ids=("gold-session",),
        construction_result_sha256="a" * 64,
    )


def _evidence() -> GraphQualityEvidence:
    return GraphQualityEvidence(
        facts=(
            TemporalFactEvidence(
                retrieval_rank=1,
                edge_uuid="edge-1",
                fact="PRIVATE_GRAPH_FACT",
                source_session_ids=("gold-session",),
                valid_at="2025-02-01",
                invalid_at=None,
                expired_at=None,
                reference_time="2025-02-01",
            ),
        ),
        entities=(
            GraphEntityEvidence(
                retrieval_rank=1,
                node_uuid="node-1",
                name="entity",
                summary="PRIVATE_ENTITY_SUMMARY",
            ),
        ),
        search_config_sha256="b" * 64,
        graphiti_search_calls=1,
        neo4j_read_requests=3,
    )


@pytest.mark.asyncio
async def test_overlay_is_gold_blind_until_judge_and_splits_public_private_data() -> None:
    calls: list[str] = []

    async def retrieve(**kwargs: object) -> GraphQualityEvidence:
        calls.append("retrieve")
        assert set(kwargs) == {
            "graph",
            "query",
            "namespace",
            "episode_uuid_to_session_id",
        }
        assert kwargs["query"] == "PRIVATE_QUESTION"
        return _evidence()

    class Reader:
        config_sha256 = "c" * 64

        async def answer(self, facts: object, entities: object, **kwargs: object) -> object:
            calls.append("reader")
            assert facts == _evidence().facts
            assert entities == _evidence().entities
            assert kwargs == {
                "question_date": "2025-03-01",
                "question": "PRIVATE_QUESTION",
            }
            return TemporalFactReaderResult(
                answer="PRIVATE_READER_ANSWER",
                prompt_for_test=render_temporal_fact_reader_prompt(
                    _evidence().facts,
                    _evidence().entities,
                    question_date="2025-03-01",
                    question="PRIVATE_QUESTION",
                ),
                prompt_tokens=80,
                completion_tokens=8,
                finish_reason="stop",
                model="reader-model",
                config_sha256=self.config_sha256,
            )

    class Judge:
        config_sha256 = "d" * 64

        async def evaluate(self, *, hypothesis: str, inputs: object) -> dict[str, object]:
            calls.append("judge")
            assert hypothesis == "PRIVATE_READER_ANSWER"
            assert getattr(inputs, "reference_answer") == "PRIVATE_REFERENCE"
            return {
                "status": "SUCCESS",
                "label": True,
                "parse_status": "YES",
                "retry_count": 0,
                "error_class": None,
                "output_sha256": hashlib.sha256(
                    b"PRIVATE_JUDGE_OUTPUT"
                ).hexdigest(),
            }

    result = await run_graph_quality_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={"episode-1": "gold-session"},
        reader=Reader(),
        judge=Judge(),
        retrieve=retrieve,
    )

    assert calls == ["retrieve", "reader", "judge"]
    assert result.public_artifact["qa_accuracy"] == 1.0
    assert result.public_artifact["edge_attributed_source_coverage_at_10"] == 1.0
    assert result.public_artifact["quality_latency_excluded_from_construction"] is True
    assert result.private_artifact["reader_answer"] == "PRIVATE_READER_ANSWER"
    assert result.private_artifact["facts"][0]["fact"] == "PRIVATE_GRAPH_FACT"
    public_json = json.dumps(result.public_artifact, sort_keys=True)
    for private in (
        "PRIVATE_QUESTION",
        "PRIVATE_REFERENCE",
        "PRIVATE_READER_ANSWER",
        "PRIVATE_READER_PROMPT",
        "PRIVATE_GRAPH_FACT",
        "PRIVATE_ENTITY_SUMMARY",
    ):
        assert private not in public_json


@pytest.mark.asyncio
async def test_invalid_judge_output_is_not_scored_as_incorrect() -> None:
    async def retrieve(**_kwargs: object) -> GraphQualityEvidence:
        return _evidence()

    class Reader:
        config_sha256 = "c" * 64

        async def answer(self, *_args: object, **_kwargs: object) -> object:
            return TemporalFactReaderResult(
                answer="answer",
                prompt_for_test=render_temporal_fact_reader_prompt(
                    _evidence().facts,
                    _evidence().entities,
                    question_date="2025-03-01",
                    question="PRIVATE_QUESTION",
                ),
                prompt_tokens=1,
                completion_tokens=1,
                finish_reason="stop",
                model="reader-model",
                config_sha256=self.config_sha256,
            )

    class Judge:
        config_sha256 = "d" * 64

        async def evaluate(self, **_kwargs: object) -> dict[str, object]:
            return {
                "status": "INVALID_OUTPUT",
                "label": False,
                "parse_status": "INVALID_OUTPUT",
                "retry_count": 0,
                "error_class": None,
            }

    result = await run_graph_quality_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=Reader(),
        judge=Judge(),
        retrieve=retrieve,
    )
    assert result.public_artifact["qa_accuracy"] is None
    assert result.public_artifact["judge_valid_denominator"] == 0
    assert result.public_artifact["headline_eligible"] is False


def test_all_three_methods_require_one_exact_quality_identity() -> None:
    identity = {
        "retrieval_config_sha256": "b" * 64,
        "reader_config_sha256": "c" * 64,
        "judge_config_sha256": "d" * 64,
    }
    assert verify_common_graph_quality_identity(
        {method: dict(identity) for method in ("U0", "A0", "P(C=2)")}
    ) == identity
    with pytest.raises(ValueError, match="identity drift"):
        verify_common_graph_quality_identity(
            {
                "U0": dict(identity),
                "A0": dict(identity),
                "P(C=2)": {**identity, "judge_config_sha256": "f" * 64},
            }
        )


@pytest.mark.asyncio
async def test_reader_and_judge_successes_are_not_resampled_after_disconnects(
    tmp_path: Path,
) -> None:
    calls = {"reader": 0, "judge": 0}

    async def retrieve(**_kwargs: object) -> GraphQualityEvidence:
        return _evidence()

    class Reader:
        config_sha256 = "c" * 64

        async def answer(self, facts: object, entities: object, **kwargs: object) -> object:
            calls["reader"] += 1
            if calls["reader"] > 1:
                raise AssertionError("successful Reader output was resampled")
            return TemporalFactReaderResult(
                answer="PRIVATE_READER_ANSWER",
                prompt_for_test=render_temporal_fact_reader_prompt(
                    facts,  # type: ignore[arg-type]
                    entities,  # type: ignore[arg-type]
                    question_date=kwargs["question_date"],  # type: ignore[arg-type]
                    question=kwargs["question"],  # type: ignore[arg-type]
                ),
                prompt_tokens=80,
                completion_tokens=8,
                finish_reason="stop",
                model="reader-model",
                config_sha256=self.config_sha256,
            )

    class Judge:
        config_sha256 = "d" * 64

        def exact_prompt_sha256(
            self, *, hypothesis: str, inputs: object
        ) -> str:
            semantic_prompt = "\x00".join(
                (
                    getattr(inputs, "question_type"),
                    getattr(inputs, "question"),
                    getattr(inputs, "reference_answer"),
                    hypothesis,
                    "False",
                )
            )
            return hashlib.sha256(semantic_prompt.encode("utf-8")).hexdigest()

        async def evaluate(self, **kwargs: object) -> dict[str, object]:
            calls["judge"] += 1
            if calls["judge"] == 1:
                raise ConnectionError("judge disconnected")
            if calls["judge"] > 2:
                raise AssertionError("successful Judge output was resampled")
            return {
                "status": "SUCCESS",
                "label": True,
                "parse_status": "YES",
                "retry_count": 0,
                "error_class": None,
                "output_sha256": hashlib.sha256(
                    b"PRIVATE_JUDGE_OUTPUT"
                ).hexdigest(),
                "raw_output": "PRIVATE_JUDGE_OUTPUT",
                "prompt_sha256": self.exact_prompt_sha256(
                    hypothesis=kwargs["hypothesis"],  # type: ignore[arg-type]
                    inputs=kwargs["inputs"],
                ),
            }

    with pytest.raises(ConnectionError, match="disconnected"):
        await run_graph_quality_question(
            inputs=_inputs(),
            graph=object(),
            episode_uuid_to_session_id={},
            reader=Reader(),
            judge=Judge(),
            retrieve=retrieve,
            stage_store=GraphQualityStageStore(tmp_path / "attempt-001"),
            runtime_identity_sha256="f" * 64,
        )

    completed = await run_graph_quality_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=Reader(),
        judge=Judge(),
        retrieve=retrieve,
        stage_store=GraphQualityStageStore(tmp_path / "attempt-002"),
        runtime_identity_sha256="f" * 64,
    )
    replayed = await run_graph_quality_question(
        inputs=_inputs(),
        graph=object(),
        episode_uuid_to_session_id={},
        reader=Reader(),
        judge=Judge(),
        retrieve=retrieve,
        stage_store=GraphQualityStageStore(tmp_path / "attempt-003"),
        runtime_identity_sha256="f" * 64,
    )

    assert calls == {"reader": 1, "judge": 2}
    assert completed.public_artifact["qa_accuracy"] == 1.0
    assert replayed.private_artifact["judge_result"]["raw_output"] == (
        "PRIVATE_JUDGE_OUTPUT"
    )
    assert completed.public_artifact["reader_stage_disposition"] == "RESTORED"
    assert replayed.public_artifact["judge_stage_disposition"] == "RESTORED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (("question", "DRIFTED_QUESTION"), ("question_date", "2025-03-02")),
)
async def test_reader_recovery_fails_closed_on_semantic_input_drift(
    tmp_path: Path, field: str, value: str
) -> None:
    calls = {"reader": 0, "judge": 0}

    async def retrieve(**_kwargs: object) -> GraphQualityEvidence:
        return _evidence()

    class Reader:
        config_sha256 = "c" * 64

        async def answer(
            self, facts: object, entities: object, **kwargs: object
        ) -> TemporalFactReaderResult:
            calls["reader"] += 1
            return TemporalFactReaderResult(
                answer="PRIVATE_READER_ANSWER",
                prompt_for_test=render_temporal_fact_reader_prompt(
                    facts,  # type: ignore[arg-type]
                    entities,  # type: ignore[arg-type]
                    question_date=kwargs["question_date"],  # type: ignore[arg-type]
                    question=kwargs["question"],  # type: ignore[arg-type]
                ),
                prompt_tokens=80,
                completion_tokens=8,
                finish_reason="stop",
                model="reader-model",
                config_sha256=self.config_sha256,
            )

    class Judge:
        config_sha256 = "d" * 64

        def exact_prompt_sha256(self, **_kwargs: object) -> str:
            return "8" * 64

        async def evaluate(self, **_kwargs: object) -> dict[str, object]:
            calls["judge"] += 1
            raise ConnectionError("judge disconnected")

    original = _inputs()
    with pytest.raises(ConnectionError, match="disconnected"):
        await run_graph_quality_question(
            inputs=original,
            graph=object(),
            episode_uuid_to_session_id={},
            reader=Reader(),
            judge=Judge(),
            retrieve=retrieve,
            stage_store=GraphQualityStageStore(tmp_path / "attempt-001"),
            runtime_identity_sha256="f" * 64,
        )

    with pytest.raises(GraphQualityStageError, match="identity drift"):
        await run_graph_quality_question(
            inputs=replace(original, **{field: value}),
            graph=object(),
            episode_uuid_to_session_id={},
            reader=Reader(),
            judge=Judge(),
            retrieve=retrieve,
            stage_store=GraphQualityStageStore(tmp_path / "attempt-002"),
            runtime_identity_sha256="f" * 64,
        )

    assert calls == {"reader": 1, "judge": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("question_type", "single-session-user"),
        ("reference_answer", "DRIFTED_REFERENCE"),
    ),
)
async def test_judge_recovery_fails_closed_on_semantic_input_drift(
    tmp_path: Path, field: str, value: str
) -> None:
    calls = {"reader": 0, "judge": 0}

    async def retrieve(**_kwargs: object) -> GraphQualityEvidence:
        return _evidence()

    class Reader:
        config_sha256 = "c" * 64

        async def answer(
            self, facts: object, entities: object, **kwargs: object
        ) -> TemporalFactReaderResult:
            calls["reader"] += 1
            return TemporalFactReaderResult(
                answer="PRIVATE_READER_ANSWER",
                prompt_for_test=render_temporal_fact_reader_prompt(
                    facts,  # type: ignore[arg-type]
                    entities,  # type: ignore[arg-type]
                    question_date=kwargs["question_date"],  # type: ignore[arg-type]
                    question=kwargs["question"],  # type: ignore[arg-type]
                ),
                prompt_tokens=80,
                completion_tokens=8,
                finish_reason="stop",
                model="reader-model",
                config_sha256=self.config_sha256,
            )

    class Judge:
        config_sha256 = "d" * 64

        def exact_prompt_sha256(
            self, *, hypothesis: str, inputs: object
        ) -> str:
            semantic_prompt = "\x00".join(
                (
                    getattr(inputs, "question_type"),
                    getattr(inputs, "question"),
                    getattr(inputs, "reference_answer"),
                    hypothesis,
                    "False",
                )
            )
            return hashlib.sha256(semantic_prompt.encode("utf-8")).hexdigest()

        async def evaluate(
            self, *, hypothesis: str, inputs: object
        ) -> dict[str, object]:
            calls["judge"] += 1
            return {
                "status": "SUCCESS",
                "label": True,
                "parse_status": "YES",
                "retry_count": 0,
                "error_class": None,
                "output_sha256": hashlib.sha256(b"PRIVATE_JUDGE_OUTPUT").hexdigest(),
                "raw_output": "PRIVATE_JUDGE_OUTPUT",
                "prompt_sha256": self.exact_prompt_sha256(
                    hypothesis=hypothesis,
                    inputs=inputs,
                ),
            }

    original = _inputs()
    await run_graph_quality_question(
        inputs=original,
        graph=object(),
        episode_uuid_to_session_id={},
        reader=Reader(),
        judge=Judge(),
        retrieve=retrieve,
        stage_store=GraphQualityStageStore(tmp_path / "attempt-001"),
        runtime_identity_sha256="f" * 64,
    )

    with pytest.raises(GraphQualityStageError, match="identity drift"):
        await run_graph_quality_question(
            inputs=replace(original, **{field: value}),
            graph=object(),
            episode_uuid_to_session_id={},
            reader=Reader(),
            judge=Judge(),
            retrieve=retrieve,
            stage_store=GraphQualityStageStore(tmp_path / "attempt-002"),
            runtime_identity_sha256="f" * 64,
        )

    assert calls == {"reader": 1, "judge": 1}
