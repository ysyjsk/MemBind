"""Bounded engineering-only AutoResearch for the MAB lane."""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .contracts import MABQA, MABContext, canonical_sha256

PROBE_CONTEXT_COUNT = 1
PROBE_QA_COUNT = 6
MAX_CANDIDATES = 3
MERGE_AUTHORITY = "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE"

_FIELDS = (
    "candidate_id",
    "parent_code_sha256",
    "code_sha256",
    "status",
    "pipeline_valid",
    "gold_blind_valid",
    "construction_count",
    "qa_count",
    "retrieval_valid_count",
    "reader_valid_count",
    "judge_valid_count",
    "qa_accuracy",
    "recall_at_1",
    "recall_at_3",
    "recall_at_5",
    "recall_at_10",
    "mrr",
    "ndcg_at_10",
    "failure_class",
    "description",
    "merge_authority",
    "payload_sha256",
)


def select_probe_qa(
    context: MABContext, *, count: int = PROBE_QA_COUNT
) -> tuple[MABQA, ...]:
    """Select questions by stable metadata buckets, never by observed answers."""

    if count <= 0:
        raise ValueError("probe count must be positive")
    buckets: dict[str, list[MABQA]] = defaultdict(list)
    for qa in context.qa_items:
        buckets[qa.question_type].append(qa)
    for values in buckets.values():
        values.sort(
            key=lambda qa: canonical_sha256([context.context_id, qa.qa_pair_id])
        )
    selected: list[MABQA] = []
    types = sorted(buckets)
    while len(selected) < min(count, len(context.qa_items)):
        progressed = False
        for name in types:
            if buckets[name]:
                selected.append(buckets[name].pop(0))
                progressed = True
                if len(selected) == count:
                    break
        if not progressed:
            break
    return tuple(selected)


def select_probe_contexts(
    contexts: Sequence[MABContext], *, count: int = PROBE_CONTEXT_COUNT
) -> tuple[MABContext, ...]:
    if count <= 0:
        raise ValueError("context count must be positive")
    return tuple(sorted(contexts, key=lambda c: canonical_sha256(c.context_id))[:count])


def _as_bool(value: Any) -> bool:
    return isinstance(value, bool) and value


class AutoResearchController:
    """Append-only controller with no automatic merge authority."""

    def __init__(self, ledger: str | Path) -> None:
        self.ledger = Path(ledger)
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        if not self.ledger.exists():
            with self.ledger.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=_FIELDS, delimiter="\t").writeheader()
        self.attempted = self._count_rows()

    def _count_rows(self) -> int:
        with self.ledger.open("r", newline="", encoding="utf-8") as handle:
            return max(0, sum(1 for _ in csv.DictReader(handle, delimiter="\t")))

    def _append(self, outcome: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            field: outcome.get(field) for field in _FIELDS if field != "payload_sha256"
        }
        body["merge_authority"] = MERGE_AUTHORITY
        body["payload_sha256"] = canonical_sha256(body)
        with self.ledger.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=_FIELDS, delimiter="\t", extrasaction="ignore"
            )
            writer.writerow({field: body.get(field, "") for field in _FIELDS})
            handle.flush()
            os.fsync(handle.fileno())
        self.attempted += 1
        return body

    def evaluate(
        self,
        candidates: Sequence[Mapping[str, Any]],
        *,
        evaluator: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        if len(candidates) > MAX_CANDIDATES:
            raise ValueError("AutoResearch candidate limit exceeded")
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            if self.attempted >= MAX_CANDIDATES:
                raise ValueError("AutoResearch candidate limit exceeded")
            candidate_id = str(candidate.get("candidate_id", f"c{self.attempted:02d}"))
            parent = str(candidate.get("parent_code_sha256", "UNBOUND"))
            code = str(candidate.get("code_sha256", "UNBOUND"))
            try:
                observed = dict(evaluator(candidate))
                observed.setdefault("status", "discard")
                observed.setdefault("pipeline_valid", False)
                observed.setdefault("gold_blind_valid", False)
                observed.setdefault("construction_count", 0)
                observed.setdefault("qa_count", 0)
                observed.setdefault("retrieval_valid_count", 0)
                observed.setdefault("reader_valid_count", 0)
                observed.setdefault("judge_valid_count", 0)
                observed.setdefault("failure_class", "UNKNOWN_INFRA_FAILURE")
                observed.setdefault("description", candidate.get("description", ""))
                hard_ok = (
                    _as_bool(observed.get("pipeline_valid"))
                    and _as_bool(observed.get("gold_blind_valid"))
                    and int(observed.get("construction_count", 0)) == 1
                    and int(observed.get("qa_count", 0)) > 0
                    and _as_bool(observed.get("semantics_unchanged", True))
                    and _as_bool(observed.get("diagnosed_engineering_fix", False))
                )
                observed["status"] = "keep" if hard_ok else "discard"
            # Candidate code is intentionally untrusted; every crash becomes a ledger row.
            except Exception:  # noqa: BLE001
                observed = {
                    "status": "crash",
                    "pipeline_valid": False,
                    "gold_blind_valid": False,
                    "construction_count": 0,
                    "qa_count": 0,
                    "retrieval_valid_count": 0,
                    "reader_valid_count": 0,
                    "judge_valid_count": 0,
                    "failure_class": "UNKNOWN_INFRA_FAILURE",
                    "description": candidate.get("description", ""),
                }
            observed.update(
                {
                    "candidate_id": candidate_id,
                    "parent_code_sha256": parent,
                    "code_sha256": code,
                }
            )
            results.append(self._append(observed))
        return tuple(results)


def freeze_identity(
    *,
    dataset_manifest_sha256: str,
    adapter_sha256: str,
    compatibility_sha256: str,
    runner_sha256: str,
    retrieval_config_sha256: str,
    reader_config_sha256: str,
    judge_config_sha256: str,
    question_inventory_sha256: str,
    selected_candidate_id: str,
) -> dict[str, Any]:
    """Create the immutable identity binding used after the probe."""

    body: dict[str, Any] = {
        "schema_version": "mab-quality-v2-final-qa.freeze.v1",
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "adapter_sha256": adapter_sha256,
        "compatibility_sha256": compatibility_sha256,
        "runner_sha256": runner_sha256,
        "retrieval_config_sha256": retrieval_config_sha256,
        "reader_config_sha256": reader_config_sha256,
        "judge_config_sha256": judge_config_sha256,
        "question_inventory_sha256": question_inventory_sha256,
        "selected_candidate_id": selected_candidate_id,
        "merge_authority": MERGE_AUTHORITY,
    }
    body["freeze_sha256"] = canonical_sha256(body)
    return body


__all__ = [
    "MAX_CANDIDATES",
    "MERGE_AUTHORITY",
    "PROBE_CONTEXT_COUNT",
    "PROBE_QA_COUNT",
    "AutoResearchController",
    "freeze_identity",
    "select_probe_contexts",
    "select_probe_qa",
]
