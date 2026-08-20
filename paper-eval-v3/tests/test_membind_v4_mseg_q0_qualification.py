from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.membind_v31.live_block import V31LiveHooks
from paper_eval.membind_v4.mseg.observability import MSEGOperatorTraceObserver
from paper_eval.membind_v4.mseg.q0_reducer import reduce_q0_qualification
from paper_eval.membind_v4.mseg.q0_runner import (
    execute_q0_measurement,
    write_operator_trace,
)
from paper_eval.membind_v4.mseg import q0_runner
from paper_eval.membind_v4.mseg.qualification import build_q0_live_composition

from test_membind_v4_mseg_instrumented_adapter import _Adapter, _binding


def _metadata() -> dict[str, object]:
    return {
        "operator_role": "graphiti.resolve_extracted_nodes",
        "operator_id": "07741c45:0:FRONTIER:resolve_extracted_nodes:0",
        "parent_bind_id": "07741c45:0:FRONTIER",
        "parent_operator_id": None,
        "operator_phase": "FRONTIER",
    }


def _request_rows(*, instrumented: bool) -> list[dict[str, object]]:
    metadata = _metadata() if instrumented else {}
    request_id = "request-0"
    return [
        {
            "event_type": "llm_request_submitted",
            "request_id": request_id,
            "request_kind": "FRONTIER",
            "stream_id": "07741c45",
            "source_sequence": 0,
            "timestamp_ns": 110,
            "token_count": 100,
            **metadata,
        },
        {
            "event_type": "llm_request_start",
            "request_id": request_id,
            "request_kind": "FRONTIER",
            "stream_id": "07741c45",
            "source_sequence": 0,
            "timestamp_ns": 120,
            **metadata,
        },
        {
            "event_type": "llm_request_terminal",
            "request_id": request_id,
            "timestamp_ns": 180,
            "status": "ok",
            **metadata,
        },
        *(
            [
                {
                    "event_type": "llm_transport_response",
                    "stream_id": "07741c45",
                    "source_sequence": 0,
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                    **metadata,
                }
            ]
            if instrumented
            else []
        ),
    ]


def _operator_events() -> list[dict[str, object]]:
    metadata = _metadata()
    common = {
        **metadata,
        "stream_id": "07741c45",
        "source_sequence": 0,
    }
    return [
        {
            "event_sequence": 0,
            "timestamp_ns": 100,
            "event_type": "operator_enter",
            "operator_ready_ns": None,
            "operator_enter_ns": 100,
            **common,
        },
        {
            "event_sequence": 1,
            "timestamp_ns": 190,
            "event_type": "operator_effect",
            "effect_scope": {"resolved_node_uuids": ["node-a"]},
            "effect_scope_complete": True,
            "read_scope": "NOT_OBSERVABLE",
            "read_scope_complete": False,
            "persistent_write": False,
            **common,
        },
        {
            "event_sequence": 2,
            "timestamp_ns": 200,
            "event_type": "operator_exit",
            "operator_start_ns": 100,
            "operator_end_ns": 200,
            "operator_status": "OK",
            **common,
        },
    ]


def _result() -> dict[str, object]:
    return {
        "history_id": "07741c45",
        "source_count": 12,
        "compile_workers": 2,
        "lookahead": 4,
        "bind_workers": 1,
        "global_llm_admission_k": 2,
        "direct_violation_count": 0,
        "observed_max_inflight": 2,
        "publication_source_sequences": list(range(12)),
        "performance": {
            "makespan_ns": 1_000,
            "p95_freshness_ns": 500,
        },
    }


def _manifest() -> dict[str, object]:
    return {
        "history_id": "07741c45",
        "source_count": 12,
        "compile_workers": 2,
        "lookahead": 4,
        "bind_workers": 1,
        "global_llm_admission_k": 2,
        "policy": "FRONTIER_FIRST_CACHE_AFFINITY",
        "shared_execution_envelope_sha256": "a" * 64,
        "source_manifest_sha256": "b" * 64,
        "arrival_trace_sha256": "c" * 64,
    }


def _state() -> dict[str, object]:
    return {
        "node_count": 3,
        "relationship_count": 4,
        "episode_names": [f"episode-{index}" for index in range(12)],
    }


def test_q0_reducer_passes_only_exact_policy_semantic_and_causal_parity() -> None:
    reduced = reduce_q0_qualification(
        baseline_result=_result(),
        q0_result=_result(),
        baseline_manifest=_manifest(),
        q0_manifest=_manifest(),
        baseline_request_rows=_request_rows(instrumented=False),
        q0_request_rows=_request_rows(instrumented=True),
        operator_events=_operator_events(),
        baseline_state=_state(),
        q0_state=_state(),
    )

    assert reduced["status"] == "PASS_INSTRUMENTATION_QUALIFICATION"
    assert reduced["execution_policy_changed"] is False
    assert reduced["request_count_parity"] is True
    assert reduced["semantic_input_token_parity"] is True
    assert reduced["publication_order_parity"] is True
    assert reduced["published_state_parity"] is True
    assert reduced["causal_correlation"]["submitted_coverage_fraction"] == 1.0
    assert reduced["causal_correlation"]["span_containment_fraction"] == 1.0
    assert reduced["causal_correlation"]["transport_response_count"] == 1
    assert reduced["causal_correlation"]["transport_response_with_metadata"] == 1
    assert reduced["effect_telemetry"]["read_scope_status"] == "NOT_OBSERVABLE"
    assert reduced["post_q0_action"] == "RECONSTRUCT_MSEG_AND_RUN_O1_O4"


def test_q0_reducer_fails_closed_when_transport_response_is_missing() -> None:
    q0_rows = [
        row
        for row in _request_rows(instrumented=True)
        if row["event_type"] != "llm_transport_response"
    ]
    reduced = reduce_q0_qualification(
        baseline_result=_result(),
        q0_result=_result(),
        baseline_manifest=_manifest(),
        q0_manifest=_manifest(),
        baseline_request_rows=_request_rows(instrumented=False),
        q0_request_rows=q0_rows,
        operator_events=_operator_events(),
        baseline_state=_state(),
        q0_state=_state(),
    )

    assert reduced["status"] == "FAIL_INSTRUMENTATION_QUALIFICATION"
    assert "transport_response_correlation_incomplete" in reduced["blocking_reasons"]


def test_q0_reducer_fails_closed_on_one_uncorrelated_request() -> None:
    q0_rows = _request_rows(instrumented=True)
    q0_rows[0].pop("operator_id")
    reduced = reduce_q0_qualification(
        baseline_result=_result(),
        q0_result=_result(),
        baseline_manifest=_manifest(),
        q0_manifest=_manifest(),
        baseline_request_rows=_request_rows(instrumented=False),
        q0_request_rows=q0_rows,
        operator_events=_operator_events(),
        baseline_state=_state(),
        q0_state=_state(),
    )

    assert reduced["status"] == "FAIL_INSTRUMENTATION_QUALIFICATION"
    assert "causal_metadata_incomplete" in reduced["blocking_reasons"]
    assert reduced["post_q0_action"] == "STOP_V4_FINE_GRAINED"


def test_q0_reducer_fails_closed_when_operator_effect_is_missing() -> None:
    reduced = reduce_q0_qualification(
        baseline_result=_result(),
        q0_result=_result(),
        baseline_manifest=_manifest(),
        q0_manifest=_manifest(),
        baseline_request_rows=_request_rows(instrumented=False),
        q0_request_rows=_request_rows(instrumented=True),
        operator_events=[
            event
            for event in _operator_events()
            if event["event_type"] != "operator_effect"
        ],
        baseline_state=_state(),
        q0_state=_state(),
    )

    assert reduced["status"] == "FAIL_INSTRUMENTATION_QUALIFICATION"
    assert "operator_effect_coverage_incomplete" in reduced["blocking_reasons"]


def test_q0_reducer_rejects_claimed_complete_read_scope() -> None:
    events = _operator_events()
    effect = next(event for event in events if event["event_type"] == "operator_effect")
    effect["read_scope_complete"] = True
    reduced = reduce_q0_qualification(
        baseline_result=_result(),
        q0_result=_result(),
        baseline_manifest=_manifest(),
        q0_manifest=_manifest(),
        baseline_request_rows=_request_rows(instrumented=False),
        q0_request_rows=_request_rows(instrumented=True),
        operator_events=events,
        baseline_state=_state(),
        q0_state=_state(),
    )

    assert reduced["status"] == "FAIL_INSTRUMENTATION_QUALIFICATION"
    assert "read_scope_completeness_invalid" in reduced["blocking_reasons"]


def test_q0_live_composition_injects_observability_without_policy_changes() -> None:
    observed: dict[str, object] = {}
    state_snapshots = [
        {"node_count": 0, "relationship_count": 0, "episode_names": []}
    ]

    def runtime_builder(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(method_public_identity={"frozen": True})

    async def namespace_probe(_runtime, namespace):
        value = {
            "node_count": len(state_snapshots),
            "relationship_count": 0,
            "episode_names": [namespace],
        }
        return value

    base = V31LiveHooks(
        runtime_builder=lambda **_kwargs: None,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=namespace_probe,
        namespace_episode=lambda episode, _namespace: episode,
        source_visibility_probe=lambda _runtime, _source: asyncio.sleep(0, result=True),
        reference_time_to_ns=lambda value: int(value),
        adapter_factory=lambda _runtime, _certification: None,
        close_runtime=lambda _runtime: asyncio.sleep(0),
    )
    observer = MSEGOperatorTraceObserver(clock_ns=iter(range(100)).__next__)
    calls: list[str] = []
    composition = build_q0_live_composition(
        observer=observer,
        stream_id="07741c45",
        comparison_namespace="sealed-baseline",
        base_hooks=base,
        runtime_builder=runtime_builder,
        semantic_binding_loader=lambda: _binding(calls),
        inner_adapter_factory=lambda _runtime, _certification, binding: _Adapter(binding),
    )

    runtime = composition.hooks.runtime_builder(
        policy="FRONTIER_FIRST_CACHE_AFFINITY",
        limit=2,
    )
    assert observed["policy"] == "FRONTIER_FIRST_CACHE_AFFINITY"
    assert observed["limit"] == 2
    assert callable(observed["causal_metadata_provider"])
    assert set(observed) == {"policy", "limit", "causal_metadata_provider"}

    adapter = composition.hooks.adapter_factory(runtime, object())
    assert adapter._stream_id == "07741c45"
    assert adapter._observer is observer
    assert composition.execution_policy_changed is False

    asyncio.run(composition.hooks.runtime_ready(runtime))
    assert composition.comparison_state == {
        "node_count": 1,
        "relationship_count": 0,
        "episode_names": ["sealed-baseline"],
    }


def test_empty_q0_operator_trace_is_created_for_early_failure(tmp_path) -> None:
    target = tmp_path / "q0" / "V4_MSEG_Q0_OPERATOR_TRACE.jsonl"

    write_operator_trace(target, [])

    assert target.is_file()
    assert target.read_bytes() == b""


def test_q0_runner_preserves_operator_trace_when_pilot_fails_after_root_creation(
    tmp_path, monkeypatch
) -> None:
    async def fail_after_root_creation(**kwargs):
        Path(kwargs["output_root"]).mkdir(parents=True, exist_ok=False)
        raise RuntimeError("controlled pilot failure")

    monkeypatch.setattr(q0_runner, "execute_w4_pilot", fail_after_root_creation)
    hooks = V31LiveHooks(
        runtime_builder=lambda **_kwargs: None,
        runtime_ready=lambda _runtime: asyncio.sleep(0),
        namespace_probe=lambda _runtime, _namespace: asyncio.sleep(
            0, result={"node_count": 0, "relationship_count": 0, "episode_names": []}
        ),
        namespace_episode=lambda episode, _namespace: episode,
        source_visibility_probe=lambda _runtime, _source: asyncio.sleep(0, result=True),
        reference_time_to_ns=lambda value: int(value),
        adapter_factory=lambda _runtime, _certification: None,
        close_runtime=lambda _runtime: asyncio.sleep(0),
    )
    composition = q0_runner.Q0LiveComposition(
        hooks=hooks,
        observer=MSEGOperatorTraceObserver(),
        stream_id="07741c45",
        comparison_namespace=None,
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    output = tmp_path / "q0"

    with pytest.raises(RuntimeError, match="controlled pilot failure"):
        asyncio.run(
            execute_q0_measurement(
                contract={},
                verified_formal_plan={},
                episodes=(),
                env={},
                output_root=output,
                state_cut_certification=object(),
                implementation_sha256="a" * 64,
                composition=composition,
                baseline_root=baseline,
            )
        )

    trace = output / "V4_MSEG_Q0_OPERATOR_TRACE.jsonl"
    assert trace.is_file()
    assert trace.read_bytes() == b""
