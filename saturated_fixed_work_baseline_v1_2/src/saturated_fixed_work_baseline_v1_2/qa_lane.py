"""Gold-blind, read-only, query-many lane over the eight formal seals."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .schedules import Method


PRIVATE_FIELDS = {"reference_answer", "gold_session_ids", "gold_evidence_quotes"}


class QALaneError(ValueError):
    """The L4 namespace, gold-blind, or graph immutability contract failed."""


@dataclass(frozen=True, slots=True)
class NamespaceSeal:
    method: str
    history_id: str
    namespace: str
    canonical_hash: str
    construction_call_ordinal: int

    def __post_init__(self) -> None:
        if self.method not in {method.value for method in Method}:
            raise QALaneError("NAMESPACE_METHOD_INVALID")
        if not self.history_id or not self.namespace:
            raise QALaneError("NAMESPACE_IDENTITY_INVALID")
        if len(self.canonical_hash) != 64:
            raise QALaneError("NAMESPACE_HASH_INVALID")
        if self.construction_call_ordinal < 1:
            raise QALaneError("CONSTRUCTION_ORDINAL_INVALID")


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def validate_l4_namespace_inventory(
    seals: Sequence[NamespaceSeal],
    *,
    expected_histories: Sequence[str],
    construction_calls: int,
) -> tuple[NamespaceSeal, ...]:
    if construction_calls != 8:
        raise QALaneError("QA_EXTRA_CONSTRUCTION_CALLS")
    selected = tuple(seals)
    expected = {
        (method.value, history)
        for method in Method
        for history in expected_histories
    }
    observed = {(seal.method, seal.history_id) for seal in selected}
    if len(selected) != 8 or observed != expected or len(observed) != len(selected):
        raise QALaneError("L4_NAMESPACE_COVERAGE_INVALID")
    if len({seal.namespace for seal in selected}) != 8:
        raise QALaneError("L4_NAMESPACE_NOT_UNIQUE")
    if {seal.construction_call_ordinal for seal in selected} != set(range(1, 9)):
        raise QALaneError("L4_CONSTRUCTION_ORDINAL_INVALID")
    return selected


def build_gold_blind_projection(question: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "question_id",
        "qa_pair_id",
        "history_id",
        "question_type",
        "question_date",
        "question",
    )
    projection = {field: question.get(field) for field in fields}
    if any(not isinstance(value, str) or not value for value in projection.values()):
        raise QALaneError("QA_PUBLIC_PROJECTION_INVALID")
    if set(projection) & PRIVATE_FIELDS:
        raise QALaneError("QA_GOLD_BLIND_VIOLATION")
    return projection


def _retrieval_metrics(retrieved: Sequence[str], gold: Sequence[str]) -> dict[str, float]:
    unique = list(dict.fromkeys(map(str, retrieved)))
    relevant = set(map(str, gold))
    if not relevant:
        raise QALaneError("QA_GOLD_SESSIONS_EMPTY")

    def recall(k: int) -> float:
        return len(set(unique[:k]) & relevant) / len(relevant)

    first_rank = next(
        (index for index, value in enumerate(unique[:10], start=1) if value in relevant),
        None,
    )
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, value in enumerate(unique[:10], start=1)
        if value in relevant
    )
    ideal = sum(
        1.0 / math.log2(index + 1)
        for index in range(1, min(len(relevant), 10) + 1)
    )
    return {
        "recall_at_1": recall(1),
        "recall_at_3": recall(3),
        "recall_at_5": recall(5),
        "recall_at_10": recall(10),
        "mrr": 0.0 if first_rank is None else 1.0 / first_rank,
        "ndcg_at_10": dcg / ideal,
    }


def run_history_qa(
    *,
    seal: NamespaceSeal,
    questions: Sequence[Mapping[str, Any]],
    snapshot_graph: Callable[[], Any],
    graph_write_attempt_count: Callable[[], int],
    retrieve: Callable[[dict[str, Any]], Mapping[str, Any]],
    reader: Callable[[dict[str, Any], dict[str, Any]], str],
    judge: Callable[[str, str], bool],
) -> list[dict[str, Any]]:
    selected = tuple(questions)
    if len(selected) != 4 or any(row.get("history_id") != seal.history_id for row in selected):
        raise QALaneError("QA_HISTORY_INVENTORY_INVALID")
    graph_before = _hash(snapshot_graph())
    writes_before = graph_write_attempt_count()
    rows: list[dict[str, Any]] = []
    for question in selected:
        public = build_gold_blind_projection(question)
        layer: str | None = None
        invalid_reason: str | None = None
        metrics = {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
            "ndcg_at_10": 0.0,
        }
        correct = False
        try:
            retrieval = dict(retrieve(dict(public)))
            retrieved = retrieval.get("retrieved_session_ids")
            if not isinstance(retrieved, list):
                raise ValueError("retrieved_session_ids_invalid")
            metrics = _retrieval_metrics(retrieved, question["gold_session_ids"])
        except Exception as error:
            layer = "retrieval"
            invalid_reason = type(error).__name__
            retrieval = {}
        if layer is None:
            try:
                answer = reader(dict(public), retrieval)
                if not isinstance(answer, str) or not answer:
                    raise ValueError("reader_answer_invalid")
            except Exception as error:
                layer = "reader"
                invalid_reason = type(error).__name__
                answer = ""
        if layer is None:
            try:
                verdict = judge(answer, str(question["reference_answer"]))
                if not isinstance(verdict, bool):
                    raise ValueError("judge_verdict_invalid")
                correct = verdict
            except Exception as error:
                layer = "judge"
                invalid_reason = type(error).__name__
        rows.append(
            {
                "method": seal.method,
                "history_id": seal.history_id,
                "namespace": seal.namespace,
                "question_id": question["question_id"],
                "qa_pair_id": question["qa_pair_id"],
                **metrics,
                "correct": correct if layer is None else False,
                "invalid": layer is not None,
                "invalid_reason": invalid_reason,
                "failure_layer": layer,
                "public_projection_sha256": _hash(public),
            }
        )
    graph_after = _hash(snapshot_graph())
    writes_after = graph_write_attempt_count()
    write_attempts = writes_after - writes_before
    if graph_before != graph_after or write_attempts != 0:
        raise QALaneError("QA_GRAPH_WRITE_OR_MUTATION")
    for row in rows:
        row["graph_hash_before"] = graph_before
        row["graph_hash_after"] = graph_after
        row["graph_write_attempts"] = write_attempts
    return rows


def paired_qa_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    indexed = {
        (str(row["method"]), str(row["qa_pair_id"])): row
        for row in rows
    }
    pair_ids = {str(row["qa_pair_id"]) for row in rows}
    if len(pair_ids) != 16 or len(indexed) != 32:
        raise QALaneError("QA_PAIR_COVERAGE_INVALID")
    summary: Counter[str] = Counter()
    invalid: Counter[str] = Counter()
    for pair_id in pair_ids:
        b0 = indexed[(Method.B0_NATIVE_SERIAL.value, pair_id)]
        b1 = indexed[(Method.B1_NAIVE_WHOLE_UPDATE_ASYNC.value, pair_id)]
        left = bool(b0.get("correct")) and not bool(b0.get("invalid"))
        right = bool(b1.get("correct")) and not bool(b1.get("invalid"))
        summary[
            "both_correct"
            if left and right
            else "b0_only_correct"
            if left
            else "b1_only_correct"
            if right
            else "both_wrong"
        ] += 1
        for row in (b0, b1):
            if row.get("invalid"):
                invalid[str(row.get("failure_layer") or "contract")] += 1
    return {
        **{key: summary[key] for key in ("both_correct", "b0_only_correct", "b1_only_correct", "both_wrong")},
        "invalid_by_layer": dict(sorted(invalid.items())),
    }


def cluster_bootstrap_accuracy(
    rows: Sequence[Mapping[str, Any]], *, seed: int, resamples: int
) -> dict[str, Any]:
    histories = tuple(dict.fromkeys(str(row["history_id"]) for row in rows))
    if len(histories) != 4 or resamples < 1:
        raise QALaneError("QA_CLUSTER_BOOTSTRAP_INPUT_INVALID")
    by_history = {
        history: [row for row in rows if str(row["history_id"]) == history]
        for history in histories
    }

    def accuracy(selected: Sequence[Mapping[str, Any]]) -> float:
        return sum(bool(row.get("correct")) and not bool(row.get("invalid")) for row in selected) / len(selected)

    generator = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        selected: list[Mapping[str, Any]] = []
        for _cluster in histories:
            selected.extend(by_history[generator.choice(histories)])
        samples.append(accuracy(selected))
    samples.sort()
    low = samples[max(0, math.ceil(0.025 * resamples) - 1)]
    high = samples[max(0, math.ceil(0.975 * resamples) - 1)]
    point = accuracy(rows)
    return {
        "n_clusters": 4,
        "resamples": resamples,
        "seed": seed,
        "point_estimate": point,
        "interval_low": min(low, point),
        "interval_high": max(high, point),
        "claim": "descriptive_only_no_significance",
    }


__all__ = [
    "NamespaceSeal",
    "QALaneError",
    "build_gold_blind_projection",
    "cluster_bootstrap_accuracy",
    "paired_qa_summary",
    "run_history_qa",
    "validate_l4_namespace_inventory",
]

