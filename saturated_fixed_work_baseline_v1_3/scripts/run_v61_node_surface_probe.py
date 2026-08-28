#!/usr/bin/env python3
"""Run a sealed read-only node-surface and grounded-summary probe."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
PAPER = ROOT / "paper-eval-v3"
for source in (SFWB / "src", PAPER / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from paper_eval.artifacts import payload_sha256  # noqa: E402
from paper_eval.s2_retrieval_probe import ProbeCounters, _read_only_query_guard  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.graphiti_compat import (  # noqa: E402
    _node_names_compatible,
)


PROFILE_ID = "local-qwen3-8b-awq-dualreplica-v1"
EXPECTED_CONTEXT_INDEX = 0
NODE_QUERIES = (
    "Spirit Airlines",
    "Notion",
    "Staples Center",
    "reading challenge",
    "TBR list",
    "book journal",
)


class NodeSurfaceProbeError(RuntimeError):
    """The candidate or read-only node probe contract is invalid."""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NodeSurfaceProbeError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise NodeSurfaceProbeError(f"JSON artifact is not an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NodeSurfaceProbeError(f"invalid JSONL artifact: {path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise NodeSurfaceProbeError(f"JSONL artifact contains a non-object: {path}")
    return rows


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
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
        raise NodeSurfaceProbeError("Neo4j query returned an invalid result shape")
    return [value if isinstance(value, dict) else dict(value) for value in values]


def _candidate(path: Path, expected_episode_count: int) -> dict[str, Any]:
    root = path.resolve()
    attempt = _read_json(root / "attempt.json")
    complete = _read_json(root / "complete.json")
    frozen = _read_json(root / "block/frozen_config.json")
    graph_path = root / "block/graph_diagnostics.json"
    graph = _read_json(graph_path)
    if (
        attempt.get("profile_id") != PROFILE_ID
        or attempt.get("context_index") != EXPECTED_CONTEXT_INDEX
        or attempt.get("method") != "V6_1"
        or attempt.get("episode_count") != expected_episode_count
        or complete.get("status") != "PASS"
    ):
        raise NodeSurfaceProbeError(f"attempt is not a completed V6.1 prefix-4 run: {root}")
    namespace = attempt.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        raise NodeSurfaceProbeError(f"candidate namespace is invalid: {root}")
    summary_policy = frozen.get("construction", {}).get("entity_summary_policy")
    grounded = isinstance(summary_policy, str) and summary_policy.startswith(
        "provenance_grounded_incremental_materialized_summary_"
    )
    return {
        "attempt_id": str(attempt["attempt_id"]),
        "run_id": str(attempt["run_id"]),
        "root": str(root),
        "namespace": namespace,
        "summary_policy": summary_policy,
        "grounded_summary_required": grounded,
        "graph": graph,
        "graph_sha256": _file_sha256(graph_path),
        "extraction_diagnostics": _read_jsonl(root / "extraction_diagnostics.jsonl"),
        "provider_calls": _read_jsonl(root / "block/provider_calls.jsonl"),
    }


def _build_read_only_runtime() -> Any:
    from paper_eval import graph_quality_live

    graph_quality_live.NEO4J_URI = os.environ["NEO4J_URI"]
    graph_quality_live.EMBEDDING_BASE_URL = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
    graph_quality_live.EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
    graph_quality_live.EMBEDDING_DIMENSION = int(os.environ["EMBEDDING_DIM"])
    runtime = graph_quality_live.build_graph_quality_runtime(env=dict(os.environ))
    if getattr(runtime.graphiti.driver, "_init_task", None) is not None:
        raise NodeSurfaceProbeError("read-only driver scheduled schema initialization")
    return runtime


async def _query_namespace(graphiti: Any, namespace: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    node_query = """
    MATCH (n:Entity) WHERE n.group_id = $group_id
    OPTIONAL MATCH (n)-[r:RELATES_TO]-(:Entity) WHERE r.group_id = $group_id
    RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary,
           n.name_embedding AS name_embedding, count(r) AS degree
    ORDER BY n.name, n.uuid
    """
    episode_query = """
    MATCH (e:Episodic) WHERE e.group_id = $group_id
    RETURN e.uuid AS uuid, e.name AS name, e.content AS content
    ORDER BY e.name, e.uuid
    """
    counters = ProbeCounters()
    with _read_only_query_guard(graphiti.driver, counters):
        nodes = _records(
            await graphiti.driver.execute_query(
                node_query, params={"group_id": namespace}, routing_="r"
            )
        )
        episodes = _records(
            await graphiti.driver.execute_query(
                episode_query, params={"group_id": namespace}, routing_="r"
            )
        )
    return nodes, episodes, counters.neo4j_read_requests


async def _namespace_counts(graphiti: Any, namespace: str) -> tuple[dict[str, int], int]:
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
        raise NodeSurfaceProbeError("namespace count query returned an invalid result")
    return {
        "node_count": int(rows[0]["node_count"]),
        "relationship_count": int(rows[0]["relationship_count"]),
    }, counters.neo4j_read_requests


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _rank_nodes(
    nodes: Sequence[Mapping[str, Any]], query: str, vector: Sequence[float]
) -> dict[str, Any]:
    ranked = []
    for node in nodes:
        embedding = node.get("name_embedding")
        if not isinstance(embedding, list) or len(embedding) != len(vector):
            raise NodeSurfaceProbeError("persisted node embedding has invalid dimensions")
        ranked.append((str(node.get("name") or ""), _cosine(vector, embedding)))
    ranked.sort(key=lambda value: (-value[1], _canonical_name(value[0])))
    top = ranked[:5]
    compatible_ranks = [
        index
        for index, (name, _) in enumerate(ranked, start=1)
        if _node_names_compatible(query, name)
    ]
    return {
        "query": query,
        "query_sha256": _sha256(query),
        "top_entities": [
            {"name": name, "cosine": round(score, 8)} for name, score in top
        ],
        "compatible_ranks": compatible_ranks[:10],
        "compatible_in_top3": bool(compatible_ranks and compatible_ranks[0] <= 3),
    }


def _grounding_check(
    candidate: Mapping[str, Any],
    nodes: Sequence[Mapping[str, Any]],
    episodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    node_events = [
        row
        for row in candidate["extraction_diagnostics"]
        if row.get("event") == "GROUNDED_SUMMARY_NODE"
    ]
    latest = {
        str(row.get("node_name_canonical_sha256")): row
        for row in node_events
        if row.get("node_name_canonical_sha256")
    }
    episode_content_by_hash = {
        _sha256(str(row.get("content") or "")): str(row.get("content") or "")
        for row in episodes
    }
    missing_evidence: list[str] = []
    summary_hash_mismatch: list[str] = []
    unit_order_mismatch: list[str] = []
    source_mismatch: list[str] = []
    degree_zero_without_episode_span: list[str] = []
    verified_units = 0
    for node in nodes:
        name = str(node.get("name") or "")
        summary = str(node.get("summary") or "")
        name_hash = _sha256(_canonical_name(name))
        event = latest.get(name_hash)
        if event is None:
            missing_evidence.append(name)
            continue
        if event.get("selected_summary_sha256") != _sha256(summary):
            summary_hash_mismatch.append(name)
            continue
        units = event.get("selected_units")
        if not isinstance(units, list):
            unit_order_mismatch.append(name)
            continue
        unit_texts: list[str] = []
        cursor = 0
        boundary_valid = True
        for index, unit in enumerate(units):
            if index:
                if cursor >= len(summary) or summary[cursor] != "\n":
                    boundary_valid = False
                    break
                cursor += 1
            chars = unit.get("chars")
            if not isinstance(chars, int) or chars < 0 or cursor + chars > len(summary):
                boundary_valid = False
                break
            unit_texts.append(summary[cursor : cursor + chars])
            cursor += chars
        if cursor != len(summary):
            boundary_valid = False
        if not boundary_valid or any(
            str(unit.get("span_sha256") or "") != _sha256(text)
            for text, unit in zip(unit_texts, units, strict=True)
        ):
            unit_order_mismatch.append(name)
            continue
        has_episode_span = False
        for unit_text, unit in zip(unit_texts, units, strict=True):
            provenance = unit.get("provenance_kind")
            source_hash = str(unit.get("source_sha256") or "")
            if provenance == "edge_fact":
                valid = source_hash == _sha256(unit_text)
            elif provenance == "episode_span":
                has_episode_span = True
                source = episode_content_by_hash.get(source_hash)
                valid = source is not None and unit_text in source
            else:
                valid = False
            if not valid:
                source_mismatch.append(name)
            else:
                verified_units += 1
        if int(node.get("degree") or 0) == 0 and not has_episode_span:
            degree_zero_without_episode_span.append(name)
    status = "PASS" if not (
        missing_evidence
        or summary_hash_mismatch
        or unit_order_mismatch
        or source_mismatch
        or degree_zero_without_episode_span
    ) else "FAIL"
    return {
        "status": status,
        "final_node_count": len(nodes),
        "node_evidence_event_count": len(node_events),
        "canonical_node_evidence_count": len(latest),
        "verified_unit_count": verified_units,
        "degree_zero_node_count": sum(int(row.get("degree") or 0) == 0 for row in nodes),
        "missing_evidence_names": missing_evidence,
        "summary_hash_mismatch_names": summary_hash_mismatch,
        "unit_order_mismatch_names": unit_order_mismatch,
        "source_mismatch_names": sorted(set(source_mismatch)),
        "degree_zero_without_episode_span_names": degree_zero_without_episode_span,
        "content_omitted": True,
    }


def _provider_node_resolution(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_sequence": row.get("source_sequence"),
            "request_digest_prefix": row.get("request_digest_prefix"),
            "response_sha256": row.get("response_sha256"),
            "transport_attempt_count": row.get("transport_attempt_count"),
        }
        for row in candidate["provider_calls"]
        if row.get("callsite") == "dedupe_nodes.nodes"
    ]


async def _run(
    runtime: Any,
    candidates: list[dict[str, Any]],
    output_root: Path,
    expected_episode_count: int,
) -> int:
    query_vectors = await runtime.graphiti.embedder.create_batch(list(NODE_QUERIES))
    if len(query_vectors) != len(NODE_QUERIES):
        raise NodeSurfaceProbeError("embedding batch returned an invalid result count")
    results = []
    total_reads = 0
    for candidate in candidates:
        namespace = str(candidate["namespace"])
        before, reads = await _namespace_counts(runtime.graphiti, namespace)
        total_reads += reads
        nodes, episodes, reads = await _query_namespace(runtime.graphiti, namespace)
        total_reads += reads
        rankings = [
            _rank_nodes(nodes, query, vector)
            for query, vector in zip(NODE_QUERIES, query_vectors, strict=True)
        ]
        grounded = (
            _grounding_check(candidate, nodes, episodes)
            if candidate["grounded_summary_required"]
            else {"status": "NOT_APPLICABLE", "reason": "native_summary_policy"}
        )
        after, reads = await _namespace_counts(runtime.graphiti, namespace)
        total_reads += reads
        if before != after:
            raise NodeSurfaceProbeError(f"namespace changed during probe: {namespace}")
        summary_by_name = {
            _canonical_name(str(row.get("name") or "")): str(row.get("summary") or "")
            for row in nodes
        }
        target_summaries = []
        for query in NODE_QUERIES:
            compatible = [
                (name, summary)
                for name, summary in summary_by_name.items()
                if _node_names_compatible(query, name)
            ]
            target_summaries.append(
                {
                    "query": query,
                    "compatible_entity_count": len(compatible),
                    "nonempty_summary_count": sum(bool(summary) for _, summary in compatible),
                    "summary_sha256": sorted(_sha256(summary) for _, summary in compatible),
                }
            )
        results.append(
            {
                "attempt_id": candidate["attempt_id"],
                "run_id": candidate["run_id"],
                "namespace": namespace,
                "summary_policy": candidate["summary_policy"],
                "graph_sha256": candidate["graph_sha256"],
                "entity_count": len(nodes),
                "episode_count": len(episodes),
                "name_rankings": rankings,
                "target_summaries": target_summaries,
                "grounding": grounded,
                "node_resolution_provider_calls": _provider_node_resolution(candidate),
                "namespace_counts_before": before,
                "namespace_counts_after": after,
            }
        )

    reference_names = {
        _canonical_name(str(row.get("name") or ""))
        for row in candidates[0]["graph"].get("entities", [])
    }
    graph_diffs = []
    for candidate in candidates[1:]:
        names = {
            _canonical_name(str(row.get("name") or ""))
            for row in candidate["graph"].get("entities", [])
        }
        graph_diffs.append(
            {
                "reference_attempt_id": candidates[0]["attempt_id"],
                "candidate_attempt_id": candidate["attempt_id"],
                "candidate_only_entity_names": sorted(names - reference_names),
                "reference_only_entity_names": sorted(reference_names - names),
            }
        )
    status = "PASS"
    for result in results:
        if not all(row["compatible_in_top3"] for row in result["name_rankings"]):
            status = "FAIL"
        if any(
            row["compatible_entity_count"] < 1 or row["nonempty_summary_count"] < 1
            for row in result["target_summaries"]
        ):
            status = "FAIL"
        if result["grounding"]["status"] == "FAIL":
            status = "FAIL"
    output = {
        "schema_version": "membind.v6.1.node-surface-grounding-probe.v1",
        "status": status,
        "profile_id": PROFILE_ID,
        "context_index": EXPECTED_CONTEXT_INDEX,
        "prefix_episode_count": expected_episode_count,
        "queries": list(NODE_QUERIES),
        "candidate_count": len(candidates),
        "results": results,
        "graph_diffs": graph_diffs,
        "runtime": {
            "public_identity": runtime.public_identity,
            "driver_init_task_present": getattr(runtime.graphiti.driver, "_init_task", None)
            is not None,
            "construction_llm_requests": 0,
            "cross_encoder_requests": 0,
            "embedding_requests": 1,
            "embedding_items": len(NODE_QUERIES),
            "neo4j_read_requests": total_reads,
            "graph_writes": 0,
        },
        "completed_at_unix": time.time(),
    }
    output["result_sha256"] = payload_sha256(output)
    _write_new_json(output_root / "node_surface_results.json", output)
    print(
        json.dumps(
            {
                "status": status,
                "output": str(output_root),
                "result_sha256": output["result_sha256"],
                "grounding": [row["grounding"]["status"] for row in results],
                "graph_diffs": graph_diffs,
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
                runtime, candidates, output_root, args.expected_episodes
            )
        finally:
            await runtime.aclose()

    return asyncio.run(execute())


if __name__ == "__main__":
    raise SystemExit(main())
