"""Offline C5/E4 contracts for naive whole-update parallel screening.

C5 is intentionally a bounded characterization step, not a mechanism
prototype.  This module keeps the first implementation pure and offline: it
builds the frozen four-block schedule, analyzes already-sanitized publication
evidence, classifies the result into the three legal workplan interpretations,
and persists one checkpoint per concurrency block.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


SCHEDULE_SCHEMA = "membind.native-characterization-c5-schedule.v1"
MANIFEST_SCHEMA = "membind.native-characterization-c5-manifest.v1"
CHECKPOINT_SCHEMA = "membind.native-characterization-c5-block-checkpoint.v1"
RESULT_SCHEMA = "membind.native-characterization-e4-whole-parallel.v1"
VERIFICATION_SCHEMA = "membind.native-characterization-c5-verification.v1"

CONCURRENCY_GRID = (1, 2, 4, 8)
NATIVE_WHOLE_UPDATE_PARALLEL = "Native-WholeUpdate-Parallel"
DIRECT_INVARIANT_VIOLATION = "DIRECT_INVARIANT_VIOLATION_OBSERVED"
OUTCOME_INSTABILITY_OR_CONFOUNDED = "OUTCOME_INSTABILITY_OR_CONFOUNDED"
NO_INSUFFICIENCY_OBSERVED = "NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED"
DIRECT_INVARIANT_VIOLATION_OBSERVED = DIRECT_INVARIANT_VIOLATION
NO_NAIVE_PARALLEL_INSUFFICIENCY_OBSERVED = NO_INSUFFICIENCY_OBSERVED
LEGAL_INTERPRETATIONS = (
    DIRECT_INVARIANT_VIOLATION,
    OUTCOME_INSTABILITY_OR_CONFOUNDED,
    NO_INSUFFICIENCY_OBSERVED,
)

_RUN_ID_RE = re.compile(r"^c5-[0-9a-f]{16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,256}$")
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "content",
        "cypher",
        "error_message",
        "exception",
        "messages",
        "password",
        "prompt",
        "raw_response",
        "request",
        "response",
        "secret",
    }
)


class NativeCharacterizationC5Error(RuntimeError):
    """Fail-closed C5 contract error with sanitized messages only."""


class TransactionFailure(RuntimeError):
    """Stable marker for a whole-update transaction failure."""


@dataclass(frozen=True)
class Episode:
    """One source-ordered input; the payload is opaque to the scheduler."""

    source_sequence: int
    payload: Any


class Clock(Protocol):
    def now_ns(self) -> int: ...

    def sleep_until_ns(self, timestamp_ns: int) -> None: ...


class DurableWriter(Protocol):
    def persist_publication(self, record: dict[str, object]) -> None: ...

    def persist_failure(self, checkpoint: dict[str, object]) -> None: ...


WholeUpdateService = Callable[[Episode, int], int]


def _fail(reason: str) -> None:
    raise NativeCharacterizationC5Error(reason)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode canonical ASCII JSON without a trailing newline."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail("payload_not_canonical_json")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_sha256(value: Mapping[str, Any]) -> str:
    """Return the canonical payload seal, ignoring an existing seal field."""

    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    return _sha256(canonical_json_bytes(candidate))


def seal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sanitized, idempotently sealed copy of one JSON object."""

    candidate = deepcopy(dict(value))
    candidate.pop("payload_sha256", None)
    _assert_sanitized(candidate)
    candidate["payload_sha256"] = _sha256(canonical_json_bytes(candidate))
    return candidate


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS:
                _fail("artifact_not_sanitized")
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json_bytes(value) + b"\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("artifact_unreadable")
    if not isinstance(value, dict):
        _fail("artifact_not_object")
    if raw != canonical_json_bytes(value) + b"\n":
        _fail("artifact_not_canonical")
    if value.get("payload_sha256") != payload_sha256(value):
        _fail("artifact_payload_mismatch")
    return value


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
        _fail("run_id_invalid")


def _validate_episode_ids(episode_ids: Sequence[str]) -> list[str]:
    if not episode_ids:
        _fail("episode_ids_empty")
    result: list[str] = []
    for value in episode_ids:
        if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
            _fail("episode_id_invalid")
        result.append(value)
    if len(set(result)) != len(result):
        _fail("episode_ids_not_unique")
    return result


def build_c5_schedule(
    *,
    history_id: str,
    episode_source_hashes: Sequence[str] | None = None,
    interarrival_ns: int = 0,
    run_id: str | None = None,
    episode_ids: Sequence[str] | None = None,
    namespace_prefix: str = "nc-e4",
) -> dict[str, Any]:
    """Build the frozen C5 grid: one history, one pass, C={1,2,4,8}."""

    if not isinstance(history_id, str) or _SAFE_ID_RE.fullmatch(history_id) is None:
        _fail("history_id_invalid")
    if not isinstance(namespace_prefix, str) or not namespace_prefix:
        _fail("namespace_prefix_invalid")
    if isinstance(interarrival_ns, bool) or not isinstance(interarrival_ns, int) or interarrival_ns < 0:
        _fail("interarrival_ns_invalid")
    if episode_ids is not None and episode_source_hashes is not None:
        _fail("episode_identity_ambiguous")
    if episode_source_hashes is not None:
        hashes = list(episode_source_hashes)
        if not hashes or any(
            not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None
            for value in hashes
        ):
            _fail("episode_source_hashes_invalid")
        selected_episode_ids = [f"{history_id}:{index}" for index in range(len(hashes))]
    elif episode_ids is not None:
        selected_episode_ids = _validate_episode_ids(episode_ids)
        hashes = [_sha256(value.encode("ascii")) for value in selected_episode_ids]
    else:
        _fail("episode_identity_missing")
    identity = {
        "history_id": history_id,
        "episode_source_hashes": hashes,
        "interarrival_ns": interarrival_ns,
    }
    if run_id is None:
        run_id = f"c5-{_sha256(canonical_json_bytes(identity))[:16]}"
    _validate_run_id(run_id)
    blocks = []
    for block_index, concurrency in enumerate(CONCURRENCY_GRID):
        blocks.append(
            {
                "block_index": block_index,
                "concurrency": concurrency,
                "screening_pass_index": 0,
                "history_id": history_id,
                "episode_count": len(selected_episode_ids),
                "graph_namespace": f"{namespace_prefix}-{run_id}-c{concurrency}",
                "method": NATIVE_WHOLE_UPDATE_PARALLEL,
                "treatment": NATIVE_WHOLE_UPDATE_PARALLEL,
                "absolute_arrival_offsets_ns": [
                    index * interarrival_ns for index in range(len(selected_episode_ids))
                ],
            }
        )
    return seal_payload(
        {
            "schema_version": SCHEDULE_SCHEMA,
            "status": "dry_run",
            "stage": "C5/E4_OFFLINE_SCHEDULE",
            "run_id": run_id,
            "history_id": history_id,
            "episode_ids": selected_episode_ids,
            "episode_source_hashes": hashes,
            "method": NATIVE_WHOLE_UPDATE_PARALLEL,
            "interarrival_ns": interarrival_ns,
            "concurrency_grid": list(CONCURRENCY_GRID),
            "screening_pass_count": 1,
            "block_schedules": blocks,
        }
    )


def _expected_source_sequences(expected_episode_ids: Sequence[str]) -> list[int]:
    expected = _validate_episode_ids(expected_episode_ids)
    result: list[int] = []
    for fallback, episode_id in enumerate(expected):
        suffix = episode_id.rsplit(":", 1)[-1]
        result.append(int(suffix) if suffix.isdigit() else fallback)
    if len(set(result)) != len(result):
        _fail("expected_source_sequences_not_unique")
    return result


def _int_field(record: Mapping[str, Any], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{field}_invalid")
    return value


def _sum_work_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for record in records:
        counts = record.get("work_counts", {})
        if not isinstance(counts, Mapping):
            _fail("work_counts_invalid")
        for key, value in counts.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
                _fail("work_counts_invalid")
            totals[key] += value
    return dict(sorted(totals.items()))


def _publication_order(records: Sequence[Mapping[str, Any]]) -> list[int]:
    def key(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int]:
        fallback_index, record = item
        event_sequence = record.get("event_sequence", fallback_index)
        if isinstance(event_sequence, bool) or not isinstance(event_sequence, int):
            _fail("event_sequence_invalid")
        return (event_sequence, fallback_index)

    return [
        _int_field(record, "source_sequence")
        for _, record in sorted(enumerate(records), key=key)
    ]


def _parity_confounds(label: str, evidence: Mapping[str, Any]) -> list[str]:
    status = evidence.get("status")
    if status == "pass":
        return []
    if status in {"mismatch", "fail"}:
        return [f"{label} trajectory divergence observed without direct semantic failure"]
    if status in {"unknown", "confounded", None}:
        return [f"{label} evidence is unavailable or confounded"]
    return [f"{label} evidence has unsupported status"]


def _validate_episodes_and_arrivals(
    episodes: Sequence[Episode],
    arrival_timestamps_ns: Sequence[int],
    concurrency: int,
    clock: Clock,
) -> list[int]:
    if concurrency not in CONCURRENCY_GRID:
        _fail("concurrency_not_in_frozen_grid")
    if not episodes:
        _fail("episodes_empty")
    if len(episodes) != len(arrival_timestamps_ns):
        _fail("arrival_count_mismatch")
    sequences = [episode.source_sequence for episode in episodes]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in sequences):
        _fail("source_sequence_invalid")
    if len(set(sequences)) != len(sequences):
        _fail("source_sequence_not_unique")
    if sequences != sorted(sequences):
        _fail("episodes_not_source_ordered")
    arrivals = []
    for value in arrival_timestamps_ns:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail("arrival_timestamp_invalid")
        arrivals.append(value)
    if any(current < previous for previous, current in zip(arrivals, arrivals[1:])):
        _fail("arrival_timestamps_not_monotonic")
    if arrivals[0] < clock.now_ns():
        _fail("first_arrival_precedes_clock")
    return arrivals


def _duration_ns(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("whole_update_duration_invalid")
    return value


def _aggregate_c5(records: Sequence[Mapping[str, Any]], episode_count: int) -> dict[str, Any]:
    if not records:
        return {
            "episode_count": episode_count,
            "published_episode_count": 0,
            "makespan_ns": 0,
            "throughput_episodes_per_second": None,
            "mean_visibility_lag_ns": 0.0,
            "max_visibility_lag_ns": 0,
            "work_counts": {},
        }
    arrivals = [_int_field(record, "arrival_timestamp_ns") for record in records]
    publishes = [_int_field(record, "publish_timestamp_ns") for record in records]
    lags = [publish - arrival for arrival, publish in zip(arrivals, publishes)]
    makespan_ns = max(publishes) - min(arrivals)
    return {
        "episode_count": episode_count,
        "published_episode_count": len(records),
        "makespan_ns": makespan_ns,
        "throughput_episodes_per_second": (
            len(records) * 1_000_000_000 / makespan_ns if makespan_ns > 0 else None
        ),
        "mean_visibility_lag_ns": sum(lags) / len(lags),
        "max_visibility_lag_ns": max(lags),
        "work_counts": _sum_work_counts(records),
    }


def _failure_checkpoint(
    *,
    failure_timestamp_ns: int,
    failed_episode: Episode,
    error: Exception,
    records: Sequence[Mapping[str, Any]],
    not_started: Sequence[Episode],
) -> dict[str, object]:
    transaction_error_count = 1 if isinstance(error, TransactionFailure) else 0
    return {
        "status": "failed",
        "method": NATIVE_WHOLE_UPDATE_PARALLEL,
        "failure_timestamp_ns": failure_timestamp_ns,
        "failed_source_sequence": failed_episode.source_sequence,
        "error_class": type(error).__name__,
        "service_error_count": 1,
        "transaction_error_count": transaction_error_count,
        "completed_source_sequences": [int(item["source_sequence"]) for item in records],
        "not_started_source_sequences": [episode.source_sequence for episode in not_started],
    }


def check_c5_invariants(
    episodes: Sequence[Episode],
    records: Sequence[Mapping[str, Any]],
    *,
    graph_parity: Mapping[str, Any] | None = None,
    retrieval_parity: Mapping[str, Any] | None = None,
    model_outputs_fixed: bool = True,
) -> dict[str, Any]:
    """Count C5 direct evidence separately from parity/oracle confounding."""

    expected = [episode.source_sequence for episode in episodes]
    if len(set(expected)) != len(expected):
        _fail("expected_source_sequence_not_unique")
    observed = [_int_field(record, "source_sequence") for record in records]
    counts = Counter(observed)
    expected_set = set(expected)
    lost = [source for source in expected if source not in counts]
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
    unexpected_count = sum(1 for source in observed if source not in expected_set)
    source_order_violation_count = (
        1
        if not lost and duplicate_count == 0 and unexpected_count == 0 and observed != expected
        else 0
    )
    temporal_violations = 0
    transaction_errors = 0
    service_errors = 0
    for record in records:
        arrival = _int_field(record, "arrival_timestamp_ns")
        service_start = _int_field(record, "service_start_timestamp_ns")
        publish = _int_field(record, "publish_timestamp_ns")
        caller_return = record.get("caller_return_timestamp_ns", publish)
        if isinstance(caller_return, bool) or not isinstance(caller_return, int):
            _fail("caller_return_timestamp_ns_invalid")
        if not arrival <= service_start <= publish or caller_return < arrival:
            temporal_violations += 1
        if record.get("transaction_error") is True or record.get("transaction_status") not in {None, "committed"}:
            transaction_errors += 1
        if record.get("service_error") is True:
            service_errors += 1

    graph = graph_parity or {}
    retrieval = retrieval_parity or {}
    graph_mismatch = graph.get("canonical_graph_sha256_match") is False
    retrieval_mismatch = retrieval.get("retrieval_result_sha256_match") is False
    oracle_miss_count = int(graph.get("oracle_miss_count", 0) or 0) + int(
        retrieval.get("oracle_miss_count", 0) or 0
    )
    direct_categories = [
        bool(lost),
        duplicate_count > 0,
        unexpected_count > 0,
        temporal_violations > 0,
        source_order_violation_count > 0,
        transaction_errors > 0,
        service_errors > 0,
    ]
    confounded_categories = [
        graph_mismatch,
        retrieval_mismatch,
        oracle_miss_count > 0 and not model_outputs_fixed,
    ]
    return {
        "requested_episode_count": len(expected),
        "published_episode_count": len(observed),
        "lost_episode_count": len(lost),
        "duplicate_episode_count": duplicate_count,
        "unexpected_episode_count": unexpected_count,
        "publication_loss_count": len(lost),
        "temporal_invariant_violation_count": temporal_violations,
        "source_order_violation_count": source_order_violation_count,
        "transaction_error_count": transaction_errors,
        "service_error_count": service_errors,
        "graph_parity_mismatch": graph_mismatch,
        "retrieval_parity_mismatch": retrieval_mismatch,
        "oracle_miss_count": oracle_miss_count,
        "model_outputs_fixed": model_outputs_fixed,
        "direct_invariant_violation_count": sum(1 for item in direct_categories if item),
        "confounded_evidence_count": sum(1 for item in confounded_categories if item),
    }


def interpret_c5_screening(block_results: Sequence[Mapping[str, Any]]) -> str:
    """Reduce one four-block C5 screening pass to a legal workplan label."""

    if not block_results:
        _fail("block_results_empty")
    saw_confounded = False
    for block in block_results:
        invariants = block.get("invariants")
        if not isinstance(invariants, Mapping):
            _fail("block_invariants_missing")
        if int(invariants.get("direct_invariant_violation_count", 0)) > 0:
            return DIRECT_INVARIANT_VIOLATION_OBSERVED
        if int(block.get("service_error_count", 0)) > 0:
            return DIRECT_INVARIANT_VIOLATION_OBSERVED
        if int(invariants.get("confounded_evidence_count", 0)) > 0:
            saw_confounded = True
    if saw_confounded:
        return OUTCOME_INSTABILITY_OR_CONFOUNDED
    return NO_INSUFFICIENCY_OBSERVED


def run_whole_update_parallel(
    episodes: Sequence[Episode],
    arrival_timestamps_ns: Sequence[int],
    *,
    concurrency: int,
    clock: Clock,
    whole_update_service: WholeUpdateService,
    durable_writer: DurableWriter,
) -> dict[str, Any]:
    """Run one deterministic C5 offline replay with complete add_episode units.

    Dispatch is source ordered.  Visibility is ordered by completion timestamp,
    so the fixture can expose the exact failure mode C5 is meant to screen:
    coarse whole-update parallelism may publish later source episodes first.
    """

    arrivals = _validate_episodes_and_arrivals(episodes, arrival_timestamps_ns, concurrency, clock)
    worker_available = [clock.now_ns()] * concurrency
    planned_records: list[dict[str, object]] = []

    for dispatch_index, (episode, arrival) in enumerate(zip(episodes, arrivals)):
        worker_id = min(range(concurrency), key=lambda index: (worker_available[index], index))
        service_start = max(worker_available[worker_id], arrival)
        clock.sleep_until_ns(service_start)
        try:
            duration_ns = _duration_ns(whole_update_service(episode, service_start))
        except Exception as error:
            eligible = [
                record
                for record in planned_records
                if int(record["publish_timestamp_ns"]) <= service_start
            ]
            records = sorted(
                eligible,
                key=lambda item: (int(item["publish_timestamp_ns"]), int(item["event_sequence"])),
            )
            for record in records:
                durable_writer.persist_publication(dict(record))
            checkpoint = _failure_checkpoint(
                failure_timestamp_ns=service_start,
                failed_episode=episode,
                error=error,
                records=records,
                not_started=episodes[dispatch_index + 1 :],
            )
            durable_writer.persist_failure(checkpoint)
            invariants = check_c5_invariants(episodes, records)
            aggregate = _aggregate_c5(records, len(episodes))
            return {
                "status": "failed",
                "method": NATIVE_WHOLE_UPDATE_PARALLEL,
                "concurrency": concurrency,
                "records": records,
                "aggregate": aggregate,
                "invariants": invariants,
                "service_error_count": 1,
                "failure_checkpoint": checkpoint,
                "interpretation": DIRECT_INVARIANT_VIOLATION_OBSERVED,
            }
        publish = service_start + duration_ns
        worker_available[worker_id] = publish
        planned_records.append(
            {
                "event_sequence": dispatch_index,
                "method": NATIVE_WHOLE_UPDATE_PARALLEL,
                "source_sequence": episode.source_sequence,
                "arrival_timestamp_ns": arrival,
                "service_start_timestamp_ns": service_start,
                "publish_timestamp_ns": publish,
                "caller_return_timestamp_ns": publish,
                "worker_id": worker_id,
                "transaction_status": "committed",
                "work_counts": {},
            }
        )

    records = sorted(
        planned_records,
        key=lambda item: (int(item["publish_timestamp_ns"]), int(item["event_sequence"])),
    )
    for record in records:
        durable_writer.persist_publication(dict(record))
    invariants = check_c5_invariants(episodes, records)
    aggregate = _aggregate_c5(records, len(episodes))
    interpretation = interpret_c5_screening(
        [{"invariants": invariants, "service_error_count": invariants["service_error_count"]}]
    )
    return {
        "status": "complete",
        "method": NATIVE_WHOLE_UPDATE_PARALLEL,
        "concurrency": concurrency,
        "records": records,
        "aggregate": aggregate,
        "invariants": invariants,
        "service_error_count": invariants["service_error_count"],
        "failure_checkpoint": None,
        "interpretation": interpretation,
    }


def analyze_c5_block(
    *,
    concurrency: int,
    expected_episode_ids: Sequence[str],
    publication_records: Sequence[Mapping[str, Any]],
    canonical_graph_parity: Mapping[str, Any],
    retrieval_parity: Mapping[str, Any],
    execution_path_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify one C5 block into the workplan's three legal outcomes."""

    if concurrency not in CONCURRENCY_GRID:
        _fail("concurrency_not_in_frozen_grid")
    if not publication_records:
        _fail("publication_records_empty")
    _assert_sanitized(publication_records)
    expected_sources = _expected_source_sequences(expected_episode_ids)
    observed_order = _publication_order(publication_records)
    observed_counts = Counter(observed_order)
    expected_set = set(expected_sources)
    direct_evidence: list[str] = []

    missing = [source for source in expected_sources if source not in observed_counts]
    duplicates = [source for source, count in sorted(observed_counts.items()) if count > 1]
    unexpected = [source for source in observed_order if source not in expected_set]
    if missing:
        direct_evidence.append(f"publication loss for source_sequence={missing}")
    if duplicates:
        direct_evidence.append(f"duplicate publication for source_sequence={duplicates}")
    if unexpected:
        direct_evidence.append(f"unexpected publication for source_sequence={unexpected}")
    if observed_order != expected_sources:
        direct_evidence.append("source-order invariant violation")

    arrivals: list[int] = []
    publishes: list[int] = []
    visibility_lags: list[int] = []
    service_errors = 0
    transaction_errors = 0
    temporal_violations = 0
    for record in publication_records:
        arrival = _int_field(record, "arrival_timestamp_ns")
        service_start = _int_field(record, "service_start_timestamp_ns")
        publish = _int_field(record, "publish_timestamp_ns")
        if not arrival <= service_start <= publish:
            direct_evidence.append("timestamp envelope invariant violation")
        arrivals.append(arrival)
        publishes.append(publish)
        visibility_lags.append(publish - arrival)
        if record.get("service_error") is True:
            service_errors += 1
        if record.get("transaction_error") is True:
            transaction_errors += 1
        if record.get("temporal_invariant_ok") is False:
            temporal_violations += 1
    if service_errors:
        direct_evidence.append(f"service error count={service_errors}")
    if transaction_errors:
        direct_evidence.append(f"transaction error count={transaction_errors}")
    if temporal_violations:
        direct_evidence.append(f"temporal invariant violation count={temporal_violations}")

    makespan_ns = max(publishes) - min(arrivals)
    throughput = (
        len(publication_records) * 1_000_000_000 / makespan_ns
        if makespan_ns > 0
        else None
    )
    metrics = {
        "concurrency": concurrency,
        "episode_count": len(expected_sources),
        "published_episode_count": len(publication_records),
        "lost_episode_count": len(missing),
        "duplicate_episode_count": sum(count - 1 for count in observed_counts.values() if count > 1),
        "unexpected_episode_count": len(unexpected),
        "service_error_count": service_errors,
        "transaction_error_count": transaction_errors,
        "temporal_invariant_violation_count": temporal_violations,
        "publication_loss_count": len(missing),
        "makespan_ns": makespan_ns,
        "service_throughput_eps_per_second": throughput,
        "mean_visibility_lag_ns": sum(visibility_lags) / len(visibility_lags),
        "max_visibility_lag_ns": max(visibility_lags),
        "work_counts": _sum_work_counts(publication_records),
    }

    confounded_evidence: list[str] = []
    if execution_path_evidence.get("treatment_is_concurrency_only") is not True:
        confounded_evidence.append("execution path is not isolated to concurrency")
    if execution_path_evidence.get("live_graph_outputs_fixed") is False:
        confounded_evidence.append("live graph outputs are not fixed")
    confounded_evidence.extend(_parity_confounds("canonical graph parity", canonical_graph_parity))
    confounded_evidence.extend(_parity_confounds("retrieval parity", retrieval_parity))

    if direct_evidence:
        interpretation = DIRECT_INVARIANT_VIOLATION
        bounded_claim = (
            "direct invariant violation is an existence counterexample for this "
            "history/interleaving, not a failure-rate or universality estimate"
        )
    elif confounded_evidence:
        interpretation = OUTCOME_INSTABILITY_OR_CONFOUNDED
        bounded_claim = (
            "outcome instability or trajectory divergence is confounded evidence, "
            "not a semantic failure proof"
        )
    else:
        interpretation = NO_INSUFFICIENCY_OBSERVED
        bounded_claim = (
            "absence of evidence in one fixed history and one screening pass; "
            "this is not a Whole-Update Parallel safety or sufficiency theorem"
        )

    return seal_payload(
        {
            "schema_version": "membind.native-characterization-c5-block-result.v1",
            "status": "complete",
            "interpretation": interpretation,
            "bounded_claim": bounded_claim,
            "metrics": metrics,
            "direct_evidence": direct_evidence,
            "confounded_evidence": confounded_evidence,
            "canonical_graph_parity": deepcopy(dict(canonical_graph_parity)),
            "retrieval_parity": deepcopy(dict(retrieval_parity)),
            "execution_path_evidence": deepcopy(dict(execution_path_evidence)),
        }
    )


def _validate_schedule(schedule: Mapping[str, Any]) -> None:
    if schedule.get("schema_version") != SCHEDULE_SCHEMA:
        _fail("schedule_schema_invalid")
    if schedule.get("payload_sha256") != payload_sha256(schedule):
        _fail("schedule_payload_mismatch")
    if tuple(schedule.get("concurrency_grid", ())) != CONCURRENCY_GRID:
        _fail("schedule_grid_invalid")
    blocks = schedule.get("block_schedules")
    if not isinstance(blocks, list) or len(blocks) != len(CONCURRENCY_GRID):
        _fail("schedule_blocks_invalid")
    for index, block in enumerate(blocks):
        if not isinstance(block, Mapping):
            _fail("schedule_block_invalid")
        if block.get("block_index") != index or block.get("concurrency") != CONCURRENCY_GRID[index]:
            _fail("schedule_block_grid_invalid")
    _validate_episode_ids(schedule.get("episode_ids", ()))


def _validate_hashes(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping) or not values:
        _fail("provenance_hashes_invalid")
    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            _fail("provenance_hashes_invalid")
        result[key] = value
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class C5ArtifactStore:
    """Small crash-consistent artifact writer for C5 offline/live runners."""

    run_dir: Path
    run_id: str
    manifest_path: Path
    events_path: Path
    result_path: Path

    @classmethod
    def create(
        cls,
        runs_root: Path,
        run_id: str,
        schedule: Mapping[str, Any],
        provenance_hashes: Mapping[str, str],
        command_argv: Sequence[str],
    ) -> "C5ArtifactStore":
        _validate_run_id(run_id)
        _validate_schedule(schedule)
        if schedule.get("run_id") != run_id:
            _fail("schedule_run_id_mismatch")
        if not isinstance(command_argv, Sequence) or isinstance(command_argv, (str, bytes)):
            _fail("command_argv_invalid")
        argv = [str(item) for item in command_argv]
        if not argv:
            _fail("command_argv_invalid")
        runs_root = Path(runs_root)
        run_dir = runs_root / run_id
        if run_dir.exists() and any(run_dir.iterdir()):
            _fail("run_directory_nonempty")
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest = seal_payload(
            {
                "schema_version": MANIFEST_SCHEMA,
                "status": "planned",
                "stage": "C5/E4",
                "run_id": run_id,
                "schedule_payload_sha256": schedule["payload_sha256"],
                "provenance_hashes": _validate_hashes(provenance_hashes),
                "command_argv": argv,
                "planned_blocks": [
                    {
                        "block_index": block["block_index"],
                        "concurrency": block["concurrency"],
                        "graph_namespace": block["graph_namespace"],
                    }
                    for block in schedule["block_schedules"]
                ],
            }
        )
        manifest_path = run_dir / "manifest.json"
        result_path = run_dir / "e4_whole_parallel.json"
        _atomic_write_json(manifest_path, manifest)
        return cls(
            run_dir=run_dir,
            run_id=run_id,
            manifest_path=manifest_path,
            events_path=run_dir / "events.jsonl",
            result_path=result_path,
        )

    def write_block_checkpoint(
        self,
        *,
        block_index: int,
        status: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if block_index not in range(len(CONCURRENCY_GRID)):
            _fail("block_index_invalid")
        if status not in {"completed", "failed"}:
            _fail("block_status_invalid")
        if result.get("payload_sha256") != payload_sha256(result):
            _fail("block_result_payload_mismatch")
        interpretation = result.get("interpretation")
        if interpretation not in LEGAL_INTERPRETATIONS:
            _fail("block_interpretation_invalid")
        checkpoint = seal_payload(
            {
                "schema_version": CHECKPOINT_SCHEMA,
                "status": status,
                "run_id": self.run_id,
                "block_index": block_index,
                "concurrency": CONCURRENCY_GRID[block_index],
                "interpretation": interpretation,
                "result_payload_sha256": result["payload_sha256"],
                "result": deepcopy(dict(result)),
            }
        )
        path = self.run_dir / "blocks" / f"{block_index:03d}" / "checkpoint.json"
        _atomic_write_json(path, checkpoint)
        return checkpoint

    def _read_block_checkpoints(self) -> list[dict[str, Any]]:
        checkpoints = []
        for block_index in range(len(CONCURRENCY_GRID)):
            path = self.run_dir / "blocks" / f"{block_index:03d}" / "checkpoint.json"
            checkpoints.append(_read_json(path))
        return checkpoints

    def write_e4_result(self) -> dict[str, Any]:
        checkpoints = self._read_block_checkpoints()
        interpretations = [item["interpretation"] for item in checkpoints]
        counts = {
            interpretation: interpretations.count(interpretation)
            for interpretation in LEGAL_INTERPRETATIONS
            if interpretations.count(interpretation)
        }
        if counts.get(DIRECT_INVARIANT_VIOLATION):
            overall = DIRECT_INVARIANT_VIOLATION
        elif counts.get(OUTCOME_INSTABILITY_OR_CONFOUNDED):
            overall = OUTCOME_INSTABILITY_OR_CONFOUNDED
        else:
            overall = NO_INSUFFICIENCY_OBSERVED
        result = seal_payload(
            {
                "schema_version": RESULT_SCHEMA,
                "status": "complete",
                "stage": "C5/E4",
                "run_id": self.run_id,
                "overall_interpretation": overall,
                "interpretation_counts": counts,
                "completed_block_count": len(checkpoints),
                "block_checkpoint_payload_sha256": [
                    item["payload_sha256"] for item in checkpoints
                ],
                "bounded_claim": (
                    "one fixed history and one screening pass; no C5 outcome "
                    "establishes Whole-Update Parallel general safety"
                ),
            }
        )
        _atomic_write_json(self.result_path, result)
        return result


def verify_c5_artifacts(run_dir: Path) -> dict[str, Any]:
    """Verify the local C5 artifact shape without contacting live services."""

    run_dir = Path(run_dir)
    result_path = run_dir / "e4_whole_parallel.json"
    manifest_path = run_dir / "manifest.json"
    try:
        manifest = _read_json(manifest_path)
    except NativeCharacterizationC5Error:
        return {
            "schema_version": VERIFICATION_SCHEMA,
            "attempt_status": "incomplete_invalid_non_mergeable",
            "completed_block_count": 0,
            "result_sha256": None,
        }
    completed = 0
    for block_index in range(len(CONCURRENCY_GRID)):
        try:
            checkpoint = _read_json(run_dir / "blocks" / f"{block_index:03d}" / "checkpoint.json")
            if checkpoint.get("status") == "completed":
                completed += 1
        except NativeCharacterizationC5Error:
            pass
    try:
        result = _read_json(result_path)
        result_sha = _sha256(result_path.read_bytes())
        status = (
            "complete"
            if result.get("schema_version") == RESULT_SCHEMA
            and result.get("run_id") == manifest.get("run_id")
            and result.get("completed_block_count") == len(CONCURRENCY_GRID)
            and completed == len(CONCURRENCY_GRID)
            else "incomplete_invalid_non_mergeable"
        )
    except NativeCharacterizationC5Error:
        result_sha = None
        status = "incomplete_invalid_non_mergeable"
    return {
        "schema_version": VERIFICATION_SCHEMA,
        "attempt_status": status,
        "run_id": manifest.get("run_id"),
        "completed_block_count": completed,
        "result_sha256": result_sha,
    }
