"""Foreground-aware provider admission for V6.1."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from ..membind_v5.runtime.core.admission import (
    AdmissionClass,
    CapacityAuthority,
)
from .policy import V61Policy
from .resource_credit import ResourceCreditAuthority, ResourceCreditPolicy, ResourcePool


_PRIORITY = {
    AdmissionClass.NATIVE_FRONTIER: 0,
    AdmissionClass.FRONTIER_PREPARE: 1,
    AdmissionClass.FUTURE_PREPARE: 2,
}


@dataclass(frozen=True, slots=True)
class SourceLease:
    """Lease for one logical PREPARE source/frontier.

    A source lease controls future-source admission only.  It deliberately
    carries no provider-slot or token weight; those resources belong to the
    physical permits created by the transports expanded from this source.
    """

    lease_id: int
    source_sequence: int
    admission_class: AdmissionClass
    acquired_admission_class: AdmissionClass


@dataclass(frozen=True, slots=True)
class PhysicalPermit:
    """Permit for one real provider transport, including its token weight."""

    permit_id: int
    source_sequence: int
    admission_class: AdmissionClass
    acquired_admission_class: AdmissionClass
    request_tokens: int
    resource_id: str | None = None


class ForegroundAdmissionArbiter:
    """Share one provider budget while protecting native intervals.

    Future calls are admitted only while the guard is open and are capped by
    the policy.  When durable publication advances, an already-active future
    call for the newly exposed source is atomically promoted to frontier work;
    the request is not cancelled or resubmitted, but it no longer consumes the
    bounded future quota. Entering the native guard closes future admission and
    drains already-active future calls to ``native_future_quota`` before native
    work starts. Calls already submitted to FCFS vLLM cannot be preempted, so
    the drain is the enforceable boundary.
    """

    def __init__(
        self,
        authority: CapacityAuthority,
        *,
        policy: V61Policy | ResourceCreditPolicy,
        token_budget: int | None = None,
        name: str = "v6.1-shared-provider",
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        phase_isolated: bool = False,
        bootstrap_future_borrow: bool = False,
    ) -> None:
        if authority.value <= 0:
            raise ValueError("provider capacity must be positive")
        self.authority = authority
        self.policy = policy
        self.resource_credit_enabled = bool(getattr(policy, "is_resource_credit", False))
        self.token_budget = (
            policy.token_budget(authority.value) if token_budget is None else int(token_budget)
        )
        if self.token_budget <= 0:
            raise ValueError("token budget must be positive")
        self.name = name
        self.phase_isolated = bool(phase_isolated)
        self.bootstrap_future_borrow = bool(bootstrap_future_borrow)
        if self.bootstrap_future_borrow and not self.phase_isolated:
            raise ValueError("bootstrap future borrowing requires phase-isolated admission")
        self.instance_id = uuid.uuid4().hex
        self.event_sink = event_sink
        self._condition = asyncio.Condition()
        self._outstanding = 0
        self._future_outstanding = 0
        self._physical_future_outstanding = 0
        self._native_outstanding = 0
        self._tokens_outstanding = 0
        self._prepare_tokens_outstanding = 0
        self._native_tokens_outstanding = 0
        self._active_classes = {item: 0 for item in AdmissionClass}
        self._active_permits: dict[int, dict[str, Any]] = {}
        self._waiters: list[dict[str, Any]] = []
        self._source_leases: dict[int, dict[str, Any]] = {}
        self._source_waiters: list[dict[str, Any]] = []
        self._source_counter = 0
        self._source_outstanding = 0
        self._future_source_outstanding = 0
        self._counter = 0
        self._durable_frontier = -1
        self._native_guard_sequence: int | None = None
        self._events: list[dict[str, Any]] = []
        self.resource_credit_authority: ResourceCreditAuthority | None = None
        if self.resource_credit_enabled:
            self.resource_credit_authority = ResourceCreditAuthority(policy=policy)
            if self.phase_isolated:
                self.resource_credit_authority.register_pool(
                    ResourcePool(
                        "native",
                        certified_capacity=authority.value,
                        bounded_future_request_cost=policy.bounded_future_request_cost,
                        shared_native_guard=True,
                    )
                )
                self.resource_credit_authority.register_pool(
                    ResourcePool(
                        "prepare",
                        certified_capacity=authority.value,
                        bounded_future_request_cost=policy.bounded_future_request_cost,
                        shared_native_guard=False,
                    )
                )
                self.resource_credit_authority.set_authoritative_reserve("native", 1)
                self.resource_credit_authority.set_authoritative_reserve("prepare", 1)
            else:
                self.resource_credit_authority.register_pool(
                    ResourcePool(
                        "shared",
                        certified_capacity=authority.value,
                        bounded_future_request_cost=policy.bounded_future_request_cost,
                        shared_native_guard=True,
                    )
                )
                self.resource_credit_authority.set_authoritative_reserve("shared", 1)

    def _resource_pool_id(self, admission_class: AdmissionClass | None = None) -> str:
        if not self.resource_credit_enabled:
            return ""
        if self.phase_isolated:
            return "native" if admission_class is AdmissionClass.NATIVE_FRONTIER else "prepare"
        return "shared"

    def _resource_snapshot(self, admission_class: AdmissionClass, ready: int = 1):
        if self.resource_credit_authority is None:
            return None
        pool_id = self._resource_pool_id(admission_class)
        if self.phase_isolated:
            active = (
                self._native_outstanding
                if pool_id == "native"
                else self.prepare_outstanding
            )
        else:
            active = self._outstanding
        self.resource_credit_authority.set_active_physical_requests(pool_id, max(0, int(active)))
        self.resource_credit_authority.set_native_guard(
            pool_id,
            self.native_guard_active and pool_id == "shared",
        )
        return self.resource_credit_authority.snapshot(
            pool_id, dependency_ready_future_count=max(0, int(ready))
        )

    def future_credit(self, *, dependency_ready_future_count: int = 1, pool_id: str | None = None) -> int:
        """Return current future credit without creating a task or call."""
        if self.resource_credit_authority is None:
            return max(0, int(self.policy.future_cap) - self._future_outstanding)
        selected_pool = pool_id or ("prepare" if self.phase_isolated else "shared")
        # Keep authoritative state synchronized with real active physical calls.
        if self.phase_isolated:
            active = self.prepare_outstanding if selected_pool == "prepare" else self._native_outstanding
        else:
            active = self._outstanding
        self.resource_credit_authority.set_active_physical_requests(selected_pool, max(0, int(active)))
        self.resource_credit_authority.set_native_guard(
            selected_pool,
            self.native_guard_active and selected_pool == "shared",
        )
        return self.resource_credit_authority.snapshot(
            selected_pool, dependency_ready_future_count=max(0, int(dependency_ready_future_count))
        ).future_credit

    @property
    def outstanding(self) -> int:
        return self._outstanding

    @property
    def future_outstanding(self) -> int:
        return self._future_outstanding

    @property
    def physical_future_outstanding(self) -> int:
        return self._physical_future_outstanding

    @property
    def tokens_outstanding(self) -> int:
        return self._tokens_outstanding

    @property
    def native_outstanding(self) -> int:
        return self._native_outstanding

    @property
    def prepare_outstanding(self) -> int:
        return self._outstanding - self._native_outstanding

    @property
    def prepare_tokens_outstanding(self) -> int:
        return self._prepare_tokens_outstanding

    @property
    def native_tokens_outstanding(self) -> int:
        return self._native_tokens_outstanding

    @property
    def native_guard_active(self) -> bool:
        return self._native_guard_sequence is not None

    @property
    def waiter_count(self) -> int:
        return len(self._waiters)

    @property
    def source_outstanding(self) -> int:
        return self._source_outstanding

    @property
    def future_source_outstanding(self) -> int:
        return self._future_source_outstanding

    @property
    def source_waiter_count(self) -> int:
        return len(self._source_waiters)

    def _emit(self, event: str, **fields: Any) -> None:
        row = {
            "schema_version": "membind.v6.1.admission-event.v5",
            "event": event,
            "arbiter": self.name,
            "arbiter_instance_id": self.instance_id,
            "monotonic_ns": time.monotonic_ns(),
            "outstanding": self._outstanding,
            "future_outstanding": self._future_outstanding,
            "physical_future_outstanding": self._physical_future_outstanding,
            "native_outstanding": self._native_outstanding,
            "prepare_outstanding": self.prepare_outstanding,
            "tokens_outstanding": self._tokens_outstanding,
            "prepare_tokens_outstanding": self._prepare_tokens_outstanding,
            "native_tokens_outstanding": self._native_tokens_outstanding,
            "token_budget": self.token_budget,
            "provider_capacity": self.authority.value,
            "phase_isolated": self.phase_isolated,
            "bootstrap_future_borrow": self.bootstrap_future_borrow,
            "durable_frontier": self._durable_frontier,
            "resource_model": self.policy.to_dict().get(
                "resource_model",
                {
                    "kind": "per_pool_resource_credit",
                    "dimensions": ["certified_physical_requests"],
                    "token_authority_reliable": getattr(
                        self.policy, "token_authority_reliable", False
                    ),
                },
            ),
            "future_cap": getattr(self.policy, "future_cap", None),
            "native_guard_active": self.native_guard_active,
            "native_guard_sequence": self._native_guard_sequence,
            "source_outstanding": self._source_outstanding,
            "future_source_outstanding": self._future_source_outstanding,
            "source_waiter_count": len(self._source_waiters),
            **fields,
        }
        self._events.append(row)
        if self.event_sink is not None:
            self.event_sink(dict(row))

    @staticmethod
    def _coerce(value: AdmissionClass | str) -> AdmissionClass:
        return value if isinstance(value, AdmissionClass) else AdmissionClass(value)

    async def acquire_source_lease(
        self,
        admission_class: AdmissionClass,
        *,
        source_sequence: int,
        class_resolver: Callable[[], AdmissionClass] | None = None,
    ) -> SourceLease:
        """Acquire one logical source lease without consuming physical budget."""

        admission_class = self._coerce(admission_class)
        if admission_class is AdmissionClass.NATIVE_FRONTIER:
            raise ValueError("source leases are only valid for PREPARE sources")
        async with self._condition:
            waiter = {
                "lease_id": self._source_counter,
                "enqueued_ns": time.monotonic_ns(),
                "source_sequence": int(source_sequence),
                "admission_class": admission_class,
            }
            self._source_counter += 1
            self._source_waiters.append(waiter)
            self._emit(
                "SOURCE_LEASE_ENQUEUE",
                lease_id=waiter["lease_id"],
                source_sequence=waiter["source_sequence"],
                admission_class=admission_class.value,
            )
            try:
                while True:
                    if class_resolver is not None:
                        resolved = self._coerce(class_resolver())
                        if resolved is AdmissionClass.NATIVE_FRONTIER:
                            raise ValueError("source lease resolver returned NATIVE_FRONTIER")
                        if resolved != waiter["admission_class"]:
                            waiter["admission_class"] = resolved
                            self._emit(
                                "SOURCE_LEASE_RECLASSIFY",
                                lease_id=waiter["lease_id"],
                                source_sequence=waiter["source_sequence"],
                                admission_class=resolved.value,
                            )
                    self._source_waiters.sort(
                        key=lambda item: (
                            _PRIORITY[item["admission_class"]],
                            item["source_sequence"],
                            item["lease_id"],
                        )
                    )
                    current_class = waiter["admission_class"]
                    at_head = bool(self._source_waiters) and self._source_waiters[0] is waiter
                    future_allowed = (
                        current_class is not AdmissionClass.FUTURE_PREPARE
                        or (
                            (self.phase_isolated or not self.native_guard_active)
                            and (
                                self.future_credit(dependency_ready_future_count=1) > 0
                                if self.resource_credit_enabled
                                else self._future_source_outstanding < getattr(self.policy, "future_cap", 0)
                            )
                        )
                    )
                    if (
                        at_head
                        and self._source_outstanding < self.authority.value
                        and future_allowed
                    ):
                        self._source_waiters.remove(waiter)
                        self._source_outstanding += 1
                        if current_class is AdmissionClass.FUTURE_PREPARE:
                            self._future_source_outstanding += 1
                        self._source_leases[waiter["lease_id"]] = {
                            "source_sequence": waiter["source_sequence"],
                            "admission_class": current_class,
                            "acquired_admission_class": current_class,
                        }
                        self._emit(
                            "SOURCE_LEASE_ADMIT",
                            lease_id=waiter["lease_id"],
                            source_sequence=waiter["source_sequence"],
                            admission_class=current_class.value,
                            queue_wait_ns=time.monotonic_ns() - waiter["enqueued_ns"],
                        )
                        return SourceLease(
                            lease_id=int(waiter["lease_id"]),
                            source_sequence=int(waiter["source_sequence"]),
                            admission_class=current_class,
                            acquired_admission_class=current_class,
                        )
                    await self._condition.wait()
            except BaseException:
                if waiter in self._source_waiters:
                    self._source_waiters.remove(waiter)
                self._emit(
                    "SOURCE_LEASE_CANCEL",
                    lease_id=waiter["lease_id"],
                    source_sequence=waiter["source_sequence"],
                    admission_class=waiter["admission_class"].value,
                )
                self._condition.notify_all()
                raise

    async def release_source_lease(self, lease: SourceLease) -> None:
        if not isinstance(lease, SourceLease):
            raise TypeError("release_source_lease requires a SourceLease")
        async with self._condition:
            state = self._source_leases.pop(int(lease.lease_id), None)
            if state is None:
                raise RuntimeError("source lease release has no matching lease")
            acquired_class = state["acquired_admission_class"]
            if acquired_class is not lease.acquired_admission_class:
                raise RuntimeError("source lease release class mismatch")
            self._source_outstanding -= 1
            if state["admission_class"] is AdmissionClass.FUTURE_PREPARE:
                self._future_source_outstanding -= 1
            self._emit(
                "SOURCE_LEASE_RELEASE",
                lease_id=int(lease.lease_id),
                source_sequence=int(state["source_sequence"]),
                admission_class=state["admission_class"].value,
                acquired_admission_class=acquired_class.value,
            )
            self._condition.notify_all()

    async def acquire(
        self,
        admission_class: AdmissionClass,
        *,
        source_sequence: int = 0,
        request_tokens: int = 1,
        prompt_tokens: int | None = None,
        decode_reserve_tokens: int | None = None,
        class_resolver: Callable[[], AdmissionClass] | None = None,
        _return_permit: bool = False,
        _resource_id: str | None = None,
    ) -> AdmissionClass | PhysicalPermit:
        admission_class = self._coerce(admission_class)
        if (
            isinstance(request_tokens, bool)
            or not isinstance(request_tokens, int)
            or request_tokens <= 0
        ):
            raise ValueError("request_tokens must be a positive integer")
        async with self._condition:
            waiter = {
                "ticket": self._counter,
                "enqueued_ns": time.monotonic_ns(),
                "source_sequence": int(source_sequence),
                "admission_class": admission_class,
                "request_tokens": request_tokens,
                "prompt_tokens": prompt_tokens,
                "decode_reserve_tokens": decode_reserve_tokens,
                "resource_id": _resource_id,
            }
            self._counter += 1
            self._waiters.append(waiter)
            self._emit(
                "ADMISSION_ENQUEUE",
                ticket=waiter["ticket"],
                source_sequence=waiter["source_sequence"],
                admission_class=admission_class.value,
            )
            try:
                while True:
                    if class_resolver is not None:
                        resolved = self._coerce(class_resolver())
                        if resolved != waiter["admission_class"]:
                            waiter["admission_class"] = resolved
                            self._emit(
                                "ADMISSION_RECLASSIFY",
                                ticket=waiter["ticket"],
                                source_sequence=waiter["source_sequence"],
                                admission_class=resolved.value,
                            )
                    self._waiters.sort(
                        key=lambda item: (
                            _PRIORITY[item["admission_class"]],
                            item["source_sequence"],
                            item["ticket"],
                        )
                    )
                    current_class = waiter["admission_class"]
                    if self.phase_isolated:
                        native_resource = current_class is AdmissionClass.NATIVE_FRONTIER
                        resource_waiters = [
                            item
                            for item in self._waiters
                            if (item["admission_class"] is AdmissionClass.NATIVE_FRONTIER)
                            == native_resource
                        ]
                        at_head = bool(resource_waiters) and resource_waiters[0] is waiter
                        resource_outstanding = (
                            self._native_outstanding
                            if native_resource
                            else self.prepare_outstanding
                        )
                        capacity_available = resource_outstanding < self.authority.value
                        resource_tokens = (
                            self._native_tokens_outstanding
                            if native_resource
                            else self._prepare_tokens_outstanding
                        )
                    else:
                        at_head = bool(self._waiters) and self._waiters[0] is waiter
                        capacity_available = self._outstanding < self.authority.value
                        resource_outstanding = self._outstanding
                        resource_tokens = self._tokens_outstanding
                    bootstrap_limit = (
                        getattr(self.policy, "future_cap", 0) + 1
                        if not self.resource_credit_enabled
                        else 0
                    )
                    bootstrap_available = (
                        self.bootstrap_future_borrow
                        and getattr(self.policy, "future_cap", 0) > 0
                        and self._durable_frontier < 0
                        and not self.native_guard_active
                        and self._future_outstanding < bootstrap_limit
                    )
                    future_allowed = (
                        current_class is not AdmissionClass.FUTURE_PREPARE
                        or (
                            (self.phase_isolated or not self.native_guard_active)
                            and (
                                (
                                    _return_permit
                                    and any(
                                        int(lease["source_sequence"])
                                        == int(waiter["source_sequence"])
                                        for lease in self._source_leases.values()
                                    )
                                )
                                or (
                                    not _return_permit
                                    and (
                                        (
                                            self.future_credit(dependency_ready_future_count=1) > 0
                                            if self.resource_credit_enabled
                                            else self._future_outstanding < getattr(self.policy, "future_cap", 0)
                                        ) or bootstrap_available
                                    )
                                )
                            )
                        )
                    )
                    token_capacity_available = (
                        resource_tokens + request_tokens <= self.token_budget
                        or (
                            request_tokens > self.token_budget
                            and resource_outstanding == 0
                            and resource_tokens == 0
                        )
                    )
                    if (
                        at_head
                        and capacity_available
                        and future_allowed
                        and token_capacity_available
                    ):
                        bootstrap_borrowed = (
                            current_class is AdmissionClass.FUTURE_PREPARE
                            and not _return_permit
                            and not self.resource_credit_enabled
                            and self._future_outstanding >= getattr(self.policy, "future_cap", 0)
                        )
                        self._waiters.remove(waiter)
                        self._outstanding += 1
                        self._active_classes[current_class] += 1
                        if current_class is AdmissionClass.FUTURE_PREPARE:
                            if _return_permit:
                                self._physical_future_outstanding += 1
                            else:
                                self._future_outstanding += 1
                        if current_class is AdmissionClass.NATIVE_FRONTIER:
                            self._native_outstanding += 1
                            self._native_tokens_outstanding += request_tokens
                        else:
                            self._prepare_tokens_outstanding += request_tokens
                        self._tokens_outstanding += request_tokens
                        self._active_permits[waiter["ticket"]] = {
                            "admission_class": current_class,
                            "acquired_admission_class": current_class,
                            "source_sequence": waiter["source_sequence"],
                            "request_tokens": request_tokens,
                            "bootstrap_borrowed": bootstrap_borrowed,
                            "physical": bool(_return_permit),
                            "resource_id": _resource_id,
                        }
                        self._emit(
                            "ADMISSION_ADMIT",
                            ticket=waiter["ticket"],
                            source_sequence=waiter["source_sequence"],
                            admission_class=current_class.value,
                            request_tokens=request_tokens,
                            prompt_tokens=prompt_tokens,
                            decode_reserve_tokens=decode_reserve_tokens,
                            resource_id=_resource_id,
                            queue_wait_ns=time.monotonic_ns() - waiter["enqueued_ns"],
                            oversized_request=request_tokens > self.token_budget,
                            bootstrap_borrowed=bootstrap_borrowed,
                        )
                        if _return_permit:
                            return PhysicalPermit(
                                permit_id=int(waiter["ticket"]),
                                source_sequence=int(waiter["source_sequence"]),
                                admission_class=current_class,
                                acquired_admission_class=current_class,
                                request_tokens=int(request_tokens),
                                resource_id=_resource_id,
                            )
                        return current_class
                    await self._condition.wait()
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._emit(
                    "ADMISSION_CANCEL",
                    ticket=waiter["ticket"],
                    source_sequence=waiter["source_sequence"],
                    admission_class=waiter["admission_class"].value,
                )
                self._condition.notify_all()
                raise

    async def acquire_physical(
        self,
        admission_class: AdmissionClass,
        *,
        source_sequence: int = 0,
        request_tokens: int = 1,
        prompt_tokens: int | None = None,
        decode_reserve_tokens: int | None = None,
        class_resolver: Callable[[], AdmissionClass] | None = None,
        endpoint_id: str | None = None,
    ) -> PhysicalPermit:
        """Acquire one weighted permit for one physical provider transport."""

        permit = await self.acquire(
            admission_class,
            source_sequence=source_sequence,
            request_tokens=request_tokens,
            prompt_tokens=prompt_tokens,
            decode_reserve_tokens=decode_reserve_tokens,
            class_resolver=class_resolver,
            _return_permit=True,
            _resource_id=endpoint_id,
        )
        if not isinstance(permit, PhysicalPermit):
            raise RuntimeError("physical admission returned a non-physical permit")
        return permit

    async def release_physical(self, permit: PhysicalPermit) -> None:
        """Release the exact physical permit returned by acquire_physical."""

        if not isinstance(permit, PhysicalPermit):
            raise TypeError("release_physical requires a PhysicalPermit")
        await self.release(
            permit.acquired_admission_class,
            permit_id=permit.permit_id,
            request_tokens=permit.request_tokens,
        )

    async def release(
        self,
        admission_class: AdmissionClass,
        *,
        permit_id: int | None = None,
        request_tokens: int | None = None,
    ) -> None:
        acquired_class = self._coerce(admission_class)
        async with self._condition:
            if self._outstanding <= 0:
                raise RuntimeError("admission release without a matching permit")
            if permit_id is None:
                matching = [
                    ticket
                    for ticket, permit in self._active_permits.items()
                    if permit["acquired_admission_class"] is acquired_class
                    and (
                        request_tokens is None
                        or int(permit["request_tokens"]) == int(request_tokens)
                    )
                ]
                if not matching:
                    raise RuntimeError("admission release has no matching weighted permit")
                if request_tokens is None and len(matching) != 1:
                    raise RuntimeError("concurrent admission release requires its permit id")
                permit_id = matching[0]
            permit = self._active_permits.pop(int(permit_id), None)
            if (
                permit is None
                or permit["acquired_admission_class"] is not acquired_class
            ):
                raise RuntimeError("admission release permit mismatch")
            current_class = permit["admission_class"]
            self._outstanding -= 1
            self._active_classes[current_class] -= 1
            request_tokens = int(permit["request_tokens"])
            if current_class is AdmissionClass.FUTURE_PREPARE:
                if permit.get("physical"):
                    self._physical_future_outstanding -= 1
                else:
                    self._future_outstanding -= 1
            if current_class is AdmissionClass.NATIVE_FRONTIER:
                self._native_outstanding -= 1
                self._native_tokens_outstanding -= request_tokens
            else:
                self._prepare_tokens_outstanding -= request_tokens
            self._tokens_outstanding -= request_tokens
            self._emit(
                "ADMISSION_RELEASE",
                admission_class=current_class.value,
                acquired_admission_class=acquired_class.value,
                permit_id=int(permit_id),
                source_sequence=int(permit["source_sequence"]),
                request_tokens=request_tokens,
                bootstrap_borrowed=bool(permit["bootstrap_borrowed"]),
                resource_id=permit.get("resource_id"),
            )
            self._condition.notify_all()

    async def enter_native_guard(self, source_sequence: int) -> None:
        async with self._condition:
            if self.native_guard_active:
                raise RuntimeError("native guard is already active")
            self._native_guard_sequence = int(source_sequence)
            self._emit("NATIVE_GUARD_ENTER", source_sequence=int(source_sequence))
            self._condition.notify_all()
            if self.phase_isolated:
                self._emit(
                    "NATIVE_GUARD_READY",
                    source_sequence=int(source_sequence),
                    active_future_calls=(
                        self._future_outstanding + self._physical_future_outstanding
                    ),
                    drained_future_calls=0,
                    isolation_reason="distinct_physical_llm_endpoint",
                )
                return
            # vLLM cannot preempt an already-submitted long prefill without
            # recomputation, and live evidence shows that co-residency inflates
            # small native calls by an order of magnitude even below the KV
            # ceiling.  Drain only the in-flight future request, then keep the
            # future lane closed until native publication completes.
            active_on_enter = (
                self._future_outstanding + self._physical_future_outstanding
            )
            while self._future_outstanding or self._physical_future_outstanding:
                self._emit(
                    "NATIVE_GUARD_INTERFERENCE_DRAIN",
                    source_sequence=int(source_sequence),
                    active_future_calls=(
                        self._future_outstanding + self._physical_future_outstanding
                    ),
                )
                try:
                    await self._condition.wait()
                except BaseException:
                    self._native_guard_sequence = None
                    self._emit("NATIVE_GUARD_CANCEL", source_sequence=int(source_sequence))
                    self._condition.notify_all()
                    raise
            self._emit(
                "NATIVE_GUARD_READY",
                source_sequence=int(source_sequence),
                active_future_calls=(
                    self._future_outstanding + self._physical_future_outstanding
                ),
                drained_future_calls=active_on_enter,
            )

    async def exit_native_guard(self, source_sequence: int) -> None:
        async with self._condition:
            if self._native_guard_sequence != int(source_sequence):
                raise RuntimeError("native guard source mismatch")
            self._emit("NATIVE_GUARD_EXIT", source_sequence=int(source_sequence))
            self._native_guard_sequence = None
            self._condition.notify_all()

    async def frontier_advanced(self, source_sequence: int) -> None:
        async with self._condition:
            published_sequence = int(source_sequence)
            if published_sequence != self._durable_frontier + 1:
                raise RuntimeError("durable frontier advance is not contiguous")
            self._durable_frontier = published_sequence
            self._emit("FRONTIER_ADVANCE", source_sequence=published_sequence)
            newly_exposed = published_sequence + 1
            for ticket, permit in sorted(self._active_permits.items()):
                if (
                    permit["admission_class"] is not AdmissionClass.FUTURE_PREPARE
                    or int(permit["source_sequence"]) != newly_exposed
                ):
                    continue
                permit["admission_class"] = AdmissionClass.FRONTIER_PREPARE
                self._active_classes[AdmissionClass.FUTURE_PREPARE] -= 1
                self._active_classes[AdmissionClass.FRONTIER_PREPARE] += 1
                if permit.get("physical"):
                    self._physical_future_outstanding -= 1
                else:
                    self._future_outstanding -= 1
                self._emit(
                    "ADMISSION_ACTIVE_RECLASSIFY",
                    ticket=int(ticket),
                    source_sequence=newly_exposed,
                    trigger_frontier_sequence=published_sequence,
                    from_admission_class=AdmissionClass.FUTURE_PREPARE.value,
                    admission_class=AdmissionClass.FRONTIER_PREPARE.value,
                    acquired_admission_class=permit[
                        "acquired_admission_class"
                    ].value,
                    request_tokens=int(permit["request_tokens"]),
                )
            for lease_id, lease in sorted(self._source_leases.items()):
                if (
                    lease["admission_class"] is not AdmissionClass.FUTURE_PREPARE
                    or int(lease["source_sequence"]) != newly_exposed
                ):
                    continue
                lease["admission_class"] = AdmissionClass.FRONTIER_PREPARE
                self._future_source_outstanding -= 1
                self._emit(
                    "SOURCE_LEASE_ACTIVE_RECLASSIFY",
                    lease_id=int(lease_id),
                    source_sequence=newly_exposed,
                    trigger_frontier_sequence=published_sequence,
                    from_admission_class=AdmissionClass.FUTURE_PREPARE.value,
                    admission_class=AdmissionClass.FRONTIER_PREPARE.value,
                    acquired_admission_class=lease[
                        "acquired_admission_class"
                    ].value,
                )
            self._condition.notify_all()

    async def preparation_frontier_advanced(self, source_sequence: int) -> None:
        """Wake queued capture calls after ordered preparation progresses."""
        async with self._condition:
            self._emit(
                "PREPARATION_FRONTIER_ADVANCE",
                source_sequence=int(source_sequence),
            )
            self._condition.notify_all()

    def evidence(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v6.1.admission-evidence.v5",
            "arbiter": self.name,
            "arbiter_instance_id": self.instance_id,
            "capacity": self.authority.to_dict(),
            "policy": self.policy.to_dict(),
            "outstanding": self._outstanding,
            "future_outstanding": self._future_outstanding,
            "physical_future_outstanding": self._physical_future_outstanding,
            "native_outstanding": self._native_outstanding,
            "prepare_outstanding": self.prepare_outstanding,
            "tokens_outstanding": self._tokens_outstanding,
            "prepare_tokens_outstanding": self._prepare_tokens_outstanding,
            "native_tokens_outstanding": self._native_tokens_outstanding,
            "token_budget": self.token_budget,
            "provider_capacity": self.authority.value,
            "phase_isolated": self.phase_isolated,
            "bootstrap_future_borrow": self.bootstrap_future_borrow,
            "durable_frontier": self._durable_frontier,
            "native_guard_active": self.native_guard_active,
            "source_outstanding": self._source_outstanding,
            "future_source_outstanding": self._future_source_outstanding,
            "source_waiters": [
                {
                    "lease_id": item["lease_id"],
                    "source_sequence": item["source_sequence"],
                    "admission_class": item["admission_class"].value,
                }
                for item in self._source_waiters
            ],
            "active_source_leases": [
                {
                    "lease_id": lease_id,
                    "source_sequence": lease["source_sequence"],
                    "admission_class": lease["admission_class"].value,
                    "acquired_admission_class": lease[
                        "acquired_admission_class"
                    ].value,
                }
                for lease_id, lease in sorted(self._source_leases.items())
            ],
            "waiters": [
                {
                    "ticket": item["ticket"],
                    "source_sequence": item["source_sequence"],
                    "admission_class": item["admission_class"].value,
                    "request_tokens": item["request_tokens"],
                }
                for item in self._waiters
            ],
            "active_permits": [
                {
                    "permit_id": ticket,
                    "admission_class": permit["admission_class"].value,
                    "request_tokens": permit["request_tokens"],
                }
                for ticket, permit in sorted(self._active_permits.items())
            ],
            "events": list(self._events),
        }


__all__ = ["ForegroundAdmissionArbiter", "PhysicalPermit", "SourceLease"]
