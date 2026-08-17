"""Gold-blind retrieval metrics and ContextPack for Quality Evaluation v1.

LongMemEval supplies gold source sessions but no gold fact annotations. Session
metrics are therefore formal ranking metrics, while edge metrics are explicitly
named provenance proxies. Context selection never reads answer labels.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from rank_bm25 import BM25Okapi


SESSION_CUTOFFS = (1, 3, 5, 10)
EDGE_CUTOFFS = (1, 3, 5, 10, 20)
MAX_LOCAL_ROUNDS = 10
_SPACE = re.compile(r"\s+")

# This is an adaptation of public, pinned retrieval/context code rather than a
# development-set heuristic.  LongMemEval supplies the USER-turn BM25 and
# USER+next-turn expansion behavior.  Zep supplies the graph-fact/validity
# representation and Top-20 graph retrieval convention.  Graphiti supplies
# the upstream candidate sessions and fact edges.
CONTEXT_POLICY = {
    "schema_version": "membind.paper-eval-v3.quality-context-v1",
    "graphiti_candidate_session_limit": 20,
    "graphiti_fact_limit": 20,
    "local_flat_user_turn_limit": MAX_LOCAL_ROUNDS,
    "local_ranker": "rank_bm25.BM25Okapi",
    "local_tokenization": "str.split(' ')",
    "tie_policy": "STABLE_GRAPHITI_SESSION_THEN_SOURCE_TURN_ORDER",
    "turn_expansion": "RETRIEVED_USER_TURN_PLUS_IMMEDIATELY_FOLLOWING_TURN",
    "post_selection_order": "CHRONOLOGICAL",
    "gold_inputs_allowed_during_selection": False,
    "longmemeval": {
        "repository": "https://github.com/xiaowu0162/LongMemEval",
        "commit": "9e0b455f4ef0e2ab8f2e582289761153549043fc",
        "generation_source": "src/generation/run_generation.py",
        "generation_source_sha256": "4f1eb3c69d7ad40f04065b9c0bc86f6582441018fc6ff751d162d66c95baf672",
        "retrieval_source": "src/retrieval/run_retrieval.py",
        "retrieval_source_sha256": "efd7fc5969a904717741fadca3c7dc73611ddbb2aaf3ef33117ebb6943b3e346",
    },
    "rank_bm25": {
        "distribution": "rank-bm25",
        "version": "0.2.2",
        "upstream_repository": "https://github.com/dorianbrown/rank_bm25",
        "upstream_commit": "47aa3ddf8dc1ebeb7ef4e65f2b4536af44594099",
        "upstream_source_sha256": "0de6c46a8d5a9ad63ff7034012cda1b296a12b7000fdee4479101375fdf62968",
        "installed_source_sha256": "2f28cc795415c01e9f3db5a8ed019774f9cba747b272c9c304271589b8081ac6",
    },
    "zep": {
        "repository": "https://github.com/getzep/zep",
        "commit": "be263ee23085410185835e0d8508b47fd35e9abb",
        "source": "benchmarks/longmemeval/zep_longmem_eval.py",
        "source_sha256": "785eacdfd9a388ea00f636074579f7409e04a48d0c1bf5685022f3830a6b72d4",
    },
}
CONTEXT_POLICY_SHA256 = hashlib.sha256(
    json.dumps(
        CONTEXT_POLICY,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


@dataclass(frozen=True)
class RetrievedEpisode:
    retrieval_rank: int
    episode_uuid: str
    session_id: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.retrieval_rank, bool)
            or not isinstance(self.retrieval_rank, int)
            or self.retrieval_rank < 1
            or not isinstance(self.episode_uuid, str)
            or not self.episode_uuid
            or not isinstance(self.session_id, str)
            or not self.session_id
        ):
            raise ValueError("Quality v1 retrieved episode is invalid")


@dataclass(frozen=True)
class RetrievedFact:
    retrieval_rank: int
    edge_uuid: str
    source_node_uuid: str
    target_node_uuid: str
    relation_name: str
    fact: str
    source_session_ids: tuple[str, ...]
    valid_at: str | None
    invalid_at: str | None
    expired_at: str | None
    reference_time: str | None

    def __post_init__(self) -> None:
        texts = (
            self.edge_uuid,
            self.source_node_uuid,
            self.target_node_uuid,
            self.relation_name,
            self.fact,
        )
        if (
            isinstance(self.retrieval_rank, bool)
            or not isinstance(self.retrieval_rank, int)
            or self.retrieval_rank < 1
            or any(not isinstance(value, str) or not value for value in texts)
            or not isinstance(self.source_session_ids, tuple)
            or not self.source_session_ids
            or len(set(self.source_session_ids)) != len(self.source_session_ids)
            or any(not isinstance(value, str) or not value for value in self.source_session_ids)
        ):
            raise ValueError("Quality v1 retrieved fact is invalid")
        for value in (self.valid_at, self.invalid_at, self.expired_at, self.reference_time):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("Quality v1 temporal field is invalid")


@dataclass(frozen=True)
class ContextPack:
    context_json: str
    evidence_count: int
    fact_count: int
    session_candidate_count: int
    local_round_count: int


def _unique_nonempty(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"Quality v1 {field} is invalid")
    result = tuple(values)
    if (
        not result
        or any(not isinstance(value, str) or not value for value in result)
        or len(set(result)) != len(result)
    ):
        raise ValueError(f"Quality v1 {field} is invalid")
    return result


def session_ranking_metrics(
    ranked_session_ids: Sequence[str], gold_session_ids: Sequence[str]
) -> dict[str, Any]:
    """Compute binary-relevance session metrics without score saturation."""

    ranked = _unique_nonempty(ranked_session_ids, field="ranked sessions")
    gold = _unique_nonempty(gold_session_ids, field="gold sessions")
    gold_set = set(gold)
    ranks = [index for index, value in enumerate(ranked, start=1) if value in gold_set]
    result: dict[str, Any] = {
        f"recall_at_{cutoff}": len(
            gold_set.intersection(ranked[:cutoff])
        )
        / len(gold)
        for cutoff in SESSION_CUTOFFS
    }
    result["mrr"] = 1.0 / min(ranks) if ranks else 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks if rank <= 10)
    ideal_hits = min(len(gold), 10)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    result["ndcg_at_10"] = dcg / ideal if ideal else 0.0
    result["gold_ranks"] = ranks
    result["relevance_unit"] = "LONGMEMEVAL_GOLD_SESSION"
    return result


def edge_provenance_metrics(
    facts: Sequence[RetrievedFact], gold_session_ids: Sequence[str]
) -> dict[str, Any]:
    """Measure edge provenance against gold sessions, not semantic fact gold."""

    values = tuple(facts)
    if any(not isinstance(value, RetrievedFact) for value in values):
        raise ValueError("Quality v1 facts are invalid")
    if [value.retrieval_rank for value in values] != list(range(1, len(values) + 1)):
        raise ValueError("Quality v1 fact ranks are invalid")
    gold = _unique_nonempty(gold_session_ids, field="gold sessions")
    gold_set = set(gold)
    result: dict[str, Any] = {
        "metric_scope": "PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL",
        "gold_fact_labels_available": False,
    }
    for cutoff in EDGE_CUTOFFS:
        selected = values[:cutoff]
        hits = [value for value in selected if gold_set.intersection(value.source_session_ids)]
        represented = {
            source
            for value in selected
            for source in value.source_session_ids
            if source in gold_set
        }
        result[f"edge_gold_source_precision_at_{cutoff}"] = (
            len(hits) / len(selected) if selected else 0.0
        )
        result[f"gold_session_edge_coverage_at_{cutoff}"] = len(represented) / len(gold)
    return result


def _datetime(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            # LongMemEval's checked-in dataset uses this display-oriented
            # timestamp format for question/session dates.
            result = datetime.strptime(value, "%Y/%m/%d (%a) %H:%M")
        except ValueError:
            raise ValueError("Quality v1 timestamp is invalid") from None
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def _normalized(value: str) -> str:
    return _SPACE.sub(" ", value.strip().casefold())


def temporal_diagnostics(
    facts: Sequence[RetrievedFact], *, question_date: str
) -> dict[str, Any]:
    """Diagnose visible stale/future/conflicting edges at the question time."""

    values = tuple(facts)
    if any(not isinstance(value, RetrievedFact) for value in values):
        raise ValueError("Quality v1 facts are invalid")
    question_time = _datetime(question_date)

    def time(value: str | None) -> datetime | None:
        return _datetime(value) if value is not None else None

    stale: set[int] = set()
    future: set[int] = set()
    active: set[int] = set()
    groups: dict[tuple[str, str, str], list[RetrievedFact]] = defaultdict(list)
    for value in values:
        valid = time(value.valid_at)
        reference = time(value.reference_time)
        invalid = time(value.invalid_at)
        expired = time(value.expired_at)
        if (invalid is not None and invalid <= question_time) or (
            expired is not None and expired <= question_time
        ):
            stale.add(value.retrieval_rank)
        elif (valid is not None and valid > question_time) or (
            valid is None and reference is not None and reference > question_time
        ):
            future.add(value.retrieval_rank)
        else:
            active.add(value.retrieval_rank)
        groups[(value.source_node_uuid, value.target_node_uuid, value.relation_name)].append(value)

    conflicts = [
        group
        for group in groups.values()
        if len({_normalized(value.fact) for value in group}) > 1
    ]
    latest_ranks: list[int] = []
    stale_before_latest = 0
    for group in conflicts:
        candidates = [value for value in group if value.retrieval_rank in active]
        if not candidates:
            continue

        def effective(value: RetrievedFact) -> tuple[datetime, int]:
            candidate = value.valid_at or value.reference_time
            return (
                _datetime(candidate)
                if candidate is not None
                else datetime.min.replace(tzinfo=timezone.utc),
                -value.retrieval_rank,
            )

        latest = max(candidates, key=effective)
        latest_ranks.append(latest.retrieval_rank)
        stale_before_latest += sum(
            1
            for value in group
            if value.retrieval_rank in stale
            and value.retrieval_rank < latest.retrieval_rank
        )
    return {
        "diagnostic_scope": "RETRIEVED_EDGE_TEMPORAL_FIELDS_ONLY",
        "stale_fact_count": len(stale),
        "active_fact_count": len(active),
        "future_fact_count": len(future),
        "conflicting_relation_group_count": len(conflicts),
        "latest_valid_fact_ranks": sorted(latest_ranks),
        "stale_ranked_before_latest_valid_count": stale_before_latest,
    }


def _validated_turns(turns: object) -> list[dict[str, str]]:
    if not isinstance(turns, list) or not turns:
        raise ValueError("Quality v1 session turns are invalid")
    result: list[dict[str, str]] = []
    for raw in turns:
        if (
            not isinstance(raw, Mapping)
            or raw.get("role") not in {"user", "assistant"}
            or not isinstance(raw.get("content"), str)
            or not raw["content"]
        ):
            raise ValueError("Quality v1 session turn is invalid")
        # LongMemEval removes has_answer before generation.  Copy only the two
        # model-visible fields so no benchmark label can enter ContextPack.
        result.append({"role": str(raw["role"]), "content": str(raw["content"])})
    return result


@dataclass(frozen=True)
class _FlatTurnCandidate:
    original_order: int
    graphiti_session_rank: int
    session_id: str
    session_date: str
    source_turn_index: int
    user_content: str
    expanded_round: tuple[dict[str, str], ...]


def _rank_flat_user_turns(
    *,
    corpus: Mapping[str, tuple[str, object]],
    episodes: Sequence[RetrievedEpisode],
    question: str,
) -> list[_FlatTurnCandidate]:
    """Adapt LongMemEval flat-bm25 and flat-turn round expansion exactly.

    The only intentional difference from LongMemEval's NumPy ``argsort()[::-1]``
    is an explicit stable tie rule.  It prevents library/version-dependent
    reversal of zero-score items while preserving Graphiti candidate rank and
    source turn order.
    """

    candidates: list[_FlatTurnCandidate] = []
    for episode in episodes:
        session_date, raw_turns = corpus[episode.session_id]
        turns = _validated_turns(raw_turns)
        for index, turn in enumerate(turns):
            if turn["role"] != "user":
                continue
            expanded = [turn]
            if index + 1 < len(turns):
                expanded.append(turns[index + 1])
            candidates.append(
                _FlatTurnCandidate(
                    original_order=len(candidates),
                    graphiti_session_rank=episode.retrieval_rank,
                    session_id=episode.session_id,
                    session_date=session_date,
                    source_turn_index=index,
                    user_content=turn["content"],
                    expanded_round=tuple(expanded),
                )
            )
    if not candidates:
        raise ValueError("Quality v1 candidate sessions contain no USER turns")
    # These two split calls intentionally reproduce LongMemEval's public
    # run_flat_retrieval implementation; do not substitute an ad-hoc tokenizer.
    bm25 = BM25Okapi([value.user_content.split(" ") for value in candidates])
    scores = bm25.get_scores(question.split(" "))
    ranked_indices = sorted(
        range(len(candidates)),
        key=lambda index: (-float(scores[index]), candidates[index].original_order),
    )
    return [candidates[index] for index in ranked_indices[:MAX_LOCAL_ROUNDS]]


def _corpus(record: Mapping[str, Any]) -> dict[str, tuple[str, object]]:
    ids = record.get("haystack_session_ids")
    dates = record.get("haystack_dates")
    sessions = record.get("haystack_sessions")
    if not all(isinstance(value, list) for value in (ids, dates, sessions)):
        raise ValueError("Quality v1 corpus is invalid")
    if not ids or len(ids) != len(dates) or len(ids) != len(sessions):
        raise ValueError("Quality v1 corpus inventory is invalid")
    result: dict[str, tuple[str, object]] = {}
    for session_id, date, turns in zip(ids, dates, sessions, strict=True):
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in result
            or not isinstance(date, str)
            or not date
        ):
            raise ValueError("Quality v1 corpus identity is invalid")
        result[session_id] = (date, turns)
    return result


def build_context_pack(
    *,
    record: Mapping[str, Any],
    question: str,
    facts: Sequence[RetrievedFact],
    episodes: Sequence[RetrievedEpisode],
) -> ContextPack:
    """Create deterministic fact plus source-local evidence without labels."""

    if not isinstance(question, str) or not question:
        raise ValueError("Quality v1 question is invalid")
    fact_values = tuple(facts)
    episode_values = tuple(episodes)
    if any(not isinstance(value, RetrievedFact) for value in fact_values) or any(
        not isinstance(value, RetrievedEpisode) for value in episode_values
    ):
        raise ValueError("Quality v1 retrieval evidence is invalid")
    if [value.retrieval_rank for value in fact_values] != list(
        range(1, len(fact_values) + 1)
    ) or [value.retrieval_rank for value in episode_values] != list(
        range(1, len(episode_values) + 1)
    ):
        raise ValueError("Quality v1 retrieval ranks are invalid")
    episode_ids = [value.episode_uuid for value in episode_values]
    session_ids = [value.session_id for value in episode_values]
    if len(set(episode_ids)) != len(episode_ids) or len(set(session_ids)) != len(session_ids):
        raise ValueError("Quality v1 episode identity is duplicated")
    corpus = _corpus(record)
    if not set(session_ids).issubset(corpus):
        raise ValueError("Quality v1 episode identity is foreign")

    evidence: list[dict[str, Any]] = []
    for value in fact_values:
        timestamp = value.reference_time or value.valid_at
        evidence.append(
            {
                "evidence_type": "graph_fact",
                "retrieval_rank": value.retrieval_rank,
                "raw_evidence": value.fact,
                "timestamp": timestamp,
                "speaker": "graph_fact",
                "source_id": value.edge_uuid,
                "source_session_ids": list(value.source_session_ids),
                "valid_at": value.valid_at,
                "invalid_at": value.invalid_at,
                "expired_at": value.expired_at,
            }
        )
    selected_rounds = _rank_flat_user_turns(
        corpus=corpus,
        episodes=episode_values,
        question=question,
    )
    for local_rank, value in enumerate(selected_rounds, start=1):
        evidence.append(
            {
                "evidence_type": "source_local_round",
                "retrieval_rank": local_rank,
                "graphiti_session_rank": value.graphiti_session_rank,
                "raw_evidence": list(value.expanded_round),
                "timestamp": value.session_date,
                "speaker": "+".join(
                    dict.fromkeys(turn["role"] for turn in value.expanded_round)
                ),
                # LongMemEval uses the original zero-based index plus one in
                # its flat-turn corpus identity.
                "source_id": f"{value.session_id}_{value.source_turn_index + 1}",
                "source_session_ids": [value.session_id],
                "valid_at": None,
                "invalid_at": None,
                "expired_at": None,
            }
        )

    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in evidence:
        normalized = _normalized(json.dumps(value["raw_evidence"], sort_keys=True))
        if normalized in seen:
            continue
        seen.add(normalized)
        deduplicated.append(value)
    deduplicated.sort(
        key=lambda value: (
            value["timestamp"] is None,
            _datetime(value["timestamp"])
            if value["timestamp"] is not None
            else datetime.max.replace(tzinfo=timezone.utc),
            value["evidence_type"],
            value["retrieval_rank"],
            value["source_id"],
        )
    )
    return ContextPack(
        context_json=json.dumps(
            deduplicated,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        evidence_count=len(deduplicated),
        fact_count=len(fact_values),
        session_candidate_count=len(episode_values),
        local_round_count=len(selected_rounds),
    )


__all__ = [
    "ContextPack",
    "CONTEXT_POLICY",
    "CONTEXT_POLICY_SHA256",
    "EDGE_CUTOFFS",
    "MAX_LOCAL_ROUNDS",
    "RetrievedEpisode",
    "RetrievedFact",
    "SESSION_CUTOFFS",
    "build_context_pack",
    "edge_provenance_metrics",
    "session_ranking_metrics",
    "temporal_diagnostics",
]
