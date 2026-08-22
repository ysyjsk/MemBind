"""P9 full-history real Graphiti V5 campaign.

This module is the production composition around the already-qualified V5
native seam.  It does not rebuild Graphiti semantics: preparation calls the
pinned extraction functions, publication calls the original
``Graphiti.add_episode``, and ``FrontierExecutor`` owns ordered durable
publication.  A scoped multiplex client keeps capture and replay available at
the same time, which is required for real preparation/native overlap.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from .campaign import FORMAL_HISTORIES, V5_METHOD, verify_baseline_reference
from .live_runner import (
    V5LiveRunnerError,
    _episode_node,
    _graphiti_kwargs,
    _maybe_await,
    _parse_reference_time,
    _write_jsonl,
    _write_new,
)
from .runtime.core.admission import AdmissionArbiter, CapacityAuthority
from .runtime.core.binder import NativeBindingScope
from .runtime.core.executor import ExecutionResult, FrontierExecutor
from .runtime.core.provider_admission import (
    FrontierAwareLLMClient,
    current_provider_scope,
    provider_scope,
)
from .runtime.core.transcript import TranscriptStore


class P9RunnerError(V5LiveRunnerError):
    pass


_CONTEXT_ERROR_RE = re.compile(
    r"maximum context length is\s+(?P<context>\d+)\s+tokens.*?"
    r"prompt contains at least\s+(?P<input>\d+)\s+input tokens",
    re.IGNORECASE | re.DOTALL,
)


def _p9_effective_max_tokens(error: BaseException, requested: int, *, safety_margin: int = 32) -> int | None:
    match = _CONTEXT_ERROR_RE.search(str(error))
    if match is None:
        return None
    budget = max(0, min(int(requested), int(match["context"]) - int(match["input"]) - int(safety_margin)))
    return budget if budget > 0 else None


def _verify_p8_seal(path: Path, *, baseline_reference: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the sealed P8 artifact identity before allowing P9."""

    if not isinstance(baseline_reference, Mapping) or not baseline_reference.get("formal_run_seal_sha256"):
        raise P9RunnerError("baseline reference is incomplete for P8 verification")

    try:
        seal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P9RunnerError("P8 seal is unreadable") from exc
    required = {
        "manifest.json",
        "frontier.jsonl",
        "admission.jsonl",
        "logical_work_summary.json",
        "native_trace.jsonl",
        "block_metrics.json",
        "canonical_graph.json",
        "lifecycle.json",
        "live_authority.json",
        "capacity_authority.json",
        "oracle_binding_summary.json",
    }
    missing = sorted(name for name in required if not (path.parent / name).is_file())
    if missing:
        raise P9RunnerError(f"P8 artifact incomplete: {missing}")
    if (
        seal.get("schema_version") != "membind.v5.p8-seal.v1"
        or seal.get("status") != "P8_LIVE_SEALED"
        or seal.get("method") != V5_METHOD
        or seal.get("source_count") not in {1, 2}
        or not isinstance(seal.get("namespace"), str)
        or not seal["namespace"].startswith("membind-v5-p8-")
        or not isinstance(seal.get("build_makespan_ns"), int)
        or seal["build_makespan_ns"] <= 0
    ):
        raise P9RunnerError("P8 seal identity is invalid")
    try:
        manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P9RunnerError("P8 manifest is unreadable") from exc
    manifest_baseline = manifest.get("baseline_reference") if isinstance(manifest, Mapping) else None
    if (
        not isinstance(manifest, Mapping)
        or not isinstance(manifest_baseline, Mapping)
        or manifest.get("status") != "PASS"
        or manifest.get("method") != V5_METHOD
        or manifest.get("namespace") != seal.get("namespace")
        or manifest.get("source_count") != seal.get("source_count")
        or manifest_baseline.get("formal_run_seal_sha256")
        != baseline_reference.get("formal_run_seal_sha256")
        or manifest.get("native_graphiti_path") != "Graphiti.add_episode"
    ):
        raise P9RunnerError("P8 manifest identity does not match baseline or native path")
    return {**seal, "path": str(path.resolve()), "artifact_completeness": "PASS"}


class _DurableJsonl:
    """Small append-only journal used while a history is executing.

    Rows are flushed and fsynced at the event boundary so a provider or native
    failure leaves enough evidence for offline diagnosis.  The journal never
    stores prompts, responses, or credentials.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("a", encoding="utf-8")
        self.count = 0
        self.last: dict[str, Any] | None = None
        self.event_counts: dict[str, int] = {}
        self.max_durable_frontier = -1
        self._previous_sha256 = "0" * 64

    def append(self, row: Mapping[str, Any]) -> None:
        body = {
            "schema_version": "membind.v5.p9-journal-event.v1",
            "ordinal": self.count,
            "previous_sha256": self._previous_sha256,
            **dict(row),
        }
        payload = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        body["payload_sha256"] = digest
        self._stream.write(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.count += 1
        self.last = body
        self._previous_sha256 = digest
        event = body.get("event")
        if event is not None:
            key = str(event)
            self.event_counts[key] = self.event_counts.get(key, 0) + 1
            if key == "PUBLICATION_DURABLE":
                self.max_durable_frontier = max(self.max_durable_frontier, int(body.get("source_sequence", -1)))

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()


def _install_p9_context_budget_adapter(llm_client: Any) -> Callable[[], None]:
    """Scope effective-budget retry to the P9 real provider transport only."""

    completions = getattr(getattr(getattr(llm_client, "client", None), "chat", None), "completions", None)
    transport = getattr(completions, "_inner", None)
    original_create = getattr(transport, "create", None)
    if transport is None or not callable(original_create):
        raise P9RunnerError("P9 Graphiti LLM transport seam is unavailable")

    async def create_with_effective_budget(*args: Any, **kwargs: Any) -> Any:
        try:
            return await original_create(*args, **kwargs)
        except Exception as exc:
            requested = int(kwargs.get("max_tokens") or getattr(llm_client, "max_tokens", 0))
            effective = _p9_effective_max_tokens(
                exc,
                requested,
                safety_margin=int(os.environ.get("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")),
            )
            if effective is None or effective >= requested:
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_tokens"] = effective
            return await original_create(*args, **retry_kwargs)

    setattr(transport, "create", create_with_effective_budget)

    def restore() -> None:
        setattr(transport, "create", original_create)

    return restore


@dataclass(frozen=True, slots=True)
class P9FullConfig:
    repo_root: Path
    baseline_root: Path
    state_path: Path
    output_root: Path
    run_id: str
    p8_seal: Path | None = None
    history_ids: tuple[str, ...] = FORMAL_HISTORIES
    source_limit: int | None = None
    smoke: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", self.run_id):
            raise P9RunnerError("P9 run_id is invalid")
        if not self.history_ids:
            raise P9RunnerError("P9 history_ids cannot be empty")
        unknown = set(self.history_ids) - set(FORMAL_HISTORIES)
        if unknown:
            raise P9RunnerError(f"P9 history is not frozen: {sorted(unknown)}")
        if self.source_limit is not None and (self.source_limit <= 0 or self.source_limit > 49):
            raise P9RunnerError("P9 source_limit is invalid")
        if self.smoke and (len(self.history_ids) != 1 or self.source_limit not in {1, 2}):
            raise P9RunnerError("P9 smoke must use one history and one or two sources")


@dataclass(frozen=True, slots=True)
class FrontierHistoryResult:
    history_id: str
    source_count: int
    execution: ExecutionResult
    overlap_evidence: dict[str, Any]
    preparation_intervals: tuple[dict[str, Any], ...]
    native_intervals: tuple[dict[str, Any], ...]

    @property
    def durable_frontier(self) -> int:
        return self.execution.durable_frontier


def _native_previous_window(episodes: Sequence[Any], sequence: int) -> list[Any]:
    """Mirror Graphiti 0.29.3 previous-episode retrieval for preparation.

    Neo4j filters on ``valid_at <= reference_time``, orders by ``valid_at DESC``,
    limits to ``RELEVANT_SCHEMA_LIMIT``, and the Graphiti maintenance helper
    reverses the result before returning it.  Source sequence is therefore not
    a substitute for native temporal ordering.
    """

    from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT

    if sequence <= 0:
        return []
    current = _parse_reference_time(episodes[sequence].reference_time)
    eligible = [
        item
        for item in episodes[:sequence]
        if _parse_reference_time(item.reference_time) <= current
    ]
    eligible.sort(key=lambda item: _parse_reference_time(item.reference_time), reverse=True)
    return list(reversed(eligible[:RELEVANT_SCHEMA_LIMIT]))


async def run_frontier_history_async(
    source_count: int,
    prepare: Callable[[int], Awaitable[Any]],
    publish: Callable[[int, Any], Awaitable[Any]],
    *,
    authority: CapacityAuthority,
    history_id: str = "smoke",
    clock: Callable[[], int] = time.monotonic_ns,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    interval_sink: Callable[[dict[str, Any]], None] | None = None,
    admission_event_sink: Callable[[dict[str, Any]], None] | None = None,
    lifecycle_sink: Callable[[dict[str, Any]], None] | None = None,
) -> FrontierHistoryResult:
    """Run concurrent preparation and ordered publication without a global barrier."""

    executor = FrontierExecutor(
        source_count,
        authority,
        clock=clock,
        # Provider-call admission, rather than a task-wide prepare permit, is
        # the capacity envelope for P9.  This lets future extraction overlap a
        # native suffix while retaining FrontierExecutor's ordered publication.
        prepare_admission=False,
        event_sink=event_sink,
        admission_event_sink=admission_event_sink,
        lifecycle_sink=lifecycle_sink,
    )
    preparation_intervals: list[dict[str, Any]] = []
    native_intervals: list[dict[str, Any]] = []

    async def traced_prepare(sequence: int) -> Any:
        start = int(clock())
        try:
            return await prepare(sequence)
        finally:
            row = {"event": "PREPARE_INTERVAL", "source_sequence": sequence, "start_ns": start, "end_ns": int(clock())}
            preparation_intervals.append({key: row[key] for key in ("source_sequence", "start_ns", "end_ns")})
            if interval_sink is not None:
                interval_sink(dict(row))

    async def traced_publish(sequence: int, value: Any) -> Any:
        start = int(clock())
        try:
            return await publish(sequence, value)
        finally:
            row = {"event": "NATIVE_INTERVAL", "source_sequence": sequence, "start_ns": start, "end_ns": int(clock())}
            native_intervals.append({key: row[key] for key in ("source_sequence", "start_ns", "end_ns")})
            if interval_sink is not None:
                interval_sink(dict(row))

    execution = await executor.run(traced_prepare, traced_publish)
    overlap_pairs = []
    for prep in preparation_intervals:
        for native in native_intervals:
            if prep["source_sequence"] <= native["source_sequence"]:
                continue
            if prep["start_ns"] < native["end_ns"] and prep["end_ns"] > native["start_ns"]:
                overlap_pairs.append(
                    {
                        "prepare_source_sequence": prep["source_sequence"],
                        "native_source_sequence": native["source_sequence"],
                    }
                )
    overlap = {
        "future_prepare_overlapped_native": bool(overlap_pairs),
        "overlap_pairs": overlap_pairs,
        "preparation_count": len(preparation_intervals),
        "native_count": len(native_intervals),
    }
    return FrontierHistoryResult(
        history_id=history_id,
        source_count=source_count,
        execution=execution,
        overlap_evidence=overlap,
        preparation_intervals=tuple(sorted(preparation_intervals, key=lambda row: row["source_sequence"])),
        native_intervals=tuple(sorted(native_intervals, key=lambda row: row["source_sequence"])),
    )


class _ScopedLLMClient:
    """Route one Graphiti client container to capture or replay by source scope."""

    def __init__(self, capture: FrontierAwareLLMClient, replay: FrontierAwareLLMClient) -> None:
        self.capture = capture
        self.replay = replay

    async def generate_response(self, messages: list[Any], **kwargs: Any) -> Any:
        region, source_sequence = current_provider_scope()
        if region is None or source_sequence is None:
            raise P9RunnerError("Graphiti provider call outside P9 source scope")
        client = self.capture if region == "PREPARE" else self.replay
        client._proxy.source_sequence = int(source_sequence)
        return await client.generate_response(messages, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.replay, name)


async def _run_history_live_async(
    *,
    config: P9FullConfig,
    history_id: str,
    namespace: str,
    runtime_builder: Callable[[], Any],
    episode_loader: Callable[[Path, str, str], Sequence[Any]],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    history_root: Path | None = None,
) -> dict[str, Any]:
    from graphiti_core.utils.maintenance.edge_operations import extract_edges
    from graphiti_core.utils.maintenance.node_operations import extract_nodes
    from saturated_fixed_work_baseline_v1_2.dataset import EXPECTED_EPISODE_COUNTS

    episodes = tuple(episode_loader(config.repo_root, history_id, namespace))
    if config.source_limit is not None:
        episodes = episodes[: config.source_limit]
    if not episodes or [int(row.source_sequence) for row in episodes] != list(range(len(episodes))):
        raise P9RunnerError(f"{history_id}: source sequence mapping is invalid")
    if not config.smoke and len(episodes) != EXPECTED_EPISODE_COUNTS[history_id]:
        raise P9RunnerError(f"{history_id}: full history source count is incomplete")

    journals: dict[str, _DurableJsonl] = {}
    if history_root is not None:
        history_root.mkdir(parents=True, exist_ok=True)
        journals = {
            "frontier": _DurableJsonl(history_root / "frontier.jsonl"),
            "admission": _DurableJsonl(history_root / "admission.jsonl"),
            "raw": _DurableJsonl(history_root / "raw_events.jsonl"),
        }

    def journal_frontier(row: dict[str, Any]) -> None:
        if journals:
            journals["frontier"].append(row)
            journals["raw"].append({"event": "FRONTIER", **row})

    def journal_admission(row: dict[str, Any]) -> None:
        if journals:
            journals["admission"].append(row)
            journals["raw"].append({"event": "ADMISSION", **row})

    runtime = await _maybe_await(runtime_builder())
    graphiti = runtime.graphiti
    recorder = recorder_factory()
    instrumentation = instrumentation_installer(graphiti, recorder)
    context_budget_restore = _install_p9_context_budget_adapter(runtime.llm_client)
    closed = False

    async def close_runtime() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        if instrumentation is not None:
            instrumentation.restore()
        context_budget_restore()
        close = getattr(graphiti, "close", None)
        if callable(close):
            await _maybe_await(close())

    original_llm = runtime.llm_client
    capacity = CapacityAuthority.from_protocol_runtime(runtime)
    provider_arbiter = AdmissionArbiter(capacity, event_sink=journal_admission if journals else None)
    store = TranscriptStore()
    frontier_ref = {"value": -1}
    client_identity = {
        "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
        "source_hash": hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest()
        if inspect.isclass(type(original_llm))
        else "unknown",
    }
    capture = FrontierAwareLLMClient(
        original_llm,
        store=store,
        arbiter=provider_arbiter,
        mode="capture",
        durable_frontier=lambda: frontier_ref["value"],
        client_identity=client_identity,
        event_sink=journal_admission if journals else None,
    )
    replay = FrontierAwareLLMClient(
        original_llm,
        store=store,
        arbiter=provider_arbiter,
        mode="replay",
        durable_frontier=lambda: frontier_ref["value"],
        client_identity=client_identity,
        event_sink=journal_admission if journals else None,
    )
    multiplex = _ScopedLLMClient(capture, replay)
    graphiti.llm_client = multiplex
    graphiti.clients.llm_client = multiplex

    async def prepare(sequence: int) -> dict[str, Any]:
        episode = episodes[sequence]
        node_episode = _episode_node(episode, namespace=namespace)
        previous = [
            _episode_node(item, namespace=namespace, uuid_value=f"prep-{item.source_sequence}")
            for item in _native_previous_window(episodes, sequence)
        ]
        with recorder.episode_scope(config.run_id, episode.name, sequence):
            with provider_scope(region="PREPARE", source_sequence=sequence):
                nodes, index_map = await extract_nodes(
                    graphiti.clients, node_episode, previous, None, None, None
                )
                edges = await extract_edges(
                    graphiti.clients,
                    node_episode,
                    nodes,
                    previous,
                    {("Entity", "Entity"): []},
                    namespace,
                    None,
                    None,
                )
        return {
            "source_sequence": sequence,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_index_count": len(index_map),
        }

    async def publish(sequence: int, _prepared: Any) -> Any:
        episode = episodes[sequence]
        with recorder.episode_scope(config.run_id, episode.name, sequence):
            with provider_scope(region="NATIVE", source_sequence=sequence):
                with NativeBindingScope(store, source_sequence=sequence):
                    return await graphiti.add_episode(**_graphiti_kwargs(episode, namespace=namespace))

    try:
        result = await run_frontier_history_async(
            len(episodes),
            prepare,
            publish,
            authority=capacity,
            history_id=history_id,
            event_sink=journal_frontier if journals else None,
            interval_sink=(journals["raw"].append if journals else None),
            admission_event_sink=journal_admission if journals else None,
            lifecycle_sink=(journals["raw"].append if journals else None),
        )
        frontier_ref["value"] = result.durable_frontier
        if result.durable_frontier != len(episodes) - 1:
            raise P9RunnerError(f"{history_id}: durable frontier did not reach final source")
        logical = store.summary()
        if logical["unconsumed"] or logical["duplicates"]:
            raise P9RunnerError(f"{history_id}: transcript consumption is incomplete")
        canonical = await _maybe_await(graph_exporter(graphiti, list(episodes), namespace))
        if not isinstance(canonical, Mapping):
            raise P9RunnerError(f"{history_id}: canonical graph export is invalid")
        envelopes = [
            recorder.episode_envelope(config.run_id, episode.name, episode.source_sequence)
            for episode in episodes
        ]
        admission = list(capture.provider_calls) + list(replay.provider_calls)
        final_publication_ns = max(
            (int(event["monotonic_ns"]) for event in result.execution.events if event["event"] == "PUBLICATION_DURABLE"),
            default=result.execution.timer_stop_ns,
        )
        lifecycle = {
            "status": "DURABLE",
            "t0_ns": result.execution.timer_start_ns,
            "timer_start_ns": result.execution.timer_start_ns,
            "t_durable_complete_ns": final_publication_ns,
            "final_publication_ns": final_publication_ns,
            "timer_stop_ns": result.execution.timer_stop_ns,
            "build_makespan_ns": result.execution.timer_stop_ns - result.execution.timer_start_ns,
        }
        summary = {
            "schema_version": "membind.v5.p9-history-result.v1",
            "status": "PASS",
            "method": V5_METHOD,
            "history_id": history_id,
            "namespace": namespace,
            "source_count": len(episodes),
            "frontier": result.execution.events,
            "durable_frontier": result.durable_frontier,
            "overlap_evidence": result.overlap_evidence,
            "preparation_intervals": list(result.preparation_intervals),
            "native_intervals": list(result.native_intervals),
            "logical_work_summary": logical,
            "oracle_binding_summary": {
                "logical_captured": logical["logical_captured"],
                "logical_consumed": logical["logical_consumed"],
                "unconsumed": logical["unconsumed"],
                "duplicates": logical["duplicates"],
                "provider_replay_calls": 0,
                "replay_binding_calls": sum(1 for row in replay.provider_calls if row.get("replay")),
                "provider_native_calls": sum(1 for row in replay.provider_calls if row.get("admitted")),
            },
            "admission": admission,
            "native_trace": envelopes,
            "lifecycle": lifecycle,
            "canonical_graph": dict(canonical),
            "build_makespan_ns": result.execution.build_makespan_ns,
        }
        await close_runtime()
        for journal in journals.values():
            journal.close()
        return summary
    except BaseException as exc:
        logical = store.summary()
        if history_root is not None:
            frontier_journal = journals.get("frontier")
            _write_new(
                history_root / "failure.json",
                {
                    "schema_version": "membind.v5.p9-history-failure.v1",
                    "status": "P9_HISTORY_FAILED",
                    "history_id": history_id,
                    "namespace": namespace,
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "durable_frontier": frontier_journal.max_durable_frontier if frontier_journal else -1,
                    "prepared_event_count": frontier_journal.event_counts.get("PREPARED", 0) if frontier_journal else 0,
                    "logical_work_summary": logical,
                    "frontier_event_count": journals.get("frontier").count if journals else 0,
                    "admission_event_count": journals.get("admission").count if journals else 0,
                },
            )
        await close_runtime()
        for journal in journals.values():
            journal.close()
        raise


async def run_p9_full_live_async(
    config: P9FullConfig,
    *,
    runtime_builder_factory: Callable[[str, str], Callable[[], Any]],
    episode_loader: Callable[[Path, str, str], Sequence[Any]],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
    authorization_checker: Callable[..., Any],
) -> dict[str, Any]:
    from current_state_gate import LiveAction

    root = Path(config.output_root).resolve()
    resumed_started = False
    if root.exists() and any(root.iterdir()):
        entries = {item.name for item in root.iterdir()}
        started_path = root / "campaign_started.json"
        failure_entries = {name for name in entries if name.startswith("campaign_failure") and name.endswith(".json")}
        if entries == {"campaign_started.json"} | failure_entries and started_path.is_file():
            try:
                previous_started = json.loads(started_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise P9RunnerError("P9 existing campaign_started evidence is unreadable") from exc
            if previous_started.get("run_id") != config.run_id or previous_started.get("status") != "P9_LIVE_STARTED":
                raise P9RunnerError("P9 existing started evidence does not match this run")
            resumed_started = True
        else:
            raise P9RunnerError("P9 output root must be fresh")
    authorization_checker(LiveAction.MEMBIND_V5, state_path=config.state_path)
    baseline = verify_baseline_reference(config.baseline_root, allow_invalid_qa=True)
    if config.p8_seal is None or not config.p8_seal.is_file():
        raise P9RunnerError("P8 seal is required before P9")
    p8 = _verify_p8_seal(config.p8_seal, baseline_reference=baseline)

    root.mkdir(parents=True, exist_ok=True)
    started = {
        "schema_version": "membind.v5.p9-campaign-started.v1",
        "status": "P9_LIVE_STARTED",
        "method": V5_METHOD,
        "run_id": config.run_id,
        "history_ids": list(config.history_ids),
        "p8_seal": str(config.p8_seal),
        "baseline_reference": baseline,
        "native_characterization_c5_reused": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    if not resumed_started:
        _write_new(root / "campaign_started.json", started)
    history_results: list[dict[str, Any]] = []
    try:
        for history_id in config.history_ids:
            namespace = f"membind-v5-p9-{config.run_id}-{history_id}-{uuid.uuid4().hex[:10]}"
            history_root = root / "histories" / history_id
            result = await _run_history_live_async(
                config=config,
                history_id=history_id,
                namespace=namespace,
                runtime_builder=runtime_builder_factory(history_id, namespace),
                episode_loader=episode_loader,
                instrumentation_installer=instrumentation_installer,
                recorder_factory=recorder_factory,
                graph_exporter=graph_exporter,
                history_root=history_root,
            )
            history_results.append(result)
            _write_new(history_root / "history_result.json", {key: value for key, value in result.items() if key not in {"canonical_graph", "native_trace", "admission", "frontier"}})
            _write_new(history_root / "canonical_graph.json", result["canonical_graph"])
            _write_new(history_root / "logical_work_summary.json", result["logical_work_summary"])
            _write_new(history_root / "oracle_binding_summary.json", result["oracle_binding_summary"])
            _write_new(history_root / "lifecycle.json", result["lifecycle"])
            _write_new(history_root / "block_metrics.json", {"history_id": history_id, "source_count": result["source_count"], "durable_frontier": result["durable_frontier"], "build_makespan_ns": result["build_makespan_ns"], "overlap_evidence": result["overlap_evidence"]})
            if not (history_root / "frontier.jsonl").exists():
                _write_jsonl(history_root / "frontier.jsonl", result["frontier"])
            if not (history_root / "admission.jsonl").exists():
                _write_jsonl(history_root / "admission.jsonl", result["admission"])
            _write_jsonl(history_root / "native_trace.jsonl", result["native_trace"])
            _write_new(history_root / "seal.json", {"schema_version": "membind.v5.p9-history-seal.v1", "status": "P9_HISTORY_SEALED", "history_id": history_id, "source_count": result["source_count"], "durable_frontier": result["durable_frontier"]})
    except BaseException as exc:
        # Keep a sanitized durable failure marker for interrupted/failed runs.
        # Raw prompts, responses, tracebacks, and credentials remain excluded.
        binding_diagnostics: dict[str, Any] = {}
        if hasattr(exc, "reason"):
            binding_diagnostics = {
                "reason": str(getattr(exc, "reason", "unknown")),
                "details": dict(getattr(exc, "details", {}) or {}),
            }
        failure_name = "campaign_failure.json" if not (root / "campaign_failure.json").exists() else f"campaign_failure_{len(list(root.glob('campaign_failure*.json'))) + 1}.json"
        _write_new(
            root / failure_name,
            {
                "schema_version": "membind.v5.p9-campaign-failure.v1",
                "status": "P9_LIVE_FAILED",
                "run_id": config.run_id,
                "history_ids": list(config.history_ids),
                "completed_history_ids": [row["history_id"] for row in history_results],
                "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                "binding_diagnostics": binding_diagnostics,
                "native_characterization_c5_reused": False,
            },
        )
        raise

    status = "P9_SMOKE_SEALED" if config.smoke else "P9_FULL_LIVE_SEALED"
    seal = {"schema_version": "membind.v5.p9-campaign-seal.v1", "status": status, "method": V5_METHOD, "run_id": config.run_id, "history_ids": list(config.history_ids), "history_count": len(history_results), "source_counts": {row["history_id"]: row["source_count"] for row in history_results}, "p8_seal": str(config.p8_seal), "native_characterization_c5_reused": False}
    _write_new(root / "campaign_summary.json", {"status": status, "method": V5_METHOD, "histories": [{"history_id": row["history_id"], "source_count": row["source_count"], "durable_frontier": row["durable_frontier"], "overlap_evidence": row["overlap_evidence"]} for row in history_results]})
    _write_new(root / "seal.json", seal)
    return {"root": str(root), "seal": seal, "histories": history_results}


def build_p9_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real Graphiti V5 P9 full-history campaign")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--p8-seal", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--smoke-history", default="07741c45")
    parser.add_argument("--smoke-sources", type=int, default=2)
    return parser


def build_p9_live_command(config: P9FullConfig, *, python: str = "membind-validation/.venv/bin/python") -> str:
    script = config.repo_root / "saturated_fixed_work_baseline_v1_3/scripts/run_v5_p9_full.py"
    validation_src = config.repo_root / "membind-validation/src"
    v12_src = config.repo_root / "saturated_fixed_work_baseline_v1_2/src"
    v13_src = config.repo_root / "saturated_fixed_work_baseline_v1_3/src"
    return " ".join(
        [
            "PYTHONPATH=" + ":".join((str(v13_src), str(v12_src), str(validation_src))),
            python,
            str(script),
            "--repo-root",
            str(config.repo_root),
            "--baseline-root",
            str(config.baseline_root),
            "--state",
            str(config.state_path),
            "--p8-seal",
            str(config.p8_seal or "<p8-seal>"),
            "--output-root",
            str(config.output_root),
            "--run-id",
            config.run_id,
            "--execute-live",
        ]
    )


__all__ = [
    "FrontierHistoryResult",
    "P9FullConfig",
    "P9RunnerError",
    "build_p9_live_command",
    "build_p9_parser",
    "run_frontier_history_async",
    "run_p9_full_live_async",
]
