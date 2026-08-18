"""Checkpointed execution for the isolated twelve-source W=4 pilot.

This module is intentionally separate from ``live_block`` and
``V31BlockStore``.  The pilot is diagnostic-only: it may reuse the qualified
Graphiti adapter/runtime, but it cannot write formal lifecycle artifacts or
become merge-eligible by construction.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
from paper_eval.membind_v31.admission import AdmissionPolicy
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.coordinator import run_membind_v31_stream
from paper_eval.membind_v31.live_block import (
    V31LiveHooks,
    _invoke_runtime_builder,
    production_v31_live_hooks,
)
from paper_eval.membind_v31.optimization_pilot import (
    ARTIFACT_STATUS,
    GLOBAL_LLM_ADMISSION_K,
    MANIFEST_SCHEMA,
    PILOT_SOURCE_COUNT,
    build_w4_pilot_checkpoint,
    build_w4_pilot_manifest,
    build_w4_pilot_result,
    verify_w4_pilot_contract,
)
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact, PreparedArtifactError
from paper_eval.membind_v31.queue_diagnostics import analyze_queue_trace_file


class OptimizationPilotExecutionError(ValueError):
    """A pilot preflight, lifecycle, or persistence invariant failed."""


def _fail(code: str) -> OptimizationPilotExecutionError:
    return OptimizationPilotExecutionError(code)


def _nonnegative(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise _fail(code)
    return value


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


def _append_record(path: Path, *, schema: str, row: Mapping[str, object]) -> None:
    body = {"schema_version": schema, "row": deepcopy(dict(row))}
    append_jsonl_durable(path, {"record": body, "record_sha256": payload_sha256(body)})


def _percentile(values: Sequence[int], quantile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _performance(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    arrivals: dict[int, int] = {}
    publications: dict[int, int] = {}
    for event in events:
        sequence = int(event["source_sequence"])
        timestamp = int(event["timestamp_ns"])
        if event["event_type"] == "ARRIVAL":
            arrivals[sequence] = timestamp
        elif event["event_type"] == "PUBLICATION_DURABLE":
            publications[sequence] = timestamp
    freshness = [
        publications[index] - arrivals[index]
        for index in sorted(publications)
        if index in arrivals
    ]
    makespan = (
        max(publications.values()) - min(arrivals.values())
        if arrivals and publications
        else None
    )
    return {
        "published_episode_count": len(publications),
        "p50_freshness_ns": _percentile(freshness, 0.50),
        "p95_freshness_ns": _percentile(freshness, 0.95),
        "p99_freshness_ns": _percentile(freshness, 0.99),
        "max_freshness_ns": max(freshness) if freshness else None,
        "makespan_ns": makespan,
        "goodput_episodes_per_second": (
            None
            if makespan is None or makespan <= 0
            else len(publications) * 1_000_000_000 / makespan
        ),
    }


_TRANSITIONS = {
    "ARRIVAL": {"NEW"},
    "COMPILE_STARTED": {"ARRIVAL"},
    "PREPARED_DURABLE": {"COMPILE_STARTED"},
    "BIND_STARTED": {"PREPARED_DURABLE"},
    "COMMIT_RETURNED": {"BIND_STARTED"},
    "PUBLICATION_DURABLE": {"COMMIT_RETURNED"},
    "TERMINAL_FAILURE": {
        "NEW",
        "ARRIVAL",
        "COMPILE_STARTED",
        "PREPARED_DURABLE",
        "BIND_STARTED",
        "COMMIT_RETURNED",
    },
}


@dataclass(slots=True)
class PilotStore:
    """Append-only diagnostic store with a sealed checkpoint after every event."""

    root: Path
    manifest: dict[str, object]
    states: list[str]
    event_count: int = 0
    lifecycle_events: list[dict[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.lifecycle_events is None:
            self.lifecycle_events = []

    @classmethod
    def create(cls, root: Path, manifest: Mapping[str, object]) -> "PilotStore":
        target = Path(root)
        if target.exists():
            allowed = {"PILOT_CONTRACT.json"}
            try:
                children = {child.name for child in target.iterdir()}
            except OSError:
                raise _fail("pilot_output_root_unreadable") from None
            if not children.issubset(allowed):
                raise _fail("pilot_output_root_not_fresh")
        else:
            target.mkdir(parents=True, exist_ok=False)
        (target / "private" / "prepared").mkdir(parents=True, exist_ok=False)
        selected = deepcopy(dict(manifest))
        atomic_write_json(target / "manifest.json", selected)
        store = cls(target, selected, ["NEW"] * PILOT_SOURCE_COUNT)
        store.write_checkpoint()
        return store

    @property
    def checkpoint(self) -> dict[str, object]:
        return build_w4_pilot_checkpoint(
            self.manifest,
            source_states=self.states,
            event_count=self.event_count,
        )

    def write_checkpoint(self) -> None:
        atomic_write_json(self.root / "checkpoint.json", self.checkpoint)

    def append_queue(self, row: Mapping[str, object]) -> None:
        _append_record(
            self.root / "queue.jsonl",
            schema="membind.paper-eval-v3.membind-v31-queue.v1",
            row=row,
        )

    def append_llm(self, row: Mapping[str, object]) -> None:
        _append_record(
            self.root / "llm.jsonl",
            schema="membind.paper-eval-v3.membind-v31-pilot-llm.v1",
            row=row,
        )

    def persist_prepared(
        self,
        artifact: PreparedArtifact,
        *,
        source_hash: str,
        certification_hash: str,
    ) -> None:
        if not isinstance(artifact, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            artifact.verify(
                expected_source_sha256=source_hash,
                expected_certification_sha256=certification_hash,
            )
        except PreparedArtifactError:
            raise _fail("prepared_artifact_invalid") from None
        target = self.root / "private" / "prepared" / f"{artifact.source_sequence:08d}.json"
        if target.exists():
            raise _fail("prepared_artifact_duplicate")
        atomic_write_json(target, artifact.to_document())

    def append_lifecycle(
        self,
        source_sequence: int,
        event_type: str,
        timestamp_ns: int,
        telemetry: Mapping[str, object] | None = None,
    ) -> None:
        sequence = _nonnegative(source_sequence, "lifecycle_source_invalid")
        if sequence >= len(self.states) or event_type not in _TRANSITIONS:
            raise _fail("lifecycle_event_invalid")
        if self.states[sequence] not in _TRANSITIONS[event_type]:
            raise _fail("lifecycle_transition_invalid")
        timestamp = _nonnegative(timestamp_ns, "lifecycle_timestamp_invalid")
        selected = {} if telemetry is None else deepcopy(dict(telemetry))
        event = {
            "schema_version": "membind.paper-eval-v3.membind-v31-pilot-lifecycle.v1",
            "event_sequence": self.event_count,
            "source_sequence": sequence,
            "source_sha256": self.manifest["source_sha256s"][sequence],
            "event_type": event_type,
            "timestamp_ns": timestamp,
            "telemetry": selected,
        }
        append_jsonl_durable(
            self.root / "events.jsonl",
            {"event": event, "event_sha256": payload_sha256(event)},
        )
        self.states[sequence] = event_type
        self.event_count += 1
        assert self.lifecycle_events is not None
        self.lifecycle_events.append(event)
        self.write_checkpoint()


def _write_failure(root: Path, *, error: BaseException, checkpoint: Mapping[str, object] | None) -> None:
    body: dict[str, object] = {
        "schema_version": "membind.paper-eval-v3.membind-v31-w4-pilot-failure.v1",
        "status": "FAILED_NON_REUSABLE",
        "artifact_status": ARTIFACT_STATUS,
        "merge_eligible": False,
        "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
        "error_code": str(error) if isinstance(error, OptimizationPilotExecutionError) else "live_execution_error",
    }
    if checkpoint is not None:
        body["checkpoint_sha256"] = checkpoint.get("checkpoint_sha256")
        body["completed_source_prefix"] = checkpoint.get("completed_source_prefix")
        body["event_count"] = checkpoint.get("event_count")
    body["payload_sha256"] = payload_sha256(body)
    atomic_write_json(Path(root) / "FAILURE.json", body)


async def execute_w4_pilot(
    *,
    contract: Mapping[str, object],
    verified_formal_plan: Mapping[str, object],
    episodes: Sequence[object],
    env: Mapping[str, str],
    output_root: Path,
    state_cut_certification: StateCutCertification,
    implementation_sha256: str,
    hooks: V31LiveHooks | None = None,
    coordinator: Callable[..., Awaitable[Mapping[str, object]]] = run_membind_v31_stream,
) -> dict[str, object]:
    """Run exactly one fresh W=4 pilot and persist every recoverable boundary."""

    try:
        selected_contract = verify_w4_pilot_contract(
            contract, verified_formal_plan=verified_formal_plan
        )
    except ValueError:
        raise _fail("pilot_contract_invalid") from None
    if (
        isinstance(episodes, (str, bytes))
        or not isinstance(episodes, Sequence)
        or len(episodes) != PILOT_SOURCE_COUNT
    ):
        raise _fail("pilot_episode_count_invalid")
    if not isinstance(env, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()
    ):
        raise _fail("pilot_environment_invalid")
    if not isinstance(state_cut_certification, StateCutCertification):
        raise _fail("pilot_certification_invalid")
    try:
        certification = state_cut_certification.verify()
    except ValueError:
        raise _fail("pilot_certification_invalid") from None
    _sha(implementation_sha256, "pilot_implementation_invalid")
    if not callable(coordinator):
        raise _fail("pilot_coordinator_invalid")
    root = Path(output_root)
    if root.exists():
        raise _fail("pilot_output_root_not_fresh")
    selected_hooks = production_v31_live_hooks() if hooks is None else hooks
    if not isinstance(selected_hooks, V31LiveHooks):
        raise _fail("pilot_hooks_invalid")

    # Contract is sealed before any service call; a partially created root is
    # intentionally not resumable under the same run identity.
    root.mkdir(parents=True, exist_ok=False)
    atomic_write_json(root / "PILOT_CONTRACT.json", selected_contract)
    store: PilotStore | None = None
    runtime: object | None = None
    try:
        namespace = str(selected_contract["namespace"])
        scoped = tuple(selected_hooks.namespace_episode(item, namespace) for item in episodes)
        source_log, raw_hashes = build_source_log_from_episodes(
            scoped,
            namespace=namespace,
            reference_time_to_ns=selected_hooks.reference_time_to_ns,
        )
        if list(raw_hashes) != selected_contract["source_sha256s"]:
            raise _fail("pilot_source_identity_mismatch")

        admission_rows: list[dict[str, object]] = []

        def request_observer(row: dict[str, object]) -> None:
            if store is None:
                raise _fail("pilot_store_not_ready")
            store.append_llm(row)

        def admission_observer(row: dict[str, object]) -> None:
            admission_rows.append(dict(row))
            if store is not None:
                store.append_queue(row)

        block_env = {
            **dict(env),
            "CONSTRUCTION_CACHE_SALT": str(selected_contract["cache_salt_sha256"]),
        }
        runtime = _invoke_runtime_builder(
            selected_hooks.runtime_builder,
            response_observer=request_observer,
            env=block_env,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix=f"w4-{selected_contract['attempt_id']}",
            observer=request_observer,
            admission_observer=admission_observer,
        )
        if inspect.isawaitable(runtime):
            raise _fail("pilot_runtime_builder_must_be_synchronous")
        if getattr(runtime, "shared_execution_envelope_sha256", None) != selected_contract[
            "shared_execution_envelope_sha256"
        ]:
            raise _fail("pilot_execution_envelope_mismatch")
        await _await(selected_hooks.runtime_ready(runtime), "pilot_runtime_ready_invalid")

        initial = await _await(
            selected_hooks.namespace_probe(runtime, namespace),
            "pilot_namespace_probe_invalid",
        )
        if not isinstance(initial, Mapping) or {
            "node_count": int(initial.get("node_count", -1)),
            "relationship_count": int(initial.get("relationship_count", -1)),
            "episode_names": sorted(str(name) for name in initial.get("episode_names", [])),
        } != {"node_count": 0, "relationship_count": 0, "episode_names": []}:
            raise _fail("pilot_namespace_not_fresh")

        execution_identity = payload_sha256(
            {
                "runtime_method_execution_identity_sha256": getattr(
                    runtime, "method_execution_identity_sha256", None
                ),
                "parent_formal_plan_payload_sha256": selected_contract[
                    "parent_formal_plan_payload_sha256"
                ],
                "state_cut_certification_sha256": certification.certification_sha256,
                "compile_workers": selected_contract["compile_workers"],
                "lookahead": selected_contract["lookahead"],
                "bind_workers": selected_contract["bind_workers"],
                "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
            }
        )
        manifest = build_w4_pilot_manifest(
            selected_contract,
            verified_formal_plan=verified_formal_plan,
            execution_identity_sha256=execution_identity,
            state_cut_certification_sha256=certification.certification_sha256,
            implementation_sha256=implementation_sha256,
        )
        store = PilotStore.create(root, manifest)
        # Admission snapshots emitted during runtime construction are retained
        # after the manifest exists, preserving their producer sequence.
        for row in admission_rows:
            store.append_queue(row)
        admission_rows.clear()

        scheduler_observer = store.append_queue
        adapter = selected_hooks.adapter_factory(runtime, certification)

        def lifecycle(row: dict[str, object]) -> None:
            mapping = {
                "arrival": "ARRIVAL",
                "arrival_failure": "TERMINAL_FAILURE",
                "compile_start": "COMPILE_STARTED",
                "prepared_durable": "PREPARED_DURABLE",
                "bind_start": "BIND_STARTED",
                "compile_failure": "TERMINAL_FAILURE",
                "bind_failure": "TERMINAL_FAILURE",
            }
            event_type = mapping.get(str(row.get("event_type")))
            if event_type is None:
                return
            telemetry = {
                key: value
                for key, value in row.items()
                if key not in {"event_type", "stream_id", "source_sequence", "timestamp_ns"}
            }
            store.append_lifecycle(
                int(row["source_sequence"]), event_type, int(row["timestamp_ns"]), telemetry
            )

        def persist_prepared(artifact: object) -> None:
            if not isinstance(artifact, PreparedArtifact):
                raise _fail("prepared_artifact_invalid")
            store.persist_prepared(
                artifact,
                source_hash=source_log.record(int(getattr(artifact, "source_sequence"))).source_sha256,
                certification_hash=certification.certification_sha256,
            )

        async def visibility(sequence: int, _result: object) -> bool:
            value = await _await(
                selected_hooks.source_visibility_probe(runtime, source_log.record(sequence)),
                "pilot_visibility_probe_invalid",
            )
            if not isinstance(value, bool):
                raise _fail("pilot_visibility_result_invalid")
            return value

        def commit(sequence: int, _result: object) -> None:
            store.append_lifecycle(sequence, "COMMIT_RETURNED", time.monotonic_ns())

        def publication(sequence: int, _result: object) -> None:
            store.append_lifecycle(
                sequence,
                "PUBLICATION_DURABLE",
                time.monotonic_ns(),
                {"visibility_confirmed": True},
            )

        coordinator_result = await coordinator(
            stream_id=str(selected_contract["history_id"]),
            source_log=source_log,
            arrival_offsets_ns=tuple(selected_contract["arrival_offsets_ns"]),
            adapter=adapter,
            request_client=getattr(runtime, "admitted_llm"),
            compile_workers=int(selected_contract["compile_workers"]),
            lookahead=int(selected_contract["lookahead"]),
            observer=lifecycle,
            scheduler_observer=scheduler_observer,
            publication_probe=visibility,
            prepared_persistor=persist_prepared,
            commit_observer=commit,
            publication_persistor=publication,
        )
        if coordinator_result.get("publication_source_sequences") != list(
            range(PILOT_SOURCE_COUNT)
        ) or coordinator_result.get("direct_violation_count") != 0:
            raise _fail("pilot_coordinator_contract_invalid")
        final = await _await(
            selected_hooks.namespace_probe(runtime, namespace),
            "pilot_final_namespace_probe_invalid",
        )
        if not isinstance(final, Mapping):
            raise _fail("pilot_final_namespace_invalid")
        expected_names = sorted(str(getattr(item, "name")) for item in scoped)
        observed_names = sorted(str(name) for name in final.get("episode_names", []))
        if observed_names != expected_names:
            raise _fail("pilot_final_namespace_coverage_invalid")

        performance = _performance(store.lifecycle_events or [])
        p95 = performance.get("p95_freshness_ns")
        makespan = performance.get("makespan_ns")
        admission = getattr(runtime, "admitted_llm").observation()
        observed_max = admission.get("observed_max_inflight")
        if not isinstance(p95, int) or not isinstance(makespan, int):
            raise _fail("pilot_performance_incomplete")
        if isinstance(observed_max, bool) or not isinstance(observed_max, int):
            raise _fail("pilot_admission_observation_invalid")
        result = build_w4_pilot_result(
            manifest,
            checkpoint=store.checkpoint,
            publication_source_sequences=list(coordinator_result["publication_source_sequences"]),
            direct_violation_count=int(coordinator_result["direct_violation_count"]),
            observed_max_inflight=observed_max,
            p95_freshness_ns=p95,
            makespan_ns=makespan,
        )
        queue_diagnostic = analyze_queue_trace_file(root / "queue.jsonl")
        result = deepcopy(result)
        result["performance"] = performance
        result["queue_diagnostic"] = queue_diagnostic
        result["queue_diagnostic_payload_sha256"] = queue_diagnostic["payload_sha256"]
        result["payload_sha256"] = payload_sha256(
            {key: value for key, value in result.items() if key != "payload_sha256"}
        )
        atomic_write_json(root / "QUEUE_DIAGNOSTIC.json", queue_diagnostic)
        atomic_write_json(root / "result.json", result)
        return result
    except BaseException as error:
        _write_failure(root, error=error, checkpoint=None if store is None else store.checkpoint)
        raise
    finally:
        if runtime is not None:
            await _await(selected_hooks.close_runtime(runtime), "pilot_runtime_close_invalid")


__all__ = [
    "OptimizationPilotExecutionError",
    "PilotStore",
    "execute_w4_pilot",
]
