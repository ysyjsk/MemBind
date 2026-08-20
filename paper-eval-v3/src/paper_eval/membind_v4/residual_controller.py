"""v4-only arbitration that makes one residual speculative slot reachable.

The frozen v3.1 controller cannot distinguish future Compile from a
speculative NodeResolve transport because both use its ``COMPILE`` kind. This
API-compatible controller adds a context-local role tag while preserving the
v3.1 request metadata and frontier invariants.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

from paper_eval.membind_v31.admission import (
    AdmissionPolicy,
    RequestKind,
    RequestSpec,
)


_SPECULATIVE_TRANSPORT = ContextVar(
    "membind_v4_speculative_transport",
    default=False,
)


class V4ResidualControllerError(ValueError):
    """A v4 residual-controller invariant failed closed."""


def _fail(code: str) -> V4ResidualControllerError:
    return V4ResidualControllerError(code)


@contextmanager
def v4_speculative_transport_scope() -> Iterator[None]:
    """Tag provider tasks created in this context as speculative transport."""

    token = _SPECULATIVE_TRANSPORT.set(True)
    try:
        yield
    finally:
        _SPECULATIVE_TRANSPORT.reset(token)


@dataclass(slots=True)
class _Record:
    spec: RequestSpec
    enqueue_sequence: int
    speculative: bool
    state: str = "WAITING"
    cancellation_requested: bool = False


class V4ResidualRequestAdmissionController:
    """Frontier-first K=2 controller with one tagged residual SPEC role.

    When a candidate reservation is active and exactly one frontier owns a
    permit, normal future Compile work cannot consume the residual permit. A
    waiting tagged speculation wins that permit. Releasing the reservation
    restores the selected v3.1 FIFO/cache-affine Compile ordering.
    """

    def __init__(self, *, limit: int, policy: AdmissionPolicy) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit != 2:
            raise _fail("global_k_must_equal_2")
        if not isinstance(policy, AdmissionPolicy):
            raise _fail("policy_invalid")
        self._limit = limit
        self._policy = policy
        self._records: dict[str, _Record] = {}
        self._active: set[str] = set()
        self._events: list[dict[str, object]] = []
        self._terminal_counts = {"CANCELLED": 0, "COMPLETED": 0, "FAILED": 0}
        self._observed_max_inflight = 0
        self._residual_reserved = False

    def set_residual_reservation(self, active: bool) -> None:
        if not isinstance(active, bool):
            raise _fail("residual_reservation_invalid")
        self._residual_reserved = active

    def _emit(
        self,
        event_type: str,
        record: _Record,
        *,
        error_class: str | None = None,
    ) -> None:
        event: dict[str, object] = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            "request_id": record.spec.request_id,
            "request_kind": record.spec.kind.value,
            "v4_request_role": (
                "SPECULATIVE" if record.speculative else record.spec.kind.value
            ),
            "request_state": record.state,
            "stream_id": record.spec.stream_id,
            "source_sequence": record.spec.source_sequence,
            "affinity_score": record.spec.affinity_score,
            "provider_recency": record.spec.provider_recency,
            "cohort_gain": record.spec.cohort_gain,
            "affinity_signature": record.spec.affinity_signature,
        }
        if error_class is not None:
            event["error_class"] = error_class
        self._events.append(event)

    def submit(self, spec: RequestSpec) -> None:
        if not isinstance(spec, RequestSpec):
            raise _fail("request_spec_invalid")
        if spec.request_id in self._records:
            raise _fail("request_id_duplicate")
        speculative = bool(_SPECULATIVE_TRANSPORT.get())
        if speculative and spec.kind is not RequestKind.COMPILE:
            raise _fail("speculative_transport_kind_invalid")
        record = _Record(
            spec=spec,
            enqueue_sequence=len(self._records),
            speculative=speculative,
        )
        self._records[spec.request_id] = record
        self._emit("request_waiting", record)

    def _record(self, request_id: str) -> _Record:
        if not isinstance(request_id, str) or not request_id:
            raise _fail("request_id_invalid")
        record = self._records.get(request_id)
        if record is None:
            raise _fail("request_unknown")
        return record

    @staticmethod
    def _frontier_key(record: _Record) -> tuple[int, str, int, str]:
        return (
            record.spec.source_sequence,
            record.spec.stream_id,
            record.enqueue_sequence,
            record.spec.request_id,
        )

    @staticmethod
    def _fifo_key(record: _Record) -> tuple[int, int, str, str]:
        return (
            record.enqueue_sequence,
            record.spec.source_sequence,
            record.spec.stream_id,
            record.spec.request_id,
        )

    @staticmethod
    def _cache_key(
        record: _Record,
    ) -> tuple[int, int, int, int, str, int, str, str]:
        return (
            -record.spec.affinity_score,
            -record.spec.provider_recency,
            -record.spec.cohort_gain,
            record.spec.source_sequence,
            record.spec.stream_id,
            record.enqueue_sequence,
            record.spec.affinity_signature,
            record.spec.request_id,
        )

    def _activate(self, record: _Record) -> RequestSpec:
        record.state = "ACTIVE"
        self._active.add(record.spec.request_id)
        self._observed_max_inflight = max(
            self._observed_max_inflight,
            len(self._active),
        )
        self._emit("request_admitted", record)
        return record.spec

    def admit_available(self) -> tuple[RequestSpec, ...]:
        frontier_groups = {
            record.spec.frontier_group or record.spec.request_id
            for record in self._records.values()
            if record.spec.kind is RequestKind.FRONTIER
            and record.state in {"WAITING", "ACTIVE"}
        }
        if len(frontier_groups) > 1:
            raise _fail("multiple_frontier_requests")

        capacity = self._limit - len(self._active)
        if capacity <= 0:
            return ()
        waiting = [
            record for record in self._records.values() if record.state == "WAITING"
        ]
        frontiers = sorted(
            (record for record in waiting if record.spec.kind is RequestKind.FRONTIER),
            key=self._frontier_key,
        )
        selected: list[_Record] = frontiers[:capacity]
        capacity -= len(selected)
        if capacity <= 0:
            return tuple(self._activate(record) for record in selected)

        active_frontier_count = sum(
            self._records[request_id].spec.kind is RequestKind.FRONTIER
            for request_id in self._active
        )
        projected_frontiers = active_frontier_count + len(selected)
        waiting_frontier_remains = len(frontiers) > len(selected)
        order = self._cache_key if self._policy is AdmissionPolicy.CACHE_AFFINE else self._fifo_key

        if projected_frontiers == 1 and not waiting_frontier_remains:
            speculative = sorted(
                (
                    record
                    for record in waiting
                    if record.speculative and record not in selected
                ),
                key=order,
            )
            if speculative:
                selected.append(speculative[0])
                capacity -= 1

            barrier_holds = self._policy is AdmissionPolicy.BARRIER
            if capacity > 0 and not self._residual_reserved and not barrier_holds:
                compiles = sorted(
                    (
                        record
                        for record in waiting
                        if record.spec.kind is RequestKind.COMPILE
                        and not record.speculative
                    ),
                    key=order,
                )
                selected.extend(compiles[:capacity])
        elif projected_frontiers == 0 and self._residual_reserved:
            # A reservation is an admission decision made while a frontier
            # was active.  If that frontier completes before the reserved
            # request is dispatched, hand the slot to the tagged SPEC rather
            # than letting an ordinary Compile consume it in the race window.
            speculative = sorted(
                (
                    record
                    for record in waiting
                    if record.speculative and record not in selected
                ),
                key=order,
            )
            if speculative:
                selected.append(speculative[0])
        elif projected_frontiers == 0:
            compiles = sorted(
                (
                    record
                    for record in waiting
                    if record.spec.kind is RequestKind.COMPILE
                    and not record.speculative
                ),
                key=order,
            )
            selected.extend(compiles[:capacity])

        return tuple(self._activate(record) for record in selected)

    def update_waiting_affinity(
        self,
        request_id: str,
        *,
        affinity_score: int,
        provider_recency: int,
        cohort_gain: int,
        affinity_signature: str,
    ) -> RequestSpec:
        record = self._record(request_id)
        if record.state != "WAITING":
            raise _fail("request_not_waiting")
        current = record.spec
        updated = RequestSpec(
            request_id=current.request_id,
            kind=current.kind,
            stream_id=current.stream_id,
            source_sequence=current.source_sequence,
            affinity_score=affinity_score,
            provider_recency=provider_recency,
            cohort_gain=cohort_gain,
            affinity_signature=affinity_signature,
            frontier_group=current.frontier_group,
        )
        record.spec = updated
        return updated

    def finish(self, request_id: str, *, outcome: str = "completed") -> None:
        record = self._record(request_id)
        if record.state != "ACTIVE" or request_id not in self._active:
            raise _fail("request_not_active")
        if outcome not in {"completed", "cancelled"}:
            raise _fail("outcome_invalid")
        if record.cancellation_requested and outcome != "cancelled":
            raise _fail("cancellation_outcome_invalid")
        terminal = outcome.upper()
        record.state = terminal
        self._active.remove(request_id)
        self._terminal_counts[terminal] += 1
        self._emit("request_finished", record)

    def fail(self, request_id: str, error: BaseException) -> None:
        record = self._record(request_id)
        if record.state != "ACTIVE" or request_id not in self._active:
            raise _fail("request_not_active")
        if not isinstance(error, BaseException):
            raise _fail("error_invalid")
        record.state = "FAILED"
        self._active.remove(request_id)
        self._terminal_counts["FAILED"] += 1
        self._emit(
            "request_failed",
            record,
            error_class=f"{type(error).__module__}.{type(error).__qualname__}",
        )

    def cancel(self, request_id: str) -> str:
        record = self._record(request_id)
        if record.state == "WAITING":
            record.state = "CANCELLED"
            self._terminal_counts["CANCELLED"] += 1
            self._emit("request_cancelled", record)
            return "CANCELLED"
        if record.state == "ACTIVE":
            if not record.cancellation_requested:
                record.cancellation_requested = True
                self._emit("cancellation_requested", record)
            return "CANCELLATION_REQUESTED"
        if record.state == "CANCELLED":
            return "CANCELLED"
        raise _fail("request_already_terminal")

    def observation(self) -> dict[str, object]:
        records = tuple(self._records.values())
        return {
            "active_count": len(self._active),
            "active_request_ids": sorted(self._active),
            "active_speculation_count": sum(
                record.state == "ACTIVE" and record.speculative for record in records
            ),
            "waiting_count": sum(record.state == "WAITING" for record in records),
            "waiting_compile_count": sum(
                record.state == "WAITING"
                and record.spec.kind is RequestKind.COMPILE
                and not record.speculative
                for record in records
            ),
            "waiting_speculation_count": sum(
                record.state == "WAITING" and record.speculative
                for record in records
            ),
            "residual_reserved": self._residual_reserved,
            "cancelled_count": self._terminal_counts["CANCELLED"],
            "completed_count": self._terminal_counts["COMPLETED"],
            "configured_limit": self._limit,
            "failed_count": self._terminal_counts["FAILED"],
            "observed_max_inflight": self._observed_max_inflight,
            "policy": self._policy.value,
        }

    @property
    def public_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)


class V4ResidualReservation:
    """Serialize reservation changes with the installed live admission lock."""

    def __init__(
        self,
        admitted_client: object,
        controller: V4ResidualRequestAdmissionController,
    ) -> None:
        lock = getattr(admitted_client, "_lock", None)
        if not isinstance(lock, asyncio.Lock):
            raise _fail("admission_lock_unavailable")
        if not callable(getattr(admitted_client, "_dispatch_locked", None)):
            raise _fail("admission_dispatch_unavailable")
        if not callable(
            getattr(admitted_client, "_emit_admission_snapshot_locked", None)
        ):
            raise _fail("admission_snapshot_unavailable")
        if not isinstance(controller, V4ResidualRequestAdmissionController):
            raise _fail("residual_controller_invalid")
        self._admitted = admitted_client
        self._controller = controller
        self._source_sequence: int | None = None

    async def reserve(self, source_sequence: int) -> None:
        if (
            isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 0
        ):
            raise _fail("source_sequence_invalid")
        async with self._admitted._lock:
            if self._source_sequence not in {None, source_sequence}:
                raise _fail("residual_reservation_busy")
            self._source_sequence = source_sequence
            self._controller.set_residual_reservation(True)
            self._admitted._dispatch_locked()
            self._admitted._emit_admission_snapshot_locked("V4_RESIDUAL_RESERVED")

    async def release(self, source_sequence: int) -> None:
        if (
            isinstance(source_sequence, bool)
            or not isinstance(source_sequence, int)
            or source_sequence < 0
        ):
            raise _fail("source_sequence_invalid")
        async with self._admitted._lock:
            if self._source_sequence is None:
                return
            if self._source_sequence != source_sequence:
                raise _fail("residual_reservation_source_mismatch")
            self._source_sequence = None
            self._controller.set_residual_reservation(False)
            self._admitted._dispatch_locked()
            self._admitted._emit_admission_snapshot_locked("V4_RESIDUAL_RELEASED")


def install_v4_residual_controller(admitted_client: object) -> V4ResidualReservation:
    """Replace an idle v3.1 controller inside one v4-only live runtime."""

    current = getattr(admitted_client, "_controller", None)
    policy = getattr(admitted_client, "_policy", None)
    waiters = getattr(admitted_client, "_waiters", None)
    observation = getattr(current, "observation", None)
    if not callable(observation) or not isinstance(waiters, dict) or waiters:
        raise _fail("admission_controller_not_idle")
    current_observation = observation()
    if (
        not isinstance(current_observation, dict)
        or current_observation.get("active_count") != 0
        or current_observation.get("waiting_count") != 0
        or current_observation.get("configured_limit") != 2
    ):
        raise _fail("admission_controller_not_idle")
    if not isinstance(policy, AdmissionPolicy):
        raise _fail("admission_policy_unavailable")
    controller = V4ResidualRequestAdmissionController(limit=2, policy=policy)
    admitted_client._controller = controller
    return V4ResidualReservation(admitted_client, controller)


__all__ = [
    "V4ResidualControllerError",
    "V4ResidualRequestAdmissionController",
    "V4ResidualReservation",
    "install_v4_residual_controller",
    "v4_speculative_transport_scope",
]
