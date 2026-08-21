"""Monotonic lifecycle, concurrency, and source-order reduction primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .correctness import CorrectnessClass


class LifecycleError(ValueError):
    """Lifecycle evidence is incomplete, non-monotonic, or inconsistent."""


@dataclass(frozen=True, slots=True)
class EpisodeLifecycle:
    source_sequence: int
    t_submit_ns: int
    t_task_created_ns: int
    t_execution_start_ns: int
    t_caller_return_ns: int
    t_publication_visible_ns: int
    t_publication_durable_ns: int

    def __post_init__(self) -> None:
        values = (
            self.t_submit_ns,
            self.t_task_created_ns,
            self.t_execution_start_ns,
            self.t_caller_return_ns,
            self.t_publication_visible_ns,
            self.t_publication_durable_ns,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise LifecycleError("LIFECYCLE_TIMESTAMP_INVALID")
        if self.t_task_created_ns < self.t_submit_ns:
            raise LifecycleError("TASK_CREATED_BEFORE_SUBMIT")
        if self.t_execution_start_ns < self.t_task_created_ns:
            raise LifecycleError("EXECUTION_BEFORE_TASK_CREATION")
        if self.t_caller_return_ns < self.t_execution_start_ns:
            raise LifecycleError("CALLER_RETURN_BEFORE_EXECUTION")
        if self.t_publication_visible_ns < self.t_submit_ns:
            raise LifecycleError("PUBLICATION_VISIBLE_BEFORE_SUBMIT")
        if self.t_publication_durable_ns < self.t_publication_visible_ns:
            raise LifecycleError("DURABLE_BEFORE_VISIBLE")


@dataclass(frozen=True, slots=True)
class Span:
    span_id: str
    phase: str
    start_ns: int
    end_ns: int
    parent_span_id: str | None

    def __post_init__(self) -> None:
        if not self.span_id or not self.phase:
            raise LifecycleError("SPAN_IDENTITY_INVALID")
        if (
            isinstance(self.start_ns, bool)
            or isinstance(self.end_ns, bool)
            or not isinstance(self.start_ns, int)
            or not isinstance(self.end_ns, int)
            or self.start_ns < 0
            or self.end_ns <= self.start_ns
        ):
            raise LifecycleError("SPAN_INTERVAL_INVALID")


def episode_durations(lifecycle: EpisodeLifecycle) -> dict[str, int]:
    if not isinstance(lifecycle, EpisodeLifecycle):
        raise LifecycleError("EPISODE_LIFECYCLE_INVALID")
    return {
        "submit_to_start_ns": lifecycle.t_execution_start_ns - lifecycle.t_submit_ns,
        "service_ns": lifecycle.t_caller_return_ns - lifecycle.t_execution_start_ns,
        "submit_to_return_ns": lifecycle.t_caller_return_ns - lifecycle.t_submit_ns,
        "submit_to_visible_ns": lifecycle.t_publication_visible_ns - lifecycle.t_submit_ns,
        "submit_to_durable_ns": lifecycle.t_publication_durable_ns - lifecycle.t_submit_ns,
        "caller_return_to_durable_ns": lifecycle.t_publication_durable_ns
        - lifecycle.t_caller_return_ns,
    }


def reduce_block_timing(
    *,
    t0_ns: int,
    t_last_submit_ns: int,
    t_durable_complete_ns: int,
    t_validated_seal_ns: int,
) -> dict[str, int]:
    values = (t0_ns, t_last_submit_ns, t_durable_complete_ns, t_validated_seal_ns)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise LifecycleError("BLOCK_TIMESTAMP_INVALID")
    if not t0_ns <= t_last_submit_ns <= t_durable_complete_ns <= t_validated_seal_ns:
        raise LifecycleError("BLOCK_TIMESTAMP_ORDER_INVALID")
    return {
        "build_makespan_ns": t_durable_complete_ns - t0_ns,
        "drain_tail_ns": t_durable_complete_ns - t_last_submit_ns,
        "validation_seal_latency_ns": t_validated_seal_ns - t_durable_complete_ns,
    }


def concurrency_summary(spans: Sequence[Span]) -> dict[str, Any]:
    selected = tuple(spans)
    if not selected or any(not isinstance(span, Span) for span in selected):
        raise LifecycleError("SPANS_INVALID")
    changes: dict[int, int] = {}
    for span in selected:
        changes[span.start_ns] = changes.get(span.start_ns, 0) + 1
        changes[span.end_ns] = changes.get(span.end_ns, 0) - 1
    timestamps = sorted(changes)
    active = 0
    integral = 0
    active_max = 0
    active_k: dict[int, int] = {}
    for left, right in zip(timestamps, timestamps[1:]):
        active += changes[left]
        duration = right - left
        if active < 0 or duration < 0:
            raise LifecycleError("SPAN_SWEEP_INVALID")
        if active:
            active_k[active] = active_k.get(active, 0) + duration
            integral += active * duration
            active_max = max(active_max, active)
    union = sum(active_k.values())
    if union <= 0:
        raise LifecycleError("SPAN_UNION_EMPTY")
    overlap = sum(duration for count, duration in active_k.items() if count > 1)
    return {
        "inclusive_sum_ns": sum(span.end_ns - span.start_ns for span in selected),
        "interval_union_ns": union,
        "active_integral_ns": integral,
        "active_mean": integral / union,
        "active_max": active_max,
        "active_k_time_ns": dict(sorted(active_k.items())),
        "overlap_wall_fraction": overlap / union,
    }


def _interval_union(intervals: Sequence[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    ordered = sorted(intervals)
    start, end = ordered[0]
    total = 0
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def span_exclusive_durations(spans: Sequence[Span]) -> dict[str, int]:
    selected = tuple(spans)
    by_id = {span.span_id: span for span in selected}
    if len(by_id) != len(selected):
        raise LifecycleError("SPAN_ID_DUPLICATE")
    children: dict[str, list[tuple[int, int]]] = {span.span_id: [] for span in selected}
    for span in selected:
        if span.parent_span_id is None:
            continue
        parent = by_id.get(span.parent_span_id)
        if parent is None:
            raise LifecycleError("SPAN_PARENT_MISSING")
        if span.start_ns < parent.start_ns or span.end_ns > parent.end_ns:
            raise LifecycleError("SPAN_CHILD_OUTSIDE_PARENT")
        children[parent.span_id].append((span.start_ns, span.end_ns))
    return {
        span.span_id: (span.end_ns - span.start_ns) - _interval_union(children[span.span_id])
        for span in selected
    }


def ordering_summary(
    source_order: Sequence[int], observed_order: Sequence[int]
) -> dict[str, Any]:
    source = tuple(source_order)
    observed = tuple(observed_order)
    if len(source) != len(set(source)) or set(source) != set(observed) or len(observed) != len(set(observed)):
        raise LifecycleError("ORDERING_PERMUTATION_INVALID")
    source_rank = {value: index for index, value in enumerate(source)}
    ranked = [source_rank[value] for value in observed]
    inversions = sum(
        ranked[left] > ranked[right]
        for left in range(len(ranked))
        for right in range(left + 1, len(ranked))
    )
    pairs = len(ranked) * (len(ranked) - 1) // 2
    observed_rank = {value: index for index, value in enumerate(observed)}
    displacement = max(
        (abs(source_rank[value] - observed_rank[value]) for value in source),
        default=0,
    )
    return {
        "inversion_count": inversions,
        "inversion_density": inversions / pairs if pairs else 0.0,
        "kendall_tau": 1.0 - (2.0 * inversions / pairs) if pairs else 1.0,
        "max_displacement": displacement,
        "classification": CorrectnessClass.ORDERING_OBSERVATION,
        "direct_semantic_violations": 0,
    }


__all__ = [
    "EpisodeLifecycle",
    "LifecycleError",
    "Span",
    "concurrency_summary",
    "episode_durations",
    "ordering_summary",
    "reduce_block_timing",
    "span_exclusive_durations",
]

