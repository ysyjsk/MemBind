"""Explicit retrieval-surface contracts for the stopped S2 qualification.

Graphiti's basic ``search`` API ranks EntityEdge objects, while LongMemEval's
``flat-session`` path ranks one item per session.  Keeping these contracts
explicit prevents an edge top-k from being reported as an official
session-level retrieval metric.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class RetrievalContractError(ValueError):
    """A retrieval identity or metric violates its declared surface."""


@dataclass(frozen=True)
class RetrievalSurfaceContract:
    """The unit and metric semantics bound to one retrieval implementation."""

    retrieval_surface: str
    retrieval_method: str
    search_recipe: str
    result_unit: str
    top_k_unit: str
    metric_name: str
    official_longmemeval_session_metric: bool
    official_longmemeval_retriever_implementation: bool
    retriever_implementation_identity: str
    question_date_used_for_retrieval: bool
    retrieval_temporal_filter: str
    construction_quality_surface: bool

    def to_identity(self) -> dict[str, Any]:
        return {
            "retrieval_surface": self.retrieval_surface,
            "retrieval_method": self.retrieval_method,
            "search_recipe": self.search_recipe,
            "retrieval_unit": self.result_unit,
            "top_k_unit": self.top_k_unit,
            "metric_name": self.metric_name,
            "official_longmemeval_session_metric": (
                self.official_longmemeval_session_metric
            ),
            "official_longmemeval_retriever_implementation": (
                self.official_longmemeval_retriever_implementation
            ),
            "retriever_implementation_identity": self.retriever_implementation_identity,
            "question_date_used_for_retrieval": self.question_date_used_for_retrieval,
            "retrieval_temporal_filter": self.retrieval_temporal_filter,
            "construction_quality_surface": self.construction_quality_surface,
        }


EDGE_SURFACE_CONTRACT = RetrievalSurfaceContract(
    retrieval_surface="graphiti_basic_edge",
    retrieval_method="Graphiti.search",
    search_recipe="EDGE_HYBRID_SEARCH_RRF",
    result_unit="EntityEdge",
    top_k_unit="edge",
    metric_name="edge_attributed_source_session_coverage_at_10",
    official_longmemeval_session_metric=False,
    official_longmemeval_retriever_implementation=False,
    retriever_implementation_identity="graphiti-0.29.3-basic-edge-hybrid-rrf",
    question_date_used_for_retrieval=False,
    retrieval_temporal_filter="none",
    construction_quality_surface=True,
)

SESSION_SURFACE_CONTRACT = RetrievalSurfaceContract(
    retrieval_surface="graphiti_episode_bm25_session_diagnostic",
    retrieval_method="Graphiti.search_",
    search_recipe="EPISODE_BM25_RRF",
    result_unit="EpisodicNode",
    top_k_unit="session",
    metric_name="longmemeval_session_recall_any_all_at_10",
    official_longmemeval_session_metric=True,
    official_longmemeval_retriever_implementation=False,
    retriever_implementation_identity="graphiti-0.29.3-episode-fulltext",
    question_date_used_for_retrieval=False,
    retrieval_temporal_filter="none",
    construction_quality_surface=False,
)


def _nonempty_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RetrievalContractError(f"{field} must contain nonempty strings")
    return value


def edge_attributed_source_session_coverage(
    *,
    ranked_edge_source_session_ids: Sequence[Sequence[str]],
    gold_session_ids: Sequence[str],
    top_k: int,
) -> tuple[float, tuple[str, ...]]:
    """Compute a diagnostic source-session coverage for an edge-ranked list.

    ``top_k`` counts EntityEdge rows.  Session IDs are deduplicated only after
    consuming those rows, which deliberately differs from official
    LongMemEval session-level recall.
    """

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RetrievalContractError("top_k must be a positive integer")
    if isinstance(ranked_edge_source_session_ids, (str, bytes)):
        raise RetrievalContractError("edge source session rows are invalid")
    if isinstance(gold_session_ids, (str, bytes)) or not gold_session_ids:
        raise RetrievalContractError("gold session IDs are invalid")
    gold = tuple(
        _nonempty_id(value, field="gold session IDs") for value in gold_session_ids
    )
    if len(set(gold)) != len(gold):
        raise RetrievalContractError("gold session IDs must be unique")

    ordered_sessions: list[str] = []
    seen: set[str] = set()
    for row in list(ranked_edge_source_session_ids)[:top_k]:
        if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
            raise RetrievalContractError("edge source session row is invalid")
        for value in row:
            session_id = _nonempty_id(value, field="edge source session")
            if session_id not in seen:
                seen.add(session_id)
                ordered_sessions.append(session_id)
    covered = len(set(ordered_sessions).intersection(gold))
    return covered / len(gold), tuple(ordered_sessions)


def validate_retrieval_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a public retrieval identity and reject unit mismatches."""

    if not isinstance(identity, Mapping):
        raise RetrievalContractError("retrieval identity must be a mapping")
    value = dict(identity)
    surface = value.get("retrieval_surface")
    if surface == EDGE_SURFACE_CONTRACT.retrieval_surface:
        expected = EDGE_SURFACE_CONTRACT.to_identity()
        if value.get("retriever_type") == "flat-session":
            raise RetrievalContractError(
                "flat-session cannot describe Graphiti EntityEdge results"
            )
    elif surface == SESSION_SURFACE_CONTRACT.retrieval_surface:
        expected = SESSION_SURFACE_CONTRACT.to_identity()
        if value.get("retriever_type") != "flat-session":
            raise RetrievalContractError(
                "session surface must declare flat-session retriever_type"
            )
    else:
        raise RetrievalContractError("unknown retrieval surface")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise RetrievalContractError(f"retrieval identity mismatch: {key}")
    return value


def classify_edge_surface_observation(
    *,
    search_surface: str,
    gold_episode_entity_edge_counts: Sequence[int],
    gold_episode_match_count: int,
    gold_session_count: int,
) -> dict[str, Any]:
    """Classify only what zero EntityEdge provenance can establish."""

    if search_surface != "EntityEdge":
        raise RetrievalContractError("edge observation has an invalid search surface")
    if (
        isinstance(gold_episode_match_count, bool)
        or not isinstance(gold_episode_match_count, int)
        or gold_episode_match_count < 0
        or isinstance(gold_session_count, bool)
        or not isinstance(gold_session_count, int)
        or gold_session_count <= 0
        or gold_episode_match_count != gold_session_count
    ):
        raise RetrievalContractError("gold episode mapping is incomplete")
    if isinstance(gold_episode_entity_edge_counts, (str, bytes)):
        raise RetrievalContractError("gold episode edge counts are invalid")
    counts = tuple(gold_episode_entity_edge_counts)
    if len(counts) != gold_episode_match_count or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts
    ):
        raise RetrievalContractError("gold episode edge counts are invalid")
    if any(counts):
        classification = "GOLD_EPISODES_HAVE_ENTITYEDGE_PROVENANCE"
    else:
        classification = "GOLD_EPISODES_HAVE_NO_ENTITYEDGE_PROVENANCE"
    return {
        "classification": classification,
        "service_failure": False,
        "whole_graph_quality_conclusion": "NOT_INFERRED",
        "official_session_recall_computed": False,
    }


def _proportion(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= float(value) <= 1
    ):
        raise RetrievalContractError(f"{field} must be a proportion")
    return float(value)


def classify_surface_comparison(
    *,
    edge_attributed_source_session_coverage: float,
    episode_session_recall_any: float,
    episode_session_recall_all: float,
) -> dict[str, Any]:
    """Interpret a diagnostic comparison without selecting a paper policy."""

    edge_coverage = _proportion(
        edge_attributed_source_session_coverage,
        field="edge coverage proportion",
    )
    episode_recall_any = _proportion(
        episode_session_recall_any,
        field="episode Recall_any proportion",
    )
    episode_recall_all = _proportion(
        episode_session_recall_all,
        field="episode Recall_all proportion",
    )
    if episode_recall_any not in {0.0, 1.0} or episode_recall_all not in {0.0, 1.0}:
        raise RetrievalContractError("episode Recall_any/Recall_all must be binary")
    if episode_recall_all > episode_recall_any:
        raise RetrievalContractError("episode Recall_all cannot exceed Recall_any")

    if edge_coverage == 0 and episode_recall_all > 0:
        classification = "EDGE_SURFACE_COVERAGE_GAP_CONFIRMED"
        next_action = "SEAL_RESULT_AND_STOP_FOR_OFFLINE_POLICY_FREEZE"
    elif edge_coverage == 0 and episode_recall_any > 0:
        classification = "PARTIAL_EPISODE_SURFACE_REACHABILITY"
        next_action = "STOP_FOR_OFFLINE_DIAGNOSIS"
    elif edge_coverage == 0:
        return {
            "classification": "EDGE_AND_EPISODE_SURFACES_NEAR_ZERO",
            "whole_graph_quality_conclusion": "NOT_INFERRED",
            "node_surface_status": "UNTESTED",
            "multi_surface_status": "UNTESTED",
            "retrieval_policy_selected": False,
            "s3_authorized": False,
            "next_action": "STOP_NODE_OR_MULTI_SURFACE_UNTESTED",
        }
    else:
        classification = "EDGE_SURFACE_HAS_GOLD_COVERAGE"
        next_action = "REVIEW_REPRESENTATIVENESS_BEFORE_POLICY_SELECTION"
    return {
        "classification": classification,
        "whole_graph_quality_conclusion": "NOT_INFERRED",
        "node_surface_status": "UNTESTED",
        "multi_surface_status": "UNTESTED",
        "retrieval_policy_selected": False,
        "s3_authorized": False,
        "next_action": next_action,
    }
