"""Frozen MemBind-Core composition for the resource-matched 8B campaign.

This module is the single ownership boundary for the V6 paper method.  The
older ``membind_v6_1`` modules remain available for audited ablations and
historical replay, but a headline Core run must enter through this file.  The
entry points deliberately do not expose scheduler knobs or work-reduction
switches: changing either creates a new V6.1 candidate/extension instead of
silently changing the paper method.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Mapping

from ..membind_v5.runtime.adapters.client_proxy import CERTIFIED_CALLSITES
from .executor import DUAL_STREAMING_EXECUTION_STRATEGY
from .mab import run_mab_v61_construction_async
from .policy import V61Policy
from .resource_credit import ResourceCreditPolicy
from .runtime_8b import (
    build_8b_shared_bounded_runtime,
    frozen_8b_config,
    public_8b_environment,
)


MEMBIND_CORE_VERSION = "v6-membind-core-resource-credit-v1"
MEMBIND_CORE_BOUNDARY = "MEMBIND_CORE"
MEMBIND_CORE_EXECUTION_STRATEGY = DUAL_STREAMING_EXECUTION_STRATEGY
MEMBIND_CORE_STATE_CONTRACT = "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION"
MEMBIND_CORE_ROUTE_POLICY = "semantic_phase_elastic_affinity"
MEMBIND_CORE_CANDIDATE = "r63b-work-conserving-edge-admission"
MEMBIND_CORE_METHOD_IDENTITY = "MEMBIND_RESOURCE_CREDIT_V1"
MEMBIND_CORE_POLICY = ResourceCreditPolicy()
MEMBIND_FIXED_ABLATION_POLICY = V61Policy(lookahead=2, future_cap=1, native_future_quota=0)
# Core preserves the complete Native request. Exact identity validation decides
# reuse; a mismatch is delegated to a fresh Native call by the Core binding.
MEMBIND_CORE_CERTIFIED_CALLSITES = CERTIFIED_CALLSITES
MEMBIND_CORE_IMPLEMENTATION_REVISION = "context-integrity-fix-v1"


class MemBindCoreConfigurationError(ValueError):
    """Raised when a run attempts to mutate the frozen Core composition."""


def core_policy() -> ResourceCreditPolicy:
    """Return a fresh immutable copy of the selected policy."""

    return ResourceCreditPolicy(**asdict(MEMBIND_CORE_POLICY))


def assert_core_policy(policy: ResourceCreditPolicy) -> None:
    """Reject scheduler tuning on the headline Core path."""

    if not isinstance(policy, ResourceCreditPolicy) or policy != MEMBIND_CORE_POLICY:
        raise MemBindCoreConfigurationError(
            "MemBind-Core requires frozen MEMBIND_RESOURCE_CREDIT_V1 policy"
        )


def core_identity() -> dict[str, Any]:
    """Return the stable identity written into campaign metadata."""

    return {
        "version": MEMBIND_CORE_VERSION,
        "implementation_revision": MEMBIND_CORE_IMPLEMENTATION_REVISION,
        "boundary": MEMBIND_CORE_BOUNDARY,
        "execution_strategy": MEMBIND_CORE_EXECUTION_STRATEGY,
        "state_contract": MEMBIND_CORE_STATE_CONTRACT,
        "selected_candidate": MEMBIND_CORE_CANDIDATE,
        "route_policy": MEMBIND_CORE_ROUTE_POLICY,
        "allowed_transformations": [
            "dependency_aware_prepare_execution_overlap",
            "dependency_aware_admission_and_work_conserving_partition_dispatch",
            "exact_certified_replay_of_dependency_free_extraction",
            "ordered_authoritative_publication",
        ],
        "work_reduction_extensions_enabled": False,
        "context_removal_allowed": False,
        "certified_message_transform": None,
        "same_logical_request_required": True,
        "fresh_fallback_on_binding_mismatch": True,
        "adaptive_scheduler_enabled": False,
        "bootstrap_future_borrow_enabled": False,
    }


def _annotate(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(value),
        "method_identity": MEMBIND_CORE_METHOD_IDENTITY,
        "membind_core": core_identity(),
    }


def build_membind_core_runtime_8b(
    *,
    routing_contract: Mapping[str, Any],
    route_event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> Any:
    """Build the selected 8B runtime with all work-reduction extensions off."""

    policy = routing_contract.get("router", {}).get("policy")
    if policy != MEMBIND_CORE_ROUTE_POLICY:
        raise MemBindCoreConfigurationError(
            "MemBind-Core requires the retained semantic-phase elastic route; "
            f"received {policy!r}"
        )
    runtime = build_8b_shared_bounded_runtime(
        routing_contract=routing_contract,
        route_event_sink=route_event_sink,
    )
    manifest = getattr(runtime, "_membind_8b_runtime_manifest", {})
    construction = manifest.get("construction", {})
    expected = {
        "entity_summary_policy": "graphiti_native_batched_summary_v1",
        "edge_endpoint_schema_policy": "entity_block_literal_endpoint_grounding_v1",
        "edge_physical_admission_policy": "arbiter_work_conserving_partition_derived_v1",
        "shared_structured_output": {
            "adapter_version": "shared-bounded-structured-output-v1",
        },
    }
    for field, value in expected.items():
        observed = construction.get(field)
        matches = (
            isinstance(observed, Mapping)
            and all(observed.get(key) == expected_value for key, expected_value in value.items())
            if field == "shared_structured_output"
            else observed == value
        )
        if not matches:
            raise MemBindCoreConfigurationError(
                f"Core runtime drifted at construction.{field}: "
                f"{construction.get(field)!r} != {value!r}"
            )
    runtime._membind_core_identity = core_identity()
    return runtime


def frozen_membind_core_config_8b(
    routing_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the frozen configuration plus the Core identity."""

    return _annotate(
        frozen_8b_config(
            routing_contract,
            shared_bounded_structured_output=True,
            enable_endpoint_schema_grounding=True,
            enable_work_conserving_edge_admission=True,
            enable_adaptive_edge_admission=False,
        )
    )


def public_membind_core_environment_8b(
    routing_contract: Mapping[str, Any],
    *,
    repo_root: Any | None = None,
) -> dict[str, Any]:
    """Return public environment evidence for the selected Core runtime."""

    return _annotate(
        public_8b_environment(
            routing_contract,
            repo_root=repo_root,
            shared_bounded_structured_output=True,
            enable_endpoint_schema_grounding=True,
            enable_work_conserving_edge_admission=True,
            enable_adaptive_edge_admission=False,
        )
    )


async def run_membind_core_construction_async(
    *,
    policy: ResourceCreditPolicy,
    execution_strategy: str | None = None,
    method_boundary: str | None = None,
    shared_bounded_label: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run one frozen Core block; extension and tuning arguments are rejected."""

    assert_core_policy(policy)
    if execution_strategy not in {None, MEMBIND_CORE_EXECUTION_STRATEGY}:
        raise MemBindCoreConfigurationError(
            "MemBind-Core execution strategy is phase-isolated dual streaming"
        )
    if method_boundary not in {None, MEMBIND_CORE_BOUNDARY}:
        raise MemBindCoreConfigurationError(
            "work-reduction extensions cannot enter MemBind-Core"
        )
    if "artifact_method" in kwargs:
        raise MemBindCoreConfigurationError(
            "MemBind-Core artifact identity is fixed and cannot be overridden"
        )
    artifact_method = (
        "MEMBIND_V6_1_SHARED_BOUNDED_SO"
        if shared_bounded_label == "MEMBIND_V6_1_SHARED_BOUNDED_SO"
        else "MEMBIND_CORE"
    )
    result = await run_mab_v61_construction_async(
        policy=policy,
        execution_strategy=MEMBIND_CORE_EXECUTION_STRATEGY,
        method_boundary=MEMBIND_CORE_BOUNDARY,
        artifact_method=artifact_method,
        certified_callsites=MEMBIND_CORE_CERTIFIED_CALLSITES,
        certified_message_transform=None,
        binding_strict=False,
        implementation_revision=MEMBIND_CORE_IMPLEMENTATION_REVISION,
        **kwargs,
    )
    result["method"] = artifact_method
    result["method_identity"] = MEMBIND_CORE_METHOD_IDENTITY
    result["method_boundary"] = MEMBIND_CORE_BOUNDARY
    result["core_identity"] = core_identity()
    return result


__all__ = [
    "MEMBIND_CORE_BOUNDARY",
    "MEMBIND_CORE_CERTIFIED_CALLSITES",
    "MEMBIND_CORE_CANDIDATE",
    "MEMBIND_CORE_EXECUTION_STRATEGY",
    "MEMBIND_CORE_IMPLEMENTATION_REVISION",
    "MEMBIND_CORE_POLICY",
    "MEMBIND_CORE_ROUTE_POLICY",
    "MEMBIND_CORE_STATE_CONTRACT",
    "MEMBIND_CORE_VERSION",
    "MemBindCoreConfigurationError",
    "assert_core_policy",
    "build_membind_core_runtime_8b",
    "core_identity",
    "core_policy",
    "frozen_membind_core_config_8b",
    "public_membind_core_environment_8b",
    "run_membind_core_construction_async",
]
