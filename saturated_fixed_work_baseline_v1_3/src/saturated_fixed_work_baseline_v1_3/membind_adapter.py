"""MemBind v3.1 execution policy adapter for the v1.3 fixed-work benchmark.

This module owns only the benchmark-facing contract.  The scheduler, semantic
adapter, compiler, request admission, and runtime remain the frozen v3.1
implementations under ``paper_eval.membind_v31``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MEMBIND_METHOD = "MEMBIND_V31"
MEMBIND_POLICY = "FRONTIER_FIRST_CACHE_AFFINITY"
MEMBIND_QUALIFICATION_SOURCE_COUNT = 12
MEMBIND_COMPILE_WORKERS = 2
MEMBIND_LOOKAHEAD = 2
MEMBIND_BIND_WORKERS = 1
MEMBIND_GLOBAL_LLM_ADMISSION_K = 2


class MemBindAdapterError(ValueError):
    """A benchmark-to-MemBind identity or execution invariant failed."""


@dataclass(frozen=True, slots=True)
class MemBindBlockSpec:
    run_id: str
    block_id: str
    history_id: str
    method: str
    policy: str
    namespace: str
    cache_salt: str
    source_sha256s: tuple[str, ...]
    source_count: int
    arrival_policy: str
    arrival_offsets_ns: tuple[int, ...]
    compile_workers: int
    lookahead: int
    bind_workers: int
    global_llm_admission_k: int


@dataclass(frozen=True, slots=True)
class MemBindExecutionDependencies:
    """Injected live boundary; production uses the existing v3.1 components."""

    hooks: Any
    certification: Any
    live_dependencies: Any
    source_log_builder: Any
    coordinator: Any
    attempt_store_factory: Any


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def build_membind_block_spec(
    *,
    run_id: str,
    namespace: str,
    cache_salt: str,
    source_sha256s: Sequence[str],
    block_id: str = "qualification-membind",
    history_id: str = "07741c45",
) -> MemBindBlockSpec:
    """Bind frozen v3.1 policy knobs to one saturated SFWB source prefix."""

    identities = tuple(source_sha256s)
    if any(
        not isinstance(value, str) or not value
        for value in (run_id, namespace, cache_salt, block_id, history_id)
    ):
        raise MemBindAdapterError("MEMBIND_BLOCK_IDENTITY_INVALID")
    if (
        len(identities) != MEMBIND_QUALIFICATION_SOURCE_COUNT
        or any(not _sha256(value) for value in identities)
        or len(set(identities)) != len(identities)
    ):
        raise MemBindAdapterError("MEMBIND_SOURCE_IDENTITY_INVALID")
    return MemBindBlockSpec(
        run_id=run_id,
        block_id=block_id,
        history_id=history_id,
        method=MEMBIND_METHOD,
        policy=MEMBIND_POLICY,
        namespace=namespace,
        cache_salt=cache_salt,
        source_sha256s=identities,
        source_count=len(identities),
        arrival_policy="SATURATED_ALL_AVAILABLE_AT_T0",
        arrival_offsets_ns=(0,) * len(identities),
        compile_workers=MEMBIND_COMPILE_WORKERS,
        lookahead=MEMBIND_LOOKAHEAD,
        bind_workers=MEMBIND_BIND_WORKERS,
        global_llm_admission_k=MEMBIND_GLOBAL_LLM_ADMISSION_K,
    )


def validate_membind_episodes(
    episodes: Sequence[Any], spec: MemBindBlockSpec
) -> tuple[Any, ...]:
    """Reject workload drift before a runtime or external request is created."""

    if not isinstance(spec, MemBindBlockSpec) or isinstance(episodes, (str, bytes)):
        raise MemBindAdapterError("MEMBIND_EPISODE_IDENTITY_MISMATCH")
    selected = tuple(episodes)
    observed = tuple(getattr(episode, "source_hash", None) for episode in selected)
    if (
        len(selected) != spec.source_count
        or tuple(getattr(episode, "source_sequence", None) for episode in selected)
        != tuple(range(spec.source_count))
        or observed != spec.source_sha256s
        or any(getattr(episode, "history_id", None) != spec.history_id for episode in selected)
        or any(getattr(episode, "namespace", None) != spec.namespace for episode in selected)
    ):
        raise MemBindAdapterError("MEMBIND_EPISODE_IDENTITY_MISMATCH")
    return selected


def normalize_membind_stream_result(
    result: Mapping[str, Any], *, source_count: int
) -> dict[str, Any]:
    """Apply the MemBind publication gate without inventing B0/B1 fields."""

    if (
        not isinstance(result, Mapping)
        or result.get("status") != "PASS"
        or isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or source_count <= 0
        or result.get("source_count") != source_count
        or result.get("publication_source_sequences") != list(range(source_count))
    ):
        raise MemBindAdapterError("MEMBIND_PUBLICATION_COVERAGE_INVALID")
    direct_count = result.get("direct_violation_count")
    direct_rows = result.get("direct_violations")
    if (
        isinstance(direct_count, bool)
        or not isinstance(direct_count, int)
        or direct_count < 0
        or not isinstance(direct_rows, list)
        or len(direct_rows) != direct_count
    ):
        raise MemBindAdapterError("MEMBIND_DIRECT_VIOLATION_ACCOUNTING_INVALID")
    scheduler = result.get("scheduler_observation", {})
    if not isinstance(scheduler, Mapping):
        raise MemBindAdapterError("MEMBIND_SCHEDULER_OBSERVATION_INVALID")
    return {
        "complete_publication_coverage": True,
        "publication_source_sequences": list(range(source_count)),
        "direct_violation_count": direct_count,
        "direct_violations": list(direct_rows),
        "scheduler_observation": dict(scheduler),
    }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _payload_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise MemBindAdapterError("MEMBIND_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, _canonical_bytes(dict(value)) + b"\n")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _snapshot_is_empty(value: object) -> bool:
    return isinstance(value, Mapping) and (
        int(value.get("node_count", 0)) == 0
        and int(value.get("relationship_count", 0)) == 0
        and value.get("episode_names") == []
    )


def _freshness(lifecycle: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    arrivals = {
        int(row["source_sequence"]): int(row["timestamp_ns"])
        for row in lifecycle
        if row.get("event_type") == "ARRIVAL"
    }
    publications = {
        int(row["source_sequence"]): int(row["timestamp_ns"])
        for row in lifecycle
        if row.get("event_type") == "PUBLICATION_DURABLE"
    }
    values = sorted(
        publications[sequence] - arrivals[sequence] for sequence in sorted(publications)
    )

    def percentile(quantile: float) -> int | None:
        if not values:
            return None
        return values[max(0, math.ceil(quantile * len(values)) - 1)]

    return {
        "freshness_p50_ns": percentile(0.50),
        "freshness_p95_ns": percentile(0.95),
        "freshness_p99_ns": percentile(0.99),
        "freshness_max_ns": max(values) if values else None,
    }


class _RecordingAdapter:
    def __init__(self, inner: Any, recorder: Any, namespace: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._namespace = namespace

    def _scope(self, sequence: int) -> Any:
        return self._recorder.episode_scope(
            self._namespace,
            f"07741c45:{sequence}",
            sequence,
        )

    async def prepare(self, compile_input: Any) -> Any:
        sequence = int(compile_input.source.source_sequence)
        with self._scope(sequence):
            return await _await(self._inner.prepare(compile_input))

    async def bind(
        self,
        compile_input: Any,
        artifact: Any,
        *,
        logical_time_ns: int,
    ) -> Any:
        sequence = int(compile_input.source.source_sequence)
        with self._scope(sequence):
            return await _await(
                self._inner.bind(
                    compile_input,
                    artifact,
                    logical_time_ns=logical_time_ns,
                )
            )


async def execute_membind_block(
    *,
    repository_root: Path,
    run_root: Path,
    spec: MemBindBlockSpec,
    identity: Any,
    episodes: Sequence[Any],
    source_tokens: int,
    env: Mapping[str, str],
    dependencies: MemBindExecutionDependencies,
    clock: Any = time.monotonic_ns,
) -> dict[str, Any]:
    """Execute one source-bound MemBind block under SFWB measurement semantics."""

    from paper_eval.membind_v31.admission import AdmissionPolicy
    from paper_eval.membind_v31.live_block import _invoke_runtime_builder
    from saturated_fixed_work_baseline_v1_2.artifacts import SealEvidence
    from saturated_fixed_work_baseline_v1_2.live_block import (
        _trace_metrics,
        _validate_complete_graph,
    )

    selected = validate_membind_episodes(episodes, spec)
    if (
        not isinstance(dependencies, MemBindExecutionDependencies)
        or isinstance(source_tokens, bool)
        or not isinstance(source_tokens, int)
        or source_tokens <= 0
        or not isinstance(env, Mapping)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in env.items())
        or not callable(clock)
    ):
        raise MemBindAdapterError("MEMBIND_EXECUTION_INPUT_INVALID")
    if getattr(identity, "namespace", None) != spec.namespace:
        raise MemBindAdapterError("MEMBIND_EXECUTION_IDENTITY_MISMATCH")

    store = dependencies.attempt_store_factory(
        Path(run_root) / "blocks" / spec.block_id, identity
    )
    if store.root.name != "attempt-001":
        raise MemBindAdapterError("MEMBIND_ATTEMPT_ORDINAL_MISMATCH")
    attempt_root = Path(store.root)
    authority = {
        "schema_version": "membind.saturated-fixed-work.membind-live-authority.v1",
        "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_3",
        "run_id": spec.run_id,
        "block_id": spec.block_id,
        "method": spec.method,
        "history_id": spec.history_id,
        "namespace": spec.namespace,
        "cache_salt_sha256": hashlib.sha256(spec.cache_salt.encode("utf-8")).hexdigest(),
        "execution_identity": asdict(identity),
    }
    authority["payload_sha256"] = _payload_sha256(authority)
    _write_new_json(attempt_root / "live_authority.json", authority)

    hooks = dependencies.hooks
    certification = dependencies.certification
    live = dependencies.live_dependencies
    runtime: Any | None = None
    phase_handle: Any | None = None
    measurement_handle: Any | None = None
    recorder: Any | None = None
    closed = False
    lifecycle_rows: list[dict[str, Any]] = []
    scheduler_rows: list[dict[str, Any]] = []
    prepared_sequences: set[int] = set()
    try:
        verified_certification = certification.verify()
        source_log, raw_hashes = dependencies.source_log_builder(
            selected,
            namespace=spec.namespace,
            reference_time_to_ns=hooks.reference_time_to_ns,
        )
        if tuple(raw_hashes) != spec.source_sha256s or source_log.source_count != spec.source_count:
            raise MemBindAdapterError("MEMBIND_SOURCE_LOG_IDENTITY_MISMATCH")
        compile_hashes = [record.source_sha256 for record in source_log.records]
        manifest: dict[str, Any] = {
            "schema_version": "membind.saturated-fixed-work.membind-block-manifest.v1",
            "run_id": spec.run_id,
            "block_id": spec.block_id,
            "history_id": spec.history_id,
            "method": spec.method,
            "policy": spec.policy,
            "namespace": spec.namespace,
            "source_count": spec.source_count,
            "source_sha256s": list(spec.source_sha256s),
            "compile_source_sha256s": compile_hashes,
            "arrival_policy": spec.arrival_policy,
            "arrival_offsets_ns": list(spec.arrival_offsets_ns),
            "compile_workers": spec.compile_workers,
            "lookahead": spec.lookahead,
            "bind_workers": spec.bind_workers,
            "global_llm_admission_k": spec.global_llm_admission_k,
            "state_cut_certification_sha256": getattr(
                verified_certification, "certification_sha256", None
            ),
        }
        manifest["manifest_sha256"] = _payload_sha256(manifest)
        _write_new_json(attempt_root / "manifest.json", manifest)
        (attempt_root / "private" / "prepared").mkdir(parents=True, exist_ok=False)

        def request_observer(row: Mapping[str, Any]) -> None:
            record = {
                "schema_version": "membind.paper-eval-v3.membind-v31-llm.v1",
                "row": dict(row),
            }
            _append_jsonl(
                attempt_root / "llm.jsonl",
                {"record": record, "record_sha256": _payload_sha256(record)},
            )

        runtime = _invoke_runtime_builder(
            hooks.runtime_builder,
            response_observer=request_observer,
            env={**dict(env), "CONSTRUCTION_CACHE_SALT": spec.cache_salt},
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix=f"sfwb-{spec.run_id}-{spec.history_id}",
            observer=request_observer,
        )
        if inspect.isawaitable(runtime):
            raise MemBindAdapterError("MEMBIND_RUNTIME_BUILDER_ASYNC")
        await _await(hooks.runtime_ready(runtime))
        initial = await _await(hooks.namespace_probe(runtime, spec.namespace))
        if not _snapshot_is_empty(initial):
            raise MemBindAdapterError("MEMBIND_NAMESPACE_NOT_FRESH")
        initial_graph = await _await(
            live.graph_exporter(runtime.graphiti, selected, spec.namespace)
        )
        if not isinstance(initial_graph, Mapping) or any(
            initial_graph.get(field) for field in ("entities", "edges", "episodes")
        ):
            raise MemBindAdapterError("MEMBIND_NAMESPACE_NOT_FRESH")

        recorder = live.recorder_factory()
        phase_handle = live.instrumentation_installer(runtime.graphiti, recorder)
        measurement_handle = live.measurement_installer(runtime.graphiti, recorder)
        adapter = _RecordingAdapter(
            hooks.adapter_factory(runtime, verified_certification), recorder, spec.namespace
        )

        def persist_prepared(artifact: Any) -> None:
            sequence = getattr(artifact, "source_sequence", None)
            if (
                isinstance(sequence, bool)
                or not isinstance(sequence, int)
                or not 0 <= sequence < spec.source_count
                or sequence in prepared_sequences
                or not callable(getattr(artifact, "to_document", None))
            ):
                raise MemBindAdapterError("MEMBIND_PREPARED_COVERAGE_INVALID")
            _write_new_json(
                attempt_root / "private" / "prepared" / f"{sequence:08d}.json",
                artifact.to_document(),
            )
            prepared_sequences.add(sequence)

        lifecycle_map = {
            "arrival": "ARRIVAL",
            "compile_start": "COMPILE_STARTED",
            "prepared_durable": "PREPARED_DURABLE",
            "bind_start": "BIND_STARTED",
            "commit_returned": "COMMIT_RETURNED",
            "publication_durable": "PUBLICATION_DURABLE",
            "arrival_failure": "TERMINAL_FAILURE",
            "compile_failure": "TERMINAL_FAILURE",
            "bind_failure": "TERMINAL_FAILURE",
        }

        def lifecycle(row: Mapping[str, Any]) -> None:
            event_type = lifecycle_map.get(str(row.get("event_type")))
            if event_type is None:
                return
            sequence = int(row["source_sequence"])
            if event_type == "PREPARED_DURABLE" and sequence not in prepared_sequences:
                raise MemBindAdapterError("MEMBIND_PREPARED_COVERAGE_INVALID")
            telemetry = {
                key: value
                for key, value in row.items()
                if key not in {"event_type", "stream_id", "source_sequence", "timestamp_ns"}
            }
            event = {
                "schema_version": "membind.paper-eval-v3.membind-v31-block-event.v1",
                "event_sequence": len(lifecycle_rows),
                "source_sequence": sequence,
                "source_sha256": spec.source_sha256s[sequence],
                "event_type": event_type,
                "timestamp_ns": int(row["timestamp_ns"]),
                "telemetry": telemetry,
            }
            lifecycle_rows.append(event)
            _append_jsonl(
                attempt_root / "events.jsonl",
                {"event": event, "event_sha256": _payload_sha256(event)},
            )
            store.append_event(
                {
                    "event": event_type,
                    "monotonic_ns": event["timestamp_ns"],
                    "source_sequence": sequence,
                    "method": spec.method,
                }
            )

        async def publication_probe(sequence: int, _result: Any) -> bool:
            return bool(
                await _await(
                    hooks.source_visibility_probe(runtime, source_log.record(sequence))
                )
            )

        t0_ns = int(clock())
        store.append_event(
            {
                "event": "BLOCK_STARTED",
                "monotonic_ns": t0_ns,
                "source_sequence": None,
                "method": spec.method,
            }
        )
        stream_result = await dependencies.coordinator(
            stream_id=spec.history_id,
            source_log=source_log,
            arrival_offsets_ns=spec.arrival_offsets_ns,
            adapter=adapter,
            request_client=runtime.admitted_llm,
            compile_workers=spec.compile_workers,
            lookahead=spec.lookahead,
            observer=lifecycle,
            scheduler_observer=lambda row: scheduler_rows.append(dict(row)),
            publication_probe=publication_probe,
            prepared_persistor=persist_prepared,
        )
        t_durable_complete_ns = int(clock())
        normalized = normalize_membind_stream_result(
            stream_result, source_count=spec.source_count
        )
        if prepared_sequences != set(range(spec.source_count)):
            raise MemBindAdapterError("MEMBIND_PREPARED_COVERAGE_INVALID")
        if sum(row["event_type"] == "ARRIVAL" for row in lifecycle_rows) != spec.source_count:
            raise MemBindAdapterError("MEMBIND_ARRIVAL_COVERAGE_INVALID")

        trace_envelopes: list[dict[str, Any]] = []
        for episode in selected:
            episode_id = f"{episode.history_id}:{episode.source_sequence}"
            envelope = dict(
                recorder.episode_envelope(
                    spec.namespace, episode_id, episode.source_sequence
                )
            )
            envelope.update(
                {
                    "block_id": spec.block_id,
                    "attempt_id": attempt_root.name,
                    "method": spec.method,
                    "history_id": spec.history_id,
                    "namespace": spec.namespace,
                    "source_hash": episode.source_hash,
                }
            )
            trace_envelopes.append(envelope)
            _append_jsonl(attempt_root / "native_trace.jsonl", envelope)
        trace_metrics = _trace_metrics(trace_envelopes)
        if trace_metrics["instrumentation_error_spans"]:
            raise MemBindAdapterError("MEMBIND_INSTRUMENTATION_ERROR")

        first_graph = await _await(
            live.graph_exporter(runtime.graphiti, selected, spec.namespace)
        )
        second_graph = await _await(
            live.graph_exporter(runtime.graphiti, selected, spec.namespace)
        )
        if not isinstance(first_graph, Mapping) or not isinstance(second_graph, Mapping):
            raise MemBindAdapterError("MEMBIND_CANONICAL_GRAPH_INVALID")
        _validate_complete_graph(first_graph, selected)
        _validate_complete_graph(second_graph, selected)
        snapshot_hashes = (_payload_sha256(first_graph), _payload_sha256(second_graph))
        if len(set(snapshot_hashes)) != 1:
            raise MemBindAdapterError("MEMBIND_CANONICAL_GRAPH_UNSTABLE")

        request_observation = runtime.admitted_llm.observation()
        if (
            not isinstance(request_observation, Mapping)
            or request_observation.get("active_count") != 0
            or request_observation.get("waiting_count") != 0
            or request_observation.get("configured_limit") != spec.global_llm_admission_k
        ):
            raise MemBindAdapterError("MEMBIND_REQUEST_ADMISSION_NOT_TERMINAL")

        measurement_handle.restore()
        measurement_handle = None
        phase_handle.restore()
        phase_handle = None
        await _await(hooks.close_runtime(runtime))
        closed = True
        runtime = None
        idle = await _await(live.service_idle())
        if idle is not True:
            raise MemBindAdapterError("MEMBIND_SERVICE_NOT_IDLE")
        t_validated_seal_ns = int(clock())
        store.append_event(
            {
                "event": "BLOCK_COMPLETED",
                "monotonic_ns": t_durable_complete_ns,
                "source_sequence": None,
                "method": spec.method,
            }
        )
        seal = store.seal(
            SealEvidence(
                episode_task_count=spec.source_count,
                terminal_episode_task_count=spec.source_count,
                open_spans=0,
                open_requests=0,
                open_transactions=0,
                orphan_tasks=0,
                unobserved_exceptions=0,
                service_idle=True,
                canonical_snapshot_hashes=snapshot_hashes,
            )
        )
        makespan_ns = t_durable_complete_ns - t0_ns
        if makespan_ns <= 0:
            raise MemBindAdapterError("MEMBIND_MAKESPAN_INVALID")
        scheduler_observation = normalized["scheduler_observation"]
        metrics: dict[str, Any] = {
            "schema_version": "membind.saturated-fixed-work.membind-block-result.v1",
            "valid": True,
            "method": spec.method,
            "policy": spec.policy,
            "history_id": spec.history_id,
            "namespace": spec.namespace,
            "block_id": spec.block_id,
            "attempt_id": attempt_root.name,
            "attempt_ordinal": 1,
            "episode_count": spec.source_count,
            "source_tokens": source_tokens,
            "build_makespan_ns": makespan_ns,
            "build_makespan_s": makespan_ns / 1_000_000_000,
            "episodes_per_s": spec.source_count * 1_000_000_000 / makespan_ns,
            "source_tokens_per_s": source_tokens * 1_000_000_000 / makespan_ns,
            "t0_ns": t0_ns,
            "t_durable_complete_ns": t_durable_complete_ns,
            "t_validated_seal_ns": t_validated_seal_ns,
            "validation_seal_latency_ns": t_validated_seal_ns - t_durable_complete_ns,
            "arrival_policy": spec.arrival_policy,
            "artificial_sleep_count": 0,
            "compile_workers": spec.compile_workers,
            "lookahead": spec.lookahead,
            "bind_workers": spec.bind_workers,
            "global_llm_admission_k": spec.global_llm_admission_k,
            "prepared_episode_count": len(prepared_sequences),
            "publication_source_sequences": normalized[
                "publication_source_sequences"
            ],
            "complete_publication_coverage": True,
            "coordinator_direct_violation_count": normalized[
                "direct_violation_count"
            ],
            "coordinator_direct_violations": normalized["direct_violations"],
            "scheduler_observation": scheduler_observation,
            "scheduler_event_count": len(scheduler_rows),
            "compile_active_max": scheduler_observation.get(
                "max_reserved_compile_count"
            ),
            "prepared_rob_occupancy_max": scheduler_observation.get(
                "max_prepared_rob_occupancy"
            ),
            "request_admission": dict(request_observation),
            "llm_observed_max_inflight": request_observation.get(
                "observed_max_inflight"
            ),
            "canonical_graph_hash": snapshot_hashes[0],
            "canonical_exact_match": None,
            "resource_availability": "NOT_EVALUATED",
            "sampler_coverage": None,
            "execution_identity_sha256": getattr(
                identity, "execution_sha256", None
            ),
            "seal_payload_sha256": seal["payload_sha256"],
            **_freshness(lifecycle_rows),
            **trace_metrics,
        }
        metrics["phase_metrics_availability"] = metrics[
            "llm_metrics_availability"
        ]
        _write_new_json(attempt_root / "canonical_graph.json", dict(first_graph))
        _write_new_json(attempt_root / "block_metrics.json", metrics)
        checkpoint = {
            "schema_version": "membind.saturated-fixed-work.membind-checkpoint.v1",
            "status": "COMPLETED",
            "source_count": spec.source_count,
            "prepared_source_sequences": sorted(prepared_sequences),
            "publication_source_sequences": normalized[
                "publication_source_sequences"
            ],
            "event_count": len(lifecycle_rows),
        }
        checkpoint["payload_sha256"] = _payload_sha256(checkpoint)
        _write_new_json(attempt_root / "checkpoint.json", checkpoint)
        return {**metrics, "attempt_root": str(attempt_root)}
    except BaseException as error:
        if not (
            store.failure_path.exists()
            or store.timeout_path.exists()
            or store.seal_path.exists()
        ):
            try:
                store.record_failure(
                    f"{type(error).__module__}.{type(error).__qualname__}",
                    {"stage": "membind_live_block"},
                )
            except Exception:
                pass
        raise
    finally:
        if measurement_handle is not None:
            measurement_handle.restore()
        if phase_handle is not None:
            phase_handle.restore()
        if runtime is not None and not closed:
            try:
                await _await(hooks.close_runtime(runtime))
            except Exception:
                pass


def build_production_membind_dependencies(
    *,
    repository_root: Path,
    live_dependencies: Any,
    attempt_store_factory: Any,
) -> MemBindExecutionDependencies:
    """Compose the existing qualified v3.1 implementation at the live boundary."""

    from paper_eval.membind_v1.graphiti_factories import (
        build_source_log_from_episodes,
    )
    from paper_eval.membind_v31.coordinator import run_membind_v31_stream
    from paper_eval.membind_v31.freezer import (
        V31FreezePaths,
        load_v31_state_cut_certification,
    )
    from paper_eval.membind_v31.live_block import production_v31_live_hooks

    if not callable(attempt_store_factory):
        raise MemBindAdapterError("MEMBIND_ATTEMPT_STORE_FACTORY_INVALID")
    paths = V31FreezePaths.from_repository(Path(repository_root))
    return MemBindExecutionDependencies(
        hooks=production_v31_live_hooks(),
        certification=load_v31_state_cut_certification(paths),
        live_dependencies=live_dependencies,
        source_log_builder=build_source_log_from_episodes,
        coordinator=run_membind_v31_stream,
        attempt_store_factory=attempt_store_factory,
    )


__all__ = [
    "MEMBIND_BIND_WORKERS",
    "MEMBIND_COMPILE_WORKERS",
    "MEMBIND_GLOBAL_LLM_ADMISSION_K",
    "MEMBIND_LOOKAHEAD",
    "MEMBIND_METHOD",
    "MEMBIND_POLICY",
    "MemBindAdapterError",
    "MemBindBlockSpec",
    "MemBindExecutionDependencies",
    "build_membind_block_spec",
    "build_production_membind_dependencies",
    "execute_membind_block",
    "normalize_membind_stream_result",
    "validate_membind_episodes",
]
