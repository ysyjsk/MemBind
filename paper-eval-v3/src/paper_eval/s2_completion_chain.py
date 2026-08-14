"""Bounded in-memory retrieval -> session Reader -> Judge chain for S2."""

from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .artifacts import payload_sha256
from .s2_live import S2LiveInputs
from .s2_session_policy import SessionRetrievalMetrics, evaluate_session_retrieval
from .s2_session_reader import OfficialSessionReader, SessionReaderResult, materialize_ranked_sessions


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompletionChainError(RuntimeError):
    """The bounded S2 completion attempt must stop without a quality claim."""


@dataclass(frozen=True)
class BoundedRetrievalOutcome:
    retrieved_session_ids: tuple[str, ...]
    graphiti_search_calls: int
    neo4j_read_requests: int
    construction_llm_requests: int
    embedding_requests: int
    cross_encoder_requests: int
    database_mutation_attempts: int
    database_mutations: int
    cleanup_calls: int
    retry_count: int


class CompletionJudge(Protocol):
    async def evaluate(
        self, *, hypothesis: str, inputs: S2LiveInputs
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class BoundedCompletionResult:
    metrics: SessionRetrievalMetrics
    qa_accuracy: float
    reference_sanity_status: str
    reader_evidence: Mapping[str, Any]
    judge_evidence: Mapping[str, Any]
    counters: Mapping[str, int]
    retrieved_session_ids: tuple[str, ...]
    gold_session_ids: tuple[str, ...]
    history_id: str
    namespace: str
    s3_ready: bool = False

    def to_artifact(self) -> dict[str, Any]:
        """Project only hashes, counters, and metrics into durable evidence."""

        return {
            "status": "SUCCESS",
            "history_id": self.history_id,
            "namespace_sha256": hashlib.sha256(
                self.namespace.encode("utf-8")
            ).hexdigest(),
            "retrieved_session_ids_sha256": payload_sha256(
                list(self.retrieved_session_ids)
            ),
            "gold_session_ids_sha256": payload_sha256(list(self.gold_session_ids)),
            "retrieved_session_count": self.metrics.retrieved_session_count,
            "gold_session_count": self.metrics.gold_session_count,
            "covered_gold_session_count": self.metrics.covered_gold_session_count,
            "gold_ranks": list(self.metrics.gold_ranks),
            "session_recall_any_at_10": self.metrics.session_recall_any_at_10,
            "session_recall_all_at_10": self.metrics.session_recall_all_at_10,
            "session_gold_coverage_fraction_at_10": (
                self.metrics.session_gold_coverage_fraction_at_10
            ),
            "coverage_fraction_is_official": False,
            "evidence_recall_at_10": self.metrics.evidence_recall_at_10,
            "qa_accuracy": self.qa_accuracy,
            "reference_sanity_status": self.reference_sanity_status,
            "reader": dict(self.reader_evidence),
            "judge": dict(self.judge_evidence),
            "counters": dict(self.counters),
            "s3_ready": self.s3_ready,
            "s3_authorized": False,
        }


async def _await(value: Awaitable[Any] | Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _emit_checkpoint(
    callback: Callable[[str, dict[str, Any]], Awaitable[Any] | Any] | None,
    *,
    stage: str,
    evidence: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        await _await(callback(stage, evidence))
    except Exception as error:
        raise CompletionChainError(
            f"checkpoint failed: {type(error).__name__}"
        ) from None


def _counter(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CompletionChainError(f"retrieval budget has invalid {field}")
    return value


def _retrieval_counters(outcome: BoundedRetrievalOutcome) -> dict[str, int]:
    names = (
        "graphiti_search_calls",
        "neo4j_read_requests",
        "construction_llm_requests",
        "embedding_requests",
        "cross_encoder_requests",
        "database_mutation_attempts",
        "database_mutations",
        "cleanup_calls",
        "retry_count",
    )
    values = {
        name: _counter(getattr(outcome, name), field=name) for name in names
    }
    if values["graphiti_search_calls"] != 1 or values["neo4j_read_requests"] < 1:
        raise CompletionChainError("retrieval budget requires one search and reads")
    forbidden = names[2:]
    if any(values[name] for name in forbidden):
        raise CompletionChainError("retrieval budget contains a forbidden call")
    return values


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _validate_judge_evidence(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompletionChainError("Judge output is invalid")
    evidence = dict(value)
    label = evidence.get("label")
    parse_status = evidence.get("parse_status")
    if (
        evidence.get("status") != "SUCCESS"
        or type(label) is not bool
        or parse_status not in {"YES", "NO"}
        or (label is True and parse_status != "YES")
        or (label is False and parse_status != "NO")
        or evidence.get("retry_count") != 0
        or evidence.get("error_class") is not None
        or not all(
            _valid_sha(evidence.get(field))
            for field in (
                "prompt_sha256",
                "config_sha256",
                "output_sha256",
            )
        )
    ):
        raise CompletionChainError("Judge output is invalid or incomplete")
    allowed = {
        "status",
        "label",
        "model",
        "prompt_sha256",
        "config_sha256",
        "output_sha256",
        "output_character_count",
        "output_byte_count",
        "parse_status",
        "retry_count",
        "error_class",
    }
    if set(evidence) != allowed:
        raise CompletionChainError("Judge output contains an unsafe field")
    return evidence


def _validate_input_alignment(
    inputs: S2LiveInputs, record: Mapping[str, Any]
) -> tuple[str, ...]:
    if not isinstance(inputs, S2LiveInputs) or not isinstance(record, Mapping):
        raise CompletionChainError("S2 completion inputs are invalid")
    expected = {
        "question_id": inputs.history_id,
        "question_type": inputs.question_type,
        "question": inputs.question,
        "question_date": inputs.question_date,
        "answer": inputs.reference_answer,
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise CompletionChainError("S2 completion dataset identity drift")
    if inputs.question_type != "knowledge-update":
        raise CompletionChainError("S2 completion question type drift")
    corpus = record.get("haystack_session_ids")
    gold = record.get("answer_session_ids")
    if (
        not isinstance(corpus, list)
        or not isinstance(gold, list)
        or tuple(str(value) for value in gold) != inputs.answer_session_ids
    ):
        raise CompletionChainError("S2 completion gold identity drift")
    return tuple(str(value) for value in corpus)


async def execute_bounded_completion_chain(
    *,
    inputs: S2LiveInputs,
    dataset_record: Mapping[str, Any],
    retrieve: Callable[..., Awaitable[BoundedRetrievalOutcome] | BoundedRetrievalOutcome],
    reader: OfficialSessionReader,
    judge: CompletionJudge,
    on_checkpoint: Callable[
        [str, dict[str, Any]], Awaitable[Any] | Any
    ]
    | None = None,
) -> BoundedCompletionResult:
    """Execute exactly one preconfigured chain; selection never receives gold IDs."""

    allowed_sessions = _validate_input_alignment(inputs, dataset_record)
    try:
        outcome = await _await(
            retrieve(question=inputs.question, namespace=inputs.namespace)
        )
    except Exception as error:
        if isinstance(error, CompletionChainError):
            raise
        raise CompletionChainError(
            f"retrieval failed: {type(error).__name__}"
        ) from None
    if not isinstance(outcome, BoundedRetrievalOutcome):
        raise CompletionChainError("retrieval returned an invalid outcome")
    counters = _retrieval_counters(outcome)
    metrics = evaluate_session_retrieval(
        retrieved_session_ids=outcome.retrieved_session_ids,
        gold_session_ids=inputs.answer_session_ids,
        top_k=10,
        allowed_session_ids=allowed_sessions,
    )
    if metrics.retrieved_session_count != 10:
        raise CompletionChainError("retrieval did not return ten unique sessions")
    await _emit_checkpoint(
        on_checkpoint,
        stage="retrieval_complete",
        evidence={
            "status": "SUCCESS",
            "retrieved_session_ids_sha256": payload_sha256(
                list(outcome.retrieved_session_ids)
            ),
            "gold_session_ids_sha256": payload_sha256(
                list(inputs.answer_session_ids)
            ),
            "retrieved_session_count": metrics.retrieved_session_count,
            "gold_session_count": metrics.gold_session_count,
            "covered_gold_session_count": metrics.covered_gold_session_count,
            "gold_ranks": list(metrics.gold_ranks),
            "session_recall_any_at_10": metrics.session_recall_any_at_10,
            "session_recall_all_at_10": metrics.session_recall_all_at_10,
            "session_gold_coverage_fraction_at_10": (
                metrics.session_gold_coverage_fraction_at_10
            ),
            "counters": dict(counters),
        },
    )
    sessions = materialize_ranked_sessions(
        record=dataset_record,
        ranked_session_ids=outcome.retrieved_session_ids,
        top_k=10,
    )
    try:
        reader_result = await reader.answer(
            sessions,
            question_date=inputs.question_date,
            question=inputs.question,
        )
    except Exception as error:
        raise CompletionChainError(f"Reader failed: {type(error).__name__}") from None
    if not isinstance(reader_result, SessionReaderResult):
        raise CompletionChainError("Reader returned an invalid result")
    counters["reader_requests"] = 1
    await _emit_checkpoint(
        on_checkpoint,
        stage="reader_complete",
        evidence=reader_result.to_artifact(),
    )
    try:
        judge_value = await judge.evaluate(
            hypothesis=reader_result.answer,
            inputs=inputs,
        )
    except Exception as error:
        raise CompletionChainError(f"Judge failed: {type(error).__name__}") from None
    judge_evidence = _validate_judge_evidence(judge_value)
    counters["judge_requests"] = 1
    await _emit_checkpoint(
        on_checkpoint,
        stage="judge_complete",
        evidence=judge_evidence,
    )
    qa_accuracy = 1.0 if judge_evidence["label"] else 0.0
    sanity = (
        "PASS"
        if metrics.evidence_recall_at_10 == 1.0 and qa_accuracy == 1.0
        else "REVIEW_REQUIRED"
    )
    return BoundedCompletionResult(
        metrics=metrics,
        qa_accuracy=qa_accuracy,
        reference_sanity_status=sanity,
        reader_evidence=reader_result.to_artifact(),
        judge_evidence=judge_evidence,
        counters=counters,
        retrieved_session_ids=outcome.retrieved_session_ids,
        gold_session_ids=inputs.answer_session_ids,
        history_id=inputs.history_id,
        namespace=inputs.namespace,
    )
