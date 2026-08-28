from __future__ import annotations

import asyncio

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.core import (
    MEMBIND_CORE_BOUNDARY,
    MEMBIND_CORE_EXECUTION_STRATEGY,
    MEMBIND_CORE_ROUTE_POLICY,
    MEMBIND_CORE_STATE_CONTRACT,
    MEMBIND_CORE_VERSION,
    MemBindCoreConfigurationError,
    assert_core_policy,
    build_membind_core_runtime_8b,
    core_identity,
    core_policy,
    run_membind_core_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy


def test_core_identity_is_explicit_and_work_reduction_is_disabled() -> None:
    identity = core_identity()
    assert identity == {
        "version": MEMBIND_CORE_VERSION,
        "boundary": MEMBIND_CORE_BOUNDARY,
        "execution_strategy": MEMBIND_CORE_EXECUTION_STRATEGY,
        "state_contract": MEMBIND_CORE_STATE_CONTRACT,
        "selected_candidate": "r63b-work-conserving-edge-admission",
        "route_policy": MEMBIND_CORE_ROUTE_POLICY,
        "allowed_transformations": [
            "dependency_aware_prepare_execution_overlap",
            "dependency_aware_admission_and_work_conserving_partition_dispatch",
            "exact_certified_replay_of_dependency_free_extraction",
            "ordered_authoritative_publication",
        ],
        "work_reduction_extensions_enabled": False,
        "adaptive_scheduler_enabled": False,
        "bootstrap_future_borrow_enabled": False,
    }


def test_core_policy_is_frozen() -> None:
    assert_core_policy(core_policy())
    with pytest.raises(MemBindCoreConfigurationError):
        assert_core_policy(V61Policy(lookahead=1, future_cap=1, native_future_quota=0))


def test_core_runtime_rejects_non_selected_route_before_provider_access() -> None:
    with pytest.raises(MemBindCoreConfigurationError, match="elastic route"):
        build_membind_core_runtime_8b(
            routing_contract={"router": {"policy": "frontier_critical_path_resource_scheduler_v1"}}
        )


def test_core_runner_rejects_tuning_and_extension_arguments() -> None:
    async def invoke() -> None:
        with pytest.raises(MemBindCoreConfigurationError):
            await run_membind_core_construction_async(
                policy=V61Policy(),
                execution_strategy="jit_frontier_interleaved_v1",
            )
        with pytest.raises(MemBindCoreConfigurationError):
            await run_membind_core_construction_async(
                policy=V61Policy(),
                method_boundary="WORK_REDUCTION_EXTENSION",
            )
        with pytest.raises(MemBindCoreConfigurationError):
            await run_membind_core_construction_async(
                policy=V61Policy(), artifact_method="V6_1"
            )

    asyncio.run(invoke())
