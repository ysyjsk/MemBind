"""Isolated live composition for the first aligned MemBind-v1 table.

This module deliberately owns a *single fresh block*, not the whole paper
matrix.  It binds one already-verified plan block to a new Graphiti namespace,
uses the same request-level LLM admission for U0/P(C=2)/MemBind, and persists
only content-safe lifecycle evidence through :class:`AlignedBlockArtifactStore`.
The caller owns service lifecycle, plan creation, tmux, and table rendering.

All external behavior is injected through ``AlignedLiveHooks``.  The default
hooks use lazy imports at the live boundary; focused tests therefore exercise
the complete scheduling and durability contract without importing Graphiti or
opening a network connection.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.admission import RequestAdmission
from paper_eval.membind_v1.aligned_artifacts import (
    AlignedBlockArtifactStore,
    inspect_aligned_block_artifacts,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_METHODS,
    AlignedPlanError,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_schedule import (
    AlignedEpisodeRef,
    P_C2_ALIGNED,
    U0_ALIGNED,
    run_aligned_baseline,
)
from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.graphiti_adapter import NodeArtifactIdentity
from paper_eval.membind_v1.graphiti_factories import build_source_log_from_episodes
from paper_eval.membind_v1.live_runtime import build_membind_v1_runtime
from paper_eval.membind_v1.runner import run_membind_v1
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v1.store import MemBindV1AttemptStore


_METHOD_MEMBIND = "MemBind-v1 node-only"
_FIRST_V1_LAST_N = 10
_SHA256_LENGTH = 64


class AlignedLiveBlockError(RuntimeError):
    """A fresh aligned block cannot safely be composed or accepted."""


def _fail(code: str) -> AlignedLiveBlockError:
    return AlignedLiveBlockError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        raise _fail(code)
    try:
        int(value, 16)
    except ValueError:
        raise _fail(code) from None
    return value


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    return await value


RuntimeBuilder = Callable[..., object]
RuntimeReady = Callable[[object], Awaitable[object]]
NamespaceProbe = Callable[[object, str], Awaitable[Mapping[str, object]]]
NamespaceEpisode = Callable[[object, str], object]
NativeAddEpisode = Callable[[object, object, SourceRecord], Awaitable[object]]
ReferenceTimeToNs = Callable[[str], int]
MemBindAdapterFactory = Callable[[object, SourceLog, NodeArtifactIdentity], object]
CloseRuntime = Callable[[object], Awaitable[object]]
ClockNs = Callable[[], int]
Sleep = Callable[[float], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class AlignedLiveHooks:
    """The narrow live boundary for one aligned row.

    ``native_add_episode`` receives the immutable ``SourceRecord`` in addition
    to the original namespaced workload object, so the benchmark can verify
    exact source content and namespace routing.  ``SourceRecord.episode_uuid``
    remains an internal candidate identity: pinned Graphiti treats a supplied
    ``add_episode(uuid=...)`` as a lookup, so native U0/P correctly retain the
    public API's fresh-node ``uuid=None`` behavior.
    """

    runtime_builder: RuntimeBuilder
    runtime_ready: RuntimeReady
    namespace_probe: NamespaceProbe
    namespace_episode: NamespaceEpisode
    native_add_episode: NativeAddEpisode
    reference_time_to_ns: ReferenceTimeToNs
    membind_adapter_factory: MemBindAdapterFactory
    close_runtime: CloseRuntime


def _verify_hooks(value: object) -> AlignedLiveHooks:
    if not isinstance(value, AlignedLiveHooks):
        raise _fail("live hooks invalid")
    for field in value.__dataclass_fields__:
        if not callable(getattr(value, field)):
            raise _fail("live hooks invalid")
    return value


def _plan_block(
    verified_plan: Mapping[str, object], block_index: object
) -> tuple[dict[str, object], dict[str, object], tuple[str, ...], tuple[int, ...]]:
    try:
        plan = verify_aligned_development_plan(verified_plan)
    except (AlignedPlanError, ValueError, TypeError):
        raise _fail("verified plan invalid") from None
    index = _nonnegative_int(block_index, "block index invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or index >= len(blocks):
        raise _fail("plan block invalid")
    raw_block = blocks[index]
    if not isinstance(raw_block, Mapping):
        raise _fail("plan block invalid")
    block = dict(raw_block)
    if block.get("block_index") != index or block.get("method") not in ALIGNED_METHODS:
        raise _fail("plan block invalid")
    if block.get("global_llm_admission_k") != 2:
        raise _fail("global LLM admission invalid")
    history_id = block.get("history_id")
    histories = plan.get("history_source_sha256s")
    traces = plan.get("arrival_traces")
    if not isinstance(history_id, str) or not isinstance(histories, Mapping) or not isinstance(traces, Mapping):
        raise _fail("plan block invalid")
    raw_hashes = histories.get(history_id)
    trace = traces.get(history_id)
    if (
        isinstance(raw_hashes, (str, bytes))
        or not isinstance(raw_hashes, Sequence)
        or not isinstance(trace, Mapping)
    ):
        raise _fail("plan block invalid")
    hashes = tuple(_sha(value, "plan source identity invalid") for value in raw_hashes)
    offsets_raw = trace.get("arrival_offsets_ns")
    if isinstance(offsets_raw, (str, bytes)) or not isinstance(offsets_raw, Sequence):
        raise _fail("plan arrival trace invalid")
    offsets = tuple(_nonnegative_int(value, "plan arrival trace invalid") for value in offsets_raw)
    if len(hashes) != int(block.get("source_count", -1)) or len(offsets) != len(hashes):
        raise _fail("plan source count invalid")
    if any(right < left for left, right in zip(offsets, offsets[1:])):
        raise _fail("plan arrival trace invalid")
    return plan, block, hashes, offsets


def _snapshot(value: object, *, code: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    required = {"node_count", "relationship_count", "episode_names"}
    if set(value) != required:
        raise _fail(code)
    nodes = _nonnegative_int(value.get("node_count"), code)
    relationships = _nonnegative_int(value.get("relationship_count"), code)
    names = value.get("episode_names")
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise _fail(code)
    normalized_names = [item for item in names if isinstance(item, str) and item]
    if len(normalized_names) != len(names) or len(set(normalized_names)) != len(normalized_names):
        raise _fail(code)
    return {
        "node_count": nodes,
        "relationship_count": relationships,
        "episode_names": sorted(normalized_names),
    }


async def _fresh_snapshot(hooks: AlignedLiveHooks, runtime: object, namespace: str) -> dict[str, object]:
    value = await _await(hooks.namespace_probe(runtime, namespace), "namespace probe must be async")
    snapshot = _snapshot(value, code="namespace probe invalid")
    if snapshot["node_count"] != 0 or snapshot["relationship_count"] != 0:
        raise _fail("fresh namespace not empty")
    if snapshot["episode_names"]:
        raise _fail("fresh namespace has episodes")
    return snapshot


def _namespaced_episodes(
    episodes: Sequence[object], *, namespace: str, hooks: AlignedLiveHooks
) -> tuple[object, ...]:
    if isinstance(episodes, (str, bytes)) or not isinstance(episodes, Sequence) or not episodes:
        raise _fail("episodes invalid")
    result: list[object] = []
    for episode in episodes:
        try:
            scoped = hooks.namespace_episode(episode, namespace)
        except AlignedLiveBlockError:
            raise
        except Exception:
            raise _fail("namespace episode materialization failed") from None
        if scoped is None or getattr(scoped, "group_id", None) != namespace:
            raise _fail("namespace episode materialization failed")
        result.append(scoped)
    return tuple(result)


def _membind_attempt_run_id(block: Mapping[str, object]) -> str:
    """Derive a compact store-safe ID without exposing workload content."""

    digest = payload_sha256(
        {
            "aligned_run_id": block.get("aligned_run_id"),
            "block_index": block.get("block_index"),
            "history_id": block.get("history_id"),
            "method": block.get("method"),
        }
    )
    return f"mv1-{str(block['history_id'])}-{int(block['block_index']):02d}-{digest[:20]}"


def _expected_episode_names(source_log: SourceLog) -> list[str]:
    names: list[str] = []
    for source in source_log.records:
        value = source.episode_projection.get("name")
        if not isinstance(value, str) or not value:
            raise _fail("source episode name invalid")
        names.append(value)
    if len(set(names)) != len(names):
        raise _fail("source episode name invalid")
    return sorted(names)


def _seal_terminal_failures(store: AlignedBlockArtifactStore, *, clock_ns: ClockNs) -> None:
    """Close every durably admitted unfinished source after an execution error.

    This is a failure classification boundary, not a retry path.  Completed
    source prefixes are retained, while any source that has durably entered
    the common lifecycle is append-only sealed ``TERMINAL_FAILURE``.  A source
    that never reached ARRIVAL deliberately has no synthetic lifecycle record.
    """

    inspected = inspect_aligned_block_artifacts(store.root)
    checkpoint = inspected["checkpoint"]
    states = checkpoint.get("source_states")
    events = inspected["events"]
    if not isinstance(states, list) or not isinstance(events, list):
        raise _fail("aligned failure checkpoint invalid")
    terminalizable = {"ARRIVAL", "ENQUEUED", "SERVICE_STARTED"}
    last_timestamp: dict[int, int] = {}
    for event in events:
        if not isinstance(event, Mapping):
            raise _fail("aligned failure checkpoint invalid")
        source = event.get("source_sequence")
        timestamp = event.get("timestamp_ns")
        if (
            isinstance(source, int)
            and not isinstance(source, bool)
            and isinstance(timestamp, int)
            and not isinstance(timestamp, bool)
        ):
            last_timestamp[source] = timestamp
    for sequence, state in enumerate(states):
        if state not in terminalizable:
            continue
        timestamp = max(
            _nonnegative_int(clock_ns(), "clock invalid"),
            last_timestamp.get(sequence, -1) + 1,
        )
        store.append_lifecycle(
            sequence,
            event_type="TERMINAL_FAILURE",
            timestamp_ns=timestamp,
            telemetry={"failure_classification": "execution_error"},
        )


async def _run_native_row(
    *,
    block: Mapping[str, object],
    scoped_episodes: Sequence[object],
    source_log: SourceLog,
    offsets: Sequence[int],
    runtime: object,
    hooks: AlignedLiveHooks,
    store: AlignedBlockArtifactStore,
    clock_ns: ClockNs,
    sleep: Sleep,
) -> dict[str, object]:
    refs = tuple(
        AlignedEpisodeRef(
            source_sequence=source.source_sequence,
            source_sha256=source.source_sha256,
            native_episode=scoped_episodes[source.source_sequence],
        )
        for source in source_log.records
    )

    async def native_add(native_episode: object) -> object:
        sequence = getattr(native_episode, "source_sequence", None)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence >= source_log.source_count:
            raise _fail("native episode source sequence invalid")
        return await _await(
            hooks.native_add_episode(runtime, native_episode, source_log.record(sequence)),
            "native add episode must be async",
        )

    async def lifecycle(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
        store.append_lifecycle(
            source_sequence,
            event_type=event_type,
            timestamp_ns=timestamp_ns,
            telemetry={"execution_path": "native-whole-update"},
        )

    return await run_aligned_baseline(
        method=str(block["method"]),
        episodes=refs,
        arrival_offsets_ns=offsets,
        native_add_episode=native_add,
        clock_ns=clock_ns,
        sleep=sleep,
        lifecycle_observer=lifecycle,
    )


async def _run_membind_row(
    *,
    block: Mapping[str, object],
    source_log: SourceLog,
    offsets: Sequence[int],
    runtime: object,
    hooks: AlignedLiveHooks,
    store: AlignedBlockArtifactStore,
    block_root: Path,
    execution_identity_sha256: str,
    artifact_identity: NodeArtifactIdentity,
    clock_ns: ClockNs,
    logical_clock_ns: ClockNs,
    sleep: Sleep,
) -> tuple[dict[str, object], Path]:
    fences = tuple(
        EvidenceFence.capture(
            source_log,
            target_source_sequence=source.source_sequence,
            last_n=_FIRST_V1_LAST_N,
        )
        for source in source_log.records
    )
    inputs = tuple(
        build_compile_input(source, fence)
        for source, fence in zip(source_log.records, fences, strict=True)
    )
    try:
        adapter = hooks.membind_adapter_factory(runtime, source_log, artifact_identity)
    except AlignedLiveBlockError:
        raise
    except Exception:
        raise _fail("membind adapter materialization failed") from None
    if not callable(getattr(adapter, "prepare", None)) or not callable(getattr(adapter, "bind", None)):
        raise _fail("membind adapter materialization failed")
    attempt_root = block_root / "membind-attempt"
    attempt = MemBindV1AttemptStore.create(
        attempt_root,
        run_id=_membind_attempt_run_id(block),
        namespace=str(block["namespace"]),
        source_sha256s=[source.source_sha256 for source in source_log.records],
        source_manifest_sha256=source_log.inventory_sha256,
        execution_identity_sha256=execution_identity_sha256,
    )
    run_start = _nonnegative_int(clock_ns(), "clock invalid")
    arrival_times = [run_start + offset for offset in offsets]

    async def lifecycle(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
        store.append_lifecycle(
            source_sequence,
            event_type=event_type,
            timestamp_ns=timestamp_ns,
            telemetry={"execution_path": "membind-v1-node-only"},
        )

    runner = await run_membind_v1(
        compile_inputs=inputs,
        logical_time_ns=None,
        arrival_time_ns=arrival_times,
        adapter=adapter,
        store=attempt,
        clock_ns=clock_ns,
        logical_clock_ns=logical_clock_ns,
        sleep=sleep,
        lifecycle_observer=lifecycle,
    )
    return runner, attempt_root


async def execute_aligned_live_block(
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    episodes: Sequence[object],
    env: Mapping[str, str],
    block_root: Path,
    execution_identity_sha256: str,
    hooks: AlignedLiveHooks | None = None,
    membind_artifact_identity: NodeArtifactIdentity | None = None,
    clock_ns: ClockNs = time.monotonic_ns,
    epoch_clock_ns: ClockNs = time.time_ns,
    sleep: Sleep = asyncio.sleep,
) -> dict[str, object]:
    """Execute one fresh aligned U0, P(C=2), or node-only MemBind block.

    The function never starts services and never reads or writes historical
    artifact roots.  A caller must pass a verified common plan, a fresh local
    block root, and the workload episodes for *that plan block's* history.
    ``hooks=None`` selects the lazy production adapters only at this point.
    """

    selected_hooks = _verify_hooks(production_aligned_live_hooks() if hooks is None else hooks)
    plan, block, raw_hashes, offsets = _plan_block(verified_plan, block_index)
    execution_identity = _sha(execution_identity_sha256, "execution identity invalid")
    if not isinstance(env, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in env.items()):
        raise _fail("environment invalid")
    if not callable(clock_ns) or not callable(epoch_clock_ns) or not callable(sleep):
        raise _fail("clock or sleep invalid")
    root = Path(block_root)
    if root.exists():
        raise _fail("aligned block root already exists")
    namespace = str(block["namespace"])
    scoped_episodes = _namespaced_episodes(episodes, namespace=namespace, hooks=selected_hooks)
    try:
        source_log, observed_raw_hashes = build_source_log_from_episodes(
            scoped_episodes,
            namespace=namespace,
            reference_time_to_ns=selected_hooks.reference_time_to_ns,
        )
    except AlignedLiveBlockError:
        raise
    except Exception:
        raise _fail("source log construction failed") from None
    if observed_raw_hashes != raw_hashes or source_log.source_count != len(raw_hashes):
        raise _fail("plan workload identity mismatch")
    if str(block["method"]) == _METHOD_MEMBIND:
        if not isinstance(membind_artifact_identity, NodeArtifactIdentity):
            raise _fail("membind artifact identity required")
    elif membind_artifact_identity is not None:
        raise _fail("membind artifact identity only applies to MemBind")

    admission = RequestAdmission(limit=2)
    request_id_prefix = f"{block['aligned_run_id']}:{int(block['block_index']):02d}"
    runtime: object | None = None
    try:
        built = selected_hooks.runtime_builder(
            env=dict(env), admission=admission, request_id_prefix=request_id_prefix
        )
        if inspect.isawaitable(built):
            raise _fail("runtime builder must be synchronous")
        runtime = built
        observed_envelope = _sha(
            getattr(runtime, "execution_envelope_sha256", None),
            "runtime execution envelope invalid",
        )
        if observed_envelope != block["shared_execution_envelope_sha256"]:
            raise _fail("runtime execution envelope mismatch")
        await _await(selected_hooks.runtime_ready(runtime), "runtime readiness must be async")
        initial = await _fresh_snapshot(selected_hooks, runtime, namespace)
        store = AlignedBlockArtifactStore.create(
            root,
            verified_plan=plan,
            block_index=int(block["block_index"]),
            execution_identity_sha256=execution_identity,
        )
        schedule: dict[str, object] | None = None
        runner: dict[str, object] | None = None
        attempt_root: Path | None = None
        try:
            if block["method"] in {U0_ALIGNED, P_C2_ALIGNED}:
                schedule = await _run_native_row(
                    block=block,
                    scoped_episodes=scoped_episodes,
                    source_log=source_log,
                    offsets=offsets,
                    runtime=runtime,
                    hooks=selected_hooks,
                    store=store,
                    clock_ns=clock_ns,
                    sleep=sleep,
                )
            elif block["method"] == _METHOD_MEMBIND:
                assert isinstance(membind_artifact_identity, NodeArtifactIdentity)
                runner, attempt_root = await _run_membind_row(
                    block=block,
                    source_log=source_log,
                    offsets=offsets,
                    runtime=runtime,
                    hooks=selected_hooks,
                    store=store,
                    block_root=root,
                    execution_identity_sha256=execution_identity,
                    artifact_identity=membind_artifact_identity,
                    clock_ns=clock_ns,
                    logical_clock_ns=epoch_clock_ns,
                    sleep=sleep,
                )
            else:  # Defensive: verifier has already constrained the method.
                raise _fail("plan method invalid")
        except asyncio.CancelledError:
            raise
        except BaseException:
            _seal_terminal_failures(store, clock_ns=clock_ns)
            raise _fail("aligned live execution failed") from None
        inspected = inspect_aligned_block_artifacts(root)
        if inspected["checkpoint"].get("complete_coverage") is not True:
            raise _fail("aligned block did not reach complete coverage")
        final_value = await _await(
            selected_hooks.namespace_probe(runtime, namespace), "namespace probe must be async"
        )
        final = _snapshot(final_value, code="final namespace probe invalid")
        if final["episode_names"] != _expected_episode_names(source_log):
            raise _fail("final namespace episode mismatch")
        return {
            "schema_version": "membind.paper-eval-v3.membind-v1-aligned-live-block.v1",
            "status": "PASS",
            "aligned_run_id": block["aligned_run_id"],
            "block_index": block["block_index"],
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": namespace,
            "source_count": source_log.source_count,
            "source_manifest_sha256": block["source_manifest_sha256"],
            "arrival_trace_sha256": block["arrival_trace_sha256"],
            "history_arrival_trace_sha256": block["history_arrival_trace_sha256"],
            "shared_execution_envelope_sha256": block["shared_execution_envelope_sha256"],
            "execution_identity_sha256": execution_identity,
            "global_llm_admission_k": 2,
            "admission_observation": admission.observation(),
            "initial_namespace": initial,
            "final_namespace": final,
            "aligned_block_root": str(root),
            "schedule": schedule,
            "runner": runner,
            "membind_attempt_root": None if attempt_root is None else str(attempt_root),
        }
    finally:
        if runtime is not None:
            await _await(selected_hooks.close_runtime(runtime), "runtime close must be async")


def production_aligned_live_hooks() -> AlignedLiveHooks:
    """Build lazy production hooks without opening a service connection.

    This is intentionally a composition helper, not a service launcher.  The
    first network-capable action remains ``execute_aligned_live_block`` after
    the caller has supplied an admitted fresh block and loaded its ignored
    environment mapping.
    """

    project_root = Path(__file__).resolve().parents[3]
    legacy_src = project_root.parent / "membind-validation" / "src"

    def ensure_legacy_imports() -> None:
        value = str(legacy_src)
        if value not in sys.path:
            sys.path.insert(0, value)

    async def runtime_ready(runtime: object) -> None:
        graphiti = getattr(runtime, "graphiti", None)
        driver = getattr(graphiti, "driver", None)
        if driver is None:
            raise _fail("runtime Graphiti driver missing")
        init_task = getattr(driver, "_init_task", None)
        if init_task is not None:
            await _await(init_task, "runtime readiness invalid")
            return
        build = getattr(driver, "build_indices_and_constraints", None)
        if not callable(build):
            raise _fail("runtime readiness missing")
        await _await(build(), "runtime readiness invalid")

    async def namespace_probe(runtime: object, namespace: str) -> Mapping[str, object]:
        graphiti = getattr(runtime, "graphiti", None)
        driver = getattr(graphiti, "driver", None)
        execute_query = getattr(driver, "execute_query", None)
        if not callable(execute_query):
            raise _fail("namespace query unavailable")
        result = await _await(
            execute_query(
                """
                CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
                CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
                CALL { MATCH (n:Episodic) WHERE n.group_id = $group_id RETURN collect(n.name) AS episode_names }
                RETURN node_count, relationship_count, episode_names
                """,
                params={"group_id": namespace},
            ),
            "namespace query must be async",
        )
        records = getattr(result, "records", None)
        if isinstance(records, (str, bytes)) or not isinstance(records, Sequence) or len(records) != 1:
            raise _fail("namespace query invalid")
        row = records[0]
        getter = getattr(row, "get", None)
        if not callable(getter):
            raise _fail("namespace query invalid")
        return {
            "node_count": int(getter("node_count") or 0),
            "relationship_count": int(getter("relationship_count") or 0),
            "episode_names": [str(value) for value in getter("episode_names") or []],
        }

    def namespace_episode(episode: object, namespace: str) -> object:
        if dataclasses.is_dataclass(episode):
            try:
                return dataclasses.replace(episode, group_id=namespace)
            except (TypeError, ValueError):
                pass
        raise _fail("namespace episode materialization failed")

    async def native_add_episode(runtime: object, _episode: object, source: SourceRecord) -> object:
        graphiti = getattr(runtime, "graphiti", None)
        add_episode = getattr(graphiti, "add_episode", None)
        if not callable(add_episode):
            raise _fail("native add episode unavailable")
        try:
            ensure_legacy_imports()
            from graphiti_native import parse_datetime
            from graphiti_core.nodes import EpisodeType
            projection = source.episode_projection
            call = add_episode(
                name=projection["name"],
                episode_body=projection["body"],
                source_description=projection["source_description"],
                reference_time=parse_datetime(str(projection["reference_time"])),
                source=EpisodeType.message,
                group_id=source.group_id,
            )
        except AlignedLiveBlockError:
            raise
        except Exception:
            raise _fail("native add episode invocation failed") from None
        return await _await(call, "native add episode must be async")

    def reference_time_to_ns(value: str) -> int:
        ensure_legacy_imports()
        try:
            from graphiti_native import parse_datetime
            return int(parse_datetime(value).timestamp() * 1_000_000_000)
        except Exception:
            raise _fail("reference time invalid") from None

    def membind_adapter_factory(
        runtime: object, _source_log: SourceLog, artifact_identity: NodeArtifactIdentity
    ) -> object:
        try:
            from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
            from paper_eval.membind_v1.graphiti_adapter import MemBindV1GraphitiAdapter
            from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories
            from paper_eval.s5_graphiti_semantic_binding import load_graphiti_semantic_binding

            graphiti = getattr(runtime, "graphiti")
            factories = make_graphiti_node_factories(
                episodic_node_type=EpisodicNode,
                entity_node_type=EntityNode,
                message_source=EpisodeType.message,
            )
            return MemBindV1GraphitiAdapter(
                graphiti=graphiti,
                llm_client=getattr(graphiti, "llm_client"),
                semantic_binding=load_graphiti_semantic_binding(),
                episode_factory=factories.episode_factory,
                extracted_node_factory=factories.extracted_node_factory,
                artifact_identity=artifact_identity,
            )
        except AlignedLiveBlockError:
            raise
        except Exception:
            raise _fail("membind adapter materialization failed") from None

    async def close_runtime(runtime: object) -> None:
        graphiti = getattr(runtime, "graphiti", None)
        close = getattr(graphiti, "close", None)
        if close is None:
            return
        if not callable(close):
            raise _fail("runtime close invalid")
        await _await(close(), "runtime close must be async")

    return AlignedLiveHooks(
        runtime_builder=build_membind_v1_runtime,
        runtime_ready=runtime_ready,
        namespace_probe=namespace_probe,
        namespace_episode=namespace_episode,
        native_add_episode=native_add_episode,
        reference_time_to_ns=reference_time_to_ns,
        membind_adapter_factory=membind_adapter_factory,
        close_runtime=close_runtime,
    )


__all__ = [
    "AlignedLiveBlockError",
    "AlignedLiveHooks",
    "execute_aligned_live_block",
    "production_aligned_live_hooks",
]
