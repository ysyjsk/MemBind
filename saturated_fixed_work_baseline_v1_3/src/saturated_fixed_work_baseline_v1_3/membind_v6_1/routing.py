"""Auditable request routing for the isolated V6.1 dual-replica runtime."""

from __future__ import annotations

import contextvars
import inspect
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from ..membind_v5.runtime.core.provider_admission import (
    current_provider_request_tokens,
    current_provider_scope,
)
from .critical_scheduler import CriticalPathResourceScheduler, ReadyTask


CAPACITY_WEIGHTED_LEAST_OUTSTANDING = "capacity_weighted_least_outstanding"
GRAPHITI_REQUEST_CLASS_AFFINITY = "graphiti_request_class_affinity"
SEMANTIC_PHASE_AFFINITY = "semantic_phase_affinity"
SEMANTIC_PHASE_ELASTIC_AFFINITY = "semantic_phase_elastic_affinity"
SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY = (
    "semantic_phase_capacity_balanced_affinity"
)
SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY = "semantic_phase_logical_token_affinity"
SEMANTIC_PHASE_EDGE_CALL_AFFINITY = "semantic_phase_edge_call_affinity"
SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY = "semantic_phase_token_debt_affinity"
SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY = "frontier_critical_path_resource_scheduler_v1"
SINGLE_ENDPOINT_FCFS = "single_endpoint_fcfs"

_SUPPORTED_POLICIES = {
    CAPACITY_WEIGHTED_LEAST_OUTSTANDING,
    GRAPHITI_REQUEST_CLASS_AFFINITY,
    SEMANTIC_PHASE_AFFINITY,
    SEMANTIC_PHASE_ELASTIC_AFFINITY,
    SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY,
    SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
    SEMANTIC_PHASE_EDGE_CALL_AFFINITY,
    SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
    SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY,
    SINGLE_ENDPOINT_FCFS,
}
_EXTRACTION_PROMPTS = {
    "extract_nodes.extract_message",
    "extract_nodes.extract_text",
    "extract_nodes.extract_json",
    "extract_edges.edge",
}
_ROUTE_PROMPT_NAME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "membind_v6_1_route_prompt_name", default=None
)


@dataclass(slots=True)
class _LogicalRouteState:
    prompt_name: str | None
    request_tokens: int | None
    logical_group_id: int | None = None
    endpoint_id: str | None = None
    release: Callable[[str], None] | None = None


_ROUTE_LOGICAL_STATE: contextvars.ContextVar[_LogicalRouteState | None] = (
    contextvars.ContextVar("membind_v6_1_route_logical_state", default=None)
)


class RoutingConfigurationError(RuntimeError):
    """The selected route cannot satisfy its frozen routing contract."""


@dataclass(frozen=True, slots=True)
class EndpointSpec:
    endpoint_id: str
    base_url: str
    served_model: str
    physical_gpu: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EndpointSpec":
        endpoint_id = value.get("id")
        base_url = value.get("base_url")
        served_model = value.get("served_model")
        physical_gpu = value.get("physical_gpu")
        if not isinstance(endpoint_id, str) or not endpoint_id:
            raise RoutingConfigurationError("routing endpoint id is invalid")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise RoutingConfigurationError(f"routing endpoint URL is invalid: {endpoint_id}")
        if not isinstance(served_model, str) or not served_model:
            raise RoutingConfigurationError(f"routing model is invalid: {endpoint_id}")
        if not isinstance(physical_gpu, int) or physical_gpu < 0:
            raise RoutingConfigurationError(f"routing GPU is invalid: {endpoint_id}")
        return cls(endpoint_id, base_url.rstrip("/"), served_model, physical_gpu)


def route_prompt_name() -> str | None:
    """Return the Graphiti request class visible to the transport facade."""

    return _ROUTE_PROMPT_NAME.get()


@contextmanager
def route_request_context(prompt_name: Any):
    """Propagate only the request-class label, never request content."""

    normalized = prompt_name if isinstance(prompt_name, str) else None
    token = _ROUTE_PROMPT_NAME.set(normalized)
    try:
        yield
    finally:
        _ROUTE_PROMPT_NAME.reset(token)


def install_routing_prompt_context(llm_client: Any) -> Callable[[], None]:
    """Expose ``prompt_name`` to physical routing without changing call payloads."""

    original = getattr(llm_client, "generate_response", None)
    if not callable(original):
        raise RoutingConfigurationError("Graphiti logical LLM seam is unavailable")
    restored = False

    @wraps(original)
    async def routed_generate(*args: Any, **kwargs: Any) -> Any:
        state = _LogicalRouteState(
            prompt_name=(
                kwargs.get("prompt_name")
                if isinstance(kwargs.get("prompt_name"), str)
                else None
            ),
            request_tokens=current_provider_request_tokens(),
        )
        state_token = _ROUTE_LOGICAL_STATE.set(state)
        status = "success"
        try:
            with route_request_context(kwargs.get("prompt_name")):
                return await original(*args, **kwargs)
        except BaseException as exc:
            status = "cancelled" if type(exc).__name__ == "CancelledError" else "failure"
            raise
        finally:
            if state.release is not None:
                state.release(status)
            _ROUTE_LOGICAL_STATE.reset(state_token)

    setattr(llm_client, "generate_response", routed_generate)

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        setattr(llm_client, "generate_response", original)

    return restore


def validate_route_evidence(
    events: Sequence[Mapping[str, Any]],
    *,
    policy: str,
    endpoint_ids: Sequence[str],
    transport_attempt_count: int,
    capacity_weights: Mapping[str, float] | None = None,
    logical_group_events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Prove that every observed transport used the selected routing contract."""

    rows = [dict(row) for row in events]
    expected_endpoints = set(endpoint_ids)
    if policy not in _SUPPORTED_POLICIES:
        raise RoutingConfigurationError("route proof policy is unsupported")
    if not rows or len(rows) != int(transport_attempt_count):
        raise RoutingConfigurationError(
            "route event count differs from instrumented transport attempts"
        )
    request_indices = sorted(row.get("request_index") for row in rows)
    if request_indices != list(range(len(rows))):
        raise RoutingConfigurationError("route request indices are incomplete")
    if any(
        row.get("schema_version") != "membind.v6.1.llm-route.v1"
        or row.get("policy") != policy
        or row.get("endpoint_id") not in expected_endpoints
        or row.get("status") != "success"
        for row in rows
    ):
        raise RoutingConfigurationError("route event identity or status is invalid")
    if policy == SEMANTIC_PHASE_AFFINITY:
        invalid = [
            row
            for row in rows
            if (row.get("region") == "PREPARE" and row.get("endpoint_id") != "prepare-replica")
            or (row.get("region") == "NATIVE" and row.get("endpoint_id") != "native-replica")
            or row.get("region") not in {"PREPARE", "NATIVE"}
        ]
        if invalid:
            raise RoutingConfigurationError("V6.1 route evidence violates phase affinity")
    elif policy == SEMANTIC_PHASE_ELASTIC_AFFINITY:
        invalid = []
        for row in rows:
            region = row.get("region")
            if region not in {"PREPARE", "NATIVE"}:
                invalid.append(row)
                continue
            preferred = "prepare-replica" if region == "PREPARE" else "native-replica"
            alternate = "native-replica" if region == "PREPARE" else "prepare-replica"
            outstanding = row.get("selection_outstanding")
            if (
                not isinstance(outstanding, Mapping)
                or set(outstanding) != expected_endpoints
                or any(not isinstance(value, int) or value < 0 for value in outstanding.values())
            ):
                invalid.append(row)
                continue
            should_spill = outstanding[preferred] > 0 and outstanding[alternate] == 0
            expected_endpoint = alternate if should_spill else preferred
            expected_reason = (
                "semantic_phase_idle_spillover"
                if should_spill
                else "semantic_phase_preferred"
            )
            if (
                row.get("preferred_endpoint_id") != preferred
                or row.get("endpoint_id") != expected_endpoint
                or row.get("route_reason") != expected_reason
                or row.get("spillover") is not should_spill
                or row.get("outstanding_before") != outstanding[expected_endpoint]
            ):
                invalid.append(row)
        if invalid:
            raise RoutingConfigurationError(
                "V6.1 route evidence violates elastic phase affinity"
            )
    elif policy == SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY:
        expected_weights = dict(capacity_weights or {})
        if set(expected_weights) != expected_endpoints or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in expected_weights.values()
        ):
            raise RoutingConfigurationError(
                "token-debt route proof has invalid manifest weights"
            )
        expected_weights = {
            endpoint_id: float(expected_weights[endpoint_id])
            for endpoint_id in sorted(expected_endpoints)
        }
        invalid = []
        for row in rows:
            region = row.get("region")
            if region not in {"PREPARE", "NATIVE"}:
                invalid.append(row)
                continue
            preferred = "prepare-replica" if region == "PREPARE" else "native-replica"
            request_tokens = row.get("request_tokens")
            selection_debt = row.get("selection_token_debt")
            row_weights = row.get("capacity_weights")
            if (
                not isinstance(request_tokens, int)
                or isinstance(request_tokens, bool)
                or request_tokens <= 0
                or not isinstance(selection_debt, Mapping)
                or set(selection_debt) != expected_endpoints
                or any(
                    not isinstance(selection_debt[endpoint_id], int)
                    or isinstance(selection_debt[endpoint_id], bool)
                    or selection_debt[endpoint_id] < 0
                    for endpoint_id in expected_endpoints
                )
                or not isinstance(row_weights, Mapping)
                or set(row_weights) != expected_endpoints
                or any(
                    not isinstance(row_weights[endpoint_id], (int, float))
                    or isinstance(row_weights[endpoint_id], bool)
                    or not math.isclose(
                        float(row_weights[endpoint_id]),
                        expected_weights[endpoint_id],
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for endpoint_id in expected_endpoints
                )
            ):
                invalid.append(row)
                continue
            active_debt = {
                endpoint_id: int(selection_debt[endpoint_id])
                for endpoint_id in expected_endpoints
            }
            if all(value == 0 for value in active_debt.values()):
                expected_endpoint = preferred
            else:
                projected = {
                    endpoint_id: (active_debt[endpoint_id] + request_tokens)
                    / expected_weights[endpoint_id]
                    for endpoint_id in expected_endpoints
                }
                best = min(projected.values())
                tied = {
                    endpoint_id
                    for endpoint_id, value in projected.items()
                    if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
                }
                expected_endpoint = preferred if preferred in tied else next(iter(tied))
            expected_reason = (
                "semantic_phase_token_debt_preferred"
                if expected_endpoint == preferred
                else "semantic_phase_token_debt_spillover"
            )
            expected_after = dict(active_debt)
            expected_after[expected_endpoint] += request_tokens
            if (
                row.get("preferred_endpoint_id") != preferred
                or row.get("endpoint_id") != expected_endpoint
                or row.get("route_reason") != expected_reason
                or row.get("spillover") is not (expected_endpoint != preferred)
                or row.get("outstanding_before")
                != row.get("selection_outstanding", {}).get(expected_endpoint)
                or row.get("active_token_debt_before") != active_debt
                or row.get("active_token_debt_after") != expected_after
            ):
                invalid.append(row)
        if invalid:
            raise RoutingConfigurationError(
                "V6.1 route evidence violates token-debt phase affinity"
            )
    elif policy == SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY:
        invalid = []
        for row in rows:
            region = row.get("region")
            preferred = "prepare-replica" if region == "PREPARE" else "native-replica"
            decision = row.get("critical_path_decision")
            scores = decision.get("candidate_scores") if isinstance(decision, Mapping) else None
            active_work = (
                decision.get("active_work_before_ns")
                if isinstance(decision, Mapping)
                else None
            )
            request_index = row.get("request_index")
            selected = row.get("endpoint_id")
            if (
                region not in {"PREPARE", "NATIVE"}
                or selected not in expected_endpoints
                or row.get("preferred_endpoint_id") != preferred
                or not isinstance(request_index, int)
                or not isinstance(decision, Mapping)
                or decision.get("task_id") != f"route-{request_index}"
                or set(scores or ()) != expected_endpoints
                or set(active_work or ()) != expected_endpoints
                or any(
                    not isinstance(scores[endpoint_id], int) or scores[endpoint_id] <= 0
                    or not isinstance(active_work[endpoint_id], int)
                    or active_work[endpoint_id] < 0
                    for endpoint_id in expected_endpoints
                )
                or not isinstance(decision.get("service_estimate_ns"), int)
                or decision["service_estimate_ns"] <= 0
                or not isinstance(row.get("service_start_ns"), int)
                or not isinstance(row.get("service_end_ns"), int)
                or row["service_end_ns"] < row["service_start_ns"]
                or not isinstance(row.get("service_duration_ns"), int)
                or row["service_duration_ns"] <= 0
                or row["service_duration_ns"]
                != row["service_end_ns"] - row["service_start_ns"]
                or decision.get("projected_finish_ns") != scores.get(selected)
                or scores[selected] != min(scores.values())
                or row.get("outstanding_before")
                != row.get("selection_outstanding", {}).get(selected)
            ):
                invalid.append(row)
                continue
            tied = {
                endpoint_id
                for endpoint_id, value in scores.items()
                if value == scores[selected]
            }
            expected_endpoint = preferred if preferred in tied else min(tied)
            expected_reason = (
                "critical_path_preferred"
                if expected_endpoint == preferred
                else "critical_path_earliest_finish_spillover"
            )
            if (
                selected != expected_endpoint
                or row.get("route_reason") != expected_reason
                or row.get("spillover") is not (selected != preferred)
                or decision.get("reason") != expected_reason
                or decision.get("token_cost") != row.get("critical_path_token_cost")
            ):
                invalid.append(row)
        if invalid:
            raise RoutingConfigurationError(
                "V6.1 route evidence violates critical-path resource scheduling"
            )
    elif policy == SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY:
        expected_weights = dict(capacity_weights or {})
        if set(expected_weights) != expected_endpoints or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in expected_weights.values()
        ):
            raise RoutingConfigurationError(
                "capacity-balanced route proof has invalid manifest weights"
            )
        expected_weights = {
            endpoint_id: float(expected_weights[endpoint_id])
            for endpoint_id in sorted(expected_endpoints)
        }
        invalid = []
        for row in rows:
            region = row.get("region")
            if region not in {"PREPARE", "NATIVE"}:
                invalid.append(row)
                continue
            preferred = "prepare-replica" if region == "PREPARE" else "native-replica"
            outstanding = row.get("selection_outstanding")
            row_weights = row.get("capacity_weights")
            if (
                not isinstance(outstanding, Mapping)
                or set(outstanding) != expected_endpoints
                or any(not isinstance(value, int) or value < 0 for value in outstanding.values())
                or not isinstance(row_weights, Mapping)
                or set(row_weights) != expected_endpoints
                or any(
                    not isinstance(row_weights[endpoint_id], (int, float))
                    or isinstance(row_weights[endpoint_id], bool)
                    or not math.isclose(
                        float(row_weights[endpoint_id]),
                        expected_weights[endpoint_id],
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for endpoint_id in expected_endpoints
                )
            ):
                invalid.append(row)
                continue
            if outstanding[preferred] == 0:
                expected_endpoint = preferred
            else:
                projected = {
                    endpoint_id: (outstanding[endpoint_id] + 1)
                    / expected_weights[endpoint_id]
                    for endpoint_id in expected_endpoints
                }
                best = min(projected.values())
                tied = {
                    endpoint_id
                    for endpoint_id, value in projected.items()
                    if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
                }
                expected_endpoint = (
                    preferred if preferred in tied else next(iter(tied))
                )
            expected_reason = (
                "semantic_phase_capacity_preferred"
                if expected_endpoint == preferred
                else "semantic_phase_capacity_spillover"
            )
            if (
                row.get("preferred_endpoint_id") != preferred
                or row.get("endpoint_id") != expected_endpoint
                or row.get("route_reason") != expected_reason
                or row.get("spillover") is not (expected_endpoint != preferred)
                or row.get("outstanding_before") != outstanding[expected_endpoint]
                or not isinstance(row.get("capacity_weight"), (int, float))
                or isinstance(row.get("capacity_weight"), bool)
                or not math.isclose(
                    float(row["capacity_weight"]),
                    expected_weights[expected_endpoint],
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                invalid.append(row)
        if invalid:
            raise RoutingConfigurationError(
                "V6.1 route evidence violates capacity-balanced phase affinity"
            )
    elif policy == SEMANTIC_PHASE_EDGE_CALL_AFFINITY:
        group_rows = [dict(row) for row in (logical_group_events or ())]
        if not group_rows or [row.get("audit_index") for row in group_rows] != list(
            range(len(group_rows))
        ):
            raise RoutingConfigurationError("edge-call group audit is incomplete")
        active_groups = {endpoint_id: 0 for endpoint_id in expected_endpoints}
        groups: dict[int, dict[str, Any]] = {}
        active: set[int] = set()
        for row in group_rows:
            if (
                row.get("schema_version") != "membind.v6.1.edge-route-group.v1"
                or row.get("policy") != policy
                or row.get("active_edge_groups_before") != active_groups
            ):
                raise RoutingConfigurationError("edge-call group audit state is invalid")
            group_id = row.get("logical_group_id")
            endpoint_id = row.get("endpoint_id")
            action = row.get("action")
            if (
                not isinstance(group_id, int)
                or group_id < 0
                or endpoint_id not in expected_endpoints
            ):
                raise RoutingConfigurationError("edge-call group identity is invalid")
            if action == "acquire":
                if group_id in groups or row.get("region") not in {"PREPARE", "NATIVE"}:
                    raise RoutingConfigurationError("edge-call acquire identity is invalid")
                preferred = (
                    "prepare-replica"
                    if row["region"] == "PREPARE"
                    else "native-replica"
                )
                alternate = (
                    "native-replica"
                    if row["region"] == "PREPARE"
                    else "prepare-replica"
                )
                outstanding = row.get("selection_outstanding")
                if (
                    not isinstance(outstanding, Mapping)
                    or set(outstanding) != expected_endpoints
                    or any(not isinstance(value, int) or value < 0 for value in outstanding.values())
                ):
                    raise RoutingConfigurationError("edge-call selection snapshot is invalid")
                should_spill = outstanding[preferred] > 0 and outstanding[alternate] == 0
                expected_endpoint = alternate if should_spill else preferred
                expected_reason = (
                    "edge_call_affinity_idle_spillover"
                    if should_spill
                    else "edge_call_affinity_preferred"
                )
                if (
                    endpoint_id != expected_endpoint
                    or row.get("preferred_endpoint_id") != preferred
                    or row.get("selection_reason") != expected_reason
                    or row.get("status") != "active"
                ):
                    raise RoutingConfigurationError("edge-call selection decision is invalid")
                groups[group_id] = {
                    "endpoint_id": endpoint_id,
                    "preferred_endpoint_id": preferred,
                }
                active.add(group_id)
                active_groups[endpoint_id] += 1
            elif action == "release":
                group = groups.get(group_id)
                if (
                    group_id not in active
                    or group is None
                    or group["endpoint_id"] != endpoint_id
                    or row.get("status") not in {"success", "failure", "cancelled"}
                ):
                    raise RoutingConfigurationError("edge-call release identity is invalid")
                active.remove(group_id)
                active_groups[endpoint_id] -= 1
            else:
                raise RoutingConfigurationError("edge-call group action is invalid")
            if row.get("active_edge_groups_after") != active_groups or min(active_groups.values()) < 0:
                raise RoutingConfigurationError("edge-call group conservation failed")
        if active or any(active_groups.values()):
            raise RoutingConfigurationError("edge-call groups did not drain")
        first_transports: dict[int, int] = {}
        invalid = []
        for row in rows:
            is_edge = row.get("prompt_name") == "extract_edges.edge"
            if is_edge:
                group_id = row.get("logical_group_id")
                group = groups.get(group_id) if isinstance(group_id, int) else None
                first = row.get("logical_group_first_transport")
                if (
                    group is None
                    or first not in {True, False}
                    or row.get("endpoint_id") != group["endpoint_id"]
                    or row.get("preferred_endpoint_id") != group["preferred_endpoint_id"]
                    or row.get("route_reason")
                    != (
                        "edge_call_affinity_preferred"
                        if first and row.get("endpoint_id") == row.get("preferred_endpoint_id")
                        else "edge_call_affinity_idle_spillover"
                        if first
                        else "edge_call_affinity_reuse"
                    )
                ):
                    invalid.append(row)
                elif first:
                    first_transports[group_id] = first_transports.get(group_id, 0) + 1
            else:
                region = row.get("region")
                if region not in {"PREPARE", "NATIVE"}:
                    invalid.append(row)
                    continue
                preferred = "prepare-replica" if region == "PREPARE" else "native-replica"
                alternate = "native-replica" if region == "PREPARE" else "prepare-replica"
                outstanding = row.get("selection_outstanding")
                if (
                    not isinstance(outstanding, Mapping)
                    or set(outstanding) != expected_endpoints
                    or any(not isinstance(value, int) or value < 0 for value in outstanding.values())
                ):
                    invalid.append(row)
                    continue
                should_spill = outstanding[preferred] > 0 and outstanding[alternate] == 0
                expected_endpoint = alternate if should_spill else preferred
                expected_reason = (
                    "semantic_phase_idle_spillover" if should_spill else "semantic_phase_preferred"
                )
                if (
                    row.get("endpoint_id") != expected_endpoint
                    or row.get("preferred_endpoint_id") != preferred
                    or row.get("route_reason") != expected_reason
                    or row.get("spillover") is not should_spill
                    or row.get("outstanding_before") != outstanding[expected_endpoint]
                ):
                    invalid.append(row)
        if invalid or any(count != 1 for count in first_transports.values()) or set(first_transports) != set(groups):
            raise RoutingConfigurationError("V6.1 route evidence violates edge-call affinity")
    elif policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY:
        expected_weights = dict(capacity_weights or {})
        if set(expected_weights) != expected_endpoints or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in expected_weights.values()
        ):
            raise RoutingConfigurationError(
                "logical-token route proof has invalid manifest weights"
            )
        expected_weights = {
            endpoint_id: float(expected_weights[endpoint_id])
            for endpoint_id in sorted(expected_endpoints)
        }
        group_rows = [dict(row) for row in (logical_group_events or ())]
        if not group_rows or [row.get("audit_index") for row in group_rows] != list(
            range(len(group_rows))
        ):
            raise RoutingConfigurationError("logical-token group audit is incomplete")
        active_debt = {endpoint_id: 0 for endpoint_id in expected_endpoints}
        active_groups = {endpoint_id: 0 for endpoint_id in expected_endpoints}
        groups: dict[int, dict[str, Any]] = {}
        active: set[int] = set()
        for row in group_rows:
            if (
                row.get("schema_version") != "membind.v6.1.route-group.v1"
                or row.get("policy") != policy
                or row.get("capacity_weights") != expected_weights
            ):
                raise RoutingConfigurationError("logical-token group audit identity is invalid")
            group_id = row.get("logical_group_id")
            endpoint_id = row.get("endpoint_id")
            request_tokens = row.get("request_tokens")
            if (
                not isinstance(group_id, int)
                or group_id < 0
                or endpoint_id not in expected_endpoints
                or not isinstance(request_tokens, int)
                or isinstance(request_tokens, bool)
                or request_tokens <= 0
                or row.get("active_token_debt_before") != active_debt
                or row.get("active_logical_groups_before") != active_groups
            ):
                raise RoutingConfigurationError("logical-token group audit state is invalid")
            action = row.get("action")
            if action == "acquire":
                region = row.get("region")
                if region not in {"PREPARE", "NATIVE"} or group_id in groups:
                    raise RoutingConfigurationError("logical-token acquire identity is invalid")
                preferred = (
                    "prepare-replica" if region == "PREPARE" else "native-replica"
                )
                if all(value == 0 for value in active_debt.values()):
                    expected_endpoint = preferred
                else:
                    projected = {
                        candidate: (active_debt[candidate] + request_tokens)
                        / expected_weights[candidate]
                        for candidate in expected_endpoints
                    }
                    best = min(projected.values())
                    tied = {
                        candidate
                        for candidate, value in projected.items()
                        if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
                    }
                    expected_endpoint = (
                        preferred if preferred in tied else next(iter(tied))
                    )
                expected_reason = (
                    "logical_token_debt_preferred"
                    if expected_endpoint == preferred
                    else "logical_token_debt_spillover"
                )
                if (
                    endpoint_id != expected_endpoint
                    or row.get("preferred_endpoint_id") != preferred
                    or row.get("selection_reason") != expected_reason
                    or row.get("status") != "active"
                ):
                    raise RoutingConfigurationError("logical-token acquire decision is invalid")
                active_debt[endpoint_id] += request_tokens
                active_groups[endpoint_id] += 1
                groups[group_id] = {
                    "endpoint_id": endpoint_id,
                    "request_tokens": request_tokens,
                }
                active.add(group_id)
            elif action == "release":
                group = groups.get(group_id)
                if (
                    group_id not in active
                    or group is None
                    or group["endpoint_id"] != endpoint_id
                    or group["request_tokens"] != request_tokens
                    or row.get("status") not in {"success", "failure", "cancelled"}
                ):
                    raise RoutingConfigurationError("logical-token release identity is invalid")
                active_debt[endpoint_id] -= request_tokens
                active_groups[endpoint_id] -= 1
                active.remove(group_id)
            else:
                raise RoutingConfigurationError("logical-token group action is invalid")
            if (
                min(active_debt.values()) < 0
                or min(active_groups.values()) < 0
                or row.get("active_token_debt_after") != active_debt
                or row.get("active_logical_groups_after") != active_groups
            ):
                raise RoutingConfigurationError("logical-token group conservation failed")
        if active or any(active_debt.values()) or any(active_groups.values()):
            raise RoutingConfigurationError("logical-token group debt did not drain")
        first_transports: dict[int, int] = {}
        invalid = []
        for row in rows:
            group_id = row.get("logical_group_id")
            group = groups.get(group_id) if isinstance(group_id, int) else None
            first = row.get("logical_group_first_transport")
            if first is True and isinstance(group_id, int):
                first_transports[group_id] = first_transports.get(group_id, 0) + 1
            expected_reason = (
                "logical_call_token_debt_selected"
                if first is True
                else "logical_call_affinity_reuse"
            )
            if (
                group is None
                or row.get("endpoint_id") != group["endpoint_id"]
                or row.get("logical_group_request_tokens") != group["request_tokens"]
                or row.get("capacity_weights") != expected_weights
                or row.get("route_reason") != expected_reason
                or first not in {True, False}
            ):
                invalid.append(row)
        if invalid or set(first_transports) != set(groups) or any(
            count != 1 for count in first_transports.values()
        ):
            raise RoutingConfigurationError(
                "V6.1 route evidence violates logical-token affinity"
            )
    elif policy == GRAPHITI_REQUEST_CLASS_AFFINITY:
        invalid = [
            row
            for row in rows
            if (
                row.get("endpoint_id")
                != (
                    "prepare-replica"
                    if row.get("prompt_name") in _EXTRACTION_PROMPTS
                    else "native-replica"
                )
            )
        ]
        if invalid:
            raise RoutingConfigurationError("static-role evidence violates request affinity")
    elif policy == SINGLE_ENDPOINT_FCFS:
        if len(expected_endpoints) != 1:
            raise RoutingConfigurationError("single-endpoint proof received multiple endpoints")
    endpoint_counts = {
        endpoint_id: sum(row.get("endpoint_id") == endpoint_id for row in rows)
        for endpoint_id in sorted(expected_endpoints)
    }
    proof = {
        "schema_version": "membind.v6.1.route-proof.v1",
        "status": "PASS",
        "policy": policy,
        "transport_attempt_count": int(transport_attempt_count),
        "route_event_count": len(rows),
        "endpoint_counts": endpoint_counts,
        "spillover_count": sum(row.get("spillover") is True for row in rows),
        "phase_endpoint_counts": {
            f"{region}:{endpoint_id}": sum(
                row.get("region") == region and row.get("endpoint_id") == endpoint_id
                for row in rows
            )
            for region in ("PREPARE", "NATIVE")
            for endpoint_id in sorted(expected_endpoints)
        },
        "request_index_min": min(request_indices),
        "request_index_max": max(request_indices),
        "all_transports_routed": True,
    }
    if policy in {
        SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY,
        SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY,
        SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY,
    }:
        proof["capacity_weights"] = expected_weights
    if policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY:
        proof["logical_group_count"] = len(groups)
        proof["logical_group_event_count"] = len(group_rows)
    return proof


class _RoutedCompletions:
    def __init__(self, owner: "RoutedOpenAIClient") -> None:
        self._owner = owner

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        return await self._owner._create(*args, **kwargs)


class RoutedOpenAIClient:
    """OpenAI-compatible facade that routes exactly at the completion seam.

    Endpoint selection and outstanding accounting are protected by a short
    synchronous lock.  No lock is held while awaiting provider I/O.
    """

    def __init__(
        self,
        *,
        policy: str,
        endpoints: Sequence[EndpointSpec | Mapping[str, Any]],
        endpoint_clients: Mapping[str, Any],
        capacity_weights: Mapping[str, float] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if policy not in _SUPPORTED_POLICIES:
            raise RoutingConfigurationError(f"unsupported routing policy: {policy}")
        specs = tuple(
            value if isinstance(value, EndpointSpec) else EndpointSpec.from_mapping(value)
            for value in endpoints
        )
        if not specs:
            raise RoutingConfigurationError("routing endpoint set is empty")
        ids = [item.endpoint_id for item in specs]
        if len(set(ids)) != len(ids):
            raise RoutingConfigurationError("routing endpoint ids are not unique")
        if set(endpoint_clients) != set(ids):
            raise RoutingConfigurationError("routing clients do not match the endpoint set")
        if policy != SINGLE_ENDPOINT_FCFS and {
            "native-replica",
            "prepare-replica",
        } - set(ids):
            raise RoutingConfigurationError("dual routing requires native and prepare replicas")
        if policy == SINGLE_ENDPOINT_FCFS and len(specs) != 1:
            raise RoutingConfigurationError("single-endpoint routing requires exactly one endpoint")

        weights = dict(capacity_weights or {})
        for endpoint_id in ids:
            weight = float(weights.get(endpoint_id, 1.0))
            if not math.isfinite(weight) or weight <= 0:
                raise RoutingConfigurationError(f"invalid capacity weight: {endpoint_id}")
            weights[endpoint_id] = weight

        self.policy = policy
        self.endpoints = specs
        self.endpoint_clients = dict(endpoint_clients)
        self.capacity_weights = weights
        self.event_sink = event_sink
        self.chat = SimpleNamespace(completions=_RoutedCompletions(self))
        self._lock = threading.Lock()
        self._outstanding = {endpoint_id: 0 for endpoint_id in ids}
        self._tie_cursor = 0
        self._request_index = 0
        self._logical_group_index = 0
        self._group_audit_index = 0
        self._active_logical_groups = {endpoint_id: 0 for endpoint_id in ids}
        self._active_token_debt = {endpoint_id: 0 for endpoint_id in ids}
        # Per-transport token debt is deliberately separate from logical-call
        # affinity.  Continuations may still spill independently; this state
        # only prices the currently dispatched physical request.
        self._active_dispatch_token_debt = {endpoint_id: 0 for endpoint_id in ids}
        self._logical_group_events: list[dict[str, Any]] = []
        self._active_edge_groups = {endpoint_id: 0 for endpoint_id in ids}
        self._edge_group_events: list[dict[str, Any]] = []
        self._edge_group_audit_index = 0
        self._closed = False
        # Optional V6.1 hook installed by the provider layer.  Keeping this
        # generic avoids a routing/admission import cycle while allowing the
        # physical permit to be acquired after endpoint selection.
        self._membind_physical_admission_enabled = False
        self._membind_physical_admission_acquire: Callable[..., Any] | None = None
        self._membind_physical_admission_release: Callable[..., Any] | None = None
        self._critical_scheduler = (
            CriticalPathResourceScheduler(tuple(item.endpoint_id for item in specs))
            if policy == SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY
            else None
        )
        self._critical_decisions: dict[int, dict[str, Any]] = {}

    def _endpoint(self, endpoint_id: str) -> EndpointSpec:
        for endpoint in self.endpoints:
            if endpoint.endpoint_id == endpoint_id:
                return endpoint
        raise RoutingConfigurationError(f"routing endpoint is unavailable: {endpoint_id}")

    def _choose_locked(self, *, region: str | None, prompt_name: str | None) -> tuple[EndpointSpec, str]:
        if self.policy == SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY:
            if region not in {"PREPARE", "NATIVE"}:
                raise RoutingConfigurationError("V6.1 transport request has no provider region")
            request_tokens = current_provider_request_tokens()
            if not isinstance(request_tokens, int) or request_tokens <= 0:
                raise RoutingConfigurationError(
                    "critical-path routing requires admitted request tokens"
                )
            preferred_id = "prepare-replica" if region == "PREPARE" else "native-replica"
            task_id = f"route-{self._request_index}"
            task = ReadyTask(
                task_id,
                source_sequence=(
                    int(current_provider_scope()[1])
                    if isinstance(current_provider_scope()[1], int)
                    else 0
                ),
                kind=str(prompt_name or region.casefold()),
                token_cost=request_tokens,
                preferred_endpoint_id=preferred_id,
                frontier_critical=region == "NATIVE",
            )
            if self._critical_scheduler is None:
                raise RoutingConfigurationError("critical-path scheduler is unavailable")
            self._critical_scheduler.submit(task)
            decision = self._critical_scheduler.choose()
            if decision is None or decision.task_id != task_id:
                raise RoutingConfigurationError("critical-path scheduler did not dispatch the submitted task")
            self._critical_decisions[self._request_index] = decision.to_dict()
            return self._endpoint(decision.endpoint_id), decision.reason
        if self.policy == SINGLE_ENDPOINT_FCFS:
            return self.endpoints[0], "single_endpoint"
        if self.policy == SEMANTIC_PHASE_AFFINITY:
            if region == "PREPARE":
                return self._endpoint("prepare-replica"), "provider_region_prepare"
            if region == "NATIVE":
                return self._endpoint("native-replica"), "provider_region_native"
            raise RoutingConfigurationError("V6.1 transport request has no provider region")
        if self.policy in {
            SEMANTIC_PHASE_ELASTIC_AFFINITY,
            SEMANTIC_PHASE_EDGE_CALL_AFFINITY,
        }:
            if region not in {"PREPARE", "NATIVE"}:
                raise RoutingConfigurationError("V6.1 transport request has no provider region")
            preferred_id = (
                "prepare-replica" if region == "PREPARE" else "native-replica"
            )
            alternate_id = (
                "native-replica" if region == "PREPARE" else "prepare-replica"
            )
            if (
                self._outstanding[preferred_id] > 0
                and self._outstanding[alternate_id] == 0
            ):
                return self._endpoint(alternate_id), "semantic_phase_idle_spillover"
            return self._endpoint(preferred_id), "semantic_phase_preferred"
        if self.policy == SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY:
            if region not in {"PREPARE", "NATIVE"}:
                raise RoutingConfigurationError("V6.1 transport request has no provider region")
            preferred_id = (
                "prepare-replica" if region == "PREPARE" else "native-replica"
            )
            if self._outstanding[preferred_id] == 0:
                return self._endpoint(preferred_id), "semantic_phase_capacity_preferred"
            projected = {
                endpoint.endpoint_id: (
                    (self._outstanding[endpoint.endpoint_id] + 1)
                    / self.capacity_weights[endpoint.endpoint_id]
                )
                for endpoint in self.endpoints
            }
            best = min(projected.values())
            tied = {
                endpoint_id
                for endpoint_id, value in projected.items()
                if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
            }
            selected_id = (
                preferred_id
                if preferred_id in tied
                else next(
                    endpoint.endpoint_id
                    for endpoint in self.endpoints
                    if endpoint.endpoint_id in tied
                )
            )
            reason = (
                "semantic_phase_capacity_preferred"
                if selected_id == preferred_id
                else "semantic_phase_capacity_spillover"
            )
            return self._endpoint(selected_id), reason
        if self.policy == SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY:
            if region not in {"PREPARE", "NATIVE"}:
                raise RoutingConfigurationError("V6.1 transport request has no provider region")
            request_tokens = current_provider_request_tokens()
            if not isinstance(request_tokens, int) or request_tokens <= 0:
                raise RoutingConfigurationError(
                    "token-debt routing requires admitted request tokens"
                )
            preferred_id = (
                "prepare-replica" if region == "PREPARE" else "native-replica"
            )
            debt = self._active_dispatch_token_debt
            if all(value == 0 for value in debt.values()):
                selected_id = preferred_id
            else:
                projected = {
                    endpoint.endpoint_id: (
                        debt[endpoint.endpoint_id] + request_tokens
                    )
                    / self.capacity_weights[endpoint.endpoint_id]
                    for endpoint in self.endpoints
                }
                best = min(projected.values())
                tied = {
                    endpoint_id
                    for endpoint_id, value in projected.items()
                    if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
                }
                selected_id = (
                    preferred_id
                    if preferred_id in tied
                    else next(
                        endpoint.endpoint_id
                        for endpoint in self.endpoints
                        if endpoint.endpoint_id in tied
                    )
                )
            reason = (
                "semantic_phase_token_debt_preferred"
                if selected_id == preferred_id
                else "semantic_phase_token_debt_spillover"
            )
            return self._endpoint(selected_id), reason
        if self.policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY:
            raise RoutingConfigurationError(
                "logical-token routing requires a logical request group"
            )
        if self.policy == GRAPHITI_REQUEST_CLASS_AFFINITY:
            if prompt_name in _EXTRACTION_PROMPTS:
                return self._endpoint("prepare-replica"), "graphiti_extraction_request"
            return self._endpoint("native-replica"), "graphiti_default_request"

        projected = {
            endpoint.endpoint_id: (
                (self._outstanding[endpoint.endpoint_id] + 1)
                / self.capacity_weights[endpoint.endpoint_id]
            )
            for endpoint in self.endpoints
        }
        best = min(projected.values())
        tied = {
            endpoint.endpoint_id
            for endpoint in self.endpoints
            if math.isclose(projected[endpoint.endpoint_id], best, rel_tol=1e-12, abs_tol=1e-12)
        }
        endpoint = next(
            self.endpoints[(self._tie_cursor + offset) % len(self.endpoints)]
            for offset in range(len(self.endpoints))
            if self.endpoints[(self._tie_cursor + offset) % len(self.endpoints)].endpoint_id
            in tied
        )
        self._tie_cursor = (self.endpoints.index(endpoint) + 1) % len(self.endpoints)
        return endpoint, "capacity_weighted_least_outstanding"

    def _acquire_logical_group_locked(
        self,
        state: _LogicalRouteState,
        *,
        region: str | None,
        source_sequence: int | None,
    ) -> EndpointSpec:
        if region not in {"PREPARE", "NATIVE"}:
            raise RoutingConfigurationError("V6.1 transport request has no provider region")
        request_tokens = state.request_tokens
        if not isinstance(request_tokens, int) or request_tokens <= 0:
            raise RoutingConfigurationError(
                "logical-token routing requires admitted request tokens"
            )
        preferred_id = "prepare-replica" if region == "PREPARE" else "native-replica"
        debt_before = dict(self._active_token_debt)
        groups_before = dict(self._active_logical_groups)
        if all(value == 0 for value in debt_before.values()):
            selected_id = preferred_id
        else:
            projected = {
                endpoint.endpoint_id: (
                    (debt_before[endpoint.endpoint_id] + request_tokens)
                    / self.capacity_weights[endpoint.endpoint_id]
                )
                for endpoint in self.endpoints
            }
            best = min(projected.values())
            tied = {
                endpoint_id
                for endpoint_id, value in projected.items()
                if math.isclose(value, best, rel_tol=1e-12, abs_tol=1e-12)
            }
            selected_id = (
                preferred_id
                if preferred_id in tied
                else next(
                    endpoint.endpoint_id
                    for endpoint in self.endpoints
                    if endpoint.endpoint_id in tied
                )
            )
        reason = (
            "logical_token_debt_preferred"
            if selected_id == preferred_id
            else "logical_token_debt_spillover"
        )
        group_id = self._logical_group_index
        self._logical_group_index += 1
        self._active_token_debt[selected_id] += request_tokens
        self._active_logical_groups[selected_id] += 1
        state.logical_group_id = group_id
        state.endpoint_id = selected_id
        state.release = lambda status: self._release_logical_group(state, status=status)
        self._logical_group_events.append(
            {
                "event": "LLM_ROUTE_GROUP",
                "schema_version": "membind.v6.1.route-group.v1",
                "policy": self.policy,
                "audit_index": self._group_audit_index,
                "action": "acquire",
                "logical_group_id": group_id,
                "endpoint_id": selected_id,
                "preferred_endpoint_id": preferred_id,
                "selection_reason": reason,
                "region": region,
                "source_sequence": source_sequence,
                "prompt_name": state.prompt_name,
                "request_tokens": request_tokens,
                "capacity_weights": dict(self.capacity_weights),
                "active_token_debt_before": debt_before,
                "active_token_debt_after": dict(self._active_token_debt),
                "active_logical_groups_before": groups_before,
                "active_logical_groups_after": dict(self._active_logical_groups),
                "status": "active",
            }
        )
        self._group_audit_index += 1
        return self._endpoint(selected_id)

    def _release_logical_group(
        self, state: _LogicalRouteState, *, status: str
    ) -> None:
        with self._lock:
            if state.release is None:
                return
            group_id = state.logical_group_id
            endpoint_id = state.endpoint_id
            request_tokens = state.request_tokens
            if (
                not isinstance(group_id, int)
                or endpoint_id not in self._active_token_debt
                or not isinstance(request_tokens, int)
                or request_tokens <= 0
                or self._active_logical_groups[endpoint_id] <= 0
                or self._active_token_debt[endpoint_id] < request_tokens
            ):
                raise RoutingConfigurationError("logical route group release underflow")
            debt_before = dict(self._active_token_debt)
            groups_before = dict(self._active_logical_groups)
            self._active_token_debt[endpoint_id] -= request_tokens
            self._active_logical_groups[endpoint_id] -= 1
            state.release = None
            self._logical_group_events.append(
                {
                    "event": "LLM_ROUTE_GROUP",
                    "schema_version": "membind.v6.1.route-group.v1",
                    "policy": self.policy,
                    "audit_index": self._group_audit_index,
                    "action": "release",
                    "logical_group_id": group_id,
                    "endpoint_id": endpoint_id,
                    "region": None,
                    "source_sequence": None,
                    "prompt_name": state.prompt_name,
                    "request_tokens": request_tokens,
                    "capacity_weights": dict(self.capacity_weights),
                    "active_token_debt_before": debt_before,
                    "active_token_debt_after": dict(self._active_token_debt),
                    "active_logical_groups_before": groups_before,
                    "active_logical_groups_after": dict(self._active_logical_groups),
                    "status": status,
                }
            )
            self._group_audit_index += 1

    def _acquire_edge_group_locked(
        self,
        state: _LogicalRouteState,
        *,
        region: str | None,
        source_sequence: int | None,
    ) -> EndpointSpec:
        """Pin one edge logical call to its first elastic endpoint choice.

        An edge page/continuation is part of one Graphiti logical request. A
        continuation that moves between replicas loses prefix locality and can
        create a second queue behind the first page. The first page keeps the
        existing idle-spillover decision; only the continuation is pinned.
        """
        if region not in {"PREPARE", "NATIVE"}:
            raise RoutingConfigurationError("V6.1 transport request has no provider region")
        preferred_id = "prepare-replica" if region == "PREPARE" else "native-replica"
        alternate_id = "native-replica" if region == "PREPARE" else "prepare-replica"
        selection_outstanding = dict(self._outstanding)
        spillover = (
            selection_outstanding[preferred_id] > 0
            and selection_outstanding[alternate_id] == 0
        )
        selected_id = alternate_id if spillover else preferred_id
        group_id = self._logical_group_index
        self._logical_group_index += 1
        before = dict(self._active_edge_groups)
        self._active_edge_groups[selected_id] += 1
        state.logical_group_id = group_id
        state.endpoint_id = selected_id
        state.release = lambda status: self._release_edge_group(state, status=status)
        self._edge_group_events.append(
            {
                "event": "LLM_EDGE_ROUTE_GROUP",
                "schema_version": "membind.v6.1.edge-route-group.v1",
                "policy": self.policy,
                "audit_index": self._edge_group_audit_index,
                "action": "acquire",
                "logical_group_id": group_id,
                "endpoint_id": selected_id,
                "preferred_endpoint_id": preferred_id,
                "selection_outstanding": selection_outstanding,
                "selection_reason": (
                    "edge_call_affinity_idle_spillover"
                    if spillover
                    else "edge_call_affinity_preferred"
                ),
                "region": region,
                "source_sequence": source_sequence,
                "prompt_name": state.prompt_name,
                "active_edge_groups_before": before,
                "active_edge_groups_after": dict(self._active_edge_groups),
                "status": "active",
            }
        )
        self._edge_group_audit_index += 1
        return self._endpoint(selected_id)

    def _release_edge_group(self, state: _LogicalRouteState, *, status: str) -> None:
        with self._lock:
            if state.release is None:
                return
            group_id = state.logical_group_id
            endpoint_id = state.endpoint_id
            if (
                not isinstance(group_id, int)
                or endpoint_id not in self._active_edge_groups
                or self._active_edge_groups[endpoint_id] <= 0
            ):
                raise RoutingConfigurationError("edge route group release underflow")
            before = dict(self._active_edge_groups)
            self._active_edge_groups[endpoint_id] -= 1
            state.release = None
            self._edge_group_events.append(
                {
                    "event": "LLM_EDGE_ROUTE_GROUP",
                    "schema_version": "membind.v6.1.edge-route-group.v1",
                    "policy": self.policy,
                    "audit_index": self._edge_group_audit_index,
                    "action": "release",
                    "logical_group_id": group_id,
                    "endpoint_id": endpoint_id,
                    "preferred_endpoint_id": None,
                    "selection_outstanding": None,
                    "selection_reason": None,
                    "region": None,
                    "source_sequence": None,
                    "prompt_name": state.prompt_name,
                    "active_edge_groups_before": before,
                    "active_edge_groups_after": dict(self._active_edge_groups),
                    "status": status,
                }
            )
            self._edge_group_audit_index += 1

    async def _create(self, *args: Any, **kwargs: Any) -> Any:
        region, source_sequence = current_provider_scope()
        prompt_name = route_prompt_name()
        logical_state = _ROUTE_LOGICAL_STATE.get()
        ephemeral_logical_group = False
        edge_affinity = (
            self.policy == SEMANTIC_PHASE_EDGE_CALL_AFFINITY
            and prompt_name == "extract_edges.edge"
        )
        if (
            self.policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY
            or edge_affinity
        ) and logical_state is None:
            logical_state = _LogicalRouteState(
                prompt_name=prompt_name,
                request_tokens=current_provider_request_tokens(),
            )
            ephemeral_logical_group = True
        start_ns = time.monotonic_ns()
        dispatch_token_debt_before: dict[str, int] | None = None
        dispatch_request_tokens: int | None = None
        dispatch_token_debt_after: dict[str, int] | None = None
        with self._lock:
            if self._closed:
                raise RoutingConfigurationError("routed client is closed")
            selection_outstanding = dict(self._outstanding)
            if self.policy == SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY:
                request_tokens = current_provider_request_tokens()
                if not isinstance(request_tokens, int) or request_tokens <= 0:
                    raise RoutingConfigurationError(
                        "token-debt routing requires admitted request tokens"
                    )
                dispatch_request_tokens = request_tokens
                dispatch_token_debt_before = dict(self._active_dispatch_token_debt)
            logical_group_first = False
            if self.policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY:
                if logical_state is None:
                    raise RoutingConfigurationError("logical route state is unavailable")
                logical_group_first = logical_state.logical_group_id is None
                endpoint = (
                    self._acquire_logical_group_locked(
                        logical_state,
                        region=region,
                        source_sequence=source_sequence,
                    )
                    if logical_group_first
                    else self._endpoint(str(logical_state.endpoint_id))
                )
                reason = (
                    "logical_call_token_debt_selected"
                    if logical_group_first
                    else "logical_call_affinity_reuse"
                )
            elif edge_affinity:
                if logical_state is None:
                    raise RoutingConfigurationError("edge route state is unavailable")
                logical_group_first = logical_state.logical_group_id is None
                if logical_group_first:
                    endpoint = self._acquire_edge_group_locked(
                        logical_state,
                        region=region,
                        source_sequence=source_sequence,
                    )
                    preferred = (
                        "prepare-replica" if region == "PREPARE" else "native-replica"
                    )
                    reason = (
                        "edge_call_affinity_idle_spillover"
                        if endpoint.endpoint_id != preferred
                        else "edge_call_affinity_preferred"
                    )
                else:
                    endpoint = self._endpoint(str(logical_state.endpoint_id))
                    reason = "edge_call_affinity_reuse"
            else:
                endpoint, reason = self._choose_locked(
                    region=region, prompt_name=prompt_name
                )
            if self.policy == SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY:
                assert dispatch_token_debt_before is not None
                assert dispatch_request_tokens is not None
                dispatch_token_debt_after = dict(dispatch_token_debt_before)
                dispatch_token_debt_after[endpoint.endpoint_id] += dispatch_request_tokens
                self._active_dispatch_token_debt[endpoint.endpoint_id] += dispatch_request_tokens
            request_index = self._request_index
            self._request_index += 1
            before = self._outstanding[endpoint.endpoint_id]
            self._outstanding[endpoint.endpoint_id] = before + 1
            at_dispatch = before + 1

        physical_permit: Any = None
        # Admission wait is a queueing signal, not endpoint service work.  The
        # critical-path scheduler learns only from the interval in which the
        # provider request is actually eligible to execute.
        service_start_ns: int | None = None
        service_end_ns: int | None = None
        status = "success"
        error_type: str | None = None
        try:
            physical_acquire = self._membind_physical_admission_acquire
            if self._membind_physical_admission_enabled and physical_acquire is not None:
                physical_permit = await physical_acquire(
                    endpoint_id=endpoint.endpoint_id,
                    region=region,
                    source_sequence=source_sequence,
                    prompt_name=prompt_name,
                    args=args,
                    kwargs=kwargs,
                )
            service_start_ns = time.monotonic_ns()
            completions = self.endpoint_clients[endpoint.endpoint_id].chat.completions
            result = await completions.create(*args, **kwargs)
            service_end_ns = time.monotonic_ns()
            return result
        except BaseException as exc:
            if service_start_ns is not None:
                # Capture provider failures/cancellation before the release
                # hook runs, so cleanup is never priced as endpoint service.
                service_end_ns = time.monotonic_ns()
            status = "cancelled" if isinstance(exc, BaseException) and type(exc).__name__ == "CancelledError" else "failure"
            error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
            raise
        finally:
            if physical_permit is not None:
                physical_release = self._membind_physical_admission_release
                if physical_release is None:
                    raise RoutingConfigurationError(
                        "physical admission release hook is unavailable"
                    )
                await physical_release(physical_permit)
            end_ns = time.monotonic_ns()
            with self._lock:
                current = self._outstanding[endpoint.endpoint_id]
                if current <= 0:
                    raise RoutingConfigurationError("routing outstanding counter underflow")
                self._outstanding[endpoint.endpoint_id] = current - 1
                after = current - 1
                if self.policy == SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY:
                    if dispatch_request_tokens is None:
                        raise RoutingConfigurationError(
                            "token-debt route is missing request token accounting"
                        )
                    debt_after_release = self._active_dispatch_token_debt[endpoint.endpoint_id] - dispatch_request_tokens
                    if debt_after_release < 0:
                        raise RoutingConfigurationError("token-debt route debt underflow")
                    self._active_dispatch_token_debt[endpoint.endpoint_id] = debt_after_release
                critical_decision = self._critical_decisions.pop(request_index, None)
                if self._critical_scheduler is not None:
                    if service_start_ns is None:
                        # No provider work started (typically admission was
                        # cancelled/failed), so release the dispatch
                        # reservation without updating the service EWMA.
                        self._critical_scheduler.cancel(f"route-{request_index}")
                    else:
                        self._critical_scheduler.complete(
                            f"route-{request_index}",
                            service_ns=max(1, end_ns - service_start_ns),
                        )
            row = {
                "event": "LLM_ROUTE",
                "schema_version": "membind.v6.1.llm-route.v1",
                "policy": self.policy,
                "endpoint_id": endpoint.endpoint_id,
                "base_url": endpoint.base_url,
                "served_model": endpoint.served_model,
                "physical_gpu": endpoint.physical_gpu,
                "region": region,
                "prompt_name": prompt_name,
                "source_sequence": source_sequence,
                "request_index": request_index,
                "capacity_weight": self.capacity_weights[endpoint.endpoint_id],
                "capacity_weights": dict(self.capacity_weights),
                "outstanding_before": before,
                "outstanding_at_dispatch": at_dispatch,
                "outstanding_after": after,
                "route_reason": reason,
                "selection_outstanding": selection_outstanding,
                "selection_token_debt": dispatch_token_debt_before,
                "request_tokens": dispatch_request_tokens,
                "active_token_debt_before": dispatch_token_debt_before,
                "active_token_debt_after": dispatch_token_debt_after,
                "preferred_endpoint_id": (
                    "prepare-replica"
                    if region == "PREPARE"
                    else "native-replica"
                    if region == "NATIVE"
                    else None
                ),
                "spillover": reason in {
                    "semantic_phase_idle_spillover",
                    "semantic_phase_capacity_spillover",
                    "semantic_phase_token_debt_spillover",
                    "edge_call_affinity_idle_spillover",
                    "critical_path_earliest_finish_spillover",
                },
                "logical_group_id": (
                    logical_state.logical_group_id if logical_state is not None else None
                ),
                "logical_group_first_transport": (
                    logical_group_first
                    if self.policy == SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY or edge_affinity
                    else None
                ),
                "logical_group_request_tokens": (
                    logical_state.request_tokens if logical_state is not None else None
                ),
                "critical_path_decision": critical_decision,
                "critical_path_token_cost": (
                    critical_decision.get("token_cost")
                    if isinstance(critical_decision, Mapping)
                    else None
                ),
                "start_ns": start_ns,
                "end_ns": end_ns,
                "duration_ns": end_ns - start_ns,
                "service_start_ns": service_start_ns,
                "service_end_ns": service_end_ns,
                "service_duration_ns": (
                    max(1, service_end_ns - service_start_ns)
                    if service_start_ns is not None and service_end_ns is not None
                    else None
                ),
                "status": status,
                "error_type": error_type,
            }
            if self.event_sink is not None:
                self.event_sink(row)
            if ephemeral_logical_group and logical_state is not None and logical_state.release:
                logical_state.release(status)

    def route_evidence(self) -> dict[str, Any]:
        with self._lock:
            outstanding = dict(self._outstanding)
            request_count = self._request_index
            active_logical_groups = dict(self._active_logical_groups)
            active_token_debt = dict(self._active_token_debt)
            active_dispatch_token_debt = dict(self._active_dispatch_token_debt)
            logical_group_events = [dict(row) for row in self._logical_group_events]
            active_edge_groups = dict(self._active_edge_groups)
            edge_group_events = [dict(row) for row in self._edge_group_events]
            critical_scheduler = (
                self._critical_scheduler.evidence()
                if self._critical_scheduler is not None
                else None
            )
        return {
            "schema_version": "membind.v6.1.route-runtime.v1",
            "policy": self.policy,
            "endpoint_set": [
                {
                    "id": endpoint.endpoint_id,
                    "base_url": endpoint.base_url,
                    "served_model": endpoint.served_model,
                    "physical_gpu": endpoint.physical_gpu,
                    "capacity_weight": self.capacity_weights[endpoint.endpoint_id],
                }
                for endpoint in self.endpoints
            ],
            "request_count": request_count,
            "outstanding": outstanding,
            "active_logical_groups": active_logical_groups,
            "active_token_debt": active_token_debt,
            "active_dispatch_token_debt": active_dispatch_token_debt,
            "logical_group_events": logical_group_events,
            "active_edge_groups": active_edge_groups,
            "edge_group_events": edge_group_events,
            "critical_scheduler": critical_scheduler,
            "balanced": (
                all(value == 0 for value in outstanding.values())
                and all(value == 0 for value in active_logical_groups.values())
                and all(value == 0 for value in active_token_debt.values())
                and all(value == 0 for value in active_dispatch_token_debt.values())
                and all(value == 0 for value in active_edge_groups.values())
                and (critical_scheduler is None or critical_scheduler.get("balanced") is True)
            ),
        }

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if any(self._outstanding.values()):
                raise RoutingConfigurationError("cannot close routed client with active requests")
            if (
                any(self._active_logical_groups.values())
                or any(self._active_token_debt.values())
                or any(self._active_dispatch_token_debt.values())
                or any(self._active_edge_groups.values())
            ):
                raise RoutingConfigurationError("cannot close routed client with active logical groups")
            self._closed = True
        seen: set[int] = set()
        for client in self.endpoint_clients.values():
            if id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "close", None)
            if callable(close):
                pending = close()
                if inspect.isawaitable(pending):
                    await pending

    def __getattr__(self, name: str) -> Any:
        primary = self.endpoint_clients[self.endpoints[0].endpoint_id]
        return getattr(primary, name)


__all__ = [
    "CAPACITY_WEIGHTED_LEAST_OUTSTANDING",
    "EndpointSpec",
    "GRAPHITI_REQUEST_CLASS_AFFINITY",
    "RoutedOpenAIClient",
    "RoutingConfigurationError",
    "SEMANTIC_PHASE_AFFINITY",
    "SEMANTIC_PHASE_CAPACITY_BALANCED_AFFINITY",
    "SEMANTIC_PHASE_ELASTIC_AFFINITY",
    "SEMANTIC_PHASE_EDGE_CALL_AFFINITY",
    "SEMANTIC_PHASE_LOGICAL_TOKEN_AFFINITY",
    "SEMANTIC_PHASE_TOKEN_DEBT_AFFINITY",
    "SEMANTIC_PHASE_CRITICAL_PATH_AFFINITY",
    "SINGLE_ENDPOINT_FCFS",
    "install_routing_prompt_context",
    "route_prompt_name",
    "route_request_context",
    "validate_route_evidence",
]
