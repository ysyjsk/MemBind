"""Execute one isolated method/history block with real Graphiti services.

The block is the failure and checkpoint unit. Scheduling is injected through
the qualified baseline bridge, instrumentation is passive and reversible, and
quality uses a separate read-only Graphiti driver. A failed block is sealed
non-mergeable; this module never cleans or resumes an uncertain namespace.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import atomic_write_json, sha256_file
from .baseline_suite import canonicalize_baseline_method
from .baseline_suite_artifacts import BaselineBlockStore
from .baseline_suite_execution import (
    graph_work_attribution_status,
    normalize_schedule_lifecycle,
)
from .baseline_suite_live import execute_method_schedule
from .baseline_suite_quality import (
    build_baseline_quality_adapters,
    run_baseline_quality_chain,
)
from .native_baseline_runner import validate_read_only_quality_graph
from .s5_native_method_adapters import S5EpisodeRef
from .unified_observability import (
    ObservabilityIdentity,
    aggregate_history_metrics,
    derive_episode_metrics,
    project_operation_views,
    validate_observability_record,
)


PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT.parent
LEGACY = ROOT / "membind-validation"
LEGACY_SRC = LEGACY / "src"
DATASET = Path(
    "/data/predator/ly/Mem/data/raw/longmemeval-cleaned/"
    "longmemeval_s_cleaned.json"
)
NATIVE_V2_FREEZE = PROJECT / "artifacts/paper_eval/native/NATIVE_BASELINE_V2_FREEZE.json"

TELEMETRY_STREAMS = (
    "spans",
    "llm",
    "embedding",
    "db",
    "graph_work",
    "queue",
    "quality",
    "errors",
    "per_episode_metrics",
)


class BaselineSuiteLiveError(RuntimeError):
    """A sanitized block execution or evidence boundary failed."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise BaselineSuiteLiveError(f"artifact_unreadable:{path.name}") from None
    if not isinstance(value, dict):
        raise BaselineSuiteLiveError(f"artifact_not_object:{path.name}")
    return value


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _runtime_ready(graphiti: Any) -> None:
    driver = getattr(graphiti, "driver", None)
    if driver is None:
        raise BaselineSuiteLiveError("runtime_graphiti_driver_missing")
    init_task = getattr(driver, "_init_task", None)
    if init_task is not None:
        await _await(init_task)
        return
    readiness = getattr(driver, "build_indices_and_constraints", None)
    if not callable(readiness):
        raise BaselineSuiteLiveError("runtime_readiness_missing")
    await _await(readiness())


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
    records = getattr(result, "records", None)
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)) or len(records) != 1:
        raise BaselineSuiteLiveError("namespace_probe_invalid")
    row = records[0]
    return {
        "node_count": int(row.get("node_count") or 0),
        "relationship_count": int(row.get("relationship_count") or 0),
        "episode_names": sorted(str(item) for item in row.get("episode_names") or []),
    }


def _identity(block: Mapping[str, Any], sequence: int) -> dict[str, Any]:
    history_id = str(block["history_id"])
    return {
        "run_id": str(block["namespace"]),
        "history_id": history_id,
        "question_id": history_id,
        "episode_id": f"{history_id}:{sequence}",
        "source_sequence": int(sequence),
        "method": str(block["method"]),
        "repeat_id": 0,
    }


def _qualified_error(error: BaseException) -> str:
    return f"{type(error).__module__}.{type(error).__qualname__}"


def _safe_stage(value: str) -> str:
    return value.replace("-", "_")


def _create_empty_durable(path: Path) -> None:
    """Materialize an empty stream so its hash is evidence, not ``missing``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def execute_baseline_block(
    *,
    block: Mapping[str, Any],
    block_root: Path,
    retrieval_runtime: Any | None = None,
) -> dict[str, Any]:
    """Run one fresh block and return its sealed completion summary."""

    method = canonicalize_baseline_method(block.get("method"))
    mode = str(block.get("mode"))
    if mode not in {"canary", "development"}:
        raise BaselineSuiteLiveError("block_mode_invalid")
    if mode == "development" and retrieval_runtime is None:
        raise BaselineSuiteLiveError("development_retrieval_runtime_missing")

    legacy_source = str(LEGACY_SRC)
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    from current_state_gate import LiveAction
    from dataset import build_episodes, load_json_records
    from graphiti_native import add_episode, load_env_file
    from native_characterization_c2_measurement import install_c2_measurement_adapter
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )
    from native_characterization_runtime import build_u0_graphiti_from_env
    from native_characterization_tracing import (
        DurableJsonlEnvelopeWriter,
        TraceRecorder,
    )

    history_id = str(block["history_id"])
    records = {
        str(record.get("question_id")): record
        for record in load_json_records(DATASET)
    }
    if history_id not in records:
        raise BaselineSuiteLiveError("dataset_history_missing")
    record = records[history_id]
    native_episodes = build_episodes(record)
    limit = block.get("episode_limit")
    if limit is not None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise BaselineSuiteLiveError("episode_limit_invalid")
        native_episodes = native_episodes[:limit]
    namespace = str(block["namespace"])
    episodes = [
        type(episode)(
            question_id=episode.question_id,
            group_id=namespace,
            session_id=episode.session_id,
            source_sequence=episode.source_sequence,
            source_hash=episode.source_hash,
            reference_time=episode.reference_time,
            body=episode.body,
        )
        for episode in native_episodes
    ]
    expected = list(range(len(episodes)))
    refs = tuple(
        S5EpisodeRef(
            source_sequence=episode.source_sequence,
            source_sha256=episode.source_hash,
            native_episode=episode,
        )
        for episode in episodes
    )
    store = BaselineBlockStore.create(
        Path(block_root),
        block=block,
        expected_sequences=expected,
        source_sha256s=[item.source_sha256 for item in refs],
    )
    telemetry_root = Path(block_root) / "telemetry"
    for name in TELEMETRY_STREAMS:
        _create_empty_durable(telemetry_root / f"{name}.jsonl")
    writers = {
        name: DurableJsonlEnvelopeWriter(telemetry_root / f"{name}.jsonl")
        for name in TELEMETRY_STREAMS
    }

    def authorize(action: Any) -> dict[str, str]:
        action_name = getattr(action, "value", action)
        if action_name != LiveAction.NATIVE_CHARACTERIZATION_C0.value:
            raise BaselineSuiteLiveError("runtime_live_action_invalid")
        if not store.manifest_path.is_file() or not store.checkpoint_path.is_file():
            raise BaselineSuiteLiveError("block_authority_not_durable")
        return {"status": "BASELINE_SUITE_BLOCK_MANIFEST_DURABLE"}

    env = load_env_file(LEGACY / ".env")
    runtime: Any | None = None
    phase_handle: Any | None = None
    measurement_handle: Any | None = None
    quality_adapters: dict[str, Any] | None = None
    recorder = TraceRecorder()
    raw_spans: dict[int, list[dict[str, Any]]] = {}
    graph_work: dict[int, dict[str, Any]] = {}
    stage = "runtime_construction"
    completed = False
    try:
        runtime = build_u0_graphiti_from_env(
            authorization_checker=authorize,
            live_action=LiveAction.NATIVE_CHARACTERIZATION_C0,
            env_loader=lambda: load_env_file(LEGACY / ".env"),
            structured_output_mode="json_schema",
        )
        stage = "runtime_readiness"
        await _runtime_ready(runtime.graphiti)
        state = await _namespace_state(runtime.graphiti.driver, namespace)
        if state["node_count"] != 0 or state["relationship_count"] != 0:
            raise BaselineSuiteLiveError("fresh_namespace_not_empty")
        if state["episode_names"]:
            raise BaselineSuiteLiveError("fresh_namespace_has_episodes")

        phase_handle = install_native_characterization_instrumentation(
            runtime.graphiti, recorder
        )
        measurement_handle = install_c2_measurement_adapter(
            runtime.graphiti, recorder
        )

        async def traced_add(native_episode: Any) -> None:
            sequence = int(native_episode.source_sequence)
            identity = _identity(block, sequence)
            before = await _namespace_state(runtime.graphiti.driver, namespace)
            error: BaseException | None = None
            try:
                with recorder.episode_scope(
                    namespace, identity["episode_id"], sequence
                ):
                    with recorder.span(
                        "graph-prefix-snapshot",
                        operation_class="group-count-before-add-episode",
                        metadata={
                            "graph_prefix_node_count": before["node_count"],
                            "graph_prefix_relationship_count": before[
                                "relationship_count"
                            ],
                        },
                    ):
                        pass
                    await add_episode(runtime.graphiti, native_episode)
            except BaseException as caught:
                error = caught
            after = (
                await _namespace_state(runtime.graphiti.driver, namespace)
                if error is None
                else None
            )
            envelope = recorder.episode_envelope(
                namespace, identity["episode_id"], sequence
            )
            spans = [dict(row) for row in envelope.get("spans", [])]
            raw_spans[sequence] = spans
            views = project_operation_views(spans)
            for stream in ("spans", "llm", "embedding", "db", "errors"):
                for raw in views.get(stream, []):
                    row = {**dict(raw), **identity, "stream": stream}
                    writers[stream].write(row)
            work = {
                **identity,
                "stream": "graph_work",
                "nodes_before": before["node_count"],
                "relationships_before": before["relationship_count"],
                "nodes_after": after["node_count"] if after is not None else None,
                "relationships_after": (
                    after["relationship_count"] if after is not None else None
                ),
                "attribution_status": graph_work_attribution_status(method),
            }
            graph_work[sequence] = work
            writers["graph_work"].write(work)
            if error is not None:
                raise error

        async def persist_scheduler_event(event: dict[str, object]) -> None:
            store.append_event(event)

        stage = "construction"
        schedule = await execute_method_schedule(
            method=method,
            run_id=namespace,
            episodes=refs,
            native_add_episode=traced_add,
            persist_event=persist_scheduler_event,
        )
        if schedule.get("status") != "PASS":
            raise BaselineSuiteLiveError("native_schedule_failed")
        lifecycle = normalize_schedule_lifecycle(
            evidence=schedule,
            method=method,
            expected_sequences=expected,
        )
        final_state = await _namespace_state(runtime.graphiti.driver, namespace)
        expected_names = sorted(episode.name for episode in episodes)
        if final_state["episode_names"] != expected_names:
            raise BaselineSuiteLiveError("final_namespace_episode_mismatch")
        store.mark_quality_pending()

        episode_metrics: list[dict[str, Any]] = []
        for lifecycle_row in lifecycle:
            sequence = int(lifecycle_row["source_sequence"])
            identity = _identity(block, sequence)
            queue_row = {
                **identity,
                "stream": "queue",
                **{
                    key: lifecycle_row[key]
                    for key in (
                        "arrival_ts_ns",
                        "enqueue_ts_ns",
                        "service_start_ts_ns",
                        "publication_ts_ns",
                        "terminal_ts_ns",
                        "queue_depth_at_enqueue",
                        "worker_id",
                        "caller_return_ts_ns",
                    )
                },
            }
            writers["queue"].write(queue_row)
            work = graph_work[sequence]
            if method == "P(C=2)":
                derived_work = {
                    "attribution_status": work["attribution_status"],
                }
            else:
                derived_work = {
                    "nodes_before": work["nodes_before"],
                    "nodes_after": work["nodes_after"],
                    "relationships_before": work["relationships_before"],
                    "relationships_after": work["relationships_after"],
                    "attribution_status": work["attribution_status"],
                }
            metric = derive_episode_metrics(
                identity=ObservabilityIdentity(**identity),
                spans=raw_spans[sequence],
                queue_event={
                    key: lifecycle_row[key]
                    for key in (
                        "arrival_ts_ns",
                        "enqueue_ts_ns",
                        "service_start_ts_ns",
                        "publication_ts_ns",
                        "terminal_ts_ns",
                        "queue_depth_at_enqueue",
                    )
                },
                graph_work=derived_work,
            )
            writers["per_episode_metrics"].write(metric)
            episode_metrics.append(metric)

        stage = "quality"
        quality_identity: Mapping[str, Any] | None = None
        if mode == "development":
            quality_adapters = build_baseline_quality_adapters(
                env=env,
                frozen_baseline=_load_json(NATIVE_V2_FREEZE),
            )
            retrieval_graph = validate_read_only_quality_graph(
                construction_graph=runtime.graphiti,
                retrieval_graph=retrieval_runtime.graphiti,
            )
            quality = await run_baseline_quality_chain(
                graph=retrieval_graph,
                record=record,
                episodes=episodes,
                history_id=history_id,
                namespace=namespace,
                run_id=namespace,
                reader=quality_adapters["reader"],
                judge=quality_adapters["judge"],
            )
            quality_identity = quality_adapters["quality_identity"]
            quality_row = validate_observability_record(
                {
                    **_identity(block, 0),
                    "stream": "quality",
                    "record_scope": "history",
                    "quality_identity": quality_identity,
                    "result": quality,
                }
            )
            writers["quality"].write(quality_row)
            quality_metrics = {
                "qa_accuracy": quality["qa_accuracy"],
                "evidence_recall_at_10": quality["retrieval"][
                    "evidence_recall_at_10"
                ],
            }
            quality_status = "MEASURED"
        else:
            quality = {"status": "NOT_RUN_CANARY"}
            quality_metrics = {
                "qa_accuracy": None,
                "evidence_recall_at_10": None,
            }
            quality_status = "NOT_RUN_CANARY"

        aggregate = aggregate_history_metrics(
            identity=ObservabilityIdentity(**_identity(block, 0)),
            episode_metrics=episode_metrics,
            quality=quality_metrics,
            serial_baseline=method == "U0",
        )
        aggregate["metrics"]["direct_violations"] = None
        aggregate["metrics"]["direct_violations_status"] = (
            "NOT_EVALUATED_IN_LIGHTWEIGHT_BASELINE_SUITE"
        )
        history_metrics_path = telemetry_root / "per_history_metrics.json"
        atomic_write_json(history_metrics_path, aggregate)
        telemetry_hashes = {
            f"{name}.jsonl": sha256_file(telemetry_root / f"{name}.jsonl")
            for name in TELEMETRY_STREAMS
        }
        telemetry_hashes["per_history_metrics.json"] = sha256_file(
            history_metrics_path
        )
        result = {
            "schema_version": "membind.paper-eval-v3.baseline-block-output.v1",
            "run_id": namespace,
            "method": method,
            "history_id": history_id,
            "mode": mode,
            "status": "PASS",
            "episode_count": len(episodes),
            "schedule_summary": dict(schedule["summary"]),
            "metrics": dict(aggregate["metrics"]),
            "latency_distributions": dict(aggregate["latency_distributions"]),
            "work_volume": dict(aggregate["work_volume"]),
            "graph_work": dict(aggregate["graph_work"]),
            "quality": quality,
            "quality_status": quality_status,
            "quality_identity": dict(quality_identity or {}),
            "final_graph": {
                "node_count": final_state["node_count"],
                "relationship_count": final_state["relationship_count"],
                "episodic_count": len(final_state["episode_names"]),
                "episode_names_match_expected": True,
            },
            "telemetry_sha256": telemetry_hashes,
            "scheduler_events_sha256": sha256_file(store.events_path),
        }
        completion = store.complete(result)
        completed = True
        print(
            json.dumps(
                {
                    "event": "block_completed",
                    "method": method,
                    "history_id": history_id,
                    "episode_count": len(episodes),
                    "result_payload_sha256": completion[
                        "result_payload_sha256"
                    ],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return {**completion, "result": result}
    except BaseException as error:
        if not completed:
            try:
                store.fail(_qualified_error(error), _safe_stage(stage))
            except Exception:
                pass
        raise
    finally:
        if measurement_handle is not None:
            measurement_handle.restore()
        if phase_handle is not None:
            phase_handle.restore()
        components: list[tuple[Any, str]] = []
        if quality_adapters is not None:
            components.extend(
                [
                    (quality_adapters.get("judge"), "aclose"),
                    (quality_adapters.get("transport"), "aclose"),
                ]
            )
        if runtime is not None:
            components.append((runtime.graphiti, "close"))
        for component, method_name in components:
            close = getattr(component, method_name, None)
            if callable(close):
                try:
                    await _await(close())
                except Exception:
                    pass


def execute_baseline_block_sync(
    *,
    block: Mapping[str, Any],
    block_root: Path,
) -> dict[str, Any]:
    """Construct read-only quality runtime before entering the event loop."""

    legacy_source = str(LEGACY_SRC)
    if legacy_source not in sys.path:
        sys.path.insert(0, legacy_source)
    retrieval_runtime = None
    if block.get("mode") == "development":
        from graphiti_native import load_env_file
        from .s2_r0_live import build_read_only_graphiti

        retrieval_runtime = build_read_only_graphiti(
            env=load_env_file(LEGACY / ".env")
        )
    async def run_and_close() -> dict[str, Any]:
        try:
            return await execute_baseline_block(
                block=block,
                block_root=block_root,
                retrieval_runtime=retrieval_runtime,
            )
        finally:
            if retrieval_runtime is not None:
                close = getattr(retrieval_runtime.graphiti, "close", None)
                if callable(close):
                    await _await(close())

    return asyncio.run(run_and_close())


__all__ = [
    "BaselineSuiteLiveError",
    "execute_baseline_block",
    "execute_baseline_block_sync",
]
