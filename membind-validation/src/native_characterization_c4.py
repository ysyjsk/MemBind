"""Pure scheduling and metric core for frozen C4/E3 characterization.

The module deliberately contains no Graphiti, database, model-serving, or file
system integration.  A later live runner can inject those boundaries while the
contracts here keep the two treatments on one absolute-arrival FIFO service
path and make every measured timestamp explicit.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


NATIVE_SYNC = "Native-Sync"
NATIVE_ASYNC_SERIAL = "Native-Async-Serial"
METHODS = frozenset({NATIVE_SYNC, NATIVE_ASYNC_SERIAL})


class NativeCharacterizationC4Error(RuntimeError):
    """Raised when a frozen scheduling or measurement invariant is violated."""


@dataclass(frozen=True)
class Episode:
    """One source-ordered input; payload remains opaque to the scheduling core."""

    source_sequence: int
    payload: Any


class Clock(Protocol):
    def now_ns(self) -> int: ...

    def sleep_until_ns(self, timestamp_ns: int) -> None: ...


class DurableWriter(Protocol):
    def persist_enqueue(self, episode: Episode, arrival_timestamp_ns: int) -> int: ...

    def persist_publication(self, record: dict[str, object]) -> None: ...

    def persist_failure(self, checkpoint: dict[str, object]) -> None: ...


Service = Callable[[Episode, int], int]


@dataclass
class _QueuedEpisode:
    episode: Episode
    arrival_timestamp_ns: int
    enqueue_ack_timestamp_ns: int


@dataclass
class _ActiveService:
    queued: _QueuedEpisode
    service_start_timestamp_ns: int
    publish_timestamp_ns: int


def _integer_ns(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeCharacterizationC4Error(f"{field} must be an integer nanosecond timestamp")
    return value


def build_absolute_arrivals(*, start_ns: int, interarrival_ns: int, count: int) -> list[int]:
    """Build a drift-free open-loop schedule from one absolute origin."""

    start_ns = _integer_ns(start_ns, "start_ns")
    interarrival_ns = _integer_ns(interarrival_ns, "interarrival_ns")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise NativeCharacterizationC4Error("count must be a non-negative integer")
    if count > 1 and interarrival_ns <= 0:
        raise NativeCharacterizationC4Error("interarrival_ns must be positive for multiple arrivals")
    if interarrival_ns < 0:
        raise NativeCharacterizationC4Error("interarrival_ns must not be negative")
    return [start_ns + index * interarrival_ns for index in range(count)]


def _validate_inputs(
    method: str,
    episodes: Sequence[Episode],
    arrival_timestamps_ns: Sequence[int],
    clock: Clock,
) -> None:
    if method not in METHODS:
        raise NativeCharacterizationC4Error(f"unsupported C4 method: {method}")
    if not episodes:
        raise NativeCharacterizationC4Error("a replay requires at least one episode")
    if len(episodes) != len(arrival_timestamps_ns):
        raise NativeCharacterizationC4Error("episode and arrival counts differ")

    sequences = [item.source_sequence for item in episodes]
    if len(set(sequences)) != len(sequences):
        raise NativeCharacterizationC4Error("source_sequence values must be unique")
    arrivals = [
        _integer_ns(value, f"arrival_timestamps_ns[{index}]")
        for index, value in enumerate(arrival_timestamps_ns)
    ]
    if any(current < previous for previous, current in zip(arrivals, arrivals[1:])):
        raise NativeCharacterizationC4Error("absolute arrivals must be non-decreasing")
    if arrivals[0] < clock.now_ns():
        raise NativeCharacterizationC4Error("first arrival precedes the injected clock")


def compute_episode_metrics(record: Mapping[str, object]) -> dict[str, int]:
    """Validate one timestamp envelope and derive the frozen E3 metrics."""

    arrival = _integer_ns(record.get("arrival_timestamp_ns"), "arrival_timestamp_ns")
    enqueue_ack = _integer_ns(
        record.get("enqueue_ack_timestamp_ns"), "enqueue_ack_timestamp_ns"
    )
    service_start = _integer_ns(
        record.get("service_start_timestamp_ns"), "service_start_timestamp_ns"
    )
    publish = _integer_ns(record.get("publish_timestamp_ns"), "publish_timestamp_ns")
    caller_return = _integer_ns(
        record.get("caller_return_timestamp_ns"), "caller_return_timestamp_ns"
    )
    if not arrival <= enqueue_ack <= service_start <= publish:
        raise NativeCharacterizationC4Error(
            "timestamps must satisfy arrival <= enqueue ack <= service start <= publish"
        )
    if caller_return < arrival:
        raise NativeCharacterizationC4Error("caller return precedes arrival")

    signed = publish - caller_return
    return {
        "caller_return_latency_ns": caller_return - arrival,
        "construction_service_time_ns": publish - service_start,
        "queue_wait_ns": service_start - enqueue_ack,
        "arrival_to_visible_ns": publish - arrival,
        "signed_publish_after_return_ns": signed,
        "post_return_stale_window_ns": max(0, signed),
    }


def validate_exactly_once(
    episodes: Sequence[Episode], records: Sequence[Mapping[str, object]]
) -> dict[str, int]:
    """Fail closed on publication loss, duplication, or FIFO reordering."""

    expected = [item.source_sequence for item in episodes]
    if len(set(expected)) != len(expected):
        raise NativeCharacterizationC4Error("requested source_sequence values are not unique")
    observed = [int(item["source_sequence"]) for item in records]
    observed_counts = Counter(observed)
    duplicate_count = sum(count - 1 for count in observed_counts.values() if count > 1)
    loss_count = sum(1 for source_sequence in expected if source_sequence not in observed_counts)
    unexpected = [source_sequence for source_sequence in observed if source_sequence not in set(expected)]
    if observed != expected or duplicate_count or loss_count or unexpected:
        raise NativeCharacterizationC4Error(
            "publication sequence violates FIFO exactly-once: "
            f"loss={loss_count}, duplicate={duplicate_count}, unexpected={len(unexpected)}"
        )
    return {
        "requested": len(expected),
        "published": len(observed),
        "loss_count": loss_count,
        "duplicate_count": duplicate_count,
    }


def analyze_backlog(
    arrival_timestamps_ns: Sequence[int],
    records: Sequence[Mapping[str, object]],
    *,
    observation_end_ns: int | None = None,
) -> dict[str, object]:
    """Integrate arrived-but-not-published episodes over grouped event times."""

    if not arrival_timestamps_ns:
        raise NativeCharacterizationC4Error("backlog analysis requires arrivals")
    arrivals = [_integer_ns(value, "arrival timestamp") for value in arrival_timestamps_ns]
    if any(current < previous for previous, current in zip(arrivals, arrivals[1:])):
        raise NativeCharacterizationC4Error("backlog arrivals must be non-decreasing")

    arrival_counts: dict[int, int] = defaultdict(int)
    for timestamp in arrivals:
        arrival_counts[timestamp] += 1
    publication_counts: dict[int, int] = defaultdict(int)
    publishes: list[int] = []
    for record in records:
        timestamp = _integer_ns(record.get("publish_timestamp_ns"), "publish_timestamp_ns")
        publishes.append(timestamp)
        publication_counts[timestamp] += 1

    event_timestamps = set(arrival_counts) | set(publication_counts)
    end_ns = max(event_timestamps) if observation_end_ns is None else _integer_ns(
        observation_end_ns, "observation_end_ns"
    )
    if end_ns < arrivals[0] or (publishes and end_ns < max(publishes)):
        raise NativeCharacterizationC4Error("observation end precedes a retained event")
    event_timestamps.add(end_ns)

    backlog = 0
    maximum = 0
    auc = 0
    previous_timestamp: int | None = None
    series: list[dict[str, int]] = []
    backlog_at_final_arrival: int | None = None
    final_arrival = arrivals[-1]
    for timestamp in sorted(event_timestamps):
        if previous_timestamp is not None:
            auc += backlog * (timestamp - previous_timestamp)
        # The replay contract admits every arrival at a timestamp before it
        # publishes work completing at that same timestamp. Preserve that
        # instantaneous peak even though it contributes zero area to the AUC.
        arrived = arrival_counts.get(timestamp, 0)
        if arrived:
            backlog += arrived
            maximum = max(maximum, backlog)
            series.append({"timestamp_ns": timestamp, "backlog": backlog})
            if timestamp == final_arrival:
                backlog_at_final_arrival = backlog

        published = publication_counts.get(timestamp, 0)
        if published:
            backlog -= published
            series.append({"timestamp_ns": timestamp, "backlog": backlog})
        if backlog < 0:
            raise NativeCharacterizationC4Error("publication count exceeds arrived backlog")
        if not arrived and not published:
            series.append({"timestamp_ns": timestamp, "backlog": backlog})
        previous_timestamp = timestamp

    if backlog_at_final_arrival is None:
        raise NativeCharacterizationC4Error("final-arrival backlog boundary is missing")
    return {
        "backlog_time_series": series,
        "backlog_auc_episode_ns": auc,
        "maximum_backlog": maximum,
        "backlog_at_final_arrival": backlog_at_final_arrival,
        "final_backlog": backlog,
    }


def _mean(values: Sequence[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def _aggregate_success(
    arrival_timestamps_ns: Sequence[int],
    records: Sequence[Mapping[str, object]],
    episode_metrics: Sequence[Mapping[str, int]],
) -> dict[str, object]:
    backlog = analyze_backlog(arrival_timestamps_ns, records)
    first_arrival = arrival_timestamps_ns[0]
    final_arrival = arrival_timestamps_ns[-1]
    final_publish = max(int(item["publish_timestamp_ns"]) for item in records)
    makespan_ns = final_publish - first_arrival
    throughput = (len(records) * 1_000_000_000 / makespan_ns) if makespan_ns > 0 else None
    aggregate: dict[str, object] = {
        **backlog,
        "episode_count": len(arrival_timestamps_ns),
        "completed_episode_count": len(records),
        "first_arrival_timestamp_ns": first_arrival,
        "final_arrival_timestamp_ns": final_arrival,
        "final_publish_timestamp_ns": final_publish,
        "makespan_ns": makespan_ns,
        "drain_time_ns": max(0, final_publish - final_arrival),
        "throughput_episodes_per_second": throughput,
        "error_count": 0,
        "checkpoint_loss_count": 0,
    }
    metric_names = (
        "caller_return_latency_ns",
        "construction_service_time_ns",
        "queue_wait_ns",
        "arrival_to_visible_ns",
        "signed_publish_after_return_ns",
        "post_return_stale_window_ns",
    )
    for name in metric_names:
        aggregate[f"mean_{name}"] = _mean([int(item[name]) for item in episode_metrics])
    return aggregate


def _failure_checkpoint(
    *,
    method: str,
    failure_timestamp_ns: int,
    failed: _QueuedEpisode,
    error: Exception,
    records: Sequence[Mapping[str, object]],
    durably_enqueued: Sequence[int],
    pending: Sequence[_QueuedEpisode],
    not_yet_arrived: Sequence[Episode],
) -> dict[str, object]:
    return {
        "status": "failed",
        "method": method,
        "failure_timestamp_ns": failure_timestamp_ns,
        "failed_source_sequence": failed.episode.source_sequence,
        "error_class": type(error).__name__,
        "completed_source_sequences": [int(item["source_sequence"]) for item in records],
        "durably_enqueued_source_sequences": list(durably_enqueued),
        "pending_source_sequences": [item.episode.source_sequence for item in pending],
        "not_yet_arrived_source_sequences": [item.source_sequence for item in not_yet_arrived],
    }


def run_replay(
    method: str,
    episodes: Sequence[Episode],
    arrival_timestamps_ns: Sequence[int],
    clock: Clock,
    u0_service: Service,
    durable_writer: DurableWriter,
) -> dict[str, object]:
    """Execute one deterministic open-loop treatment on a FIFO single worker.

    The service callback returns its service duration in nanoseconds.  This keeps
    the core a deterministic, dependency-free event scheduler; a live adapter is
    responsible for measuring a real U0 call and exposing that duration.
    """

    _validate_inputs(method, episodes, arrival_timestamps_ns, clock)
    arrivals = list(arrival_timestamps_ns)
    queue: deque[_QueuedEpisode] = deque()
    records: list[dict[str, object]] = []
    durably_enqueued: list[int] = []
    active: _ActiveService | None = None
    next_arrival_index = 0

    while len(records) < len(episodes):
        candidate_times: list[int] = []
        if next_arrival_index < len(episodes):
            candidate_times.append(arrivals[next_arrival_index])
        if active is not None:
            candidate_times.append(active.publish_timestamp_ns)
        elif queue:
            candidate_times.append(queue[0].enqueue_ack_timestamp_ns)
        if not candidate_times:
            raise NativeCharacterizationC4Error("scheduler has unfinished work but no next event")

        next_timestamp = min(candidate_times)
        if next_timestamp < clock.now_ns():
            raise NativeCharacterizationC4Error("scheduler attempted to move backwards")
        clock.sleep_until_ns(next_timestamp)

        # Absolute arrivals at a shared timestamp are admitted before publication
        # and worker dispatch.  This makes tie handling deterministic and ensures
        # a failure checkpoint includes every input that had already arrived.
        while (
            next_arrival_index < len(episodes)
            and arrivals[next_arrival_index] == next_timestamp
        ):
            episode = episodes[next_arrival_index]
            # Native-Sync is the upstream caller path and therefore has no
            # background durable-enqueue boundary. Async-Serial acknowledges
            # only after its queue record is durable.
            ack = (
                next_timestamp
                if method == NATIVE_SYNC
                else durable_writer.persist_enqueue(episode, next_timestamp)
            )
            ack = _integer_ns(ack, "enqueue_ack_timestamp_ns")
            if ack < next_timestamp:
                raise NativeCharacterizationC4Error("durable enqueue ack precedes arrival")
            queued = _QueuedEpisode(episode, next_timestamp, ack)
            queue.append(queued)
            if method == NATIVE_ASYNC_SERIAL:
                durably_enqueued.append(episode.source_sequence)
            next_arrival_index += 1

        if active is not None and active.publish_timestamp_ns == next_timestamp:
            queued = active.queued
            caller_return = (
                next_timestamp
                if method == NATIVE_SYNC
                else queued.enqueue_ack_timestamp_ns
            )
            record: dict[str, object] = {
                "method": method,
                "source_sequence": queued.episode.source_sequence,
                "arrival_timestamp_ns": queued.arrival_timestamp_ns,
                "enqueue_ack_timestamp_ns": queued.enqueue_ack_timestamp_ns,
                "service_start_timestamp_ns": active.service_start_timestamp_ns,
                "publish_timestamp_ns": next_timestamp,
                "caller_return_timestamp_ns": caller_return,
            }
            compute_episode_metrics(record)
            durable_writer.persist_publication(record)
            records.append(record)
            active = None

        if active is None and queue and queue[0].enqueue_ack_timestamp_ns <= next_timestamp:
            queued = queue[0]
            service_start = next_timestamp
            try:
                duration_ns = _integer_ns(
                    u0_service(queued.episode, service_start), "construction service duration"
                )
                if duration_ns < 0:
                    raise NativeCharacterizationC4Error(
                        "construction service duration must not be negative"
                    )
            except Exception as error:
                checkpoint = _failure_checkpoint(
                    method=method,
                    failure_timestamp_ns=service_start,
                    failed=queued,
                    error=error,
                    records=records,
                    durably_enqueued=durably_enqueued,
                    pending=list(queue),
                    not_yet_arrived=episodes[next_arrival_index:],
                )
                durable_writer.persist_failure(checkpoint)
                partial_backlog = analyze_backlog(
                    arrivals[:next_arrival_index],
                    records,
                    observation_end_ns=service_start,
                )
                return {
                    "status": "failed",
                    "method": method,
                    "records": records,
                    "episode_metrics": [compute_episode_metrics(item) for item in records],
                    "aggregate": {
                        **partial_backlog,
                        "completed_episode_count": len(records),
                        "error_count": 1,
                    },
                    "failure_checkpoint": checkpoint,
                }
            queue.popleft()
            active = _ActiveService(
                queued=queued,
                service_start_timestamp_ns=service_start,
                publish_timestamp_ns=service_start + duration_ns,
            )

    integrity = validate_exactly_once(episodes, records)
    episode_metrics = [compute_episode_metrics(item) for item in records]
    aggregate = _aggregate_success(arrivals, records, episode_metrics)
    if aggregate["final_backlog"] != 0:
        raise NativeCharacterizationC4Error("successful replay retained nonzero backlog")
    return {
        "status": "complete",
        "method": method,
        "records": records,
        "episode_metrics": episode_metrics,
        "aggregate": aggregate,
        "integrity": integrity,
        "failure_checkpoint": None,
    }
