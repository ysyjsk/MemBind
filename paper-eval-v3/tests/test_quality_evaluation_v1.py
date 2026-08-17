"""TDD contracts for the frozen Quality Evaluation v1 data plane."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from paper_eval.quality_evaluation_v1 import (
    RetrievedEpisode,
    RetrievedFact,
    build_context_pack,
    edge_provenance_metrics,
    session_ranking_metrics,
    temporal_diagnostics,
)


def _fact(
    rank: int,
    fact: str,
    sessions: tuple[str, ...],
    *,
    valid_at: str | None = None,
    invalid_at: str | None = None,
    source: str = "user",
    target: str = "ratio",
    name: str = "HAS_VALUE",
) -> RetrievedFact:
    return RetrievedFact(
        retrieval_rank=rank,
        edge_uuid=f"edge-{rank}",
        source_node_uuid=source,
        target_node_uuid=target,
        relation_name=name,
        fact=fact,
        source_session_ids=sessions,
        valid_at=valid_at,
        invalid_at=invalid_at,
        expired_at=None,
        reference_time=valid_at,
    )


def _record() -> dict:
    return {
        # These labels deliberately exist in the record; the selector must not
        # consult or serialize them.
        "answer": "5 ounces",
        "answer_session_ids": ["gold-secret"],
        "haystack_session_ids": ["s-old", "s-new", "s-noise"],
        "haystack_dates": [
            "2023-01-01T00:00:00+00:00",
            "2023-02-01T00:00:00+00:00",
            "2023-03-01T00:00:00+00:00",
        ],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I use 6 ounces of water."},
                {"role": "assistant", "content": "Six ounces noted."},
                {"role": "user", "content": "Unrelated hiking question."},
                {"role": "assistant", "content": "Try a map."},
            ],
            [
                {"role": "user", "content": "I now use 5 ounces of water."},
                {"role": "assistant", "content": "That is less water."},
            ],
            [
                {"role": "user", "content": "I bought new shoes."},
                {"role": "assistant", "content": "Nice shoes."},
            ],
        ],
    }


def test_session_metrics_do_not_saturate_at_recall10() -> None:
    ranked = ("noise", "gold-a", "other", "gold-b", "tail")
    metrics = session_ranking_metrics(ranked, ("gold-a", "gold-b"))

    assert metrics["recall_at_1"] == 0.0
    assert metrics["recall_at_3"] == 0.5
    assert metrics["recall_at_5"] == 1.0
    assert metrics["recall_at_10"] == 1.0
    assert metrics["mrr"] == 0.5
    expected_dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = 1 + 1 / math.log2(3)
    assert metrics["ndcg_at_10"] == pytest.approx(expected_dcg / ideal)
    assert metrics["gold_ranks"] == [2, 4]


def test_edge_metrics_are_explicit_provenance_proxies() -> None:
    facts = (
        _fact(1, "noise", ("noise",)),
        _fact(2, "old", ("gold-a",)),
        _fact(3, "new", ("gold-b", "noise")),
    )

    metrics = edge_provenance_metrics(facts, ("gold-a", "gold-b"))

    assert metrics["metric_scope"] == "PROVENANCE_PROXY_NOT_GOLD_FACT_RECALL"
    assert metrics["edge_gold_source_precision_at_1"] == 0.0
    assert metrics["edge_gold_source_precision_at_3"] == pytest.approx(2 / 3)
    assert metrics["gold_session_edge_coverage_at_3"] == 1.0


def test_temporal_diagnostics_identify_stale_and_conflicting_relation_group() -> None:
    facts = (
        _fact(
            1,
            "The ratio is 6 ounces.",
            ("s-old",),
            valid_at="2023-01-01T00:00:00+00:00",
            invalid_at="2023-02-01T00:00:00+00:00",
        ),
        _fact(
            2,
            "The ratio is 5 ounces.",
            ("s-new",),
            valid_at="2023-02-01T00:00:00+00:00",
        ),
        _fact(
            3,
            "A future ratio is 4 ounces.",
            ("s-future",),
            valid_at="2024-01-01T00:00:00+00:00",
        ),
    )

    metrics = temporal_diagnostics(
        facts, question_date="2023-03-01T00:00:00+00:00"
    )

    assert metrics["stale_fact_count"] == 1
    assert metrics["active_fact_count"] == 1
    assert metrics["future_fact_count"] == 1
    assert metrics["conflicting_relation_group_count"] == 1
    assert metrics["latest_valid_fact_ranks"] == [2]
    assert metrics["stale_ranked_before_latest_valid_count"] == 1


def test_temporal_diagnostics_accept_longmemeval_native_question_date() -> None:
    metrics = temporal_diagnostics(
        (
            _fact(
                1,
                "The ratio was 6 ounces.",
                ("s-old",),
                valid_at="2023-05-01T00:00:00+00:00",
                invalid_at="2023-06-01T00:00:00+00:00",
            ),
        ),
        question_date="2023/06/23 (Fri) 07:31",
    )

    assert metrics["stale_fact_count"] == 1


def test_context_pack_is_gold_blind_local_deduplicated_and_chronological() -> None:
    facts = (
        _fact(
            1,
            "The ratio is now 5 ounces.",
            ("s-new",),
            valid_at="2023-02-01T00:00:00+00:00",
        ),
        _fact(
            2,
            "The ratio was 6 ounces.",
            ("s-old",),
            valid_at="2023-01-01T00:00:00+00:00",
            invalid_at="2023-02-01T00:00:00+00:00",
        ),
    )
    episodes = (
        RetrievedEpisode(1, "episode-new", "s-new"),
        RetrievedEpisode(2, "episode-old", "s-old"),
        RetrievedEpisode(3, "episode-noise", "s-noise"),
    )

    pack = build_context_pack(
        record=_record(),
        question="Did I switch to more water or less water?",
        facts=facts,
        episodes=episodes,
    )
    decoded = json.loads(pack.context_json)

    assert pack.session_candidate_count == 3
    # LongMemEval flat-turn retrieval ranks USER turns globally and expands
    # each selected USER turn with its immediately following turn.  There are
    # four eligible USER turns in this fixture, so no per-session best-round
    # heuristic may silently discard the hiking round.
    assert pack.local_round_count == 4
    def instant(value: str) -> datetime:
        if "/" in value:
            return datetime.strptime(value, "%Y/%m/%d (%a) %H:%M").replace(
                tzinfo=timezone.utc
            )
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    observed_instants = [
        instant(value["timestamp"])
        for value in decoded
        if value["timestamp"] is not None
    ]
    assert observed_instants == sorted(observed_instants)
    assert all(
        {"evidence_type", "raw_evidence", "timestamp", "speaker", "source_id", "valid_at", "invalid_at"}
        <= set(value)
        for value in decoded
    )
    serialized = pack.context_json
    assert "gold-secret" not in serialized
    assert "5 ounces" in serialized
    assert "6 ounces" in serialized
    assert "Unrelated hiking question" in serialized


def test_context_pack_ranks_user_turns_across_all_graphiti_candidate_sessions() -> None:
    record = {
        "answer": "must never affect retrieval",
        "answer_session_ids": ["s-11"],
        "haystack_session_ids": [f"s-{index}" for index in range(1, 12)],
        "haystack_dates": [
            f"2023-01-{index:02d}T00:00:00+00:00" for index in range(1, 12)
        ],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": "needle appears here" if index == 11 else f"noise {index}",
                },
                {"role": "assistant", "content": f"reply {index}"},
            ]
            for index in range(1, 12)
        ],
    }
    episodes = tuple(
        RetrievedEpisode(index, f"episode-{index}", f"s-{index}")
        for index in range(1, 12)
    )

    pack = build_context_pack(
        record=record,
        question="needle",
        facts=(),
        episodes=episodes,
    )

    # Graphiti supplies the candidate-session boundary; LongMemEval-style
    # flat-turn BM25 then selects one frozen global Top-10 from every USER turn
    # in that boundary.  The relevant turn in candidate session 11 must not be
    # lost by slicing sessions before local ranking.
    assert pack.session_candidate_count == 11
    assert pack.local_round_count == 10
    assert "needle appears here" in pack.context_json
    assert "reply 11" in pack.context_json


def test_context_pack_rejects_duplicate_or_foreign_episode_identity() -> None:
    with pytest.raises(ValueError):
        build_context_pack(
            record=_record(),
            question="ratio",
            facts=(),
            episodes=(
                RetrievedEpisode(1, "ep-1", "s-old"),
                RetrievedEpisode(2, "ep-2", "s-old"),
            ),
        )
