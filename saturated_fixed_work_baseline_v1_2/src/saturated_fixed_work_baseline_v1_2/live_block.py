"""Graphiti block orchestration shared by qualification, rehearsal, and L3."""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, AttemptStore
from .contracts import EpisodeInput, ResumeIdentity
from .correctness import reduce_direct_semantic_evidence
from .graphiti_adapter import GraphitiNativeAdapter
from .instrumentation import TerminalProbe, execute_instrumented_block
from .lifecycle import Span, concurrency_summary, ordering_summary
from .live import FormalBlock
from .schedules import Method


class LiveBlockError(ValueError):
    """A live block violated isolation, completeness, or evidence contracts."""


@dataclass(frozen=True, slots=True)
class LiveBlockDependencies:
    runtime_factory: Callable[[str, Path], Any]
    graph_exporter: Callable[
        [Any, tuple[EpisodeInput, ...], str],
        Mapping[str, Any] | Awaitable[Mapping[str, Any]],
    ]
    recorder_factory: Callable[[], Any]
    instrumentation_installer: Callable[[Any, Any], Any]
    measurement_installer: Callable[[Any, Any], Any]
    episode_source: Any
    service_idle: Callable[[], bool | Awaitable[bool]]
    sampler_factory: Callable[[Path], Any] | None = None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise LiveBlockError("LIVE_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_bytes(value) + b"\n"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _graph_is_empty(graph: Mapping[str, Any]) -> bool:
    return all(not graph.get(field) for field in ("entities", "edges", "episodes"))


def _validate_complete_graph(
    graph: Mapping[str, Any], episodes: Sequence[EpisodeInput]
) -> None:
    rows = graph.get("episodes")
    if not isinstance(rows, list) or len(rows) != len(episodes):
        raise LiveBlockError("FINAL_GRAPH_EPISODE_COUNT_MISMATCH")
    expected = {
        (episode.source_sequence, episode.source_hash, episode.session_id)
        for episode in episodes
    }
    observed = {
        (
            row.get("source_sequence"),
            row.get("source_hash"),
            row.get("session_id"),
        )
        for row in rows
        if isinstance(row, Mapping)
    }
    if observed != expected:
        raise LiveBlockError("FINAL_GRAPH_EPISODE_PROVENANCE_MISMATCH")


def _trace_metrics(envelopes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spans = [
        span
        for envelope in envelopes
        for span in envelope.get("spans", [])
        if isinstance(span, Mapping)
    ]
    llm_logical = [
        span
        for span in spans
        if span.get("phase") == "llm"
        and span.get("operation_class") == "logical-call"
    ]
    llm_transport = [span for span in spans if span.get("phase") == "llm-transport"]
    embeddings = [span for span in spans if span.get("phase") == "embedding"]
    db_writes = [
        span
        for span in spans
        if span.get("phase") == "database-transaction"
        and span.get("operation_class") == "write"
    ]

    def percentile(selected: Sequence[Mapping[str, Any]], quantile: float) -> float | None:
        durations = sorted(
            int(span.get("duration_ns") or 0)
            for span in selected
            if isinstance(span.get("duration_ns"), int)
            and not isinstance(span.get("duration_ns"), bool)
            and int(span["duration_ns"]) >= 0
        )
        if not durations:
            return None
        index = max(0, math.ceil(quantile * len(durations)) - 1)
        return durations[index] / 1_000_000_000

    database = [span for span in spans if span.get("phase") == "database"]
    direct = reduce_direct_semantic_evidence(envelopes)
    return {
        "llm_input_tokens": sum(
            int((span.get("metadata") or {}).get("input_tokens") or 0)
            for span in llm_logical
        ),
        "llm_logical_calls": len(llm_logical),
        "llm_transport_attempts": len(llm_transport),
        "embedding_items": sum(
            int((span.get("metadata") or {}).get("text_count") or 0)
            for span in embeddings
        ),
        "db_writes": len(db_writes),
        "instrumentation_error_spans": sum(span.get("status") != "ok" for span in spans),
        **{
            f"llm_duration_{label}_s": percentile(llm_logical, quantile)
            for label, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
        },
        **{
            f"embedding_duration_{label}_s": percentile(embeddings, quantile)
            for label, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
        },
        **{
            f"db_duration_{label}_s": percentile(database, quantile)
            for label, quantile in (("p50", 0.50), ("p95", 0.95), ("p99", 0.99))
        },
        "llm_metrics_availability": "MEASURED" if llm_logical else "NOT_EVALUATED",
        "embedding_metrics_availability": "MEASURED" if embeddings else "NOT_EVALUATED",
        "db_metrics_availability": "MEASURED" if database else "NOT_EVALUATED",
        "direct_semantic_violations": direct["direct_semantic_violations"],
        "direct_semantic_evidence_availability": direct["availability"],
        "direct_semantic_violations_by_observation": direct["by_observation"],
        "direct_semantic_evidence_records": direct["direct_evidence_records"],
        "ordering_observations_counted_as_direct": direct[
            "ordering_observations_counted_as_direct"
        ],
    }


def _completion_order(store: AttemptStore) -> list[int]:
    return [
        int(row["source_sequence"])
        for row in store.recover_journal().events
        if row.get("event") == "PUBLICATION_DURABLE"
    ]


async def execute_live_block(
    *,
    repository_root: Path,
    run_root: Path,
    block: FormalBlock,
    identity: ResumeIdentity,
    episodes: Sequence[EpisodeInput],
    dependencies: LiveBlockDependencies,
    source_tokens: int,
    clock: Callable[[], int] = time.monotonic_ns,
    attempt_store_factory: Callable[[Path, Any], Any] = AttemptStore.create,
) -> dict[str, Any]:
    del repository_root
    if not isinstance(block, FormalBlock) or not isinstance(
        getattr(identity, "namespace", None), str
    ):
        raise LiveBlockError("LIVE_BLOCK_IDENTITY_INVALID")
    selected = tuple(episodes)
    if not selected or any(not isinstance(row, EpisodeInput) for row in selected):
        raise LiveBlockError("LIVE_BLOCK_EPISODES_INVALID")
    if [row.source_sequence for row in selected] != list(range(len(selected))):
        raise LiveBlockError("LIVE_BLOCK_SOURCE_SEQUENCE_INVALID")
    if any(
        row.history_id != block.history_id or row.namespace != block.namespace
        for row in selected
    ):
        raise LiveBlockError("LIVE_BLOCK_EPISODE_IDENTITY_MISMATCH")
    if identity.namespace != block.namespace:
        raise LiveBlockError("LIVE_BLOCK_RESUME_NAMESPACE_MISMATCH")
    if isinstance(source_tokens, bool) or not isinstance(source_tokens, int) or source_tokens <= 0:
        raise LiveBlockError("LIVE_BLOCK_SOURCE_TOKENS_INVALID")

    store = attempt_store_factory(run_root / "blocks" / block.block_id, identity)
    expected_attempt_id = f"attempt-{block.attempt_ordinal:03d}"
    if store.root.name != expected_attempt_id:
        store.record_failure(
            "saturated_fixed_work_baseline_v1_2.live_block.LiveBlockError",
            {
                "stage": "attempt_identity",
                "expected_attempt_id": expected_attempt_id,
                "actual_attempt_id": store.root.name,
            },
        )
        raise LiveBlockError("LIVE_BLOCK_ATTEMPT_ORDINAL_MISMATCH")
    authority_path = store.root / "live_authority.json"
    authority = {
        "schema_version": "membind.saturated-fixed-work.live-authority.v1",
        "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2",
        "run_id": block.run_id,
        "block_id": block.block_id,
        "method": block.method.value,
        "history_id": block.history_id,
        "namespace": block.namespace,
        "attempt_ordinal": block.attempt_ordinal,
        "cache_salt_sha256": hashlib.sha256(block.cache_salt.encode("ascii")).hexdigest(),
        "resume_identity": asdict(identity),
    }
    authority["payload_sha256"] = _payload_hash(authority)
    _write_new_json(authority_path, authority)

    runtime: Any | None = None
    phase_handle: Any | None = None
    measurement_handle: Any | None = None
    recorder: Any | None = None
    trace_envelopes: list[dict[str, Any]] = []
    update_intervals: list[Span] = []
    canonical_graph: Mapping[str, Any] | None = None
    sampler: Any | None = None
    sampler_started = False
    sampler_stopped = False
    sampler_summary: Mapping[str, Any] | None = None
    try:
        runtime = await _await(dependencies.runtime_factory(block.cache_salt, authority_path))
        graphiti = getattr(runtime, "graphiti", None)
        if graphiti is None:
            raise LiveBlockError("GRAPHITI_RUNTIME_MISSING")
        try:
            initial = await _await(
                dependencies.graph_exporter(graphiti, selected, block.namespace)
            )
        except (KeyError, IndexError):
            raise LiveBlockError("FRESH_NAMESPACE_NOT_EMPTY") from None
        if not isinstance(initial, Mapping):
            raise LiveBlockError("INITIAL_GRAPH_EXPORT_INVALID")
        if not _graph_is_empty(initial):
            raise LiveBlockError("FRESH_NAMESPACE_NOT_EMPTY")

        recorder = dependencies.recorder_factory()
        phase_handle = dependencies.instrumentation_installer(graphiti, recorder)
        measurement_handle = dependencies.measurement_installer(graphiti, recorder)
        adapter = GraphitiNativeAdapter(graphiti, source=dependencies.episode_source)
        if dependencies.sampler_factory is not None:
            sampler = dependencies.sampler_factory(store.root / "telemetry.jsonl")
            if sampler is None or not callable(getattr(sampler, "start", None)) or not callable(
                getattr(sampler, "stop", None)
            ):
                raise LiveBlockError("TELEMETRY_SAMPLER_INVALID")
            await _await(sampler.start())
            sampler_started = True

        async def traced_add(episode: EpisodeInput) -> Any:
            start_ns = clock()
            episode_id = f"{episode.history_id}:{episode.source_sequence}"
            try:
                with recorder.episode_scope(
                    block.namespace, episode_id, episode.source_sequence
                ):
                    return await adapter.add_episode(episode)
            finally:
                end_ns = max(clock(), start_ns + 1)
                update_intervals.append(
                    Span(
                        span_id=f"whole-update-{episode.source_sequence:04d}",
                        phase="whole-update",
                        start_ns=start_ns,
                        end_ns=end_ns,
                        parent_span_id=None,
                    )
                )
                envelope = dict(
                    recorder.episode_envelope(
                        block.namespace, episode_id, episode.source_sequence
                    )
                )
                envelope.update(
                    {
                        "block_id": block.block_id,
                        "attempt_id": store.root.name,
                        "method": block.method.value,
                        "history_id": block.history_id,
                        "namespace": block.namespace,
                        "source_hash": episode.source_hash,
                    }
                )
                trace_envelopes.append(envelope)
                _append_jsonl(store.root / "native_trace.jsonl", envelope)

        async def snapshot() -> Mapping[str, Any]:
            nonlocal canonical_graph
            graph = await _await(
                dependencies.graph_exporter(graphiti, selected, block.namespace)
            )
            if not isinstance(graph, Mapping):
                raise LiveBlockError("FINAL_GRAPH_EXPORT_INVALID")
            _validate_complete_graph(graph, selected)
            canonical_graph = dict(graph)
            return graph

        result = await execute_instrumented_block(
            method=block.method,
            episodes=selected,
            add_episode=traced_add,
            store=store,
            snapshot_graph=snapshot,
            terminal_probe=TerminalProbe.clean,
            service_idle=dependencies.service_idle,
            clock=clock,
        )
        if sampler is not None:
            stopped = await _await(sampler.stop())
            sampler_stopped = True
            if not isinstance(stopped, Mapping):
                raise LiveBlockError("TELEMETRY_SAMPLER_SUMMARY_INVALID")
            sampler_summary = dict(stopped)
        if canonical_graph is None:
            raise LiveBlockError("CANONICAL_GRAPH_MISSING")
        trace_metrics = _trace_metrics(trace_envelopes)
        if trace_metrics["instrumentation_error_spans"]:
            raise LiveBlockError("INSTRUMENTATION_ERROR_SPANS_PRESENT")
        concurrency = concurrency_summary(update_intervals)
        if block.method is Method.B0_NATIVE_SERIAL and concurrency["active_max"] != 1:
            raise LiveBlockError("B0_WHOLE_UPDATE_CONCURRENCY_INVALID")
        completion = _completion_order(store)
        ordering = ordering_summary(list(range(len(selected))), completion)
        source_coverage = (
            sampler_summary.get("source_coverage", {})
            if sampler_summary is not None
            else {}
        )
        sampler_valid = bool(
            sampler_summary is not None
            and isinstance(source_coverage, Mapping)
            and source_coverage
            and sampler_summary.get("coverage", 0) >= 0.9
            and all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0.9
                for value in source_coverage.values()
            )
            and sampler_summary.get("gap_p95_s", float("inf")) <= 1.5
            and sampler_summary.get("gap_max_s", float("inf")) <= 2.5
        )
        block_metrics = {
            **result,
            **trace_metrics,
            "block_id": block.block_id,
            "attempt_id": store.root.name,
            "attempt_ordinal": block.attempt_ordinal,
            "source_tokens": source_tokens,
            "build_makespan_s": result["build_makespan_ns"] / 1_000_000_000,
            "source_tokens_per_s": source_tokens
            / (result["build_makespan_ns"] / 1_000_000_000),
            "whole_update_active_mean": concurrency["active_mean"],
            "whole_update_active_max": concurrency["active_max"],
            "whole_update_active_k_time_ns": concurrency["active_k_time_ns"],
            "inversion_count": ordering["inversion_count"],
            "inversion_density": ordering["inversion_density"],
            "kendall_tau": ordering["kendall_tau"],
            "max_displacement": ordering["max_displacement"],
            "canonical_exact_match": (
                True if block.method is Method.B0_NATIVE_SERIAL else None
            ),
            "phase_metrics_availability": trace_metrics["llm_metrics_availability"],
            "embedding_metrics_availability": trace_metrics[
                "embedding_metrics_availability"
            ],
            "db_metrics_availability": trace_metrics["db_metrics_availability"],
            "resource_availability": (
                "MEASURED" if sampler_valid else "NOT_EVALUATED"
            ),
            "sampler_coverage": (
                sampler_summary.get("coverage")
                if sampler_summary is not None
                else None
            ),
            "sampler_source_coverage": dict(source_coverage),
            "sampler_gap_p95_s": (
                sampler_summary.get("gap_p95_s")
                if sampler_summary is not None
                else None
            ),
            "sampler_gap_max_s": (
                sampler_summary.get("gap_max_s")
                if sampler_summary is not None
                else None
            ),
        }
        if hasattr(identity, "resource_sha256"):
            block_metrics["resource_envelope_id"] = identity.resource_sha256
        elif hasattr(identity, "execution_sha256"):
            block_metrics["execution_identity_sha256"] = identity.execution_sha256
        _write_new_json(
            store.root / "canonical_graph.json", dict(canonical_graph)
        )
        _write_new_json(store.root / "block_metrics.json", block_metrics)
        if sampler_summary is not None:
            _write_new_json(store.root / "sampler_summary.json", sampler_summary)
        return {**block_metrics, "attempt_root": str(store.root)}
    except BaseException as error:
        if not (
            store.failure_path.exists()
            or store.timeout_path.exists()
            or store.seal_path.exists()
        ):
            try:
                store.record_failure(
                    f"{type(error).__module__}.{type(error).__qualname__}",
                    {"stage": "live_block_orchestration"},
                )
            except (ArtifactError, OSError):
                pass
        raise
    finally:
        if sampler is not None and sampler_started and not sampler_stopped:
            try:
                await _await(sampler.stop())
            except Exception:
                pass
        if measurement_handle is not None:
            measurement_handle.restore()
        if phase_handle is not None:
            phase_handle.restore()
        graphiti = getattr(runtime, "graphiti", None) if runtime is not None else None
        close = getattr(graphiti, "close", None)
        if callable(close):
            try:
                await _await(close())
            except Exception:
                pass


__all__ = [
    "LiveBlockDependencies",
    "LiveBlockError",
    "execute_live_block",
]
