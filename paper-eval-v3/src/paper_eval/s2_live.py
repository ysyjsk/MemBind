"""One-history S2 retrieval, Reader, and Judge numeric sanity chain.

This controller intentionally has no construction or namespace cleanup path.
It consumes the completed S1 namespace exactly once and persists only
sanitized metrics and content hashes.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope
from .s2_reader import RetrievedFact
from .s2_retrieval_contract import (
    EDGE_SURFACE_CONTRACT,
    edge_attributed_source_session_coverage,
)


@dataclass(frozen=True)
class S2LiveInputs:
    run_id: str
    history_id: str
    namespace: str
    question: str
    question_date: str
    question_type: str
    reference_answer: str
    answer_session_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in (
            "run_id",
            "history_id",
            "namespace",
            "question",
            "question_date",
            "question_type",
            "reference_answer",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be nonempty")
        if not self.namespace.startswith("pev3-s1-"):
            raise ValueError("S2 may only consume a completed S1 namespace")
        if not isinstance(self.answer_session_ids, tuple) or not self.answer_session_ids:
            raise ValueError("answer_session_ids must be a nonempty tuple")
        if any(not isinstance(value, str) or not value for value in self.answer_session_ids):
            raise ValueError("answer_session_ids contain an invalid value")


class Reader(Protocol):
    async def answer(
        self,
        facts: Sequence[RetrievedFact],
        *,
        question_date: str,
        question: str,
    ) -> Any: ...


class Judge(Protocol):
    async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class S2LiveQualification:
    edge_attributed_source_session_coverage_at_10: float
    qa_accuracy: float
    edge_result_count: int
    retrieved_source_session_ids: tuple[str, ...]
    reader_status: str
    reader_evidence: Mapping[str, Any]
    judge_status: str
    judge_evidence: Mapping[str, Any]


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if values is None:
        raise RuntimeError("S2 episode mapping query returned an invalid shape")
    return [value if isinstance(value, dict) else dict(value) for value in values]


def _value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, Mapping) else getattr(item, name, None)


def _plain_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "iso_format"):
        return str(value.iso_format())
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


async def _episode_uuid_map(
    graph: Any,
    namespace: str,
    episodes: Sequence[Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    result = await graph.driver.execute_query(
        """
        MATCH (ep:Episodic)
        WHERE ep.group_id = $group_id
        RETURN ep.uuid AS uuid, ep.name AS name, ep.valid_at AS valid_at
        """,
        params={"group_id": namespace},
    )
    expected = {str(getattr(item, "name")): item for item in episodes}
    mapped: dict[str, Any] = {}
    valid_at_by_uuid: dict[str, str] = {}
    for row in _records(result):
        episode = expected.get(str(row.get("name") or ""))
        uuid = str(row.get("uuid") or "")
        if episode is not None and uuid:
            mapped[uuid] = episode
            valid_at = _plain_time(row.get("valid_at"))
            if valid_at is not None:
                valid_at_by_uuid[uuid] = valid_at
    if len(mapped) != len(episodes):
        raise RuntimeError("S2 namespace episode mapping coverage mismatch")
    return mapped, valid_at_by_uuid


async def run_s2_numeric_sanity(
    *,
    inputs: S2LiveInputs,
    graph: Any,
    episodes: Sequence[Any],
    reader: Reader,
    judge: Judge,
) -> S2LiveQualification:
    """Run exactly one retrieval/Reader/Judge chain over completed S1 state."""

    try:
        by_uuid, valid_at_by_uuid = await _episode_uuid_map(
            graph, inputs.namespace, episodes
        )
        results = list(
            islice(
                await graph.search(
                    inputs.question,
                    group_ids=[inputs.namespace],
                    num_results=10,
                ),
                10,
            )
        )
        facts: list[RetrievedFact] = []
        ranked_edge_source_session_ids: list[tuple[str, ...]] = []
        for rank, result in enumerate(results, start=1):
            fact = _value(result, "fact")
            if not isinstance(fact, str) or not fact.strip():
                raise RuntimeError("S2 retrieval returned an invalid fact")
            mapped_episodes = [
                by_uuid[str(uuid)]
                for uuid in (_value(result, "episodes") or [])
                if str(uuid) in by_uuid
            ]
            source_ids = tuple(
                dict.fromkeys(str(getattr(episode, "session_id")) for episode in mapped_episodes)
            )
            ranked_edge_source_session_ids.append(source_ids)
            reference_time = _plain_time(_value(result, "reference_time"))
            if reference_time is None and mapped_episodes:
                reference_time = _plain_time(
                    getattr(mapped_episodes[0], "reference_time", None)
                )
            if reference_time is None:
                reference_time = next(
                    (
                        valid_at_by_uuid[str(uuid)]
                        for uuid in (_value(result, "episodes") or [])
                        if str(uuid) in valid_at_by_uuid
                    ),
                    None,
                )
            if reference_time is None:
                raise RuntimeError("S2 retrieval fact lacks reference time")
            facts.append(
                RetrievedFact(
                    rank=rank,
                    fact=fact.strip(),
                    reference_time=reference_time,
                    source_session_ids=source_ids,
                )
            )
        if not facts:
            raise RuntimeError("S2 retrieval returned no facts")

        edge_coverage, retrieved_source_session_ids = (
            edge_attributed_source_session_coverage(
                ranked_edge_source_session_ids=ranked_edge_source_session_ids,
                gold_session_ids=inputs.answer_session_ids,
                top_k=10,
            )
        )
        reader_result = await reader.answer(
            facts,
            question_date=inputs.question_date,
            question=inputs.question,
        )
        hypothesis = getattr(reader_result, "answer", None)
        if not isinstance(hypothesis, str) or not hypothesis:
            raise RuntimeError("S2 Reader returned no answer")
        judge_evidence = dict(
            await judge.evaluate(hypothesis=hypothesis, inputs=inputs)
        )
        judge_status = judge_evidence.get("status")
        if judge_status not in {"SUCCESS", "INVALID_OUTPUT"}:
            raise RuntimeError("S2 Judge service did not produce a terminal label")
        label = judge_evidence.get("label")
        if type(label) is not bool:
            raise RuntimeError("S2 Judge terminal label is invalid")
        reader_evidence = dict(reader_result.to_artifact())
        if reader_evidence.get("status") != "SUCCESS":
            raise RuntimeError("S2 Reader artifact status is invalid")
        return S2LiveQualification(
            edge_attributed_source_session_coverage_at_10=edge_coverage,
            qa_accuracy=1.0 if label else 0.0,
            edge_result_count=len(results),
            retrieved_source_session_ids=retrieved_source_session_ids,
            reader_status="SUCCESS",
            reader_evidence=reader_evidence,
            judge_status=str(judge_status),
            judge_evidence=judge_evidence,
        )
    finally:
        close = getattr(graph, "close", None)
        if callable(close):
            value = close()
            if inspect.isawaitable(value):
                await value


def finalize_s2_qualification(
    output_path: Path,
    *,
    result: S2LiveQualification,
    inputs: S2LiveInputs,
    git_commit: str,
    qualification_evidence_sha256: str,
    adapter_identity_sha256: str,
) -> dict[str, Any]:
    """Write a content-free, sealed S2 numeric-sanity artifact."""

    near_zero = (
        result.edge_attributed_source_session_coverage_at_10 == 0
        or result.qa_accuracy == 0
    )
    payload = {
        "stage": "S2",
        "method": "U0",
        "history_id": inputs.history_id,
        "namespace": inputs.namespace,
        "qualification_evidence_sha256": qualification_evidence_sha256,
        "adapter_identity_sha256": adapter_identity_sha256,
        "retrieval_surface": EDGE_SURFACE_CONTRACT.retrieval_surface,
        "retrieval_unit": EDGE_SURFACE_CONTRACT.result_unit,
        "top_k": 10,
        "top_k_unit": EDGE_SURFACE_CONTRACT.top_k_unit,
        "edge_result_count": result.edge_result_count,
        "retrieved_source_session_ids": list(result.retrieved_source_session_ids),
        "gold_session_count": len(inputs.answer_session_ids),
        "edge_attributed_source_session_coverage_at_10": (
            result.edge_attributed_source_session_coverage_at_10
        ),
        "official_longmemeval_session_recall_at_10": None,
        "qa_accuracy": result.qa_accuracy,
        "reader_status": result.reader_status,
        "reader_evidence": dict(result.reader_evidence),
        "judge_status": result.judge_status,
        "judge_evidence": dict(result.judge_evidence),
        "numeric_alignment": "NATIVE_EDGE_SURFACE_NOT_LONGMEMEVAL_SESSION_RETRIEVAL",
        "near_zero_stop_triggered": near_zero,
        "status": "PIPELINE_ANOMALY_NEAR_ZERO" if near_zero else "PASS",
    }
    envelope = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=inputs.run_id,
    )
    atomic_write_json(output_path, envelope)
    return envelope
