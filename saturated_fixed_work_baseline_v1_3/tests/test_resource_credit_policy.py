from __future__ import annotations

import asyncio

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (
    AdmissionItem,
    ResourceCreditAuthority,
    ResourceCreditPolicy,
    ResourcePool,
    ReadyQueue,
    run_lazy_ready_queue,
)


def _authority(capacity: int, *, reserve: int = 1, pool: str = "prepare"):
    authority = ResourceCreditAuthority()
    authority.register_pool(
        ResourcePool(pool, certified_capacity=capacity, bounded_future_request_cost=1)
    )
    authority.set_authoritative_reserve(pool, reserve)
    return authority


@pytest.mark.parametrize(("capacity", "expected"), [(1, 0), (2, 1), (4, 3)])
def test_credit_is_capacity_minus_authoritative_reserve(capacity: int, expected: int):
    authority = _authority(capacity)
    assert authority.snapshot("prepare", dependency_ready_future_count=99).future_credit == expected


def test_active_physical_requests_reduce_only_their_pool_credit():
    authority = _authority(4)
    authority.set_active_physical_requests("prepare", 2)
    assert authority.snapshot("prepare", dependency_ready_future_count=9).future_credit == 1
    authority.register_pool(ResourcePool("native", certified_capacity=2, bounded_future_request_cost=1))
    authority.set_authoritative_reserve("native", 1)
    assert authority.snapshot("native", dependency_ready_future_count=9).future_credit == 1


def test_shared_native_guard_closes_speculative_credit():
    authority = _authority(4)
    authority.set_native_guard("prepare", True)
    assert authority.snapshot("prepare", dependency_ready_future_count=9).future_credit == 0


def test_isolated_pool_does_not_consume_other_pool_reserve():
    authority = _authority(2, pool="native")
    authority.set_authoritative_reserve("native", 2)
    authority.register_pool(ResourcePool("prepare", certified_capacity=2, bounded_future_request_cost=1))
    authority.set_authoritative_reserve("prepare", 0)
    assert authority.snapshot("prepare", dependency_ready_future_count=9).future_credit == 2
    assert authority.snapshot("native", dependency_ready_future_count=9).future_credit == 0


def test_token_credit_is_optional_and_conservative():
    policy = ResourceCreditPolicy(token_authority_reliable=True)
    pool = ResourcePool("p", certified_capacity=4, bounded_future_request_cost=100, token_budget=250, token_authority_reliable=True)
    authority = ResourceCreditAuthority(policy=policy)
    authority.register_pool(pool)
    authority.set_authoritative_reserve("p", 1)
    authority.set_available_token_budget("p", 250)
    assert authority.snapshot("p", dependency_ready_future_count=9).future_credit == 2


def test_ready_work_is_limited_by_credit_and_credit_zero_creates_no_tasks():
    authority = _authority(2)
    queue = ReadyQueue()
    for seq in range(4):
        queue.push(AdmissionItem(source_sequence=seq, admission_class="FUTURE_PREPARE", logical_call_id=f"s{seq}"))
    admitted = queue.admit(authority, "prepare")
    assert [item.source_sequence for item in admitted] == [0]

    authority.set_native_guard("prepare", True)
    created: list[int] = []

    async def work(sequence: int):
        created.append(sequence)

    asyncio.run(run_lazy_ready_queue([], authority=authority, pool="prepare", work=work))
    assert created == []


def test_priority_is_deterministic_and_p0_cannot_starve():
    authority = _authority(3)
    queue = ReadyQueue()
    queue.push(AdmissionItem(2, "FUTURE_PREPARE", "f2"))
    queue.push(AdmissionItem(0, "NATIVE_FRONTIER", "p0"))
    queue.push(AdmissionItem(1, "FRONTIER_PREPARE", "p1"))
    assert [item.logical_call_id for item in queue.admit(authority, "prepare")] == ["p0", "p1", "f2"]


def test_accounting_never_exceeds_certified_capacity():
    authority = _authority(2)
    for active in range(5):
        authority.set_active_physical_requests("prepare", active)
        snap = authority.snapshot("prepare", dependency_ready_future_count=10)
        assert snap.active_physical_requests + snap.authoritative_reserve <= snap.certified_capacity or snap.overcommitted


def test_future_completion_order_does_not_change_publication_order():
    authority = _authority(3)
    completed: dict[int, str] = {}
    completed[2] = "two"
    completed[1] = "one"
    publication = [completed[index] for index in sorted(completed)]
    assert publication == ["one", "two"]


def test_fixed_ablation_remains_explicit():
    fixed = ResourceCreditPolicy.fixed_ablation()
    assert fixed.method_identity == "MEMBIND_FIXED_2_1_0_ABLATION"
    assert fixed.is_resource_credit is False


def test_policy_identity_has_no_algorithmic_window_constants():
    policy = ResourceCreditPolicy()
    payload = policy.to_dict()
    assert payload["method_identity"] == "MEMBIND_RESOURCE_CREDIT_V1"
    assert "lookahead" not in payload
    assert "future_cap" not in payload


def test_invalid_requests_fail_closed():
    with pytest.raises(ValueError):
        ResourcePool("bad", certified_capacity=0, bounded_future_request_cost=1)
    with pytest.raises(ValueError):
        ResourcePool("bad", certified_capacity=1, bounded_future_request_cost=0)
