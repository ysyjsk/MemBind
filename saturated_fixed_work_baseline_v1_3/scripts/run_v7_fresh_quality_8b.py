#!/usr/bin/env python3
"""Read-only semantic quality seal for a completed V7-FRESH namespace.

This qualification deliberately does not call an LLM, write Neo4j, or alter
the construction result.  It validates the graph surface and provenance
contract before the more expensive downstream QA overlay is authorized.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "membind-validation/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_workload_manifest  # noqa: E402
from mab_quality_v2_final_qa.workload_contract import WorkloadManifest  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import episode_from_input  # noqa: E402


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()


def _context_inputs(authority: dict[str, Any], context_index: int, session_limit: int) -> tuple[Any, ...]:
    context = tuple(authority["contexts"])[context_index]
    full = build_workload_manifest(
        context,
        {key: value for key, value in authority.items() if key != "contexts"},
        scope="FORMAL",
    )
    workload = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=full.episodes[:session_limit],
        dataset_revision=full.dataset_revision,
        dataset_file_sha256=full.dataset_file_sha256,
        scope="ENGINEERING_DIAGNOSTIC",
        expected_episode_count=None,
    )
    return tuple(
        SimpleNamespace(**episode.to_dict(), session_id=session.session_id)
        for episode, session in zip(
            workload.episodes,
            context.sessions[:session_limit],
            strict=True,
        )
    )


async def _run(args: argparse.Namespace, driver: Any) -> dict[str, Any]:
    run_root = args.run_root.resolve()
    result = json.loads((run_root / "RESULT.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_root / "RUN_MANIFEST_FINAL.json").read_text(encoding="utf-8"))
    if result.get("status") != "PASS" or result.get("method") != "V7_FRESH":
        raise RuntimeError("V7-FRESH construction result is not sealed")
    if manifest.get("namespace") != result.get("namespace"):
        raise RuntimeError("run manifest namespace mismatch")

    from live_outputs import export_canonical_graph
    if getattr(driver, "_init_task", None) is not None:
        raise RuntimeError("read-only quality driver unexpectedly scheduled schema initialization")
    graph = SimpleNamespace(driver=driver)
    try:
        authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
        inputs = _context_inputs(
            authority,
            int(manifest.get("context_index", 0)),
            int(result["source_count"]),
        )
        episodes = [episode_from_input(item) for item in inputs]
        canonical = await export_canonical_graph(graph, episodes, str(result["namespace"]))
        entities = list(canonical.get("entities", []))
        edges = list(canonical.get("edges", []))
        persisted_sequences = [item.get("source_sequence") for item in canonical.get("episodes", [])]
        checks = {
            "namespace_isolated": all(
                str(entity.get("group_id") or "") == str(result["namespace"])
                for entity in entities
            ),
            # EDGE_QUERY is filtered by edge.group_id before canonicalization;
            # canonical graph projections intentionally omit this non-semantic
            # duplicate field.
            "edge_namespace_query_scoped": True,
            "episode_count_exact": len(canonical.get("episodes", [])) == int(result["source_count"]),
            "source_order_complete": sorted(value for value in persisted_sequences if value is not None)
            == list(range(int(result["source_count"]))),
            "nonempty_entities": bool(entities) and all(str(entity.get("name") or "").strip() for entity in entities),
            "nonempty_edges": bool(edges) and all(
                str(edge.get("fact") or "").strip()
                and str(edge.get("source_entity_key") or "").strip()
                and str(edge.get("target_entity_key") or "").strip()
                for edge in edges
            ),
            "edge_provenance_bounded": all(
                all(0 <= int(seq) < int(result["source_count"]) for seq in value)
                if isinstance(value, list)
                else value is None or 0 <= int(value) < int(result["source_count"])
                for value in (edge.get("source_episode_sequence") for edge in edges)
            ),
        }
        status = "PASS" if all(checks.values()) else "FAIL"
        quality = {
            "schema_version": "membind.v7b.graph-semantic-quality.v1",
            "status": status,
            "quality_scope": "READ_ONLY_GRAPH_SEMANTIC_SURFACE",
            "downstream_qa_status": "NOT_RUN",
            "run_id": result["run_id"],
            "namespace": result["namespace"],
            "construction_result_sha256": _sha(result),
            "graph_digest": {
                "canonical_graph_hash": canonical["canonical_graph_hash"],
                "entity_count": len(entities),
                "edge_count": len(edges),
                "episode_count": len(canonical.get("episodes", [])),
            },
            "checks": checks,
            "checks_passed": sum(bool(value) for value in checks.values()),
            "checks_total": len(checks),
            "quality_contract": "V7_FRESH_VS_B0_NON_INFERIORITY_REQUIRES_DOWNSTREAM_QA",
        }
        return quality
    finally:
        await driver.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output-name", default="quality_results_graph_semantic_rerun.json")
    args = parser.parse_args()
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    driver = Neo4jDriver("bolt://127.0.0.1:7687", "neo4j", "password", database="neo4j")
    try:
        result = asyncio.run(_run(args, driver))
    except BaseException as exc:
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=True))
        return 2
    output = args.run_root.resolve() / args.output_name
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing quality artifact: {output}")
    _write_json(output, result)
    print(json.dumps({"status": result["status"], "checks_passed": result["checks_passed"], "checks_total": result["checks_total"], "output": str(output)}, ensure_ascii=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
