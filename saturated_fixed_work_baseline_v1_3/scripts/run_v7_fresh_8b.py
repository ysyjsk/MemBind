#!/usr/bin/env python3
"""Run the V7-FRESH two-stage qualification on the frozen 8B dual platform.

This command intentionally runs only from-scratch V7-FRESH.  It does not run
V7-INCREMENTAL, alter the sealed B0 artifact, or launch any relaxed-order arm.
Each source is built and published serially, with source-local extraction sent
under the PREPARE provider scope and stateful reconciliation/publication under
the NATIVE scope.  The output namespace is fresh and append-only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
V7_SUMMARY_ENTITY_PAGE_CAPACITY = 24
V7_DEDUPE_CANDIDATE_PAGE_CAPACITY = 32
for source in (
    ROOT / "mab_quality_v2_final_qa/src",
    ROOT / "saturated_fixed_work_baseline_v1_3/src",
    ROOT / "paper-eval-v3/src",
    ROOT / "membind-validation/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_workload_manifest  # noqa: E402
from mab_quality_v2_final_qa.workload_contract import WorkloadManifest  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import (  # noqa: E402
    episode_from_input,
    _mab_graphiti_kwargs,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import provider_scope  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (  # noqa: E402
    build_8b_u0_runtime,
    close_8b_u0_runtime,
    load_8b_platform_manifest,
    load_8b_routing_contract,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.v7_fresh import (  # noqa: E402
    OrderedPublicationGate,
    build_v7_fresh_to_seam_async,
    default_bindings,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write one durable, append-only artifact for an already completed run."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _record_dict(record: Any) -> dict[str, Any]:
    value = record.to_dict() if hasattr(record, "to_dict") else dict(record)
    if not isinstance(value, dict):
        raise TypeError("trace record is not a mapping")
    return value


def _work_accounting(records: Iterable[Any], route_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate observed work without retaining prompts, responses, or Cypher."""

    spans = [_record_dict(record) for record in records]
    metadata = [row.get("metadata") if isinstance(row.get("metadata"), dict) else {} for row in spans]
    logical = [row for row in spans if row.get("phase") == "llm" and row.get("operation_class") == "logical-call"]
    transports = [row for row in spans if row.get("phase") == "llm-transport"]
    embeddings = [row for row in spans if row.get("phase") == "embedding"]
    databases = [row for row in spans if row.get("phase") in {"database", "database-transaction"}]
    logical_metadata = [
        row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for row in logical
    ]
    transport_metadata = [
        row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        for row in transports
    ]
    logical_input_tokens = sum(int(item.get("input_tokens") or 0) for item in logical_metadata)
    logical_output_tokens = sum(int(item.get("output_tokens") or 0) for item in logical_metadata)
    # Token volume is attributed to physical transport spans.  Logical spans
    # carry the same usage aggregate for request-level audit, so summing both
    # would double count work.
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in transport_metadata)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in transport_metadata)
    embedding_items = sum(int(item.get("text_count") or 0) for item in metadata if "text_count" in item)
    return {
        "schema_version": "membind.v7b.work-accounting.v1",
        "trace_span_count": len(spans),
        "llm_logical_calls": len(logical),
        "llm_transport_attempts": len(transports),
        "llm_input_tokens": input_tokens,
        "llm_output_tokens": output_tokens,
        "llm_logical_input_tokens": logical_input_tokens,
        "llm_logical_output_tokens": logical_output_tokens,
        "embedding_calls": len(embeddings),
        "embedding_items": embedding_items,
        "database_operations": len(databases),
        "database_reads": sum(1 for row in databases if row.get("operation_class") in {"query", "read"}),
        "database_writes": sum(1 for row in databases if row.get("operation_class") in {"write", "execute_write"}),
        "route_event_count": len(route_events),
        "physical_route_attempts": sum(1 for row in route_events if row.get("event") == "LLM_ROUTE"),
        "provider_calls_observed": len(transports),
        "accounting_status": "OBSERVED_TRACE_SCOPE",
        "route_endpoint_counts": {
            str(endpoint): sum(1 for row in route_events if row.get("event") == "LLM_ROUTE" and row.get("endpoint_id") == endpoint)
            for endpoint in sorted({str(row.get("endpoint_id")) for row in route_events if row.get("event") == "LLM_ROUTE"})
        },
        "route_region_counts": {
            str(region): sum(1 for row in route_events if row.get("event") == "LLM_ROUTE" and row.get("region") == region)
            for region in sorted({str(row.get("region")) for row in route_events if row.get("event") == "LLM_ROUTE"})
        },
        "route_scope_contract": "LOGICAL_REGION_LABELS_CAPACITY_WEIGHTED_POOL_NOT_HARD_AFFINITY",
    }


def _artifact_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _context_inputs(authority: Mapping[str, Any], context_index: int, session_limit: int) -> tuple[Any, WorkloadManifest, tuple[Any, ...]]:
    context = tuple(authority["contexts"])[context_index]
    full = build_workload_manifest(
        context,
        {key: value for key, value in authority.items() if key != "contexts"},
        scope="FORMAL",
    )
    if session_limit <= 0 or session_limit > len(full.episodes):
        raise ValueError("session_limit is outside the frozen workload")
    workload = WorkloadManifest.from_episodes(
        context_id=context.context_id,
        episodes=full.episodes[:session_limit],
        dataset_revision=full.dataset_revision,
        dataset_file_sha256=full.dataset_file_sha256,
        scope="ENGINEERING_DIAGNOSTIC",
        expected_episode_count=None,
    )
    inputs = tuple(
        SimpleNamespace(**episode.to_dict(), session_id=session.session_id)
        for episode, session in zip(
            workload.episodes,
            context.sessions[:session_limit],
            strict=True,
        )
    )
    return context, workload, inputs


def _episode_kwargs(item: Any, namespace: str) -> dict[str, Any]:
    episode = episode_from_input(item)
    return _mab_graphiti_kwargs(episode, namespace=namespace)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.environ.get("MEMBIND_PROFILE_ID") != "local-qwen3-8b-awq-dualreplica-v1":
        raise RuntimeError("activate scripts/local_runtime_8b_dual/activate.sh before running")
    platform_path, platform = load_8b_platform_manifest()
    authority = build_authority(ROOT / "mab_quality_v2_final_qa/data/official_5_contexts.json")
    context, workload, inputs = _context_inputs(authority, args.context_index, args.session_limit)
    routes = load_8b_routing_contract(os.environ["MEMBIND_NATIVE_ROUTING_CONFIG"])
    output_root = args.output_root.resolve()
    experiment_root = Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]).resolve()
    if experiment_root != output_root and experiment_root not in output_root.parents:
        raise RuntimeError("output root is outside the isolated 8B experiment root")
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"output root is not fresh: {output_root}")
    output_root.mkdir(parents=True, mode=0o700)
    namespace = f"local-qwen3-8b-awq-dualreplica-v1-v7fresh-{args.run_id}-{uuid.uuid4().hex[:10]}"
    run_manifest = {
        "schema_version": "membind.v7b.fresh-8b-qualification.v1",
        "status": "RUNNING",
        "method": "V7_FRESH",
        "profile_id": os.environ["MEMBIND_PROFILE_ID"],
        "run_id": args.run_id,
        "context_index": args.context_index,
        "context_id": context.context_id,
        "source_count": len(inputs),
        "namespace": namespace,
        "platform_manifest": {"path": str(platform_path), "payload_sha256": platform["payload_sha256"]},
        "workload_manifest_sha256": _sha256_bytes(workload.jsonl().encode("utf-8")),
        "state_contract": "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
        "source_local_previous_count": 0,
        "incremental_enabled": False,
        "treatment_authorized": False,
        "summary_entity_page_capacity": V7_SUMMARY_ENTITY_PAGE_CAPACITY,
        "dedupe_candidate_page_capacity": V7_DEDUPE_CANDIDATE_PAGE_CAPACITY,
        "summary_partition_semantics": "NATIVE_SUMMARY_OPERATOR_ENTITY_PAGE_MERGE_NO_WORK_REDUCTION",
        "started_at_unix": time.time(),
    }
    _write_json(output_root / "RUN_MANIFEST.json", run_manifest)
    runtime = None
    instrumentation = None
    recorder = None
    route_events: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    gate = OrderedPublicationGate()
    bindings = default_bindings()
    formal_start = time.monotonic_ns()
    try:
        runtime = build_8b_u0_runtime(
            routing_contract=routes,
            route_event_sink=route_events.append,
            summary_entity_page_capacity=V7_SUMMARY_ENTITY_PAGE_CAPACITY,
            dedupe_candidate_page_capacity=V7_DEDUPE_CANDIDATE_PAGE_CAPACITY,
        )
        from native_characterization_instrumentation import install_native_characterization_instrumentation
        from native_characterization_tracing import TraceRecorder

        recorder = TraceRecorder()
        instrumentation = install_native_characterization_instrumentation(runtime.graphiti, recorder)
        for sequence, item in enumerate(inputs):
            kwargs = _episode_kwargs(item, namespace)
            stage_start = time.monotonic_ns()

            async def source_nodes(graphiti: Any, episode: Any, kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="PREPARE", source_sequence=_seq):
                    return await bindings.extract_source_nodes(graphiti, episode, kw)

            async def source_edges(graphiti: Any, episode: Any, nodes: list[Any], kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="PREPARE", source_sequence=_seq):
                    return await bindings.extract_source_edges(graphiti, episode, nodes, kw)

            async def retrieve(graphiti: Any, kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="NATIVE", source_sequence=_seq):
                    return await bindings.retrieve_previous(graphiti, kw)

            async def resolve_nodes(graphiti: Any, nodes: list[Any], episode: Any, previous: Any, kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="NATIVE", source_sequence=_seq):
                    return await bindings.resolve_nodes(graphiti, nodes, episode, previous, kw)

            async def resolve_edges(graphiti: Any, edges: list[Any], episode: Any, previous: Any, nodes: list[Any], uuid_map: Mapping[str, str], kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="NATIVE", source_sequence=_seq):
                    return await bindings.resolve_edges(graphiti, edges, episode, previous, nodes, uuid_map, kw)

            async def attributes(graphiti: Any, nodes: list[Any], episode: Any, previous: Any, new_edges: list[Any], kw: Mapping[str, Any], _seq: int = sequence) -> Any:
                with provider_scope(region="NATIVE", source_sequence=_seq):
                    return await bindings.extract_attributes(graphiti, nodes, episode, previous, new_edges, kw)

            scoped = type(bindings)(
                now=bindings.now,
                make_episode=bindings.make_episode,
                retrieve_previous=retrieve,
                extract_source_nodes=source_nodes,
                extract_source_edges=source_edges,
                resolve_nodes=resolve_nodes,
                resolve_edges=resolve_edges,
                extract_attributes=attributes,
                continuation_k=bindings.continuation_k,
            )
            # TraceRecorder only records spans inside an episode scope.  Keep
            # build and durable publication in the same scope so the work
            # accounting reflects the actual source transaction.
            with recorder.episode_scope(args.run_id, episode_from_input(item).name, sequence):
                built = await build_v7_fresh_to_seam_async(
                    runtime.graphiti,
                    kwargs,
                    publication_frontier=sequence,
                    backend_epoch=str(platform["payload_sha256"]),
                    bindings=scoped,
                )
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    await gate.publish(runtime.graphiti, sequence, built)
            rows.append({
                "source_sequence": sequence,
                "stage_events": list(built.stage_events),
                "previous_episode_count": len(built.previous_episodes),
                "source_node_count": len(built.extracted_nodes),
                "source_edge_count": len(built.extracted_edges),
                "resolved_node_count": len(built.nodes),
                "entity_edge_count": len(built.entity_edges),
                "hydrated_node_count": len(built.hydrated_nodes),
                "duration_ns": time.monotonic_ns() - stage_start,
                "publication_order": sequence,
                "status": "PASS",
            })
        if gate.frontier != len(inputs):
            raise RuntimeError("final ordered frontier is incomplete")
        if instrumentation is not None:
            instrumentation.restore()
            instrumentation = None
        episodes = [episode_from_input(item) for item in inputs]
        from live_outputs import export_canonical_graph

        graph = await export_canonical_graph(runtime.graphiti, episodes, namespace)
        graph_digest = {
            "schema_version": "membind.v7b.graph-digest.v1",
            "canonical_graph_hash": graph["canonical_graph_hash"],
            "entity_count": len(graph.get("entities", [])),
            "edge_count": len(graph.get("edges", [])),
            "episode_count": len(graph.get("episodes", [])),
            "source_sequences": [row.get("source_sequence") for row in graph.get("episodes", [])],
        }
        records = list(recorder.records)
        work = _work_accounting(records, route_events)
        provider_rows = [_record_dict(record) for record in records]
        _write_jsonl(output_root / "provider_events.jsonl", provider_rows)
        _write_jsonl(
            output_root / "semantic_ir.jsonl",
            [
                {
                    "schema_version": "membind.v7b.stable-ir-observation.v1",
                    "source_sequence": row["source_sequence"],
                    "source_hash": episode_from_input(inputs[row["source_sequence"]]).source_hash,
                    "node_count": row["source_node_count"],
                    "edge_count": row["source_edge_count"],
                    "previous_collection_count": 0,
                    "purity_status": "PASS",
                }
                for row in rows
            ],
        )
        _write_jsonl(
            output_root / "publication_events.jsonl",
            [
                {
                    "schema_version": "membind.v7b.publication-event.v1",
                    "source_sequence": row["source_sequence"],
                    "frontier_before": row["source_sequence"],
                    "frontier_after": row["source_sequence"] + 1,
                    "durable": True,
                    "status": row["status"],
                }
                for row in rows
            ],
        )
        _write_json(output_root / "work_accounting.json", work)
        _write_json(output_root / "graph_digest.json", graph_digest)
        _write_json(
            output_root / "quality_results.json",
            {
                "schema_version": "membind.v7b.quality-result.v1",
                "status": "PENDING_EXTERNAL_SUITE",
                "quality_contract": "V7_FRESH_VS_B0_NON_INFERIORITY",
                "graph_digest": graph_digest,
            },
        )
        _write_json(
            output_root / "construction_seal.json",
            {
                "schema_version": "membind.v7b.construction-seal.v1",
                "status": "PASS",
                "method": "V7_FRESH",
                "run_id": args.run_id,
                "namespace": namespace,
                "platform_manifest_sha256": platform["payload_sha256"],
                "work_accounting_sha256": _artifact_hash(output_root / "work_accounting.json"),
                "graph_digest_sha256": _artifact_hash(output_root / "graph_digest.json"),
                "publication_order": [row["publication_order"] for row in rows],
            },
        )
        result = {
            "schema_version": "membind.v7b.fresh-8b-qualification-result.v1",
            "status": "PASS",
            "method": "V7_FRESH",
            "profile_id": os.environ["MEMBIND_PROFILE_ID"],
            "run_id": args.run_id,
            "context_id": context.context_id,
            "namespace": namespace,
            "source_count": len(inputs),
            "durable_publication_count": gate.frontier,
            "publication_source_sequences": [row["publication_order"] for row in rows],
            "rows": rows,
            "t_build_ns": time.monotonic_ns() - formal_start,
            "provider_calls": work["provider_calls_observed"],
            "llm_logical_calls": work["llm_logical_calls"],
            "llm_transport_attempts": work["llm_transport_attempts"],
            "llm_input_tokens": work["llm_input_tokens"],
            "llm_output_tokens": work["llm_output_tokens"],
            "llm_logical_input_tokens": work["llm_logical_input_tokens"],
            "llm_logical_output_tokens": work["llm_logical_output_tokens"],
            "embedding_calls": work["embedding_calls"],
            "embedding_items": work["embedding_items"],
            "database_reads": work["database_reads"],
            "database_writes": work["database_writes"],
            "source_local_previous_count": 0,
            "incremental_enabled": False,
            "summary_entity_page_capacity": V7_SUMMARY_ENTITY_PAGE_CAPACITY,
            "dedupe_candidate_page_capacity": V7_DEDUPE_CANDIDATE_PAGE_CAPACITY,
            "summary_partition_semantics": "NATIVE_SUMMARY_OPERATOR_ENTITY_PAGE_MERGE_NO_WORK_REDUCTION",
            "quality_status": "PENDING_QUALITY_SUITE",
            "b0_speedup_status": "NOT_COMPUTED",
            "route_event_count": len(route_events),
            "platform_manifest_sha256": platform["payload_sha256"],
            "graph_digest": graph_digest,
            "work_accounting": work,
        }
        _write_json(output_root / "RESULT.json", result)
        _write_json(output_root / "ROUTE_EVENTS.json", {"events": route_events})
        run_manifest["accounting_status"] = work["accounting_status"]
        run_manifest["artifact_hashes"] = {
            name: _artifact_hash(output_root / name)
            for name in (
                "RESULT.json",
                "ROUTE_EVENTS.json",
                "provider_events.jsonl",
                "semantic_ir.jsonl",
                "publication_events.jsonl",
                "work_accounting.json",
                "graph_digest.json",
                "quality_results.json",
                "construction_seal.json",
            )
        }
        run_manifest["route_scope_contract"] = work["route_scope_contract"]
        run_manifest["status"] = "PASS"
        run_manifest["ended_at_unix"] = time.time()
        _write_json(output_root / "RUN_MANIFEST_FINAL.json", run_manifest)
        return result
    except BaseException as exc:
        # Preserve a machine-readable failed attempt, including partial route
        # and provider observations, before cleanup.  A failed namespace is
        # never resumed or promoted to a performance result.
        failure = {
            "schema_version": "membind.v7b.fresh-8b-qualification-failure.v1",
            "status": "FAILED",
            "method": "V7_FRESH",
            "run_id": args.run_id,
            "namespace": namespace,
            "source_count": len(inputs),
            "completed_source_count": len(rows),
            "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "error_sha256": _sha256_bytes(str(exc).encode("utf-8", errors="backslashreplace")),
            "route_event_count": len(route_events),
            "partial_trace_span_count": len(getattr(recorder, "records", ()) or ()) if recorder is not None else 0,
            "treatment_authorized": False,
            "replacement_policy": "fresh_run_id_and_fresh_namespace_required",
        }
        try:
            if route_events:
                _write_json(output_root / "ROUTE_EVENTS_PARTIAL.json", {"events": route_events})
            if recorder is not None and getattr(recorder, "records", None):
                _write_jsonl(output_root / "provider_events_partial.jsonl", [_record_dict(record) for record in recorder.records])
            _write_json(output_root / "RUN_FAILURE.json", failure)
            run_manifest["status"] = "FAILED"
            run_manifest["ended_at_unix"] = time.time()
            run_manifest["failure"] = failure
            _write_json(output_root / "RUN_MANIFEST_FAILURE.json", run_manifest)
        except BaseException:
            pass
        raise
    finally:
        if instrumentation is not None:
            instrumentation.restore()
        if runtime is not None:
            await close_8b_u0_runtime(runtime)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--context-index", type=int, default=0)
    parser.add_argument("--session-limit", type=int, default=2)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("/data/predator/ly/Mem/experiments/local-qwen3-8b-awq-dualreplica-v1/v7_fresh_qualification"),
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args))
    except BaseException as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)[:1000]}, ensure_ascii=True), flush=True)
        return 2
    print(json.dumps({key: result[key] for key in ("status", "method", "source_count", "durable_publication_count", "t_build_ns", "quality_status")}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
