"""Deterministic, provider-free resource-credit admission primitives.

The headline MemBind policy derives speculative admission from certified
capacity and the current resource state.  This module intentionally contains
no provider calls and no latency/quality feedback, making the method easy to
test and freeze before a campaign starts.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar, Iterable


@dataclass(frozen=True, slots=True)
class ResourceCreditPolicy:
    """Frozen resource-credit rules shared by all deployments.

    ``bounded_future_request_cost`` is an envelope from the structured-output
    contract, never an observed mean.  Token accounting is used only when the
    provider exposes an authoritative token/KV budget.
    """

    method_identity: str = "MEMBIND_RESOURCE_CREDIT_V1"
    bounded_future_request_cost: int = 1
    token_authority_reliable: bool = False
    is_resource_credit: bool = True
    MAX_ADMITTED_KV_TOKENS: ClassVar[int] = 61_440
    STRUCTURED_DECODE_RESERVE_TOKENS: ClassVar[int] = 4_096

    def __post_init__(self) -> None:
        if self.method_identity not in {
            "MEMBIND_RESOURCE_CREDIT_V1",
            "MEMBIND_FIXED_2_1_0_ABLATION",
        }:
            raise ValueError("unknown resource-credit method identity")
        if (
            isinstance(self.bounded_future_request_cost, bool)
            or not isinstance(self.bounded_future_request_cost, int)
            or self.bounded_future_request_cost <= 0
        ):
            raise ValueError("bounded_future_request_cost must be positive")
        if not isinstance(self.token_authority_reliable, bool):
            raise ValueError("token_authority_reliable must be boolean")
        if not isinstance(self.is_resource_credit, bool):
            raise ValueError("is_resource_credit must be boolean")
        if self.method_identity == "MEMBIND_RESOURCE_CREDIT_V1" and not self.is_resource_credit:
            raise ValueError("resource-credit identity must enable resource-credit mode")
        if self.method_identity == "MEMBIND_FIXED_2_1_0_ABLATION" and self.is_resource_credit:
            raise ValueError("fixed ablation cannot be resource-credit mode")

    @classmethod
    def fixed_ablation(cls) -> "ResourceCreditPolicy":
        return cls(
            method_identity="MEMBIND_FIXED_2_1_0_ABLATION",
            bounded_future_request_cost=1,
            token_authority_reliable=False,
            is_resource_credit=False,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.resource-credit-policy.v1",
            "method_identity": self.method_identity,
            "bounded_future_request_cost": self.bounded_future_request_cost,
            "token_authority_reliable": self.token_authority_reliable,
            "is_resource_credit": self.is_resource_credit,
            "formula": {
                "request_credit": "max(0, certified_capacity - active_physical_requests - authoritative_reserve)",
                "token_credit": "floor(max(0, available_token_budget - authoritative_token_reserve) / bounded_future_request_cost)",
                "future_credit": "min(dependency_ready_future_count, request_credit, token_credit when authoritative)",
            },
            "forbidden_inputs": ["latency_prediction", "quality", "arm_id", "observed_mean_cost"],
            "resource_model": {
                "kind": "per_pool_resource_credit",
                "token_budget": "authoritative_only",
                "structured_decode_reserve_tokens": self.STRUCTURED_DECODE_RESERVE_TOKENS,
            },
        }

    def token_budget(self, authority: int) -> int:
        """Compatibility envelope for the existing weighted permit seam."""
        if isinstance(authority, bool) or not isinstance(authority, int) or authority <= 0:
            raise ValueError("authority must be a positive integer")
        return min(authority * 8_192, self.MAX_ADMITTED_KV_TOKENS)


@dataclass(frozen=True, slots=True)
class ResourcePool:
    pool_id: str
    certified_capacity: int
    bounded_future_request_cost: int
    token_budget: int | None = None
    token_authority_reliable: bool = False
    shared_native_guard: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.pool_id, str) or not self.pool_id:
            raise ValueError("pool_id must be non-empty")
        for name in ("certified_capacity", "bounded_future_request_cost"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.token_budget is not None and (
            isinstance(self.token_budget, bool)
            or not isinstance(self.token_budget, int)
            or self.token_budget <= 0
        ):
            raise ValueError("token_budget must be positive when supplied")
        if self.token_authority_reliable and self.token_budget is None:
            raise ValueError("reliable token authority requires token_budget")


@dataclass(frozen=True, slots=True)
class ResourceCreditSnapshot:
    pool_id: str
    certified_capacity: int
    active_physical_requests: int
    authoritative_reserve: int
    bounded_future_request_cost: int
    dependency_ready_future_count: int
    request_credit: int
    token_credit: int | None
    future_credit: int
    native_guard_active: bool
    overcommitted: bool = False

    @property
    def active_plus_reserved(self) -> int:
        return self.active_physical_requests + self.authoritative_reserve

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "certified_capacity": self.certified_capacity,
            "active_physical_requests": self.active_physical_requests,
            "authoritative_reserve": self.authoritative_reserve,
            "bounded_future_request_cost": self.bounded_future_request_cost,
            "dependency_ready_future_count": self.dependency_ready_future_count,
            "request_credit": self.request_credit,
            "token_credit": self.token_credit,
            "future_credit": self.future_credit,
            "native_guard_active": self.native_guard_active,
            "overcommitted": self.overcommitted,
        }


class ResourceCreditAuthority:
    """Mutable runtime state over immutable, per-pool capacity facts."""

    def __init__(self, *, policy: ResourceCreditPolicy | None = None) -> None:
        self.policy = policy or ResourceCreditPolicy()
        self._pools: dict[str, ResourcePool] = {}
        self._active: dict[str, int] = {}
        self._reserve: dict[str, int] = {}
        self._available_tokens: dict[str, int] = {}
        self._native_guard: dict[str, bool] = {}
        self.events: list[dict[str, Any]] = []

    def register_pool(self, pool: ResourcePool) -> None:
        if pool.pool_id in self._pools:
            raise ValueError(f"pool already registered: {pool.pool_id}")
        self._pools[pool.pool_id] = pool
        self._active[pool.pool_id] = 0
        self._reserve[pool.pool_id] = 0
        self._native_guard[pool.pool_id] = False
        if pool.token_budget is not None:
            self._available_tokens[pool.pool_id] = pool.token_budget

    def _pool(self, pool_id: str) -> ResourcePool:
        try:
            return self._pools[pool_id]
        except KeyError as exc:
            raise KeyError(f"unknown resource pool: {pool_id}") from exc

    def set_active_physical_requests(self, pool_id: str, count: int) -> None:
        self._pool(pool_id)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("active physical request count must be non-negative")
        self._active[pool_id] = count

    def adjust_active_physical_requests(self, pool_id: str, delta: int) -> None:
        self.set_active_physical_requests(pool_id, self._active[pool_id] + int(delta))

    def set_authoritative_reserve(self, pool_id: str, count: int) -> None:
        pool = self._pool(pool_id)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("authoritative reserve must be non-negative")
        if count > pool.certified_capacity:
            raise ValueError("authoritative reserve exceeds certified capacity")
        self._reserve[pool_id] = count

    def set_available_token_budget(self, pool_id: str, count: int) -> None:
        pool = self._pool(pool_id)
        if not pool.token_authority_reliable:
            raise ValueError("token budget is not authoritative for this pool")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("available token budget must be non-negative")
        self._available_tokens[pool_id] = count

    def set_native_guard(self, pool_id: str, active: bool) -> None:
        self._pool(pool_id)
        if not isinstance(active, bool):
            raise ValueError("native guard must be boolean")
        self._native_guard[pool_id] = active

    def snapshot(self, pool_id: str, *, dependency_ready_future_count: int) -> ResourceCreditSnapshot:
        pool = self._pool(pool_id)
        if (
            isinstance(dependency_ready_future_count, bool)
            or not isinstance(dependency_ready_future_count, int)
            or dependency_ready_future_count < 0
        ):
            raise ValueError("dependency-ready count must be non-negative")
        active = self._active[pool_id]
        reserve = self._reserve[pool_id]
        request_credit = max(0, pool.certified_capacity - active - reserve)
        token_credit: int | None = None
        if pool.token_authority_reliable:
            available = self._available_tokens.get(pool_id, 0)
            token_credit = max(0, available // pool.bounded_future_request_cost)
        future_credit = min(dependency_ready_future_count, request_credit)
        if token_credit is not None:
            future_credit = min(future_credit, token_credit)
        guard = self._native_guard[pool_id] and pool.shared_native_guard
        if guard:
            future_credit = 0
        snap = ResourceCreditSnapshot(
            pool_id=pool_id,
            certified_capacity=pool.certified_capacity,
            active_physical_requests=active,
            authoritative_reserve=reserve,
            bounded_future_request_cost=pool.bounded_future_request_cost,
            dependency_ready_future_count=dependency_ready_future_count,
            request_credit=request_credit,
            token_credit=token_credit,
            future_credit=future_credit,
            native_guard_active=guard,
            overcommitted=active + reserve > pool.certified_capacity,
        )
        self.events.append({"event": "RESOURCE_CREDIT_SNAPSHOT", **snap.to_dict()})
        return snap

    def acquire_future(self, pool_id: str, *, dependency_ready_future_count: int = 1) -> ResourceCreditSnapshot:
        snap = self.snapshot(pool_id, dependency_ready_future_count=dependency_ready_future_count)
        if snap.future_credit <= 0:
            raise RuntimeError("future admission denied: no resource credit")
        self.adjust_active_physical_requests(pool_id, 1)
        return self.snapshot(pool_id, dependency_ready_future_count=dependency_ready_future_count - 1)

    def release_future(self, pool_id: str) -> None:
        if self._active[pool_id] <= 0:
            raise RuntimeError("future request release underflow")
        self.adjust_active_physical_requests(pool_id, -1)


@dataclass(frozen=True, slots=True)
class AdmissionItem:
    source_sequence: int
    admission_class: str
    logical_call_id: str
    payload: Any = None

    @property
    def priority(self) -> tuple[int, int, str]:
        rank = {"NATIVE_FRONTIER": 0, "FRONTIER_PREPARE": 1, "FUTURE_PREPARE": 2}.get(
            self.admission_class, 3
        )
        return (rank, int(self.source_sequence), str(self.logical_call_id))


class ReadyQueue:
    """Deterministic lazy queue; pushing never creates an asyncio task."""

    def __init__(self, items: Iterable[AdmissionItem] = ()) -> None:
        self._items: list[AdmissionItem] = list(items)

    def push(self, item: AdmissionItem) -> None:
        if not isinstance(item, AdmissionItem):
            raise TypeError("ReadyQueue accepts AdmissionItem")
        self._items.append(item)

    def __len__(self) -> int:
        return len(self._items)

    def admit(self, authority: ResourceCreditAuthority, pool_id: str) -> list[AdmissionItem]:
        ordered = sorted(self._items, key=lambda item: item.priority)
        credit = authority.snapshot(
            pool_id,
            dependency_ready_future_count=sum(
                item.admission_class == "FUTURE_PREPARE" for item in ordered
            ),
        ).future_credit
        admitted: list[AdmissionItem] = []
        for item in ordered:
            if item.admission_class != "FUTURE_PREPARE":
                admitted.append(item)
            elif credit > 0:
                admitted.append(item)
                credit -= 1
            if item in admitted:
                self._items.remove(item)
        return admitted


async def run_lazy_ready_queue(
    items: Iterable[AdmissionItem],
    *,
    authority: ResourceCreditAuthority,
    pool: str,
    work: Callable[[int], Awaitable[Any]],
) -> list[Any]:
    """Materialize at most the currently certified number of future tasks."""

    queue = ReadyQueue(items)
    admitted = queue.admit(authority, pool)
    tasks = [asyncio.create_task(work(item.source_sequence)) for item in admitted]
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks))


__all__ = [
    "AdmissionItem",
    "ReadyQueue",
    "ResourceCreditAuthority",
    "ResourceCreditPolicy",
    "ResourceCreditSnapshot",
    "ResourcePool",
    "run_lazy_ready_queue",
]
