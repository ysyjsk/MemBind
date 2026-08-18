"""TDD for the sealed v3.1 development reducer and its exact output surface."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from paper_eval.apc_aligned_baseline import build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.baseline_acceptance import ACCEPTANCE_SCHEMA
from paper_eval.membind_v31.method_plan import build_membind_v31_method_plan
from paper_eval.membind_v31.reducer import (
    DevelopmentReducerError,
    reduce_development_results,
    write_development_outputs,
)
from paper_eval.membind_v31.workload_complexity import WORKLOAD_COMPLEXITY_SCHEMA


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
COUNTS = {"07741c45": 49, "b6019101": 49, "6071bd76": 46, "a2f3aa27": 44}
BASELINE_METHODS = ("U0-aligned", "A0-aligned", "P(C=2)-aligned")
QUALITY_METHOD = {
    "U0-aligned": "U0",
    "A0-aligned": "A0",
    "P(C=2)-aligned": "P(C=2)",
    "MemBind": "MemBind",
}


def _seal(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _seal_checkpoint(value: dict[str, object]) -> dict[str, object]:
    result = deepcopy(value)
    result["checkpoint_sha256"] = payload_sha256(result)
    return result


def _baseline_result(block: dict[str, object]) -> dict[str, object]:
    count = int(block["source_count"])
    index = int(block["block_index"])
    makespan = count * 1_000_000_000 + index
    return _seal(
        {
            "schema_version": "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1",
            "status": "PASS",
            "run_id": block["run_id"],
            "block_index": index,
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "episode_count": count,
            "plan_payload_sha256": "PLAN_PLACEHOLDER",
            "performance": {
                "makespan_ns": makespan,
                "max_outstanding_backlog": 2,
                "max_waiting_queue_depth": 1,
                "per_source": [
                    {
                        "source_sequence": sequence,
                        "freshness_ns": 100 + sequence + index,
                        "queue_delay_ns": 10,
                        "service_latency_ns": 90 + sequence + index,
                    }
                    for sequence in range(count)
                ],
            },
            "correctness": {
                "checker_status": "MEASURED",
                "direct_violations_total": 1 if block["method"] == "P(C=2)-aligned" else 0,
                "counts": {
                    "lost_or_missing_source_count": 0,
                    "duplicate_source_or_publication_count": 0,
                    "source_publication_order_violation_count": (
                        1 if block["method"] == "P(C=2)-aligned" else 0
                    ),
                    "visibility_publication_violation_count": 0,
                    "temporal_provenance_hard_violation_count": 0,
                },
            },
        }
    )


def _method_events(count: int, offset: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sequence in range(count):
        arrival = sequence * 1_000_000_000
        rows.extend(
            [
                {
                    "event_type": "ARRIVAL",
                    "source_sequence": sequence,
                    "timestamp_ns": arrival,
                },
                {
                    "event_type": "COMPILE_STARTED",
                    "source_sequence": sequence,
                    "timestamp_ns": arrival + 10,
                },
                {
                    "event_type": "PREPARED_DURABLE",
                    "source_sequence": sequence,
                    "timestamp_ns": arrival + 50,
                },
                {
                    "event_type": "BIND_STARTED",
                    "source_sequence": sequence,
                    "timestamp_ns": arrival + 80,
                },
                {
                    "event_type": "PUBLICATION_DURABLE",
                    "source_sequence": sequence,
                    "timestamp_ns": arrival + 200 + offset + sequence,
                },
            ]
        )
    return rows


def _method_result(block: dict[str, object], plan_sha: str) -> dict[str, object]:
    count = int(block["source_count"])
    index = int(block["block_index"])
    events = _method_events(count, index)
    makespan = int(events[-1]["timestamp_ns"])
    freshness = [200 + index + sequence for sequence in range(count)]

    def nearest(quantile: float) -> int:
        import math

        return sorted(freshness)[max(0, math.ceil(quantile * len(freshness)) - 1)]

    return _seal(
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-live-block-result.v1",
            "status": "PASS",
            "run_id": block["run_id"],
            "block_index": index,
            "method": block["method"],
            "policy": block["policy"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": count,
            "plan_payload_sha256": plan_sha,
            "manifest_sha256": f"{1000 + index:064x}",
            "state_cut_certification_sha256": "c" * 64,
            "execution_identity_sha256": "d" * 64,
            "compile_workers": 2,
            "lookahead": 2,
            "global_llm_admission_k": 2,
            "direct_violation_count": 0,
            "performance": {
                "published_episode_count": count,
                "p50_freshness_ns": nearest(0.50),
                "p95_freshness_ns": nearest(0.95),
                "p99_freshness_ns": nearest(0.99),
                "max_freshness_ns": max(freshness),
                "makespan_ns": makespan,
                "goodput_episodes_per_second": count * 1_000_000_000 / makespan,
            },
            "request_admission": {
                "configured_limit": 2,
                "observed_max_inflight": 2,
                "completed_count": count,
            },
            "checkpoint": _seal_checkpoint(
                {
                    "schema_version": "membind.paper-eval-v3.membind-v31-block-checkpoint.v1",
                    "complete_coverage": True,
                    "completed_source_prefix": count - 1,
                    "terminal_status": "COMPLETED",
                    "resume_status": "NOT_NEEDED_COMPLETE",
                }
            ),
        }
    )


def _fixture() -> dict[str, object]:
    quality_identity = {
        "context_policy_sha256": "5" * 64,
        "judge_config_sha256": "6" * 64,
        "reader_config_sha256": "7" * 64,
        "retrieval_config_sha256": "8" * 64,
    }
    quality_runtime = {"implementation": "quality-v1", "read_only": True}
    sources = {
        history: [f"{cursor + index:064x}" for index in range(COUNTS[history])]
        for cursor, history in zip((1, 101, 201, 301), HISTORIES, strict=True)
    }
    baseline_plan = build_apc_aligned_baseline_plan(
        run_id="apc-baseline-dev-20260817-001",
        history_source_sha256s=sources,
        interarrival_ns=1_000_000_000,
        execution_envelope_sha256="e" * 64,
        service_reference_ns=1_000_000_000,
        normalized_offered_load=1.0,
    )
    baseline_results = [_baseline_result(block) for block in baseline_plan["blocks"]]
    for result in baseline_results:
        result["plan_payload_sha256"] = baseline_plan["payload_sha256"]
        result["payload_sha256"] = payload_sha256(
            {key: value for key, value in result.items() if key != "payload_sha256"}
        )
    semantic_verdicts = {
        "U0-aligned": {"direct_violations": 0, "semantic_status": "SAFE"},
        "A0-aligned": {"direct_violations": 0, "semantic_status": "SAFE"},
        "P(C=2)-aligned": {
            "direct_violations": 4,
            "semantic_status": "VIOLATION_OBSERVED",
        },
    }
    acceptance = _seal(
        {
            "schema_version": ACCEPTANCE_SCHEMA,
            "status": "PASS",
            "artifact_status": "SEALED_VALID",
            "semantic_verdicts": semantic_verdicts,
            "run_id": baseline_plan["run_id"],
            "completed_block_count": 12,
            "terminal_episode_count_per_method": 188,
            "plan_payload_sha256": baseline_plan["payload_sha256"],
            "source_manifest_sha256": baseline_plan["source_manifest_sha256"],
            "arrival_trace_sha256": baseline_plan["arrival_trace_sha256"],
            "shared_execution_envelope_sha256": baseline_plan[
                "shared_execution_envelope_sha256"
            ],
            "global_llm_admission_k": 2,
            "execution_identity_sha256": "a" * 64,
            "block_result_payload_sha256s": [
                result["payload_sha256"] for result in baseline_results
            ],
            "quality_run_id": "quality-baseline-sealed",
            "quality_report_payload_sha256": "b" * 64,
            "quality_identity_sha256": payload_sha256(quality_identity),
            "quality_runtime_identity_sha256": payload_sha256(quality_runtime),
        }
    )
    method_plan = build_membind_v31_method_plan(
        run_id="membind-v31-dev-20260817-001",
        verified_baseline_plan=baseline_plan,
        verified_baseline_acceptance=acceptance,
        methodology_sha256="3" * 64,
        workplan_sha256="4" * 64,
    )
    method_artifacts = [
        {
            "result": _method_result(block, method_plan["payload_sha256"]),
            "events": _method_events(int(block["source_count"]), int(block["block_index"])),
        }
        for block in method_plan["blocks"]
    ]
    result_by_pair = {
        (result["method"], result["history_id"]): result
        for result in baseline_results
    }
    result_by_pair.update(
        {
            (artifact["result"]["method"], artifact["result"]["history_id"]): artifact[
                "result"
            ]
            for artifact in method_artifacts
            if artifact["result"]["method"] == "MemBind"
        }
    )
    quality_rows: list[dict[str, object]] = []
    for method in (*BASELINE_METHODS, "MemBind"):
        quality_method = QUALITY_METHOD[method]
        for history in HISTORIES:
            result = result_by_pair[(method, history)]
            row = _seal(
                {
                    "schema_version": "membind.paper-eval-v3.quality-v1-public.v1",
                    "overlay_run_id": "qev1-shared-dev-001",
                    "method": quality_method,
                    "history_id": history,
                    "namespace_sha256": hashlib.sha256(
                        str(result["namespace"]).encode("utf-8")
                    ).hexdigest(),
                    "construction_result_sha256": result["payload_sha256"],
                    "runtime_identity_sha256": payload_sha256(quality_runtime),
                    "quality_identity": quality_identity,
                    "judge_valid_denominator": 1,
                    "qa_accuracy": 1.0 if history in {"07741c45", "b6019101"} else 0.0,
                    "session_metrics": {"recall_at_10": 0.75},
                }
            )
            quality_rows.append(row)
    summary = {
        "schema_version": "membind.paper-eval-v3.quality-v1-summary.v1",
        "methods": ["U0", "A0", "P(C=2)", "MemBind"],
        "question_count": 16,
        "by_method": {
            method: {
                "question_count": 4,
                "valid_judge_count": 4,
                "qa_accuracy": 0.5,
                "recall_at_10_macro": 0.75,
            }
            for method in ("U0", "A0", "P(C=2)", "MemBind")
        },
    }
    quality_report = _seal(
        {
            "schema_version": "membind.paper-eval-v3.quality-v1-report.v1",
            "status": "PASS",
            "run_id": "qev1-shared-dev-001",
            "summary": summary,
            "quality_identity": quality_identity,
            "runtime_identity": quality_runtime,
            "construction_latency_includes_quality": False,
            "construction_rerun": False,
        }
    )
    workload_rows = {
        history: {
            "episode_count": COUNTS[history],
            "source_turn_count": COUNTS[history] * 2,
            "source_input_token_count": COUNTS[history] * 100,
            "source_input_character_count": COUNTS[history] * 400,
        }
        for history in HISTORIES
    }
    workload_complexity = _seal(
        {
            "schema_version": WORKLOAD_COMPLEXITY_SCHEMA,
            "status": "PASS",
            "methodology_sha256": "3" * 64,
            "workplan_sha256": "4" * 64,
            "source_manifest_sha256": baseline_plan["source_manifest_sha256"],
            "definitions": {
                "source_input_characters": "sum(len(rendered Episode.body))",
                "source_input_tokens": (
                    "sum(Qwen tokenizer encode(rendered Episode.body, "
                    "add_special_tokens=False))"
                ),
                "source_turn": "one raw message in each frozen LongMemEval session",
            },
            "histories": workload_rows,
            "totals": {
                field: sum(row[field] for row in workload_rows.values())
                for field in (
                    "episode_count",
                    "source_turn_count",
                    "source_input_token_count",
                    "source_input_character_count",
                )
            },
            "raw_content_persisted": False,
            "token_ids_persisted": False,
        }
    )
    return {
        "baseline_plan": baseline_plan,
        "baseline_acceptance": acceptance,
        "baseline_results": baseline_results,
        "method_plan": method_plan,
        "method_artifacts": method_artifacts,
        "quality_report": quality_report,
        "quality_rows": quality_rows,
        "workload_complexity": workload_complexity,
    }


def _reduce(fixture: dict[str, object]) -> dict[str, object]:
    return reduce_development_results(
        table_run_id="main-table-v31-dev-001",
        baseline_acceptance=fixture["baseline_acceptance"],
        baseline_results=fixture["baseline_results"],
        method_plan=fixture["method_plan"],
        method_artifacts=fixture["method_artifacts"],
        quality_report=fixture["quality_report"],
        quality_rows=fixture["quality_rows"],
        workload_complexity=fixture["workload_complexity"],
    )


def test_reducer_builds_exact_main_and_mechanism_tables_from_sealed_inputs() -> None:
    reduced = _reduce(_fixture())

    assert set(reduced) == {
        "INPUT_BINDINGS.json",
        "PER_HISTORY_RESULTS.jsonl",
        "MECHANISM_ABLATION.json",
        "DEVELOPMENT_MAIN_TABLE.json",
        "DEVELOPMENT_MAIN_TABLE.csv",
        "DEVELOPMENT_MAIN_TABLE.md",
        "EXPERIMENT_REPORT.md",
    }
    rows = reduced["DEVELOPMENT_MAIN_TABLE.json"]["rows"]
    assert [row["method"] for row in rows] == [
        "U0-aligned",
        "A0-aligned",
        "P(C=2)-aligned",
        "MemBind",
    ]
    assert all(row["episode_count"] == 188 for row in rows)
    assert rows[2]["direct_violations"] == 4
    assert rows[3]["direct_violations"] == 0
    assert rows[3]["qa_accuracy"] == 0.5
    assert rows[3]["evidence_recall_at_10"] == 0.75
    assert rows[3]["source_turn_count"] == 376
    assert rows[3]["source_input_token_count"] == 18_800
    assert rows[3]["source_turns_per_second"] > 0
    assert rows[3]["source_input_tokens_per_second"] > 0
    paired = reduced["DEVELOPMENT_MAIN_TABLE.json"]["paired_history_analysis"]
    assert paired["significance_test"] == "NOT_PERFORMED_DEVELOPMENT_N4"
    assert set(paired["by_method"]["MemBind"]["makespan_speedup_vs_u0"]["values"]) == set(
        HISTORIES
    )
    assert paired["by_method"]["MemBind"]["makespan_speedup_vs_u0"][
        "geometric_mean"
    ] > 0
    per_history = reduced["PER_HISTORY_RESULTS.jsonl"]
    membind_077 = next(
        row
        for row in per_history
        if row["method"] == "MemBind" and row["history_id"] == "07741c45"
    )
    assert membind_077["source_turn_count"] == 98
    assert membind_077["source_input_token_count"] == 4_900
    assert membind_077["makespan_speedup_vs_u0"] > 0
    assert membind_077["goodput_ratio_vs_u0"] > 0
    assert isinstance(membind_077["p95_freshness_reduction_fraction_vs_u0"], float)
    mechanism = reduced["MECHANISM_ABLATION.json"]
    assert [row["method"] for row in mechanism["rows"]] == [
        "MemBind-Barrier",
        "MemBind-FIFO",
        "MemBind",
    ]
    assert mechanism["history_id"] == "07741c45"
    assert all(row["frontier_wait_p95_ns"] == 30 for row in mechanism["rows"])
    assert all(row["safe_work_fraction"] > 0 for row in mechanism["rows"])
    assert all(row["lifecycle_diagnostics_status"] == "MEASURED" for row in mechanism["rows"])


def test_reducer_fails_closed_on_hash_incomplete_or_quality_binding_drift() -> None:
    fixture = _fixture()
    fixture["baseline_acceptance"]["payload_sha256"] = "f" * 64
    with pytest.raises(DevelopmentReducerError, match="baseline acceptance hash mismatch"):
        _reduce(fixture)

    fixture = _fixture()
    artifact = fixture["method_artifacts"][0]
    artifact["result"]["checkpoint"]["terminal_status"] = "RUNNING"
    artifact["result"]["checkpoint"]["checkpoint_sha256"] = payload_sha256(
        {
            key: value
            for key, value in artifact["result"]["checkpoint"].items()
            if key != "checkpoint_sha256"
        }
    )
    artifact["result"]["payload_sha256"] = payload_sha256(
        {key: value for key, value in artifact["result"].items() if key != "payload_sha256"}
    )
    with pytest.raises(DevelopmentReducerError, match="method block incomplete"):
        _reduce(fixture)

    fixture = _fixture()
    fixture["quality_rows"][0]["construction_result_sha256"] = "f" * 64
    fixture["quality_rows"][0]["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in fixture["quality_rows"][0].items()
            if key != "payload_sha256"
        }
    )
    with pytest.raises(DevelopmentReducerError, match="quality construction binding"):
        _reduce(fixture)


def test_reducer_fails_closed_on_semantic_or_workload_binding_drift() -> None:
    fixture = _fixture()
    fixture["baseline_acceptance"]["semantic_verdicts"]["P(C=2)-aligned"] = {
        "direct_violations": 0,
        "semantic_status": "SAFE",
    }
    fixture["baseline_acceptance"]["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in fixture["baseline_acceptance"].items()
            if key != "payload_sha256"
        }
    )
    fixture["method_plan"] = build_membind_v31_method_plan(
        run_id="membind-v31-dev-20260817-001",
        verified_baseline_plan=fixture["baseline_plan"],
        verified_baseline_acceptance=fixture["baseline_acceptance"],
        methodology_sha256="3" * 64,
        workplan_sha256="4" * 64,
    )
    with pytest.raises(
        DevelopmentReducerError, match="baseline semantic verdict result mismatch"
    ):
        _reduce(fixture)

    fixture = _fixture()
    fixture["workload_complexity"]["histories"]["07741c45"][
        "source_input_token_count"
    ] += 1
    fixture["workload_complexity"]["payload_sha256"] = payload_sha256(
        {
            key: value
            for key, value in fixture["workload_complexity"].items()
            if key != "payload_sha256"
        }
    )
    with pytest.raises(DevelopmentReducerError, match="workload complexity totals drift"):
        _reduce(fixture)


def test_writer_creates_only_the_frozen_output_inventory(tmp_path: Path) -> None:
    output = tmp_path / "main-table-v31-dev-001"
    reduced = _reduce(_fixture())

    write_development_outputs(output, reduced)

    assert sorted(path.name for path in output.iterdir()) == sorted(reduced)
    assert (output / "PER_HISTORY_RESULTS.jsonl").read_text(encoding="utf-8").count("\n") == 18
    with pytest.raises(DevelopmentReducerError, match="output root exists"):
        write_development_outputs(output, reduced)
