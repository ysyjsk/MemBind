"""Frontier-first, resource-gated admission for the v4 lane.

The controller is intentionally small and non-preemptive.  It owns only
logical permits; transport cancellation is cooperative and must be reported
through :meth:`finish` or :meth:`cancel`, so a permit cannot silently leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from .resource_profile import Criticality, RequestProfile, ResourceClass


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class AdmissionDecision(str, Enum):
    ADMIT = "ADMIT"
    REJECT = "REJECT"


class RequestKind(str, Enum):
    FRONTIER = "FRONTIER"
    SPECULATIVE = "SPECULATIVE"


class V4AdmissionError(ValueError):
    """A resource-gate invariant failed closed."""


def _fail(code: str) -> V4AdmissionError:
    return V4AdmissionError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class AdmissionRequest:
    """A content-free admission candidate."""

    request_id: str
    kind: RequestKind
    stream_id: str
    source_sequence: int
    speculation_distance: int
    profile: RequestProfile

    def __post_init__(self) -> None:
        _identity(self.request_id, "request_id_invalid")
        _identity(self.stream_id, "stream_id_invalid")
        if not isinstance(self.kind, RequestKind):
            raise _fail("request_kind_invalid")
        if isinstance(self.source_sequence, bool) or not isinstance(self.source_sequence, int) or self.source_sequence < 0:
            raise _fail("source_sequence_invalid")
        if self.kind is RequestKind.SPECULATIVE:
            if self.speculation_distance != 1:
                raise _fail("speculation_distance_invalid")
            if self.profile.criticality is not Criticality.BACKGROUND:
                raise _fail("speculation_criticality_invalid")
        elif self.speculation_distance != 0:
            raise _fail("frontier_distance_invalid")
        if self.profile.source_sequence != self.source_sequence:
            raise _fail("profile_source_sequence_mismatch")


@dataclass(slots=True)
class _Record:
    request: AdmissionRequest
    enqueue_sequence: int
    state: str = "WAITING"
    cancellation_requested: bool = False


class ResourceGatedAdmission:
    """Global-K=2 frontier-first admission with optional c02 pairing gate."""

    def __init__(
        self,
        *,
        global_k: int = 2,
        phase_complementary: bool = False,
    ) -> None:
        if isinstance(global_k, bool) or not isinstance(global_k, int) or global_k != 2:
            raise _fail("global_k_must_equal_2")
        if not isinstance(phase_complementary, bool):
            raise _fail("phase_complementary_invalid")
        self._limit = global_k
        self._phase_complementary = phase_complementary
        self._records: dict[str, _Record] = {}
        self._active: set[str] = set()
        self._events: list[dict[str, object]] = []
        self._max_inflight = 0

    def _emit(self, event_type: str, record: _Record, **fields: object) -> None:
        request = record.request
        event: dict[str, object] = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            "request_id": request.request_id,
            "request_kind": request.kind.value,
            "stream_id": request.stream_id,
            "source_sequence": request.source_sequence,
            "state": record.state,
            "speculation_distance": request.speculation_distance,
            "resource_class": request.profile.resource_class.value,
            "criticality": request.profile.criticality.value,
            **fields,
        }
        self._events.append(event)

    def submit(self, request: AdmissionRequest) -> None:
        if not isinstance(request, AdmissionRequest):
            raise _fail("request_invalid")
        if request.request_id in self._records:
            raise _fail("request_id_duplicate")
        if request.kind is RequestKind.SPECULATIVE and any(
            row.request.kind is RequestKind.SPECULATIVE
            and row.request.stream_id == request.stream_id
            and row.request.source_sequence == request.source_sequence
            and row.state in {"WAITING", "ACTIVE"}
            for row in self._records.values()
        ):
            raise _fail("source_speculation_duplicate")
        record = _Record(request=request, enqueue_sequence=len(self._records))
        self._records[request.request_id] = record
        self._emit("request_waiting", record)

    @staticmethod
    def _frontier_key(record: _Record) -> tuple[int, int, str]:
        return (
            record.request.source_sequence,
            record.enqueue_sequence,
            record.request.request_id,
        )

    @staticmethod
    def _speculation_key(record: _Record) -> tuple[int, int, str]:
        return (
            record.request.source_sequence,
            record.enqueue_sequence,
            record.request.request_id,
        )

    def _activate(self, record: _Record) -> AdmissionRequest:
        record.state = "ACTIVE"
        self._active.add(record.request.request_id)
        self._max_inflight = max(self._max_inflight, len(self._active))
        self._emit("request_admitted", record)
        return record.request

    def _record(self, request_id: str) -> _Record:
        record = self._records.get(_identity(request_id, "request_id_invalid"))
        if record is None:
            raise _fail("request_unknown")
        return record

    def admit_available(self) -> tuple[AdmissionRequest, ...]:
        """Admit frontier work first, then at most one safe speculation.

        A waiting frontier always wins the residual slot.  Speculation is only
        allowed while exactly one frontier is active, which makes it a
        residual-slot user rather than a competing frontier worker.
        """

        capacity = self._limit - len(self._active)
        if capacity <= 0:
            return ()
        active_frontiers = [
            row
            for row in self._records.values()
            if row.state == "ACTIVE" and row.request.kind is RequestKind.FRONTIER
        ]
        waiting_frontiers = sorted(
            (
                row
                for row in self._records.values()
                if row.state == "WAITING" and row.request.kind is RequestKind.FRONTIER
            ),
            key=self._frontier_key,
        )
        selected: list[_Record] = []
        if waiting_frontiers and not active_frontiers:
            # One logical frontier at a time is part of the protocol.
            selected.append(waiting_frontiers[0])
            capacity -= 1
        if capacity <= 0:
            return tuple(self._activate(row) for row in selected)

        if waiting_frontiers or len(active_frontiers) != 1:
            return tuple(self._activate(row) for row in selected)

        candidates = sorted(
            (
                row
                for row in self._records.values()
                if row.state == "WAITING" and row.request.kind is RequestKind.SPECULATIVE
            ),
            key=self._speculation_key,
        )
        if not candidates:
            return tuple(self._activate(row) for row in selected)
        candidate = candidates[0]
        if self._phase_complementary:
            frontier_class = active_frontiers[0].request.profile.resource_class
            if (
                candidate.request.profile.resource_class is ResourceClass.LONG_PREFILL
                and frontier_class is ResourceClass.LONG_PREFILL
            ):
                self._emit(
                    "speculation_rejected",
                    candidate,
                    reason="LONG_PREFILL_LONG_PREFILL",
                )
                return tuple(self._activate(row) for row in selected)
        selected.append(candidate)
        return tuple(self._activate(row) for row in selected)

    def finish(self, request_id: str, *, outcome: str = "completed") -> None:
        record = self._record(request_id)
        if record.state != "ACTIVE" or request_id not in self._active:
            raise _fail("request_not_active")
        if outcome not in {"completed", "cancelled", "failed"}:
            raise _fail("outcome_invalid")
        if record.cancellation_requested and outcome != "cancelled":
            raise _fail("cancellation_outcome_invalid")
        record.state = outcome.upper()
        self._active.remove(request_id)
        self._emit("request_finished", record, outcome=outcome)

    def cancel(self, request_id: str) -> str:
        """Cancel waiting work immediately or active work cooperatively.

        Active cancellation is terminal in this logical controller: the
        transport owner must stop/await its task, while the permit is released
        now so another frontier cannot deadlock behind a cancelled request.
        """

        record = self._record(request_id)
        if record.state == "WAITING":
            record.state = "CANCELLED"
            self._emit("request_cancelled", record)
            return "CANCELLED"
        if record.state == "ACTIVE":
            record.cancellation_requested = True
            record.state = "CANCELLED"
            self._active.remove(request_id)
            self._emit("request_cancelled", record)
            return "CANCELLED"
        raise _fail("request_not_cancellable")

    def fail(self, request_id: str, error: BaseException) -> None:
        record = self._record(request_id)
        if record.state != "ACTIVE" or request_id not in self._active:
            raise _fail("request_not_active")
        if not isinstance(error, BaseException):
            raise _fail("error_invalid")
        record.state = "FAILED"
        self._active.remove(request_id)
        self._emit(
            "request_failed",
            record,
            error_class=f"{type(error).__module__}.{type(error).__qualname__}",
        )

    def observation(self) -> dict[str, object]:
        waiting = sum(row.state == "WAITING" for row in self._records.values())
        waiting_frontier = sum(
            row.state == "WAITING" and row.request.kind is RequestKind.FRONTIER
            for row in self._records.values()
        )
        return {
            "configured_global_k": self._limit,
            "active_count": len(self._active),
            "active_request_ids": tuple(sorted(self._active)),
            "waiting_count": waiting,
            "waiting_frontier_count": waiting_frontier,
            "observed_max_inflight": self._max_inflight,
            "speculation_active_count": sum(
                row.state == "ACTIVE" and row.request.kind is RequestKind.SPECULATIVE
                for row in self._records.values()
            ),
        }

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)


# Explicit controller spelling for callers that distinguish policy from gate.
ResourceGatedAdmissionController = ResourceGatedAdmission


__all__ = [
    "AdmissionDecision",
    "AdmissionRequest",
    "RequestKind",
    "ResourceGatedAdmission",
    "ResourceGatedAdmissionController",
    "V4AdmissionError",
]
