#!/usr/bin/env python3
"""Run the isolated four-history Native U0 baseline.

The script is deliberately separate from the historical S1/C2 controllers.
It consumes a PASS N0 artifact, uses the pinned Graphiti callable and passive
C1/C2 instrumentation, and writes a durable checkpoint after every episode.
Construction/service failures are fail-closed and never merged into a later
history.  Use the tmux wrapper for the long-running command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

PROJECT = Path(__file__).resolve().parents[1]
LEGACY = PROJECT.parent / "membind-validation"
DATASET = Path("/data/predator/ly/Mem/data/raw/longmemeval-cleaned/longmemeval_s_cleaned.json")
N0_PATH = PROJECT / "artifacts/paper_eval/native_baseline/N0_READ_ONLY_CHECK.json"
NATIVE_V2_FREEZE_PATH = PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"
RUN_ROOT = PROJECT / "artifacts/paper_eval/native_baseline/runs"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"artifact_not_object:{path.name}")
    return value


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="ascii") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    from paper_eval.artifacts import atomic_write_json

    atomic_write_json(path, dict(value))


def _event(identity: Mapping[str, Any], event_type: str, timestamp_ns: int, **extra: Any) -> dict[str, Any]:
    return {
        **identity,
        "stream": "events",
        "event_type": event_type,
        "timestamp_ns": int(timestamp_ns),
        **extra,
    }


def _validate_n0(path: Path, run_id: str) -> dict[str, Any]:
    from paper_eval.artifacts import payload_sha256

    value = _json(path)
    body = dict(value)
    observed = body.pop("payload_sha256", None)
    if observed != payload_sha256(body):
        raise RuntimeError("N0_payload_hash_mismatch")
    if value.get("status") != "PASS" or value.get("run_id") != run_id:
        raise RuntimeError("N0_not_authorized")
    if value.get("namespace_all_empty") is not True:
        raise RuntimeError("N0_namespace_not_empty")
    if value.get("construction", {}).get("max_model_len") != 65536:
        raise RuntimeError("N0_construction_context_mismatch")
    return value


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _runtime_ready(graphiti: Any) -> None:
    driver = graphiti.driver
    task = getattr(driver, "_init_task", None)
    if task is not None:
        await task
        return
    method = getattr(driver, "build_indices_and_constraints", None)
    if callable(method):
        await _await(method())


async def _namespace_state(driver: Any, namespace: str) -> dict[str, Any]:
    result = await driver.execute_query(
        """
        CALL {
          MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count
        }
        CALL {
          MATCH ()-[r]->() WHERE r.group_id = $group_id
          RETURN count(r) AS relationship_count
        }
        CALL {
          MATCH (n:Episodic) WHERE n.group_id = $group_id
          RETURN collect(n.name) AS episode_names
        }
        RETURN node_count, relationship_count, episode_names
        """,
        params={"group_id": namespace},
    )
    rows = getattr(result, "records", None)
    if not isinstance(rows, Sequence) or len(rows) != 1:
        raise RuntimeError("namespace_probe_invalid")
    row = rows[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "episode_names": sorted(str(value) for value in row.get("episode_names") or []),
    }


def _identity(run_id: str, history_id: str, sequence: int) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "history_id": history_id,
        "question_id": history_id,
        "episode_id": f"{history_id}:{sequence}",
        "source_sequence": sequence,
        "method": "U0",
        "repeat_id": 0,
    }


async def _quality_chain(
    *,
    graph: Any,
    record: Mapping[str, Any],
    episodes: Sequence[Any],
    history_id: str,
    namespace: str,
    run_id: str,
    reader: Any,
    judge: Any,
) -> dict[str, Any]:
    """Run one frozen retrieval/Reader/Judge chain with hash-only output."""

    from paper_eval.s2_formal_retrieval import run_formal_session_retrieval
    from paper_eval.s2_retrieval_probe import ProbeCounters, build_episode_bm25_search_config, corpus_identity_sha256
    from paper_eval.s2_session_policy import evaluate_session_retrieval
    from paper_eval.s2_session_reader import materialize_ranked_sessions

    session_ids = tuple(str(value) for value in record["haystack_session_ids"])
    gold_ids = tuple(str(value) for value in record["answer_session_ids"])
    counters = ProbeCounters()
    search_config = build_episode_bm25_search_config()
    corpus_hash = corpus_identity_sha256(episodes)
    started = time.monotonic_ns()
    outcome = await run_formal_session_retrieval(
        graph=graph,
        query=str(record["question"]),
        namespace=namespace,
        episodes=episodes,
        expected_frozen_session_ids=session_ids,
        expected_corpus_identity_sha256=corpus_hash,
        search_config=search_config,
        counters=counters,
    )
    retrieval_done = time.monotonic_ns()
    metrics = evaluate_session_retrieval(
        retrieved_session_ids=outcome.retrieved_session_ids,
        gold_session_ids=gold_ids,
        top_k=10,
        allowed_session_ids=session_ids,
    )
    sessions = materialize_ranked_sessions(
        record=record,
        ranked_session_ids=outcome.retrieved_session_ids,
        top_k=10,
    )
    reader_result = await reader.answer(
        sessions,
        question_date=str(record["question_date"]),
        question=str(record["question"]),
    )
    reader_done = time.monotonic_ns()
    judge_input = SimpleNamespace(
        run_id=run_id,
        history_id=history_id,
        question_type=str(record["question_type"]),
        question=str(record["question"]),
        reference_answer=str(record["answer"]),
    )
    judge_result = await judge.evaluate(
        hypothesis=reader_result.answer,
        inputs=judge_input,
    )
    judge_done = time.monotonic_ns()
    return {
        "status": "SUCCESS",
        "history_id": history_id,
        "retrieval": {
            "evidence_recall_at_10": metrics.evidence_recall_at_10,
            "gold_ranks": list(metrics.gold_ranks),
            "retrieved_session_ids_sha256": hashlib.sha256(
                json.dumps(list(outcome.retrieved_session_ids), sort_keys=True).encode()
            ).hexdigest(),
            "retrieved_count": len(outcome.retrieved_session_ids),
        },
        "qa_accuracy": 1.0 if judge_result.get("label") is True else 0.0,
        "reader": reader_result.to_artifact(),
        "judge": dict(judge_result),
        "latency_ns": {
            "retrieval": retrieval_done - started,
            "reader": reader_done - retrieval_done,
            "judge": judge_done - reader_done,
            "quality_total": judge_done - started,
        },
        "counters": {
            "graphiti_search_calls": outcome.graphiti_search_calls,
            "neo4j_read_requests": outcome.neo4j_read_requests,
            "reader_requests": 1,
            "judge_requests": 1,
        },
    }


async def run(args: argparse.Namespace, *, retrieval_runtime: Any) -> int:
    from paper_eval.native_baseline_runner import (
        build_native_baseline_plan,
        decide_history_resume,
        make_checkpoint,
        seal_history_result,
        validate_read_only_quality_graph,
        verify_checkpoint,
        verify_history_result,
        verify_native_quality_bindings,
    )
    from paper_eval.unified_observability import (
        ObservabilityIdentity,
        aggregate_history_metrics,
        derive_episode_metrics,
        project_operation_views,
        validate_observability_record,
    )

    _validate_n0(N0_PATH, args.run_id)
    plan = build_native_baseline_plan(args.run_id)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    legacy_source = str(LEGACY / "src")
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from current_state_gate import LiveAction
    from dataset import build_episodes, load_json_records
    from graphiti_native import graphiti_episode_kwargs, load_env_file
    from native_characterization_c2_measurement import collect_graph_prefix_size, install_c2_measurement_adapter
    from native_characterization_instrumentation import install_native_characterization_instrumentation
    from native_characterization_runtime import build_u0_graphiti_from_env
    from native_characterization_tracing import DurableJsonlEnvelopeWriter, TraceRecorder

    records = {str(item.get("question_id")): item for item in load_json_records(DATASET)}
    for item in plan.histories:
        if item.history_id not in records:
            raise RuntimeError("dataset_history_missing")

    def authorize(_action: Any) -> None:
        _validate_n0(N0_PATH, args.run_id)

    runtime = build_u0_graphiti_from_env(
        authorization_checker=authorize,
        live_action=LiveAction.NATIVE_CHARACTERIZATION_C0,
        env_loader=lambda: load_env_file(LEGACY / ".env"),
    )
    recorder = TraceRecorder()
    phase_handle = None
    measurement_handle = None

    # Build the quality adapters lazily after the construction runtime exists.
    from paper_eval.native_reader_v2 import OfficialConSessionReader
    from paper_eval.s2_adapters import OpenAIChatCompletionsTransport, build_qualified_qwen_judge
    env = load_env_file(LEGACY / ".env")
    base_url = str(env.get("CONSTRUCTION_LLM_BASE_URL", "http://10.87.5.247:8000/v1/"))
    model = str(env.get("CONSTRUCTION_LLM_MODEL", "qwen3-32b-fp8"))
    api_key = str(env.get("CONSTRUCTION_LLM_API_KEY", "not-required")) or "not-required"
    transport = OpenAIChatCompletionsTransport(model=model, base_url=base_url, api_key=api_key, timeout_seconds=180.0)
    reader = OfficialConSessionReader(model=model, transport=transport)
    judge = build_qualified_qwen_judge(base_url=base_url, api_key=api_key)
    quality_identity = verify_native_quality_bindings(
        frozen_baseline=_json(NATIVE_V2_FREEZE_PATH),
        reader_config_sha256=reader.config_sha256,
        judge_config_sha256=judge.config_sha256,
    )

    try:
        await _runtime_ready(runtime.graphiti)
        retrieval_graph = validate_read_only_quality_graph(
            construction_graph=runtime.graphiti,
            retrieval_graph=retrieval_runtime.graphiti,
        )
        phase_handle = install_native_characterization_instrumentation(runtime.graphiti, recorder)
        measurement_handle = install_c2_measurement_adapter(runtime.graphiti, recorder)
        for history in plan.histories[: args.history_limit]:
            history_dir = RUN_ROOT / args.run_id / history.history_id
            history_dir.mkdir(parents=True, exist_ok=True)
            record = records[history.history_id]
            episodes = [replace(item, group_id=history.namespace) for item in build_episodes(record)]
            expected = list(range(len(episodes)))
            checkpoint_path = history_dir / "checkpoint.json"
            result_path = history_dir / "history_result.json"
            if checkpoint_path.exists():
                checkpoint = verify_checkpoint(_json(checkpoint_path))
                resume_state = decide_history_resume(
                    checkpoint,
                    result_exists=result_path.exists(),
                )
                if resume_state in {"FINALIZED", "FINALIZATION_PENDING"}:
                    verify_history_result(_json(result_path), expected_plan=history)
                    if resume_state == "FINALIZATION_PENDING":
                        _atomic_json(
                            checkpoint_path,
                            make_checkpoint(
                                run_id=args.run_id,
                                history_id=history.history_id,
                                namespace=history.namespace,
                                expected_sequences=expected,
                                completed_sequences=expected,
                                status="completed",
                            ),
                        )
                    print(json.dumps({"history_id": history.history_id, "status": "already_completed"}), flush=True)
                    continue
                completed = list(checkpoint["completed_sequences"])
            else:
                completed = []
                _atomic_json(
                    checkpoint_path,
                    make_checkpoint(
                        run_id=args.run_id,
                        history_id=history.history_id,
                        namespace=history.namespace,
                        expected_sequences=expected,
                        completed_sequences=[],
                        status="planned",
                    ),
                )
            state = await _namespace_state(runtime.graphiti.driver, history.namespace)
            if state["episode_names"] and len(completed) == 0:
                raise RuntimeError("namespace_nonempty_without_checkpoint")
            if completed:
                expected_names = [episodes[i].name for i in completed]
                if state["episode_names"] != sorted(expected_names):
                    raise RuntimeError("namespace_checkpoint_prefix_mismatch")
            _atomic_json(
                checkpoint_path,
                make_checkpoint(
                    run_id=args.run_id,
                    history_id=history.history_id,
                    namespace=history.namespace,
                    expected_sequences=expected,
                    completed_sequences=completed,
                    status="running",
                ),
            )
            writers = {
                name: DurableJsonlEnvelopeWriter(history_dir / name)
                for name in ("spans.jsonl", "llm.jsonl", "embedding.jsonl", "db.jsonl", "events.jsonl", "errors.jsonl", "graph_work.jsonl", "queue.jsonl", "quality.jsonl")
            }
            episode_rows: list[dict[str, Any]] = []
            metrics_path = history_dir / "per_episode_metrics.jsonl"
            if metrics_path.exists():
                for line in metrics_path.read_text(encoding="ascii").splitlines():
                    if line.strip():
                        episode_rows.append(json.loads(line))
            for episode in episodes[len(completed) :]:
                sequence = int(episode.source_sequence)
                identity = _identity(args.run_id, history.history_id, sequence)
                arrival = time.monotonic_ns()
                enqueue = time.monotonic_ns()
                _append_jsonl(history_dir / "events.jsonl", _event(identity, "intent", enqueue))
                service_start = time.monotonic_ns()
                _append_jsonl(history_dir / "events.jsonl", _event(identity, "service_start", service_start))
                prefix = await collect_graph_prefix_size(runtime.graphiti.driver, history.namespace)
                try:
                    with recorder.episode_scope(args.run_id, identity["episode_id"], sequence):
                        with recorder.span("graph-prefix-snapshot", operation_class="group-count-before-add-episode", metadata=prefix):
                            pass
                        await runtime.graphiti.add_episode(**graphiti_episode_kwargs(episode))
                except BaseException as exc:
                    envelope = recorder.episode_envelope(args.run_id, identity["episode_id"], sequence)
                    spans = envelope.get("spans", [])
                    for row in spans:
                        row.update(identity)
                        row["stream"] = "spans"
                        writers["spans.jsonl"].write(row)
                    failure = _event(identity, "failure", time.monotonic_ns(), error_class=type(exc).__name__, failure_stage="construction")
                    writers["errors.jsonl"].write(failure)
                    writers["events.jsonl"].write(failure)
                    _atomic_json(
                        checkpoint_path,
                        make_checkpoint(
                            run_id=args.run_id,
                            history_id=history.history_id,
                            namespace=history.namespace,
                            expected_sequences=expected,
                            completed_sequences=completed,
                            status="incomplete_non_mergeable",
                            error_class=type(exc).__name__,
                        ),
                    )
                    raise
                publication = time.monotonic_ns()
                after = await collect_graph_prefix_size(runtime.graphiti.driver, history.namespace)
                terminal = time.monotonic_ns()
                _append_jsonl(history_dir / "events.jsonl", _event(identity, "publication", publication))
                _append_jsonl(history_dir / "events.jsonl", _event(identity, "terminal", terminal, status="published"))
                envelope = recorder.episode_envelope(args.run_id, identity["episode_id"], sequence)
                raw_spans = envelope.get("spans", [])
                views = project_operation_views(raw_spans)
                for stream, rows in views.items():
                    if stream not in {"spans", "llm", "embedding", "db", "errors"}:
                        continue
                    for row in rows:
                        safe = dict(row); safe.update(identity); safe["stream"] = stream
                        writers[f"{stream}.jsonl"].write(safe)
                graph_work = {
                    **identity,
                    "stream": "graph_work",
                    "nodes_before": prefix["graph_prefix_node_count"],
                    "relationships_before": prefix["graph_prefix_relationship_count"],
                    "nodes_after": after["graph_prefix_node_count"],
                    "relationships_after": after["graph_prefix_relationship_count"],
                    "semantic_counts_status": "NOT_CAPTURED",
                }
                writers["graph_work.jsonl"].write(graph_work)
                queue = {
                    **identity,
                    "stream": "queue",
                    "arrival_ts_ns": arrival,
                    "enqueue_ts_ns": enqueue,
                    "service_start_ts_ns": service_start,
                    "publication_ts_ns": publication,
                    "terminal_ts_ns": terminal,
                    "queue_depth_at_enqueue": 0,
                    "queue_status": "NOT_APPLICABLE_SERIAL_BASELINE",
                }
                writers["queue.jsonl"].write(queue)
                converted_spans = [dict(row) for row in raw_spans]
                episode_metric = derive_episode_metrics(
                    identity=ObservabilityIdentity(**identity),
                    spans=converted_spans,
                    queue_event={key: queue[key] for key in ("arrival_ts_ns", "enqueue_ts_ns", "service_start_ts_ns", "publication_ts_ns", "terminal_ts_ns", "queue_depth_at_enqueue")},
                    graph_work={"nodes_before": graph_work["nodes_before"], "nodes_after": graph_work["nodes_after"], "relationships_before": graph_work["relationships_before"], "relationships_after": graph_work["relationships_after"]},
                )
                _append_jsonl(metrics_path, episode_metric)
                episode_rows.append(episode_metric)
                completed.append(sequence)
                _atomic_json(
                    checkpoint_path,
                    make_checkpoint(
                        run_id=args.run_id,
                        history_id=history.history_id,
                        namespace=history.namespace,
                        expected_sequences=expected,
                        completed_sequences=completed,
                        status="running",
                    ),
                )
                print(json.dumps({"history_id": history.history_id, "source_sequence": sequence, "completed": len(completed), "expected": len(expected), "service_latency_ns": episode_metric["latency_ns"]["service"]}, sort_keys=True), flush=True)
            if completed != expected:
                raise RuntimeError("history_construction_prefix_incomplete")
            final_state = await _namespace_state(runtime.graphiti.driver, history.namespace)
            expected_names = sorted(episode.name for episode in episodes)
            if final_state["episode_names"] != expected_names:
                raise RuntimeError("history_final_namespace_episode_mismatch")
            quality_path = history_dir / "quality.jsonl"
            if quality_path.exists():
                quality_lines = [
                    line for line in quality_path.read_text(encoding="ascii").splitlines()
                    if line.strip()
                ]
                if len(quality_lines) != 1:
                    raise RuntimeError("history_quality_row_count_invalid")
                quality_record = validate_observability_record(json.loads(quality_lines[0]))
                if quality_record.get("quality_identity") != quality_identity:
                    raise RuntimeError("history_quality_identity_mismatch")
                quality = quality_record.get("result")
                if not isinstance(quality, Mapping):
                    raise RuntimeError("history_quality_result_invalid")
                quality = dict(quality)
            else:
                quality = await _quality_chain(
                    graph=retrieval_graph,
                    record=record,
                    episodes=episodes,
                    history_id=history.history_id,
                    namespace=history.namespace,
                    run_id=args.run_id,
                    reader=reader,
                    judge=judge,
                )
                quality_record = validate_observability_record(
                    {
                        **_identity(args.run_id, history.history_id, 0),
                        "stream": "quality",
                        "record_scope": "history",
                        "quality_identity": quality_identity,
                        "result": quality,
                    }
                )
                writers["quality.jsonl"].write(quality_record)
            aggregate = aggregate_history_metrics(
                identity=ObservabilityIdentity(**_identity(args.run_id, history.history_id, 0)),
                episode_metrics=episode_rows,
                quality={"qa_accuracy": quality["qa_accuracy"], "evidence_recall_at_10": quality["retrieval"]["evidence_recall_at_10"]},
                serial_baseline=True,
            )
            final_namespace_observation = {
                "node_count": final_state["node_count"],
                "relationship_count": final_state["relationship_count"],
                "episode_count": len(final_state["episode_names"]),
                "episode_names_sha256": hashlib.sha256(
                    json.dumps(final_state["episode_names"], sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "episode_names_match_expected": True,
            }
            result = seal_history_result({"schema_version": "membind.paper-eval-v3.native-baseline-history.v1", "run_id": args.run_id, "history_id": history.history_id, "namespace": history.namespace, "method": "U0", "repeat_id": 0, "status": "completed", "quality_identity": quality_identity, "quality": quality, "aggregate": aggregate, "final_namespace_observation": final_namespace_observation})
            _atomic_json(result_path, result)
            _atomic_json(
                checkpoint_path,
                make_checkpoint(
                    run_id=args.run_id,
                    history_id=history.history_id,
                    namespace=history.namespace,
                    expected_sequences=expected,
                    completed_sequences=completed,
                    status="completed",
                ),
            )
            print(json.dumps({"history_id": history.history_id, "status": "completed", "qa_accuracy": quality["qa_accuracy"], "evidence_recall_at_10": quality["retrieval"]["evidence_recall_at_10"]}, sort_keys=True), flush=True)
    finally:
        if measurement_handle is not None:
            measurement_handle.restore()
        if phase_handle is not None:
            phase_handle.restore()
        for component, method in (
            (judge, "aclose"),
            (transport, "aclose"),
            (retrieval_runtime.graphiti, "close"),
            (runtime.graphiti, "close"),
        ):
            close = getattr(component, method, None)
            if callable(close):
                await _await(close())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--history-limit", type=int, default=4)
    args = parser.parse_args()
    if args.history_limit < 1 or args.history_limit > 4:
        raise SystemExit("--history-limit must be between 1 and 4")
    _validate_n0(N0_PATH, args.run_id)
    legacy_source = str(LEGACY / "src")
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from graphiti_native import load_env_file
    from paper_eval.s2_r0_live import build_read_only_graphiti

    # S2-R0 deliberately constructs its driver before an event loop exists, so
    # Neo4j schema initialization can never be scheduled on the retrieval path.
    retrieval_runtime = build_read_only_graphiti(env=load_env_file(LEGACY / ".env"))
    return asyncio.run(run(args, retrieval_runtime=retrieval_runtime))


if __name__ == "__main__":
    raise SystemExit(main())
