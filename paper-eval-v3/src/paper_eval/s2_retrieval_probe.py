"""Fail-closed policy for the one authorized S2-R0 Episode probe.

The module keeps raw benchmark text in memory only. It validates the complete
one-episode-per-session corpus before calling upstream ``Graphiti.search_`` and
records observed request counters rather than hard-coding a zero-call claim.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import atomic_write_json, finalize_envelope, payload_sha256
from .s2_retrieval_contract import (
    SESSION_SURFACE_CONTRACT,
    RetrievalContractError,
    classify_surface_comparison,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MUTATING_CYPHER = re.compile(
    r"\b(?:CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b",
    re.IGNORECASE,
)
_CORPUS_QUERY = """
MATCH (ep:Episodic)
WHERE ep.group_id = $group_id
RETURN ep.uuid AS uuid, ep.name AS name, ep.group_id AS group_id,
       ep.content AS content
ORDER BY ep.name, ep.uuid
"""


@dataclass
class ProbeCounters:
    """Observed calls for the bounded probe; forbidden calls fail immediately."""

    construction_llm_requests: int = 0
    embedding_requests: int = 0
    cross_encoder_requests: int = 0
    reader_requests: int = 0
    judge_requests: int = 0
    neo4j_read_requests: int = 0
    database_mutation_attempts: int = 0
    database_mutations: int = 0
    graphiti_search_calls: int = 0
    namespace_cleanup_calls: int = 0
    retry_count: int = 0

    def snapshot(self) -> "ProbeCounterSnapshot":
        return ProbeCounterSnapshot(**self.__dict__)


@dataclass(frozen=True)
class ProbeCounterSnapshot:
    construction_llm_requests: int
    embedding_requests: int
    cross_encoder_requests: int
    reader_requests: int
    judge_requests: int
    neo4j_read_requests: int
    database_mutation_attempts: int
    database_mutations: int
    graphiti_search_calls: int
    namespace_cleanup_calls: int
    retry_count: int

    def to_artifact(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class _CorpusGuardResult:
    uuid_to_session_id: Mapping[str, str]
    observed_session_count: int
    expected_name_content_map_sha256: str
    observed_name_content_map_sha256: str


@dataclass(frozen=True)
class EpisodeSurfaceProbeResult:
    retrieved_session_ids: tuple[str, ...]
    gold_session_ids: tuple[str, ...]
    covered_gold_session_count: int
    session_recall_any_at_10: float
    session_recall_all_at_10: float
    session_gold_coverage_fraction_at_10: float
    edge_attributed_source_session_coverage: float
    classification: str
    node_surface_status: str
    multi_surface_status: str
    corpus_completeness_pass: bool
    observed_session_count: int
    frozen_corpus_identity_sha256: str
    expected_name_content_map_sha256: str
    observed_name_content_map_sha256: str
    retrieval_config_identity: Mapping[str, Any]
    query_sha256: str
    query_character_count: int
    counters: ProbeCounterSnapshot


def _nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RetrievalContractError(f"{field} must be nonempty")
    return value


def _hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RetrievalContractError(f"{field} is not a SHA256")
    return value


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if values is None:
        raise RuntimeError("episode corpus query returned an invalid result shape")
    return [value if isinstance(value, dict) else dict(value) for value in values]


def _episode_body(episode: Any) -> str:
    body = getattr(episode, "body", None)
    if body is None:
        body = getattr(episode, "content", None)
    if not isinstance(body, str):
        raise RetrievalContractError("episode body must be a string")
    return body


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _expected_corpus_rows(episodes: Sequence[Any]) -> list[dict[str, str]]:
    if isinstance(episodes, (str, bytes)) or not episodes:
        raise RetrievalContractError("episode corpus identity is incomplete")
    rows: list[dict[str, str]] = []
    names: set[str] = set()
    sessions: set[str] = set()
    for episode in episodes:
        name = _nonempty(getattr(episode, "name", None), field="episode name")
        session_id = _nonempty(
            getattr(episode, "session_id", None), field="episode session ID"
        )
        if name in names or session_id in sessions:
            raise RetrievalContractError(
                "episode corpus requires one unique EpisodicNode per session"
            )
        names.add(name)
        sessions.add(session_id)
        rows.append(
            {
                "episode_name": name,
                "session_id": session_id,
                "content_sha256": _text_sha256(_episode_body(episode)),
            }
        )
    return rows


def corpus_identity_sha256(episodes: Sequence[Any]) -> str:
    """Bind ordered episode names, session IDs, and exact content hashes."""

    return payload_sha256(_expected_corpus_rows(episodes))


def build_episode_bm25_search_config(
    *,
    top_k: int = 10,
    config_types: tuple[Any, Any, Any, Any] | None = None,
) -> Any:
    """Create a fresh exact Graphiti Episode BM25/RRF configuration."""

    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise RetrievalContractError("top_k must be a positive integer")
    if config_types is None:
        from graphiti_core.search.search_config import (
            EpisodeReranker,
            EpisodeSearchConfig,
            EpisodeSearchMethod,
            SearchConfig,
        )

        config_types = (
            SearchConfig,
            EpisodeSearchConfig,
            EpisodeSearchMethod,
            EpisodeReranker,
        )
    SearchConfig, EpisodeSearchConfig, EpisodeSearchMethod, EpisodeReranker = config_types
    return SearchConfig(
        edge_config=None,
        node_config=None,
        episode_config=EpisodeSearchConfig(
            search_methods=[EpisodeSearchMethod.bm25],
            reranker=EpisodeReranker.rrf,
        ),
        community_config=None,
        limit=top_k,
        reranker_min_score=0,
    )


def search_config_identity(search_config: Any) -> dict[str, Any]:
    """Return the behaviorally complete, content-free search identity."""

    episode = getattr(search_config, "episode_config", None)
    methods = getattr(episode, "search_methods", None)
    limit = getattr(search_config, "limit", None)
    return {
        "edge_config": None,
        "node_config": None,
        "episode_config": {
            "search_methods": [_enum_value(value) for value in methods or []],
            "reranker": _enum_value(getattr(episode, "reranker", None)),
            "sim_min_score": getattr(episode, "sim_min_score", None),
            "mmr_lambda": getattr(episode, "mmr_lambda", None),
            "bfs_max_depth": getattr(episode, "bfs_max_depth", None),
        },
        "community_config": None,
        "limit": limit,
        "reranker_min_score": getattr(search_config, "reranker_min_score", None),
        "candidate_limit": 2 * limit if isinstance(limit, int) else None,
        "search_filter": "EMPTY",
        "center_node_uuid": None,
        "bfs_origin_node_uuids": None,
        "query_vector": None,
    }


def _validate_episode_bm25_config(search_config: Any, *, top_k: int) -> None:
    episode_config = getattr(search_config, "episode_config", None)
    methods = getattr(episode_config, "search_methods", None)
    reranker = getattr(episode_config, "reranker", None)
    if (
        getattr(search_config, "edge_config", None) is not None
        or getattr(search_config, "node_config", None) is not None
        or getattr(search_config, "community_config", None) is not None
        or getattr(search_config, "limit", None) != top_k
        or getattr(search_config, "reranker_min_score", None) != 0
        or not isinstance(methods, list)
        or [_enum_value(value) for value in methods] != ["bm25"]
        or _enum_value(reranker) != "reciprocal_rank_fusion"
    ):
        raise RetrievalContractError(
            "probe requires the pinned episode-only BM25/RRF search config"
        )


@contextmanager
def _read_only_query_guard(driver: Any, counters: ProbeCounters):
    """Observe all driver queries and reject writes before they reach Neo4j."""

    if getattr(driver, "_init_task", None) is not None:
        raise RetrievalContractError(
            "driver auto schema initialization must be disabled for S2-R0"
        )
    original = getattr(driver, "execute_query", None)
    if not callable(original):
        raise RetrievalContractError("Graphiti driver has no query API")

    async def execute_read_only(query: str, *args: Any, **kwargs: Any) -> Any:
        routing = kwargs.get("routing_")
        if (
            not isinstance(query, str)
            or routing != "r"
            or _MUTATING_CYPHER.search(query) is not None
        ):
            counters.database_mutation_attempts += 1
            raise RuntimeError("read-only database contract rejected a query")
        counters.neo4j_read_requests += 1
        return await original(query, *args, **kwargs)

    setattr(driver, "execute_query", execute_read_only)
    try:
        yield
    finally:
        setattr(driver, "execute_query", original)


def _validate_local_inputs(
    *,
    episodes: Sequence[Any],
    expected_frozen_session_ids: Sequence[str],
    expected_corpus_identity_sha256: str,
    answer_session_ids: Sequence[str],
) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    rows = _expected_corpus_rows(episodes)
    expected_sessions = tuple(
        _nonempty(value, field="frozen session ID")
        for value in expected_frozen_session_ids
    )
    if len(set(expected_sessions)) != len(expected_sessions):
        raise RetrievalContractError("frozen session IDs must be unique")
    if expected_sessions != tuple(row["session_id"] for row in rows):
        raise RetrievalContractError("frozen session IDs do not match probe corpus")
    if corpus_identity_sha256(episodes) != _hash(
        expected_corpus_identity_sha256, field="frozen corpus identity"
    ):
        raise RetrievalContractError("frozen corpus identity does not match probe corpus")
    gold = tuple(_nonempty(value, field="gold session ID") for value in answer_session_ids)
    if not gold or len(set(gold)) != len(gold):
        raise RetrievalContractError("gold session IDs must be nonempty and unique")
    if not set(gold).issubset(expected_sessions):
        raise RetrievalContractError("gold session IDs fall outside frozen corpus")
    return rows, gold


async def _preflight_corpus(
    *,
    driver: Any,
    namespace: str,
    expected_rows: Sequence[Mapping[str, str]],
    expected_frozen_session_ids: Sequence[str],
) -> _CorpusGuardResult:
    result = await driver.execute_query(
        _CORPUS_QUERY,
        params={"group_id": namespace},
        routing_="r",
    )
    records = _records(result)
    if len(records) != len(expected_rows):
        raise RetrievalContractError("observed episode corpus count is incomplete")

    expected_by_name = {row["episode_name"]: dict(row) for row in expected_rows}
    observed_by_name: dict[str, dict[str, str]] = {}
    uuid_to_session: dict[str, str] = {}
    observed_uuids: set[str] = set()
    for record in records:
        uuid = _nonempty(record.get("uuid"), field="observed episode UUID")
        name = _nonempty(record.get("name"), field="observed episode name")
        group_id = _nonempty(record.get("group_id"), field="observed episode namespace")
        content = record.get("content")
        if not isinstance(content, str):
            raise RetrievalContractError("observed episode content identity is unavailable")
        if group_id != namespace:
            raise RetrievalContractError("observed episode namespace drift")
        if uuid in observed_uuids or name in observed_by_name:
            raise RetrievalContractError("observed episode corpus has duplicate identity")
        expected = expected_by_name.get(name)
        if expected is None:
            raise RetrievalContractError("observed episode corpus contains an unexpected name")
        observed_uuids.add(uuid)
        observed_by_name[name] = {
            "episode_name": name,
            "content_sha256": _text_sha256(content),
        }
        uuid_to_session[uuid] = expected["session_id"]

    if set(observed_by_name) != set(expected_by_name):
        raise RetrievalContractError("observed episode corpus name mapping is incomplete")
    expected_name_content = [
        {
            "episode_name": row["episode_name"],
            "content_sha256": row["content_sha256"],
        }
        for row in expected_rows
    ]
    observed_name_content = [
        observed_by_name[row["episode_name"]] for row in expected_rows
    ]
    expected_map_hash = payload_sha256(expected_name_content)
    observed_map_hash = payload_sha256(observed_name_content)
    if observed_map_hash != expected_map_hash:
        raise RetrievalContractError("observed episode content identity drift")

    observed_sessions = tuple(
        expected_by_name[row["episode_name"]]["session_id"] for row in expected_rows
    )
    if observed_sessions != tuple(expected_frozen_session_ids):
        raise RetrievalContractError("observed frozen session mapping drift")
    return _CorpusGuardResult(
        uuid_to_session_id=uuid_to_session,
        observed_session_count=len(observed_sessions),
        expected_name_content_map_sha256=expected_map_hash,
        observed_name_content_map_sha256=observed_map_hash,
    )


def _validate_success_counters(counters: ProbeCounterSnapshot) -> None:
    forbidden = (
        counters.construction_llm_requests,
        counters.embedding_requests,
        counters.cross_encoder_requests,
        counters.reader_requests,
        counters.judge_requests,
        counters.database_mutation_attempts,
        counters.database_mutations,
        counters.namespace_cleanup_calls,
        counters.retry_count,
    )
    if any(forbidden):
        raise RetrievalContractError("S2-R0 observed a forbidden operation")
    if counters.graphiti_search_calls != 1 or counters.neo4j_read_requests < 1:
        raise RetrievalContractError("S2-R0 read/search counters are inconsistent")


async def run_episode_surface_probe(
    *,
    graph: Any,
    query: str,
    namespace: str,
    episodes: Sequence[Any],
    expected_frozen_session_ids: Sequence[str],
    expected_corpus_identity_sha256: str,
    answer_session_ids: Sequence[str],
    edge_attributed_source_session_coverage: float,
    search_config: Any,
    top_k: int = 10,
    counters: ProbeCounters | None = None,
) -> EpisodeSurfaceProbeResult:
    """Run one upstream episode search after exact corpus completeness passes."""

    query_value = _nonempty(query, field="query")
    namespace_value = _nonempty(namespace, field="namespace")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k != 10:
        raise RetrievalContractError("S2-R0 top_k must equal 10")
    _validate_episode_bm25_config(search_config, top_k=top_k)
    expected_rows, gold = _validate_local_inputs(
        episodes=episodes,
        expected_frozen_session_ids=expected_frozen_session_ids,
        expected_corpus_identity_sha256=expected_corpus_identity_sha256,
        answer_session_ids=answer_session_ids,
    )
    observed = counters if counters is not None else ProbeCounters()

    with _read_only_query_guard(graph.driver, observed):
        corpus = await _preflight_corpus(
            driver=graph.driver,
            namespace=namespace_value,
            expected_rows=expected_rows,
            expected_frozen_session_ids=expected_frozen_session_ids,
        )
        observed.graphiti_search_calls += 1
        search_results = await graph.search_(
            query_value,
            config=search_config,
            group_ids=[namespace_value],
        )

    returned = getattr(search_results, "episodes", None)
    if not isinstance(returned, list):
        raise RuntimeError("episode surface returned an invalid result shape")
    if len(returned) > top_k:
        raise RuntimeError("episode surface returned more than top_k results")
    ranked_sessions: list[str] = []
    for result in returned:
        uuid = str(getattr(result, "uuid", ""))
        session_id = corpus.uuid_to_session_id.get(uuid)
        if session_id is None:
            raise RuntimeError("episode surface result UUID mapping failed")
        if str(getattr(result, "group_id", "")) != namespace_value:
            raise RuntimeError("episode surface result escaped the frozen namespace")
        if session_id in ranked_sessions:
            raise RuntimeError("episode surface returned duplicate session ranks")
        ranked_sessions.append(session_id)

    retrieved = set(ranked_sessions)
    gold_set = set(gold)
    covered = len(retrieved.intersection(gold_set))
    coverage_fraction = covered / len(gold_set)
    recall_any = 1.0 if covered > 0 else 0.0
    recall_all = 1.0 if covered == len(gold_set) else 0.0
    comparison = classify_surface_comparison(
        edge_attributed_source_session_coverage=(
            edge_attributed_source_session_coverage
        ),
        episode_session_recall_any=recall_any,
        episode_session_recall_all=recall_all,
    )
    snapshot = observed.snapshot()
    _validate_success_counters(snapshot)
    return EpisodeSurfaceProbeResult(
        retrieved_session_ids=tuple(ranked_sessions),
        gold_session_ids=gold,
        covered_gold_session_count=covered,
        session_recall_any_at_10=recall_any,
        session_recall_all_at_10=recall_all,
        session_gold_coverage_fraction_at_10=coverage_fraction,
        edge_attributed_source_session_coverage=float(
            edge_attributed_source_session_coverage
        ),
        classification=str(comparison["classification"]),
        node_surface_status=str(comparison["node_surface_status"]),
        multi_surface_status=str(comparison["multi_surface_status"]),
        corpus_completeness_pass=True,
        observed_session_count=corpus.observed_session_count,
        frozen_corpus_identity_sha256=expected_corpus_identity_sha256,
        expected_name_content_map_sha256=(
            corpus.expected_name_content_map_sha256
        ),
        observed_name_content_map_sha256=(
            corpus.observed_name_content_map_sha256
        ),
        retrieval_config_identity=search_config_identity(search_config),
        query_sha256=_text_sha256(query_value),
        query_character_count=len(query_value),
        counters=snapshot,
    )


def _validate_result(result: EpisodeSurfaceProbeResult) -> dict[str, Any]:
    if not isinstance(result, EpisodeSurfaceProbeResult):
        raise RetrievalContractError("probe result has an invalid type")
    retrieved = result.retrieved_session_ids
    gold = result.gold_session_ids
    if (
        len(retrieved) > 10
        or len(set(retrieved)) != len(retrieved)
        or not gold
        or len(set(gold)) != len(gold)
    ):
        raise RetrievalContractError("probe result session identity is invalid")
    covered = len(set(retrieved).intersection(gold))
    coverage = covered / len(gold)
    recall_any = 1.0 if covered else 0.0
    recall_all = 1.0 if covered == len(gold) else 0.0
    comparison = classify_surface_comparison(
        edge_attributed_source_session_coverage=(
            result.edge_attributed_source_session_coverage
        ),
        episode_session_recall_any=recall_any,
        episode_session_recall_all=recall_all,
    )
    if (
        result.covered_gold_session_count != covered
        or result.session_gold_coverage_fraction_at_10 != coverage
        or result.session_recall_any_at_10 != recall_any
        or result.session_recall_all_at_10 != recall_all
        or result.classification != comparison["classification"]
        or result.node_surface_status != comparison["node_surface_status"]
        or result.multi_surface_status != comparison["multi_surface_status"]
        or result.corpus_completeness_pass is not True
        or result.expected_name_content_map_sha256
        != result.observed_name_content_map_sha256
    ):
        raise RetrievalContractError("probe metric consistency check failed")
    _validate_success_counters(result.counters)
    for value, field in (
        (result.frozen_corpus_identity_sha256, "frozen corpus identity"),
        (result.expected_name_content_map_sha256, "expected content map"),
        (result.observed_name_content_map_sha256, "observed content map"),
        (result.query_sha256, "query identity"),
    ):
        _hash(value, field=field)
    return comparison


def finalize_episode_surface_probe(
    output_path: Path,
    *,
    run_id: str,
    history_id: str,
    namespace: str,
    result: EpisodeSurfaceProbeResult,
    reference_sanity_sha256: str,
    authorization_sha256: str,
    dataset_sha256: str,
    frozen_split_sha256: str,
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Seal internally consistent, content-minimized evidence exactly once."""

    path = Path(output_path)
    if path.exists():
        raise RetrievalContractError("S2-R0 result already exists")
    for value, field in (
        (run_id, "run_id"),
        (history_id, "history_id"),
        (namespace, "namespace"),
        (git_commit, "git_commit"),
    ):
        _nonempty(value, field=field)
    for value, field in (
        (reference_sanity_sha256, "reference sanity"),
        (authorization_sha256, "authorization"),
        (dataset_sha256, "dataset"),
        (frozen_split_sha256, "frozen split"),
    ):
        _hash(value, field=field)
    if not isinstance(source_sha256, Mapping) or not source_sha256:
        raise RetrievalContractError("source hash binding is incomplete")
    sources = {
        _nonempty(key, field="source hash name"): _hash(value, field="source hash")
        for key, value in source_sha256.items()
    }
    comparison = _validate_result(result)
    counters = result.counters.to_artifact()
    payload = {
        "schema_version": "membind.paper-eval-v3.s2-r0-episode-probe.v2",
        "stage": "S2-R0",
        "status": "READ_ONLY_RETRIEVAL_SURFACE_DIAGNOSTIC",
        "history_id": history_id,
        "namespace": namespace,
        **SESSION_SURFACE_CONTRACT.to_identity(),
        "top_k": 10,
        "retrieval_config": dict(result.retrieval_config_identity),
        "retrieval_config_sha256": payload_sha256(result.retrieval_config_identity),
        "retrieved_session_count": len(result.retrieved_session_ids),
        "retrieved_session_ids": list(result.retrieved_session_ids),
        "retrieved_session_ids_sha256": payload_sha256(
            list(result.retrieved_session_ids)
        ),
        "gold_session_count": len(result.gold_session_ids),
        "gold_session_ids": list(result.gold_session_ids),
        "gold_session_ids_sha256": payload_sha256(list(result.gold_session_ids)),
        "covered_gold_session_count": result.covered_gold_session_count,
        "session_recall_any_at_10": result.session_recall_any_at_10,
        "session_recall_all_at_10": result.session_recall_all_at_10,
        "session_gold_coverage_fraction_at_10": (
            result.session_gold_coverage_fraction_at_10
        ),
        "edge_attributed_source_session_coverage": (
            result.edge_attributed_source_session_coverage
        ),
        "classification": result.classification,
        "node_surface_status": result.node_surface_status,
        "multi_surface_status": result.multi_surface_status,
        "whole_graph_quality_conclusion": comparison[
            "whole_graph_quality_conclusion"
        ],
        "corpus_completeness_pass": True,
        "observed_session_count": result.observed_session_count,
        "frozen_corpus_identity_sha256": result.frozen_corpus_identity_sha256,
        "expected_name_content_map_sha256": (
            result.expected_name_content_map_sha256
        ),
        "observed_name_content_map_sha256": (
            result.observed_name_content_map_sha256
        ),
        "query_sha256": result.query_sha256,
        "query_character_count": result.query_character_count,
        "result_sealed_before_policy_freeze": True,
        "retrieval_policy_selected": False,
        "s3_authorized": False,
        "neo4j_auto_schema_initialization": False,
        "driver_init_task_present": False,
        "driver_routing_policy": "read_only",
        **counters,
        "reference_sanity_sha256": reference_sanity_sha256,
        "authorization_sha256": authorization_sha256,
        "dataset_sha256": dataset_sha256,
        "frozen_split_sha256": frozen_split_sha256,
        "source_sha256": sources,
    }
    artifact = finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    atomic_write_json(path, artifact)
    return artifact
