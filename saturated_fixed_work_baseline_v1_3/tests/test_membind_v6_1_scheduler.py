from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import (
    AdmissionClass,
    CapacityAuthority,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import (
    NativeBindingScope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import (
    current_provider_request_tokens,
    provider_scope,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import (
    TranscriptStore,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.admission import (
    ForegroundAdmissionArbiter,
    PhysicalPermit,
    SourceLease,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.critical_scheduler import (
    CriticalPathResourceScheduler,
    CriticalSchedulerError,
    ReadyTask,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.evidence import (
    extraction_work_inventory,
    provider_proof,
    response_sha256,
    span_work_inventory,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.edge_predicate import (
    install_edge_invalidation_predicate_pushdown,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.executor import (
    DUAL_STREAMING_EXECUTION_STRATEGY,
    STAGED_EXECUTION_STRATEGY,
    run_jit_frontier_history_async,
    run_staged_frontier_history_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (
    _Journal,
    _resolve_routed_client,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy
from saturated_fixed_work_baseline_v1_3.membind_v6_1.provider import (
    V61ProviderClient,
    V61ProviderError,
    incremental_native_summary_context,
    install_auxiliary_transport_guard,
    install_routed_physical_admission,
    strip_certified_previous_context,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.routing import (
    EndpointSpec,
    RoutedOpenAIClient,
    SEMANTIC_PHASE_AFFINITY,
)


def test_provider_free_stress_oracle_covers_required_state_transitions() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_v61_scheduler_stress.py"
    spec = importlib.util.spec_from_file_location("membind_v61_scheduler_stress", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run_stress_suite()
    assert result["status"] == "PASS_PROVIDER_FREE_EVENT_ORACLE"
    assert result["provider_calls"] == 0
    assert set(result["scenarios"]) == {
        "critical_only",
        "critical_busy_future_ready",
        "bounded_opportunity_and_priority_inversion",
        "many_ready_futures_and_token_bound",
        "variable_lengths_failure_and_fallback",
        "ordered_publication",
    }
    assert all(row["oracle"].get("conservation", True) for row in result["scenarios"].values())


def test_critical_scheduler_prefers_frontier_then_earliest_resource_finish() -> None:
    scheduler = CriticalPathResourceScheduler(("native-replica", "prepare-replica"))
    scheduler.submit(
        ReadyTask(
            "future-edge",
            source_sequence=1,
            kind="edge-page",
            token_cost=20,
            estimated_service_ns=10,
            preferred_endpoint_id="prepare-replica",
        )
    )
    scheduler.submit(
        ReadyTask(
            "frontier-native",
            source_sequence=0,
            kind="native-merge",
            token_cost=20,
            estimated_service_ns=100,
            preferred_endpoint_id="native-replica",
            frontier_critical=True,
        )
    )

    first = scheduler.choose()
    assert first is not None
    assert first.task_id == "frontier-native"
    assert first.endpoint_id == "native-replica"
    assert first.reason == "critical_path_preferred"
    assert first.candidate_scores == {"native-replica": 100, "prepare-replica": 100}
    scheduler.complete("frontier-native", service_ns=120)

    second = scheduler.choose()
    assert second is not None
    assert second.task_id == "future-edge"
    assert second.endpoint_id == "prepare-replica"
    scheduler.cancel("future-edge")
    assert scheduler.evidence()["balanced"] is True


def test_critical_scheduler_spills_when_preferred_endpoint_has_active_work() -> None:
    scheduler = CriticalPathResourceScheduler(("native-replica", "prepare-replica"))
    scheduler.submit(
        ReadyTask(
            "page-a",
            source_sequence=0,
            kind="edge-page",
            token_cost=40,
            estimated_service_ns=100,
            preferred_endpoint_id="prepare-replica",
            frontier_critical=True,
        )
    )
    scheduler.submit(
        ReadyTask(
            "page-b",
            source_sequence=1,
            kind="edge-page",
            token_cost=40,
            estimated_service_ns=60,
            preferred_endpoint_id="prepare-replica",
        )
    )
    first = scheduler.choose()
    assert first is not None and first.task_id == "page-a"
    assert first.endpoint_id == "prepare-replica"
    second = scheduler.choose()
    assert second is not None and second.task_id == "page-b"
    assert second.endpoint_id == "native-replica"
    assert second.reason == "critical_path_earliest_finish_spillover"
    scheduler.complete("page-a", service_ns=90)
    scheduler.complete("page-b", service_ns=50)
    evidence = scheduler.evidence()
    assert evidence["balanced"] is True
    assert evidence["service_ewma_ns"]["native-replica:edge-page"] == 50
    assert evidence["service_ewma_ns"]["prepare-replica:edge-page"] == 90


def test_critical_scheduler_respects_dependencies_and_cancellation() -> None:
    scheduler = CriticalPathResourceScheduler(("native-replica", "prepare-replica"))
    scheduler.submit(
        ReadyTask(
            "nodes",
            source_sequence=0,
            kind="node-partition",
            token_cost=12,
            estimated_service_ns=30,
            preferred_endpoint_id="prepare-replica",
        )
    )
    scheduler.submit(
        ReadyTask(
            "edges",
            source_sequence=0,
            kind="edge-page",
            token_cost=12,
            estimated_service_ns=30,
            preferred_endpoint_id="prepare-replica",
            dependencies=("nodes",),
        )
    )
    first = scheduler.choose()
    assert first is not None and first.task_id == "nodes"
    scheduler.complete("nodes", service_ns=30)
    second = scheduler.choose()
    assert second is not None and second.task_id == "edges"
    scheduler.cancel("edges")
    assert scheduler.evidence()["completed_task_ids"] == ["nodes"]
    assert scheduler.evidence()["balanced"] is True


def test_critical_scheduler_rejects_duplicate_or_unknown_tasks() -> None:
    scheduler = CriticalPathResourceScheduler(("native-replica",))
    task = ReadyTask(
        "one",
        source_sequence=0,
        kind="node",
        token_cost=1,
        estimated_service_ns=1,
    )
    scheduler.submit(task)
    with pytest.raises(CriticalSchedulerError, match="already registered"):
        scheduler.submit(task)
    with pytest.raises(CriticalSchedulerError, match="registered first"):
        scheduler.submit(
            ReadyTask(
                "two",
                source_sequence=0,
                kind="edge",
                token_cost=1,
                dependencies=("missing",),
            )
        )
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    LocalRuntimeConfigurationError,
    _AdaptiveEdgePagePriorityGate,
    _EdgePagePriorityGate,
    _edge_page_messages,
    _edge_pair_partitions,
    _edge_turn_local_partitions,
    _endpoint_grounded_edge_page_model,
    _merge_extraction_responses,
    build_local_openai_transport,
    close_local_u0_runtime,
    install_local_context_budget_adapter,
    install_local_extraction_chunking_policy,
    install_local_single_attempt_policy,
)


def test_adaptive_edge_page_gate_expands_on_queue_and_reduces_on_service_dilation() -> None:
    async def scenario() -> None:
        gate = _AdaptiveEdgePagePriorityGate(4, initial_capacity=2)
        first = await gate.acquire(0)
        second = await gate.acquire(1)
        waiting = asyncio.create_task(gate.acquire(2))
        await asyncio.sleep(0)
        assert not waiting.done()
        gate.release(queue_wait_ns=2_000, service_ns=1_000)
        await asyncio.wait_for(waiting, timeout=1.0)
        assert gate.target_capacity == 3
        await gate.acquire(3)
        gate.release(queue_wait_ns=0, service_ns=2_000)
        await gate.acquire(4)
        gate.release(queue_wait_ns=0, service_ns=2_000)
        assert gate.target_capacity == 2
        gate.release()
        gate.release()
        assert gate.available == gate.target_capacity
        assert first == 0
        assert second == 1

    asyncio.run(scenario())


def test_endpoint_grounded_edge_schema_enumerates_current_entity_block() -> None:
    model = _endpoint_grounded_edge_page_model(2, ("Boston", "JetBlue", "Boston"))
    schema = model.model_json_schema()
    edge_schema = schema["$defs"]["MemBindEndpointGroundedEdge2_2"]
    assert edge_schema["properties"]["source_entity_name"]["enum"] == [
        "Boston",
        "JetBlue",
    ]
    assert edge_schema["properties"]["target_entity_name"]["enum"] == [
        "Boston",
        "JetBlue",
    ]
    assert schema["properties"]["edges"]["maxItems"] == 2


def test_edge_page_priority_gate_prefers_frontier_and_preserves_capacity() -> None:
    async def scenario() -> None:
        gate = _EdgePagePriorityGate(1)
        assert await gate.acquire(2) == 0
        future = asyncio.create_task(gate.acquire(3))
        await asyncio.sleep(0)
        frontier = asyncio.create_task(gate.acquire(1))
        await asyncio.sleep(0)
        assert not future.done()
        assert not frontier.done()
        gate.release()
        assert await frontier == 2
        assert not future.done()
        gate.release()
        assert await future == 1
        gate.release()
        assert gate.available == gate.capacity == 1

    asyncio.run(scenario())


def test_edge_page_priority_gate_reclaims_cancelled_grant() -> None:
    async def scenario() -> None:
        gate = _EdgePagePriorityGate(1)
        task = asyncio.create_task(gate.acquire(0))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert gate.available == gate.capacity == 1

    asyncio.run(scenario())


def test_source_lease_is_separate_from_physical_admission() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(1), policy=policy)

        lease = await arbiter.acquire_source_lease(
            AdmissionClass.FRONTIER_PREPARE, source_sequence=0
        )
        assert isinstance(lease, SourceLease)
        assert arbiter.source_outstanding == 1
        assert arbiter.future_source_outstanding == 0
        assert arbiter.outstanding == 0
        assert arbiter.tokens_outstanding == 0

        physical = await arbiter.acquire_physical(
            AdmissionClass.FRONTIER_PREPARE,
            source_sequence=0,
            request_tokens=32,
        )
        assert isinstance(physical, PhysicalPermit)
        assert arbiter.source_outstanding == 1
        assert arbiter.outstanding == 1
        assert arbiter.tokens_outstanding == 32

        await arbiter.release_physical(physical)
        await arbiter.release_source_lease(lease)
        assert arbiter.source_outstanding == 0
        assert arbiter.outstanding == 0
        assert arbiter.tokens_outstanding == 0
        events = arbiter.evidence()["events"]
        assert [row["event"] for row in events].count("SOURCE_LEASE_ADMIT") == 1
        assert [row["event"] for row in events].count("SOURCE_LEASE_RELEASE") == 1
        assert [row["event"] for row in events].count("ADMISSION_ADMIT") == 1
        assert [row["event"] for row in events].count("ADMISSION_RELEASE") == 1

    asyncio.run(scenario())


def test_multiple_physical_transports_share_one_source_lease() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        lease = await arbiter.acquire_source_lease(
            AdmissionClass.FUTURE_PREPARE, source_sequence=1
        )
        first = await arbiter.acquire_physical(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=11,
        )
        second = await arbiter.acquire_physical(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=13,
        )
        assert arbiter.source_outstanding == 1
        assert arbiter.future_source_outstanding == 1
        assert arbiter.outstanding == 2
        assert arbiter.future_outstanding == 0
        assert arbiter.physical_future_outstanding == 2
        assert arbiter.tokens_outstanding == 24

        await arbiter.frontier_advanced(0)
        assert arbiter.future_source_outstanding == 0
        assert arbiter.future_outstanding == 0
        assert arbiter.physical_future_outstanding == 0
        assert arbiter.source_outstanding == 1

        await arbiter.release_physical(first)
        await arbiter.release_physical(second)
        await arbiter.release_source_lease(lease)
        assert arbiter.source_outstanding == 0
        assert arbiter.outstanding == 0
        assert arbiter.tokens_outstanding == 0
        events = arbiter.evidence()["events"]
        assert [row["event"] for row in events].count("SOURCE_LEASE_ADMIT") == 1
        assert [row["event"] for row in events].count("ADMISSION_ADMIT") == 2
        assert [row["event"] for row in events].count("ADMISSION_RELEASE") == 2

    asyncio.run(scenario())


def test_provider_proof_separates_source_cap_from_physical_transport_count() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        lease = await arbiter.acquire_source_lease(
            AdmissionClass.FUTURE_PREPARE, source_sequence=1
        )
        first = await arbiter.acquire_physical(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=11,
        )
        second = await arbiter.acquire_physical(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=13,
        )
        await arbiter.frontier_advanced(0)
        await arbiter.release_physical(first)
        await arbiter.release_physical(second)
        await arbiter.release_source_lease(lease)
        proof = provider_proof(
            arbiter.evidence()["events"],
            capacity=2,
            future_cap=1,
            arbiter_instance_id=arbiter.instance_id,
            token_budget=arbiter.token_budget,
        )
        assert proof["status"] == "PASS"
        assert proof["source_lease_count"] == 1
        assert proof["max_future_source_outstanding"] == 1
        assert proof["max_physical_future_outstanding"] == 2
        assert proof["active_source_promotion_count"] == 1

    asyncio.run(scenario())


def test_routed_physical_admission_is_endpoint_aware_and_released_on_failure() -> None:
    async def scenario() -> None:
        class Completion:
            def __init__(self, endpoint_id: str) -> None:
                self.endpoint_id = endpoint_id

            async def create(self, **_kwargs):
                if self.endpoint_id == "native-replica":
                    raise RuntimeError("endpoint failed")
                return {"endpoint_id": self.endpoint_id}

        endpoints = (
            EndpointSpec(
                "native-replica", "http://127.0.0.1:18200/v1", "qwen3-8b-awq", 0
            ),
            EndpointSpec(
                "prepare-replica", "http://127.0.0.1:18201/v1", "qwen3-8b-awq", 1
            ),
        )
        clients = {
            item.endpoint_id: SimpleNamespace(
                chat=SimpleNamespace(completions=Completion(item.endpoint_id))
            )
            for item in endpoints
        }
        router = RoutedOpenAIClient(
            policy=SEMANTIC_PHASE_AFFINITY,
            endpoints=endpoints,
            endpoint_clients=clients,
        )
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(1),
            policy=V61Policy(lookahead=1, future_cap=0, native_future_quota=0),
        )
        restore = install_routed_physical_admission(
            router,
            arbiter=arbiter,
            durable_frontier=lambda: -1,
            token_counter=lambda _messages: 10,
        )
        try:
            with provider_scope(region="NATIVE", source_sequence=0):
                with pytest.raises(RuntimeError, match="endpoint failed"):
                    await router.chat.completions.create(
                        messages=[{"role": "user", "content": "x"}], max_tokens=8
                    )
            admits = [
                row
                for row in arbiter.evidence()["events"]
                if row.get("event") == "ADMISSION_ADMIT"
            ]
            assert len(admits) == 1
            assert admits[0]["resource_id"] == "native-replica"
            assert arbiter.outstanding == 0
            assert arbiter.tokens_outstanding == 0
        finally:
            restore()

    asyncio.run(scenario())


def test_resolve_routed_client_unwraps_qwen_transport_wrapper() -> None:
    """Physical admission must mutate the concrete router instance."""

    endpoints = (
        EndpointSpec(
            "native-replica", "http://127.0.0.1:18200/v1", "qwen3-8b-awq", 0
        ),
        EndpointSpec(
            "prepare-replica", "http://127.0.0.1:18201/v1", "qwen3-8b-awq", 1
        ),
    )
    router = RoutedOpenAIClient(
        policy=SEMANTIC_PHASE_AFFINITY,
        endpoints=endpoints,
        endpoint_clients={item.endpoint_id: object() for item in endpoints},
    )

    class QwenTransportWrapper:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

    wrapper = QwenTransportWrapper(router)
    assert _resolve_routed_client(SimpleNamespace(), SimpleNamespace(client=wrapper)) is router
    assert _resolve_routed_client(
        SimpleNamespace(_membind_route_client=router), SimpleNamespace(client=wrapper)
    ) is router


def test_edge_page_priority_gate_bounds_frontier_burst() -> None:
    async def scenario() -> None:
        gate = _EdgePagePriorityGate(2, priority_burst=2)
        assert await gate.acquire(0) == 0
        assert await gate.acquire(0) == 1
        future = asyncio.create_task(gate.acquire(1))
        await asyncio.sleep(0)
        frontier = asyncio.create_task(gate.acquire(0))
        await asyncio.sleep(0)

        gate.release()
        assert await future == 2
        assert not frontier.done()
        future_evidence = gate.grant_evidence(2)
        assert future_evidence["admission_reason"] == "bounded_waiter_aging"
        assert future_evidence["preferred_source_sequence"] == 0
        assert future_evidence["selected_source_sequence"] == 1
        assert future_evidence["preferred_consecutive_grants_before"] == 2
        assert future_evidence["priority_burst_limit"] == 2

        gate.release()
        assert await frontier == 3
        assert gate.grant_evidence(3)["admission_reason"] == "source_priority"
        gate.release()
        gate.release()
        assert gate.available == gate.capacity == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lookahead": 0},
        {"future_cap": -1},
        {"future_cap": 8},
        {"future_cap": 1, "native_future_quota": 2},
    ],
)
def test_policy_rejects_out_of_contract_values(kwargs) -> None:
    with pytest.raises(ValueError):
        V61Policy(**kwargs)


def test_jit_executor_materializes_only_the_bounded_window() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(3), policy=policy)
        started: list[int] = []
        published: list[int] = []
        release_zero = asyncio.Event()
        first_window_started = asyncio.Event()

        async def prepare(sequence: int) -> int:
            started.append(sequence)
            if {0, 1}.issubset(started):
                first_window_started.set()
            if sequence == 0:
                await release_zero.wait()
            return sequence

        async def publish(sequence: int, value: int) -> None:
            assert value == sequence
            assert arbiter.native_guard_active
            published.append(sequence)

        task = asyncio.create_task(
            run_jit_frontier_history_async(
                4,
                prepare,
                publish,
                authority=CapacityAuthority(3),
                policy=policy,
                admission=arbiter,
            )
        )
        await asyncio.wait_for(first_window_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert sorted(started) == [0, 1]
        release_zero.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert published == [0, 1, 2, 3]
        assert result.durable_frontier == 3
        assert result.max_started_ahead <= 1
        assert result.arbiter_instance_id == arbiter.instance_id

    asyncio.run(scenario())


def test_native_guard_drains_interfering_future_and_blocks_new_future_admission() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(3), policy=policy)
        admitted = await arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=2)
        assert admitted is AdmissionClass.FUTURE_PREPARE
        guard = asyncio.create_task(arbiter.enter_native_guard(0))
        await asyncio.sleep(0)
        assert not guard.done()
        await arbiter.release(admitted)
        await asyncio.wait_for(guard, timeout=1)
        assert arbiter.native_guard_active
        blocked = asyncio.create_task(
            arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=3)
        )
        await asyncio.sleep(0)
        assert not blocked.done()

        native = await arbiter.acquire(AdmissionClass.NATIVE_FRONTIER, source_sequence=0)
        await arbiter.release(native)
        await arbiter.exit_native_guard(0)
        future = await asyncio.wait_for(blocked, timeout=1)
        await arbiter.release(future)
        assert arbiter.outstanding == 0
        assert arbiter.future_outstanding == 0

    asyncio.run(scenario())


def test_phase_isolated_dual_streaming_overlaps_prepare_and_native() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(
            authority, policy=policy, phase_isolated=True
        )
        prepare_one_active = asyncio.Event()
        release_prepare_one = asyncio.Event()
        native_zero_active = asyncio.Event()
        release_native_zero = asyncio.Event()

        async def prepare(sequence: int) -> int:
            admission_class = (
                AdmissionClass.FRONTIER_PREPARE
                if sequence == 0
                else AdmissionClass.FUTURE_PREPARE
            )
            admitted = await arbiter.acquire(admission_class, source_sequence=sequence)
            try:
                if sequence == 1:
                    prepare_one_active.set()
                    await release_prepare_one.wait()
                return sequence
            finally:
                await arbiter.release(admitted)

        async def publish(sequence: int, value: int) -> None:
            assert value == sequence
            admitted = await arbiter.acquire(
                AdmissionClass.NATIVE_FRONTIER, source_sequence=sequence
            )
            try:
                if sequence == 0:
                    native_zero_active.set()
                    await release_native_zero.wait()
            finally:
                await arbiter.release(admitted)

        task = asyncio.create_task(
            run_jit_frontier_history_async(
                2,
                prepare,
                publish,
                authority=authority,
                policy=policy,
                admission=arbiter,
                execution_strategy=DUAL_STREAMING_EXECUTION_STRATEGY,
            )
        )
        await asyncio.wait_for(prepare_one_active.wait(), timeout=1)
        await asyncio.wait_for(native_zero_active.wait(), timeout=1)
        assert arbiter.prepare_outstanding == 1
        assert arbiter.native_outstanding == 1
        release_prepare_one.set()
        release_native_zero.set()
        result = await asyncio.wait_for(task, timeout=2)
        assert result.execution_strategy == DUAL_STREAMING_EXECUTION_STRATEGY
        assert result.stage_barrier is None
        assert arbiter.outstanding == 0
        assert arbiter.tokens_outstanding == 0
        proof = provider_proof(
            arbiter.evidence()["events"],
            capacity=authority.value,
            future_cap=policy.future_cap,
            arbiter_instance_id=arbiter.instance_id,
            token_budget=arbiter.token_budget,
            phase_isolated=True,
        )
        assert proof["status"] == "PASS"
        assert proof["phase_isolated"] is True
        assert proof["max_outstanding"] == 2

    asyncio.run(scenario())


def test_native_requests_use_provider_and_token_capacity_without_a_private_lane_cap() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(8), policy=policy, token_budget=61_440
        )
        await arbiter.enter_native_guard(0)
        small_request_tokens = list(range(5_000, 5_008))
        tasks = [
            asyncio.create_task(
                arbiter.acquire(
                    AdmissionClass.NATIVE_FRONTIER,
                    source_sequence=0,
                    request_tokens=request_tokens,
                )
            )
            for request_tokens in small_request_tokens
        ]
        try:
            await asyncio.sleep(0)
            assert arbiter.outstanding == 8
            assert arbiter.native_outstanding == 8
            assert all(task.done() for task in tasks)
            permits = [
                (task.result(), request_tokens)
                for task, request_tokens in zip(tasks, small_request_tokens, strict=True)
            ]

            ninth_task = asyncio.create_task(
                arbiter.acquire(
                    AdmissionClass.NATIVE_FRONTIER,
                    source_sequence=0,
                    request_tokens=5_100,
                )
            )
            await asyncio.sleep(0)
            assert not ninth_task.done()
            released, released_tokens = permits.pop()
            await arbiter.release(released, request_tokens=released_tokens)
            ninth = await asyncio.wait_for(ninth_task, timeout=1)
            assert arbiter.native_outstanding == 8
            permits.append((ninth, 5_100))
            for permit, request_tokens in permits:
                await arbiter.release(permit, request_tokens=request_tokens)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        token_limited_first = await arbiter.acquire(
            AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=40_000
        )
        token_limited_waiter = asyncio.create_task(
            arbiter.acquire(
                AdmissionClass.NATIVE_FRONTIER, source_sequence=0, request_tokens=30_000
            )
        )
        await asyncio.sleep(0)
        assert arbiter.native_outstanding == 1
        assert not token_limited_waiter.done()
        await arbiter.release(token_limited_first, request_tokens=40_000)
        token_limited_second = await asyncio.wait_for(token_limited_waiter, timeout=1)
        await arbiter.release(token_limited_second, request_tokens=30_000)
        await arbiter.exit_native_guard(0)
        proof = provider_proof(
            arbiter.evidence()["events"],
            capacity=8,
            future_cap=1,
            arbiter_instance_id=arbiter.instance_id,
            token_budget=61_440,
        )
        assert proof["max_native_outstanding"] == 8
        assert proof["max_native_tokens_outstanding"] == 40_121
        assert proof["capacity"] == 8

    asyncio.run(scenario())


def test_cancelled_waiter_does_not_leak_permits() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=0, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        waiting = asyncio.create_task(
            arbiter.acquire(AdmissionClass.FUTURE_PREPARE, source_sequence=2)
        )
        await asyncio.sleep(0)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        evidence = arbiter.evidence()
        assert evidence["outstanding"] == 0
        assert evidence["future_outstanding"] == 0
        assert evidence["waiters"] == []

    asyncio.run(scenario())


def test_durable_frontier_promotes_active_future_and_releases_quota() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(
            authority, policy=policy, phase_isolated=True
        )

        source_one = await arbiter.acquire(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=5_001,
        )
        source_two_task = asyncio.create_task(
            arbiter.acquire(
                AdmissionClass.FUTURE_PREPARE,
                source_sequence=2,
                request_tokens=5_002,
            )
        )
        await asyncio.sleep(0)
        assert not source_two_task.done()
        assert arbiter.future_outstanding == 1

        await arbiter.frontier_advanced(0)
        source_two = await asyncio.wait_for(source_two_task, timeout=1)
        assert source_one is AdmissionClass.FUTURE_PREPARE
        assert source_two is AdmissionClass.FUTURE_PREPARE
        assert arbiter.outstanding == 2
        assert arbiter.future_outstanding == 1

        # Promotion changes live accounting, not the caller's acquire handle.
        await arbiter.release(source_one, request_tokens=5_001)
        await arbiter.release(source_two, request_tokens=5_002)
        assert arbiter.outstanding == 0
        assert arbiter.future_outstanding == 0
        assert arbiter.waiter_count == 0

        evidence = arbiter.evidence()
        promotions = [
            row
            for row in evidence["events"]
            if row["event"] == "ADMISSION_ACTIVE_RECLASSIFY"
        ]
        assert len(promotions) == 1
        assert promotions[0]["source_sequence"] == 1
        assert promotions[0]["trigger_frontier_sequence"] == 0
        assert promotions[0]["from_admission_class"] == "FUTURE_PREPARE"
        assert promotions[0]["admission_class"] == "FRONTIER_PREPARE"
        proof = provider_proof(
            evidence["events"],
            capacity=authority.value,
            future_cap=policy.future_cap,
            arbiter_instance_id=arbiter.instance_id,
            token_budget=arbiter.token_budget,
            phase_isolated=True,
        )
        assert proof["active_promotion_count"] == 1

    asyncio.run(scenario())


def test_durable_frontier_does_not_promote_a_nonfrontier_active_future() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2), policy=policy, phase_isolated=True
        )
        source_two = await arbiter.acquire(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=2,
            request_tokens=5_002,
        )
        source_three_task = asyncio.create_task(
            arbiter.acquire(
                AdmissionClass.FUTURE_PREPARE,
                source_sequence=3,
                request_tokens=5_003,
            )
        )
        await asyncio.sleep(0)
        await arbiter.frontier_advanced(0)
        await asyncio.sleep(0)
        assert not source_three_task.done()
        assert not any(
            row["event"] == "ADMISSION_ACTIVE_RECLASSIFY"
            for row in arbiter.evidence()["events"]
        )

        await arbiter.release(source_two, request_tokens=5_002)
        source_three = await asyncio.wait_for(source_three_task, timeout=1)
        await arbiter.release(source_three, request_tokens=5_003)

    asyncio.run(scenario())


def test_bootstrap_borrow_admits_one_extra_future_before_first_publication() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(4)
        arbiter = ForegroundAdmissionArbiter(
            authority,
            policy=policy,
            phase_isolated=True,
            bootstrap_future_borrow=True,
        )
        frontier = await arbiter.acquire(
            AdmissionClass.FRONTIER_PREPARE,
            source_sequence=0,
            request_tokens=5_000,
        )
        source_one = await arbiter.acquire(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=5_001,
        )
        source_two = await arbiter.acquire(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=2,
            request_tokens=5_002,
        )
        assert arbiter.future_outstanding == 2

        source_three_task = asyncio.create_task(
            arbiter.acquire(
                AdmissionClass.FUTURE_PREPARE,
                source_sequence=3,
                request_tokens=5_003,
            )
        )
        await asyncio.sleep(0)
        assert not source_three_task.done()

        await arbiter.release(frontier, request_tokens=5_000)
        await arbiter.frontier_advanced(0)
        await asyncio.sleep(0)
        assert arbiter.future_outstanding == 1
        assert not source_three_task.done()

        # Borrowing closes permanently after the first durable publication.
        await arbiter.release(source_two, request_tokens=5_002)
        source_three = await asyncio.wait_for(source_three_task, timeout=1)
        await arbiter.release(source_one, request_tokens=5_001)
        await arbiter.release(source_three, request_tokens=5_003)

        evidence = arbiter.evidence()
        borrowed = [
            row
            for row in evidence["events"]
            if row["event"] == "ADMISSION_ADMIT" and row["bootstrap_borrowed"]
        ]
        assert len(borrowed) == 1
        assert borrowed[0]["source_sequence"] == 2
        assert borrowed[0]["durable_frontier"] == -1
        proof = provider_proof(
            evidence["events"],
            capacity=authority.value,
            future_cap=policy.future_cap,
            arbiter_instance_id=arbiter.instance_id,
            token_budget=arbiter.token_budget,
            phase_isolated=True,
            bootstrap_future_borrow=True,
        )
        assert proof["bootstrap_borrow_count"] == 1
        assert proof["max_future_outstanding"] == 2

    asyncio.run(scenario())


def test_provider_proof_rejects_empty_or_unbalanced_evidence() -> None:
    with pytest.raises(ValueError, match="empty"):
        provider_proof([], capacity=8, future_cap=1, arbiter_instance_id="a")
    with pytest.raises(ValueError, match="unbalanced"):
        provider_proof(
            [
                {
                    "event": "ADMISSION_ADMIT",
                    "arbiter_instance_id": "a",
                    "admission_class": "FUTURE_PREPARE",
                    "outstanding": 1,
                    "future_outstanding": 1,
                }
            ],
            capacity=8,
            future_cap=1,
            arbiter_instance_id="a",
        )


def test_provider_proof_rejects_active_promotion_for_wrong_frontier() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2), policy=policy, phase_isolated=True
        )
        admitted = await arbiter.acquire(
            AdmissionClass.FUTURE_PREPARE,
            source_sequence=1,
            request_tokens=5_001,
        )
        await arbiter.frontier_advanced(0)
        await arbiter.release(admitted, request_tokens=5_001)
        events = [dict(row) for row in arbiter.evidence()["events"]]
        promotion = next(
            row for row in events if row["event"] == "ADMISSION_ACTIVE_RECLASSIFY"
        )
        promotion["trigger_frontier_sequence"] = 7
        with pytest.raises(ValueError, match="frontier permit provenance"):
            provider_proof(
                events,
                capacity=2,
                future_cap=policy.future_cap,
                arbiter_instance_id=arbiter.instance_id,
                token_budget=arbiter.token_budget,
                phase_isolated=True,
            )

    asyncio.run(scenario())


def test_auxiliary_shared_transport_calls_are_admitted_and_accounted() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=0, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        events: list[dict] = []

        class Completions:
            async def create(self, **kwargs):
                return {"choices": [{"message": {"content": "True"}}], "request": kwargs}

        completions = Completions()
        transport = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        restore = install_auxiliary_transport_guard(
            transport,
            arbiter=arbiter,
            event_sink=events.append,
        )
        try:
            with provider_scope(region="NATIVE", source_sequence=0):
                result = await transport.chat.completions.create(
                    messages=[{"role": "user", "content": "relevance"}],
                    max_tokens=1,
                )
            assert result["choices"][0]["message"]["content"] == "True"
            assert len(events) == 1
            assert events[0]["event"] == "V61_PROVIDER_CALL"
            assert events[0]["auxiliary"] is True
            assert events[0]["replay"] is False
            assert events[0]["status"] == "success"
            assert events[0]["transport_attempt_count"] == 1
            assert events[0]["transport_retry_count"] == 0
            assert arbiter.outstanding == 0
            assert arbiter.native_outstanding == 0
        finally:
            restore()

    asyncio.run(scenario())


def test_logical_provider_call_accounts_for_transport_expansion_once_admitted() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        events: list[dict] = []

        class Completions:
            def __init__(self) -> None:
                self.calls = 0
                self.request_token_scopes: list[int | None] = []

            async def create(self, **kwargs):
                self.calls += 1
                self.request_token_scopes.append(current_provider_request_tokens())
                return {"call": self.calls, "request": kwargs}

        completions = Completions()
        transport = SimpleNamespace(chat=SimpleNamespace(completions=completions))

        class Delegate:
            client = transport
            max_tokens = 64

            async def generate_response(
                self,
                messages,
                response_model=None,
                max_tokens=None,
                model_size=None,
                group_id=None,
                prompt_name=None,
                *,
                attribute_extraction=False,
            ):
                kwargs = {"messages": messages, "max_tokens": max_tokens or self.max_tokens}
                first = await self.client.chat.completions.create(**kwargs)
                second = await self.client.chat.completions.create(**kwargs)
                return {"first": first["call"], "second": second["call"]}

        restore = install_auxiliary_transport_guard(
            transport,
            arbiter=arbiter,
            event_sink=events.append,
        )
        try:
            client = V61ProviderClient(
                Delegate(),
                store=TranscriptStore(),
                arbiter=arbiter,
                mode="capture",
                durable_frontier=lambda: -1,
                event_sink=events.append,
            )
            with provider_scope(region="PREPARE", source_sequence=0):
                result = await client.generate_response(
                    [{"role": "user", "content": "partitioned extraction"}],
                    max_tokens=64,
                    prompt_name="extract_nodes.extract_message",
                )
            provider_rows = [row for row in events if row.get("event") == "V61_PROVIDER_CALL"]
            admission_rows = arbiter.evidence()["events"]
            assert result == {"first": 1, "second": 2}
            assert completions.calls == 2
            assert len(provider_rows) == 1
            assert provider_rows[0].get("auxiliary", False) is False
            assert provider_rows[0]["transport_attempt_count"] == 2
            assert provider_rows[0]["transport_retry_count"] == 0
            assert completions.request_token_scopes == [
                provider_rows[0]["request_tokens"],
                provider_rows[0]["request_tokens"],
            ]
            assert sum(row["event"] == "ADMISSION_ADMIT" for row in admission_rows) == 1
            assert sum(row["event"] == "ADMISSION_RELEASE" for row in admission_rows) == 1
            assert arbiter.outstanding == 0
        finally:
            restore()

    asyncio.run(scenario())


def test_nested_transport_guards_count_only_the_instrumented_outer_seam() -> None:
    async def scenario() -> None:
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2),
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
        )
        events: list[dict] = []

        class InnerCompletions:
            def __init__(self) -> None:
                self.calls = 0

            async def create(self, **kwargs):
                self.calls += 1
                return {"call": self.calls, "request": kwargs}

        inner_completions = InnerCompletions()
        inner_transport = SimpleNamespace(
            chat=SimpleNamespace(completions=inner_completions)
        )

        class OuterCompletions:
            async def create(self, **kwargs):
                return await inner_transport.chat.completions.create(**kwargs)

        outer_transport = SimpleNamespace(
            chat=SimpleNamespace(completions=OuterCompletions())
        )

        class Delegate:
            client = outer_transport
            max_tokens = 64

            async def generate_response(
                self,
                messages,
                response_model=None,
                max_tokens=None,
                model_size=None,
                group_id=None,
                prompt_name=None,
                *,
                attribute_extraction=False,
            ):
                return await self.client.chat.completions.create(
                    messages=messages,
                    max_tokens=max_tokens or self.max_tokens,
                )

        restore_inner = install_auxiliary_transport_guard(
            inner_transport, arbiter=arbiter, event_sink=events.append
        )
        restore_outer = install_auxiliary_transport_guard(
            outer_transport, arbiter=arbiter, event_sink=events.append
        )
        try:
            client = V61ProviderClient(
                Delegate(),
                store=TranscriptStore(),
                arbiter=arbiter,
                mode="capture",
                durable_frontier=lambda: -1,
                event_sink=events.append,
                client_identity={"class": "fixture.Delegate", "source_hash": "fixture"},
            )
            with provider_scope(region="PREPARE", source_sequence=0):
                await client.generate_response(
                    [{"role": "user", "content": "nested transport"}],
                    max_tokens=64,
                    prompt_name="extract_nodes.extract_message",
                )
            provider_rows = [row for row in events if row.get("event") == "V61_PROVIDER_CALL"]
            assert inner_completions.calls == 1
            assert len(provider_rows) == 1
            assert provider_rows[0]["transport_attempt_count"] == 1
            assert provider_rows[0]["transport_retry_count"] == 0
        finally:
            restore_outer()
            restore_inner()

    asyncio.run(scenario())


def test_response_hash_tracks_response_not_request() -> None:
    left = response_sha256({"answer": [1, 2], "ok": True})
    reordered = response_sha256({"ok": True, "answer": [1, 2]})
    changed = response_sha256({"answer": [1, 3], "ok": True})
    assert left == reordered
    assert left != changed


def test_span_inventory_reads_top_level_operation_class() -> None:
    records = [
        SimpleNamespace(phase="database", operation_class="write", metadata={}),
        SimpleNamespace(phase="database-transaction", operation_class="transaction", metadata={}),
        SimpleNamespace(phase="database", operation_class="query", metadata={}),
        SimpleNamespace(phase="embedding", operation_class=None, metadata={"text_count": 4}),
        SimpleNamespace(
            phase="llm",
            span_id="logical-1",
            parent_span_id=None,
            operation_class="logical-call",
            metadata={"prompt_name": "extract_nodes.extract_message"},
        ),
        SimpleNamespace(
            phase="llm-transport",
            span_id="transport-1",
            parent_span_id="logical-1",
            operation_class="request-attempt",
            status="error",
            metadata={
                "attempt_index": 0,
                "input_tokens": 12,
                "output_tokens": 0,
            },
        ),
        SimpleNamespace(
            phase="llm-transport",
            span_id="transport-2",
            parent_span_id="logical-1",
            operation_class="request-attempt",
            status="ok",
            metadata={
                "attempt_index": 1,
                "input_tokens": 12,
                "output_tokens": 3,
                "finish_reason": "length",
            },
        ),
    ]
    assert span_work_inventory(records) == {
        "llm_logical_requests": 1,
        "llm_logical_requests_by_prompt": {"extract_nodes.extract_message": 1},
        "transport_attempts": 2,
        "transport_failed_attempts": 1,
        "transport_true_retry_attempts": 1,
        "compatibility_expansion_attempts": 0,
        "transport_retry_attempts": 1,
        "prompt_tokens": 24,
        "completion_tokens": 3,
        "finish_reason_length_count": 1,
        "embedding_calls": 1,
        "embedding_items": 4,
        "db_reads": 1,
        "db_write_statements": 1,
        "db_write_transactions": 1,
        "db_writes": 2,
    }


def test_span_inventory_does_not_mislabel_successful_expansion_as_retry() -> None:
    records = [
        SimpleNamespace(
            phase="llm",
            span_id="logical-edge",
            parent_span_id=None,
            operation_class="logical-call",
            status="ok",
            metadata={"prompt_name": "extract_edges.edge"},
        ),
        *[
            SimpleNamespace(
                phase="llm-transport",
                span_id=f"transport-{index}",
                parent_span_id="logical-edge",
                operation_class="request-attempt",
                status="ok",
                metadata={
                    "attempt_index": index,
                    "input_tokens": 100 + index,
                    "output_tokens": 10,
                    "finish_reason": "stop",
                },
            )
            for index in range(4)
        ],
    ]

    inventory = span_work_inventory(records)

    assert inventory["transport_attempts"] == 4
    assert inventory["transport_failed_attempts"] == 0
    assert inventory["transport_true_retry_attempts"] == 0
    assert inventory["transport_retry_attempts"] == 0
    assert inventory["compatibility_expansion_attempts"] == 3


def test_extraction_inventory_aggregates_bounded_page_progress() -> None:
    diagnostics = [
        {
            "event": "EDGE_PAGINATION_PAGE",
            "page_index": 0,
            "page_capacity": 8,
            "raw_edge_count": 3,
            "raw_unique_progress_edge_count": 2,
            "delta_edge_count": 2,
            "duplicate_edge_count": 1,
        },
        {
            "event": "EDGE_PAGINATION_PAGE",
            "page_index": 1,
            "page_capacity": 8,
            "raw_edge_count": 0,
            "raw_unique_progress_edge_count": 0,
            "delta_edge_count": 0,
            "duplicate_edge_count": 0,
        },
        {"event": "EDGE_PAGINATION_EMPTY_PAGE"},
        {"event": "EDGE_PAGINATION_ZERO_DELTA"},
    ]

    assert extraction_work_inventory(diagnostics) == {
        "pagination_requests": 2,
        "pagination_continuation_requests": 1,
        "pagination_raw_unique_progress_edges": 2,
        "pagination_unique_delta_edges": 2,
        "pagination_duplicate_edges": 1,
        "pagination_duplicate_recovery_requests": 0,
        "pagination_duplicate_recovery_successes": 0,
        "pagination_invalid_endpoint_edges": 0,
        "pagination_zero_delta_terminations": 1,
        "pagination_empty_terminations": 1,
        "pagination_page_capacity": 8,
        "node_response_audits": 0,
        "node_returned_entities": 0,
        "node_accepted_entities": 0,
        "node_ungrounded_rejected": 0,
        "node_duplicate_rejected": 0,
        "node_malformed_rejected": 0,
        "node_partition_pipeline_calls": 0,
        "node_partition_pipeline_partitions": 0,
        "node_partition_pipeline_max_active": 0,
        "summary_response_audits": 0,
        "summary_unknown_rejected": 0,
        "summary_duplicate_rejected": 0,
        "summary_omitted_requested": 0,
        "grounded_summary_materializations": 0,
        "grounded_summary_nodes": 0,
        "grounded_summary_edge_fact_units": 0,
        "grounded_summary_episode_span_units": 0,
        "grounded_summary_prior_units": 0,
        "grounded_summary_selected_units": 0,
        "grounded_summary_dropped_units": 0,
        "grounded_summary_empty_nodes": 0,
        "grounded_summary_node_evidence": 0,
        "summary_llm_bypasses": 0,
        "summary_upstream_fallbacks": 0,
        "edge_invalidation_predicate_audits": 0,
        "edge_invalidation_candidates": 0,
        "edge_invalidation_candidates_retained": 0,
        "edge_invalidation_structurally_ineligible_candidates_rejected": 0,
        "edge_invalidation_disjoint_candidates_rejected": 0,
        "edge_invalidation_malformed_candidates_retained": 0,
        "edge_dedupe_llm_bypasses_from_predicate": 0,
        "edge_invalidation_llm_proposals": 0,
        "edge_invalidations_accepted": 0,
        "edge_invalidations_rejected_by_temporal_acceptance": 0,
        "edge_reused_resolved_temporal_snapshots": 0,
        "edge_reused_resolved_temporal_mutations_rolled_back": 0,
    }


def test_edge_invalidation_predicate_pushdown_is_all_or_nothing_and_restorable(
    monkeypatch,
) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    calls: list[dict[str, object]] = []

    async def original(
        _client,
        extracted,
        related,
        existing,
        _episode,
        edge_types=None,
    ):
        calls.append(
            {
                "extracted": extracted,
                "related": list(related),
                "existing": list(existing),
                "edge_types": edge_types,
            }
        )
        return "resolved"

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)
    wrapped = edge_operations.resolve_extracted_edge

    extracted = SimpleNamespace(source_node_uuid="a", target_node_uuid="b")
    related = [SimpleNamespace(source_node_uuid="a", target_node_uuid="b")]
    same_source = SimpleNamespace(source_node_uuid="a", target_node_uuid="c")
    same_target = SimpleNamespace(source_node_uuid="d", target_node_uuid="b")
    cross_endpoint = SimpleNamespace(source_node_uuid="e", target_node_uuid="a")
    disjoint = SimpleNamespace(source_node_uuid="x", target_node_uuid="y")
    malformed = SimpleNamespace(source_node_uuid=None, target_node_uuid="b")

    async def scenario() -> None:
        assert await wrapped(
            object(),
            extracted,
            related,
            [same_source, same_target, cross_endpoint, disjoint, malformed],
            object(),
            {"REL": object},
        ) == "resolved"
        assert calls[0]["related"] == related
        assert calls[0]["existing"] == [
            same_source,
            same_target,
            cross_endpoint,
            disjoint,
            malformed,
        ]
        assert await wrapped(
            object(), extracted, [], [disjoint], object(), None
        ) == "resolved"
        assert calls[1]["existing"] == []
        assert await wrapped(
            object(), extracted, [], [same_source, disjoint], object(), None
        ) == "resolved"
        assert calls[2]["existing"] == [same_source, disjoint]

    try:
        asyncio.run(scenario())
        assert diagnostics[0]["invalidation_candidate_count"] == 5
        assert diagnostics[0]["retained_invalidation_candidate_count"] == 5
        assert diagnostics[0]["rejected_disjoint_candidate_count"] == 0
        assert diagnostics[0]["malformed_candidate_retained_count"] == 1
        assert diagnostics[0]["newly_enabled_llm_bypass"] is False
        assert diagnostics[0]["original_prompt_context_preserved"] is True
        assert diagnostics[1]["newly_enabled_llm_bypass"] is True
        assert diagnostics[1]["original_prompt_context_preserved"] is False
        assert diagnostics[2]["retained_invalidation_candidate_count"] == 2
        assert diagnostics[2]["rejected_disjoint_candidate_count"] == 0
        assert diagnostics[2]["original_prompt_context_preserved"] is True
        inventory = extraction_work_inventory(diagnostics)
        assert inventory["edge_invalidation_predicate_audits"] == 3
        assert inventory["edge_invalidation_candidates"] == 8
        assert inventory["edge_invalidation_candidates_retained"] == 7
        assert (
            inventory[
                "edge_invalidation_structurally_ineligible_candidates_rejected"
            ]
            == 1
        )
        assert inventory["edge_invalidation_disjoint_candidates_rejected"] == 1
        assert inventory["edge_invalidation_malformed_candidates_retained"] == 1
        assert inventory["edge_dedupe_llm_bypasses_from_predicate"] == 1
    finally:
        restore()
    assert edge_operations.resolve_extracted_edge is original


def test_edge_invalidation_pushdown_bypasses_only_post_acceptance_impossibilities(
    monkeypatch,
) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    calls: list[list[object]] = []

    async def original(_client, extracted, _related, existing, _episode, _types=None):
        calls.append(list(existing))
        return extracted, [], []

    def edge(source, target, relation, fact):
        return SimpleNamespace(
            source_node_uuid=source,
            target_node_uuid=target,
            name=relation,
            fact=fact,
        )

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)
    wrapped = edge_operations.resolve_extracted_edge

    extracted = edge("a", "b", "USES", "USER uses the current app")
    same_source = edge("a", "c", "LIKES", "USER likes another app")
    disjoint = edge("x", "y", "USES", "Another user uses another app")
    same_pair = edge("b", "a", "USED", "The app is used by USER")
    transition = edge("a", "b", "USES", "USER switched to the current app")
    transition_candidate = edge("a", "c", "USES", "USER uses the old app")
    malformed = edge(None, "b", "USES", "Malformed endpoint")
    related = edge("a", "b", "USES", "A possible duplicate")

    async def scenario() -> None:
        await wrapped(object(), extracted, [], [same_source, disjoint], object(), None)
        await wrapped(object(), extracted, [], [same_pair, disjoint], object(), None)
        await wrapped(
            object(), transition, [], [transition_candidate, disjoint], object(), None
        )
        await wrapped(object(), extracted, [], [malformed, disjoint], object(), None)
        await wrapped(object(), extracted, [related], [disjoint], object(), None)

    try:
        asyncio.run(scenario())
        assert calls[0] == []
        assert calls[1] == [same_pair, disjoint]
        assert calls[2] == [transition_candidate, disjoint]
        assert calls[3] == [malformed, disjoint]
        assert calls[4] == [disjoint]
        assert diagnostics[0]["newly_enabled_llm_bypass"] is True
        assert diagnostics[0]["rejected_structurally_ineligible_candidate_count"] == 2
        assert diagnostics[0]["rejected_disjoint_candidate_count"] == 1
        assert diagnostics[1]["newly_enabled_llm_bypass"] is False
        assert diagnostics[2]["newly_enabled_llm_bypass"] is False
        assert diagnostics[3]["malformed_candidate_retained_count"] == 1
        assert diagnostics[4]["related_edge_count"] == 1
    finally:
        restore()


def test_edge_invalidation_temporal_acceptance_rolls_back_unproven_mutation(
    monkeypatch,
) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    def edge(source, target, relation, fact, *, invalid_at=None, expired_at=None):
        return SimpleNamespace(
            source_node_uuid=source,
            target_node_uuid=target,
            name=relation,
            fact=fact,
            invalid_at=invalid_at,
            expired_at=expired_at,
        )

    exact = edge("user", "miles", "HAS_BALANCE", "old balance")
    additive = edge("user", "evernote", "USES", "USER uses Evernote")
    explicit = edge("user", "boston", "LIVES_IN", "USER lives in Boston")
    prior_invalid = object()
    prior_expired = object()
    additive.invalid_at = prior_invalid
    additive.expired_at = prior_expired

    async def original(_client, extracted, _related, existing, _episode, _types=None):
        for candidate in existing:
            candidate.invalid_at = "new-invalid-at"
            candidate.expired_at = "new-expired-at"
        return extracted, list(existing), []

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)
    resolved = edge(
        "user",
        "miles",
        "HAS_BALANCE",
        "USER changed balance to 20,000 miles",
    )
    transition = edge(
        "user",
        "miami",
        "LIVES_IN",
        "USER moved to Miami",
    )

    async def scenario():
        first = await edge_operations.resolve_extracted_edge(
            object(), resolved, [], [exact, additive], object(), None
        )
        assert first[1] == [exact]
        assert exact.invalid_at == "new-invalid-at"
        assert additive.invalid_at is prior_invalid
        assert additive.expired_at is prior_expired
        second = await edge_operations.resolve_extracted_edge(
            object(), transition, [], [explicit], object(), None
        )
        assert second[1] == [explicit]

    try:
        asyncio.run(scenario())
        assert diagnostics[0]["llm_invalidation_proposal_count"] == 2
        assert diagnostics[0]["accepted_invalidation_count"] == 1
        assert diagnostics[0]["rejected_invalidation_count"] == 1
        assert diagnostics[0]["invalidation_acceptance_reason_counts"] == {
            "same_canonical_pair": 1,
            "unproven_cross_pair_transition": 1,
        }
        assert diagnostics[1]["accepted_invalidation_count"] == 1
        assert diagnostics[1]["invalidation_acceptance_reason_counts"] == {
            "explicit_relation_transition": 1,
        }
        inventory = extraction_work_inventory(diagnostics)
        assert inventory["edge_invalidation_llm_proposals"] == 3
        assert inventory["edge_invalidations_accepted"] == 2
        assert inventory["edge_invalidations_rejected_by_temporal_acceptance"] == 1
    finally:
        restore()


def test_edge_invalidation_rejects_resolved_edge_self_invalidation(monkeypatch) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    prior_invalid = object()
    prior_expired = object()
    duplicate = SimpleNamespace(
        uuid="existing-edge",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact="USER has 10,000 miles",
        invalid_at=prior_invalid,
        expired_at=prior_expired,
    )
    extracted = SimpleNamespace(
        uuid="extracted-edge",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact="USER has 10,000 miles",
        invalid_at=None,
        expired_at=None,
    )

    async def original(_client, _extracted, related, _existing, _episode, _types=None):
        resolved = related[0]
        resolved.invalid_at = "new-invalid-at"
        resolved.expired_at = "new-expired-at"
        return resolved, [resolved], [resolved]

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)

    async def scenario() -> None:
        result = await edge_operations.resolve_extracted_edge(
            object(), extracted, [duplicate], [], object(), None
        )
        assert result == (duplicate, [], [duplicate])
        assert duplicate.invalid_at is prior_invalid
        assert duplicate.expired_at is prior_expired

    try:
        asyncio.run(scenario())
        assert diagnostics[0]["llm_invalidation_proposal_count"] == 1
        assert diagnostics[0]["accepted_invalidation_count"] == 0
        assert diagnostics[0]["rejected_invalidation_count"] == 1
        assert diagnostics[0]["invalidation_acceptance_reason_counts"] == {
            "self_invalidation": 1
        }
    finally:
        restore()


def test_edge_invalidation_rejects_hydrated_exact_duplicate_invalidation(
    monkeypatch,
) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    prior_invalid = object()
    prior_expired = object()
    resolved = SimpleNamespace(
        uuid="duplicate-retrieval-copy",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact=" USER   has 10,000 MILES ",
        invalid_at=None,
        expired_at=None,
    )
    candidate = SimpleNamespace(
        uuid="invalidation-retrieval-copy",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact="user has 10,000 miles",
        invalid_at=prior_invalid,
        expired_at=prior_expired,
    )
    extracted = SimpleNamespace(
        uuid="extracted-edge",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact="USER has 10,000 miles",
        invalid_at=None,
        expired_at=None,
    )

    async def original(_client, _extracted, _related, existing, _episode, _types=None):
        existing[0].invalid_at = "new-invalid-at"
        existing[0].expired_at = "new-expired-at"
        return resolved, [existing[0]], [resolved]

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)

    async def scenario() -> None:
        result = await edge_operations.resolve_extracted_edge(
            object(), extracted, [resolved], [candidate], object(), None
        )
        assert result == (resolved, [], [resolved])
        assert candidate.invalid_at is prior_invalid
        assert candidate.expired_at is prior_expired

    try:
        asyncio.run(scenario())
        assert diagnostics[0]["accepted_invalidation_count"] == 0
        assert diagnostics[0]["rejected_invalidation_count"] == 1
        assert diagnostics[0]["invalidation_acceptance_reason_counts"] == {
            "idempotent_duplicate_invalidation": 1
        }
    finally:
        restore()


def test_edge_invalidation_rolls_back_reused_resolved_edge_side_effect(
    monkeypatch,
) -> None:
    from graphiti_core.utils.maintenance import edge_operations

    prior_invalid = object()
    prior_expired = object()
    reused = SimpleNamespace(
        uuid="reused-edge",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="HAS_BALANCE",
        fact="USER has 10,000 miles",
        invalid_at=prior_invalid,
        expired_at=prior_expired,
    )
    newer_candidate = SimpleNamespace(
        uuid="newer-candidate",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="EARNED",
        fact="USER earned 10,000 miles",
        invalid_at=None,
        expired_at=None,
    )
    extracted = SimpleNamespace(
        uuid="extracted-edge",
        source_node_uuid="user",
        target_node_uuid="miles",
        name="EARNED",
        fact="USER earned 10,000 miles",
        invalid_at=None,
        expired_at=None,
    )

    async def original(_client, _extracted, related, _existing, _episode, _types=None):
        resolved = related[0]
        resolved.invalid_at = "new-invalid-at"
        resolved.expired_at = "new-expired-at"
        # Graphiti's newer-candidate branch mutates resolved but returns no invalidation.
        return resolved, [], [resolved]

    monkeypatch.setattr(edge_operations, "resolve_extracted_edge", original)
    diagnostics: list[dict[str, object]] = []
    restore = install_edge_invalidation_predicate_pushdown(diagnostics)

    async def scenario() -> None:
        result = await edge_operations.resolve_extracted_edge(
            object(), extracted, [reused], [newer_candidate], object(), None
        )
        assert result == (reused, [], [reused])
        assert reused.invalid_at is prior_invalid
        assert reused.expired_at is prior_expired

    try:
        asyncio.run(scenario())
        assert diagnostics[0]["llm_invalidation_proposal_count"] == 0
        assert diagnostics[0]["reused_resolved_edge_temporal_snapshot_present"] is True
        assert diagnostics[0]["reused_resolved_edge_temporal_mutation_rolled_back"] is True
        inventory = extraction_work_inventory(diagnostics)
        assert inventory["edge_reused_resolved_temporal_snapshots"] == 1
        assert inventory["edge_reused_resolved_temporal_mutations_rolled_back"] == 1
    finally:
        restore()


def test_summary_response_rejects_unknown_entities_and_records_coverage() -> None:
    from graphiti_core.utils.text_utils import MAX_SUMMARY_CHARS

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, _messages, response_model=None, **_kwargs):
            schema = response_model.model_json_schema()
            summaries = schema["properties"]["summaries"]
            assert summaries["maxItems"] == 2
            item_ref = summaries["items"]["$ref"].rsplit("/", 1)[-1]
            assert schema["$defs"][item_ref]["properties"]["summary"]["maxLength"] == (
                MAX_SUMMARY_CHARS
            )
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            return {
                "summaries": [
                    {"name": "Notion", "summary": "Notion is a workspace."},
                    {"name": "baggage restrictions", "summary": "Unrelated output."},
                ]
            }

    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        '<ENTITIES>[{"name":"Notion"},{"name":"Evernote"}]'
                        "</ENTITIES>"
                    ),
                }
            ],
            prompt_name="extract_nodes.extract_summaries_batch",
        )
    )

    assert result == {
        "summaries": [{"name": "Notion", "summary": "Notion is a workspace."}]
    }
    audits = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "SUMMARY_RESPONSE_AUDIT"
    ]
    assert audits == [
        {
            "schema_version": "membind.v6.1.summary-response-audit.v1",
            "event": "SUMMARY_RESPONSE_AUDIT",
            "prompt_name": "extract_nodes.extract_summaries_batch",
            "requested_entity_count": 2,
            "returned_summary_count": 2,
            "accepted_summary_count": 1,
            "unknown_summary_count": 1,
            "duplicate_summary_count": 0,
            "omitted_requested_count": 1,
            "schema_max_items": 2,
            "schema_summary_max_chars": MAX_SUMMARY_CHARS,
            "status": "filtered_unknown",
        }
    ]


def test_summary_entity_partition_merges_pages_without_dropping_entities() -> None:
    class Client:
        max_tokens = 32_768

        def __init__(self) -> None:
            self.call_events: list[dict[str, object]] = []
            self.page_names: list[list[str]] = []

        async def generate_response(self, messages, response_model=None, **_kwargs):
            content = messages[0]["content"]
            start = content.index("<ENTITIES>") + len("<ENTITIES>")
            end = content.index("</ENTITIES>")
            entities = json.loads(content[start:end])
            names = [str(item["name"]) for item in entities]
            self.page_names.append(names)
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 8},
                }
            )
            return {"summaries": [{"name": name, "summary": f"summary:{name}"} for name in names]}

    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        summary_entity_page_capacity=2,
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        '<ENTITIES>[{"name":"A"},{"name":"B"},{"name":"C"},'
                        '{"name":"D"},{"name":"E"}]</ENTITIES>'
                    ),
                }
            ],
            prompt_name="extract_nodes.extract_summaries_batch",
        )
    )

    assert client.page_names == [["A", "B"], ["C", "D"], ["E"]]
    assert [row["name"] for row in result["summaries"]] == ["A", "B", "C", "D", "E"]
    merge = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "SUMMARY_ENTITY_PARTITION_MERGE"
    ]
    assert merge == [
        {
            "schema_version": "membind.v6.1.summary-partition.v1",
            "event": "SUMMARY_ENTITY_PARTITION_MERGE",
            "prompt_name": "extract_nodes.extract_summaries_batch",
            "entity_count": 5,
            "page_count": 3,
            "page_capacity": 2,
            "status": "merged",
        }
    ]


def test_local_context_budget_adapter_uses_exact_remaining_context() -> None:
    class Completions:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def create(self, **kwargs):
            requested = int(kwargs["max_tokens"])
            self.calls.append(requested)
            return {"ok": True}

    completions = Completions()
    llm = SimpleNamespace(
        max_tokens=32_768,
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        ),
    )
    restore = install_local_context_budget_adapter(llm, token_counter=lambda _messages: 40_000)
    try:
        result = asyncio.run(completions.create(messages=[{"role": "user", "content": "x"}], max_tokens=32_768))
    finally:
        restore()
    assert result == {"ok": True}
    assert completions.calls == [25_504]


def test_local_context_budget_adapter_fails_closed_on_tokenizer_drift() -> None:
    class Completions:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def create(self, **kwargs):
            requested = int(kwargs["max_tokens"])
            self.calls.append(requested)
            if len(self.calls) == 1:
                raise RuntimeError(
                    "maximum context length is 65536 tokens; requested 32768 output tokens "
                    "and prompt contains at least 40000 input tokens"
                )
            return {"ok": True}

    completions = Completions()
    llm = SimpleNamespace(
        max_tokens=32_768,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    restore = install_local_context_budget_adapter(llm, token_counter=lambda _messages: 1_000)
    try:
        with pytest.raises(RuntimeError, match="maximum context length"):
            asyncio.run(
                completions.create(messages=[{"role": "user", "content": "x"}], max_tokens=32_768)
            )
    finally:
        restore()
    assert completions.calls == [32_768]


def test_local_context_budget_adapter_does_not_retry_unrelated_errors() -> None:
    class Completions:
        async def create(self, **_kwargs):
            raise RuntimeError("provider unavailable")

    completions = Completions()
    llm = SimpleNamespace(
        max_tokens=32_768,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    restore = install_local_context_budget_adapter(llm, token_counter=lambda _messages: 1_000)
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            asyncio.run(
                completions.create(
                    messages=[{"role": "user", "content": "x"}], max_tokens=32_768
                )
            )
    finally:
        restore()


def test_local_openai_transport_uses_long_timeout_without_sdk_retries(monkeypatch) -> None:
    captured = {}

    class AsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=AsyncOpenAI))
    client = build_local_openai_transport(
        api_key="test-key",
        base_url="http://127.0.0.1:18100/v1",
    )
    assert isinstance(client, AsyncOpenAI)
    http_client = captured.pop("http_client")
    assert captured == {
        "api_key": "test-key",
        "base_url": "http://127.0.0.1:18100/v1",
        "timeout": 3_600.0,
        "max_retries": 0,
    }
    assert type(http_client).__module__.startswith("httpx2")
    asyncio.run(http_client.aclose())


def test_runtime_close_attempts_all_components_and_retries_partial_failure() -> None:
    class Closeable:
        def __init__(self, *, fail_once: bool = False) -> None:
            self.calls = 0
            self.fail_once = fail_once

        async def close(self) -> None:
            self.calls += 1
            if self.fail_once and self.calls == 1:
                raise RuntimeError("first close failed")

    async def scenario() -> None:
        graphiti = Closeable()
        construction = Closeable(fail_once=True)
        embedding = Closeable()
        runtime = SimpleNamespace(
            graphiti=graphiti,
            _membind_owned_transports=(construction, embedding, construction),
            _membind_runtime_closed=False,
        )
        with pytest.raises(RuntimeError, match="first close failed"):
            await close_local_u0_runtime(runtime)
        assert (graphiti.calls, construction.calls, embedding.calls) == (1, 1, 1)
        assert runtime._membind_runtime_closed is False
        await close_local_u0_runtime(runtime)
        assert (graphiti.calls, construction.calls, embedding.calls) == (2, 2, 2)
        assert runtime._membind_runtime_closed is True
        await close_local_u0_runtime(runtime)
        assert (graphiti.calls, construction.calls, embedding.calls) == (2, 2, 2)

    asyncio.run(scenario())


def test_live_journal_group_commits_high_frequency_events(tmp_path, monkeypatch) -> None:
    fsync_calls = 0

    def observe_fsync(_fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1

    monkeypatch.setattr("saturated_fixed_work_baseline_v1_3.membind_v6_1.mab.os.fsync", observe_fsync)
    journal_path = tmp_path / "live.jsonl"
    journal = _Journal(journal_path)
    journal.append({"event": "ADMISSION_ENQUEUE"})
    journal.append({"event": "ADMISSION_ADMIT"})
    assert fsync_calls == 0
    journal.append({"event": "PUBLICATION_DURABLE"}, durable=True)
    assert fsync_calls == 1
    journal.close()
    assert fsync_calls == 2
    assert [json.loads(line)["event"] for line in journal_path.read_text().splitlines()] == [
        "ADMISSION_ENQUEUE",
        "ADMISSION_ADMIT",
        "PUBLICATION_DURABLE",
    ]


def test_local_graphiti_retry_policy_executes_one_attempt() -> None:
    class Client:
        async def _generate_response(self, *args, **kwargs):
            return {"args": args, "kwargs": kwargs}

    client = Client()
    install_local_single_attempt_policy(client)
    result = asyncio.run(client._generate_response_with_retry("message", max_tokens=8))
    assert result == {"args": ("message",), "kwargs": {"max_tokens": 8}}


def test_local_extraction_chunking_merges_turn_partitions(monkeypatch) -> None:
    calls: list[tuple[str, int | None]] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(
            self,
            messages,
            response_model=None,
            max_tokens=None,
            model_size=None,
            group_id=None,
            prompt_name=None,
            *,
            attribute_extraction=False,
        ):
            content = next(item["content"] for item in messages if isinstance(item, dict))
            current = content.split("<CURRENT MESSAGE>", 1)[1].split("</CURRENT MESSAGE>", 1)[0]
            calls.append((current.strip(), max_tokens))
            first = "A" if "turn-a" in current else "B"
            return {
                "extracted_entities": [
                    {"name": first, "entity_type_id": 0, "episode_indices": [0]}
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda messages: (
            60_000
            if "turn-a" in str(messages) and "turn-b" in str(messages)
            else 1_000
        ),
        chunk_char_limit=8,
    )
    result = asyncio.run(
        client.generate_response(
            [
                {"role": "user", "content": "<CURRENT MESSAGE>\n[USER]\nturn-a\n[ASSISTANT]\nturn-b\n</CURRENT MESSAGE>"}
            ],
            prompt_name="extract_nodes.extract_message",
        )
    )
    assert len(calls) == 2
    assert all(budget == 8_192 for _, budget in calls)
    assert result["extracted_entities"] == [
        {"name": "A", "entity_type_id": 0, "episode_indices": [0]},
        {"name": "B", "entity_type_id": 0, "episode_indices": [0]},
    ]
    assert client._membind_entity_partition_sources == {
        0: "[USER]\nturn-a\n",
        1: "[ASSISTANT]\nturn-b\n",
    }


def test_node_partition_pipeline_preserves_order_and_shared_physical_cap(monkeypatch) -> None:
    active = 0
    max_active = 0
    completion_order: list[str] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(self, messages, **_kwargs):
            nonlocal active, max_active
            content = next(item["content"] for item in messages if isinstance(item, dict))
            current = content.split("<CURRENT MESSAGE>", 1)[1].split(
                "</CURRENT MESSAGE>", 1
            )[0]
            label = next(value for value in ("Alpha", "Beta", "Gamma") if value in current)
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep({"Alpha": 0.03, "Beta": 0.01, "Gamma": 0.0}[label])
                completion_order.append(label)
                return {
                    "extracted_entities": [
                        {"name": "Shared", "entity_type_id": 0, "episode_indices": [0]},
                        {"name": label, "entity_type_id": 0, "episode_indices": [0]},
                    ]
                }
            finally:
                active -= 1

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        chunk_char_limit=8,
        partition_extraction_by_turns=True,
        node_partition_concurrency=2,
    )

    async def extract(source_sequence: int):
        with provider_scope(region="PREPARE", source_sequence=source_sequence):
            return await client.generate_response(
                [
                    {
                        "role": "user",
                        "content": (
                            "<CURRENT MESSAGE>\n[USER]\nShared Alpha\n"
                            "[ASSISTANT]\nShared Beta\n[USER]\nShared Gamma\n"
                            "</CURRENT MESSAGE>"
                        ),
                    }
                ],
                prompt_name="extract_nodes.extract_message",
            )

    async def scenario():
        return await asyncio.gather(extract(0), extract(1))

    first, second = asyncio.run(scenario())

    assert max_active == 2
    assert completion_order[:2] != ["Alpha", "Beta"]
    expected_names = ["Shared", "Alpha", "Beta", "Gamma"]
    assert [row["name"] for row in first["extracted_entities"]] == expected_names
    assert [row["name"] for row in second["extracted_entities"]] == expected_names
    for source_sequence in (0, 1):
        hints = client._membind_entity_partition_hints_by_scope[("PREPARE", source_sequence)]
        assert hints["shared"] == [0, 1, 2]
    expansions = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.node-partition-pipeline.v1"
    ]
    assert len(expansions) == 2
    assert all(row["partition_worker_concurrency"] == 2 for row in expansions)
    assert all(row["physical_partition_concurrency"] == 2 for row in expansions)
    assert all(row["shared_max_active_partition_requests"] == 2 for row in expansions)
    assert all(row["merge_partition_order"] == [0, 1, 2] for row in expansions)
    inventory = extraction_work_inventory(client._membind_extraction_diagnostics)
    assert inventory["node_partition_pipeline_calls"] == 2
    assert inventory["node_partition_pipeline_partitions"] == 6
    assert inventory["node_partition_pipeline_max_active"] == 2


def test_node_partition_pipeline_releases_permits_after_sibling_failure(monkeypatch) -> None:
    fail_once = True

    class Client:
        max_tokens = 32_768

        async def generate_response(self, messages, **_kwargs):
            nonlocal fail_once
            content = next(item["content"] for item in messages if isinstance(item, dict))
            current = content.split("<CURRENT MESSAGE>", 1)[1].split(
                "</CURRENT MESSAGE>", 1
            )[0]
            if fail_once and "Beta" in current:
                fail_once = False
                raise RuntimeError("injected node partition failure")
            await asyncio.sleep(0)
            name = "Alpha" if "Alpha" in current else "Beta"
            return {
                "extracted_entities": [
                    {"name": name, "entity_type_id": 0, "episode_indices": [0]}
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        chunk_char_limit=8,
        partition_extraction_by_turns=True,
        node_partition_concurrency=2,
    )
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>\n[USER]\nAlpha\n"
                "[ASSISTANT]\nBeta\n</CURRENT MESSAGE>"
            ),
        }
    ]

    async def scenario():
        with pytest.raises(RuntimeError, match="injected node partition failure"):
            await client.generate_response(
                messages,
                prompt_name="extract_nodes.extract_message",
            )
        return await asyncio.wait_for(
            client.generate_response(
                messages,
                prompt_name="extract_nodes.extract_message",
            ),
            timeout=1,
        )

    recovered = asyncio.run(scenario())
    assert [row["name"] for row in recovered["extracted_entities"]] == ["Alpha", "Beta"]


def test_8b_extraction_partitions_dialogue_below_context_trigger(monkeypatch) -> None:
    calls: list[str] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(
            self,
            messages,
            response_model=None,
            max_tokens=None,
            model_size=None,
            group_id=None,
            prompt_name=None,
            *,
            attribute_extraction=False,
        ):
            content = messages[0]["content"]
            current = content.split("<CURRENT MESSAGE>", 1)[1].split(
                "</CURRENT MESSAGE>", 1
            )[0]
            calls.append(current)
            return {"edges": []}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 4_700,
        chunk_char_limit=16,
        partition_extraction_by_turns=True,
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nfirst-turn\n"
                        "[ASSISTANT]\nsecond-turn\n</CURRENT MESSAGE>"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )
    assert result == {"edges": []}
    assert len(calls) == 2


def test_partitioned_node_extraction_uses_bounded_structured_schema(monkeypatch) -> None:
    schemas: list[dict[str, object]] = []

    class Client:
        max_tokens = 32_768

        async def generate_response(self, _messages, response_model=None, **_kwargs):
            schemas.append(response_model.model_json_schema())
            return {"extracted_entities": []}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        chunk_char_limit=16,
        partition_extraction_by_turns=True,
    )
    asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nalpha beta gamma\n"
                        "[ASSISTANT]\ndelta epsilon\n</CURRENT MESSAGE>"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_nodes.extract_message",
        )
    )
    assert len(schemas) == 2
    for schema in schemas:
        entities = schema["properties"]["extracted_entities"]
        assert 1 <= entities["maxItems"] <= 64
        item_ref = entities["items"]["$ref"].rsplit("/", 1)[1]
        item = schema["$defs"][item_ref]
        assert item["properties"]["name"]["maxLength"] == 256
        assert item["properties"]["episode_indices"]["maxItems"] == 1
    diagnostics = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.extraction-diagnostic.v1"
    ]
    assert all(row["node_schema_max_items"] <= 64 for row in diagnostics)
    assert all(row["node_schema_name_max_chars"] == 256 for row in diagnostics)


def test_node_extraction_filters_ungrounded_and_duplicate_entities(monkeypatch) -> None:
    class Client:
        max_tokens = 32_768

        async def generate_response(self, _messages, **_kwargs):
            return {
                "extracted_entities": [
                    {"name": "A.J. Finn", "entity_type_id": 0, "episode_indices": [0]},
                    {
                        "name": "The Silent Patient",
                        "entity_type_id": 0,
                        "episode_indices": [0],
                    },
                    {"name": "GameCube", "entity_type_id": 0, "episode_indices": [0]},
                    {"name": "A.J. Finn", "entity_type_id": 0, "episode_indices": [0]},
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(client, token_counter=lambda _messages: 100)
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA. J. Finn recommended "
                        "The Silent Patient.\n</CURRENT MESSAGE>"
                    ),
                }
            ],
            prompt_name="extract_nodes.extract_message",
        )
    )
    assert [row["name"] for row in result["extracted_entities"]] == [
        "A.J. Finn",
        "The Silent Patient",
    ]
    audit = next(
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "NODE_RESPONSE_AUDIT"
    )
    assert audit["returned_entity_count"] == 4
    assert audit["accepted_entity_count"] == 2
    assert audit["lexically_grounded_count"] == 2
    assert audit["ungrounded_entity_count"] == 1
    assert audit["duplicate_entity_count"] == 1
    assert audit["status"] == "filtered_ungrounded"
    assert audit["schema_max_items"] <= 64
    assert len(audit["source_text_sha256"]) == 64
    assert "source_text" not in audit
    inventory = extraction_work_inventory(client._membind_extraction_diagnostics)
    assert inventory["node_response_audits"] == 1
    assert inventory["node_returned_entities"] == 4
    assert inventory["node_accepted_entities"] == 2
    assert inventory["node_ungrounded_rejected"] == 1
    assert inventory["node_duplicate_rejected"] == 1


def test_edge_candidate_expansion_is_complete_and_content_scoped() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>\n[USER]\nA mentioned B and C.\n"
                "</CURRENT MESSAGE>\n<ENTITIES>\n"
                "[{\"name\": \"A\", \"entity_types\": [\"Entity\"]}, "
                "{\"name\": \"B\", \"entity_types\": [\"Entity\"]}, "
                "{\"name\": \"C\", \"entity_types\": [\"Entity\"]}]\n"
                "</ENTITIES>\n# TASK\nExtract edges."
            ),
        }
    ]
    expanded = _edge_pair_partitions(messages)
    assert expanded is not None
    partitions, entity_count, partition_count = expanded
    assert (entity_count, partition_count) == (3, 3)
    assert all("EDGE_PARTITION_SCOPE" in item[0]["content"] for item in partitions)
    names = []
    for item in partitions:
        body = item[0]["content"].split("<ENTITIES>", 1)[1].split("</ENTITIES>", 1)[0]
        names.append(tuple(value["name"] for value in json.loads(body)))
    assert {frozenset(value) for value in names} == {
        frozenset(("A", "B")),
        frozenset(("A", "C")),
        frozenset(("B", "C")),
    }


def test_edge_candidate_merge_deduplicates_without_losing_facts() -> None:
    edge_ab = {
        "source_entity_name": "A",
        "target_entity_name": "B",
        "relation_type": "KNOWS",
        "fact": "A knows B",
        "valid_at": None,
        "invalid_at": None,
        "episode_indices": [0],
    }
    edge_bc = {
        "source_entity_name": "B",
        "target_entity_name": "C",
        "relation_type": "VISITS",
        "fact": "B visits C",
        "valid_at": None,
        "invalid_at": None,
        "episode_indices": [0],
    }
    merged = _merge_extraction_responses(
        "extract_edges.edge",
        [{"edges": [edge_ab]}, {"edges": [dict(edge_ab), edge_bc]}],
    )
    assert len(merged["edges"]) == 2
    assert {row["fact"] for row in merged["edges"]} == {"A knows B", "B visits C"}
    assert all(row["episode_indices"] == [0] for row in merged["edges"])


def test_edge_turn_local_partition_reuses_node_provenance_and_overlaps_adjacent_turns() -> None:
    sources = {
        0: "[USER]\nA and B\n",
        1: "[ASSISTANT]\nB and C\n",
        2: "[USER]\nC and D\n",
    }
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>\n[USER]\nA and B\n[ASSISTANT]\n"
                "B and C\n[USER]\nC and D\n</CURRENT MESSAGE>\n<ENTITIES>\n"
                "[{\"name\":\"A\"},{\"name\":\"B\"},{\"name\":\"C\"},{\"name\":\"D\"}]\n"
                "</ENTITIES>"
            ),
        }
    ]
    expanded = _edge_turn_local_partitions(
        messages,
        entity_partition_hints={
            "a": [0],
            "b": [0, 1],
            "c": [1, 2],
            "d": [2],
        },
        entity_partition_sources=sources,
    )
    assert expanded is not None
    partitions, entity_count, partition_count, max_size = expanded
    assert entity_count == 4
    assert partition_count == 5
    assert max_size == 3
    assert all("EDGE_PARTITION_SCOPE" in item[0]["content"] for item in partitions)
    evidence_texts = {
        item[0]["content"].split("<CURRENT MESSAGE>", 1)[1].split(
            "</CURRENT MESSAGE>", 1
        )[0]
        for item in partitions
    }
    assert evidence_texts == {
        sources[0],
        sources[1],
        sources[2],
        sources[0] + sources[1],
        sources[1] + sources[2],
    }
    assert all("C and D" not in text for text in evidence_texts if text == sources[0])


def test_actor_domain_cover_structurally_separates_user_state_and_discourse() -> None:
    sources = {
        0: "[USER]\nI have 10,000 SkyMiles and fly Spirit Airlines.\n",
        1: "[ASSISTANT]\nAirbnb and VRBO serve Miami.\n",
    }
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>" + sources[0] + sources[1] + "</CURRENT MESSAGE>\n"
                '<ENTITIES>[{"name":"USER"},{"name":"ASSISTANT"},'
                '{"name":"SkyMiles"},{"name":"Spirit Airlines"},'
                '{"name":"Airbnb"},{"name":"VRBO"},{"name":"Miami"}]</ENTITIES>'
            ),
        }
    ]
    metadata: list[dict[str, object]] = []
    expanded = _edge_turn_local_partitions(
        messages,
        entity_partition_hints={
            "user": [0],
            "assistant": [1],
            "skymiles": [0],
            "spirit airlines": [0],
            "airbnb": [1],
            "vrbo": [1],
            "miami": [1],
        },
        entity_partition_sources=sources,
        partition_metadata=metadata,
        actor_domain_cover=True,
    )
    assert expanded is not None
    partitions, _, partition_count, max_size = expanded
    assert partition_count == 5
    assert max_size == 5
    assert [row["evidence_view_kind"] for row in metadata] == [
        "user_state",
        "domain",
        "domain",
        "user_state",
        "domain",
    ]
    entity_sets = []
    evidence = []
    for partition in partitions:
        content = partition[0]["content"]
        entity_sets.append(
            {
                value["name"]
                for value in json.loads(
                    content.split("<ENTITIES>", 1)[1].split("</ENTITIES>", 1)[0]
                )
            }
        )
        evidence.append(
            content.split("<CURRENT MESSAGE>", 1)[1].split("</CURRENT MESSAGE>", 1)[0]
        )
    assert {"USER", "SkyMiles", "Spirit Airlines"} in entity_sets
    assert {"Airbnb", "VRBO", "Miami"} in entity_sets
    for names, text in zip(entity_sets, evidence, strict=True):
        if "USER" in names:
            assert "[ASSISTANT]" not in text
        if names == {"Airbnb", "VRBO", "Miami"}:
            assert "ASSISTANT" not in names


def test_actor_domain_cover_rejects_domain_entity_without_local_text_evidence() -> None:
    source = "[ASSISTANT]\nEvernote is useful for a book journal.\n"
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>" + source + "</CURRENT MESSAGE>"
                '<ENTITIES>[{"name":"Evernote"},{"name":"book journal"},'
                '{"name":"Airbnb"}]</ENTITIES>'
            ),
        }
    ]
    expanded = _edge_turn_local_partitions(
        messages,
        entity_partition_hints={
            "evernote": [0],
            "book journal": [0],
            # A retrieved/hallucinated entity must not enter the edge view.
            "airbnb": [0],
        },
        entity_partition_sources={0: source},
        actor_domain_cover=True,
    )
    assert expanded is not None
    partitions, _, partition_count, _ = expanded
    assert partition_count == 1
    names = json.loads(
        partitions[0][0]["content"].split("<ENTITIES>", 1)[1].split(
            "</ENTITIES>", 1
        )[0]
    )
    assert {value["name"] for value in names} == {"Evernote", "book journal"}


def test_actor_domain_cover_can_prune_only_adjacent_domain_work() -> None:
    sources = {
        0: "[USER]\nI use SkyMiles.\n",
        1: "[ASSISTANT]\nAirbnb serves Miami.\n",
    }
    metadata: list[dict[str, object]] = []
    expanded = _edge_turn_local_partitions(
        [
            {
                "role": "user",
                "content": (
                    "<CURRENT MESSAGE>" + sources[0] + sources[1] + "</CURRENT MESSAGE>"
                    '<ENTITIES>[{"name":"USER"},{"name":"SkyMiles"},'
                    '{"name":"Airbnb"},{"name":"Miami"}]</ENTITIES>'
                ),
            }
        ],
        entity_partition_hints={
            "user": [0],
            "skymiles": [0],
            "airbnb": [1],
            "miami": [1],
        },
        entity_partition_sources=sources,
        partition_metadata=metadata,
        actor_domain_cover=True,
        actor_domain_adjacent_domain=False,
    )
    assert expanded is not None
    _, _, partition_count, _ = expanded
    assert partition_count == 3
    assert [row["evidence_view_kind"] for row in metadata] == [
        "user_state",
        "domain",
        "user_state",
    ]
    assert all(
        not (
            len(row["evidence_source_partition_ids"]) == 2
            and row["evidence_view_kind"] == "domain"
        )
        for row in metadata
    )


def test_actor_domain_boundary_join_is_separate_from_base_domain_cover() -> None:
    sources = {
        0: "[USER]\nI need a hotel in Miami.\n",
        1: "[ASSISTANT]\nJazz Hostel is in Miami.\n",
    }
    metadata: list[dict[str, object]] = []
    expanded = _edge_turn_local_partitions(
        [
            {
                "role": "user",
                "content": (
                    "<CURRENT MESSAGE>" + sources[0] + sources[1] + "</CURRENT MESSAGE>"
                    '<ENTITIES>[{"name":"USER"},{"name":"hotel"},'
                    '{"name":"Miami"},{"name":"Jazz Hostel"}]</ENTITIES>'
                ),
            }
        ],
        entity_partition_hints={
            "user": [0],
            "hotel": [0],
            "miami": [0],
            "jazz hostel": [1],
        },
        entity_partition_sources=sources,
        partition_metadata=metadata,
        actor_domain_cover=True,
        actor_domain_adjacent_domain=False,
        actor_domain_boundary_join=True,
    )
    assert expanded is not None
    assert any(row["evidence_view_kind"] == "domain_boundary_join" for row in metadata)
    boundary = next(
        row for row in metadata if row["evidence_view_kind"] == "domain_boundary_join"
    )
    assert boundary["cross_boundary_required"] is True
    assert boundary["_cross_left_endpoint_names"] == {"hotel", "miami"}
    assert boundary["_cross_right_endpoint_names"] == {"jazz hostel"}


def test_edge_candidate_policy_expands_every_physical_call_and_records_diagnostics(
    monkeypatch,
) -> None:
    calls: list[tuple[str, int | None]] = []

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, max_tokens=None, **_kwargs):
            body = messages[0]["content"].split("<ENTITIES>", 1)[1].split(
                "</ENTITIES>", 1
            )[0]
            names = tuple(value["name"] for value in json.loads(body))
            calls.append(("|".join(names), max_tokens))
            returned_block = messages[0]["content"].split(
                "<ALREADY_RETURNED_EDGES>", 1
            )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            if json.loads(returned_block) if returned_block.strip() else []:
                return {"edges": []}
            return {
                "edges": [
                    {
                        "source_entity_name": names[0],
                        "target_entity_name": names[1],
                        "relation_type": "RELATED_TO",
                        "fact": f"{names[0]} relates to {names[1]}",
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    client._membind_entity_partition_hints.update(
        {"a": [0, 2], "b": [0, 1], "c": [1, 2]}
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                        "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B\n[ASSISTANT]\nB C\n"
                        "[USER]\nC A\n</CURRENT MESSAGE>\n"
                        "<ENTITIES>[{\"name\":\"A\"},{\"name\":\"B\"},"
                        "{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )
    assert len(calls) == 10
    assert all(budget == 16_384 for _, budget in calls)
    assert len(result["edges"]) == 3
    physical = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.extraction-diagnostic.v1"
    ]
    assert [row["partition_id"] for row in physical] == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert all(row["partition_count"] == 5 for row in physical)
    assert [row["page_index"] for row in physical] == [0, 1] * 5
    assert all(row["distinct_entity_count"] in {2, 3} for row in physical)
    assert client._membind_extraction_diagnostics[-1]["status"] == "merged"
    expansion = client._membind_extraction_diagnostics[-1]
    assert expansion["duplicate_recovery_enabled"] is False
    assert expansion["evidence_window_count"] == 5
    assert expansion["evidence_source_partition_ids"] == [
        [0],
        [1],
        [2],
        [0, 1],
        [1, 2],
    ]
    assert all(len(value) == 64 for row in expansion["evidence_source_hashes"] for value in row)


def test_shared_edge_substrate_enforces_wire_budget_when_client_is_32768(
    monkeypatch,
) -> None:
    calls: list[int | None] = []

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, max_tokens=None, **_kwargs):
            calls.append(max_tokens)
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            )
            return {"edges": []}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        edge_page_capacity=2,
        shared_bounded_structured_output=True,
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\nA relates to B\n</CURRENT MESSAGE>\n"
                        '<ENTITIES>[{"name":"A"},{"name":"B"}]</ENTITIES>\n# TASK'
                    ),
                }
            ],
            max_tokens=32_768,
            prompt_name="extract_edges.edge",
        )
    )
    assert result == {"edges": []}
    assert calls and all(value == 16_384 for value in calls)
    rows = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("schema_version") == "membind.v6.1.extraction-diagnostic.v1"
    ]
    assert rows
    assert all(row["requested_max_tokens"] == 16_384 for row in rows)
    assert all(row["requested_edge_page_capacity"] == 1 for row in rows)
    assert all(row["certified_edge_page_capacity"] == 1 for row in rows)
    assert all(row["shared_structured_output_wire_max_tokens"] == 16_384 for row in rows)
    assert all(row["shared_structured_output_construction_max_tokens"] == 32_768 for row in rows)


def test_shared_duplicate_recovery_accepts_explicit_no_additional_edge(
    monkeypatch,
) -> None:
    prompts: list[str] = []

    def edge() -> dict[str, object]:
        return {
            "source_entity_name": "A",
            "target_entity_name": "B",
            "relation_type": "KNOWS",
            "fact": "A knows B",
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, response_model=None, **_kwargs):
            content = messages[0]["content"]
            prompts.append(content)
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            if len(prompts) == 1:
                assert response_model.model_json_schema()["properties"]["edges"]["maxItems"] == 1
                return {"edges": [edge()]}
            if len(prompts) == 2:
                assert "<DUPLICATE_RECOVERY>" not in content
                return {"edges": [edge()]}
            schema = response_model.model_json_schema()
            assert schema["properties"]["status"]["enum"] == ["new_edge", "no_additional_edge"]
            assert "<DUPLICATE_RECOVERY>" in content
            assert "<FINAL_DUPLICATE_RECOVERY_DIRECTIVE>" in content
            return {"status": "no_additional_edge", "edge": None}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        edge_duplicate_recovery=True,
        edge_page_capacity=2,
        shared_bounded_structured_output=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [0]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA knows B\n</CURRENT MESSAGE>\n"
                        '<ENTITIES>[{"name":"A"},{"name":"B"},{"name":"C"}]</ENTITIES>\n# TASK'
                    ),
                }
            ],
            max_tokens=32_768,
            prompt_name="extract_edges.edge",
        )
    )
    assert result["edges"][0]["fact"] == "A knows B"
    assert len(prompts) == 3
    pages = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    recovery_pages = [row for row in pages if row["duplicate_recovery_request"]]
    assert recovery_pages
    assert recovery_pages[-1]["recovery_status"] == "no_additional_edge"


def test_shared_duplicate_recovery_rejects_missing_null_edge(monkeypatch) -> None:
    calls = 0

    def edge() -> dict[str, object]:
        return {
            "source_entity_name": "A",
            "target_entity_name": "B",
            "relation_type": "KNOWS",
            "fact": "A knows B",
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, _messages, **_kwargs):
            nonlocal calls
            calls += 1
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            if calls <= 2:
                return {"edges": [edge()]}
            return {"status": "no_additional_edge"}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        edge_duplicate_recovery=True,
        shared_bounded_structured_output=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [0]})
    with pytest.raises(
        LocalRuntimeConfigurationError,
        match="must contain edge:null",
    ):
        asyncio.run(
            client.generate_response(
                [
                    {
                        "role": "user",
                        "content": (
                            "<CURRENT MESSAGE>\nA knows B\n</CURRENT MESSAGE>\n"
                            '<ENTITIES>[{"name":"A"},{"name":"B"},'
                            '{"name":"C"}]</ENTITIES>\n# TASK'
                        ),
                    }
                ],
                max_tokens=32_768,
                prompt_name="extract_edges.edge",
            )
        )


def test_edge_pagination_duplicate_page_is_audited_zero_delta_fixed_point(monkeypatch) -> None:
    calls = 0

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, _messages, **_kwargs):
            nonlocal calls
            calls += 1
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            return {
                "edges": [
                    {
                        "source_entity_name": "A",
                        "target_entity_name": "B",
                        "relation_type": "KNOWS",
                        "fact": "A knows B",
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [1]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B\n[ASSISTANT]\nC\n"
                        "</CURRENT MESSAGE>\n<ENTITIES>[{\"name\":\"A\"},"
                        "{\"name\":\"B\"},{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )
    assert len(result["edges"]) == 1
    assert calls == 4
    fixed_points = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_ZERO_DELTA"
    ]
    assert len(fixed_points) == 2
    assert all(row["status"] == "converged" for row in fixed_points)


def test_edge_pagination_accepts_unique_delta_from_single_edge_pages(monkeypatch) -> None:
    calls = 0

    def edge(fact: str) -> dict[str, object]:
        return {
            "source_entity_name": "A",
            "target_entity_name": "B",
            "relation_type": "KNOWS",
            "fact": fact,
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, _messages, response_model=None, **_kwargs):
            nonlocal calls
            calls += 1
            schema = response_model.model_json_schema()
            assert schema["properties"]["edges"]["maxItems"] == 1
            assert schema["title"] == "MemBindSingleEdgePage"
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            returned_block = _messages[0]["content"].split(
                "<ALREADY_RETURNED_EDGES>", 1
            )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
            returned_count = len(json.loads(returned_block))
            if returned_count == 0:
                return {"edges": [edge("A knows B")]}
            if returned_count == 1:
                return {"edges": [edge("A trusts B")]}
            if returned_count == 2:
                return {"edges": [edge("A visits B")]}
            return {"edges": [edge("A visits B")]}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [1]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B\n[ASSISTANT]\nC\n"
                        "</CURRENT MESSAGE>\n<ENTITIES>[{\"name\":\"A\"},"
                        "{\"name\":\"B\"},{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )

    assert {row["fact"] for row in result["edges"]} == {
        "A knows B",
        "A trusts B",
        "A visits B",
    }
    page_rows = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    assert len(page_rows) == 8
    assert all(row["raw_edge_count"] == 1 for row in page_rows)
    assert sorted(row["delta_edge_count"] for row in page_rows) == [0, 0, 1, 1, 1, 1, 1, 1]
    assert sorted(row["duplicate_edge_count"] for row in page_rows) == [0, 0, 0, 0, 0, 0, 1, 1]
    assert all(row["page_capacity"] == 1 for row in page_rows)


def test_single_edge_continuation_preserves_frozen_pre_task_prompt_contract() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                "<CURRENT MESSAGE>\n[USER]\nA B\n</CURRENT MESSAGE>\n"
                "<ENTITIES>[{\"name\":\"A\"},{\"name\":\"B\"}]</ENTITIES>\n"
                "# TASK\nExtract all edges."
            ),
        }
    ]
    page = _edge_page_messages(messages, [], page_capacity=1)
    content = page[0]["content"]
    assert "Return exactly one not-yet-returned factual edge" in content
    assert "Return between one and" not in content
    assert content.index("<EDGE_PAGINATION>") < content.index("# TASK")

    bounded = _edge_page_messages(messages, [], page_capacity=2)
    bounded_content = bounded[0]["content"]
    assert "Return up to 2 distinct not-yet-returned factual edges" in bounded_content
    assert "Return exactly one" not in bounded_content

    utility = _edge_page_messages(
        messages,
        [],
        page_capacity=2,
        memory_utility_order=True,
    )
    utility_content = utility[0]["content"]
    assert "<MEMORY_UTILITY_ORDER>" in utility_content
    assert "Explicit USER state" in utility_content
    assert "Do not convert an option or recommendation" in utility_content
    assert utility_content.index("<MEMORY_UTILITY_ORDER>") < utility_content.index("# TASK")


def test_edge_pagination_recovers_once_from_a_duplicate_before_converging(
    monkeypatch,
) -> None:
    prompts: list[list[dict[str, object]]] = []
    schema_capacities: list[int] = []

    def edge(fact: str) -> dict[str, object]:
        return {
            "source_entity_name": "A",
            "target_entity_name": "B",
            "relation_type": "KNOWS",
            "fact": fact,
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, response_model=None, **_kwargs):
            prompts.append(messages)
            schema_capacities.append(
                response_model.model_json_schema()["properties"]["edges"]["maxItems"]
            )
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            content = messages[0]["content"]
            returned_block = content.split("<ALREADY_RETURNED_EDGES>", 1)[1].split(
                "</ALREADY_RETURNED_EDGES>", 1
            )[0]
            returned = json.loads(returned_block)
            if not returned:
                return {"edges": [edge("A knows B")]}
            if len(returned) == 1 and "<DUPLICATE_RECOVERY>" not in content:
                return {"edges": [edge("A knows B")]}
            if len(returned) == 1:
                return {"edges": [edge("A trusts B")]}
            if len(returned) == 2:
                return {"edges": [edge("A trusts B")]}
            return {"edges": []}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        edge_duplicate_recovery=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [0]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B C\n</CURRENT MESSAGE>\n"
                        "<ENTITIES>[{\"name\":\"A\"},{\"name\":\"B\"},"
                        "{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )

    assert [row["fact"] for row in result["edges"]] == ["A knows B", "A trusts B"]
    assert len(prompts) == 4
    assert "<DUPLICATE_RECOVERY>" in prompts[2][0]["content"]
    assert "<DUPLICATE_RECOVERY>" not in prompts[3][0]["content"]
    recovery_content = prompts[2][0]["content"]
    assert "<FINAL_DUPLICATE_RECOVERY_DIRECTIVE>" in recovery_content
    assert recovery_content.rindex("<FINAL_DUPLICATE_RECOVERY_DIRECTIVE>") > recovery_content.index("# TASK")
    assert '"status":"new_edge"' in recovery_content
    assert '"status":"no_additional_edge"' in recovery_content
    assert '"edge":null' in recovery_content
    assert "never copy the rejected tuple into a new_edge payload" in recovery_content
    assert 'return exactly {"status":"no_additional_edge","edge":null}' in recovery_content
    final_directive = recovery_content.split(
        "<FINAL_DUPLICATE_RECOVERY_DIRECTIVE>", 1
    )[1].split("</FINAL_DUPLICATE_RECOVERY_DIRECTIVE>", 1)[0]
    assert "never copy the rejected tuple into a new_edge payload" in final_directive
    assert 'return exactly {"status":"no_additional_edge","edge":null}' in final_directive
    assert '"source_entity_name":"A"' in final_directive
    assert '"fact":"A knows B"' in final_directive
    rejected_block = recovery_content.split("The previous response repeated this already-returned edge:\n", 1)[1].split(
        "\nThat repeat is not evidence", 1
    )[0]
    assert json.loads(rejected_block) == {
        "source_entity_name": "A",
        "target_entity_name": "B",
        "relation_type": "KNOWS",
        "fact": "A knows B",
        "valid_at": None,
        "invalid_at": None,
    }
    returned_block = recovery_content.split("<ALREADY_RETURNED_EDGES>", 1)[1].split(
        "</ALREADY_RETURNED_EDGES>", 1
    )[0]
    assert json.loads(returned_block) == [
        {
            "source_entity_name": "A",
            "target_entity_name": "B",
            "relation_type": "KNOWS",
            "fact": "A knows B",
            "valid_at": None,
            "invalid_at": None,
        }
    ]
    assert "arm_identity" not in recovery_content
    assert schema_capacities == [1, 1, 1, 1]
    pages = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    assert [row["duplicate_recovery_request"] for row in pages] == [False, False, True, False]
    assert [row["duplicate_recovery_succeeded"] for row in pages] == [False, False, True, False]
    inventory = extraction_work_inventory(client._membind_extraction_diagnostics)
    assert inventory["pagination_duplicate_recovery_requests"] == 1
    assert inventory["pagination_duplicate_recovery_successes"] == 1


def test_edge_partition_workers_overlap_but_pages_remain_sequential(monkeypatch) -> None:
    active = 0
    max_active = 0
    calls_by_partition: dict[str, list[int]] = {}

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, response_model=None, **_kwargs):
            nonlocal active, max_active
            schema = response_model.model_json_schema()
            assert schema["properties"]["edges"]["maxItems"] == 1
            body = messages[0]["content"].split("<ENTITIES>", 1)[1].split(
                "</ENTITIES>", 1
            )[0]
            names = tuple(value["name"] for value in json.loads(body))
            evidence = messages[0]["content"].split("<CURRENT MESSAGE>", 1)[1].split(
                "</CURRENT MESSAGE>", 1
            )[0]
            key = "|".join(names) + ":" + hashlib.sha256(evidence.encode()).hexdigest()
            returned_block = messages[0]["content"].split(
                "<ALREADY_RETURNED_EDGES>", 1
            )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
            page_index = len(json.loads(returned_block))
            calls_by_partition.setdefault(key, []).append(page_index)
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            if page_index:
                return {"edges": []}
            return {
                "edges": [
                    {
                        "source_entity_name": names[0],
                        "target_entity_name": names[1],
                        "relation_type": "RELATED_TO",
                        "fact": key,
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
        edge_partition_concurrency=2,
        edge_physical_concurrency=2,
    )
    client._membind_entity_partition_hints.update(
        {"a": [0, 2], "b": [0, 1], "c": [1, 2]}
    )
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B\n[ASSISTANT]\nB C\n"
                        "[USER]\nC A\n</CURRENT MESSAGE>\n"
                        "<ENTITIES>[{\"name\":\"A\"},{\"name\":\"B\"},"
                        "{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )

    assert max_active == 2
    assert all(pages == [0, 1] for pages in calls_by_partition.values())
    assert [row["fact"] for row in result["edges"]] == list(calls_by_partition)
    expansion = client._membind_extraction_diagnostics[-1]
    assert expansion["partition_worker_concurrency"] == 2
    assert expansion["physical_page_concurrency"] == 2
    assert expansion["max_active_page_requests"] == 2
    assert expansion["shared_max_active_page_requests"] == 2
    assert expansion["merge_partition_order"] == list(range(expansion["partition_count"]))
    assert expansion["pages_per_partition"] == [2] * expansion["partition_count"]
    page_rows = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    assert all(row["queue_wait_ns"] >= 0 for row in page_rows)
    assert all(row["service_ns"] > 0 for row in page_rows)


def test_edge_pagination_rejects_partition_external_endpoints(monkeypatch) -> None:
    prompts: list[list[dict[str, object]]] = []

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, **_kwargs):
            prompts.append(messages)
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            return {
                "edges": [
                    {
                        "source_entity_name": "A",
                        "target_entity_name": "PROVIDES",
                        "relation_type": "KNOWS",
                        "fact": "invalid endpoint",
                        "valid_at": None,
                        "invalid_at": None,
                        "episode_indices": [0],
                    }
                ]
            }

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [1]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B\n[ASSISTANT]\nC\n"
                        "</CURRENT MESSAGE>\n<ENTITIES>[{\"name\":\"A\"},"
                        "{\"name\":\"B\"},{\"name\":\"C\"}]</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )

    assert result == {"edges": []}
    pages = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    assert len(prompts) == 4
    assert sum(row["invalid_endpoint_edge_count"] for row in pages) == 4
    assert [row["raw_unique_progress_edge_count"] for row in pages] == [1, 0, 1, 0]
    assert [row["delta_edge_count"] for row in pages] == [0, 0, 0, 0]
    fixed_points = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_ZERO_DELTA"
    ]
    assert len(fixed_points) == 2
    for continuation in (prompts[1], prompts[3]):
        returned_block = continuation[0]["content"].split(
            "<ALREADY_RETURNED_EDGES>", 1
        )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
        returned = json.loads(returned_block)
        assert [row["fact"] for row in returned] == ["invalid endpoint"]


def test_edge_pagination_continues_from_invalid_endpoint_to_later_valid_edge(
    monkeypatch,
) -> None:
    prompts: list[list[dict[str, object]]] = []

    def edge(target: str, fact: str) -> dict[str, object]:
        return {
            "source_entity_name": "A",
            "target_entity_name": target,
            "relation_type": "KNOWS",
            "fact": fact,
            "valid_at": None,
            "invalid_at": None,
            "episode_indices": [0],
        }

    class Client:
        max_tokens = 32_768
        call_events: list[dict[str, object]] = []

        async def generate_response(self, messages, **_kwargs):
            prompts.append(messages)
            self.call_events.append(
                {
                    "finish_reason": "stop",
                    "token_usage": {"prompt_tokens": 100, "completion_tokens": 20},
                }
            )
            if len(prompts) == 1:
                return {"edges": [edge("PROVIDES", "invalid endpoint")]}
            return {"edges": [edge("B", "A knows B")]}

    monkeypatch.setenv("CONSTRUCTION_CONTEXT_SAFETY_TOKENS", "32")
    client = Client()
    install_local_extraction_chunking_policy(
        client,
        token_counter=lambda _messages: 100,
        partition_extraction_by_turns=True,
        partition_edge_candidates=True,
    )
    client._membind_entity_partition_hints.update({"a": [0], "b": [0], "c": [0]})
    result = asyncio.run(
        client.generate_response(
            [
                {
                    "role": "user",
                    "content": (
                        "<CURRENT MESSAGE>\n[USER]\nA B C\n</CURRENT MESSAGE>\n"
                        "<ENTITIES>[{\"name\":\"A\"},{\"name\":\"B\"},"
                        "{\"name\":\"C\"}]"
                        "</ENTITIES>\n# TASK"
                    ),
                }
            ],
            max_tokens=16_384,
            prompt_name="extract_edges.edge",
        )
    )

    assert [row["fact"] for row in result["edges"]] == ["A knows B"]
    assert len(prompts) == 3
    second_history = prompts[1][0]["content"].split(
        "<ALREADY_RETURNED_EDGES>", 1
    )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
    third_history = prompts[2][0]["content"].split(
        "<ALREADY_RETURNED_EDGES>", 1
    )[1].split("</ALREADY_RETURNED_EDGES>", 1)[0]
    assert [row["fact"] for row in json.loads(second_history)] == ["invalid endpoint"]
    assert [row["fact"] for row in json.loads(third_history)] == [
        "invalid endpoint",
        "A knows B",
    ]
    pages = [
        row
        for row in client._membind_extraction_diagnostics
        if row.get("event") == "EDGE_PAGINATION_PAGE"
    ]
    assert [row["raw_unique_progress_edge_count"] for row in pages] == [1, 1, 0]
    assert [row["delta_edge_count"] for row in pages] == [0, 1, 0]
    assert [row["invalid_endpoint_edge_count"] for row in pages] == [1, 0, 0]


def test_v61_provider_hashes_captured_and_replayed_responses() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_response(
            self,
            messages,
            response_model=None,
            max_tokens=None,
            model_size=None,
            group_id=None,
            prompt_name=None,
            *,
            attribute_extraction=False,
        ):
            self.calls += 1
            return {"messages": messages, "call": self.calls}

    async def scenario() -> None:
        delegate = Delegate()
        store = TranscriptStore()
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        capture = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: -1,
        )
        kwargs = {
            "response_model": {"type": "object"},
            "max_tokens": 32,
            "model_size": "medium",
            "group_id": "g",
            "prompt_name": "extract_nodes.extract_message",
        }
        with provider_scope(region="PREPARE", source_sequence=0):
            captured = await capture.generate_response(
                [{"role": "user", "content": "x"}], **kwargs
            )
        replay = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: 0,
        )
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0):
                replayed = await replay.generate_response(
                    [{"role": "user", "content": "x"}], **kwargs
                )
        assert captured == replayed
        assert delegate.calls == 1
        assert capture.observations[0]["response_sha256"]
        assert (
            capture.observations[0]["response_sha256"]
            == replay.observations[0]["response_sha256"]
        )
        assert capture.arbiter is replay.arbiter is arbiter
        assert store.summary()["unconsumed"] == 0

    asyncio.run(scenario())


def test_certified_context_selection_strips_history_before_capture_and_replay() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        async def generate_response(self, messages, **_kwargs):
            self.calls.append(messages)
            return {"content": messages[0]["content"]}

    async def scenario() -> None:
        delegate = Delegate()
        store = TranscriptStore()
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        capture = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: -1,
            certified_message_transform=strip_certified_previous_context,
        )
        replay = V61ProviderClient(
            delegate,
            store=store,
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: 0,
            certified_message_transform=strip_certified_previous_context,
        )
        kwargs = {
            "response_model": {"type": "object"},
            "max_tokens": 32,
            "group_id": "g",
            "prompt_name": "extract_nodes.extract_message",
        }
        capture_messages = [
            {
                "role": "user",
                "content": (
                    "<PREVIOUS MESSAGES>private history A</PREVIOUS MESSAGES>\n"
                    "<CURRENT MESSAGE>shared current evidence</CURRENT MESSAGE>"
                ),
            }
        ]
        replay_messages = [
            {
                "role": "user",
                "content": (
                    "<PREVIOUS MESSAGES>different native history</PREVIOUS MESSAGES>\n"
                    "<CURRENT MESSAGE>shared current evidence</CURRENT MESSAGE>"
                ),
            }
        ]
        with provider_scope(region="PREPARE", source_sequence=0):
            captured = await capture.generate_response(capture_messages, **kwargs)
        with provider_scope(region="NATIVE", source_sequence=0):
            with NativeBindingScope(store, source_sequence=0):
                replayed = await replay.generate_response(replay_messages, **kwargs)
        assert captured == replayed
        assert len(delegate.calls) == 1
        assert "private history A" not in delegate.calls[0][0]["content"]
        assert "<PREVIOUS MESSAGES>\n[]\n</PREVIOUS MESSAGES>" in delegate.calls[0][0][
            "content"
        ]
        assert capture_messages[0]["content"].find("private history A") >= 0
        assert capture.context_selection_events[0]["previous_context_chars_removed"] == len(
            "private history A"
        )
        assert replay.context_selection_events[0]["previous_context_chars_removed"] == len(
            "different native history"
        )
        assert all(
            "source_text" not in row
            for row in (*capture.context_selection_events, *replay.context_selection_events)
        )
        assert store.summary()["unconsumed"] == 0

    asyncio.run(scenario())


def test_incremental_native_summary_context_retains_durable_and_current_evidence() -> None:
    original = [
        {"role": "system", "content": "summary system"},
        {
            "role": "user",
            "content": (
                "<MESSAGES>\n"
                '[{"content":"old fact","timestamp":"2026-01-01T00:00:00Z"}]\n'
                '"current fact"\n'
                "</MESSAGES>\n"
                "<ENTITIES>\n"
                '[{"name":"USER","summary":"durable prior fact",'
                '"entity_types":["Entity"],"attributes":{}}]\n'
                "</ENTITIES>"
            ),
        },
    ]
    transformed = incremental_native_summary_context(
        original, "extract_nodes.extract_summaries_batch"
    )
    assert transformed is not None
    messages, event = transformed
    assert "old fact" not in messages[1]["content"]
    assert "<MESSAGES>\n[]\n\"current fact\"\n</MESSAGES>" in messages[1]["content"]
    assert "durable prior fact" in messages[1]["content"]
    assert "old fact" in original[1]["content"]
    assert event["previous_episode_count"] == 1
    assert event["retained_previous_episode_count"] == 0
    assert event["current_episode_chars_retained"] == len("current fact")
    assert event["entity_count"] == 1
    assert event["nonempty_existing_summary_count"] == 1
    assert event["existing_summary_chars_retained"] == len("durable prior fact")
    assert len(event["previous_context_sha256"]) == 64
    assert len(event["current_episode_sha256"]) == 64
    assert len(event["existing_summaries_sha256"]) == 64
    assert len(event["transformed_messages_sha256"]) == 64
    assert incremental_native_summary_context(original, "extract_edges.edge") is None


def test_incremental_native_summary_context_matches_graphiti_prompt_builder() -> None:
    from graphiti_core.prompts import prompt_library

    messages = prompt_library.extract_nodes.extract_summaries_batch(
        {
            "previous_episodes": [
                {"content": "old graphiti fact", "timestamp": "2026-01-01T00:00:00Z"}
            ],
            "episode_content": "current graphiti fact",
            "entity_type_descriptions": {},
            "entities": [
                {
                    "name": "USER",
                    "summary": "durable graphiti fact",
                    "entity_types": ["Entity"],
                    "attributes": {},
                }
            ],
        }
    )
    transformed = incremental_native_summary_context(
        messages, "extract_nodes.extract_summaries_batch"
    )
    assert transformed is not None
    updated, event = transformed
    assert "old graphiti fact" not in updated[1].content
    assert "current graphiti fact" in updated[1].content
    assert "durable graphiti fact" in updated[1].content
    assert "old graphiti fact" in messages[1].content
    assert event["previous_episode_count"] == 1
    assert event["entity_count"] == 1


def test_incremental_native_summary_context_fails_closed_on_prompt_drift() -> None:
    messages = [
        {
            "role": "user",
            "content": (
                "<MESSAGES>not-json</MESSAGES>"
                '<ENTITIES>[{"name":"USER","summary":"durable"}]</ENTITIES>'
            ),
        }
    ]
    with pytest.raises(V61ProviderError, match="structured JSON"):
        incremental_native_summary_context(
            messages, "extract_nodes.extract_summaries_batch"
        )


def test_native_summary_transform_precedes_request_identity_and_transport() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        async def generate_response(self, messages, **_kwargs):
            self.calls.append(messages)
            return {"summaries": []}

    async def scenario() -> None:
        delegate = Delegate()
        arbiter = ForegroundAdmissionArbiter(
            CapacityAuthority(2),
            policy=V61Policy(lookahead=1, future_cap=1, native_future_quota=0),
        )
        client = V61ProviderClient(
            delegate,
            store=TranscriptStore(),
            arbiter=arbiter,
            mode="replay",
            durable_frontier=lambda: 0,
            native_message_transform=incremental_native_summary_context,
        )
        messages = [
            {
                "role": "user",
                "content": (
                    "<MESSAGES>\n"
                    '[{"content":"old fact","timestamp":null}]\n'
                    '"current fact"\n'
                    "</MESSAGES>\n"
                    '<ENTITIES>[{"name":"USER","summary":"durable fact"}]</ENTITIES>'
                ),
            }
        ]
        with provider_scope(region="NATIVE", source_sequence=0):
            result = await client.generate_response(
                messages,
                response_model={"type": "object"},
                max_tokens=32,
                group_id="g",
                prompt_name="extract_nodes.extract_summaries_batch",
            )
        assert result == {"summaries": []}
        assert len(delegate.calls) == 1
        assert "old fact" not in delegate.calls[0][0]["content"]
        assert "current fact" in delegate.calls[0][0]["content"]
        assert "durable fact" in delegate.calls[0][0]["content"]
        assert "old fact" in messages[0]["content"]
        assert len(client.context_selection_events) == 1
        event = client.context_selection_events[0]
        assert event["mode"] == "replay"
        assert event["region"] == "NATIVE"
        assert event["source_sequence"] == 0
        assert (
            client.observations[0]["public_summary"]["field_digests"]["messages"]
            == event["transformed_messages_sha256"]
        )
        assert arbiter.outstanding == 0
        assert arbiter.tokens_outstanding == 0

    asyncio.run(scenario())


def test_provider_failure_releases_its_permit() -> None:
    class FailingDelegate:
        async def generate_response(self, *_args, **_kwargs):
            raise RuntimeError("provider failed")

    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        arbiter = ForegroundAdmissionArbiter(CapacityAuthority(2), policy=policy)
        client = V61ProviderClient(
            FailingDelegate(),
            store=TranscriptStore(),
            arbiter=arbiter,
            mode="capture",
            durable_frontier=lambda: -1,
        )
        with provider_scope(region="PREPARE", source_sequence=0):
            with pytest.raises(RuntimeError, match="provider failed"):
                await client.generate_response(
                    [{"role": "user", "content": "x"}],
                    prompt_name="extract_nodes.extract_message",
                )
        assert arbiter.outstanding == 0
        assert arbiter.future_outstanding == 0

    asyncio.run(scenario())


def test_publish_failure_exits_guard_and_cancels_future_tasks() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(authority, policy=policy)
        cancelled = asyncio.Event()

        async def prepare(sequence: int) -> int:
            if sequence == 1:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return sequence

        async def publish(_sequence: int, _value: int) -> None:
            raise RuntimeError("database failed")

        with pytest.raises(RuntimeError, match="database failed"):
            await run_jit_frontier_history_async(
                2,
                prepare,
                publish,
                authority=authority,
                policy=policy,
                admission=arbiter,
            )
        assert cancelled.is_set()
        assert not arbiter.native_guard_active
        assert arbiter.outstanding == 0

    asyncio.run(scenario())


def test_staged_executor_separates_prepare_and_native_with_clean_barrier() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(authority, policy=policy)
        preparation_frontier = {"value": -1}
        prepare_finished: list[int] = []
        published: list[int] = []

        async def prepare(sequence: int) -> dict[str, int]:
            if sequence == 0:
                await asyncio.sleep(0.02)
            else:
                await asyncio.sleep(0)
            prepare_finished.append(sequence)
            return {"sequence": sequence}

        async def publish(sequence: int, value: dict[str, int]) -> None:
            assert len(prepare_finished) == 4
            assert value == {"sequence": sequence}
            published.append(sequence)

        result = await run_staged_frontier_history_async(
            4,
            prepare,
            publish,
            authority=authority,
            policy=policy,
            admission=arbiter,
            preparation_frontier_sink=lambda sequence: preparation_frontier.__setitem__(
                "value", sequence
            ),
        )
        assert result.execution_strategy == STAGED_EXECUTION_STRATEGY
        assert result.preparation_durable_frontier == 3
        assert result.durable_frontier == 3
        assert preparation_frontier["value"] == 3
        assert published == [0, 1, 2, 3]
        assert prepare_finished.index(1) < prepare_finished.index(0)
        assert result.stage_barrier == {
            "status": "PASS",
            "source_count": 4,
            "prepared_count": 4,
            "preparation_durable_frontier": 3,
            "outstanding": 0,
            "future_outstanding": 0,
            "native_outstanding": 0,
            "tokens_outstanding": 0,
            "waiter_count": 0,
            "native_guard_active": False,
        }

    asyncio.run(scenario())


def test_staged_prepare_failure_cancels_window_without_leaking_state() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(authority, policy=policy)
        cancelled: set[int] = set()

        async def prepare(sequence: int) -> int:
            if sequence == 0:
                await asyncio.sleep(0)
                raise RuntimeError("prepare failed")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(sequence)
                raise

        with pytest.raises(RuntimeError, match="prepare failed"):
            await run_staged_frontier_history_async(
                4,
                prepare,
                lambda *_args: None,
                authority=authority,
                policy=policy,
                admission=arbiter,
                preparation_frontier_sink=lambda _sequence: None,
            )
        assert cancelled
        assert arbiter.outstanding == 0
        assert arbiter.future_outstanding == 0
        assert arbiter.tokens_outstanding == 0
        assert arbiter.waiter_count == 0
        assert not arbiter.native_guard_active

    asyncio.run(scenario())


def test_staged_out_of_order_prepare_failure_is_fail_fast() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(authority, policy=policy)
        predecessor_cancelled = asyncio.Event()

        async def prepare(sequence: int) -> int:
            if sequence == 1:
                await asyncio.sleep(0)
                raise RuntimeError("successor failed")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                predecessor_cancelled.set()
                raise

        with pytest.raises(RuntimeError, match="successor failed"):
            await asyncio.wait_for(
                run_staged_frontier_history_async(
                    2,
                    prepare,
                    lambda *_args: None,
                    authority=authority,
                    policy=policy,
                    admission=arbiter,
                    preparation_frontier_sink=lambda _sequence: None,
                ),
                timeout=1,
            )
        assert predecessor_cancelled.is_set()
        assert arbiter.outstanding == 0
        assert arbiter.waiter_count == 0

    asyncio.run(scenario())


def test_staged_publish_failure_does_not_repeat_preparation() -> None:
    async def scenario() -> None:
        policy = V61Policy(lookahead=1, future_cap=1, native_future_quota=0)
        authority = CapacityAuthority(2)
        arbiter = ForegroundAdmissionArbiter(authority, policy=policy)
        prepare_counts = [0, 0, 0]
        publish_calls: list[int] = []

        async def prepare(sequence: int) -> int:
            prepare_counts[sequence] += 1
            return sequence

        async def publish(sequence: int, _value: int) -> None:
            publish_calls.append(sequence)
            if sequence == 1:
                raise RuntimeError("publish failed")

        with pytest.raises(RuntimeError, match="publish failed"):
            await run_staged_frontier_history_async(
                3,
                prepare,
                publish,
                authority=authority,
                policy=policy,
                admission=arbiter,
                preparation_frontier_sink=lambda _sequence: None,
            )
        assert prepare_counts == [1, 1, 1]
        assert publish_calls == [0, 1]
        assert arbiter.outstanding == 0
        assert arbiter.waiter_count == 0
        assert not arbiter.native_guard_active

    asyncio.run(scenario())
