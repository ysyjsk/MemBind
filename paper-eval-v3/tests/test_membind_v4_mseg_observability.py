from __future__ import annotations

import asyncio

import pytest

from paper_eval.membind_v4.mseg.observability import (
    MSEGObservabilityError,
    MSEGOperatorTraceObserver,
    current_operator_metadata,
    trace_observer_scope,
    workflow_scope,
)


def test_operator_context_is_stable_and_inherited_by_async_child_tasks() -> None:
    observer = MSEGOperatorTraceObserver(clock_ns=iter((100, 101)).__next__)

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        with workflow_scope(stream_id="07741c45", source_sequence=3, phase="FRONTIER"):
            with trace_observer_scope(observer):
                with observer.span("resolve_extracted_nodes"):
                    direct = current_operator_metadata()
                    inherited = await asyncio.create_task(
                        asyncio.sleep(0, result=current_operator_metadata())
                    )
                    return direct, inherited

    direct, inherited = asyncio.run(scenario())
    assert direct == inherited == {
        "operator_role": "graphiti.resolve_extracted_nodes",
        "operator_id": "07741c45:3:FRONTIER:resolve_extracted_nodes:0",
        "parent_bind_id": "07741c45:3:FRONTIER",
        "parent_operator_id": None,
        "operator_phase": "FRONTIER",
    }
    assert [event["timestamp_ns"] for event in observer.events] == [100, 101]
    assert observer.events[0]["operator_enter_ns"] == 100
    assert observer.events[1]["operator_end_ns"] == 101
    assert observer.events[0]["operator_ready_ns"] is None


def test_error_span_has_a_real_monotonic_end_timestamp_and_resets_context() -> None:
    observer = MSEGOperatorTraceObserver(clock_ns=iter((500, 507)).__next__)

    with pytest.raises(RuntimeError, match="private failure"):
        with workflow_scope(stream_id="07741c45", source_sequence=4, phase="FRONTIER"):
            with trace_observer_scope(observer):
                with observer.span("resolve_extracted_edges"):
                    raise RuntimeError("private failure")

    enter, exit_event = observer.events
    assert enter["timestamp_ns"] == 500
    assert exit_event["timestamp_ns"] == 507
    assert exit_event["operator_end_ns"] == 507
    assert exit_event["operator_status"] == "ERROR"
    assert exit_event["error_class"] == "builtins.RuntimeError"
    assert current_operator_metadata() == {}
    assert "private failure" not in repr(observer.events)


def test_long_valid_workflow_identity_uses_bounded_stable_operator_ids() -> None:
    stream_id = "h" * 128
    observer = MSEGOperatorTraceObserver(clock_ns=iter((1, 2)).__next__)
    with workflow_scope(stream_id=stream_id, source_sequence=11, phase="FRONTIER"):
        with observer.span("extract_attributes_from_nodes") as context:
            assert len(context.parent_bind_id) <= 128
            assert len(context.operator_id) <= 128
            assert context.operator_id.startswith("mseg-op:")


def test_effect_scope_accepts_only_content_safe_exact_identifiers_and_marks_reads_unknown() -> None:
    observer = MSEGOperatorTraceObserver(clock_ns=iter((1, 2, 3)).__next__)
    with workflow_scope(stream_id="07741c45", source_sequence=2, phase="FRONTIER"):
        with observer.span("resolve_extracted_edges") as context:
            observer.record_effect(
                context,
                effect_scope={
                    "resolved_edges": [
                        {
                            "edge_uuid": "edge-1",
                            "source_node_uuid": "node-a",
                            "target_node_uuid": "node-b",
                        }
                    ],
                    "invalidated_edges": [],
                    "new_edges": [],
                },
                persistent_write=False,
            )

    effect = observer.events[1]
    assert effect["event_type"] == "operator_effect"
    assert effect["effect_scope_complete"] is True
    assert effect["read_scope"] == "NOT_OBSERVABLE"
    assert effect["read_scope_complete"] is False
    assert effect["persistent_write"] is False

    observer = MSEGOperatorTraceObserver(clock_ns=iter((10, 11, 12)).__next__)
    with workflow_scope(stream_id="07741c45", source_sequence=2, phase="FRONTIER"):
        with pytest.raises(MSEGObservabilityError, match="effect_identifier_invalid"):
            with observer.span("resolve_extracted_edges") as context:
                observer.record_effect(
                    context,
                    effect_scope={"resolved_edge_uuids": ["private prompt text"]},
                    persistent_write=False,
                )
