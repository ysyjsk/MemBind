"""Retrieval guardrail metrics."""

from __future__ import annotations

from typing import Iterable


def evidence_recall(retrieved_episode_ids: list[str], gold_episode_ids: Iterable[str], k: int) -> float:
    gold = {str(x) for x in gold_episode_ids}
    if not gold:
        return 1.0
    got = {str(x) for x in retrieved_episode_ids[:k]}
    return len(gold & got) / len(gold)


def episode_set_overlap(reference: Iterable[str], candidate: Iterable[str]) -> float:
    ref = {str(x) for x in reference}
    cand = {str(x) for x in candidate}
    if not ref and not cand:
        return 1.0
    return len(ref & cand) / len(ref | cand)


def rank_biased_overlap(reference: list[str], candidate: list[str], p: float = 0.9) -> float:
    if not reference and not candidate:
        return 1.0
    depth = max(len(reference), len(candidate))
    score = 0.0
    ref_seen: set[str] = set()
    cand_seen: set[str] = set()
    final_agreement = 0.0
    for d in range(1, depth + 1):
        if d <= len(reference):
            ref_seen.add(str(reference[d - 1]))
        if d <= len(candidate):
            cand_seen.add(str(candidate[d - 1]))
        agreement = len(ref_seen & cand_seen) / d
        final_agreement = agreement
        score += (p ** (d - 1)) * agreement
    return (1 - p) * score + (p**depth) * final_agreement


def retrieval_metrics(
    retrieved_episode_ids: list[str],
    gold_episode_ids: Iterable[str],
    reference_episode_ids: list[str] | None = None,
) -> dict[str, float]:
    metrics = {
        "evidence_recall_at_5": evidence_recall(retrieved_episode_ids, gold_episode_ids, 5),
        "evidence_recall_at_10": evidence_recall(retrieved_episode_ids, gold_episode_ids, 10),
    }
    if reference_episode_ids is not None:
        metrics["episode_set_overlap_with_m0"] = episode_set_overlap(reference_episode_ids[:10], retrieved_episode_ids[:10])
        metrics["rank_biased_overlap_with_m0"] = rank_biased_overlap(reference_episode_ids[:10], retrieved_episode_ids[:10])
    return metrics
