from __future__ import annotations

import pytest

from paper_eval.unified_observability import (
    OBSERVABILITY_SCHEMA_VERSION,
    PRIMARY_METRICS,
    SECONDARY_METRICS,
    ObservabilityIdentity,
    aggregate_history_metrics,
    derive_queue_metrics,
    derive_episode_metrics,
    project_operation_views,
    validate_attempt_outcomes,
    validate_observability_record,
    validate_raw_quality_evidence,
)


def _span(
    *,
    span_id: str,
    phase: str,
    start: int,
    end: int,
    parent: str | None = None,
    operation_class: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "phase": phase,
        "start_ns": start,
        "end_ns": end,
        "status": "ok",
        "operation_class": operation_class,
        "metadata": metadata or {},
    }


def _identity() -> ObservabilityIdentity:
    return ObservabilityIdentity(
        run_id="native-baseline-001",
        history_id="07741c45",
        question_id="07741c45",
        episode_id="07741c45:0",
        source_sequence=0,
        method="U0",
        repeat_id=0,
    )


def test_contract_exposes_frozen_metric_tiers() -> None:
    assert OBSERVABILITY_SCHEMA_VERSION.endswith(".v1")
    assert PRIMARY_METRICS == (
        "qa_accuracy",
        "evidence_recall_at_10",
        "direct_violations",
        "p95_freshness_ns",
        "successful_goodput",
        "makespan_ns",
    )
    assert SECONDARY_METRICS == ("p99_freshness_ns", "max_backlog")


def test_episode_reducer_derives_queue_service_freshness_and_phase_percentiles() -> None:
    identity = _identity()
    spans = [
        _span(span_id="root", phase="add-episode", start=10, end=110),
        _span(
            span_id="llm",
            phase="llm",
            start=20,
            end=80,
            parent="root",
            operation_class="logical-call",
            metadata={"prompt_name": "extract_nodes", "input_tokens": 12, "output_tokens": 3},
        ),
        _span(
            span_id="db",
            phase="database",
            start=85,
            end=100,
            parent="root",
            operation_class="query",
        ),
    ]
    queue_events = {
        "arrival_ts_ns": 0,
        "enqueue_ts_ns": 1,
        "service_start_ts_ns": 10,
        "publication_ts_ns": 110,
        "terminal_ts_ns": 120,
        "queue_depth_at_enqueue": 0,
    }
    result = derive_episode_metrics(
        identity=identity,
        spans=spans,
        queue_event=queue_events,
        graph_work={"nodes_before": 0, "nodes_after": 2},
    )
    assert result["identity"]["method"] == "U0"
    assert result["latency_ns"] == {
        "queue_delay": 10,
        "service": 100,
        "freshness": 110,
        "terminal": 120,
    }
    assert result["phase_metrics"]["llm"]["duration_ns"] == 60
    assert result["phase_metrics"]["llm"]["input_tokens"] == 12
    assert result["graph_work"]["node_delta"] == 2


def test_history_aggregator_derives_p99_backlog_and_goodput_without_summing_nested_phases() -> None:
    identity = _identity()
    rows = []
    for sequence, (arrival, publication, terminal) in enumerate(
        [(0, 10, 12), (20, 50, 52), (60, 90, 92)]
    ):
        item = derive_episode_metrics(
            identity=ObservabilityIdentity(
                run_id="native-baseline-001",
                history_id="07741c45",
                question_id="07741c45",
                episode_id=f"07741c45:{sequence}",
                source_sequence=sequence,
                method="U0",
                repeat_id=0,
            ),
            spans=[
                _span(span_id=f"r{sequence}", phase="add-episode", start=arrival, end=publication)
            ],
            queue_event={
                "arrival_ts_ns": arrival,
                "enqueue_ts_ns": arrival,
                "service_start_ts_ns": arrival,
                "publication_ts_ns": publication,
                "terminal_ts_ns": terminal,
                "queue_depth_at_enqueue": sequence,
            },
            graph_work={},
        )
        rows.append(item)
    aggregate = aggregate_history_metrics(
        identity=identity,
        episode_metrics=rows,
        quality={"qa_accuracy": 1.0, "evidence_recall_at_10": 0.5},
    )
    assert aggregate["metrics"]["successful_goodput"] == pytest.approx(3e9 / 92)
    assert aggregate["metrics"]["successful_goodput_unit"] == "episodes_per_second"
    assert aggregate["metrics"]["max_backlog"] == 2
    assert aggregate["metrics"]["p99_freshness_ns"] == 30
    assert aggregate["quality"]["qa_accuracy"] == 1.0


def test_serial_history_aggregate_does_not_report_zero_backlog_and_derives_tail_amplification() -> None:
    identity = _identity()
    rows = []
    for sequence, publication in enumerate((10, 20, 30, 40)):
        rows.append(
            derive_episode_metrics(
                identity=ObservabilityIdentity(
                    run_id="native-baseline-001",
                    history_id="07741c45",
                    question_id="07741c45",
                    episode_id=f"07741c45:{sequence}",
                    source_sequence=sequence,
                    method="U0",
                    repeat_id=0,
                ),
                spans=[
                    _span(
                        span_id=f"serial-{sequence}",
                        phase="add-episode",
                        start=sequence * 50,
                        end=publication + sequence * 50,
                    )
                ],
                queue_event={
                    "arrival_ts_ns": sequence * 50,
                    "enqueue_ts_ns": sequence * 50,
                    "service_start_ts_ns": sequence * 50,
                    "publication_ts_ns": sequence * 50 + publication,
                    "terminal_ts_ns": sequence * 50 + publication,
                    "queue_depth_at_enqueue": 0,
                },
            )
        )

    aggregate = aggregate_history_metrics(
        identity=identity,
        episode_metrics=rows,
        serial_baseline=True,
    )

    assert aggregate["metrics"]["max_backlog"] is None
    assert aggregate["metrics"]["max_backlog_status"] == "NOT_APPLICABLE_SERIAL_BASELINE"
    # Nearest-rank P50=20 and P99=40 for this four-value fixture.
    assert aggregate["latency_distributions"]["freshness_ns"]["tail_amplification"] == 2.0


def test_history_work_volume_does_not_double_count_transport_or_candidate_embedding() -> None:
    identity = _identity()
    row = derive_episode_metrics(
        identity=identity,
        spans=[
            _span(span_id="root", phase="add-episode", start=0, end=100),
            _span(
                span_id="logical",
                phase="llm",
                start=10,
                end=50,
                parent="root",
                operation_class="logical-call",
                metadata={"input_tokens": 100, "output_tokens": 10},
            ),
            _span(
                span_id="attempt",
                phase="llm-transport",
                start=10,
                end=50,
                parent="logical",
                operation_class="request-attempt",
                metadata={"input_tokens": 100, "output_tokens": 10},
            ),
            _span(
                span_id="embedding",
                phase="embedding",
                start=55,
                end=60,
                parent="root",
                operation_class="create_batch",
                metadata={"text_count": 2},
            ),
            _span(
                span_id="candidate-embedding",
                phase="candidate-embedding",
                start=65,
                end=70,
                parent="root",
                operation_class="semantic-work",
                metadata={"text_count": 2},
            ),
        ],
        queue_event={
            "arrival_ts_ns": 0,
            "enqueue_ts_ns": 0,
            "service_start_ts_ns": 0,
            "publication_ts_ns": 100,
            "terminal_ts_ns": 100,
            "queue_depth_at_enqueue": 0,
        },
    )
    aggregate = aggregate_history_metrics(
        identity=identity,
        episode_metrics=[row],
        serial_baseline=True,
    )

    assert aggregate["work_volume"] == {
        "llm_logical_calls": 1,
        "llm_transport_attempts": 1,
        "llm_input_tokens": 100,
        "llm_output_tokens": 10,
        "embedding_calls": 1,
        "embedding_items": 2,
        "candidate_embedding_spans": 1,
        "candidate_embedding_items": 2,
    }


def test_raw_quality_evidence_rejects_prompt_or_answer_content() -> None:
    with pytest.raises(ValueError, match="content"):
        validate_raw_quality_evidence(
            {"question": "private question", "judge_label": True}
        )


def test_content_safe_contract_allows_prompt_label_but_rejects_prompt_text() -> None:
    identity = _identity()
    record = validate_observability_record(
        {
            **identity.to_dict(),
            "stream": "llm",
            "phase": "node-extraction",
            "metadata": {"prompt_name": "extract_nodes", "input_tokens": 4},
        }
    )
    assert record["metadata"]["prompt_name"] == "extract_nodes"
    with pytest.raises(ValueError, match="content"):
        validate_observability_record(
            {
                **identity.to_dict(),
                "stream": "llm",
                "metadata": {"prompt_text": "private"},
            }
        )


def test_queue_reducer_reports_area_and_does_not_infer_serial_capacity() -> None:
    observed = derive_queue_metrics(
        [
            {"timestamp_ns": 0, "queue_depth": 0},
            {"timestamp_ns": 10, "queue_depth": 2},
            {"timestamp_ns": 20, "queue_depth": 1},
        ]
    )
    # Each sample describes the depth from its timestamp until the next state
    # change: 0*10 + 2*10.
    assert observed["queue_area_ns_items"] == 20
    assert observed["max_backlog"] == 2
    assert observed["p95_backlog"] == 2
    serial = derive_queue_metrics([], serial_baseline=True)
    assert serial["status"] == "NOT_APPLICABLE_SERIAL_BASELINE"
    assert serial["max_backlog"] is None


def test_operation_views_are_deterministic_and_attempt_outcomes_are_exact() -> None:
    spans = [
        _span(span_id="root", phase="add-episode", start=0, end=10),
        _span(span_id="llm", phase="llm", start=1, end=2),
        _span(span_id="embed", phase="embedding", start=2, end=3),
        _span(span_id="db", phase="database", start=3, end=4),
        _span(span_id="err", phase="llm-transport", start=4, end=5),
    ]
    views = project_operation_views(spans)
    assert [row["span_id"] for row in views["llm"]] == ["llm", "err"]
    assert [row["span_id"] for row in views["embedding"]] == ["embed"]
    assert [row["span_id"] for row in views["db"]] == ["db"]
    accounting = validate_attempt_outcomes(
        expected_sequences=[0, 1, 2],
        outcomes=[
            {"source_sequence": 0, "status": "published"},
            {"source_sequence": 1, "status": "failed"},
            {"source_sequence": 2, "status": "censored"},
        ],
    )
    assert accounting == {
        "expected": 3,
        "published": 1,
        "failed": 1,
        "censored": 1,
    }


def test_lifecycle_requires_enqueue_and_monotonic_timestamps() -> None:
    identity = _identity()
    spans = [_span(span_id="root", phase="add-episode", start=1, end=5)]
    with pytest.raises(ValueError, match="monotonic"):
        derive_episode_metrics(
            identity=identity,
            spans=spans,
            queue_event={
                "arrival_ts_ns": 4,
                "enqueue_ts_ns": 3,
                "service_start_ts_ns": 5,
                "publication_ts_ns": 6,
                "terminal_ts_ns": 7,
                "queue_depth_at_enqueue": 0,
            },
        )


def test_identity_rejects_mismatched_history_and_question() -> None:
    with pytest.raises(ValueError, match="question_id"):
        ObservabilityIdentity(
            run_id="r",
            history_id="h",
            question_id="other",
            episode_id="h:0",
            source_sequence=0,
            method="U0",
            repeat_id=0,
        )
