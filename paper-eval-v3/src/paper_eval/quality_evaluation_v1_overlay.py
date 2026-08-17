"""One resumable, read-only Quality Evaluation v1 question."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping

from .artifacts import payload_sha256
from .graph_quality_stages import GraphQualityStageStore
from .quality_evaluation_v1 import (
    CONTEXT_POLICY_SHA256,
    build_context_pack,
    edge_provenance_metrics,
    session_ranking_metrics,
    temporal_diagnostics,
)
from .quality_evaluation_v1_reader import (
    QualityEvaluationV1ReaderInvalidOutput,
    render_quality_v1_prompt,
)
from .quality_evaluation_v1_retrieval import (
    QualityV1RetrievalBundle,
    retrieve_quality_v1,
)
from .quality_terminal_semantics import classify_judge_artifact
from .temporal_fact_reader import TemporalFactReaderResult


@dataclass(frozen=True)
class QualityV1Inputs:
    overlay_run_id: str
    method: str
    history_id: str
    namespace: str
    construction_result_sha256: str
    record: Mapping[str, Any]


@dataclass(frozen=True)
class QualityV1QuestionResult:
    public_artifact: dict[str, Any]
    private_artifact: dict[str, Any]


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Quality v1 {field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Quality v1 {field} is invalid")
    return value


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _component_hash(value: object, *, field: str) -> str:
    return _sha(getattr(value, "config_sha256", None), field=field)


def _record_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ValueError("Quality v1 record is invalid")
    result = dict(record)
    for field in ("question", "question_date", "question_type"):
        _text(result.get(field), field=field)
    answer = str(result.get("answer", ""))
    _text(answer, field="reference answer")
    gold = result.get("answer_session_ids")
    if (
        not isinstance(gold, list)
        or not gold
        or any(not isinstance(value, str) or not value for value in gold)
        or len(set(gold)) != len(gold)
    ):
        raise ValueError("Quality v1 gold session inventory is invalid")
    return result


def _public_judge(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: child for key, child in value.items() if key != "raw_output"}


def _base_private(
    *,
    inputs: QualityV1Inputs,
    record: Mapping[str, Any],
    retrieval: QualityV1RetrievalBundle,
    context_json: str,
) -> dict[str, Any]:
    return {
        "schema_version": "membind.paper-eval-v3.quality-v1-private.v1",
        "overlay_run_id": inputs.overlay_run_id,
        "method": inputs.method,
        "history_id": inputs.history_id,
        "namespace": inputs.namespace,
        "question": record["question"],
        "question_date": record["question_date"],
        "question_type": record["question_type"],
        "reference_answer": str(record["answer"]),
        "answer_session_ids": list(record["answer_session_ids"]),
        "facts": [asdict(value) for value in retrieval.facts],
        "episodes": [asdict(value) for value in retrieval.episodes],
        "reader_context_json": context_json,
        "context_policy_sha256": CONTEXT_POLICY_SHA256,
    }


async def run_quality_v1_question(
    *,
    inputs: QualityV1Inputs,
    graph: Any,
    episode_uuid_to_session_id: Mapping[str, str],
    reader: Any,
    judge: Any,
    retrieve: Callable[..., Awaitable[QualityV1RetrievalBundle]] = retrieve_quality_v1,
    stage_store: GraphQualityStageStore | None = None,
    runtime_identity_sha256: str,
) -> QualityV1QuestionResult:
    """Evaluate one sealed graph; gold labels enter only post-retrieval metrics."""

    if not isinstance(inputs, QualityV1Inputs):
        raise ValueError("Quality v1 inputs are invalid")
    record = _record_fields(inputs.record)
    runtime_hash = _sha(runtime_identity_sha256, field="runtime identity")
    started = time.monotonic_ns()
    retrieval = await retrieve(
        graph=graph,
        query=record["question"],
        namespace=inputs.namespace,
        episode_uuid_to_session_id=episode_uuid_to_session_id,
    )
    retrieval_done = time.monotonic_ns()
    if not isinstance(retrieval, QualityV1RetrievalBundle):
        raise ValueError("Quality v1 retrieval result is invalid")
    ranked_sessions = tuple(value.session_id for value in retrieval.episodes)
    gold_sessions = tuple(record["answer_session_ids"])
    session_metrics = session_ranking_metrics(ranked_sessions, gold_sessions)
    edge_metrics = edge_provenance_metrics(retrieval.facts, gold_sessions)
    temporal_metrics = temporal_diagnostics(
        retrieval.facts,
        question_date=record["question_date"],
    )
    context = build_context_pack(
        record=record,
        question=record["question"],
        facts=retrieval.facts,
        episodes=retrieval.episodes,
    )
    exact_prompt = render_quality_v1_prompt(
        context_json=context.context_json,
        question_date=record["question_date"],
        question=record["question"],
    )
    evidence_payload = {
        "facts": [asdict(value) for value in retrieval.facts],
        "episodes": [asdict(value) for value in retrieval.episodes],
        "context_json": context.context_json,
    }
    reader_binding = {
        "overlay_run_id": inputs.overlay_run_id,
        "method": inputs.method,
        "history_id": inputs.history_id,
        "namespace_sha256": _hash(inputs.namespace),
        "construction_result_sha256": _sha(
            inputs.construction_result_sha256,
            field="construction result",
        ),
        "runtime_identity_sha256": runtime_hash,
        "retrieval_config_sha256": _sha(
            retrieval.search_config_sha256,
            field="retrieval config",
        ),
        "evidence_sha256": payload_sha256(evidence_payload),
        "reader_config_sha256": _component_hash(reader, field="Reader config"),
        "question_sha256": _hash(record["question"]),
        "question_date_sha256": _hash(record["question_date"]),
        "reader_prompt_sha256": _hash(exact_prompt),
    }
    restored_reader = stage_store.load_reader(reader_binding) if stage_store else None
    reader_stage_sha: str | None = None
    reader_disposition = "NOT_CHECKPOINTED"
    if restored_reader is not None:
        reader_result, reader_stage_sha = restored_reader
        reader_disposition = "RESTORED"
    else:
        try:
            reader_result = await reader.answer(
                context_json=context.context_json,
                question_date=record["question_date"],
                question=record["question"],
            )
        except QualityEvaluationV1ReaderInvalidOutput as error:
            reader_done = time.monotonic_ns()
            private = _base_private(
                inputs=inputs,
                record=record,
                retrieval=retrieval,
                context_json=context.context_json,
            )
            private.update(
                {
                    "status": "READER_INVALID",
                    "error_class": type(error).__name__,
                    "predicted_answer": None,
                    "judge_result": None,
                }
            )
            private["payload_sha256"] = payload_sha256(private)
            public = _build_public(
                inputs=inputs,
                retrieval=retrieval,
                context=context,
                session_metrics=session_metrics,
                edge_metrics=edge_metrics,
                temporal_metrics=temporal_metrics,
                reader_result=None,
                judge_result=None,
                private_hash=private["payload_sha256"],
                failure_category="READER_INVALID",
                latency={
                    "retrieval": retrieval_done - started,
                    "reader": reader_done - retrieval_done,
                    "judge": 0,
                    "total": reader_done - started,
                },
                reader_disposition="INVALID_NOT_CHECKPOINTED",
                judge_disposition="NOT_RUN",
            )
            return QualityV1QuestionResult(public, private)
        if not isinstance(reader_result, TemporalFactReaderResult):
            raise ValueError("Quality v1 Reader result is invalid")
        if reader_result.prompt_for_test != exact_prompt:
            raise ValueError("Quality v1 Reader prompt identity drift")
        if stage_store is not None:
            reader_stage_sha = stage_store.persist_reader(
                reader_binding, reader_result
            )
            reader_disposition = "EXECUTED_AND_SEALED"
    reader_done = time.monotonic_ns()
    if not isinstance(reader_result, TemporalFactReaderResult):
        raise ValueError("Quality v1 Reader result is invalid")

    judge_inputs = SimpleNamespace(
        run_id=inputs.overlay_run_id,
        history_id=inputs.history_id,
        question_type=record["question_type"],
        question=record["question"],
        reference_answer=str(record["answer"]),
    )
    exact_judge = getattr(judge, "exact_prompt_sha256", None)
    if not callable(exact_judge):
        raise ValueError("Quality v1 Judge prompt identity is unavailable")
    judge_prompt_sha = _sha(
        exact_judge(hypothesis=reader_result.answer, inputs=judge_inputs),
        field="Judge prompt",
    )
    judge_binding = {
        **reader_binding,
        "reader_stage_sha256": _sha(
            reader_stage_sha or payload_sha256(asdict(reader_result)),
            field="Reader stage",
        ),
        "judge_config_sha256": _component_hash(judge, field="Judge config"),
        "question_type_sha256": _hash(record["question_type"]),
        "reference_answer_sha256": _hash(str(record["answer"])),
        "reader_answer_sha256": _hash(reader_result.answer),
        "judge_prompt_sha256": judge_prompt_sha,
    }
    restored_judge = stage_store.load_judge(judge_binding) if stage_store else None
    judge_disposition = "NOT_CHECKPOINTED"
    if restored_judge is not None:
        judge_result, _judge_stage_sha = restored_judge
        judge_disposition = "RESTORED"
    else:
        judge_result = dict(
            await judge.evaluate(
                hypothesis=reader_result.answer,
                inputs=judge_inputs,
            )
        )
        if stage_store is not None:
            stage_store.persist_judge(judge_binding, judge_result)
            judge_disposition = "EXECUTED_AND_SEALED"
    judge_done = time.monotonic_ns()
    outcome = classify_judge_artifact(judge_result)
    context_sources = {
        value.session_id for value in retrieval.episodes[:10]
    }.union(
        source for fact in retrieval.facts for source in fact.source_session_ids
    )
    context_gold_coverage = len(set(gold_sessions).intersection(context_sources)) / len(
        gold_sessions
    )
    if outcome.correct is True:
        failure_category = "SUCCESS"
    elif context_gold_coverage < 1.0:
        failure_category = "CONTEXT_EVIDENCE_COVERAGE_INCOMPLETE"
    else:
        failure_category = "READER_OR_JUDGE_INCORRECT"
    private = _base_private(
        inputs=inputs,
        record=record,
        retrieval=retrieval,
        context_json=context.context_json,
    )
    private.update(
        {
            "status": outcome.status,
            "predicted_answer": reader_result.answer,
            "reader_result": asdict(reader_result),
            "judge_result": dict(judge_result),
            "context_gold_session_coverage_posthoc": context_gold_coverage,
            "failure_category": failure_category,
        }
    )
    private["payload_sha256"] = payload_sha256(private)
    public = _build_public(
        inputs=inputs,
        retrieval=retrieval,
        context=context,
        session_metrics=session_metrics,
        edge_metrics=edge_metrics,
        temporal_metrics=temporal_metrics,
        reader_result=reader_result,
        judge_result=judge_result,
        private_hash=private["payload_sha256"],
        failure_category=failure_category,
        latency={
            "retrieval": retrieval_done - started,
            "reader": reader_done - retrieval_done,
            "judge": judge_done - reader_done,
            "total": judge_done - started,
        },
        reader_disposition=reader_disposition,
        judge_disposition=judge_disposition,
    )
    public["context_gold_session_coverage_posthoc"] = context_gold_coverage
    public["payload_sha256"] = payload_sha256(
        {key: value for key, value in public.items() if key != "payload_sha256"}
    )
    return QualityV1QuestionResult(public, private)


def _build_public(
    *,
    inputs: QualityV1Inputs,
    retrieval: QualityV1RetrievalBundle,
    context: Any,
    session_metrics: Mapping[str, Any],
    edge_metrics: Mapping[str, Any],
    temporal_metrics: Mapping[str, Any],
    reader_result: TemporalFactReaderResult | None,
    judge_result: Mapping[str, Any] | None,
    private_hash: str,
    failure_category: str,
    latency: Mapping[str, int],
    reader_disposition: str,
    judge_disposition: str,
) -> dict[str, Any]:
    outcome = classify_judge_artifact(judge_result) if judge_result is not None else None
    public: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.quality-v1-public.v1",
        "overlay_run_id": inputs.overlay_run_id,
        "method": inputs.method,
        "history_id": inputs.history_id,
        "namespace_sha256": _hash(inputs.namespace),
        "construction_result_sha256": inputs.construction_result_sha256,
        "retrieval_config_sha256": retrieval.search_config_sha256,
        "session_metrics": dict(session_metrics),
        "edge_provenance_metrics": dict(edge_metrics),
        "temporal_diagnostics": dict(temporal_metrics),
        "context": {
            "evidence_count": context.evidence_count,
            "fact_count": context.fact_count,
            "session_candidate_count": context.session_candidate_count,
            "local_round_count": context.local_round_count,
            "context_sha256": _hash(context.context_json),
            "context_policy_sha256": CONTEXT_POLICY_SHA256,
        },
        "reader": reader_result.to_public_artifact() if reader_result else None,
        "judge": _public_judge(judge_result) if judge_result else None,
        "qa_accuracy": (
            1.0 if outcome and outcome.correct is True else 0.0
            if outcome and outcome.correct is False
            else None
        ),
        "judge_valid_denominator": 1 if outcome and outcome.included else 0,
        "headline_eligible": bool(outcome and outcome.included),
        "failure_category": failure_category,
        "private_payload_sha256": private_hash,
        "reader_stage_disposition": reader_disposition,
        "judge_stage_disposition": judge_disposition,
        "graphiti_search_calls": retrieval.graphiti_search_calls,
        "neo4j_read_requests": retrieval.neo4j_read_requests,
        "quality_latency_ns": dict(latency),
        "construction_latency_includes_quality": False,
        "claim_scope": "DEVELOPMENT_DIAGNOSTIC_NOT_PAPER_SIGNIFICANCE",
    }
    public["payload_sha256"] = payload_sha256(public)
    return public


__all__ = [
    "QualityV1Inputs",
    "QualityV1QuestionResult",
    "run_quality_v1_question",
]
