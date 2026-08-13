"""Crash-bounded live core for the frozen C5 whole-update screening.

The module deliberately owns no credentials or service construction.  It
accepts a small runtime boundary, records an intent before every complete
``Graphiti.add_episode`` call, and advances resumability only at a completed
block boundary.  QA is a redacted supplemental view and never changes C5's
three legal headline interpretations.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

import native_characterization_c5 as c5
from graphiti_native import graphiti_episode_kwargs
from neo4j.exceptions import ConstraintError


FROZEN_HISTORY_ID = "07741c45"
FROZEN_EPISODE_COUNT = 49
FROZEN_CONCURRENCY_GRID = (1, 2, 4, 8)
FROZEN_E4_NAMESPACES = (
    "nc-e4-1434fcb947df5c3d",
    "nc-e4-b352061ffa0d4b21",
    "nc-e4-c15538d1fe2801cb",
    "nc-e4-2a427029b1a8b2ac",
)

INFRASTRUCTURE_FAILURE = "infrastructure_failure"
DIRECT_INVARIANT_FAILURE = "direct_invariant_failure"
INCOMPLETE_NON_MERGEABLE = "incomplete_invalid_non_mergeable"


class C5LiveCoreError(RuntimeError):
    """A sanitized C5 live-boundary contract error."""


@dataclass(frozen=True)
class C5Block:
    block_index: int
    concurrency: int
    graph_namespace: str


@dataclass(frozen=True)
class NamespaceCounts:
    nodes: int
    relationships: int

    @property
    def is_empty(self) -> bool:
        return self.nodes == 0 and self.relationships == 0


@dataclass(frozen=True)
class LiveFailureClassification:
    failure_kind: str
    scientific_interpretation: str | None


@dataclass(frozen=True)
class C5SerialReference:
    canonical_graph_sha256: str
    retrieved_episode_ids: tuple[str, ...]


@dataclass(frozen=True)
class C5ResumePrefix:
    completed_block_indices: tuple[int, ...] = ()
    partial_block_index: int | None = None
    serial_reference: C5SerialReference | None = None
    completed_block_results: tuple[Mapping[str, object], ...] = ()


class MonotonicCounter:
    """Deterministic strictly increasing timestamp source for tests/dry-runs."""

    def __init__(self, start: int = 0) -> None:
        self._value = start

    def __call__(self) -> int:
        value = self._value
        self._value += 1
        return value


class BlockRuntime(Protocol):
    async def namespace_counts(self) -> NamespaceCounts: ...

    async def clear_namespace(self) -> None: ...

    async def add_episode(self, episode: c5.Episode) -> Mapping[str, object] | None: ...

    async def export_canonical_graph(self) -> Mapping[str, object]: ...

    async def evaluate_retrieval(
        self, reference_episode_ids: list[str] | None
    ) -> Mapping[str, object]: ...

    async def close(self) -> None: ...


def _fail(reason: str) -> C5LiveCoreError:
    return C5LiveCoreError(reason)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise _fail("value_not_canonical_json") from None


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _qualified_error_class(error: BaseException) -> str:
    error_type = type(error)
    return f"{error_type.__module__}.{error_type.__qualname__}"


def _validate_sha256_list(values: Sequence[str]) -> list[str]:
    hashes = list(values)
    if len(hashes) != FROZEN_EPISODE_COUNT:
        raise _fail("episode_source_hash_count_not_frozen")
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in hashes
    ):
        raise _fail("episode_source_hash_invalid")
    return hashes


def load_frozen_e4_schedule(
    freeze: Mapping[str, object],
    *,
    run_id: str,
    episode_source_hashes: Sequence[str],
) -> dict[str, object]:
    """Load only the exact E4 history/grid/namespaces from the 64K freeze.

    Episode hashes are supplied by the independently loaded dataset boundary;
    the freeze is used to verify the frozen count and contiguous source range.
    A later live adapter can therefore compare those supplied hashes with its
    own episode payloads without this pure loader reading dataset files.
    """

    if not isinstance(freeze, Mapping):
        raise _fail("freeze_not_object")
    if freeze.get("artifact_id") != "native-characterization-freeze-reference-aligned-64k":
        raise _fail("freeze_identity_invalid")
    screening = freeze.get("screening")
    dataset = freeze.get("dataset")
    if not isinstance(screening, Mapping) or not isinstance(dataset, Mapping):
        raise _fail("freeze_sections_missing")
    e4 = screening.get("e4")
    histories = dataset.get("calibration_histories")
    if not isinstance(e4, Mapping) or not isinstance(histories, list):
        raise _fail("freeze_e4_missing")
    if e4.get("history_id") != FROZEN_HISTORY_ID:
        raise _fail("freeze_e4_history_invalid")
    if tuple(e4.get("concurrency_order", ())) != FROZEN_CONCURRENCY_GRID:
        raise _fail("freeze_e4_grid_invalid")
    raw_blocks = e4.get("block_order")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != 4:
        raise _fail("freeze_e4_blocks_invalid")
    expected_blocks = [
        {
            "block_index": index,
            "concurrency": concurrency,
            "graph_namespace": namespace,
        }
        for index, (concurrency, namespace) in enumerate(
            zip(FROZEN_CONCURRENCY_GRID, FROZEN_E4_NAMESPACES)
        )
    ]
    if raw_blocks != expected_blocks:
        raise _fail("freeze_e4_blocks_not_exact")

    history = next(
        (
            item
            for item in histories
            if isinstance(item, Mapping) and item.get("history_id") == FROZEN_HISTORY_ID
        ),
        None,
    )
    if not isinstance(history, Mapping) or history.get("episode_count") != FROZEN_EPISODE_COUNT:
        raise _fail("freeze_history_episode_count_invalid")
    frozen_episodes = history.get("episodes")
    if not isinstance(frozen_episodes, list) or [
        item.get("source_sequence") if isinstance(item, Mapping) else None
        for item in frozen_episodes
    ] != list(range(FROZEN_EPISODE_COUNT)):
        raise _fail("freeze_history_source_order_invalid")

    hashes = _validate_sha256_list(episode_source_hashes)
    schedule = c5.build_c5_schedule(
        history_id=FROZEN_HISTORY_ID,
        episode_source_hashes=hashes,
        interarrival_ns=0,
        run_id=run_id,
    )
    schedule["block_schedules"] = [
        {
            "block_index": item["block_index"],
            "concurrency": item["concurrency"],
            "screening_pass_index": 0,
            "history_id": FROZEN_HISTORY_ID,
            "episode_count": FROZEN_EPISODE_COUNT,
            "graph_namespace": item["graph_namespace"],
            "method": c5.NATIVE_WHOLE_UPDATE_PARALLEL,
            "treatment": c5.NATIVE_WHOLE_UPDATE_PARALLEL,
            "absolute_arrival_offsets_ns": [0] * FROZEN_EPISODE_COUNT,
        }
        for item in expected_blocks
    ]
    schedule["payload_sha256"] = c5.payload_sha256(schedule)
    return schedule


def classify_live_failure(error: BaseException) -> LiveFailureClassification:
    """Keep transport/service failures out of the scientific headline."""

    if isinstance(error, (c5.TransactionFailure, ConstraintError)):
        return LiveFailureClassification(
            DIRECT_INVARIANT_FAILURE,
            c5.DIRECT_INVARIANT_VIOLATION_OBSERVED,
        )
    return LiveFailureClassification(INFRASTRUCTURE_FAILURE, None)


def build_supplemental_qa_view(result: Mapping[str, object]) -> dict[str, object]:
    """Project only the qualified Judge's non-sensitive evidence fields."""

    status = str(result.get("status", "UNKNOWN"))
    correct = result.get("correct")
    allowed = (
        "qa_surface",
        "question_id_sha256",
        "retrieval_payload_sha256",
        "retrieved_facts_sha256",
        "retrieved_fact_count",
        "prompt_sha256",
        "judge_model",
        "judge_config_sha256",
        "reader_generation_performed",
        "retry_count",
        "error_class",
    )
    view = {key: result[key] for key in allowed if key in result}
    view.update(
        {
            "status": status,
            "accuracy": (
                float(bool(correct)) if isinstance(correct, bool) else None
            ),
            "headline_interpretation_effect": "none",
        }
    )
    return view


def _normalize_graph_namespace(value: object) -> object:
    """Remove the intentionally different per-block namespace from parity."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "<c5-block-namespace>"
                if str(key) == "group_id"
                else _normalize_graph_namespace(child)
            )
            for key, child in value.items()
            if str(key) != "canonical_graph_hash"
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_graph_namespace(child) for child in value]
    return value


def build_serial_reference(
    canonical_graph: Mapping[str, object], retrieval: Mapping[str, object]
) -> C5SerialReference:
    """Reduce C=1 output to the only evidence required by later blocks."""

    return C5SerialReference(
        canonical_graph_sha256=_sha256(_normalize_graph_namespace(canonical_graph)),
        retrieved_episode_ids=_retrieved_ids(retrieval),
    )


def serial_reference_artifact(reference: C5SerialReference) -> dict[str, object]:
    if (
        not isinstance(reference.canonical_graph_sha256, str)
        or len(reference.canonical_graph_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in reference.canonical_graph_sha256
        )
        or any(not isinstance(item, str) or not item for item in reference.retrieved_episode_ids)
        or len(set(reference.retrieved_episode_ids)) != len(reference.retrieved_episode_ids)
    ):
        raise _fail("serial_reference_invalid")
    return {
        "canonical_graph_sha256": reference.canonical_graph_sha256,
        "retrieved_episode_ids": list(reference.retrieved_episode_ids),
        "retrieved_episode_ids_sha256": _sha256(list(reference.retrieved_episode_ids)),
    }


def serial_reference_from_artifact(value: Mapping[str, object]) -> C5SerialReference:
    if not isinstance(value, Mapping):
        raise _fail("serial_reference_invalid")
    graph_hash = value.get("canonical_graph_sha256")
    ids = value.get("retrieved_episode_ids")
    ids_hash = value.get("retrieved_episode_ids_sha256")
    if (
        not isinstance(graph_hash, str)
        or not isinstance(ids, list)
        or any(not isinstance(item, str) or not item for item in ids)
        or len(set(ids)) != len(ids)
        or ids_hash != _sha256(ids)
    ):
        raise _fail("serial_reference_invalid")
    reference = C5SerialReference(graph_hash, tuple(ids))
    if serial_reference_artifact(reference) != dict(value):
        raise _fail("serial_reference_invalid")
    return reference


def _graph_parity(
    reference_hash: str, candidate: Mapping[str, object]
) -> dict[str, object]:
    candidate_hash = _sha256(_normalize_graph_namespace(candidate))
    return {
        "status": "pass" if reference_hash == candidate_hash else "mismatch",
        "reference_sha256": reference_hash,
        "candidate_sha256": candidate_hash,
    }


def _retrieved_ids(value: Mapping[str, object]) -> tuple[str, ...]:
    raw = value.get("retrieved_episode_ids")
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise _fail("retrieval_episode_ids_invalid")
    return tuple(raw)


def _retrieval_parity(
    reference_ids: tuple[str, ...], candidate: Mapping[str, object]
) -> dict[str, object]:
    candidate_ids = _retrieved_ids(candidate)
    return {
        "status": "pass" if candidate_ids == reference_ids else "mismatch",
        "reference_sha256": _sha256(list(reference_ids)),
        "candidate_sha256": _sha256(list(candidate_ids)),
    }


def _retrieval_metrics(value: Mapping[str, object]) -> dict[str, object]:
    raw = value.get("metrics")
    if not isinstance(raw, Mapping):
        raise _fail("retrieval_metrics_missing")
    required = ("evidence_recall_at_5", "evidence_recall_at_10")
    for name in required:
        metric = raw.get(name)
        if (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not 0.0 <= float(metric) <= 1.0
        ):
            raise _fail("retrieval_metrics_invalid")
    overlap = raw.get("episode_set_overlap_with_m0", 1.0)
    rank_overlap = raw.get("rank_biased_overlap_with_m0", 1.0)
    for metric in (overlap, rank_overlap):
        if (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not 0.0 <= float(metric) <= 1.0
        ):
            raise _fail("retrieval_metrics_invalid")
    return {
        "reference_surface": "c1",
        "evidence_recall_at_5": float(raw["evidence_recall_at_5"]),
        "evidence_recall_at_10": float(raw["evidence_recall_at_10"]),
        "top_10_set_overlap_vs_c1": float(overlap),
        "rank_biased_overlap_vs_c1": float(rank_overlap),
        "retrieved_episode_ids_sha256": _sha256(list(_retrieved_ids(value))),
    }


def _validate_schedule_and_inputs(
    schedule: Mapping[str, object],
    episodes: Sequence[c5.Episode],
    episode_source_hashes: Sequence[str],
) -> list[C5Block]:
    hashes = _validate_sha256_list(episode_source_hashes)
    if schedule.get("history_id") != FROZEN_HISTORY_ID:
        raise _fail("schedule_history_not_frozen")
    if schedule.get("episode_source_hashes") != hashes:
        raise _fail("schedule_episode_hash_mismatch")
    if schedule.get("episode_ids") != [
        f"{FROZEN_HISTORY_ID}:{index}" for index in range(FROZEN_EPISODE_COUNT)
    ]:
        raise _fail("schedule_episode_ids_not_frozen")
    if tuple(schedule.get("concurrency_grid", ())) != FROZEN_CONCURRENCY_GRID:
        raise _fail("schedule_grid_not_frozen")
    if schedule.get("payload_sha256") != c5.payload_sha256(schedule):
        raise _fail("schedule_payload_mismatch")
    raw_blocks = schedule.get("block_schedules")
    if not isinstance(raw_blocks, list) or len(raw_blocks) != 4:
        raise _fail("schedule_blocks_invalid")
    blocks: list[C5Block] = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, Mapping):
            raise _fail("schedule_block_invalid")
        block = C5Block(
            block_index=index,
            concurrency=FROZEN_CONCURRENCY_GRID[index],
            graph_namespace=FROZEN_E4_NAMESPACES[index],
        )
        if (
            raw.get("block_index") != block.block_index
            or raw.get("concurrency") != block.concurrency
            or raw.get("graph_namespace") != block.graph_namespace
            or raw.get("absolute_arrival_offsets_ns") != [0] * FROZEN_EPISODE_COUNT
        ):
            raise _fail("schedule_block_not_frozen")
        blocks.append(block)
    if len(episodes) != FROZEN_EPISODE_COUNT or [
        item.source_sequence for item in episodes
    ] != list(range(FROZEN_EPISODE_COUNT)):
        raise _fail("episodes_not_frozen_source_order")
    for index, (episode, expected_hash) in enumerate(zip(episodes, hashes)):
        payload = episode.payload
        if getattr(payload, "question_id", None) != FROZEN_HISTORY_ID:
            raise _fail("episode_history_mismatch")
        if getattr(payload, "source_sequence", None) != index:
            raise _fail("episode_payload_sequence_mismatch")
        if getattr(payload, "source_hash", None) != expected_hash:
            raise _fail("episode_payload_hash_mismatch")
    return blocks


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _write_root(
    store: Any,
    *,
    status: str,
    completed: Sequence[int],
    partial_block_index: int | None,
) -> None:
    await store.write_root_checkpoint(
        status=status,
        completed_block_indices=list(completed),
        partial_block_index=partial_block_index,
    )


async def _durable_block_failure(
    *,
    store: Any,
    block: C5Block,
    completed: Sequence[int],
    now_ns: Callable[[], int],
    error: BaseException,
    failure_stage: str,
    source_sequence: int | None,
) -> dict[str, object]:
    """Persist one sanitized terminal failure and close the resumable prefix."""

    classification = classify_live_failure(error)
    failure_event = await store.append_failure_event(
        {
            "event_type": "failure",
            "block_index": block.block_index,
            "concurrency": block.concurrency,
            "graph_namespace": block.graph_namespace,
            "source_sequence": source_sequence,
            "failure_timestamp_ns": now_ns(),
            "error_class": _qualified_error_class(error),
            "failure_stage": failure_stage,
            "failure_kind": classification.failure_kind,
            "scientific_interpretation": classification.scientific_interpretation,
        }
    )
    await _write_root(
        store,
        status=INCOMPLETE_NON_MERGEABLE,
        completed=completed,
        partial_block_index=block.block_index,
    )
    observation: Mapping[str, object] | None = None
    if classification.failure_kind == DIRECT_INVARIANT_FAILURE:
        finalize_direct = getattr(store, "finalize_direct_observation", None)
        if callable(finalize_direct):
            observation = await _maybe_await(
                finalize_direct(
                    failure_event=failure_event,
                    completed_block_indices=list(completed),
                )
            )
    return {
        "status": (
            "direct_invariant_observed"
            if classification.failure_kind == DIRECT_INVARIANT_FAILURE
            else INCOMPLETE_NON_MERGEABLE
        ),
        "failure_kind": classification.failure_kind,
        "scientific_interpretation": classification.scientific_interpretation,
        "failure_stage": failure_stage,
        "failed_block_index": block.block_index,
        "failed_source_sequence": source_sequence,
        "completed_block_indices": list(completed),
        "direct_observation": deepcopy(dict(observation)) if observation else None,
    }


async def _run_episode_workers(
    *,
    runtime: BlockRuntime,
    block: C5Block,
    episodes: Sequence[c5.Episode],
    hashes: Sequence[str],
    store: Any,
    now_ns: Callable[[], int],
) -> tuple[list[dict[str, object]], BaseException | None, int | None]:
    queue: asyncio.Queue[c5.Episode] = asyncio.Queue()
    for episode in episodes:
        queue.put_nowait(episode)
    publications: list[dict[str, object]] = []
    first_failure: tuple[BaseException, int] | None = None
    stop = asyncio.Event()
    # Frozen interarrival=0 means the whole block is offered at once.  Worker
    # availability controls service_start, never the scientific arrival time.
    block_arrival_ns = now_ns()

    async def worker(worker_id: int) -> None:
        nonlocal first_failure
        while not stop.is_set():
            try:
                episode = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            source = episode.source_sequence
            arrival = block_arrival_ns
            intent = await store.append_intent_event(
                {
                    "event_type": "intent",
                    "block_index": block.block_index,
                    "concurrency": block.concurrency,
                    "graph_namespace": block.graph_namespace,
                    "source_sequence": source,
                    "episode_source_sha256": hashes[source],
                    "arrival_timestamp_ns": arrival,
                    "worker_id": worker_id,
                }
            )
            service_start = now_ns()
            try:
                returned = await runtime.add_episode(episode)
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                if first_failure is None:
                    first_failure = (error, source)
                    stop.set()
                return
            publish = now_ns()
            work_counts: Mapping[str, object] = {}
            if isinstance(returned, Mapping):
                candidate = returned.get("work_counts", {})
                if isinstance(candidate, Mapping):
                    work_counts = candidate
            publication = await store.append_publication_event(
                {
                    "event_type": "publication",
                    "block_index": block.block_index,
                    "concurrency": block.concurrency,
                    "graph_namespace": block.graph_namespace,
                    "source_sequence": source,
                    "episode_source_sha256": hashes[source],
                    "arrival_timestamp_ns": arrival,
                    "service_start_timestamp_ns": service_start,
                    "publish_timestamp_ns": publish,
                    "caller_return_timestamp_ns": publish,
                    "worker_id": worker_id,
                    "transaction_status": "committed",
                    "work_counts": dict(work_counts),
                }
            )
            publications.append(publication)
            await store.write_episode_checkpoint(
                status="published",
                block_index=block.block_index,
                source_sequence=source,
                publication_event_sequence=publication["event_sequence"],
                publication_payload_sha256=publication["payload_sha256"],
                intent_event_sequence=intent["event_sequence"],
                intent_payload_sha256=intent["payload_sha256"],
            )

    workers = [asyncio.create_task(worker(worker_id)) for worker_id in range(block.concurrency)]
    await asyncio.gather(*workers)
    if first_failure is None:
        return publications, None, None
    return publications, first_failure[0], first_failure[1]


async def run_c5_live_core(
    *,
    schedule: Mapping[str, object],
    episodes: Sequence[c5.Episode],
    episode_source_hashes: Sequence[str],
    runtime_factory: Callable[[C5Block], BlockRuntime | Awaitable[BlockRuntime]],
    store: Any,
    now_ns: Callable[[], int],
    provenance_hashes: Mapping[str, str] | None = None,
    resume_prefix: C5ResumePrefix | None = None,
    qa_evaluator: Callable[[BlockRuntime, C5Block], Mapping[str, object] | Awaitable[Mapping[str, object]]] | None = None,
) -> dict[str, object]:
    """Execute the exact four-block C5 screening with block-boundary resume."""

    if provenance_hashes is not None and (
        not isinstance(provenance_hashes, Mapping)
        or not provenance_hashes
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for key, value in provenance_hashes.items()
        )
    ):
        raise _fail("provenance_hashes_invalid")

    blocks = _validate_schedule_and_inputs(schedule, episodes, episode_source_hashes)
    hashes = list(episode_source_hashes)
    prefix = resume_prefix or C5ResumePrefix()
    completed = list(prefix.completed_block_indices)
    if completed not in ([*range(len(completed))],):
        raise _fail("resume_completed_blocks_not_prefix")
    if any(index not in range(4) for index in completed):
        raise _fail("resume_completed_block_invalid")
    if prefix.partial_block_index is not None and prefix.partial_block_index != len(completed):
        raise _fail("resume_partial_block_not_next")
    if completed and 0 in completed and prefix.serial_reference is None:
        raise _fail("resume_serial_reference_missing")
    if len(prefix.completed_block_results) != len(completed):
        raise _fail("resume_completed_block_results_missing")
    for index, result in enumerate(prefix.completed_block_results):
        if (
            not isinstance(result, Mapping)
            or result.get("block_index") != index
            or result.get("graph_namespace") != FROZEN_E4_NAMESPACES[index]
            or result.get("payload_sha256") != c5.payload_sha256(result)
        ):
            raise _fail("resume_completed_block_result_invalid")
    serial_reference = prefix.serial_reference
    block_results: list[dict[str, object]] = [
        deepcopy(dict(item)) for item in prefix.completed_block_results
    ]

    for block in blocks[len(completed) :]:
        try:
            runtime = await _maybe_await(runtime_factory(block))
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            return await _durable_block_failure(
                store=store,
                block=block,
                completed=completed,
                now_ns=now_ns,
                error=error,
                failure_stage="runtime_init",
                source_sequence=None,
            )
        terminal: dict[str, object] | None = None
        try:
            try:
                counts = await runtime.namespace_counts()
                if not isinstance(counts, NamespaceCounts):
                    raise _fail("namespace_counts_invalid")
                is_partial = prefix.partial_block_index == block.block_index
                if is_partial:
                    if not counts.is_empty:
                        await runtime.clear_namespace()
                    cleared = await runtime.namespace_counts()
                    if (
                        not isinstance(cleared, NamespaceCounts)
                        or not cleared.is_empty
                    ):
                        raise _fail("partial_namespace_clear_failed")
                elif not counts.is_empty:
                    raise _fail("fresh_namespace_not_empty")
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                terminal = await _durable_block_failure(
                    store=store,
                    block=block,
                    completed=completed,
                    now_ns=now_ns,
                    error=error,
                    failure_stage="namespace_check",
                    source_sequence=None,
                )
                return terminal

            publications, error, failed_source = await _run_episode_workers(
                runtime=runtime,
                block=block,
                episodes=episodes,
                hashes=hashes,
                store=store,
                now_ns=now_ns,
            )
            if error is not None:
                terminal = await _durable_block_failure(
                    store=store,
                    block=block,
                    completed=completed,
                    now_ns=now_ns,
                    error=error,
                    failure_stage="add_episode",
                    source_sequence=failed_source,
                )
                return terminal

            try:
                canonical_graph = dict(await runtime.export_canonical_graph())
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                terminal = await _durable_block_failure(
                    store=store,
                    block=block,
                    completed=completed,
                    now_ns=now_ns,
                    error=error,
                    failure_stage="export",
                    source_sequence=None,
                )
                return terminal
            reference_ids = (
                None
                if serial_reference is None
                else list(serial_reference.retrieved_episode_ids)
            )
            try:
                retrieval_result = dict(
                    await runtime.evaluate_retrieval(reference_ids)
                )
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                terminal = await _durable_block_failure(
                    store=store,
                    block=block,
                    completed=completed,
                    now_ns=now_ns,
                    error=error,
                    failure_stage="retrieval",
                    source_sequence=None,
                )
                return terminal
            if serial_reference is None:
                serial_reference = build_serial_reference(
                    canonical_graph, retrieval_result
                )
            canonical_parity = _graph_parity(
                serial_reference.canonical_graph_sha256, canonical_graph
            )
            retrieval_parity = _retrieval_parity(
                serial_reference.retrieved_episode_ids, retrieval_result
            )
            try:
                qa_result = (
                    dict(await _maybe_await(qa_evaluator(runtime, block)))
                    if qa_evaluator is not None
                    else {"status": "NOT_RUN"}
                )
                if qa_result.get("status") == "SERVICE_ERROR":
                    raise C5LiveCoreError("supplemental_qa_service_error")
                qa_view = build_supplemental_qa_view(qa_result)
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                terminal = await _durable_block_failure(
                    store=store,
                    block=block,
                    completed=completed,
                    now_ns=now_ns,
                    error=error,
                    failure_stage="judge",
                    source_sequence=None,
                )
                return terminal
            result = c5.analyze_c5_block(
                concurrency=block.concurrency,
                expected_episode_ids=schedule["episode_ids"],
                publication_records=publications,
                canonical_graph_parity=canonical_parity,
                retrieval_parity=retrieval_parity,
                execution_path_evidence={
                    "treatment_is_concurrency_only": True,
                    "live_graph_outputs_fixed": False,
                    "live_graph_outputs_replay_fixed": False,
                    "complete_add_episode_units": True,
                    "work_conserving_dispatch": True,
                },
            )
            result.pop("payload_sha256", None)
            result["block_index"] = block.block_index
            result["graph_namespace"] = block.graph_namespace
            result["retrieval_metrics"] = _retrieval_metrics(retrieval_result)
            result["supplemental_qa"] = qa_view
            if block.block_index == 0:
                result["serial_reference"] = serial_reference_artifact(
                    serial_reference
                )
            result = c5.seal_payload(result)
            block_results.append(result)
        finally:
            try:
                await runtime.close()
            except BaseException as error:
                if isinstance(error, asyncio.CancelledError):
                    raise
                if terminal is None:
                    terminal = await _durable_block_failure(
                        store=store,
                        block=block,
                        completed=completed,
                        now_ns=now_ns,
                        error=error,
                        failure_stage="close",
                        source_sequence=None,
                    )
        if terminal is not None:
            return terminal
        await store.write_block_checkpoint(
            status="completed",
            block_index=block.block_index,
            concurrency=block.concurrency,
            graph_namespace=block.graph_namespace,
            block_result=result,
        )
        completed.append(block.block_index)
        await _write_root(
            store,
            status="running" if len(completed) < 4 else "complete",
            completed=completed,
            partial_block_index=None,
        )

    await store.finalize_success(block_results)
    return {
        "status": "complete",
        "completed_block_indices": completed,
        "block_results": block_results,
        "serial_reference": serial_reference,
    }


class GraphitiBlockRuntime:
    """Production-shaped adapter for one isolated Graphiti C5 namespace."""

    _COUNT_QUERY = """
    MATCH (n) WHERE n.group_id = $group_id
    WITH count(n) AS nodes
    OPTIONAL MATCH ()-[r]->() WHERE r.group_id = $group_id
    RETURN nodes, count(r) AS relationships
    """
    _CLEAR_QUERY = "MATCH (n) WHERE n.group_id = $group_id DETACH DELETE n"

    def __init__(
        self,
        *,
        graphiti: Any,
        block: C5Block,
        episodes: Sequence[Any],
        instance: Mapping[str, object],
        graph_exporter: Callable[..., Awaitable[Mapping[str, object]]],
        retrieval_evaluator: Callable[..., Awaitable[Mapping[str, object]]],
    ) -> None:
        self.graphiti = graphiti
        self.block = block
        self.episodes = [replace(item, group_id=block.graph_namespace) for item in episodes]
        self.instance = dict(instance)
        self.graph_exporter = graph_exporter
        self.retrieval_evaluator = retrieval_evaluator
        self._cached_retrieval: dict[str, object] | None = None

    async def namespace_counts(self) -> NamespaceCounts:
        result = await self.graphiti.driver.execute_query(
            self._COUNT_QUERY, params={"group_id": self.block.graph_namespace}
        )
        rows = getattr(result, "records", result[0] if isinstance(result, tuple) else result)
        row = list(rows)[0]
        if hasattr(row, "data"):
            row = row.data()
        return NamespaceCounts(int(row["nodes"]), int(row["relationships"]))

    async def clear_namespace(self) -> None:
        await self.graphiti.driver.execute_query(
            self._CLEAR_QUERY, params={"group_id": self.block.graph_namespace}
        )

    async def add_episode(self, episode: c5.Episode) -> dict[str, object]:
        payload = episode.payload
        runtime_episode = replace(payload, group_id=self.block.graph_namespace)
        await self.graphiti.add_episode(**graphiti_episode_kwargs(runtime_episode))
        return {"work_counts": {"add_episode_calls": 1}}

    async def export_canonical_graph(self) -> dict[str, object]:
        return dict(
            await self.graph_exporter(
                self.graphiti, self.episodes, self.block.graph_namespace
            )
        )

    async def evaluate_retrieval(
        self, reference_episode_ids: list[str] | None
    ) -> dict[str, object]:
        result = dict(
            await self.retrieval_evaluator(
                self.graphiti,
                self.instance,
                self.episodes,
                reference_episode_ids,
                10,
            )
        )
        self._cached_retrieval = deepcopy(result)
        return result

    def cached_retrieval_result(self) -> dict[str, object]:
        """Return the already-executed top-10 retrieval for supplemental QA."""

        if self._cached_retrieval is None:
            raise _fail("retrieval_not_evaluated")
        return deepcopy(self._cached_retrieval)

    async def close(self) -> None:
        close = getattr(self.graphiti, "close", None)
        if callable(close):
            await _maybe_await(close())


__all__ = [
    "C5Block",
    "C5ResumePrefix",
    "C5SerialReference",
    "DIRECT_INVARIANT_FAILURE",
    "FROZEN_EPISODE_COUNT",
    "GraphitiBlockRuntime",
    "INFRASTRUCTURE_FAILURE",
    "INCOMPLETE_NON_MERGEABLE",
    "LiveFailureClassification",
    "MonotonicCounter",
    "NamespaceCounts",
    "build_supplemental_qa_view",
    "build_serial_reference",
    "classify_live_failure",
    "load_frozen_e4_schedule",
    "run_c5_live_core",
    "serial_reference_artifact",
    "serial_reference_from_artifact",
]
