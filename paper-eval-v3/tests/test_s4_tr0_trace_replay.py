"""Offline TDD contract for TR0 fixed-demand scheduling trace replay.

TR0 is intentionally separate from legacy candidate-level D0 replay.  These
tests pin a deterministic virtual-time scheduler, input-work conservation, and
the boundary that makes TR0 supporting evidence rather than measured-system
performance or semantic-correctness evidence.
"""

from __future__ import annotations

import copy

import pytest

from paper_eval.s4_tr0_trace_replay import (
    TR0_RUN_ID,
    TR0_SCHEMA,
    TR0TraceReplayError,
    build_tr0_trace_replay,
    replay_fixed_demand_trace,
    verify_tr0_trace_replay,
)


SHA = "a" * 64


def _trace() -> dict:
    return {
        "schema_version": "membind.paper-eval-v3.measured-work-trace.v1",
        "trace_id": "rx0-native-real-trace-dev-001",
        "source_run_id": "rx0-native-real-dev-001",
        "source_method": "U0",
        "capture_scope": "MINIMAL_REAL_SYSTEM_INSTRUMENTATION",
        "trace_complete": True,
        "checkpoint_complete": True,
        "failure_count": 0,
        "lost_count": 0,
        "duplicate_count": 0,
        "expected_source_sequences": [0, 1, 2],
        "event_count": 3,
        "events": [
            {
                "work_id": "episode-000",
                "source_sequence": 0,
                "arrival_ns": 0,
                "service_demand_ns": 10,
                "phase_demand_ns": {"prepare": 6, "bind_publish": 4},
            },
            {
                "work_id": "episode-001",
                "source_sequence": 1,
                "arrival_ns": 5,
                "service_demand_ns": 4,
                "phase_demand_ns": {"prepare": 3, "bind_publish": 1},
            },
            {
                "work_id": "episode-002",
                "source_sequence": 2,
                "arrival_ns": 8,
                "service_demand_ns": 6,
                "phase_demand_ns": {"prepare": 4, "bind_publish": 2},
            },
        ],
    }


def _policies() -> list[dict]:
    return [
        {
            "policy_id": "NATIVE_SERIAL_FIFO",
            "worker_count": 1,
            "dispatch_order": "ARRIVAL_THEN_SOURCE_SEQUENCE",
            "worker_selection": "EARLIEST_AVAILABLE_THEN_LOWEST_ID",
        },
        {
            "policy_id": "CHANGED_POLICY_C2_FIFO",
            "worker_count": 2,
            "dispatch_order": "ARRIVAL_THEN_SOURCE_SEQUENCE",
            "worker_selection": "EARLIEST_AVAILABLE_THEN_LOWEST_ID",
        },
    ]


def _build() -> dict:
    return build_tr0_trace_replay(
        trace=_trace(),
        trace_file_sha256="1" * 64,
        policies=_policies(),
        parent_protocol_file_sha256="2" * 64,
        amendment_document_file_sha256="3" * 64,
        amendment_artifact_file_sha256="4" * 64,
        amendment_payload_sha256="5" * 64,
        current_stage_pointer_file_sha256="6" * 64,
        current_stage_pointer_payload_sha256="7" * 64,
        source_sha256={
            "tr0_source": "8" * 64,
            "tr0_test": "9" * 64,
        },
        git_commit="deadbeef",
    )


def test_virtual_time_replay_is_exact_and_deterministic() -> None:
    serial = replay_fixed_demand_trace(_trace(), _policies()[0])
    parallel = replay_fixed_demand_trace(_trace(), _policies()[1])

    assert serial["schedule"] == [
        {
            "work_id": "episode-000",
            "source_sequence": 0,
            "worker_id": 0,
            "arrival_ns": 0,
            "start_ns": 0,
            "completion_ns": 10,
            "queue_wait_ns": 0,
            "flow_time_ns": 10,
            "service_demand_ns": 10,
        },
        {
            "work_id": "episode-001",
            "source_sequence": 1,
            "worker_id": 0,
            "arrival_ns": 5,
            "start_ns": 10,
            "completion_ns": 14,
            "queue_wait_ns": 5,
            "flow_time_ns": 9,
            "service_demand_ns": 4,
        },
        {
            "work_id": "episode-002",
            "source_sequence": 2,
            "worker_id": 0,
            "arrival_ns": 8,
            "start_ns": 14,
            "completion_ns": 20,
            "queue_wait_ns": 6,
            "flow_time_ns": 12,
            "service_demand_ns": 6,
        },
    ]
    assert parallel["schedule"][2] == {
        "work_id": "episode-002",
        "source_sequence": 2,
        "worker_id": 1,
        "arrival_ns": 8,
        "start_ns": 9,
        "completion_ns": 15,
        "queue_wait_ns": 1,
        "flow_time_ns": 7,
        "service_demand_ns": 6,
    }
    assert replay_fixed_demand_trace(_trace(), _policies()[1]) == parallel
    assert serial["metrics"] == {
        "event_count": 3,
        "first_arrival_ns": 0,
        "last_completion_ns": 20,
        "makespan_ns": 20,
        "total_service_demand_ns": 20,
        "total_queue_wait_ns": 11,
        "maximum_queue_wait_ns": 6,
        "total_flow_time_ns": 31,
    }


def test_artifact_binds_identity_inputs_and_policy_conservation() -> None:
    artifact = verify_tr0_trace_replay(_build())
    payload = artifact["payload"]

    assert artifact["run_id"] == TR0_RUN_ID
    assert payload["schema_version"] == TR0_SCHEMA
    assert payload["lane"] == "TR0_SCHEDULING_TRACE_REPLAY"
    assert payload["bindings"] == {
        "parent_protocol_file_sha256": "2" * 64,
        "amendment_document_file_sha256": "3" * 64,
        "amendment_artifact_file_sha256": "4" * 64,
        "amendment_payload_sha256": "5" * 64,
        "current_stage_pointer_file_sha256": "6" * 64,
        "current_stage_pointer_payload_sha256": "7" * 64,
    }
    assert payload["trace_identity"]["trace_file_sha256"] == "1" * 64
    assert payload["trace_identity"]["trace_complete"] is True
    assert payload["trace_identity"]["event_count"] == 3
    assert payload["trace_identity"]["source_sequences"] == [0, 1, 2]
    assert payload["trace_identity"]["total_service_demand_ns"] == 20
    assert payload["policy_conservation"] == {
        "same_trace_for_all_policies": True,
        "source_coverage_exact_for_all_policies": True,
        "exactly_once_for_all_policies": True,
        "arrival_demand_immutable_for_all_policies": True,
        "total_service_demand_ns": 20,
        "policy_count": 2,
    }
    assert [result["policy_id"] for result in payload["results"]] == [
        "NATIVE_SERIAL_FIFO",
        "CHANGED_POLICY_C2_FIFO",
    ]


def test_claim_boundary_and_calibration_cannot_be_upgraded_by_tr0() -> None:
    payload = verify_tr0_trace_replay(_build())["payload"]

    assert payload["claim_boundary"] == {
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
    assert payload["calibration"] == {
        "real_system_calibration_required": True,
        "status": "REQUIRED_NOT_SATISFIED",
        "required_policies": ["NATIVE", "CHANGED_POLICY"],
        "required_load_regions": ["LOW", "NEAR_SATURATION"],
        "acceptance_rule_status": "MUST_PREREGISTER_BEFORE_RESULTS",
        "paper_claim_authorized": False,
    }


def test_new_lane_inherits_no_legacy_authority_and_opens_no_execution() -> None:
    authority = verify_tr0_trace_replay(_build())["payload"]["authority"]

    assert authority == {
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


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda trace: trace["events"].pop(1),
            "event count or source coverage",
        ),
        (
            lambda trace: trace["events"][1].update(source_sequence=0),
            "event count or source coverage",
        ),
        (
            lambda trace: trace["events"][0]["phase_demand_ns"].update(
                prepare=5
            ),
            "phase demand",
        ),
        (
            lambda trace: trace.update(trace_complete=False),
            "trace is incomplete",
        ),
        (
            lambda trace: trace.update(failure_count=1),
            "trace is incomplete",
        ),
    ],
)
def test_trace_completeness_fails_closed(mutate, message: str) -> None:
    trace = _trace()
    mutate(trace)
    with pytest.raises(TR0TraceReplayError, match=message):
        replay_fixed_demand_trace(trace, _policies()[0])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda artifact: artifact["payload"]["results"][0]["schedule"][0].update(
            completion_ns=11
        ),
        lambda artifact: artifact["payload"]["claim_boundary"].update(
            headline_performance_source=True
        ),
        lambda artifact: artifact["payload"]["calibration"].update(
            paper_claim_authorized=True
        ),
        lambda artifact: artifact["payload"]["authority"].update(
            legacy_authority_inheritance_allowed=True
        ),
        lambda artifact: artifact["payload"]["authority"].update(
            live_execution_authorized=True
        ),
    ],
)
def test_verifier_recomputes_results_and_rejects_authority_drift(mutate) -> None:
    artifact = _build()
    mutate(artifact)
    # Re-sealing must not make a semantically forged payload valid.
    from paper_eval.artifacts import payload_sha256

    artifact["payload_sha256"] = payload_sha256(artifact["payload"])
    with pytest.raises(TR0TraceReplayError):
        verify_tr0_trace_replay(artifact)


def test_policy_shape_and_hash_bindings_fail_closed() -> None:
    changed = _policies()
    changed[1]["worker_count"] = 0
    with pytest.raises(TR0TraceReplayError, match="worker_count"):
        replay_fixed_demand_trace(_trace(), changed[1])

    with pytest.raises(TR0TraceReplayError, match="SHA256"):
        build_tr0_trace_replay(
            trace=_trace(),
            trace_file_sha256=SHA,
            policies=_policies(),
            parent_protocol_file_sha256="not-a-hash",
            amendment_document_file_sha256=SHA,
            amendment_artifact_file_sha256=SHA,
            amendment_payload_sha256=SHA,
            current_stage_pointer_file_sha256=SHA,
            current_stage_pointer_payload_sha256=SHA,
            source_sha256={"tr0_source": SHA, "tr0_test": SHA},
            git_commit="deadbeef",
        )


def test_no_prompt_secret_or_raw_content_can_enter_trace_artifact() -> None:
    trace = _trace()
    trace["events"][0]["prompt"] = "private"
    with pytest.raises(TR0TraceReplayError, match="private data"):
        replay_fixed_demand_trace(trace, _policies()[0])


def test_input_trace_is_not_mutated() -> None:
    trace = _trace()
    original = copy.deepcopy(trace)
    replay_fixed_demand_trace(trace, _policies()[1])
    assert trace == original
