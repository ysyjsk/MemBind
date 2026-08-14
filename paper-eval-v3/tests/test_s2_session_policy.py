from __future__ import annotations

import pytest

from paper_eval.s2_session_policy import (
    FrozenEpisodeSession,
    RankedEpisodeObservation,
    SessionPolicyError,
    evaluate_session_retrieval,
    map_ranked_episodes_to_sessions,
)


def _frozen(count: int = 12) -> tuple[FrozenEpisodeSession, ...]:
    return tuple(
        FrozenEpisodeSession(
            episode_name=f"episode-{index:02d}",
            source_sequence=index,
            session_id=f"session-{index:02d}",
            content_sha256=f"{index + 1:x}" * 64,
        )
        for index in range(count)
    )


def _ranked(order: tuple[int, ...]) -> tuple[RankedEpisodeObservation, ...]:
    frozen = _frozen()
    return tuple(
        RankedEpisodeObservation(
            episode_uuid=f"uuid-{index}",
            episode_name=frozen[index].episode_name,
            content_sha256=frozen[index].content_sha256,
        )
        for index in order
    )


def test_maps_native_episodic_nodes_to_unique_longmemeval_sessions() -> None:
    mapped = map_ranked_episodes_to_sessions(
        ranked_episodes=_ranked(tuple(range(11, -1, -1))),
        frozen_mapping=_frozen(),
        top_k=10,
    )

    assert mapped == tuple(f"session-{index:02d}" for index in range(11, 1, -1))


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_episode_name",
        "duplicate_source_sequence",
        "noncontiguous_source_sequence",
        "duplicate_session_id",
        "bad_content_hash",
    ],
)
def test_rejects_invalid_frozen_episode_to_session_mapping(mutation: str) -> None:
    frozen = list(_frozen())
    if mutation == "duplicate_episode_name":
        frozen[1] = FrozenEpisodeSession(
            frozen[0].episode_name,
            frozen[1].source_sequence,
            frozen[1].session_id,
            frozen[1].content_sha256,
        )
    elif mutation == "duplicate_source_sequence":
        frozen[1] = FrozenEpisodeSession(
            frozen[1].episode_name,
            frozen[0].source_sequence,
            frozen[1].session_id,
            frozen[1].content_sha256,
        )
    elif mutation == "noncontiguous_source_sequence":
        frozen[1] = FrozenEpisodeSession(
            frozen[1].episode_name,
            99,
            frozen[1].session_id,
            frozen[1].content_sha256,
        )
    elif mutation == "duplicate_session_id":
        frozen[1] = FrozenEpisodeSession(
            frozen[1].episode_name,
            frozen[1].source_sequence,
            frozen[0].session_id,
            frozen[1].content_sha256,
        )
    else:
        frozen[1] = FrozenEpisodeSession(
            frozen[1].episode_name,
            frozen[1].source_sequence,
            frozen[1].session_id,
            "bad-hash",
        )

    with pytest.raises(SessionPolicyError, match="frozen mapping"):
        map_ranked_episodes_to_sessions(
            ranked_episodes=_ranked(tuple(range(10))),
            frozen_mapping=frozen,
            top_k=10,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_episode",
        "duplicate_episode",
        "duplicate_uuid",
        "content_mismatch",
        "too_few_results",
    ],
)
def test_rejects_foreign_duplicate_or_incomplete_ranked_results(mutation: str) -> None:
    ranked = list(_ranked(tuple(range(10))))
    if mutation == "unknown_episode":
        ranked[0] = RankedEpisodeObservation(
            "uuid-foreign", "episode-foreign", "f" * 64
        )
    elif mutation == "duplicate_episode":
        ranked[1] = RankedEpisodeObservation(
            "uuid-distinct",
            ranked[0].episode_name,
            ranked[0].content_sha256,
        )
    elif mutation == "duplicate_uuid":
        ranked[1] = RankedEpisodeObservation(
            ranked[0].episode_uuid,
            ranked[1].episode_name,
            ranked[1].content_sha256,
        )
    elif mutation == "content_mismatch":
        ranked[1] = RankedEpisodeObservation(
            ranked[1].episode_uuid,
            ranked[1].episode_name,
            "f" * 64,
        )
    else:
        ranked.pop()

    with pytest.raises(SessionPolicyError, match="ranked episode"):
        map_ranked_episodes_to_sessions(
            ranked_episodes=ranked,
            frozen_mapping=_frozen(),
            top_k=10,
        )


@pytest.mark.parametrize(
    ("retrieved", "gold", "expected"),
    [
        (
            ("s1", "s2", "s3"),
            ("s1", "s2"),
            (1.0, 1.0, 1.0, (1, 2)),
        ),
        (
            ("s1", "s3", "s4"),
            ("s1", "s2"),
            (1.0, 0.0, 0.5, (1, None)),
        ),
        (
            ("s3", "s4", "s5"),
            ("s1", "s2"),
            (0.0, 0.0, 0.0, (None, None)),
        ),
    ],
)
def test_session_recall_any_all_matches_official_binary_semantics(
    retrieved: tuple[str, ...],
    gold: tuple[str, ...],
    expected: tuple[float, float, float, tuple[int | None, ...]],
) -> None:
    result = evaluate_session_retrieval(
        retrieved_session_ids=retrieved,
        gold_session_ids=gold,
        top_k=10,
    )

    assert result.session_recall_any_at_10 == expected[0]
    assert result.session_recall_all_at_10 == expected[1]
    assert result.session_gold_coverage_fraction_at_10 == expected[2]
    assert result.gold_ranks == expected[3]
    assert result.evidence_recall_at_10 == expected[1]
    assert result.coverage_fraction_is_official is False


def test_metric_rejects_duplicate_or_out_of_corpus_gold_and_ranked_sessions() -> None:
    with pytest.raises(SessionPolicyError, match="retrieved session"):
        evaluate_session_retrieval(
            retrieved_session_ids=("s1", "s1"),
            gold_session_ids=("s1",),
            top_k=10,
        )
    with pytest.raises(SessionPolicyError, match="gold session"):
        evaluate_session_retrieval(
            retrieved_session_ids=("s1", "s2"),
            gold_session_ids=("s3",),
            top_k=10,
            allowed_session_ids=("s1", "s2"),
        )


def test_gold_labels_are_evaluator_only_and_do_not_enter_mapping_api() -> None:
    assert "gold" not in map_ranked_episodes_to_sessions.__annotations__
    assert "answer_session_ids" not in map_ranked_episodes_to_sessions.__annotations__
