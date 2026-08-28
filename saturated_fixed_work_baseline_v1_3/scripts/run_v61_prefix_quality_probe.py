#!/usr/bin/env python3
"""Run a sealed read-only Quality-v1 probe over completed V6.1 prefix attempts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
PAPER = ROOT / "paper-eval-v3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", PAPER / "src", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import load_main_contexts  # noqa: E402
from paper_eval.artifacts import payload_sha256  # noqa: E402
from paper_eval.quality_evaluation_v1_retrieval import retrieve_quality_v1  # noqa: E402
from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard  # noqa: E402


PROFILE_ID = "local-qwen3-8b-awq-dualreplica-v1"
EXPECTED_CONTEXT_INDEX = 0


class PrefixQualityProbeError(RuntimeError):
    """A candidate or runtime cannot satisfy the read-only probe contract."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrefixQualityProbeError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PrefixQualityProbeError(f"JSON artifact is not an object: {path}")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _records(result: Any) -> list[dict[str, Any]]:
    values = getattr(result, "records", None)
    if values is None and isinstance(result, tuple) and result:
        values = result[0]
    if values is None and isinstance(result, list):
        values = result
    if not isinstance(values, list):
        raise PrefixQualityProbeError("Neo4j query returned an invalid result shape")
    return [value if isinstance(value, dict) else dict(value) for value in values]


def _candidate(path: Path, expected_episode_count: int) -> dict[str, Any]:
    root = path.resolve()
    attempt = _read_json(root / "attempt.json")
    complete = _read_json(root / "complete.json")
    graph_path = root / "block/graph_diagnostics.json"
    graph = _read_json(graph_path)
    if (
        attempt.get("profile_id") != PROFILE_ID
        or attempt.get("context_index") != EXPECTED_CONTEXT_INDEX
        or attempt.get("method") != "V6_1"
        or attempt.get("episode_count") != expected_episode_count
        or complete.get("status") != "PASS"
    ):
        raise PrefixQualityProbeError(f"attempt is not a completed V6.1 prefix-4 run: {root}")
    namespace = attempt.get("namespace")
    episodes = graph.get("episodes")
    if not isinstance(namespace, str) or not namespace or not isinstance(episodes, list):
        raise PrefixQualityProbeError(f"candidate namespace/episodes are invalid: {root}")
    if len(episodes) != expected_episode_count or {
        row.get("source_sequence") for row in episodes
    } != set(range(expected_episode_count)):
        raise PrefixQualityProbeError(
            f"candidate episode coverage is not prefix-{expected_episode_count}: {root}"
        )
    graph_namespaces = {
        row.get("group_id")
        for field in ("entities", "edges")
        for row in graph.get(field, [])
        if isinstance(row, Mapping) and row.get("group_id") is not None
    }
    if graph_namespaces != {namespace}:
        raise PrefixQualityProbeError(f"canonical graph namespace mismatch: {root}")
    return {
        "attempt_id": str(attempt["attempt_id"]),
        "run_id": str(attempt["run_id"]),
        "root": str(root),
        "namespace": namespace,
        "canonical_graph_path": str(graph_path),
        "canonical_graph_sha256": _file_sha256(graph_path),
        "canonical_graph_hash": graph.get("canonical_graph_hash"),
    }


def _build_read_only_runtime() -> Any:
    from paper_eval import graph_quality_live

    embedding_base = os.environ.get("EMBEDDING_BASE_URL", "").rstrip("/")
    embedding_model = os.environ.get("EMBEDDING_MODEL", "")
    neo4j_uri = os.environ.get("NEO4J_URI", "")
    if not embedding_base or not embedding_model or not neo4j_uri:
        raise PrefixQualityProbeError("activated embedding/Neo4j identity is incomplete")
    graph_quality_live.NEO4J_URI = neo4j_uri
    graph_quality_live.EMBEDDING_BASE_URL = embedding_base
    graph_quality_live.EMBEDDING_MODEL = embedding_model
    graph_quality_live.EMBEDDING_DIMENSION = int(os.environ["EMBEDDING_DIM"])
    runtime = graph_quality_live.build_graph_quality_runtime(env=dict(os.environ))
    if getattr(runtime.graphiti.driver, "_init_task", None) is not None:
        raise PrefixQualityProbeError("read-only driver scheduled schema initialization")
    return runtime


async def _namespace_snapshot(graphiti: Any, namespace: str) -> tuple[dict[str, int], int]:
    query = """
    MATCH (n) WHERE n.group_id = $group_id
    WITH count(n) AS node_count
    OPTIONAL MATCH ()-[r]->() WHERE r.group_id = $group_id
    RETURN node_count, count(r) AS relationship_count
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        rows = _records(
            await graphiti.driver.execute_query(
                query, params={"group_id": namespace}, routing_="r"
            )
        )
    if len(rows) != 1:
        raise PrefixQualityProbeError("namespace snapshot returned an invalid row count")
    return {
        "node_count": int(rows[0]["node_count"]),
        "relationship_count": int(rows[0]["relationship_count"]),
    }, counters.neo4j_read_requests


async def _episode_mapping(
    graphiti: Any,
    *,
    namespace: str,
    expected_name_to_session: Mapping[str, str],
) -> tuple[dict[str, str], int]:
    query = """
    MATCH (e:Episodic) WHERE e.group_id = $group_id
    RETURN e.uuid AS uuid, e.name AS name, e.group_id AS group_id
    ORDER BY e.name, e.uuid
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        rows = _records(
            await graphiti.driver.execute_query(
                query, params={"group_id": namespace}, routing_="r"
            )
        )
    mapping: dict[str, str] = {}
    for row in rows:
        name = str(row.get("name") or "")
        uuid = str(row.get("uuid") or "")
        if (
            name not in expected_name_to_session
            or not uuid
            or uuid in mapping
            or row.get("group_id") != namespace
        ):
            raise PrefixQualityProbeError("persisted episode mapping escaped the prefix contract")
        mapping[uuid] = expected_name_to_session[name]
    if len(mapping) != len(expected_name_to_session):
        raise PrefixQualityProbeError(
            f"persisted episode mapping is incomplete: {len(mapping)}/{len(expected_name_to_session)}"
        )
    return mapping, counters.neo4j_read_requests


def _fact_artifact(fact: Any) -> dict[str, Any]:
    semantic = {
        "rank": int(fact.retrieval_rank),
        "relation": str(fact.relation_name),
        "fact": str(fact.fact),
        "source_session_ids": list(fact.source_session_ids),
        "valid_at": fact.valid_at,
        "invalid_at": fact.invalid_at,
        "expired_at": fact.expired_at,
    }
    return {**semantic, "semantic_sha256": payload_sha256(semantic)}


async def _run(
    *,
    runtime: Any,
    candidates: list[dict[str, Any]],
    output_root: Path,
    expected_episode_count: int,
) -> int:
    contexts = load_main_contexts(MAB / "data/official_5_contexts.json")
    context = contexts[EXPECTED_CONTEXT_INDEX]
    prefix_sessions = tuple(context.sessions[:expected_episode_count])
    prefix_session_ids = {session.session_id for session in prefix_sessions}
    questions = [
        qa for qa in context.qa_items if prefix_session_ids.intersection(qa.gold_session_ids)
    ]
    if not questions:
        raise PrefixQualityProbeError(
            f"prefix-{expected_episode_count} has no addressable quality questions"
        )
    expected_name_to_session = {
        f"{context.context_id}::episode::{session.source_sequence:04d}": session.session_id
        for session in prefix_sessions
    }
    rows: list[dict[str, Any]] = []
    total_reads = 0
    snapshots: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        namespace = str(candidate["namespace"])
        before, reads = await _namespace_snapshot(runtime.graphiti, namespace)
        total_reads += reads
        mapping, reads = await _episode_mapping(
            runtime.graphiti,
            namespace=namespace,
            expected_name_to_session=expected_name_to_session,
        )
        total_reads += reads
        for qa in questions:
            retrieval = await retrieve_quality_v1(
                graph=runtime.graphiti,
                query=qa.question,
                namespace=namespace,
                episode_uuid_to_session_id=mapping,
            )
            total_reads += retrieval.neo4j_read_requests
            ranked = [episode.session_id for episode in retrieval.episodes]
            prefix_gold = [value for value in qa.gold_session_ids if value in prefix_session_ids]
            rows.append(
                {
                    "attempt_id": candidate["attempt_id"],
                    "run_id": candidate["run_id"],
                    "namespace": namespace,
                    "question_id": qa.question_id,
                    "question_type": qa.question_type,
                    "question_sha256": hashlib.sha256(qa.question.encode("utf-8")).hexdigest(),
                    "search_config_sha256": retrieval.search_config_sha256,
                    "ranked_session_ids": ranked,
                    "episode_ranking_sha256": payload_sha256(ranked),
                    "prefix_gold_session_ids_posthoc": prefix_gold,
                    "prefix_gold_ranks_posthoc": [
                        rank
                        for rank, session_id in enumerate(ranked, start=1)
                        if session_id in set(prefix_gold)
                    ],
                    "top_facts": [_fact_artifact(fact) for fact in retrieval.facts],
                    "top_fact_semantic_sha256": payload_sha256(
                        [_fact_artifact(fact)["semantic_sha256"] for fact in retrieval.facts]
                    ),
                    "graphiti_search_calls": retrieval.graphiti_search_calls,
                    "neo4j_read_requests": retrieval.neo4j_read_requests,
                    "gold_inputs_during_retrieval": False,
                }
            )
        after, reads = await _namespace_snapshot(runtime.graphiti, namespace)
        total_reads += reads
        if before != after:
            raise PrefixQualityProbeError(f"namespace changed during read-only probe: {namespace}")
        snapshots[str(candidate["attempt_id"])] = {"before": before, "after": after}

    question_ids = sorted({row["question_id"] for row in rows})
    comparisons: list[dict[str, Any]] = []
    for question_id in question_ids:
        selected = [row for row in rows if row["question_id"] == question_id]
        comparisons.append(
            {
                "question_id": question_id,
                "episode_rankings_exact": len(
                    {row["episode_ranking_sha256"] for row in selected}
                )
                == 1,
                "top_facts_exact": len(
                    {row["top_fact_semantic_sha256"] for row in selected}
                )
                == 1,
                "prefix_gold_ranks_exact": len(
                    {tuple(row["prefix_gold_ranks_posthoc"]) for row in selected}
                )
                == 1,
            }
        )
    status = (
        "PASS"
        if all(
            row["episode_rankings_exact"] and row["prefix_gold_ranks_exact"]
            for row in comparisons
        )
        else "FAIL"
    )
    result = {
        "schema_version": "membind.v6.1.prefix-quality-v1.v1",
        "status": status,
        "profile_id": PROFILE_ID,
        "context_index": EXPECTED_CONTEXT_INDEX,
        "context_id": context.context_id,
        "prefix_episode_count": expected_episode_count,
        "candidate_count": len(candidates),
        "question_count": len(questions),
        "candidates": candidates,
        "rows": rows,
        "comparisons": comparisons,
        "runtime": {
            "public_identity": runtime.public_identity,
            "driver_init_task_present": getattr(runtime.graphiti.driver, "_init_task", None)
            is not None,
            "construction_llm_requests": 0,
            "graph_writes": 0,
            "neo4j_read_requests": total_reads,
            "namespace_snapshots": snapshots,
        },
        "completed_at_unix": time.time(),
    }
    result["result_sha256"] = payload_sha256(result)
    _write_new_json(output_root / "quality_v1_results.json", result)
    print(
        json.dumps(
            {
                "status": status,
                "output": str(output_root),
                "result_sha256": result["result_sha256"],
                "comparisons": comparisons,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if status == "PASS" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt-root", type=Path, action="append", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--expected-episodes", type=int, default=4)
    args = parser.parse_args()
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        parser.error("activate scripts/local_runtime_8b_dual/activate.sh first")
    if args.expected_episodes <= 0:
        parser.error("--expected-episodes must be positive")
    candidates = [
        _candidate(path, args.expected_episodes) for path in args.attempt_root
    ]
    if len(candidates) < 2 or len({row["namespace"] for row in candidates}) != len(candidates):
        parser.error("at least two candidates with distinct namespaces are required")
    output_root = args.output_root.resolve()
    experiment_root = Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]).resolve()
    if experiment_root != output_root and experiment_root not in output_root.parents:
        parser.error("output root must remain inside the activated experiment root")
    output_root.mkdir(parents=True, exist_ok=False)
    runtime = _build_read_only_runtime()

    async def execute() -> int:
        try:
            return await _run(
                runtime=runtime,
                candidates=candidates,
                output_root=output_root,
                expected_episode_count=args.expected_episodes,
            )
        finally:
            await runtime.aclose()

    return asyncio.run(execute())


if __name__ == "__main__":
    raise SystemExit(main())
