from __future__ import annotations

from pathlib import Path

import pytest

from paper_eval.s2_retrieval_contract import (
    EDGE_SURFACE_CONTRACT,
    SESSION_SURFACE_CONTRACT,
    RetrievalContractError,
    classify_edge_surface_observation,
    classify_surface_comparison,
    edge_attributed_source_session_coverage,
    validate_retrieval_identity,
)


def test_native_edge_contract_is_explicitly_not_flat_session() -> None:
    assert EDGE_SURFACE_CONTRACT.retrieval_method == "Graphiti.search"
    assert EDGE_SURFACE_CONTRACT.result_unit == "EntityEdge"
    assert EDGE_SURFACE_CONTRACT.top_k_unit == "edge"
    assert EDGE_SURFACE_CONTRACT.metric_name == (
        "edge_attributed_source_session_coverage_at_10"
    )
    assert EDGE_SURFACE_CONTRACT.official_longmemeval_session_metric is False
    assert EDGE_SURFACE_CONTRACT.question_date_used_for_retrieval is False
    assert EDGE_SURFACE_CONTRACT.retrieval_temporal_filter == "none"
    assert EDGE_SURFACE_CONTRACT.construction_quality_surface is True
    assert SESSION_SURFACE_CONTRACT.retrieval_method == "Graphiti.search_"
    assert SESSION_SURFACE_CONTRACT.search_recipe == "EPISODE_BM25_RRF"
    assert SESSION_SURFACE_CONTRACT.result_unit == "EpisodicNode"
    assert SESSION_SURFACE_CONTRACT.top_k_unit == "session"
    assert SESSION_SURFACE_CONTRACT.official_longmemeval_session_metric is True
    assert (
        SESSION_SURFACE_CONTRACT.official_longmemeval_retriever_implementation
        is False
    )
    assert SESSION_SURFACE_CONTRACT.retriever_implementation_identity == (
        "graphiti-0.29.3-episode-fulltext"
    )
    assert SESSION_SURFACE_CONTRACT.construction_quality_surface is False


def test_edge_coverage_deduplicates_sessions_without_promoting_it_to_session_recall() -> None:
    coverage, sessions = edge_attributed_source_session_coverage(
        ranked_edge_source_session_ids=[
            ("s1",),
            ("s1",),
            ("s2",),
        ],
        gold_session_ids=("s1", "s2"),
        top_k=3,
    )
    assert coverage == 1.0
    assert sessions == ("s1", "s2")


def test_edge_coverage_rejects_invalid_ranked_rows() -> None:
    with pytest.raises(RetrievalContractError, match="edge source session"):
        edge_attributed_source_session_coverage(
            ranked_edge_source_session_ids=[("s1",), "not-a-row"],
            gold_session_ids=("s1",),
            top_k=10,
        )


def test_flat_session_identity_cannot_describe_entity_edge_results() -> None:
    with pytest.raises(RetrievalContractError, match="flat-session"):
        validate_retrieval_identity(
            {
                "retrieval_surface": "graphiti_basic_edge",
                "retrieval_unit": "EntityEdge",
                "top_k_unit": "edge",
                "retriever_type": "flat-session",
            }
        )


def test_edge_identity_requires_scope_and_metric_fields() -> None:
    identity = {
        **EDGE_SURFACE_CONTRACT.to_identity(),
        "retriever_type": "graphiti-basic-edge",
    }
    assert validate_retrieval_identity(identity) == identity


def test_zero_edge_observation_is_limited_to_provenance_claim() -> None:
    observation = classify_edge_surface_observation(
        search_surface="EntityEdge",
        gold_episode_entity_edge_counts=(0, 0),
        gold_episode_match_count=2,
        gold_session_count=2,
    )
    assert observation == {
        "classification": "GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE",
        "service_failure": False,
        "whole_graph_quality_conclusion": "NOT_INFERRED",
        "official_session_recall_computed": False,
    }


def test_zero_edge_observation_fails_closed_on_partial_gold_coverage() -> None:
    with pytest.raises(RetrievalContractError, match="gold episode"):
        classify_edge_surface_observation(
            search_surface="EntityEdge",
            gold_episode_entity_edge_counts=(0, 0),
            gold_episode_match_count=1,
            gold_session_count=2,
        )


def test_offline_s2_scaffold_cannot_reintroduce_session_metric_mislabel() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "scripts/run_s2_offline.py"
    ).read_text(encoding="utf-8")
    assert '"evidence_recall_at_10"' not in script
    assert '"edge_attributed_source_session_coverage_at_10": None' in script
    assert '"official_longmemeval_session_recall_at_10": None' in script
    assert "refusing to overwrite finalized S2 sanity" in script


def test_surface_comparison_confirms_edge_coverage_gap_without_selecting_policy() -> None:
    decision = classify_surface_comparison(
        edge_attributed_source_session_coverage=0.0,
        episode_session_recall_any=1.0,
        episode_session_recall_all=1.0,
    )
    assert decision == {
        "classification": "EDGE_SURFACE_COVERAGE_GAP_CONFIRMED",
        "whole_graph_quality_conclusion": "NOT_INFERRED",
        "node_surface_status": "UNTESTED",
        "multi_surface_status": "UNTESTED",
        "retrieval_policy_selected": False,
        "s3_authorized": False,
        "next_action": "SEAL_RESULT_AND_STOP_FOR_OFFLINE_POLICY_FREEZE",
    }


def test_surface_comparison_stops_at_exact_tested_scope_on_double_miss() -> None:
    decision = classify_surface_comparison(
        edge_attributed_source_session_coverage=0.0,
        episode_session_recall_any=0.0,
        episode_session_recall_all=0.0,
    )
    assert decision == {
        "classification": "EDGE_AND_EPISODE_SURFACES_NEAR_ZERO",
        "whole_graph_quality_conclusion": "NOT_INFERRED",
        "node_surface_status": "UNTESTED",
        "multi_surface_status": "UNTESTED",
        "retrieval_policy_selected": False,
        "s3_authorized": False,
        "next_action": "STOP_NODE_OR_MULTI_SURFACE_UNTESTED",
    }


def test_surface_comparison_distinguishes_partial_episode_reachability() -> None:
    decision = classify_surface_comparison(
        edge_attributed_source_session_coverage=0.0,
        episode_session_recall_any=1.0,
        episode_session_recall_all=0.0,
    )
    assert decision["classification"] == "PARTIAL_EPISODE_SURFACE_REACHABILITY"
    assert decision["next_action"] == "STOP_FOR_OFFLINE_DIAGNOSIS"
    assert decision["s3_authorized"] is False


@pytest.mark.parametrize(
    ("edge_coverage", "episode_recall"),
    [(-0.1, 0.0), (0.0, 1.1), (True, 0.0)],
)
def test_surface_comparison_rejects_invalid_proportions(
    edge_coverage: object, episode_recall: object
) -> None:
    with pytest.raises(RetrievalContractError, match="proportion"):
        classify_surface_comparison(
            edge_attributed_source_session_coverage=edge_coverage,
            episode_session_recall_any=episode_recall,
            episode_session_recall_all=0.0,
        )


def test_surface_comparison_rejects_all_without_any() -> None:
    with pytest.raises(RetrievalContractError, match="Recall_all"):
        classify_surface_comparison(
            edge_attributed_source_session_coverage=0.0,
            episode_session_recall_any=0.0,
            episode_session_recall_all=1.0,
        )
