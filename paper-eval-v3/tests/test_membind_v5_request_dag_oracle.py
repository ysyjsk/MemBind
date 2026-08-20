from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from paper_eval.artifacts import payload_sha256

from paper_eval.membind_v5_oracle.model import (
    DependencyKind,
    PublicationRecord,
    RequestRecord,
    TraceBundle,
)
from paper_eval.membind_v5_oracle.artifacts import write_analysis_artifacts
from paper_eval.membind_v5_oracle.request_dag import (
    RequestDAG,
    build_request_dag,
)
from paper_eval.membind_v5_oracle.replay import (
    ReplayError,
    replay,
)
from paper_eval.membind_v5_oracle.trace_parser import (
    TraceParseError,
    load_trace_bundle,
)


def _request(
    request_id: str,
    *,
    source: int = 0,
    kind: str = "FRONTIER",
    role: str = "graphiti.resolve_extracted_nodes",
    submitted: int = 0,
    started: int = 0,
    terminal: int = 10,
    token_count: int = 1,
) -> RequestRecord:
    return RequestRecord(
        request_id=request_id,
        stream_id="fixture",
        source_sequence=source,
        request_kind=kind,
        operator_role=role,
        operator_id=f"fixture:{source}:{request_id}",
        parent_bind_id=f"fixture:{source}:FRONTIER",
        parent_operator_id=None,
        operator_phase="FRONTIER" if kind == "FRONTIER" else "COMPILE",
        submitted_ns=submitted,
        started_ns=started,
        terminal_ns=terminal,
        service_duration_ns=terminal - started,
        token_count=token_count,
        prompt_tokens="NOT_OBSERVABLE",
        completion_tokens="NOT_OBSERVABLE",
        execution_mode="NOT_OBSERVABLE",
        persistent_state_access_class="NOT_OBSERVABLE",
    )


def _bundle(
    requests: tuple[RequestRecord, ...],
    *,
    arrival: int = 0,
    publication: int = 10,
    k: int = 2,
) -> TraceBundle:
    return TraceBundle(
        history_id="fixture",
        requests=requests,
        publications=(
            PublicationRecord(
                source_sequence=0,
                arrival_ns=arrival,
                publication_ns=publication,
            ),
        ),
        configured_k=k,
        source_count=1,
        input_paths=(),
    )


def test_request_dag_is_acyclic_and_has_evidence_backed_edges() -> None:
    requests = (
        _request("nodes", role="graphiti.extract_nodes", kind="COMPILE", terminal=3),
        _request(
            "edges",
            role="graphiti.extract_edges",
            kind="COMPILE",
            submitted=3,
            started=3,
            terminal=5,
        ),
        _request(
            "resolve",
            role="graphiti.resolve_extracted_nodes",
            submitted=5,
            started=5,
            terminal=8,
        ),
        _request(
            "attrs",
            role="graphiti.extract_attributes_from_nodes",
            submitted=8,
            started=8,
            terminal=10,
        ),
    )
    dag = build_request_dag(_bundle(requests))

    assert dag.topological_order
    assert dag.has_cycle is False
    assert dag.edge("nodes", "edges").kind is DependencyKind.DATA
    assert dag.edge("resolve", "attrs").kind is DependencyKind.CONTROL
    assert dag.publication_sink_id(0) in dag.nodes


def test_unknown_dependency_is_conservative_and_does_not_fabricate_an_edge() -> None:
    request = _request(
        "mystery",
        role="graphiti.unrecorded_operator",
        submitted=0,
        started=0,
        terminal=2,
    )
    dag = build_request_dag(_bundle((request,)))

    assert dag.unknown_dependencies
    assert dag.edge("mystery", dag.publication_sink_id(0)).kind is DependencyKind.PUBLICATION
    assert dag.oracle_evaluable is False


def test_replay_enforces_dependency_ready_and_k_two() -> None:
    requests = (
        _request("a", submitted=0, started=0, terminal=5),
        _request("b", submitted=0, started=5, terminal=9),
        _request("c", submitted=0, started=9, terminal=12),
    )
    bundle = _bundle(requests, publication=12)
    dag = RequestDAG.from_edges(
        bundle,
        extra_edges=(("a", "b", DependencyKind.DATA), ("b", "c", DependencyKind.DATA)),
    )

    result = replay(bundle, dag=dag, policy="ORACLE")
    assert result.request_count == len(requests)
    assert result.max_active_count <= 2
    assert result.request_service_duration_ns == {
        request.request_id: request.service_duration_ns for request in requests
    }
    assert all(
        result.request_start_ns[predecessor]
        + requests[[r.request_id for r in requests].index(predecessor)].service_duration_ns
        <= result.request_start_ns[successor]
        for edge in dag.edges
        if edge.predecessor in result.request_start_ns and edge.successor in result.request_start_ns
        for predecessor, successor in [(edge.predecessor, edge.successor)]
    )


def test_actual_replay_reproduces_publication_timestamps_exactly() -> None:
    requests = (
        _request("a", submitted=0, started=0, terminal=5),
        _request("b", submitted=0, started=5, terminal=9),
    )
    bundle = _bundle(requests, publication=20)
    dag = RequestDAG.from_edges(
        bundle,
        extra_edges=(("a", "b", DependencyKind.DATA),),
    )

    result = replay(bundle, dag=dag, policy="ACTUAL")
    assert result.publication_ns == {0: 20}
    assert result.actual_publication_delta_ns == {0: 0}
    assert result.extra_llm_calls == 0
    assert result.extra_input_tokens == 0


def test_oracle_never_schedules_unreleased_or_unready_request() -> None:
    requests = (
        _request("a", submitted=10, started=10, terminal=15),
        _request("b", submitted=0, started=0, terminal=2),
    )
    bundle = _bundle(requests, publication=15)
    dag = RequestDAG.from_edges(
        bundle,
        extra_edges=(("b", "a", DependencyKind.DATA),),
    )

    result = replay(bundle, dag=dag, policy="ORACLE")
    assert result.request_start_ns["b"] == 0
    assert result.request_start_ns["a"] >= 10
    assert result.request_start_ns["a"] >= result.request_terminal_ns["b"]


def test_frontier_first_legality_is_preserved_in_oracle() -> None:
    compile_request = _request(
        "compile",
        kind="COMPILE",
        role="graphiti.extract_nodes",
        submitted=0,
        terminal=10,
    )
    frontier_request = _request(
        "frontier",
        kind="FRONTIER",
        submitted=0,
        terminal=1,
    )
    bundle = _bundle((compile_request, frontier_request), publication=10)
    dag = RequestDAG.from_edges(bundle)

    result = replay(bundle, dag=dag, policy="ORACLE")
    assert result.request_start_order[0] == "frontier"


def test_synthetic_zero_choice_fixture_has_no_scheduler_opportunity() -> None:
    request = _request("only", submitted=0, started=0, terminal=3)
    bundle = _bundle((request,), publication=3)
    result = replay(bundle, dag=build_request_dag(bundle), policy="ACTUAL")

    assert result.scheduler_choice_count == 0
    assert result.criticality_inversion_count == 0
    assert result.max_legal_choice_width == 1


def test_synthetic_criticality_inversion_is_detected() -> None:
    selected = _request("selected", submitted=0, started=0, terminal=2)
    alternative = _request(
        "critical",
        submitted=0,
        started=2,
        terminal=12,
        role="graphiti.extract_attributes_from_nodes",
    )
    bundle = _bundle((selected, alternative), publication=12)
    dag = RequestDAG.from_edges(bundle)

    result = replay(bundle, dag=dag, policy="ACTUAL")
    assert result.scheduler_choice_count >= 1
    assert result.criticality_inversion_count == 1
    assert result.max_legal_choice_width == 2


def test_invalid_trace_service_duration_fails_closed() -> None:
    with pytest.raises(ValueError, match="service_duration"):
        request = replace(_request("bad"), service_duration_ns=-1)
        bundle = _bundle((request,), publication=3)
        replay(bundle, dag=build_request_dag(bundle), policy="ORACLE")


def _write_wrapped(path, payload_key: str, digest_key: str, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps({payload_key: row, digest_key: payload_sha256(row)}, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def test_trace_parser_verifies_wrappers_and_preserves_unavailable_fields(tmp_path) -> None:
    request = _request("r", terminal=3)
    submitted = {
        "event_type": "llm_request_submitted",
        "request_id": request.request_id,
        "request_kind": request.request_kind,
        "stream_id": request.stream_id,
        "source_sequence": request.source_sequence,
        "timestamp_ns": request.submitted_ns,
        "token_count": request.token_count,
        "operator_role": request.operator_role,
        "operator_id": request.operator_id,
        "parent_bind_id": request.parent_bind_id,
        "parent_operator_id": request.parent_operator_id,
        "operator_phase": request.operator_phase,
    }
    started = {"event_type": "llm_request_start", "request_id": request.request_id, "timestamp_ns": request.started_ns}
    terminal = {"event_type": "llm_request_terminal", "request_id": request.request_id, "timestamp_ns": request.terminal_ns}
    event_arrival = {"event_type": "ARRIVAL", "source_sequence": 0, "timestamp_ns": 0}
    event_publication = {"event_type": "PUBLICATION_DURABLE", "source_sequence": 0, "timestamp_ns": 3}
    llm = tmp_path / "llm.jsonl"
    events = tmp_path / "events.jsonl"
    _write_wrapped(llm, "record", "record_sha256", [{"row": row, "schema_version": "fixture"} for row in (submitted, started, terminal)])
    _write_wrapped(events, "event", "event_sha256", [
        {"schema_version": "fixture", **event}
        for event in (event_arrival, event_publication)
    ])
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"history_id": "fixture", "source_count": 1, "global_llm_admission_k": 2}), encoding="utf-8")

    bundle = load_trace_bundle(llm_path=llm, events_path=events, manifest_path=manifest)
    assert len(bundle.requests) == 1
    assert bundle.observability["prompt_name"] == "NOT_OBSERVABLE"
    assert bundle.requests[0].completion_tokens == "NOT_OBSERVABLE"


def test_trace_parser_rejects_hash_invalid_wrapper(tmp_path) -> None:
    llm = tmp_path / "llm.jsonl"
    llm.write_text(json.dumps({"record": {"event_type": "llm_request_submitted"}, "record_sha256": "0" * 64}) + "\n", encoding="utf-8")
    events = tmp_path / "events.jsonl"
    _write_wrapped(events, "event", "event_sha256", [{"event_type": "noop"}])

    with pytest.raises(TraceParseError, match="trace_hash_invalid"):
        load_trace_bundle(llm_path=llm, events_path=events, history_id="fixture", configured_k=2, source_count=0)


def test_offline_artifact_writer_emits_sealed_json_and_stop_gate(tmp_path) -> None:
    request = _request("r", terminal=3)
    bundle = _bundle((request,), publication=3)
    output = tmp_path / "postmortem"
    result = write_analysis_artifacts(bundle, build_request_dag(bundle), output)

    assert result["opportunity"]["decision"]["decision"] == "STOP_REQUEST_SCHEDULER"
    assert result["opportunity"]["q0_diagnostic_only"] is True
    assert result["opportunity"]["live_run_performed"] is False
    for name in (
        "V5_REQUEST_DAG_AUDIT.json",
        "V5_REQUEST_DAG_AUDIT.md",
        "V5_SCHEDULER_OPPORTUNITY.json",
        "V5_SCHEDULER_OPPORTUNITY.csv",
        "V5_PUBLICATION_CRITICAL_ORACLE.json",
        "V5_PUBLICATION_CRITICAL_ORACLE.md",
        "V5_NEXT_DECISION.md",
    ):
        assert (output / name).is_file()
    payload = json.loads((output / "V5_SCHEDULER_OPPORTUNITY.json").read_text(encoding="utf-8"))
    digest = payload.pop("payload_sha256")
    assert digest == payload_sha256(payload)


def test_sealed_q0_trace_stops_before_replay_on_unknown_edge_subchains(
    tmp_path,
) -> None:
    project = Path(__file__).resolve().parents[1]
    trace_root = project / "artifacts/paper_eval/membind_v4/mseg/q0/membind-v31-opt-w4-q0-20260820-001"
    bundle = load_trace_bundle(
        llm_path=trace_root / "llm.jsonl",
        events_path=trace_root / "events.jsonl",
        manifest_path=trace_root / "manifest.json",
    )
    dag = build_request_dag(bundle)
    result = write_analysis_artifacts(bundle, dag, tmp_path / "postmortem")

    assert len(bundle.requests) == 193
    assert dag.oracle_evaluable is False
    assert result["opportunity"]["decision"]["decision"] == (
        "STOP_ORACLE_INSUFFICIENT_OBSERVABILITY"
    )
    assert result["opportunity"]["actual"]["makespan_ns"] == 595_158_084_737
    assert result["opportunity"]["actual"]["replay_status"] == (
        "NOT_EVALUABLE_DEPENDENCY_DAG_INCOMPLETE"
    )
    assert result["opportunity"]["publication_critical_oracle"]["status"] == (
        "NOT_EVALUABLE"
    )
