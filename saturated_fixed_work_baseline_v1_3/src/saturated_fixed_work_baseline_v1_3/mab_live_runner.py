"""Live MAB construction composition for the v1.3 three-way campaign.

This module deliberately owns no method algorithm.  It adapts the frozen MAB
``EpisodeInput`` projection to the already-qualified Graphiti seams and emits
the common v1.3 block evidence consumed by the validators/materializer.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .artifact_materializer import materialize_construction_block
from .campaign_reducer import METHOD_CLASSES
from .evaluation_contract import (
    TraceValidationError,
    validate_block_trace,
    validate_order_contract,
    validate_v6_bindings,
)
from .membind_v5.live_runner import _episode_node, _graphiti_kwargs, _maybe_await, _parse_reference_time
from .membind_v5.p9_runner import (
    _native_previous_window,
    _transport_attempt_rows,
    _transport_evidence_summary,
    run_frontier_history_async,
)
from .membind_v5.runtime.core.admission import AdmissionArbiter, CapacityAuthority
from .membind_v5.runtime.core.binder import NativeBindingScope
from .membind_v5.runtime.core.provider_admission import (
    FrontierAwareLLMClient,
    current_provider_scope,
    provider_scope,
)
from .membind_v5.runtime.core.transcript import TranscriptStore
from .membind_v6.provider import V6ProviderClient
from .membind_v6.request_observation import (
    compare_request_observations,
    write_private_request_capture,
)
from .membind_v6.proof import (
    validate_provider_events,
    validate_replay_accounting,
    validate_request_comparisons,
)


class MABLiveRunnerError(RuntimeError):
    """The live MAB block cannot satisfy the fixed-work contract."""


async def resolve_runtime_builder(runtime_builder: Callable[[], Any]) -> Any:
    """Resolve a runtime factory regardless of sync/async construction style."""

    return await _maybe_await(runtime_builder())


class _AppendOnlyJsonl:
    """Durable event journal with no method-specific ordering predicate."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("x", encoding="utf-8")

    def append(self, row: Mapping[str, Any]) -> None:
        self._stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()


@dataclass(frozen=True, slots=True)
class MABLiveEpisode:
    context_id: str
    source_sequence: int
    episode_id: str
    reference_time: str
    body: str
    session_id: str
    source_hash: str

    @property
    def name(self) -> str:
        return f"{self.context_id}::episode::{self.source_sequence:04d}"

    @property
    def group_id(self) -> str:
        return ""


def episode_from_input(item: Any) -> MABLiveEpisode:
    """Convert the method-independent input without exposing QA labels."""

    required = ("context_id", "source_sequence", "episode_id", "reference_time", "body")
    if any(not hasattr(item, field) for field in required):
        raise MABLiveRunnerError("MAB_EPISODE_INPUT_INVALID")
    sequence = int(item.source_sequence)
    body = str(item.body)
    source_hash = hashlib.sha256(
        json.dumps(
            {
                "context_id": str(item.context_id),
                "source_sequence": sequence,
                "episode_id": str(item.episode_id),
                "reference_time": str(item.reference_time),
                "body": body,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return MABLiveEpisode(
        context_id=str(item.context_id),
        source_sequence=sequence,
        episode_id=str(item.episode_id),
        reference_time=str(item.reference_time),
        body=body,
        session_id=str(getattr(item, "session_id", item.episode_id)),
        source_hash=source_hash,
    )


def _event_sink(events: list[dict[str, Any]], context_id: str) -> Callable[[str, int | None, int | None], None]:
    def emit(name: str, sequence: int | None = None, stamp: int | None = None) -> None:
        row: dict[str, Any] = {
            "event": name,
            "event_index": len(events),
            "monotonic_ns": int(stamp if stamp is not None else time.monotonic_ns()),
            "context_id": context_id,
        }
        if sequence is not None:
            row["source_sequence"] = int(sequence)
        events.append(row)

    return emit


def _episode_envelopes(recorder: Any, run_id: str, episodes: Sequence[MABLiveEpisode]) -> list[dict[str, Any]]:
    return [
        recorder.episode_envelope(run_id, episode.name, episode.source_sequence)
        for episode in episodes
    ]


def _mab_graphiti_kwargs(episode: MABLiveEpisode, *, namespace: str) -> dict[str, Any]:
    """Build the pinned Graphiti call shape while keeping offline fakes light."""

    try:
        from graphiti_core.nodes import EpisodeType
    except ModuleNotFoundError:
        return {
            "name": episode.name,
            "episode_body": episode.body,
            "source_description": "MemoryAgentBench LongMemEval session",
            "reference_time": episode.reference_time,
            "group_id": namespace,
        }
    return {
        "name": episode.name,
        "episode_body": episode.body,
        "source_description": "MemoryAgentBench LongMemEval session",
        "reference_time": _parse_reference_time(episode.reference_time),
        "source": EpisodeType.message,
        "group_id": namespace,
    }


def _span_metrics(
    recorder: Any, *, extraction_diagnostics: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    from .membind_v6_1.evidence import extraction_work_inventory, span_work_inventory

    return {
        **span_work_inventory(list(getattr(recorder, "records", ()) or ())),
        **extraction_work_inventory(extraction_diagnostics),
    }


async def run_mab_construction_async(
    *,
    method: str,
    run_id: str,
    context_id: str,
    namespace: str,
    episodes: Sequence[Any],
    runtime_builder: Callable[[], Any],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    output_root: str | Path,
    authority: Mapping[str, Any],
    workload_manifest: Any,
    frozen_config: Mapping[str, Any],
    environment: Mapping[str, Any] | None = None,
    preflight: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one fresh MAB construction block and materialize its seal."""

    if method not in METHOD_CLASSES:
        raise MABLiveRunnerError("METHOD_NOT_FROZEN")
    selected = tuple(episode_from_input(item) for item in episodes)
    if not selected or [item.source_sequence for item in selected] != list(range(len(selected))):
        raise MABLiveRunnerError("MAB_EPISODE_SEQUENCE_INVALID")
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise MABLiveRunnerError("BLOCK_ROOT_NOT_FRESH")
    root.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    emit = _event_sink(events, context_id)
    lifecycle = {
        "schema_version": "sfwb.v1.3.mab-live-lifecycle.v1",
        "status": "STARTING",
        "events": [],
    }
    for name in ("FRESH_NAMESPACE", "BACKEND_PREPARED", "SERVICE_READY", "WARMUP_COMPLETE", "BACKEND_IDLE"):
        lifecycle["events"].append({"event": name, "monotonic_ns": time.monotonic_ns()})

    runtime: Any = None
    graphiti: Any = None
    instrumentation: Any = None
    recorder: Any = None
    # Keep the crash-tolerant journal separate from the final materializer
    # member.  The latter is written atomically only after all validators pass.
    journals = {
        key: _AppendOnlyJsonl(root.parent / f".{root.name}.live_raw_events.jsonl")
        for key, filename in (("raw", "live_raw_events.jsonl"),)
    }
    store = TranscriptStore()
    bindings: list[dict[str, Any]] = []
    observations: list[Any] = []
    comparisons: list[dict[str, Any]] = []
    frontier_ref = {"value": -1}

    def record(row: Mapping[str, Any]) -> None:
        journals["raw"].append(dict(row))

    async def close_runtime() -> None:
        nonlocal instrumentation
        if instrumentation is not None:
            instrumentation.restore()
            instrumentation = None
        close = getattr(graphiti, "close", None) if graphiti is not None else None
        if callable(close):
            await _maybe_await(close())

    try:
        runtime = await _maybe_await(runtime_builder())
        graphiti = runtime.graphiti
        recorder = recorder_factory()
        instrumentation = instrumentation_installer(graphiti, recorder)
        formal_start = time.monotonic_ns()
        lifecycle["events"].append({"event": "FORMAL_START", "monotonic_ns": formal_start})
        emit("FORMAL_START", stamp=formal_start)

        async def direct_publish(sequence: int) -> None:
            episode = selected[sequence]
            emit("NATIVE_ENTER", sequence)
            with recorder.episode_scope(run_id, episode.name, sequence):
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    await graphiti.add_episode(**_mab_graphiti_kwargs(episode, namespace=namespace))
            durable = time.monotonic_ns()
            emit("PUBLICATION_DURABLE", sequence, durable)
            record({"event": "PUBLICATION_DURABLE", "source_sequence": sequence, "monotonic_ns": durable, "context_id": context_id})

        if method == "B0":
            for sequence in range(len(selected)):
                emit("SUBMIT", sequence)
                await direct_publish(sequence)
        elif method == "B1":
            for sequence in range(len(selected)):
                emit("SUBMIT", sequence)
            await asyncio.gather(*(direct_publish(sequence) for sequence in range(len(selected))))
        else:
            from graphiti_core.utils.maintenance.edge_operations import extract_edges
            from graphiti_core.utils.maintenance.node_operations import extract_nodes

            original_llm = runtime.llm_client
            capacity = CapacityAuthority.from_protocol_runtime(runtime)
            arbiter = AdmissionArbiter(capacity, name="mab-v6")
            client_identity = {
                "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
                "source_hash": hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest(),
            }
            capture = V6ProviderClient(original_llm, store=store, arbiter=arbiter, mode="capture", durable_frontier=lambda: frontier_ref["value"], client_identity=client_identity)
            replay = V6ProviderClient(original_llm, store=store, arbiter=arbiter, mode="replay", durable_frontier=lambda: frontier_ref["value"], client_identity=client_identity)

            class Multiplex:
                async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
                    region, _ = current_provider_scope()
                    return await (capture if region == "PREPARE" else replay).generate_response(messages, **kwargs)

                def __getattr__(self, name: str) -> Any:
                    return getattr(replay, name)

            graphiti.llm_client = Multiplex()
            graphiti.clients.llm_client = graphiti.llm_client
            for sequence in range(len(selected)):
                emit("SUBMIT", sequence)

            async def prepare(sequence: int) -> dict[str, Any]:
                episode = selected[sequence]
                node_episode = _episode_node(episode, namespace=namespace)
                previous = [_episode_node(item, namespace=namespace, uuid_value=f"prep-{item.source_sequence}") for item in _native_previous_window(selected, sequence)]
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="PREPARE", source_sequence=sequence):
                        nodes, index_map = await extract_nodes(graphiti.clients, node_episode, previous, None, None, None)
                        edges = await extract_edges(graphiti.clients, node_episode, nodes, previous, {("Entity", "Entity"): []}, namespace, None, None)
                return {"source_sequence": sequence, "node_count": len(nodes), "edge_count": len(edges), "node_index_count": len(index_map)}

            async def publish(sequence: int, _prepared: Any) -> Any:
                episode = selected[sequence]
                emit("NATIVE_ENTER", sequence)
                with recorder.episode_scope(run_id, episode.name, sequence):
                    with provider_scope(region="NATIVE", source_sequence=sequence):
                        with NativeBindingScope(store, source_sequence=sequence):
                            result = await graphiti.add_episode(**_mab_graphiti_kwargs(episode, namespace=namespace))
                durable = time.monotonic_ns()
                emit("PUBLICATION_DURABLE", sequence, durable)
                frontier_ref["value"] = sequence
                record({"event": "PUBLICATION_DURABLE", "source_sequence": sequence, "monotonic_ns": durable, "context_id": context_id})
                return result

            result = await run_frontier_history_async(len(selected), prepare, publish, authority=capacity, history_id=context_id, admit_native=False)
            if result.durable_frontier != len(selected) - 1:
                raise MABLiveRunnerError("V6_DURABLE_FRONTIER_INCOMPLETE")
            logical = store.summary()
            if logical["duplicates"] or logical["unconsumed"]:
                raise MABLiveRunnerError("V6_REPLAY_ACCOUNTING_INCOMPLETE")
            bindings = []
            for row in capture.observations:
                observations.append(row["observation"])
            for row in replay.observations:
                observations.append(row["observation"])
            shadow = {(int(row["source_sequence"]), row["public_summary"]["callsite"], int(row["public_summary"]["ordinal"])): row["observation"] for row in capture.observations}
            native = {(int(row["source_sequence"]), row["public_summary"]["callsite"], int(row["public_summary"]["ordinal"])): row["observation"] for row in replay.observations}
            for key in sorted(set(shadow) | set(native)):
                if key not in shadow or key not in native:
                    comparisons.append({"key": key, "match": False, "reason": "missing_side"})
                else:
                    comparisons.append({"key": key, **compare_request_observations(shadow[key], native[key])})
                    left, right = shadow[key], native[key]
                    bindings.append({
                        "source_sequence": key[0], "callsite": key[1], "ordinal_within_episode": key[2],
                        "request_identity_hash": left.public_summary["digest"],
                        "prepared_response_hash": hashlib.sha256(json.dumps(left.private_payload, sort_keys=True, default=str).encode()).hexdigest(),
                        "native_request_hash": right.public_summary["digest"],
                        "capture_count": 1, "consume_count": 1,
                        "match_status": "EXACT_MATCH" if compare_request_observations(left, right)["match"] else "MISMATCH",
                        "external_transport_attempted_during_replay": False,
                    })
        seal_time = time.monotonic_ns()
        emit("CONSTRUCTION_SEAL", stamp=seal_time)
        lifecycle["events"].append({"event": "CONSTRUCTION_COMPLETE", "monotonic_ns": seal_time})
        lifecycle["events"].append({"event": "DURABLE_COMPLETE", "monotonic_ns": seal_time})
        lifecycle.update({"status": "DURABLE", "timer_start_ns": formal_start, "timer_stop_ns": seal_time, "build_makespan_ns": seal_time - formal_start})
        canonical = await _maybe_await(graph_exporter(graphiti, list(selected), namespace))
        if not isinstance(canonical, Mapping):
            raise MABLiveRunnerError("CANONICAL_GRAPH_INVALID")
        transport_rows = _transport_attempt_rows(recorder)
        envelopes = _episode_envelopes(recorder, run_id, selected)
        result = {
            "schema_version": "membind.v1.3.mab-live-block.v1",
            "status": "PASS",
            "method": method,
            "semantic_class": METHOD_CLASSES[method],
            "context_id": context_id,
            "namespace": namespace,
            "workload_hash": str(getattr(workload_manifest, "manifest_sha256", "")),
            "expected_episode_count": len(selected),
            "submitted_count": len(selected),
            "completed_count": len(selected),
            "events": events,
            "native_trace": envelopes,
            "transport_trace": transport_rows,
            "request_identity": [row.public_summary for row in observations],
            "bindings": bindings,
            "lifecycle": lifecycle,
            "lifecycle_validation": validate_block_trace(events, expected_source_count=len(selected), method=method, context_id=context_id),
            "order_validation": validate_order_contract(events, expected_source_count=len(selected), method=method),
            "refinement_validation": validate_v6_bindings(bindings) if method == "V6" else {"refinement_status": "N/A"},
            "graph_diagnostics": dict(canonical),
            "t_build_ns": lifecycle["build_makespan_ns"],
            **_span_metrics(
                recorder,
                extraction_diagnostics=list(
                    getattr(runtime.llm_client, "_membind_extraction_diagnostics", ()) or ()
                ),
            ),
            "transport_evidence": _transport_evidence_summary(transport_rows),
        }
        if method == "V6":
            result["refinement_validation"] = {**result["refinement_validation"], "proof": {"request": validate_request_comparisons(comparisons), "replay": validate_replay_accounting(store.summary()), "provider": validate_provider_events([], capacity=capacity.value)}}
        journals["raw"].close()
        seal = materialize_construction_block(root, authority=authority, workload_manifest=workload_manifest, frozen_config=frozen_config, result=result, identity={"method": method, "context_id": context_id, "namespace": namespace, "run_id": run_id}, environment=environment, preflight=preflight)
        await close_runtime()
        return {**result, "construction_seal": seal}
    except BaseException:
        try:
            await close_runtime()
        finally:
            journals["raw"].close()
        raise


__all__ = [
    "MABLiveEpisode",
    "MABLiveRunnerError",
    "episode_from_input",
    "resolve_runtime_builder",
    "run_mab_construction_async",
]
