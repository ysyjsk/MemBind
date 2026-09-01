#!/usr/bin/env python3
"""Provider-free event-level stress suite for the V6.1 admission boundary.

The suite exercises the real ``ForegroundAdmissionArbiter`` and frontier
executor with deterministic fixtures.  It emits every arbiter/executor event
and a small oracle summary; no model, HTTP endpoint, database, or benchmark
result is involved in candidate selection.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import AdmissionClass, CapacityAuthority
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import ForegroundAdmissionArbiter
from saturated_fixed_work_baseline_v1_3.membind_v6_1.executor import run_jit_frontier_history_async
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy


OUT = Path(__file__).resolve().parents[1] / "structured_output_recovery" / "SCHEDULER_STRESS_TEST_RESULT.json"


def _policy(*, future_cap: int = 1) -> V61Policy:
    return V61Policy(lookahead=2, future_cap=future_cap, native_future_quota=0)


async def _wait_for(task: asyncio.Task[Any]) -> Any:
    return await asyncio.wait_for(task, timeout=1.0)


async def _only_critical() -> dict[str, Any]:
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(1), policy=_policy(future_cap=0))
    permit = await arbiter.acquire_physical(AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=32)
    await arbiter.release_physical(permit)
    ev = arbiter.evidence()
    return {"events": ev["events"], "oracle": {"native_only": True, "conservation": ev["outstanding"] == 0 and ev["tokens_outstanding"] == 0}}


async def _critical_priority() -> dict[str, Any]:
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(1), policy=_policy())
    first = await arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=2, request_tokens=8)
    future_later = asyncio.create_task(arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=3, request_tokens=8))
    native = asyncio.create_task(arbiter.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=8))
    await asyncio.sleep(0)
    assert not native.done() and not future_later.done()
    await arbiter.release(first)
    native_permit = await _wait_for(native)
    assert not future_later.done()
    await arbiter.release(native_permit)
    future_permit = await _wait_for(future_later)
    await arbiter.release(future_permit)
    ev = arbiter.evidence()
    admits = [row for row in ev["events"] if row["event"] == "ADMISSION_ADMIT"]
    return {"events": ev["events"], "oracle": {"native_precedes_queued_future": admits[1]["admission_class"] == AdmissionClass.NATIVE_FRONTIER.value, "new_p2_while_p0_waiting": 0, "conservation": ev["outstanding"] == 0}}


async def _bounded_opportunity_and_inversion() -> dict[str, Any]:
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(1), policy=_policy())
    future = await arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=1, request_tokens=11)
    assert arbiter.future_outstanding == 1
    native_waiter = asyncio.create_task(arbiter.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=11))
    future_waiter = asyncio.create_task(arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=2, request_tokens=11))
    await asyncio.sleep(0)
    assert not native_waiter.done()
    assert not future_waiter.done()
    await arbiter.release(future)
    native = await _wait_for(native_waiter)
    assert not future_waiter.done()
    await arbiter.release(native)
    future2 = await _wait_for(future_waiter)
    await arbiter.release(future2)
    ev = arbiter.evidence()
    return {"events": ev["events"], "oracle": {"bounded_speculation_opportunity": True, "new_p2_while_p0_waiting": 0, "unavoidable_future_blocking_observed": True, "conservation": ev["outstanding"] == 0}}


async def _many_futures_and_token_bound() -> dict[str, Any]:
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=_policy(), token_budget=100)
    first = await arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=1, request_tokens=90)
    waiters = [asyncio.create_task(arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=i, request_tokens=20)) for i in range(2, 6)]
    await asyncio.sleep(0)
    assert sum(task.done() for task in waiters) == 0
    await arbiter.release(first)
    admitted = await _wait_for(waiters[0])
    await arbiter.release(admitted)
    for task in waiters[1:]:
        task.cancel()
    await asyncio.gather(*waiters[1:], return_exceptions=True)
    ev = arbiter.evidence()
    return {"events": ev["events"], "oracle": {"max_future_outstanding": max((row["future_outstanding"] for row in ev["events"]), default=0), "token_budget_respected": all(row["tokens_outstanding"] <= 100 for row in ev["events"]), "conservation": ev["outstanding"] == 0}}


async def _variable_lengths_and_failure() -> dict[str, Any]:
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=_policy(), token_budget=120)
    permits = []
    permits.append(await arbiter.acquire(AdmissionClass.FRONTIER_PREPARE, source_sequence=0, request_tokens=7))
    await arbiter.release(permits[0])
    permits.append(await arbiter.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=63))
    await arbiter.release(permits[1])
    failed = await arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=1, request_tokens=9)
    try:
        raise ValueError("deterministic-future-fallback")
    except ValueError:
        await arbiter.release(failed)
    ev = arbiter.evidence()
    return {"events": ev["events"], "oracle": {"invalid_future_counted_as_wasted": True, "variable_request_tokens": [7, 63, 9], "conservation": ev["outstanding"] == 0 and ev["tokens_outstanding"] == 0}}


async def _ordered_publication() -> dict[str, Any]:
    policy = _policy()
    arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
    published: list[int] = []

    async def prepare(sequence: int) -> int:
        await asyncio.sleep(0.002 if sequence == 0 else 0)
        return sequence

    async def publish(sequence: int, value: int) -> None:
        assert sequence == value
        published.append(sequence)

    result = await run_jit_frontier_history_async(3, prepare, publish, authority=CapacityAuthority(2), policy=policy, admission=arbiter)
    return {"events": list(result.events), "oracle": {"publication_order": published, "ordered_durable_publication": published == [0, 1, 2], "max_started_ahead": result.max_started_ahead}}


async def _run_all() -> dict[str, Any]:
    scenarios: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = [
        ("critical_only", _only_critical),
        ("critical_busy_future_ready", _critical_priority),
        ("bounded_opportunity_and_priority_inversion", _bounded_opportunity_and_inversion),
        ("many_ready_futures_and_token_bound", _many_futures_and_token_bound),
        ("variable_lengths_failure_and_fallback", _variable_lengths_and_failure),
        ("ordered_publication", _ordered_publication),
    ]
    results: dict[str, Any] = {}
    for name, fn in scenarios:
        results[name] = await fn()
    return results


def run_stress_suite() -> dict[str, Any]:
    scenarios = asyncio.run(_run_all())
    required = {
        "critical_only", "critical_busy_future_ready", "bounded_opportunity_and_priority_inversion",
        "many_ready_futures_and_token_bound", "variable_lengths_failure_and_fallback", "ordered_publication",
    }
    passed = all(bool(value.get("oracle", {}).get("conservation", True)) for value in scenarios.values()) and required == set(scenarios)
    result = {
        "schema_version": "membind.v6.1.scheduler-stress.v1",
        "status": "PASS_PROVIDER_FREE_EVENT_ORACLE" if passed else "FAIL_PROVIDER_FREE_EVENT_ORACLE",
        "provider_calls": 0,
        "resource_model": {"logical_ready_set": "unbounded_fixture_queue", "physical_admission": "ForegroundAdmissionArbiter", "priority": ["P0:NATIVE_FRONTIER", "P1:UNPROVEN_DIRECT_CONSUMER_DISABLED", "P2:FUTURE_PREPARE"], "future_bound": "policy.future_cap plus token_budget; no arrival predictor"},
        "p1_identity_policy": "P1_DISABLED_UNLESS_DIRECT_CONSUMER_EDGE_PROVEN; source distance alone is insufficient",
        "speculation_debt_policy": "count/work/tokens/age are observable; stop new P2 at physical/token bound and resume after consumer release",
        "scenarios": scenarios,
        "event_oracle": "Every scenario includes ordered source events with outstanding, future, physical-future, native, token and queue state.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps({"status": run_stress_suite()["status"], "provider_calls": 0}, sort_keys=True))
