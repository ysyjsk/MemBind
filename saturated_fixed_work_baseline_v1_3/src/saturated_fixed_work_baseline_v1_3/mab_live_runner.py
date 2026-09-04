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
import uuid
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


MAB8192_ADAPTER_VERSION = "MAB_ROLE_AWARE_LOSSLESS_8192_V1"
_MAB8192_FORMAL_ARMS = frozenset(
    {
        "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
        "RELAXED_ORDER_SHARED_BOUNDED_SO",
        "MEMBIND_V6_1_SHARED_BOUNDED_SO",
        "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
        "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
        "MEMBIND_V6_1_UPSTREAM_CORE_MAB8192",
    }
)


def _reliability_identity() -> dict[str, str]:
    """Load the shared structured-output identity after package initialization."""

    from .membind_v6_1.structured_output_recovery import reliability_identity

    return reliability_identity()


async def resolve_runtime_builder(runtime_builder: Callable[[], Any]) -> Any:
    """Resolve a runtime factory regardless of sync/async construction style."""

    return await _maybe_await(runtime_builder())


class _AppendOnlyJsonl:
    """Durable event journal with no method-specific ordering predicate."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._committed: dict[int, dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(row, Mapping)
                    and row.get("event") == "PUBLICATION_COMMITTED"
                    and isinstance(row.get("source_sequence"), int)
                    and isinstance(row.get("idempotency_key"), str)
                ):
                    self._committed[int(row["source_sequence"])] = dict(row)
            self._stream = self.path.open("a", encoding="utf-8")
        else:
            self._stream = self.path.open("x", encoding="utf-8")

    def committed_publications(self) -> dict[int, dict[str, Any]]:
        return {sequence: dict(row) for sequence, row in self._committed.items()}

    def append(self, row: Mapping[str, Any]) -> None:
        self._stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        if (
            row.get("event") == "PUBLICATION_COMMITTED"
            and isinstance(row.get("source_sequence"), int)
            and isinstance(row.get("idempotency_key"), str)
        ):
            self._committed[int(row["source_sequence"])] = dict(row)

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
    dataset_revision: str = "UNSPECIFIED"
    original_source_sequence: int = 0
    chunk_ordinal: int = 0
    chunk_count: int = 1
    chunk_id: str = ""
    previous_chunk_id: str | None = None
    adapter_version: str | None = None

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
        dataset_revision=str(getattr(item, "dataset_revision", "UNSPECIFIED")),
        original_source_sequence=int(
            getattr(item, "original_source_sequence", getattr(item, "source_sequence", sequence))
        ),
        chunk_ordinal=int(getattr(item, "chunk_ordinal", 0)),
        chunk_count=int(getattr(item, "chunk_count", 1)),
        chunk_id=str(getattr(item, "chunk_id", "")),
        previous_chunk_id=getattr(item, "previous_chunk_id", None),
        adapter_version=getattr(item, "adapter_version", None),
    )


def _logical_identity(episode: MABLiveEpisode) -> dict[str, Any]:
    return {
        "dataset_revision": episode.dataset_revision,
        "context_id": episode.context_id,
        "source_sequence": episode.original_source_sequence,
        "chunk_ordinal": episode.chunk_ordinal,
        "session_id": episode.session_id,
        "chunk_id": episode.chunk_id or episode.episode_id,
    }


def _adapter_coverage(
    episodes: Sequence[MABLiveEpisode], *, require_mab8192: bool = False
) -> dict[str, Any]:
    by_session: dict[str, list[MABLiveEpisode]] = {}
    for episode in episodes:
        by_session.setdefault(episode.session_id, []).append(episode)
    violations: list[dict[str, Any]] = []
    source_sequence_owners: dict[int, str] = {}
    for session_id, values in by_session.items():
        values.sort(key=lambda item: item.source_sequence)
        global_sequences = [item.source_sequence for item in values]
        if global_sequences != list(
            range(global_sequences[0], global_sequences[0] + len(global_sequences))
        ):
            violations.append(
                {"session_id": session_id, "reason": "session_chunks_not_adjacent"}
            )
        if [item.chunk_ordinal for item in values] != list(range(len(values))):
            violations.append({"session_id": session_id, "reason": "chunk_ordinal_gap"})
            continue
        if any(item.chunk_count != len(values) for item in values):
            violations.append({"session_id": session_id, "reason": "chunk_count_mismatch"})
        if len({item.original_source_sequence for item in values}) != 1:
            violations.append(
                {"session_id": session_id, "reason": "session_source_sequence_mismatch"}
            )
        else:
            original_sequence = values[0].original_source_sequence
            owner = source_sequence_owners.setdefault(original_sequence, session_id)
            if owner != session_id:
                violations.append(
                    {
                        "session_id": session_id,
                        "reason": "original_source_sequence_not_unique",
                        "original_source_sequence": original_sequence,
                        "other_session_id": owner,
                    }
                )
        for index, item in enumerate(values):
            expected = None if index == 0 else values[index - 1].chunk_id
            if item.previous_chunk_id != expected:
                violations.append({"session_id": session_id, "reason": "predecessor_mismatch"})
    versions = {item.adapter_version for item in episodes if item.adapter_version is not None}
    dataset_revisions = {item.dataset_revision for item in episodes}
    context_ids = {item.context_id for item in episodes}
    all_bounded = all(len(item.body) <= 8192 for item in episodes)
    if len(versions) > 1:
        violations.append({"reason": "adapter_version_not_unique"})
    if require_mab8192 and versions != {MAB8192_ADAPTER_VERSION}:
        violations.append(
            {
                "reason": "formal_adapter_version_mismatch",
                "expected": MAB8192_ADAPTER_VERSION,
                "observed": sorted(versions),
            }
        )
    if len(dataset_revisions) != 1:
        violations.append({"reason": "dataset_revision_not_unique"})
    if len(context_ids) != 1:
        violations.append({"reason": "context_identity_not_unique"})
    if (require_mab8192 or MAB8192_ADAPTER_VERSION in versions) and not all_bounded:
        violations.append({"reason": "chunk_body_exceeds_8192"})
    chunk_ids = [item.chunk_id for item in episodes]
    if require_mab8192 and (
        any(not chunk_id for chunk_id in chunk_ids)
        or len(set(chunk_ids)) != len(chunk_ids)
    ):
        violations.append({"reason": "chunk_id_inventory_invalid"})
    return {
        "status": "PASS" if not violations else "FAIL",
        "adapter_version": next(iter(versions)) if len(versions) == 1 else None,
        "dataset_revision": (
            next(iter(dataset_revisions)) if len(dataset_revisions) == 1 else None
        ),
        "context_id": next(iter(context_ids)) if len(context_ids) == 1 else None,
        "chunk_count": len(episodes),
        "session_count": len(by_session),
        "all_chunk_bodies_bounded": all_bounded,
        "dependency_violation_count": len(violations),
        "violations": violations,
    }


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


def _mab_graphiti_kwargs(
    episode: MABLiveEpisode,
    *,
    namespace: str,
    include_uuid: bool = True,
) -> dict[str, Any]:
    """Build the pinned Graphiti call shape.

    A strict upstream write deliberately omits ``uuid``: in Graphiti's pinned
    implementation a supplied UUID is an existing-node lookup.  Strict native
    and V6.1 publication therefore omit it; V6.1 keeps a separate local
    idempotency key for at-least-once replay accounting.
    """

    publication_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"membind:{namespace}:{episode.context_id}:{episode.source_sequence}:"
            f"{episode.source_hash}",
        )
    )
    try:
        from graphiti_core.nodes import EpisodeType
    except ModuleNotFoundError:
        result = {
            "name": episode.name,
            "episode_body": episode.body,
            "source_description": "MemoryAgentBench LongMemEval session",
            "reference_time": episode.reference_time,
            "group_id": namespace,
        }
        if include_uuid:
            result["uuid"] = publication_uuid
        return result
    result = {
        "name": episode.name,
        "episode_body": episode.body,
        "source_description": "MemoryAgentBench LongMemEval session",
        "reference_time": _parse_reference_time(episode.reference_time),
        "source": EpisodeType.message,
        "group_id": namespace,
    }
    if include_uuid:
        result["uuid"] = publication_uuid
    return result


def _mab_publication_idempotency_key(
    episode: MABLiveEpisode,
    *,
    namespace: str,
) -> str:
    """Derive a stable source key without assigning Graphiti's episode UUID.

    Graphiti 0.29.3 interprets ``uuid=`` as a lookup of an already persisted
    ``Episodic`` node.  The key is therefore a local publication identity only;
    callers must not pass it as the Graphiti ``uuid`` on a fresh write.
    """

    source_key = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"membind-idempotency:{namespace}:{episode.context_id}:"
        f"{episode.source_sequence}:{episode.source_hash}",
    )
    return f"membind-idempotency:{source_key}"


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
    publication_fault_injector: Callable[[str, int, Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Run one fresh MAB construction block and materialize its seal."""

    if method not in METHOD_CLASSES:
        raise MABLiveRunnerError("METHOD_NOT_FROZEN")
    selected = tuple(episode_from_input(item) for item in episodes)
    if not selected or [item.source_sequence for item in selected] != list(range(len(selected))):
        raise MABLiveRunnerError("MAB_EPISODE_SEQUENCE_INVALID")
    adapter_coverage = _adapter_coverage(
        selected, require_mab8192=method in _MAB8192_FORMAL_ARMS
    )
    if adapter_coverage["status"] != "PASS":
        reasons = ",".join(
            str(row.get("reason", "unknown")) for row in adapter_coverage["violations"]
        )
        raise MABLiveRunnerError(
            f"MAB8192_ADAPTER_COVERAGE_INVALID:{reasons}"
        )
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
    publication_state = journals["raw"].committed_publications()
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
        formal_runtime = None
        if method in {
            "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
            "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
        }:
            from .membind_v6_1.upstream_runtime import formal_runtime_identity

            formal_runtime = formal_runtime_identity(
                runtime,
                mab8192_manifest_sha256=str(
                    getattr(workload_manifest, "manifest_sha256", "")
                ),
            )
        recorder = recorder_factory()
        instrumentation = instrumentation_installer(graphiti, recorder)
        formal_start = time.monotonic_ns()
        lifecycle["events"].append({"event": "FORMAL_START", "monotonic_ns": formal_start})
        emit("FORMAL_START", stamp=formal_start)

        # Shared bounded-output A/B keep the Native state/publication
        # semantics while changing only the arm-agnostic wire substrate.
        # Treat them as Native for the journal/resume contract; C alone uses
        # the V6.1 recovery/scheduler path.
        strict_native = method in {
            "GRAPHITI_UPSTREAM_SERIAL",
            "RELAXED_ORDER_PARALLEL",
            "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
            "RELAXED_ORDER_SHARED_BOUNDED_SO",
            "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
            "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
        }

        async def direct_publish(sequence: int) -> None:
            episode = selected[sequence]
            # Graphiti 0.29.3 treats ``uuid=`` as an existing-node lookup.
            # Every fresh publication therefore omits it; the V6.1-compatible
            # local key below is the only replay identity used by this branch.
            publication_kwargs = _mab_graphiti_kwargs(
                episode,
                namespace=namespace,
                include_uuid=False,
            )
            if strict_native:
                # Native A/B has no V6.1 journal replay or idempotency branch.
                # The journal records observation only and cannot alter the
                # upstream Graphiti write path.
                emit("NATIVE_ENTER", sequence)
                record(
                    {
                        "event": "UPSTREAM_PUBLICATION_BEGIN",
                        "source_sequence": sequence,
                        "context_id": context_id,
                        "publication_guarantee": "UPSTREAM_GRAPHITI_NO_RESUME",
                    }
                )
                from .membind_v6_1.upstream_runtime import logical_request_context

                with logical_request_context(_logical_identity(episode)):
                    with recorder.episode_scope(run_id, episode.name, sequence):
                        with provider_scope(region="NATIVE", source_sequence=sequence):
                            await graphiti.add_episode(**publication_kwargs)
                emit("PUBLICATION_DURABLE", sequence, time.monotonic_ns())
                record(
                    {
                        "event": "UPSTREAM_PUBLICATION_RETURNED",
                        "source_sequence": sequence,
                        "context_id": context_id,
                        "publication_guarantee": "UPSTREAM_GRAPHITI_NO_RESUME",
                    }
                )
                return
            idempotency_key = _mab_publication_idempotency_key(
                episode, namespace=namespace
            )
            prior = publication_state.get(sequence)
            if prior is not None:
                if prior.get("idempotency_key") != idempotency_key:
                    raise MABLiveRunnerError("SOURCE_PUBLICATION_IDEMPOTENCY_KEY_CHANGED")
                emit("NATIVE_ENTER", sequence)
                emit("PUBLICATION_DURABLE", sequence, time.monotonic_ns())
                record(
                    {
                        "event": "PUBLICATION_REUSED",
                        "source_sequence": sequence,
                        "idempotency_key": idempotency_key,
                        "context_id": context_id,
                    }
                )
                return
            emit("NATIVE_ENTER", sequence)
            record(
                {
                    "event": "PUBLICATION_BEGIN",
                    "source_sequence": sequence,
                    "idempotency_key": idempotency_key,
                    "context_id": context_id,
                }
            )
            if publication_fault_injector is not None:
                await _maybe_await(
                    publication_fault_injector(
                        "before_db_write", sequence, publication_kwargs
                    )
                )
            with recorder.episode_scope(run_id, episode.name, sequence):
                with provider_scope(region="NATIVE", source_sequence=sequence):
                    result = await graphiti.add_episode(**publication_kwargs)
            result_digest = hashlib.sha256(
                json.dumps(result, ensure_ascii=False, sort_keys=True, default=str).encode(
                    "utf-8"
                )
            ).hexdigest()
            # This fault window models a database commit that becomes visible
            # before the local COMMITTED journal record is durable.  It is
            # Intentionally expose this fault window: a restart can replay the
            # source under the stable local key, but the database and journal
            # are separate systems and do not provide atomic exactly-once.
            if publication_fault_injector is not None:
                await _maybe_await(
                    publication_fault_injector(
                        "after_db_commit_before_journal", sequence, publication_kwargs
                    )
                )
            record(
                {
                    "event": "PUBLICATION_COMMITTED",
                    "source_sequence": sequence,
                    "idempotency_key": idempotency_key,
                    "result_sha256": result_digest,
                    "context_id": context_id,
                }
            )
            publication_state[sequence] = {
                "event": "PUBLICATION_COMMITTED",
                "idempotency_key": idempotency_key,
                "result_sha256": result_digest,
            }
            if publication_fault_injector is not None:
                await _maybe_await(
                    publication_fault_injector("after_commit", sequence, publication_kwargs)
                )
            durable = time.monotonic_ns()
            emit("PUBLICATION_DURABLE", sequence, durable)
            record({"event": "PUBLICATION_DURABLE", "source_sequence": sequence, "monotonic_ns": durable, "context_id": context_id})

        shared_serial = method in {"GRAPHITI_SERIAL_SHARED_BOUNDED_SO"}
        shared_parallel = method in {"RELAXED_ORDER_SHARED_BOUNDED_SO"}
        if method in {
            "B0",
            "GRAPHITI_UPSTREAM_SERIAL",
            "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
        } or shared_serial:
            for sequence in range(len(selected)):
                emit("SUBMIT", sequence)
                await direct_publish(sequence)
        elif method in {
            "B1",
            "RELAXED_ORDER_PARALLEL",
            "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
        } or shared_parallel:
            for sequence in range(len(selected)):
                emit("SUBMIT", sequence)
            by_session: dict[str, list[int]] = {}
            for sequence, episode in enumerate(selected):
                by_session.setdefault(episode.session_id, []).append(sequence)

            async def publish_session(sequences: Sequence[int]) -> None:
                for sequence in sequences:
                    await direct_publish(sequence)

            # Session tasks may overlap, but every task owns one complete,
            # ascending chunk dependency chain.
            await asyncio.gather(
                *(asyncio.create_task(publish_session(tuple(sequences))) for sequences in by_session.values())
            )
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
            "runtime_identity": formal_runtime,
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
            "publication_guarantee": (
                "UPSTREAM_GRAPHITI_NO_RESUME"
                if strict_native
                else "AT_LEAST_ONCE_WITH_STABLE_IDEMPOTENCY_KEY"
            ),
            "adapter_coverage": adapter_coverage,
            **(
                {
                    "structured_output_policy": "UPSTREAM_GRAPHITI_PYDANTIC_JSON_SCHEMA",
                    "response_repair_enabled": formal_runtime[
                        "response_repair_enabled"
                    ],
                    "finite_pair_tasks_enabled": formal_runtime[
                        "finite_pair_tasks_enabled"
                    ],
                }
                if method in {
                    "GRAPHITI_SERIAL_UPSTREAM_CORE_MAB8192",
                    "GRAPHITI_ASYNC_UPSTREAM_CORE_MAB8192",
                    "GRAPHITI_SERIAL_SHARED_BOUNDED_SO",
                    "RELAXED_ORDER_SHARED_BOUNDED_SO",
                }
                else _reliability_identity()
            ),
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
