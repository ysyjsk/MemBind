"""Deterministic request-level admission policies for MemBind v3.1.

Affinity is supplied by an adapter as a precomputed content-safe scalar and
signature.  This layer never sees prompts, tokenizes content, or contacts a
backend.  Admission is non-preemptive: cancellation of an active request is a
request to its owner, and its permit is released only at a terminal callback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SIGNATURE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class MemBindV31AdmissionError(ValueError):
    """A request-level admission invariant was violated."""


def _fail(code: str) -> MemBindV31AdmissionError:
    return MemBindV31AdmissionError(code)


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise _fail(code)
    return value


def _sequence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("source_sequence_invalid")
    return value


def _error_class(error: BaseException) -> str:
    if not isinstance(error, BaseException):
        raise _fail("error_invalid")
    return f"{type(error).__module__}.{type(error).__qualname__}"


class AdmissionPolicy(str, Enum):
    BARRIER = "BARRIER"
    FIFO = "FIFO"
    CACHE_AFFINE = "CACHE_AFFINE"


class RequestKind(str, Enum):
    FRONTIER = "FRONTIER"
    COMPILE = "COMPILE"


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """Content-free request scheduling metadata supplied by a live adapter."""

    request_id: str
    kind: RequestKind
    stream_id: str
    source_sequence: int
    affinity_score: int = 0
    provider_recency: int = 0
    cohort_gain: int = 0
    affinity_signature: str = "signature-none"
    # Multiple transport attempts may belong to one logical frontier bind.
    # Compile requests never carry this grouping identity.
    frontier_group: str | None = None

    def __post_init__(self) -> None:
        _identity(self.request_id, "request_id_invalid")
        if not isinstance(self.kind, RequestKind):
            raise _fail("request_kind_invalid")
        _identity(self.stream_id, "stream_id_invalid")
        _sequence(self.source_sequence)
        if (
            isinstance(self.affinity_score, bool)
            or not isinstance(self.affinity_score, int)
            or self.affinity_score < 0
        ):
            raise _fail("affinity_score_invalid")
        for value, code in (
            (self.provider_recency, "provider_recency_invalid"),
            (self.cohort_gain, "cohort_gain_invalid"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise _fail(code)
        if (
            not isinstance(self.affinity_signature, str)
            or _SIGNATURE.fullmatch(self.affinity_signature) is None
        ):
            raise _fail("affinity_signature_invalid")
        if self.frontier_group is not None:
            if self.kind is not RequestKind.FRONTIER:
                raise _fail("frontier_group_kind_invalid")
            if _IDENTITY.fullmatch(self.frontier_group) is None:
                raise _fail("frontier_group_invalid")


@dataclass(slots=True)
class _RequestRecord:
    spec: RequestSpec
    enqueue_sequence: int
    state: str = "WAITING"
    cancellation_requested: bool = False


class RequestAdmissionController:
    """Non-preemptive frontier-first admission over one global K limit."""

    def __init__(self, *, limit: int, policy: AdmissionPolicy) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise _fail("limit_invalid")
        if not isinstance(policy, AdmissionPolicy):
            raise _fail("policy_invalid")
        self._limit = limit
        self._policy = policy
        self._records: dict[str, _RequestRecord] = {}
        self._active: set[str] = set()
        self._events: list[dict[str, object]] = []
        self._observed_max_inflight = 0
        self._terminal_counts = {"CANCELLED": 0, "COMPLETED": 0, "FAILED": 0}

    def _emit(
        self,
        event_type: str,
        record: _RequestRecord,
        *,
        error_class: str | None = None,
    ) -> None:
        spec = record.spec
        event: dict[str, object] = {
            "event_sequence": len(self._events),
            "event_type": event_type,
            "request_id": spec.request_id,
            "request_kind": spec.kind.value,
            "request_state": record.state,
            "stream_id": spec.stream_id,
            "source_sequence": spec.source_sequence,
            "affinity_score": spec.affinity_score,
            "provider_recency": spec.provider_recency,
            "cohort_gain": spec.cohort_gain,
            "affinity_signature": spec.affinity_signature,
        }
        if error_class is not None:
            event["error_class"] = error_class
        self._events.append(event)

    def submit(self, spec: RequestSpec) -> None:
        if not isinstance(spec, RequestSpec):
            raise _fail("request_spec_invalid")
        if spec.request_id in self._records:
            raise _fail("request_id_duplicate")
        record = _RequestRecord(spec=spec, enqueue_sequence=len(self._records))
        self._records[spec.request_id] = record
        self._emit("request_waiting", record)

    @staticmethod
    def _frontier_key(record: _RequestRecord) -> tuple[int, str, int, str]:
        return (
            record.spec.source_sequence,
            record.spec.stream_id,
            record.enqueue_sequence,
            record.spec.request_id,
        )

    @staticmethod
    def _fifo_key(record: _RequestRecord) -> tuple[int, int, str, str]:
        return (
            record.enqueue_sequence,
            record.spec.source_sequence,
            record.spec.stream_id,
            record.spec.request_id,
        )

    @staticmethod
    def _cache_key(
        record: _RequestRecord,
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

    def update_waiting_affinity(
        self,
        request_id: str,
        *,
        affinity_score: int,
        provider_recency: int,
        cohort_gain: int,
        affinity_signature: str,
    ) -> RequestSpec:
        """Refresh oracle-derived metadata before the next admission."""

        record = self._record(request_id)
        if record.state != "WAITING":
            raise _fail("request_not_waiting")
        current = record.spec
        selected = RequestSpec(
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
        record.spec = selected
        return selected

    def _activate(self, record: _RequestRecord) -> RequestSpec:
        record.state = "ACTIVE"
        self._active.add(record.spec.request_id)
        self._observed_max_inflight = max(self._observed_max_inflight, len(self._active))
        self._emit("request_admitted", record)
        return record.spec

    def admit_available(self) -> tuple[RequestSpec, ...]:
        """Admit up to residual K with frontier semantics ahead of policy order."""

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
        waiting = [record for record in self._records.values() if record.state == "WAITING"]
        frontiers = sorted(
            (record for record in waiting if record.spec.kind is RequestKind.FRONTIER),
            key=self._frontier_key,
        )
        selected: list[_RequestRecord] = frontiers[:capacity]
        capacity -= len(selected)

        frontier_active = any(
            self._records[request_id].spec.kind is RequestKind.FRONTIER
            for request_id in self._active
        )
        barrier_holds = self._policy is AdmissionPolicy.BARRIER and (
            bool(frontiers) or frontier_active
        )
        if capacity > 0 and not barrier_holds:
            compiles = [
                record for record in waiting if record.spec.kind is RequestKind.COMPILE
            ]
            key = self._cache_key if self._policy is AdmissionPolicy.CACHE_AFFINE else self._fifo_key
            selected.extend(sorted(compiles, key=key)[:capacity])
        return tuple(self._activate(record) for record in selected)

    def _record(self, request_id: str) -> _RequestRecord:
        selected = _identity(request_id, "request_id_invalid")
        record = self._records.get(selected)
        if record is None:
            raise _fail("request_unknown")
        return record

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
        record.state = "FAILED"
        self._active.remove(request_id)
        self._terminal_counts["FAILED"] += 1
        self._emit("request_failed", record, error_class=_error_class(error))

    def cancel(self, request_id: str) -> str:
        """Cancel waiting work, or request cooperative completion of active work."""

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
        """Return content-safe counters and IDs in deterministic order."""

        waiting = sorted(
            record.spec.request_id
            for record in self._records.values()
            if record.state == "WAITING"
        )
        return {
            "active_count": len(self._active),
            "active_request_ids": sorted(self._active),
            "cancelled_count": self._terminal_counts["CANCELLED"],
            "completed_count": self._terminal_counts["COMPLETED"],
            "configured_limit": self._limit,
            "failed_count": self._terminal_counts["FAILED"],
            "observed_max_inflight": self._observed_max_inflight,
            "policy": self._policy.value,
            "waiting_count": len(waiting),
            "waiting_request_ids": waiting,
        }

    @property
    def public_events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(event) for event in self._events)


__all__ = [
    "AdmissionPolicy",
    "MemBindV31AdmissionError",
    "RequestAdmissionController",
    "RequestKind",
    "RequestSpec",
]
