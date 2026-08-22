"""Strict P8 live composition over the pinned Graphiti 0.29.3 runtime.

The runner deliberately keeps Graphiti's stateful suffix untouched.  Preparation
invokes the pinned extraction functions against source-closed frozen episodes;
publication invokes the original ``Graphiti.add_episode`` with exact transcript
binding.  The module is dependency-injectable so the orchestration is testable
without opening provider or Neo4j connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from .campaign import V5_METHOD, verify_baseline_reference
from .runtime.core.admission import AdmissionArbiter, CapacityAuthority
from .runtime.core.binder import NativeBindingScope
from .runtime.core.provider_admission import FrontierAwareLLMClient, provider_scope
from .runtime.core.transcript import TranscriptStore


class V5LiveRunnerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class P8LiveConfig:
    root: Path
    baseline_root: Path
    state_path: Path
    history_id: str = "07741c45"
    source_count: int = 2
    run_id: str = "v5-p8-live"


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise V5LiveRunnerError(f"artifact already exists: {path}")
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, default=str) + "\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise V5LiveRunnerError(f"artifact already exists: {path}")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(dict(row), ensure_ascii=True, sort_keys=True, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _await(value: Any) -> Awaitable[Any] | Any:
    return value


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _parse_reference_time(value: str) -> datetime:
    text = str(value).strip()
    match = re.fullmatch(
        r"(?P<date>\d{4}/\d{2}/\d{2}) \((?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\) (?P<time>\d{2}:\d{2})",
        text,
    )
    if match is not None:
        parsed = datetime.strptime(f"{match['date']} {match['time']}", "%Y/%m/%d %H:%M")
        weekdays = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        if weekdays[parsed.weekday()] != match["weekday"]:
            raise V5LiveRunnerError("reference time weekday mismatch")
        return parsed.replace(tzinfo=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    result = datetime.fromisoformat(text)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def _episode_node(episode: Any, *, namespace: str, uuid_value: str | None = None) -> Any:
    from graphiti_core.nodes import EpisodicNode, EpisodeType

    return EpisodicNode(
        uuid=uuid_value or str(uuid.uuid4()),
        name=episode.name,
        group_id=namespace,
        labels=[],
        source=EpisodeType.message,
        content=episode.body,
        source_description="LongMemEval-S haystack session",
        created_at=datetime.now(timezone.utc),
        valid_at=_parse_reference_time(episode.reference_time),
    )


def _graphiti_kwargs(episode: Any, *, namespace: str) -> dict[str, Any]:
    from graphiti_core.nodes import EpisodeType

    return {
        "name": episode.name,
        "episode_body": episode.body,
        "source_description": "LongMemEval-S haystack session",
        "reference_time": _parse_reference_time(episode.reference_time),
        "source": EpisodeType.message,
        "group_id": namespace,
    }


async def run_p8_minimal_live_async(
    config: P8LiveConfig,
    *,
    runtime_builder: Callable[..., Any],
    authorization_checker: Callable[..., Any],
    episode_loader: Callable[[Path, str, str], Sequence[Any]],
    instrumentation_installer: Callable[[Any, Any], Any],
    recorder_factory: Callable[[], Any],
    graph_exporter: Callable[[Any, list[Any], str], Any],
) -> dict[str, Any]:
    """Execute one fresh, sealed 1-2 source P8 block."""

    from current_state_gate import LiveAction
    from graphiti_core.utils.maintenance.edge_operations import extract_edges
    from graphiti_core.utils.maintenance.node_operations import extract_nodes

    if config.source_count not in {1, 2}:
        raise V5LiveRunnerError("P8 source_count must be 1 or 2")
    root = Path(config.root).resolve()
    if root.exists() and any(root.iterdir()):
        raise V5LiveRunnerError("P8 root must be fresh")
    authorization_checker(LiveAction.MEMBIND_V5, state_path=config.state_path)
    baseline = verify_baseline_reference(config.baseline_root, allow_invalid_qa=True)
    namespace = f"membind-v5-p8-{config.run_id}-{uuid.uuid4().hex[:10]}"
    episodes = tuple(episode_loader(Path.cwd(), config.history_id, namespace))[: config.source_count]
    if len(episodes) != config.source_count or [int(row.source_sequence) for row in episodes] != list(range(config.source_count)):
        raise V5LiveRunnerError("frozen episode selection is incomplete")

    runtime = await _maybe_await(runtime_builder())
    graphiti = runtime.graphiti
    recorder = recorder_factory()
    instrumentation = instrumentation_installer(graphiti, recorder)
    closed = False

    async def close_runtime() -> None:
        nonlocal closed
        if closed:
            return
        closed = True
        if instrumentation is not None:
            instrumentation.restore()
        close = getattr(graphiti, "close", None)
        if callable(close):
            await _maybe_await(close())

    original_llm = runtime.llm_client
    authority = CapacityAuthority.from_protocol_runtime(runtime)
    arbiter = AdmissionArbiter(authority)
    frontier = {"value": -1}
    store = TranscriptStore()
    identity = {
        "class": f"{type(original_llm).__module__}.{type(original_llm).__qualname__}",
        "source_hash": hashlib.sha256(inspect.getsource(type(original_llm)).encode()).hexdigest()
        if inspect.isclass(type(original_llm))
        else "unknown",
    }
    capture = FrontierAwareLLMClient(
        original_llm,
        store=store,
        arbiter=arbiter,
        mode="capture",
        durable_frontier=lambda: frontier["value"],
        client_identity=identity,
    )
    graphiti.llm_client = capture
    graphiti.clients.llm_client = capture

    prepare_rows: dict[int, Any] = {}
    prep_events: list[dict[str, Any]] = []
    publication_events: list[dict[str, Any]] = []
    replay_clients: list[FrontierAwareLLMClient] = []
    timer_start = time.monotonic_ns()

    async def prepare(sequence: int) -> Any:
        episode = episodes[sequence]
        node_episode = _episode_node(episode, namespace=namespace)
        previous = [_episode_node(item, namespace=namespace, uuid_value=f"prep-{item.source_sequence}") for item in episodes[:sequence]]
        with recorder.episode_scope(config.run_id, episode.name, sequence):
            with provider_scope(region="PREPARE", source_sequence=sequence):
                nodes, index_map = await extract_nodes(
                    graphiti.clients,
                    node_episode,
                    previous,
                    None,
                    None,
                    None,
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
        row = {"source_sequence": sequence, "node_count": len(nodes), "edge_count": len(edges), "node_index_count": len(index_map)}
        prep_events.append({"event": "PREPARE_COMPLETE", **row})
        return row

    prep_tasks = [asyncio.create_task(prepare(index)) for index in range(config.source_count)]
    try:
        # Preparation tasks share Graphiti's client container.  Complete the
        # concurrent capture phase before swapping that container to replay;
        # otherwise a slower future task could observe the native proxy outside
        # its binding scope.  This preserves concurrent preparation while making
        # the capture/replay handoff deterministic and fail-closed.
        prepared_values = await asyncio.gather(*prep_tasks)
        for sequence, episode in enumerate(episodes):
            prepared = prepared_values[sequence]
            prepare_rows[sequence] = prepared
            replay = FrontierAwareLLMClient(
                original_llm,
                store=store,
                arbiter=arbiter,
                mode="replay",
                durable_frontier=lambda: frontier["value"],
                client_identity=identity,
            )
            replay_clients.append(replay)
            graphiti.llm_client = replay
            graphiti.clients.llm_client = replay
            with recorder.episode_scope(config.run_id, episode.name, sequence):
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    with NativeBindingScope(store, source_sequence=sequence):
                        result = await graphiti.add_episode(**_graphiti_kwargs(episode, namespace=namespace))
            frontier["value"] = sequence
            publication_events.append({"event": "PUBLICATION_DURABLE", "source_sequence": sequence, "monotonic_ns": time.monotonic_ns()})
            prepare_rows[sequence]["native_node_count"] = len(getattr(result, "nodes", ()) or ())
            prepare_rows[sequence]["native_edge_count"] = len(getattr(result, "edges", ()) or ())
    except BaseException:
        await close_runtime()
        raise
    finally:
        for task in prep_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*prep_tasks, return_exceptions=True)

    timer_stop = publication_events[-1]["monotonic_ns"] if publication_events else time.monotonic_ns()
    graph = await _maybe_await(graph_exporter(graphiti, list(episodes), namespace))
    if not isinstance(graph, Mapping):
        await close_runtime()
        raise V5LiveRunnerError("canonical graph export invalid")
    native_trace = [recorder.episode_envelope(config.run_id, episode.name, episode.source_sequence) for episode in episodes]
    admission_rows = list(capture.provider_calls)
    for replay_client in replay_clients:
        admission_rows.extend(replay_client.provider_calls)
    logical_summary = store.summary()
    if int(logical_summary.get("captured", logical_summary.get("logical_captured", 0))) <= 0:
        await close_runtime()
        raise V5LiveRunnerError("no captured logical transcript")
    manifest = {
        "schema_version": "membind.v5.p8-manifest.v1",
        "status": "PASS",
        "method": V5_METHOD,
        "run_id": config.run_id,
        "history_id": config.history_id,
        "namespace": namespace,
        "source_count": config.source_count,
        "baseline_reference": baseline,
        "native_graphiti_path": "Graphiti.add_episode",
        "preparation": "graphiti_core.utils.maintenance.node_operations.extract_nodes + edge_operations.extract_edges",
    }
    body = {
        "manifest": manifest,
        "live_authority": {"action": LiveAction.MEMBIND_V5.value, "state_path": str(config.state_path), "native_characterization_c5_reused": False},
        "capacity_authority": authority.to_dict(),
        "oracle_binding_summary": {
            "logical_captured": logical_summary.get("logical_captured", logical_summary.get("captured", 0)),
            "logical_consumed": logical_summary.get("logical_consumed", logical_summary.get("consumed", 0)),
            "provider_replay_calls": 0,
            "replay_binding_calls": sum(1 for row in admission_rows if row.get("replay") is True and row.get("admitted") is False),
            "provider_native_calls": sum(1 for row in admission_rows if row.get("region") == "NATIVE" and row.get("admitted") is True),
            "replay_sources": sorted({int(row["source_sequence"]) for row in admission_rows if row.get("replay") is True}),
        },
        "frontier": publication_events,
        "admission": admission_rows,
        "logical_work_summary": logical_summary,
        "native_trace": native_trace,
        "block_metrics": {"timer_start_ns": timer_start, "timer_stop_ns": timer_stop, "build_makespan_ns": timer_stop - timer_start, "source_count": config.source_count, "frontier": frontier["value"]},
        "canonical_graph": dict(graph),
        "lifecycle": {"status": "DURABLE", "timer_start_ns": timer_start, "timer_stop_ns": timer_stop},
    }
    await close_runtime()
    root.mkdir(parents=True, exist_ok=False)
    _write_new(root / "manifest.json", manifest)
    _write_new(root / "live_authority.json", body["live_authority"])
    _write_new(root / "capacity_authority.json", body["capacity_authority"])
    _write_new(root / "oracle_binding_summary.json", body["oracle_binding_summary"])
    _write_jsonl(root / "frontier.jsonl", publication_events)
    _write_jsonl(root / "admission.jsonl", admission_rows)
    _write_new(root / "logical_work_summary.json", logical_summary)
    _write_jsonl(root / "native_trace.jsonl", native_trace)
    _write_new(root / "block_metrics.json", body["block_metrics"])
    _write_new(root / "canonical_graph.json", dict(graph))
    _write_new(root / "lifecycle.json", body["lifecycle"])
    seal = {"schema_version": "membind.v5.p8-seal.v1", "status": "P8_LIVE_SEALED", "method": V5_METHOD, "namespace": namespace, "source_count": config.source_count, "build_makespan_ns": timer_stop - timer_start}
    _write_new(root / "seal.json", seal)
    await close_runtime()
    return {**body, "seal": seal, "root": str(root)}


__all__ = ["P8LiveConfig", "V5LiveRunnerError", "run_p8_minimal_live_async"]
