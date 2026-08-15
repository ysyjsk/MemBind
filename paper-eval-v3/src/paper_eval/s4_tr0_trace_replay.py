"""Deterministic offline scheduling replay for the revised S4 TR0 lane.

TR0 holds observed arrivals and service demands fixed and varies only a small,
explicit scheduling policy.  It performs no I/O and inherits no legacy D0
semantics or authority.  Its output is a counterfactual scheduling control,
never headline real-system performance or a semantic-correctness oracle.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256


TR0_SCHEMA = "membind.paper-eval-v3.tr0-scheduling-trace-replay.v1"
TR0_TRACE_SCHEMA = "membind.paper-eval-v3.measured-work-trace.v1"
TR0_RUN_ID = "s4-tr0-scheduling-trace-replay-offline-20260815-001"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TRACE_FIELDS = {
    "schema_version",
    "trace_id",
    "source_run_id",
    "source_method",
    "capture_scope",
    "trace_complete",
    "checkpoint_complete",
    "failure_count",
    "lost_count",
    "duplicate_count",
    "expected_source_sequences",
    "event_count",
    "events",
}
_EVENT_FIELDS = {
    "work_id",
    "source_sequence",
    "arrival_ns",
    "service_demand_ns",
    "phase_demand_ns",
}
_POLICY_FIELDS = {
    "policy_id",
    "worker_count",
    "dispatch_order",
    "worker_selection",
}
_SOURCE_FIELDS = {"tr0_source", "tr0_test"}
_PRIVATE_FIELDS = {
    "answer",
    "api_key",
    "content",
    "credential",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_content",
    "raw_episode",
    "raw_output",
    "raw_response",
    "secret",
}
_CLAIM_BOUNDARY = {
    "claim_class": "FIXED_DEMAND_COUNTERFACTUAL_SCHEDULING_ONLY",
    "supporting_control_only": True,
    "headline_performance_source": False,
    "real_system_performance_claim_authorized": False,
    "semantic_correctness_oracle": False,
    "fixed_wall_clock_demand_is_counterfactual_only": True,
    "omitted_dynamic_effects": [
        "VLLM_BATCHING_AND_QUEUEING",
        "DATABASE_CONTENTION",
        "CONCURRENCY_DEPENDENT_SERVICE_TIMES",
        "STATE_DEPENDENT_WORK_GENERATION",
        "CHANGING_CANDIDATE_AND_SEARCH_DEMAND",
        "COMMIT_ORDER_FEEDBACK",
    ],
}
_CALIBRATION = {
    "real_system_calibration_required": True,
    "status": "REQUIRED_NOT_SATISFIED",
    "required_policies": ["NATIVE", "CHANGED_POLICY"],
    "required_load_regions": ["LOW", "NEAR_SATURATION"],
    "acceptance_rule_status": "MUST_PREREGISTER_BEFORE_RESULTS",
    "paper_claim_authorized": False,
}
_AUTHORITY = {
    "offline_trace_replay_completed": True,
    "legacy_d0_semantics_inherited": False,
    "legacy_authority_inheritance_allowed": False,
    "legacy_authority_reuse_allowed": False,
    "model_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "live_execution_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}


class TR0TraceReplayError(ValueError):
    """The measured trace, replay policy, or sealed TR0 result is invalid."""


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TR0TraceReplayError(f"{label} must be a mapping")
    return deepcopy(dict(value))


def _sequence(value: object, *, label: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TR0TraceReplayError(f"{label} must be a sequence")
    return deepcopy(list(value))


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise TR0TraceReplayError(f"{label} must be a SHA256")
    return value


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TR0TraceReplayError(f"{label} must be an integer >= {minimum}")
    return value


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _PRIVATE_FIELDS:
                raise TR0TraceReplayError("TR0 trace contains private data")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _validate_trace(value: object) -> dict[str, Any]:
    trace = _mapping(value, label="measured work trace")
    _reject_private(trace)
    if set(trace) != _TRACE_FIELDS:
        raise TR0TraceReplayError("measured work trace fields drift")
    for field in ("trace_id", "source_run_id", "source_method", "capture_scope"):
        if not isinstance(trace.get(field), str) or not trace[field]:
            raise TR0TraceReplayError(f"trace {field} must be explicit")
    if trace.get("schema_version") != TR0_TRACE_SCHEMA:
        raise TR0TraceReplayError("measured work trace schema drift")
    if (
        trace.get("trace_complete") is not True
        or trace.get("checkpoint_complete") is not True
        or trace.get("failure_count") != 0
        or trace.get("lost_count") != 0
        or trace.get("duplicate_count") != 0
    ):
        raise TR0TraceReplayError("trace is incomplete")

    expected = _sequence(
        trace.get("expected_source_sequences"),
        label="expected source sequences",
    )
    if (
        not expected
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in expected
        )
        or expected != list(range(len(expected)))
    ):
        raise TR0TraceReplayError("event count or source coverage is invalid")
    event_count = _integer(trace.get("event_count"), label="trace event_count")
    raw_events = _sequence(trace.get("events"), label="trace events")
    if event_count != len(expected) or len(raw_events) != event_count:
        raise TR0TraceReplayError("event count or source coverage is invalid")

    events: list[dict[str, Any]] = []
    seen_sources: set[int] = set()
    seen_work_ids: set[str] = set()
    for raw_event in raw_events:
        event = _mapping(raw_event, label="trace event")
        if set(event) != _EVENT_FIELDS:
            raise TR0TraceReplayError("trace event fields drift")
        work_id = event.get("work_id")
        if not isinstance(work_id, str) or not work_id or work_id in seen_work_ids:
            raise TR0TraceReplayError("event count or source coverage is invalid")
        source = _integer(
            event.get("source_sequence"), label="event source_sequence"
        )
        if source in seen_sources:
            raise TR0TraceReplayError("event count or source coverage is invalid")
        arrival = _integer(event.get("arrival_ns"), label="event arrival_ns")
        service = _integer(
            event.get("service_demand_ns"),
            label="event service_demand_ns",
            minimum=1,
        )
        phases = _mapping(event.get("phase_demand_ns"), label="phase demand")
        if (
            not phases
            or any(not isinstance(name, str) or not name for name in phases)
            or any(
                isinstance(demand, bool)
                or not isinstance(demand, int)
                or demand < 0
                for demand in phases.values()
            )
            or sum(phases.values()) != service
        ):
            raise TR0TraceReplayError("phase demand does not conserve service demand")
        seen_work_ids.add(work_id)
        seen_sources.add(source)
        events.append(
            {
                "work_id": work_id,
                "source_sequence": source,
                "arrival_ns": arrival,
                "service_demand_ns": service,
                "phase_demand_ns": {
                    name: phases[name] for name in sorted(phases)
                },
            }
        )
    if sorted(seen_sources) != expected:
        raise TR0TraceReplayError("event count or source coverage is invalid")
    trace["expected_source_sequences"] = expected
    trace["events"] = sorted(
        events, key=lambda event: (event["arrival_ns"], event["source_sequence"])
    )
    return trace


def _validate_policy(value: object) -> dict[str, Any]:
    policy = _mapping(value, label="TR0 scheduling policy")
    if set(policy) != _POLICY_FIELDS:
        raise TR0TraceReplayError("TR0 scheduling policy fields drift")
    if not isinstance(policy.get("policy_id"), str) or not policy["policy_id"]:
        raise TR0TraceReplayError("policy_id must be explicit")
    _integer(policy.get("worker_count"), label="worker_count", minimum=1)
    if policy.get("dispatch_order") != "ARRIVAL_THEN_SOURCE_SEQUENCE":
        raise TR0TraceReplayError("unsupported dispatch_order")
    if (
        policy.get("worker_selection")
        != "EARLIEST_AVAILABLE_THEN_LOWEST_ID"
    ):
        raise TR0TraceReplayError("unsupported worker_selection")
    return policy


def _replay_validated_trace(
    trace: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    workers = [0] * policy["worker_count"]
    schedule: list[dict[str, Any]] = []
    for event in trace["events"]:
        worker_id = min(range(len(workers)), key=lambda item: (workers[item], item))
        start = max(event["arrival_ns"], workers[worker_id])
        completion = start + event["service_demand_ns"]
        workers[worker_id] = completion
        schedule.append(
            {
                "work_id": event["work_id"],
                "source_sequence": event["source_sequence"],
                "worker_id": worker_id,
                "arrival_ns": event["arrival_ns"],
                "start_ns": start,
                "completion_ns": completion,
                "queue_wait_ns": start - event["arrival_ns"],
                "flow_time_ns": completion - event["arrival_ns"],
                "service_demand_ns": event["service_demand_ns"],
            }
        )
    first_arrival = min(item["arrival_ns"] for item in schedule)
    last_completion = max(item["completion_ns"] for item in schedule)
    metrics = {
        "event_count": len(schedule),
        "first_arrival_ns": first_arrival,
        "last_completion_ns": last_completion,
        "makespan_ns": last_completion - first_arrival,
        "total_service_demand_ns": sum(
            item["service_demand_ns"] for item in schedule
        ),
        "total_queue_wait_ns": sum(item["queue_wait_ns"] for item in schedule),
        "maximum_queue_wait_ns": max(item["queue_wait_ns"] for item in schedule),
        "total_flow_time_ns": sum(item["flow_time_ns"] for item in schedule),
    }
    return {
        "policy_id": policy["policy_id"],
        "worker_count": policy["worker_count"],
        "schedule": schedule,
        "metrics": metrics,
    }


def replay_fixed_demand_trace(
    trace: Mapping[str, Any], policy: Mapping[str, Any]
) -> dict[str, Any]:
    """Replay one complete fixed-demand trace under a deterministic policy."""

    selected_trace = _validate_trace(trace)
    selected_policy = _validate_policy(policy)
    return _replay_validated_trace(selected_trace, selected_policy)


def _trace_identity(trace: Mapping[str, Any], trace_file_sha256: str) -> dict[str, Any]:
    return {
        "trace_schema_version": trace["schema_version"],
        "trace_id": trace["trace_id"],
        "source_run_id": trace["source_run_id"],
        "source_method": trace["source_method"],
        "capture_scope": trace["capture_scope"],
        "trace_file_sha256": _sha(trace_file_sha256, label="trace file"),
        "normalized_trace_payload_sha256": payload_sha256(trace),
        "trace_complete": True,
        "checkpoint_complete": True,
        "event_count": trace["event_count"],
        "source_sequences": trace["expected_source_sequences"],
        "total_service_demand_ns": sum(
            event["service_demand_ns"] for event in trace["events"]
        ),
    }


def _conservation(trace: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected_sources = trace["expected_source_sequences"]
    expected_demand = {
        event["source_sequence"]: (
            event["arrival_ns"],
            event["service_demand_ns"],
        )
        for event in trace["events"]
    }
    exact_coverage = True
    exactly_once = True
    immutable = True
    for result in results:
        schedule = result["schedule"]
        sources = [item["source_sequence"] for item in schedule]
        exact_coverage &= sorted(sources) == expected_sources
        exactly_once &= len(sources) == len(set(sources)) == len(expected_sources)
        immutable &= all(
            expected_demand.get(item["source_sequence"])
            == (item["arrival_ns"], item["service_demand_ns"])
            for item in schedule
        )
    if not exact_coverage or not exactly_once or not immutable:
        raise TR0TraceReplayError("TR0 policy conservation failed")
    return {
        "same_trace_for_all_policies": True,
        "source_coverage_exact_for_all_policies": True,
        "exactly_once_for_all_policies": True,
        "arrival_demand_immutable_for_all_policies": True,
        "total_service_demand_ns": sum(
            event["service_demand_ns"] for event in trace["events"]
        ),
        "policy_count": len(results),
    }


def build_tr0_trace_replay(
    *,
    trace: Mapping[str, Any],
    trace_file_sha256: str,
    policies: Sequence[Mapping[str, Any]],
    parent_protocol_file_sha256: str,
    amendment_document_file_sha256: str,
    amendment_artifact_file_sha256: str,
    amendment_payload_sha256: str,
    current_stage_pointer_file_sha256: str,
    current_stage_pointer_payload_sha256: str,
    source_sha256: Mapping[str, str],
    git_commit: str,
) -> dict[str, Any]:
    """Build a sealed, independently verifiable offline TR0 result."""

    selected_trace = _validate_trace(trace)
    selected_policies = [_validate_policy(policy) for policy in policies]
    if not selected_policies or len({p["policy_id"] for p in selected_policies}) != len(
        selected_policies
    ):
        raise TR0TraceReplayError("TR0 policies must be nonempty and uniquely identified")
    results = [
        _replay_validated_trace(selected_trace, policy)
        for policy in selected_policies
    ]
    bindings = {
        "parent_protocol_file_sha256": _sha(
            parent_protocol_file_sha256, label="parent protocol file"
        ),
        "amendment_document_file_sha256": _sha(
            amendment_document_file_sha256, label="amendment document file"
        ),
        "amendment_artifact_file_sha256": _sha(
            amendment_artifact_file_sha256, label="amendment artifact file"
        ),
        "amendment_payload_sha256": _sha(
            amendment_payload_sha256, label="amendment payload"
        ),
        "current_stage_pointer_file_sha256": _sha(
            current_stage_pointer_file_sha256, label="current stage pointer file"
        ),
        "current_stage_pointer_payload_sha256": _sha(
            current_stage_pointer_payload_sha256,
            label="current stage pointer payload",
        ),
    }
    sources = _mapping(source_sha256, label="TR0 source inventory")
    if set(sources) != _SOURCE_FIELDS:
        raise TR0TraceReplayError("TR0 source inventory drift")
    sources = {name: _sha(sources[name], label=name) for name in sorted(sources)}
    payload = {
        "schema_version": TR0_SCHEMA,
        "lane": "TR0_SCHEDULING_TRACE_REPLAY",
        "bindings": bindings,
        "trace_identity": _trace_identity(selected_trace, trace_file_sha256),
        "fixed_demand_trace": selected_trace,
        "policies": selected_policies,
        "results": results,
        "policy_conservation": _conservation(selected_trace, results),
        "claim_boundary": deepcopy(_CLAIM_BOUNDARY),
        "calibration": deepcopy(_CALIBRATION),
        "authority": deepcopy(_AUTHORITY),
        "source_sha256": sources,
    }
    _reject_private(payload)
    return finalize_envelope(
        payload=payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=TR0_RUN_ID,
    )


def verify_tr0_trace_replay(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute the complete offline replay and reject any semantic drift."""

    artifact = _mapping(value, label="TR0 artifact")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise TR0TraceReplayError("TR0 artifact envelope shape drift")
    payload = _mapping(artifact.get("payload"), label="TR0 payload")
    _reject_private(payload)
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") != TR0_RUN_ID
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise TR0TraceReplayError("TR0 artifact is not finalized")
    if set(payload) != {
        "schema_version",
        "lane",
        "bindings",
        "trace_identity",
        "fixed_demand_trace",
        "policies",
        "results",
        "policy_conservation",
        "claim_boundary",
        "calibration",
        "authority",
        "source_sha256",
    }:
        raise TR0TraceReplayError("TR0 payload shape drift")
    if (
        payload.get("schema_version") != TR0_SCHEMA
        or payload.get("lane") != "TR0_SCHEDULING_TRACE_REPLAY"
    ):
        raise TR0TraceReplayError("TR0 lane identity drift")

    bindings = _mapping(payload.get("bindings"), label="TR0 bindings")
    expected_binding_fields = {
        "parent_protocol_file_sha256",
        "amendment_document_file_sha256",
        "amendment_artifact_file_sha256",
        "amendment_payload_sha256",
        "current_stage_pointer_file_sha256",
        "current_stage_pointer_payload_sha256",
    }
    if set(bindings) != expected_binding_fields:
        raise TR0TraceReplayError("TR0 bindings drift")
    for name, digest in bindings.items():
        _sha(digest, label=name)

    sources = _mapping(payload.get("source_sha256"), label="TR0 source inventory")
    if set(sources) != _SOURCE_FIELDS:
        raise TR0TraceReplayError("TR0 source inventory drift")
    for name, digest in sources.items():
        _sha(digest, label=name)

    trace = _validate_trace(payload.get("fixed_demand_trace"))
    trace_identity = _mapping(payload.get("trace_identity"), label="trace identity")
    trace_file_sha = _sha(
        trace_identity.get("trace_file_sha256"), label="trace file"
    )
    if trace_identity != _trace_identity(trace, trace_file_sha):
        raise TR0TraceReplayError("TR0 trace identity drift")

    raw_policies = _sequence(payload.get("policies"), label="TR0 policies")
    policies = [_validate_policy(policy) for policy in raw_policies]
    if not policies or len({p["policy_id"] for p in policies}) != len(policies):
        raise TR0TraceReplayError("TR0 policies must be nonempty and uniquely identified")
    expected_results = [_replay_validated_trace(trace, policy) for policy in policies]
    if payload.get("results") != expected_results:
        raise TR0TraceReplayError("TR0 deterministic replay result drift")
    expected_conservation = _conservation(trace, expected_results)
    if payload.get("policy_conservation") != expected_conservation:
        raise TR0TraceReplayError("TR0 policy conservation drift")
    if payload.get("claim_boundary") != _CLAIM_BOUNDARY:
        raise TR0TraceReplayError("TR0 claim boundary drift")
    if payload.get("calibration") != _CALIBRATION:
        raise TR0TraceReplayError("TR0 calibration boundary drift")
    if payload.get("authority") != _AUTHORITY:
        raise TR0TraceReplayError("TR0 authority drift")
    artifact["payload"] = payload
    return artifact
