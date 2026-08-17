from __future__ import annotations

import copy

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.native_baseline_n3 import reduce_native_baseline_n3
from paper_eval.native_baseline_runner import (
    DEVELOPMENT_HISTORIES,
    build_native_baseline_plan,
    make_checkpoint,
    seal_history_result,
)
from paper_eval.unified_observability import (
    ObservabilityIdentity,
    aggregate_history_metrics,
    derive_episode_metrics,
    project_operation_views,
)


RUN_ID = "nb-20260816-n3test"


def _identity(history_id: str, sequence: int) -> dict:
    return {
        "run_id": RUN_ID,
        "history_id": history_id,
        "question_id": history_id,
        "episode_id": f"{history_id}:{sequence}",
        "source_sequence": sequence,
        "method": "U0",
        "repeat_id": 0,
    }


def _raw_rows(index: int, *, graph_work: bool = True, system_work: bool = True) -> dict:
    history_id = DEVELOPMENT_HISTORIES[index]
    streams = {
        name: []
        for name in (
            "spans",
            "events",
            "llm",
            "embedding",
            "db",
            "graph_work",
            "queue",
            "per_episode",
        )
    }
    for sequence in range(2):
        identity = _identity(history_id, sequence)
        base = index * 10_000 + sequence * 200
        duration = 100 + sequence * 20 + index
        queue = {
            **identity,
            "stream": "queue",
            "arrival_ts_ns": base,
            "enqueue_ts_ns": base,
            "service_start_ts_ns": base,
            "publication_ts_ns": base + duration,
            "terminal_ts_ns": base + duration + 10,
            "queue_depth_at_enqueue": 0,
            "queue_status": "NOT_APPLICABLE_SERIAL_BASELINE",
        }
        spans = [
            {
                **identity,
                "stream": "spans",
                "sequence": sequence * 10,
                "span_id": f"root-{sequence}",
                "parent_span_id": None,
                "phase": "add-episode",
                "operation_class": "native-update",
                "start_ns": base,
                "end_ns": base + duration,
                "duration_ns": duration,
                "status": "ok",
                "error_code": None,
                "metadata": {},
            }
        ]
        if system_work:
            spans.extend(
                [
                    {
                        **identity,
                        "stream": "spans",
                        "sequence": sequence * 10 + 1,
                        "span_id": f"logical-{sequence}",
                        "parent_span_id": f"root-{sequence}",
                        "phase": "llm",
                        "operation_class": "logical-call",
                        "start_ns": base + 10,
                        "end_ns": base + 50,
                        "duration_ns": 40,
                        "status": "ok",
                        "error_code": None,
                        "metadata": {"input_tokens": 10, "output_tokens": 2},
                    },
                    {
                        **identity,
                        "stream": "spans",
                        "sequence": sequence * 10 + 2,
                        "span_id": f"transport-{sequence}",
                        "parent_span_id": f"logical-{sequence}",
                        "phase": "llm-transport",
                        "operation_class": "request-attempt",
                        "start_ns": base + 10,
                        "end_ns": base + 50,
                        "duration_ns": 40,
                        "status": "ok",
                        "error_code": None,
                        # Transport repeats token accounting.  Canonical work
                        # volume must count tokens from logical calls only.
                        "metadata": {"input_tokens": 10, "output_tokens": 2},
                    },
                    {
                        **identity,
                        "stream": "spans",
                        "sequence": sequence * 10 + 3,
                        "span_id": f"embedding-{sequence}",
                        "parent_span_id": f"root-{sequence}",
                        "phase": "embedding",
                        "operation_class": "create_batch",
                        "start_ns": base + 55,
                        "end_ns": base + 60,
                        "duration_ns": 5,
                        "status": "ok",
                        "error_code": None,
                        "metadata": {"text_count": 2},
                    },
                    {
                        **identity,
                        "stream": "spans",
                        "sequence": sequence * 10 + 4,
                        "span_id": f"db-{sequence}",
                        "parent_span_id": f"root-{sequence}",
                        "phase": "database",
                        "operation_class": "query",
                        "start_ns": base + 65,
                        "end_ns": base + 70,
                        "duration_ns": 5,
                        "status": "ok",
                        "error_code": None,
                        "metadata": {},
                    },
                ]
            )
        graph = {
            **identity,
            "stream": "graph_work",
            "nodes_before": sequence * 2 if graph_work else 0,
            "nodes_after": (sequence + 1) * 2 if graph_work else 0,
            "relationships_before": sequence if graph_work else 0,
            "relationships_after": sequence + 1 if graph_work else 0,
            "semantic_counts_status": "NOT_CAPTURED",
        }
        events = [
            {**identity, "stream": "events", "event_type": "intent", "timestamp_ns": base},
            {**identity, "stream": "events", "event_type": "service_start", "timestamp_ns": base},
            {**identity, "stream": "events", "event_type": "publication", "timestamp_ns": base + duration},
            {
                **identity,
                "stream": "events",
                "event_type": "terminal",
                "timestamp_ns": base + duration + 10,
                "status": "published",
            },
        ]
        views = project_operation_views(spans)
        streams["spans"].extend(spans)
        streams["events"].extend(events)
        for stream in ("llm", "embedding", "db"):
            for span in views[stream]:
                row = dict(span)
                row["stream"] = stream
                streams[stream].append(row)
        streams["graph_work"].append(graph)
        streams["queue"].append(queue)
        streams["per_episode"].append(
            derive_episode_metrics(
                identity=ObservabilityIdentity(**identity),
                spans=spans,
                queue_event=queue,
                graph_work={
                    name: graph[name]
                    for name in (
                        "nodes_before",
                        "nodes_after",
                        "relationships_before",
                        "relationships_after",
                    )
                },
            )
        )
    return streams


def _pair(
    index: int,
    *,
    checkpoint_status: str = "completed",
    quality_status: str = "SUCCESS",
    qa_accuracy: float = 1.0,
    recall_at_10: float = 0.5,
    graph_work: bool = True,
    system_work: bool = True,
) -> dict:
    history = build_native_baseline_plan(RUN_ID).histories[index]
    completed = [0, 1] if checkpoint_status == "completed" else [0]
    checkpoint = make_checkpoint(
        run_id=RUN_ID,
        history_id=history.history_id,
        namespace=history.namespace,
        expected_sequences=[0, 1],
        completed_sequences=completed,
        status=checkpoint_status,
    )
    raw_rows = _raw_rows(index, graph_work=graph_work, system_work=system_work)
    quality_projection = {
        "qa_accuracy": qa_accuracy,
        "evidence_recall_at_10": recall_at_10,
    }
    aggregate = aggregate_history_metrics(
        identity=ObservabilityIdentity(**_identity(history.history_id, 0)),
        episode_metrics=copy.deepcopy(raw_rows["per_episode"]),
        quality=quality_projection,
        serial_baseline=True,
    )
    result = seal_history_result(
        {
            "schema_version": "membind.paper-eval-v3.native-baseline-history.v1",
            "run_id": RUN_ID,
            "history_id": history.history_id,
            "namespace": history.namespace,
            "method": "U0",
            "repeat_id": 0,
            "status": "completed",
            "quality": {
                "status": quality_status,
                "qa_accuracy": qa_accuracy,
                "retrieval": {"evidence_recall_at_10": recall_at_10},
            },
            "aggregate": aggregate,
            "final_namespace_observation": {
                "node_count": 4 if graph_work else 0,
                "relationship_count": 2 if graph_work else 0,
                "episode_count": 2,
                "episode_names_match_expected": True,
            },
        }
    )
    return {
        "checkpoint": checkpoint,
        "history_result": result,
        "raw_rows": raw_rows,
    }


def _complete_pairs(**first_overrides: object) -> list[dict]:
    pairs = [_pair(index) for index in range(len(DEVELOPMENT_HISTORIES))]
    if first_overrides:
        pairs[0] = _pair(0, **first_overrides)
    return pairs


def test_complete_native_screen_recomputes_history_vectors_from_level_zero() -> None:
    report = reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=_complete_pairs())

    assert report["eligibility"] is True
    assert report["decision"] == "HEALTHY_FOR_NEXT_BASELINE"
    assert [row["history_id"] for row in report["per_history"]] == list(
        DEVELOPMENT_HISTORIES
    )
    first = report["per_history"][0]
    assert first["headline_metrics"] == {
        "qa_accuracy": 1.0,
        "evidence_recall_at_10": 0.5,
        "direct_violations": 0,
        "p95_freshness_ns": 120,
        "successful_goodput": pytest.approx(2e9 / 330),
        "makespan_ns": 330,
    }
    # Logical and transport spans both carry token counts. Canonical work
    # volume counts logical tokens once while retaining attempt count.
    assert first["work_volume"]["llm_input_tokens"] == 20
    assert first["work_volume"]["llm_output_tokens"] == 4
    assert first["work_volume"]["llm_logical_calls"] == 2
    assert first["work_volume"]["llm_transport_attempts"] == 2
    assert report["macro_descriptive"]["p95_freshness_ns"] == {
        "history_count": 4,
        "mean": 121.5,
        "median": 121.5,
        "min": 120,
        "max": 123,
    }
    assert report["successful_goodput_unit"] == "episodes_per_second"
    assert report["secondary_metrics"]["max_backlog"] is None
    assert (
        report["secondary_metrics"]["max_backlog_status"]
        == "NOT_APPLICABLE_SERIAL_BASELINE"
    )


def test_partial_inputs_are_ineligible_and_do_not_authorize_decision() -> None:
    report = reduce_native_baseline_n3(
        run_id=RUN_ID,
        history_evidence=_complete_pairs()[:3],
    )
    assert report["eligibility"] is False
    assert report["decision"] is None
    assert report["ineligibility_reasons"] == ["FIXED_FOUR_HISTORY_EVIDENCE_INCOMPLETE"]

    incomplete = _complete_pairs()
    incomplete[0] = _pair(0, checkpoint_status="running")
    report = reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=incomplete)
    assert report["eligibility"] is False
    assert report["decision"] is None
    assert report["ineligibility_reasons"] == ["CHECKPOINT_INCOMPLETE:07741c45"]


def test_wrong_history_order_and_unsealed_or_tampered_result_fail_closed() -> None:
    wrong_order = _complete_pairs()
    wrong_order[0], wrong_order[1] = wrong_order[1], wrong_order[0]
    with pytest.raises(ValueError, match="ordered four histories"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=wrong_order)

    unsealed = _complete_pairs()
    unsealed[0]["history_result"].pop("payload_sha256")
    with pytest.raises(ValueError, match="payload hash"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=unsealed)

    tampered = _complete_pairs()
    tampered[0]["history_result"]["aggregate"]["metrics"]["qa_accuracy"] = 0.0
    with pytest.raises(ValueError, match="payload hash"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=tampered)


def test_source_gap_or_duplicate_singleton_row_fails_closed() -> None:
    gap = _complete_pairs()
    gap[0]["raw_rows"]["per_episode"].pop()
    with pytest.raises(ValueError, match="source coverage"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=gap)

    duplicate = _complete_pairs()
    duplicate[0]["raw_rows"]["queue"].append(
        copy.deepcopy(duplicate[0]["raw_rows"]["queue"][0])
    )
    with pytest.raises(ValueError, match="unique queue row"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=duplicate)


def test_event_queue_timestamp_mismatch_and_graph_discontinuity_fail_closed() -> None:
    mismatch = _complete_pairs()
    mismatch[0]["raw_rows"]["events"][2]["timestamp_ns"] += 1
    with pytest.raises(ValueError, match="event/queue timestamp"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=mismatch)

    discontinuous = _complete_pairs()
    discontinuous[0]["raw_rows"]["graph_work"][1]["nodes_before"] += 1
    with pytest.raises(ValueError, match="graph prefix continuity"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=discontinuous)


def test_fresh_prefix_and_final_namespace_must_match_level_zero() -> None:
    nonempty_start = _complete_pairs()
    nonempty_start[0]["raw_rows"]["graph_work"][0]["nodes_before"] = 1
    with pytest.raises(ValueError, match="fresh graph prefix"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=nonempty_start)

    final_drift = _complete_pairs()
    body = dict(final_drift[0]["history_result"])
    body.pop("payload_sha256")
    body["final_namespace_observation"] = copy.deepcopy(
        body["final_namespace_observation"]
    )
    body["final_namespace_observation"]["node_count"] += 1
    body["payload_sha256"] = payload_sha256(body)
    final_drift[0]["history_result"] = body
    with pytest.raises(ValueError, match="final namespace"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=final_drift)


def test_content_leak_and_sidecar_projection_drift_fail_closed() -> None:
    leaked = _complete_pairs()
    leaked[0]["raw_rows"]["spans"][0]["metadata"]["prompt_text"] = "private"
    with pytest.raises(ValueError, match="content-bearing"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=leaked)

    sidecar_drift = _complete_pairs()
    sidecar_drift[0]["raw_rows"]["llm"].pop()
    with pytest.raises(ValueError, match="sidecar projection"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=sidecar_drift)


def test_common_identity_lifecycle_and_single_root_contracts_fail_closed() -> None:
    identity_drift = _complete_pairs()
    identity_drift[0]["raw_rows"]["spans"][0]["method"] = "M1"
    with pytest.raises(ValueError, match="common identity"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=identity_drift)

    duplicate_lifecycle = _complete_pairs()
    duplicate_lifecycle[0]["raw_rows"]["events"].append(
        copy.deepcopy(duplicate_lifecycle[0]["raw_rows"]["events"][0])
    )
    with pytest.raises(ValueError, match="lifecycle events are not unique"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=duplicate_lifecycle)

    duplicate_root = _complete_pairs()
    root = copy.deepcopy(duplicate_root[0]["raw_rows"]["spans"][0])
    root["span_id"] = "second-root"
    root["sequence"] = 99
    duplicate_root[0]["raw_rows"]["spans"].append(root)
    with pytest.raises(ValueError, match="exactly one add-episode root"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=duplicate_root)


def test_durable_per_episode_or_history_aggregate_is_only_a_cross_check() -> None:
    episode_drift = _complete_pairs()
    episode_drift[0]["raw_rows"]["per_episode"][0]["latency_ns"]["service"] += 1
    with pytest.raises(ValueError, match="durable per_episode"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=episode_drift)

    aggregate_drift = _complete_pairs()
    body = dict(aggregate_drift[0]["history_result"])
    body.pop("payload_sha256")
    body["aggregate"] = copy.deepcopy(body["aggregate"])
    body["aggregate"]["metrics"]["direct_violations"] = 1
    body["payload_sha256"] = payload_sha256(body)
    aggregate_drift[0]["history_result"] = body
    with pytest.raises(ValueError, match="durable history aggregate"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=aggregate_drift)


def test_wrong_goodput_unit_or_serial_backlog_contract_fails_closed() -> None:
    wrong_unit = _complete_pairs()
    body = dict(wrong_unit[0]["history_result"])
    body.pop("payload_sha256")
    body["aggregate"] = copy.deepcopy(body["aggregate"])
    body["aggregate"]["metrics"]["successful_goodput_unit"] = "histories_per_second"
    body["payload_sha256"] = payload_sha256(body)
    wrong_unit[0]["history_result"] = body
    with pytest.raises(ValueError, match="episodes_per_second"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=wrong_unit)

    numeric_backlog = _complete_pairs()
    body = dict(numeric_backlog[0]["history_result"])
    body.pop("payload_sha256")
    body["aggregate"] = copy.deepcopy(body["aggregate"])
    body["aggregate"]["metrics"]["max_backlog"] = 0
    body["payload_sha256"] = payload_sha256(body)
    numeric_backlog[0]["history_result"] = body
    with pytest.raises(ValueError, match="serial max_backlog"):
        reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=numeric_backlog)


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"quality_status": "FAILED"}, "QUALITY_NOT_SUCCESS"),
        ({"graph_work": False}, "GRAPH_WORK_EMPTY"),
        ({"system_work": False}, "SYSTEM_WORK_EMPTY"),
    ],
)
def test_completed_but_unhealthy_history_yields_diagnose(
    overrides: dict, reason: str
) -> None:
    report = reduce_native_baseline_n3(
        run_id=RUN_ID,
        history_evidence=_complete_pairs(**overrides),
    )
    assert report["eligibility"] is True
    assert report["decision"] == "DIAGNOSE_BEFORE_METHODS"
    assert any(item.startswith(f"{reason}:") for item in report["decision_reasons"])


def test_all_zero_quality_signals_yield_diagnose_but_one_zero_history_does_not() -> None:
    all_zero = [
        _pair(index, qa_accuracy=0.0, recall_at_10=0.0)
        for index in range(len(DEVELOPMENT_HISTORIES))
    ]
    report = reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=all_zero)
    assert report["decision"] == "DIAGNOSE_BEFORE_METHODS"
    assert "ALL_QA_ACCURACY_ZERO" in report["decision_reasons"]
    assert "ALL_EVIDENCE_RECALL_AT_10_ZERO" in report["decision_reasons"]

    one_zero = _complete_pairs(qa_accuracy=0.0, recall_at_10=0.0)
    report = reduce_native_baseline_n3(run_id=RUN_ID, history_evidence=one_zero)
    assert report["decision"] == "HEALTHY_FOR_NEXT_BASELINE"
