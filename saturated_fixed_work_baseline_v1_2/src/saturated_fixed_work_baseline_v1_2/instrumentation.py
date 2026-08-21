"""Block-level instrumentation boundary shared by fake and live Graphiti runs."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .artifacts import AttemptStore, SealEvidence
from .contracts import EpisodeInput
from .lifecycle import reduce_block_timing
from .schedules import (
    Method,
    run_b0_native_serial,
    run_b1_naive_whole_update_async,
)


@dataclass(frozen=True, slots=True)
class TerminalProbe:
    open_spans: int
    open_requests: int
    open_transactions: int
    orphan_tasks: int
    unobserved_exceptions: int

    @classmethod
    def clean(cls) -> "TerminalProbe":
        return cls(0, 0, 0, 0, 0)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


async def execute_instrumented_block(
    *,
    method: Method,
    episodes: Sequence[EpisodeInput],
    add_episode: Callable[[EpisodeInput], Awaitable[Any]],
    store: AttemptStore,
    snapshot_graph: Callable[[], Any],
    terminal_probe: Callable[[], TerminalProbe],
    service_idle: Callable[[], bool | Awaitable[bool]],
    clock: Callable[[], int] = time.monotonic_ns,
) -> dict[str, Any]:
    selected = tuple(episodes)
    if not selected:
        raise ValueError("BLOCK_EPISODES_EMPTY")
    if any(episode.namespace != store.identity.namespace for episode in selected):
        raise ValueError("BLOCK_NAMESPACE_IDENTITY_MISMATCH")
    store.append_event(
        {
            "event": "BLOCK_STARTED",
            "monotonic_ns": clock(),
            "source_sequence": None,
            "method": method.value,
        }
    )
    t0_ns = clock()

    def event_sink(event: dict[str, Any]) -> None:
        store.append_event({**event, "method": method.value})

    runner = (
        run_b0_native_serial
        if method is Method.B0_NATIVE_SERIAL
        else run_b1_naive_whole_update_async
    )
    try:
        schedule = await runner(
            selected,
            add_episode,
            event_sink=event_sink,
            clock=clock,
        )
    except BaseException as error:
        store.record_failure(
            f"{type(error).__module__}.{type(error).__qualname__}",
            {"stage": "construction"},
        )
        raise
    t_durable_complete_ns = clock()

    probe = terminal_probe()
    if not isinstance(probe, TerminalProbe):
        raise ValueError("TERMINAL_PROBE_INVALID")
    first_graph = await _await(snapshot_graph())
    second_graph = await _await(snapshot_graph())
    snapshot_hashes = (_hash(first_graph), _hash(second_graph))
    idle = await _await(service_idle())
    t_validated_seal_ns = clock()
    timing = reduce_block_timing(
        t0_ns=t0_ns,
        t_last_submit_ns=max(
            (
                int(event["monotonic_ns"])
                for event in store.recover_journal().events
                if event.get("event") in {"SUBMIT", "TASK_CREATED"}
            ),
            default=t0_ns,
        ),
        t_durable_complete_ns=t_durable_complete_ns,
        t_validated_seal_ns=t_validated_seal_ns,
    )
    seal = store.seal(
        SealEvidence(
            episode_task_count=len(selected),
            terminal_episode_task_count=len(schedule.outcomes),
            open_spans=probe.open_spans,
            open_requests=probe.open_requests,
            open_transactions=probe.open_transactions,
            orphan_tasks=probe.orphan_tasks,
            unobserved_exceptions=probe.unobserved_exceptions,
            service_idle=idle is True,
            canonical_snapshot_hashes=snapshot_hashes,
        )
    )
    return {
        "schema_version": "membind.saturated-fixed-work.block-result.v1",
        "method": method.value,
        "history_id": selected[0].history_id,
        "namespace": selected[0].namespace,
        "valid": True,
        "episode_count": len(selected),
        "created_sequences": list(schedule.created_sequences),
        "feeder_workload_await_count": schedule.feeder_workload_await_count,
        "application_gate_count": schedule.application_gate_count,
        "artificial_sleep_count": schedule.artificial_sleep_count,
        "configured_max_inflight": schedule.configured_max_inflight,
        "t0_ns": t0_ns,
        "t_durable_complete_ns": t_durable_complete_ns,
        "t_validated_seal_ns": t_validated_seal_ns,
        **timing,
        "canonical_graph_hash": snapshot_hashes[0],
        "seal_payload_sha256": seal["payload_sha256"],
    }


def _metric(
    name: str,
    *,
    level: str,
    unit: str,
    direction: str,
    formula: str,
    numerator: str | None,
    denominator: str | None,
    source: str,
    clock: str,
    scope: str,
    availability: str,
    core: bool,
    interpretation: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1.2",
        "level": level,
        "unit": unit,
        "better_direction": direction,
        "formula": formula,
        "numerator": numerator,
        "denominator": denominator,
        "source": source,
        "clock": clock,
        "attribution_scope": scope,
        "availability": availability,
        "core_validity_gate": core,
        "interpretation": interpretation,
    }


def metric_dictionary() -> dict[str, dict[str, Any]]:
    specs = (
        ("build_makespan_s", "history", "s", "lower", "t_durable_complete-t0", "elapsed monotonic ns", "1e9", "block lifecycle", "runner monotonic", "block-exclusive", "MEASURED", True, "Construction only; validation and QA excluded."),
        ("submission_span_s", "history", "s", "lower", "t_last_submit-t_first_submit", "submission interval ns", "1e9", "schedule journal", "runner monotonic", "block-exclusive", "DERIVED", True, "Feeder submission duration."),
        ("drain_tail_s", "history", "s", "lower", "t_durable_complete-t_last_submit", "drain interval ns", "1e9", "block lifecycle", "runner monotonic", "block-exclusive", "DERIVED", True, "Work remaining after last submission."),
        ("validation_seal_latency_s", "history", "s", "lower", "t_validated_seal-t_durable_complete", "validation interval ns", "1e9", "block lifecycle", "runner monotonic", "block-exclusive", "DERIVED", False, "Excluded from build makespan."),
        ("source_tokens_per_s", "history", "tokens/s", "higher", "source_tokens/build_makespan_s", "source tokens", "build makespan s", "frozen tokenizer and lifecycle", "runner monotonic", "block-exclusive", "DERIVED", False, "Input-normalized construction throughput."),
        ("whole_update_active_mean", "history", "updates", "descriptive", "active integral/interval union", "active update ns", "union ns", "schedule spans", "runner monotonic", "block-exclusive", "DERIVED", False, "Time-weighted update concurrency."),
        ("whole_update_active_max", "history", "updates", "descriptive", "max simultaneous intervals", "maximum active", None, "schedule spans", "runner monotonic", "block-exclusive", "DERIVED", True, "B0 must be one; B1 is unconstrained by the app."),
        ("whole_update_active_k_time_ns", "history", "ns by active-k", "descriptive", "partition interval-union time by simultaneous whole updates", "active-k nanoseconds", "whole-update union ns", "schedule spans", "runner monotonic", "block-exclusive", "DERIVED", False, "Full concurrency residence distribution; categories sum to the interval union."),
        ("inversion_count", "history", "pairs", "descriptive", "discordant completion pairs relative to source order", "discordant pairs", None, "durable completion journal", "runner monotonic", "block-exclusive", "DERIVED", False, "Ordering observation only unless separately supported by direct causal evidence."),
        ("inversion_density", "history", "fraction", "descriptive", "inversion_count / possible ordered pairs", "discordant pairs", "n*(n-1)/2", "durable completion journal", "runner monotonic", "block-exclusive", "DERIVED", False, "Normalized ordering observation."),
        ("kendall_tau", "history", "coefficient", "descriptive", "1 - 2*inversion_count/possible ordered pairs", "concordance minus discordance", "possible ordered pairs", "durable completion journal", "runner monotonic", "block-exclusive", "DERIVED", False, "Completion-order rank agreement with source order."),
        ("max_displacement", "history", "positions", "descriptive", "max absolute source-rank minus completion-rank", "maximum rank displacement", None, "durable completion journal", "runner monotonic", "block-exclusive", "DERIVED", False, "Largest completion reordering displacement."),
        ("llm_input_tokens", "history", "tokens", "descriptive", "sum logical-call prompt tokens", "prompt tokens", None, "native instrumentation", "runner monotonic", "block-exclusive", "MEASURED", False, "Construction work-volume control."),
        ("llm_logical_calls", "history", "calls", "descriptive", "count logical LLM spans", "logical calls", None, "native instrumentation", "runner monotonic", "block-exclusive", "MEASURED", False, "Logical model workload."),
        ("llm_transport_attempts", "history", "attempts", "lower", "count transport spans", "HTTP attempts", None, "native instrumentation", "runner monotonic", "block-exclusive", "MEASURED", False, "Includes existing client retries."),
        ("embedding_items", "history", "items", "descriptive", "sum embedding item counts", "items", None, "native instrumentation", "runner monotonic", "block-exclusive", "MEASURED", False, "Embedding work volume."),
        ("db_writes", "history", "operations", "descriptive", "count write operations", "write operations", None, "native instrumentation", "runner monotonic", "block-exclusive", "MEASURED", False, "Database work volume."),
        ("llm_duration_p50_s", "request", "s", "lower", "nearest-rank p50 logical LLM duration", "ordered logical-call durations", "logical LLM calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Logical LLM median latency."),
        ("llm_duration_p95_s", "request", "s", "lower", "nearest-rank p95 logical LLM duration", "ordered logical-call durations", "logical LLM calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Logical LLM tail latency."),
        ("llm_duration_p99_s", "request", "s", "lower", "nearest-rank p99 logical LLM duration", "ordered logical-call durations", "logical LLM calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Logical LLM extreme-tail latency."),
        ("embedding_duration_p50_s", "request", "s", "lower", "nearest-rank p50 embedding duration", "ordered embedding durations", "embedding calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Embedding median latency."),
        ("embedding_duration_p95_s", "request", "s", "lower", "nearest-rank p95 embedding duration", "ordered embedding durations", "embedding calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Embedding tail latency."),
        ("embedding_duration_p99_s", "request", "s", "lower", "nearest-rank p99 embedding duration", "ordered embedding durations", "embedding calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Embedding extreme-tail latency."),
        ("db_duration_p50_s", "request", "s", "lower", "nearest-rank p50 database duration", "ordered database durations", "database calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Database median latency."),
        ("db_duration_p95_s", "request", "s", "lower", "nearest-rank p95 database duration", "ordered database durations", "database calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Database tail latency."),
        ("db_duration_p99_s", "request", "s", "lower", "nearest-rank p99 database duration", "ordered database durations", "database calls", "native instrumentation", "runner monotonic", "block-exclusive", "DERIVED", False, "Database extreme-tail latency."),
        ("vllm_waiting_mean", "sample", "requests", "lower", "time-weighted waiting gauge", "waiting request-seconds", "sample duration s", "vLLM 0.26.0 /metrics", "server sampling", "process-global", "MEASURED", False, "Only causal when block-exclusive attribution passes."),
        ("gpu_kv_cache_usage_p95", "sample", "fraction", "lower", "p95 sampled KV gauge", "ordered KV samples", "sample count", "vLLM 0.26.0 /metrics", "server sampling", "process-global", "MEASURED", False, "KV pressure diagnostic."),
        ("gpu_utilization_mean", "sample", "percent", "descriptive", "time-weighted GPU utilization", "utilization-percent-seconds", "sample duration s", "nvidia-smi sampler", "sampler monotonic", "sampled", "MEASURED", False, "Physical resource utilization."),
        ("direct_semantic_violations", "history", "count", "lower", "count pre-registered directly evidenced violations", "direct violation records", None, "correctness ledger", "runner monotonic", "block-exclusive", "DERIVED", True, "Excludes ordering-only observations."),
        ("direct_semantic_evidence_availability", "history", "availability state", "descriptive", "availability classification of explicit causal C2 evidence", "validated direct evidence records", None, "native C2 span metadata", "runner monotonic", "block-exclusive", "DERIVED", False, "DERIVED zero means no direct causal record; it does not promote ordering observations."),
        ("ordering_observations_counted_as_direct", "history", "count", "lower", "ordering-only records admitted to direct semantic ledger", "misclassified ordering records", None, "correctness reducer", "runner monotonic", "block-exclusive", "DERIVED", True, "Must remain zero by protocol definition."),
        ("sampler_coverage", "sample", "fraction", "higher", "min(actual_samples/expected_samples, 1)", "actual samples", "expected 1 Hz samples", "durable telemetry journal", "sampler monotonic", "sampled", "DERIVED", True, "Core resource coverage gate."),
        ("sampler_gap_p95_s", "sample", "s", "lower", "nearest-rank p95 adjacent sample gap", "ordered adjacent gaps", "sample gaps", "durable telemetry journal", "sampler monotonic", "sampled", "DERIVED", True, "Must not exceed the pre-registered 1.5 second gate."),
        ("sampler_gap_max_s", "sample", "s", "lower", "maximum adjacent sample gap", "maximum adjacent gap", None, "durable telemetry journal", "sampler monotonic", "sampled", "DERIVED", True, "Must not exceed the pre-registered 2.5 second gate."),
        ("canonical_exact_match", "history", "boolean", "descriptive", "B1 canonical hash equals paired B0", "equal hashes", None, "canonical export", "not applicable", "block-exclusive", "DERIVED", False, "Structural description, not ground truth."),
        ("qa_accuracy_invalid_wrong", "qa", "fraction", "higher", "correct and valid/all QA", "correct valid QA", "all QA", "sealed QA rows", "not applicable", "qa-only", "DERIVED", False, "Invalid rows remain in denominator."),
    )
    return {
        spec[0]: _metric(
            spec[0],
            level=spec[1],
            unit=spec[2],
            direction=spec[3],
            formula=spec[4],
            numerator=spec[5],
            denominator=spec[6],
            source=spec[7],
            clock=spec[8],
            scope=spec[9],
            availability=spec[10],
            core=spec[11],
            interpretation=spec[12],
        )
        for spec in specs
    }


__all__ = [
    "TerminalProbe",
    "execute_instrumented_block",
    "metric_dictionary",
]
