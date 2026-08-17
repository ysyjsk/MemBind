"""One-question graph-derived QA overlay with split audit artifacts.

Retrieval and Reader calls are gold-blind.  The reference answer reaches only
the Judge adapter.  Public output contains hashes and metrics; a separate
private payload retains the ranked evidence and raw Reader material needed for
local error analysis.  All overlay latency is explicitly outside construction
makespan.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Any

from .artifacts import payload_sha256
from .graphiti_longmemeval_quality import (
    GraphQualityEvidence,
    retrieve_graph_quality_evidence,
)
from .graph_quality_stages import GraphQualityStageStore
from .quality_terminal_semantics import classify_judge_artifact
from .temporal_fact_reader import (
    TemporalFactReaderResult,
    render_temporal_fact_reader_prompt,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = ("U0", "A0", "P(C=2)")


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is invalid")
    return value


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


@dataclass(frozen=True)
class GraphQualityInputs:
    """Private one-question inputs; only hashes are projected publicly."""

    overlay_run_id: str
    method: str
    history_id: str
    namespace: str
    question: str
    question_date: str
    question_type: str
    reference_answer: str
    answer_session_ids: tuple[str, ...]
    construction_result_sha256: str

    def __post_init__(self) -> None:
        for field in (
            "overlay_run_id",
            "history_id",
            "namespace",
            "question",
            "question_date",
            "question_type",
            "reference_answer",
        ):
            _text(getattr(self, field), field=field)
        if self.method not in _METHODS:
            raise ValueError("method is invalid")
        if (
            not isinstance(self.answer_session_ids, tuple)
            or not self.answer_session_ids
            or len(set(self.answer_session_ids)) != len(self.answer_session_ids)
            or any(not isinstance(value, str) or not value for value in self.answer_session_ids)
        ):
            raise ValueError("answer_session_ids are invalid")
        _sha(self.construction_result_sha256, field="construction_result_sha256")


@dataclass(frozen=True)
class GraphQualityQuestionResult:
    """Bound public/private artifacts for one evaluated graph."""

    public_artifact: dict[str, Any]
    private_artifact: dict[str, Any]


def _component_hash(component: object, *, field: str) -> str:
    return _sha(getattr(component, "config_sha256", None), field=field)


def _question_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def run_graph_quality_question(
    *,
    inputs: GraphQualityInputs,
    graph: Any,
    episode_uuid_to_session_id: Mapping[str, str],
    reader: Any,
    judge: Any,
    retrieve: Callable[..., Awaitable[GraphQualityEvidence]] = (
        retrieve_graph_quality_evidence
    ),
    stage_store: GraphQualityStageStore | None = None,
    runtime_identity_sha256: str | None = None,
) -> GraphQualityQuestionResult:
    """Run one question, sealing successful model stages before advancing."""

    if not isinstance(inputs, GraphQualityInputs):
        raise ValueError("graph quality inputs are invalid")
    started = time.monotonic_ns()
    evidence = await retrieve(
        graph=graph,
        query=inputs.question,
        namespace=inputs.namespace,
        episode_uuid_to_session_id=episode_uuid_to_session_id,
    )
    retrieval_done = time.monotonic_ns()
    if not isinstance(evidence, GraphQualityEvidence):
        raise ValueError("graph quality retrieval returned an invalid result")
    evidence_payload = {
        "facts": [asdict(value) for value in evidence.facts],
        "entities": [asdict(value) for value in evidence.entities],
    }
    exact_reader_prompt = render_temporal_fact_reader_prompt(
        evidence.facts,
        evidence.entities,
        question_date=inputs.question_date,
        question=inputs.question,
    )
    reader_stage_sha256: str | None = None
    reader_stage_disposition = "NOT_CHECKPOINTED"
    reader_binding: dict[str, Any] | None = None
    if stage_store is not None:
        runtime_sha = _sha(
            runtime_identity_sha256,
            field="runtime_identity_sha256",
        )
        reader_binding = {
            "overlay_run_id": inputs.overlay_run_id,
            "method": inputs.method,
            "history_id": inputs.history_id,
            "namespace_sha256": _question_hash(inputs.namespace),
            "construction_result_sha256": inputs.construction_result_sha256,
            "runtime_identity_sha256": runtime_sha,
            "retrieval_config_sha256": _sha(
                evidence.search_config_sha256,
                field="retrieval_config_sha256",
            ),
            "evidence_sha256": payload_sha256(evidence_payload),
            "reader_config_sha256": _component_hash(
                reader, field="reader_config_sha256"
            ),
            "question_sha256": _question_hash(inputs.question),
            "question_date_sha256": _question_hash(inputs.question_date),
            "reader_prompt_sha256": _question_hash(exact_reader_prompt),
        }
        restored_reader = stage_store.load_reader(reader_binding)
    else:
        restored_reader = None
    if restored_reader is not None:
        reader_result, reader_stage_sha256 = restored_reader
        reader_stage_disposition = "RESTORED"
    else:
        reader_result = await reader.answer(
            evidence.facts,
            evidence.entities,
            question_date=inputs.question_date,
            question=inputs.question,
        )
        if stage_store is not None:
            assert reader_binding is not None
            reader_stage_sha256 = stage_store.persist_reader(
                reader_binding,
                reader_result,
            )
            reader_stage_disposition = "EXECUTED_AND_SEALED"
    reader_done = time.monotonic_ns()
    if not isinstance(reader_result, TemporalFactReaderResult):
        raise ValueError("graph quality Reader returned an invalid result")
    if reader_result.prompt_for_test != exact_reader_prompt:
        raise ValueError("graph quality Reader prompt identity drift")
    judge_stage_sha256: str | None = None
    judge_stage_disposition = "NOT_CHECKPOINTED"
    if stage_store is not None:
        assert reader_binding is not None
        assert reader_stage_sha256 is not None
        judge_inputs = SimpleNamespace(
            run_id=inputs.overlay_run_id,
            history_id=inputs.history_id,
            question_type=inputs.question_type,
            question=inputs.question,
            reference_answer=inputs.reference_answer,
        )
        exact_prompt = getattr(judge, "exact_prompt_sha256", None)
        if not callable(exact_prompt):
            raise ValueError("graph quality Judge prompt identity is unavailable")
        judge_prompt_sha256 = _sha(
            exact_prompt(
                hypothesis=reader_result.answer,
                inputs=judge_inputs,
            ),
            field="judge_prompt_sha256",
        )
        judge_binding = {
            **reader_binding,
            "reader_stage_sha256": reader_stage_sha256,
            "judge_config_sha256": _component_hash(
                judge, field="judge_config_sha256"
            ),
            "question_type_sha256": _question_hash(inputs.question_type),
            "reference_answer_sha256": _question_hash(inputs.reference_answer),
            "reader_answer_sha256": _question_hash(reader_result.answer),
            "judge_prompt_sha256": judge_prompt_sha256,
        }
        restored_judge = stage_store.load_judge(judge_binding)
    else:
        judge_binding = None
        judge_inputs = SimpleNamespace(
            run_id=inputs.overlay_run_id,
            history_id=inputs.history_id,
            question_type=inputs.question_type,
            question=inputs.question,
            reference_answer=inputs.reference_answer,
        )
        restored_judge = None
    if restored_judge is not None:
        judge_result, judge_stage_sha256 = restored_judge
        judge_stage_disposition = "RESTORED"
    else:
        judge_result = dict(
            await judge.evaluate(
                hypothesis=reader_result.answer,
                inputs=judge_inputs,
            )
        )
        if stage_store is not None:
            assert judge_binding is not None
            judge_stage_sha256 = stage_store.persist_judge(
                judge_binding,
                judge_result,
            )
            judge_stage_disposition = "EXECUTED_AND_SEALED"
    judge_done = time.monotonic_ns()
    outcome = classify_judge_artifact(judge_result)

    top_ten_sources = {
        session_id
        for fact in evidence.facts[:10]
        for session_id in fact.source_session_ids
    }
    covered = len(top_ten_sources.intersection(inputs.answer_session_ids))
    coverage = covered / len(inputs.answer_session_ids)
    quality_identity = {
        "retrieval_config_sha256": _sha(
            evidence.search_config_sha256,
            field="retrieval_config_sha256",
        ),
        "reader_config_sha256": _component_hash(
            reader, field="reader_config_sha256"
        ),
        "judge_config_sha256": _component_hash(
            judge, field="judge_config_sha256"
        ),
    }

    private_artifact: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-private.v1",
        "overlay_run_id": inputs.overlay_run_id,
        "method": inputs.method,
        "history_id": inputs.history_id,
        "namespace": inputs.namespace,
        "question": inputs.question,
        "question_date": inputs.question_date,
        "question_type": inputs.question_type,
        "reference_answer": inputs.reference_answer,
        "answer_session_ids": list(inputs.answer_session_ids),
        "facts": evidence_payload["facts"],
        "entities": evidence_payload["entities"],
        "reader_prompt": reader_result.prompt_for_test,
        "reader_answer": reader_result.answer,
        "reader_public_artifact": reader_result.to_public_artifact(),
        "judge_result": judge_result,
        "reader_stage_sha256": reader_stage_sha256,
        "judge_stage_sha256": judge_stage_sha256,
    }
    public_artifact: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.graph-quality-public.v1",
        "overlay_run_id": inputs.overlay_run_id,
        "method": inputs.method,
        "history_id": inputs.history_id,
        "namespace_sha256": _question_hash(inputs.namespace),
        "question_sha256": _question_hash(inputs.question),
        "construction_result_sha256": inputs.construction_result_sha256,
        "quality_identity": quality_identity,
        "fact_count": len(evidence.facts),
        "entity_count": len(evidence.entities),
        "gold_session_count": len(inputs.answer_session_ids),
        "covered_gold_session_count_at_10": covered,
        "edge_attributed_source_coverage_at_10": coverage,
        "edge_coverage_is_official_session_recall": False,
        "qa_accuracy": 1.0 if outcome.correct is True else (
            0.0 if outcome.correct is False else None
        ),
        "judge_status": outcome.status,
        "judge_valid_denominator": 1 if outcome.included else 0,
        "headline_eligible": outcome.included,
        "reader": reader_result.to_public_artifact(),
        "judge_output_sha256": judge_result.get("output_sha256"),
        "reader_stage_disposition": reader_stage_disposition,
        "reader_stage_sha256": reader_stage_sha256,
        "judge_stage_disposition": judge_stage_disposition,
        "judge_stage_sha256": judge_stage_sha256,
        "graphiti_search_calls": evidence.graphiti_search_calls,
        "neo4j_read_requests": evidence.neo4j_read_requests,
        "quality_latency_ns": {
            "retrieval": retrieval_done - started,
            "reader": reader_done - retrieval_done,
            "judge": judge_done - reader_done,
            "total": judge_done - started,
        },
        "quality_latency_excluded_from_construction": True,
        "private_artifact_sha256": payload_sha256(private_artifact),
    }
    public_artifact["payload_sha256"] = payload_sha256(public_artifact)
    return GraphQualityQuestionResult(
        public_artifact=public_artifact,
        private_artifact=private_artifact,
    )


def verify_common_graph_quality_identity(
    by_method: Mapping[str, Mapping[str, str]],
) -> dict[str, str]:
    """Require U0/A0/P(C=2) to use one exact quality configuration."""

    if set(by_method) != set(_METHODS):
        raise ValueError("graph quality method inventory is invalid")
    identities = {method: dict(by_method[method]) for method in _METHODS}
    first = identities["U0"]
    required = {
        "retrieval_config_sha256",
        "reader_config_sha256",
        "judge_config_sha256",
    }
    if set(first) != required or any(
        _SHA256.fullmatch(str(value)) is None for value in first.values()
    ):
        raise ValueError("graph quality identity is invalid")
    if any(identities[method] != first for method in _METHODS[1:]):
        raise ValueError("graph quality identity drift")
    return first


__all__ = [
    "GraphQualityInputs",
    "GraphQualityQuestionResult",
    "run_graph_quality_question",
    "verify_common_graph_quality_identity",
]
