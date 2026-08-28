"""Deterministic token-aware ready-task scheduling for V6.1.

The scheduler is deliberately provider-independent.  It models each ready
provider operation as a task with a token cost and a remaining critical-path
estimate, then chooses an endpoint using the currently observed finish-time
work.  It does not change the number of permits or the semantic dependency
contract; callers still decide which tasks are safe to submit and when a
result may be committed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class CriticalSchedulerError(RuntimeError):
    """Raised when a task or scheduler lifecycle contract is invalid."""


@dataclass(frozen=True, slots=True)
class ReadyTask:
    """A provider operation whose dependencies have been materialized."""

    task_id: str
    source_sequence: int
    kind: str
    token_cost: int
    remaining_critical_path_ns: int = 0
    preferred_endpoint_id: str | None = None
    dependencies: tuple[str, ...] = ()
    frontier_critical: bool = False
    estimated_service_ns: int | None = None

    def __post_init__(self) -> None:
        if not self.task_id:
            raise CriticalSchedulerError("ready task id must be non-empty")
        if self.source_sequence < 0:
            raise CriticalSchedulerError("ready task source sequence must be non-negative")
        if not self.kind:
            raise CriticalSchedulerError("ready task kind must be non-empty")
        if self.token_cost <= 0:
            raise CriticalSchedulerError("ready task token cost must be positive")
        if self.remaining_critical_path_ns < 0:
            raise CriticalSchedulerError("critical path estimate must be non-negative")
        if self.estimated_service_ns is not None and self.estimated_service_ns <= 0:
            raise CriticalSchedulerError("service estimate must be positive")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise CriticalSchedulerError("ready task dependencies must be unique")
        if self.task_id in self.dependencies:
            raise CriticalSchedulerError("ready task cannot depend on itself")


@dataclass(frozen=True, slots=True)
class DispatchDecision:
    """Auditable result of one deterministic scheduler selection."""

    task_id: str
    endpoint_id: str
    preferred_endpoint_id: str | None
    projected_finish_ns: int
    service_estimate_ns: int
    critical_path_ns: int
    candidate_scores: Mapping[str, int]
    active_work_before_ns: Mapping[str, int]
    token_cost: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v6.1.critical-dispatch.v1",
            "task_id": self.task_id,
            "endpoint_id": self.endpoint_id,
            "preferred_endpoint_id": self.preferred_endpoint_id,
            "projected_finish_ns": self.projected_finish_ns,
            "service_estimate_ns": self.service_estimate_ns,
            "critical_path_ns": self.critical_path_ns,
            "candidate_scores": dict(self.candidate_scores),
            "active_work_before_ns": dict(self.active_work_before_ns),
            "token_cost": self.token_cost,
            "reason": self.reason,
        }


class CriticalPathResourceScheduler:
    """A small deterministic ready-task scheduler with measured service EWMAs.

    ``active_work_ns`` is a conservative serialized-work approximation of the
    requests already dispatched to an endpoint.  Once a request completes,
    its measured service time updates the endpoint/kind EWMA.  Cold-start
    tasks use token cost as a unitless prior, so the first tie preserves the
    phase-preferred endpoint; no hardware-specific tuning constant is needed.
    """

    def __init__(
        self,
        endpoint_ids: tuple[str, ...] | list[str],
        *,
        ewma_alpha_numerator: int = 1,
        ewma_alpha_denominator: int = 2,
    ) -> None:
        normalized = tuple(str(item) for item in endpoint_ids)
        if not normalized or any(not item for item in normalized):
            raise CriticalSchedulerError("scheduler endpoint set is invalid")
        if len(set(normalized)) != len(normalized):
            raise CriticalSchedulerError("scheduler endpoint ids are not unique")
        if not 0 < ewma_alpha_numerator <= ewma_alpha_denominator:
            raise CriticalSchedulerError("scheduler EWMA alpha is invalid")
        self.endpoint_ids = normalized
        self._endpoint_order = {endpoint: index for index, endpoint in enumerate(normalized)}
        self._alpha_num = int(ewma_alpha_numerator)
        self._alpha_den = int(ewma_alpha_denominator)
        self._active_work_ns = {endpoint: 0 for endpoint in normalized}
        self._active_tokens = {endpoint: 0 for endpoint in normalized}
        self._service_ewma_ns: dict[tuple[str, str], int] = {}
        self._tasks: dict[str, ReadyTask] = {}
        self._completed: set[str] = set()
        self._inflight: dict[str, tuple[str, int]] = {}
        self._decisions: list[dict[str, Any]] = []

    @property
    def active_work_ns(self) -> dict[str, int]:
        return dict(self._active_work_ns)

    @property
    def active_tokens(self) -> dict[str, int]:
        return dict(self._active_tokens)

    def submit(self, task: ReadyTask) -> None:
        if task.task_id in self._tasks or task.task_id in self._completed:
            raise CriticalSchedulerError(f"task id is already registered: {task.task_id}")
        unknown = set(task.dependencies) - set(self._tasks) - self._completed
        if unknown:
            raise CriticalSchedulerError(
                f"task dependencies must be registered first: {sorted(unknown)}"
            )
        self._tasks[task.task_id] = task

    def _ready(self) -> list[ReadyTask]:
        return [
            task
            for task in self._tasks.values()
            if task.task_id not in self._inflight
            and task.task_id not in self._completed
            and all(dependency in self._completed for dependency in task.dependencies)
        ]

    def _estimate_service_ns(self, task: ReadyTask, endpoint_id: str) -> int:
        if task.estimated_service_ns is not None:
            return int(task.estimated_service_ns)
        measured = self._service_ewma_ns.get((endpoint_id, task.kind))
        if measured is not None:
            return measured
        measured = self._service_ewma_ns.get((endpoint_id, "*"))
        if measured is not None:
            return measured
        # Token cost is a cold-start ordering prior, not a serving parameter.
        return int(task.token_cost)

    @staticmethod
    def _frontier_rank(task: ReadyTask) -> int:
        # Frontier-critical work must win over speculative work when both have
        # the same projected finish.  Source order then makes the decision
        # replayable without depending on task insertion timing.
        return 0 if task.frontier_critical else 1

    def choose(self) -> DispatchDecision | None:
        ready = self._ready()
        if not ready:
            return None
        candidates: list[tuple[tuple[Any, ...], ReadyTask, str, int, dict[str, int]]] = []
        for task in ready:
            scores: dict[str, int] = {}
            for endpoint_id in self.endpoint_ids:
                estimate = self._estimate_service_ns(task, endpoint_id)
                projected = self._active_work_ns[endpoint_id] + estimate
                scores[endpoint_id] = int(projected)
            for endpoint_id in self.endpoint_ids:
                projected = scores[endpoint_id]
                preferred_rank = 0 if endpoint_id == task.preferred_endpoint_id else 1
                key = (
                    self._frontier_rank(task),
                    projected + int(task.remaining_critical_path_ns),
                    projected,
                    -int(task.remaining_critical_path_ns),
                    int(task.source_sequence),
                    preferred_rank,
                    self._endpoint_order[endpoint_id],
                    task.task_id,
                )
                candidates.append((key, task, endpoint_id, projected, scores))
        _key, task, endpoint_id, projected, scores = min(candidates, key=lambda row: row[0])
        active_before = dict(self._active_work_ns)
        estimate = self._estimate_service_ns(task, endpoint_id)
        self._active_work_ns[endpoint_id] += estimate
        self._active_tokens[endpoint_id] += task.token_cost
        self._inflight[task.task_id] = (endpoint_id, estimate)
        reason = (
            "critical_path_preferred"
            if endpoint_id == task.preferred_endpoint_id
            else "critical_path_earliest_finish_spillover"
        )
        decision = DispatchDecision(
            task_id=task.task_id,
            endpoint_id=endpoint_id,
            preferred_endpoint_id=task.preferred_endpoint_id,
            projected_finish_ns=projected,
            service_estimate_ns=estimate,
            critical_path_ns=int(task.remaining_critical_path_ns),
            candidate_scores=scores,
            active_work_before_ns=active_before,
            token_cost=task.token_cost,
            reason=reason,
        )
        self._decisions.append(decision.to_dict())
        return decision

    def complete(self, task_id: str, *, service_ns: int) -> None:
        if service_ns <= 0:
            raise CriticalSchedulerError("measured service time must be positive")
        task = self._tasks.get(task_id)
        reservation = self._inflight.pop(task_id, None)
        if task is None or reservation is None:
            raise CriticalSchedulerError(f"task is not inflight: {task_id}")
        endpoint_id, estimate = reservation
        remaining = self._active_work_ns[endpoint_id] - estimate
        if remaining < 0:
            raise CriticalSchedulerError("scheduler active work underflow")
        self._active_work_ns[endpoint_id] = remaining
        self._active_tokens[endpoint_id] -= task.token_cost
        if self._active_tokens[endpoint_id] < 0:
            raise CriticalSchedulerError("scheduler active token underflow")
        key = (endpoint_id, task.kind)
        previous = self._service_ewma_ns.get(key)
        self._service_ewma_ns[key] = (
            int(service_ns)
            if previous is None
            else (
                previous * (self._alpha_den - self._alpha_num)
                + int(service_ns) * self._alpha_num
            )
            // self._alpha_den
        )
        self._completed.add(task_id)

    def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        reservation = self._inflight.pop(task_id, None)
        if task is None or reservation is None:
            raise CriticalSchedulerError(f"task is not inflight: {task_id}")
        endpoint_id, estimate = reservation
        self._active_work_ns[endpoint_id] -= estimate
        self._active_tokens[endpoint_id] -= task.token_cost
        if self._active_work_ns[endpoint_id] < 0 or self._active_tokens[endpoint_id] < 0:
            raise CriticalSchedulerError("scheduler cancellation underflow")

    def evidence(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v6.1.critical-scheduler.v1",
            "endpoint_ids": list(self.endpoint_ids),
            "active_work_ns": dict(self._active_work_ns),
            "active_tokens": dict(self._active_tokens),
            "service_ewma_ns": {
                f"{endpoint}:{kind}": value
                for (endpoint, kind), value in sorted(self._service_ewma_ns.items())
            },
            "inflight_task_ids": sorted(self._inflight),
            "completed_task_ids": sorted(self._completed),
            "decisions": [dict(row) for row in self._decisions],
            "balanced": all(value == 0 for value in self._active_work_ns.values())
            and all(value == 0 for value in self._active_tokens.values())
            and not self._inflight,
        }


__all__ = [
    "CriticalPathResourceScheduler",
    "CriticalSchedulerError",
    "DispatchDecision",
    "ReadyTask",
]
