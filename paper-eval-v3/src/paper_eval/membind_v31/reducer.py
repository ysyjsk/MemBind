"""Fail-closed offline reduction for the v3.1 development main table.

The reducer consumes only sealed construction and Quality v1 artifacts.  It
does not import a model client, graph driver, or live runner.  Presentation
files are deterministic projections of the sealed JSON result.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import tempfile
from statistics import median
from collections.abc import Mapping, Sequence
from copy import deepcopy
from io import StringIO
from pathlib import Path
from typing import Any

from paper_eval.apc_aligned_baseline import APC_BASELINE_HISTORIES, APC_BASELINE_METHODS
from paper_eval.artifacts import append_jsonl_durable, atomic_write_json, payload_sha256
from paper_eval.membind_v31.artifacts import inspect_v31_block
from paper_eval.membind_v31.baseline_acceptance import ACCEPTANCE_SCHEMA
from paper_eval.membind_v31.method_plan import (
    MEMBIND_V31_METHODS,
    REPRESENTATIVE_HISTORY,
    verify_membind_v31_method_plan,
)
from paper_eval.membind_v31.workload_complexity import WORKLOAD_COMPLEXITY_SCHEMA


INPUT_SCHEMA = "membind.paper-eval-v3.membind-v31-input-bindings.v1"
PER_HISTORY_SCHEMA = "membind.paper-eval-v3.membind-v31-per-history.v1"
MECHANISM_SCHEMA = "membind.paper-eval-v3.membind-v31-mechanism-ablation.v1"
MAIN_TABLE_SCHEMA = "membind.paper-eval-v3.membind-v31-development-main-table.v1"
QUALITY_REPORT_SCHEMA = "membind.paper-eval-v3.quality-v1-report.v1"
BASELINE_RESULT_SCHEMA = "membind.paper-eval-v3.apc-aligned-baseline-block-result.v1"
METHOD_RESULT_SCHEMA = "membind.paper-eval-v3.membind-v31-live-block-result.v1"
OUTPUT_FILES = (
    "INPUT_BINDINGS.json",
    "PER_HISTORY_RESULTS.jsonl",
    "MECHANISM_ABLATION.json",
    "DEVELOPMENT_MAIN_TABLE.json",
    "DEVELOPMENT_MAIN_TABLE.csv",
    "DEVELOPMENT_MAIN_TABLE.md",
    "EXPERIMENT_REPORT.md",
)
HEADLINE_METHODS = (*APC_BASELINE_METHODS, "MemBind")
QUALITY_NAMES = {
    "U0-aligned": "U0",
    "A0-aligned": "A0",
    "P(C=2)-aligned": "P(C=2)",
    "MemBind": "MemBind",
}
EXPECTED_HISTORY_COUNTS = {
    "07741c45": 49,
    "b6019101": 49,
    "6071bd76": 46,
    "a2f3aa27": 44,
}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DevelopmentReducerError(ValueError):
    """A reducer input or exact-output invariant failed."""


def _fail(code: str) -> DevelopmentReducerError:
    return DevelopmentReducerError(code)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _int(value: object, code: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(code)
    return value


def _number(value: object, code: str, *, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise _fail(code)
    return float(value)


def _sealed(value: Mapping[str, object], *, label: str, field: str = "payload_sha256") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label} invalid")
    selected = deepcopy(dict(value))
    stored = _sha(selected.get(field), f"{label} hash invalid")
    body = {key: child for key, child in selected.items() if key != field}
    if stored != payload_sha256(body):
        raise _fail(f"{label} hash mismatch")
    return selected


def _seal(body: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(body))
    selected["payload_sha256"] = payload_sha256(selected)
    return selected


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _nearest(values: Sequence[int], quantile: float) -> int:
    if not values:
        raise _fail("freshness inventory empty")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _performance(freshness: Sequence[int], makespan_ns: int) -> dict[str, object]:
    values = [_int(value, "freshness invalid") for value in freshness]
    span = _int(makespan_ns, "makespan invalid", minimum=1)
    return {
        "episode_count": len(values),
        "p50_freshness_ns": _nearest(values, 0.50),
        "p90_freshness_ns": _nearest(values, 0.90),
        "p95_freshness_ns": _nearest(values, 0.95),
        "p99_freshness_ns": _nearest(values, 0.99),
        "max_freshness_ns": max(values),
        "successful_goodput_episodes_per_second": len(values) * 1_000_000_000 / span,
        "makespan_ns": span,
    }


def _verify_acceptance(value: Mapping[str, object]) -> dict[str, Any]:
    accepted = _sealed(value, label="baseline acceptance")
    if (
        accepted.get("schema_version") != ACCEPTANCE_SCHEMA
        or accepted.get("status") != "PASS"
        or accepted.get("artifact_status") != "SEALED_VALID"
        or accepted.get("completed_block_count") != 12
        or accepted.get("terminal_episode_count_per_method") != 188
        or accepted.get("global_llm_admission_k") != 2
    ):
        raise _fail("baseline acceptance incomplete")
    verdicts = accepted.get("semantic_verdicts")
    if not isinstance(verdicts, Mapping) or set(verdicts) != set(APC_BASELINE_METHODS):
        raise _fail("baseline semantic verdict inventory invalid")
    for method in APC_BASELINE_METHODS:
        verdict = verdicts.get(method)
        if not isinstance(verdict, Mapping):
            raise _fail("baseline semantic verdict invalid")
        violations = _int(
            verdict.get("direct_violations"), "baseline semantic verdict invalid"
        )
        expected_status = "SAFE" if violations == 0 else "VIOLATION_OBSERVED"
        if verdict.get("semantic_status") != expected_status:
            raise _fail("baseline semantic verdict invalid")
    hashes = accepted.get("block_result_payload_sha256s")
    if (
        not isinstance(hashes, list)
        or len(hashes) != 12
        or len(set(hashes)) != 12
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in hashes)
    ):
        raise _fail("baseline acceptance result inventory invalid")
    for field in (
        "plan_payload_sha256",
        "source_manifest_sha256",
        "arrival_trace_sha256",
        "shared_execution_envelope_sha256",
        "execution_identity_sha256",
        "quality_report_payload_sha256",
        "quality_identity_sha256",
        "quality_runtime_identity_sha256",
    ):
        _sha(accepted.get(field), f"baseline acceptance {field} invalid")
    return accepted


def _baseline_rows(
    results: Sequence[Mapping[str, object]], accepted: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, str]]]:
    if isinstance(results, (str, bytes)) or not isinstance(results, Sequence) or len(results) != 12:
        raise _fail("baseline result inventory incomplete")
    rows: list[dict[str, Any]] = []
    construction_bindings: dict[tuple[str, str], dict[str, str]] = {}
    expected_hashes = accepted["block_result_payload_sha256s"]
    observed_pairs: set[tuple[str, str]] = set()
    namespaces: set[str] = set()
    for expected_index, raw in enumerate(results):
        result = _sealed(raw, label=f"baseline block {expected_index} result")
        if result["payload_sha256"] != expected_hashes[expected_index]:
            raise _fail("baseline block result acceptance hash mismatch")
        if (
            result.get("schema_version") != BASELINE_RESULT_SCHEMA
            or result.get("status") != "PASS"
            or result.get("run_id") != accepted.get("run_id")
            or result.get("block_index") != expected_index
            or result.get("plan_payload_sha256") != accepted.get("plan_payload_sha256")
        ):
            raise _fail("baseline block identity invalid")
        method = result.get("method")
        history = result.get("history_id")
        namespace = result.get("namespace")
        if (
            method not in APC_BASELINE_METHODS
            or history not in APC_BASELINE_HISTORIES
            or not isinstance(namespace, str)
            or not namespace
            or namespace in namespaces
            or (method, history) in observed_pairs
        ):
            raise _fail("baseline block inventory invalid")
        observed_pairs.add((str(method), str(history)))
        namespaces.add(namespace)
        count = _int(result.get("episode_count"), "baseline episode count invalid", minimum=1)
        if count != EXPECTED_HISTORY_COUNTS[str(history)]:
            raise _fail("baseline episode coverage invalid")
        perf = result.get("performance")
        if not isinstance(perf, Mapping):
            raise _fail("baseline performance invalid")
        per_source = perf.get("per_source")
        if isinstance(per_source, (str, bytes)) or not isinstance(per_source, Sequence) or len(per_source) != count:
            raise _fail("baseline per-source performance incomplete")
        freshness: list[int] = []
        sequences: list[int] = []
        for source in per_source:
            if not isinstance(source, Mapping):
                raise _fail("baseline per-source performance invalid")
            sequences.append(_int(source.get("source_sequence"), "baseline source sequence invalid"))
            freshness.append(_int(source.get("freshness_ns"), "baseline freshness invalid"))
        if sequences != list(range(count)):
            raise _fail("baseline source coverage invalid")
        makespan = _int(perf.get("makespan_ns"), "baseline makespan invalid", minimum=1)
        correctness = result.get("correctness")
        if not isinstance(correctness, Mapping) or correctness.get("checker_status") != "MEASURED":
            raise _fail("baseline correctness incomplete")
        violations = _int(
            correctness.get("direct_violations_total"), "baseline direct violations invalid"
        )
        rows.append(
            {
                "method": method,
                "history_id": history,
                "namespace": namespace,
                "episode_count": count,
                "freshness_ns": freshness,
                "makespan_ns": makespan,
                "direct_violations": violations,
                "construction_result_sha256": result["payload_sha256"],
                "request_admission": None,
                "mechanism_only": False,
            }
        )
        construction_bindings[(QUALITY_NAMES[str(method)], str(history))] = {
            "namespace_sha256": _text_sha256(namespace),
            "result_sha256": result["payload_sha256"],
        }
    expected_pairs = {
        (method, history) for method in APC_BASELINE_METHODS for history in APC_BASELINE_HISTORIES
    }
    if observed_pairs != expected_pairs:
        raise _fail("baseline result inventory incomplete")
    measured_by_method = {
        method: sum(
            int(row["direct_violations"]) for row in rows if row["method"] == method
        )
        for method in APC_BASELINE_METHODS
    }
    for method, measured in measured_by_method.items():
        verdict = accepted["semantic_verdicts"][method]
        if verdict.get("direct_violations") != measured:
            raise _fail("baseline semantic verdict result mismatch")
    return rows, construction_bindings


def _verify_workload_complexity(
    value: Mapping[str, object],
    *,
    accepted: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    workload = _sealed(value, label="workload complexity")
    if (
        workload.get("schema_version") != WORKLOAD_COMPLEXITY_SCHEMA
        or workload.get("status") != "PASS"
        or workload.get("methodology_sha256") != plan.get("methodology_sha256")
        or workload.get("workplan_sha256") != plan.get("workplan_sha256")
        or workload.get("source_manifest_sha256")
        != accepted.get("source_manifest_sha256")
    ):
        raise _fail("workload complexity binding invalid")
    histories = workload.get("histories")
    if not isinstance(histories, Mapping) or list(histories) != list(APC_BASELINE_HISTORIES):
        raise _fail("workload complexity history inventory invalid")
    fields = (
        "episode_count",
        "source_turn_count",
        "source_input_token_count",
        "source_input_character_count",
    )
    normalized: dict[str, dict[str, int]] = {}
    for history in APC_BASELINE_HISTORIES:
        raw = histories.get(history)
        if not isinstance(raw, Mapping):
            raise _fail("workload complexity history invalid")
        selected = {
            field: _int(
                raw.get(field),
                "workload complexity count invalid",
                minimum=1,
            )
            for field in fields
        }
        if selected["episode_count"] != EXPECTED_HISTORY_COUNTS[history]:
            raise _fail("workload complexity episode count mismatch")
        normalized[history] = selected
    totals = workload.get("totals")
    expected_totals = {
        field: sum(row[field] for row in normalized.values()) for field in fields
    }
    if not isinstance(totals, Mapping) or dict(totals) != expected_totals:
        raise _fail("workload complexity totals drift")
    definitions = workload.get("definitions")
    if not isinstance(definitions, Mapping) or definitions.get("source_turn") != (
        "one raw message in each frozen LongMemEval session"
    ) or definitions.get("source_input_tokens") != (
        "sum(Qwen tokenizer encode(rendered Episode.body, add_special_tokens=False))"
    ):
        raise _fail("workload complexity definition invalid")
    content_flag = workload.get(
        "raw_content_persisted", workload.get("content_persisted")
    )
    if content_flag is not False or workload.get("token_ids_persisted", False) is not False:
        raise _fail("workload complexity content safety invalid")
    workload["histories"] = normalized
    return workload


def _events_performance(events: object, *, count: int) -> tuple[list[int], int]:
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise _fail("method lifecycle invalid")
    arrivals: dict[int, int] = {}
    publications: dict[int, int] = {}
    for raw in events:
        if not isinstance(raw, Mapping):
            raise _fail("method lifecycle invalid")
        event_type = raw.get("event_type")
        if event_type not in {"ARRIVAL", "PUBLICATION_DURABLE"}:
            continue
        sequence = _int(raw.get("source_sequence"), "method lifecycle source invalid")
        timestamp = _int(raw.get("timestamp_ns"), "method lifecycle timestamp invalid")
        selected = arrivals if event_type == "ARRIVAL" else publications
        if sequence in selected:
            raise _fail("method lifecycle duplicate")
        selected[sequence] = timestamp
    expected = set(range(count))
    if set(arrivals) != expected or set(publications) != expected:
        raise _fail("method lifecycle incomplete")
    freshness = []
    for sequence in range(count):
        if publications[sequence] < arrivals[sequence]:
            raise _fail("method lifecycle timestamp order invalid")
        freshness.append(publications[sequence] - arrivals[sequence])
    makespan = max(publications.values()) - min(arrivals.values())
    return freshness, _int(makespan, "method makespan invalid", minimum=1)


def _lifecycle_diagnostics(events: object, *, count: int) -> dict[str, object]:
    """Derive only diagnostics supported by complete lifecycle timestamps."""

    required = (
        "ARRIVAL",
        "COMPILE_STARTED",
        "PREPARED_DURABLE",
        "BIND_STARTED",
        "PUBLICATION_DURABLE",
    )
    by_source = {sequence: {} for sequence in range(count)}
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise _fail("method lifecycle invalid")
    for raw in events:
        if not isinstance(raw, Mapping) or raw.get("event_type") not in required:
            continue
        sequence = _int(raw.get("source_sequence"), "method lifecycle source invalid")
        if sequence not in by_source or raw["event_type"] in by_source[sequence]:
            raise _fail("method lifecycle duplicate")
        by_source[sequence][str(raw["event_type"])] = _int(
            raw.get("timestamp_ns"), "method lifecycle timestamp invalid"
        )
    if any(set(value) != set(required) for value in by_source.values()):
        return {
            "lifecycle_diagnostics_status": "NOT_PRESENT_IN_SEALED_LIFECYCLE",
            "frontier_wait_mean_ns": None,
            "frontier_wait_p95_ns": None,
            "safe_work_fraction": None,
        }
    frontier_wait: list[int] = []
    safe_duration = 0
    freshness_duration = 0
    for sequence in range(count):
        value = by_source[sequence]
        ordered = [value[name] for name in required]
        if ordered != sorted(ordered):
            raise _fail("method lifecycle stage order invalid")
        frontier_wait.append(value["BIND_STARTED"] - value["PREPARED_DURABLE"])
        safe_duration += value["PREPARED_DURABLE"] - value["COMPILE_STARTED"]
        freshness_duration += value["PUBLICATION_DURABLE"] - value["ARRIVAL"]
    return {
        "lifecycle_diagnostics_status": "MEASURED",
        "frontier_wait_mean_ns": sum(frontier_wait) / len(frontier_wait),
        "frontier_wait_p95_ns": _nearest(frontier_wait, 0.95),
        "safe_work_fraction": safe_duration / freshness_duration,
    }


def _method_rows(
    artifacts: Sequence[Mapping[str, object]], plan: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, str]]]:
    blocks = plan["blocks"]
    if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence) or len(artifacts) != 6:
        raise _fail("method block inventory incomplete")
    rows: list[dict[str, Any]] = []
    construction_bindings: dict[tuple[str, str], dict[str, str]] = {}
    for index, (artifact, block) in enumerate(zip(artifacts, blocks, strict=True)):
        if not isinstance(artifact, Mapping) or not isinstance(artifact.get("result"), Mapping):
            raise _fail("method block artifact invalid")
        result = _sealed(artifact["result"], label=f"method block {index} result")
        expected = {
            "schema_version": METHOD_RESULT_SCHEMA,
            "status": "PASS",
            "run_id": plan["run_id"],
            "block_index": index,
            "method": block["method"],
            "policy": block["policy"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "source_count": block["source_count"],
            "plan_payload_sha256": plan["payload_sha256"],
            "global_llm_admission_k": 2,
        }
        if any(result.get(key) != value for key, value in expected.items()):
            raise _fail("method block identity invalid")
        checkpoint = result.get("checkpoint")
        if not isinstance(checkpoint, Mapping):
            raise _fail("method block incomplete")
        _sealed(checkpoint, label="method checkpoint", field="checkpoint_sha256")
        if (
            checkpoint.get("terminal_status") != "COMPLETED"
            or checkpoint.get("complete_coverage") is not True
            or checkpoint.get("completed_source_prefix") != int(block["source_count"]) - 1
        ):
            raise _fail("method block incomplete")
        violations = _int(result.get("direct_violation_count"), "method direct violations invalid")
        if violations != 0:
            raise _fail("method direct violation observed")
        count = int(block["source_count"])
        freshness, makespan = _events_performance(artifact.get("events"), count=count)
        reported = result.get("performance")
        expected_performance = {
            "published_episode_count": count,
            "p50_freshness_ns": _nearest(freshness, 0.50),
            "p95_freshness_ns": _nearest(freshness, 0.95),
            "p99_freshness_ns": _nearest(freshness, 0.99),
            "max_freshness_ns": max(freshness),
            "makespan_ns": makespan,
            "goodput_episodes_per_second": count * 1_000_000_000 / makespan,
        }
        if not isinstance(reported, Mapping) or dict(reported) != expected_performance:
            raise _fail("method performance drift")
        admission = result.get("request_admission")
        if not isinstance(admission, Mapping) or admission.get("configured_limit") != 2:
            raise _fail("method request admission invalid")
        observed = _int(admission.get("observed_max_inflight"), "method request admission invalid")
        if observed > 2:
            raise _fail("method request admission exceeds K")
        row = {
            "method": block["method"],
            "history_id": block["history_id"],
            "namespace": block["namespace"],
            "episode_count": count,
            "freshness_ns": freshness,
            "makespan_ns": makespan,
            "direct_violations": violations,
            "construction_result_sha256": result["payload_sha256"],
            "request_admission": deepcopy(dict(admission)),
            "lifecycle_diagnostics": _lifecycle_diagnostics(
                artifact.get("events"), count=count
            ),
            "mechanism_only": block["method"] != "MemBind",
        }
        rows.append(row)
        if block["method"] == "MemBind":
            construction_bindings[("MemBind", str(block["history_id"]))] = {
                "namespace_sha256": _text_sha256(str(block["namespace"])),
                "result_sha256": result["payload_sha256"],
            }
    return rows, construction_bindings


def _quality(
    report_value: Mapping[str, object],
    row_values: Sequence[Mapping[str, object]],
    *,
    accepted: Mapping[str, Any],
    construction_bindings: Mapping[tuple[str, str], Mapping[str, str]],
) -> tuple[dict[str, dict[str, object]], dict[tuple[str, str], dict[str, object]], dict[str, Any]]:
    report = _sealed(report_value, label="quality report")
    if (
        report.get("schema_version") != QUALITY_REPORT_SCHEMA
        or report.get("status") != "PASS"
        or report.get("construction_latency_includes_quality") is not False
        or report.get("construction_rerun") is not False
    ):
        raise _fail("quality report incomplete")
    identity = report.get("quality_identity")
    runtime = report.get("runtime_identity")
    if not isinstance(identity, Mapping) or not isinstance(runtime, Mapping):
        raise _fail("quality identity invalid")
    if payload_sha256(identity) != accepted["quality_identity_sha256"]:
        raise _fail("quality identity drift")
    if payload_sha256(runtime) != accepted["quality_runtime_identity_sha256"]:
        raise _fail("quality runtime identity drift")
    if isinstance(row_values, (str, bytes)) or not isinstance(row_values, Sequence) or len(row_values) != 16:
        raise _fail("quality unit inventory incomplete")
    rows: dict[tuple[str, str], dict[str, object]] = {}
    for index, raw in enumerate(row_values):
        row = _sealed(raw, label=f"quality unit {index}")
        method = row.get("method")
        history = row.get("history_id")
        key = (str(method), str(history))
        if (
            method not in QUALITY_NAMES.values()
            or history not in APC_BASELINE_HISTORIES
            or key in rows
            or row.get("overlay_run_id") != report.get("run_id")
            or row.get("quality_identity") != identity
            or row.get("runtime_identity_sha256") != payload_sha256(runtime)
        ):
            raise _fail("quality unit identity invalid")
        binding = construction_bindings.get(key)
        if (
            not isinstance(binding, Mapping)
            or row.get("construction_result_sha256") != binding.get("result_sha256")
            or row.get("namespace_sha256") != binding.get("namespace_sha256")
        ):
            raise _fail("quality construction binding invalid")
        rows[key] = row
    expected_keys = {
        (method, history)
        for method in QUALITY_NAMES.values()
        for history in APC_BASELINE_HISTORIES
    }
    if set(rows) != expected_keys:
        raise _fail("quality unit inventory incomplete")
    metrics: dict[str, dict[str, object]] = {}
    for method in QUALITY_NAMES.values():
        selected = [rows[(method, history)] for history in APC_BASELINE_HISTORIES]
        valid = [row for row in selected if row.get("judge_valid_denominator") == 1]
        qa = (
            sum(_number(row.get("qa_accuracy"), "quality QA invalid") for row in valid) / len(valid)
            if valid
            else None
        )
        recall = sum(
            _number(
                row.get("session_metrics", {}).get("recall_at_10")
                if isinstance(row.get("session_metrics"), Mapping)
                else None,
                "quality recall invalid",
            )
            for row in selected
        ) / len(selected)
        metrics[method] = {
            "qa_accuracy": qa if len(valid) == 4 else None,
            "qa_status": "QUALIFIED" if len(valid) == 4 else "NQ_INVALID_DENOMINATOR",
            "valid_judge_count": len(valid),
            "evidence_recall_at_10": recall,
        }
    summary = report.get("summary")
    if not isinstance(summary, Mapping) or summary.get("methods") != list(QUALITY_NAMES.values()):
        raise _fail("quality summary inventory invalid")
    by_method = summary.get("by_method")
    if not isinstance(by_method, Mapping):
        raise _fail("quality summary invalid")
    for method, derived in metrics.items():
        row = by_method.get(method)
        if not isinstance(row, Mapping):
            raise _fail("quality summary incomplete")
        expected = {
            "question_count": 4,
            "valid_judge_count": derived["valid_judge_count"],
            "qa_accuracy": derived["qa_accuracy"],
            "recall_at_10_macro": derived["evidence_recall_at_10"],
        }
        if any(row.get(key) != value for key, value in expected.items()):
            raise _fail("quality summary drift")
    return metrics, rows, report


def _per_history(
    construction_rows: Sequence[Mapping[str, Any]],
    quality_rows: Mapping[tuple[str, str], Mapping[str, object]],
    workload: Mapping[str, Any],
    *,
    input_sha: str,
    table_run_id: str,
) -> list[dict[str, Any]]:
    by_pair = {(row["method"], row["history_id"]): row for row in construction_rows}
    u0_metrics = {
        history: _performance(
            by_pair[("U0-aligned", history)]["freshness_ns"],
            by_pair[("U0-aligned", history)]["makespan_ns"],
        )
        for history in APC_BASELINE_HISTORIES
    }
    ordered_pairs = [
        (method, history) for method in HEADLINE_METHODS for history in APC_BASELINE_HISTORIES
    ] + [
        (method, REPRESENTATIVE_HISTORY) for method in ("MemBind-Barrier", "MemBind-FIFO")
    ]
    output: list[dict[str, Any]] = []
    for method, history in ordered_pairs:
        row = by_pair[(method, history)]
        quality_name = QUALITY_NAMES.get(method)
        quality = quality_rows.get((quality_name, history)) if quality_name else None
        valid = quality is not None and quality.get("judge_valid_denominator") == 1
        metrics = _performance(row["freshness_ns"], row["makespan_ns"])
        source = workload["histories"][history]
        reference = u0_metrics[history]
        makespan_speedup = reference["makespan_ns"] / metrics["makespan_ns"]
        goodput_ratio = (
            metrics["successful_goodput_episodes_per_second"]
            / reference["successful_goodput_episodes_per_second"]
        )
        freshness_reduction = (
            reference["p95_freshness_ns"] - metrics["p95_freshness_ns"]
        ) / reference["p95_freshness_ns"]
        body = {
            "schema_version": PER_HISTORY_SCHEMA,
            "status": "PASS",
            "table_run_id": table_run_id,
            "input_bindings_payload_sha256": input_sha,
            "data_role": "DEVELOPMENT_EXPOSED",
            "method": method,
            "history_id": history,
            "episode_count": row["episode_count"],
            "qa_accuracy": quality.get("qa_accuracy") if valid else None,
            "qa_status": "QUALIFIED" if valid else "NOT_EVALUATED" if quality is None else "NQ_INVALID_DENOMINATOR",
            "evidence_recall_at_10": (
                quality.get("session_metrics", {}).get("recall_at_10")
                if quality is not None and isinstance(quality.get("session_metrics"), Mapping)
                else None
            ),
            "direct_violations": row["direct_violations"],
            **metrics,
            "source_turn_count": source["source_turn_count"],
            "source_input_token_count": source["source_input_token_count"],
            "source_input_character_count": source[
                "source_input_character_count"
            ],
            "source_turns_per_second": (
                source["source_turn_count"] * 1_000_000_000 / metrics["makespan_ns"]
            ),
            "source_input_tokens_per_second": (
                source["source_input_token_count"]
                * 1_000_000_000
                / metrics["makespan_ns"]
            ),
            "makespan_speedup_vs_u0": makespan_speedup,
            "goodput_ratio_vs_u0": goodput_ratio,
            "p95_freshness_reduction_fraction_vs_u0": freshness_reduction,
            "construction_result_sha256": row["construction_result_sha256"],
            "quality_result_sha256": quality.get("payload_sha256") if quality else None,
        }
        output.append(_seal(body))
    return output


def _paired_summary(values: Mapping[str, float]) -> dict[str, object]:
    ordered = {history: float(values[history]) for history in APC_BASELINE_HISTORIES}
    observations = list(ordered.values())
    all_positive = all(value > 0 for value in observations)
    return {
        "values": ordered,
        "median": median(observations),
        "geometric_mean": (
            math.exp(sum(math.log(value) for value in observations) / len(observations))
            if all_positive
            else None
        ),
        "geometric_mean_status": (
            "DEFINED" if all_positive else "NOT_DEFINED_NON_POSITIVE_VALUES"
        ),
        "range": {"min": min(observations), "max": max(observations)},
    }


def _paired_history_analysis(
    construction_rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    by_pair = {(row["method"], row["history_id"]): row for row in construction_rows}
    by_method: dict[str, dict[str, object]] = {}
    for method in HEADLINE_METHODS:
        speedups: dict[str, float] = {}
        goodput_ratios: dict[str, float] = {}
        freshness_reductions: dict[str, float] = {}
        for history in APC_BASELINE_HISTORIES:
            reference_row = by_pair[("U0-aligned", history)]
            selected_row = by_pair[(method, history)]
            reference = _performance(
                reference_row["freshness_ns"], reference_row["makespan_ns"]
            )
            selected = _performance(
                selected_row["freshness_ns"], selected_row["makespan_ns"]
            )
            speedups[history] = reference["makespan_ns"] / selected["makespan_ns"]
            goodput_ratios[history] = (
                selected["successful_goodput_episodes_per_second"]
                / reference["successful_goodput_episodes_per_second"]
            )
            freshness_reductions[history] = (
                reference["p95_freshness_ns"] - selected["p95_freshness_ns"]
            ) / reference["p95_freshness_ns"]
        by_method[method] = {
            "makespan_speedup_vs_u0": _paired_summary(speedups),
            "goodput_ratio_vs_u0": _paired_summary(goodput_ratios),
            "p95_freshness_reduction_fraction_vs_u0": _paired_summary(
                freshness_reductions
            ),
        }
    return {
        "experimental_unit": "history",
        "history_count": 4,
        "significance_test": "NOT_PERFORMED_DEVELOPMENT_N4",
        "by_method": by_method,
    }


def _main_table(
    construction_rows: Sequence[Mapping[str, Any]],
    quality_metrics: Mapping[str, Mapping[str, object]],
    workload: Mapping[str, Any],
    *,
    input_sha: str,
    table_run_id: str,
) -> dict[str, Any]:
    rows: list[dict[str, object]] = []
    for method in HEADLINE_METHODS:
        selected = [row for row in construction_rows if row["method"] == method]
        if len(selected) != 4:
            raise _fail("headline history inventory incomplete")
        freshness = [value for row in selected for value in row["freshness_ns"]]
        makespan = sum(int(row["makespan_ns"]) for row in selected)
        quality = quality_metrics[QUALITY_NAMES[method]]
        source_turn_count = sum(
            int(workload["histories"][history]["source_turn_count"])
            for history in APC_BASELINE_HISTORIES
        )
        source_token_count = sum(
            int(workload["histories"][history]["source_input_token_count"])
            for history in APC_BASELINE_HISTORIES
        )
        rows.append(
            {
                "method": method,
                "history_count": 4,
                "episode_count": len(freshness),
                "qa_accuracy": quality["qa_accuracy"],
                "qa_status": quality["qa_status"],
                "evidence_recall_at_10": quality["evidence_recall_at_10"],
                "direct_violations": sum(int(row["direct_violations"]) for row in selected),
                "p95_freshness_ns": _nearest(freshness, 0.95),
                "p99_freshness_ns": _nearest(freshness, 0.99),
                "successful_goodput_episodes_per_second": len(freshness) * 1_000_000_000 / makespan,
                "source_turn_count": source_turn_count,
                "source_input_token_count": source_token_count,
                "source_turns_per_second": source_turn_count * 1_000_000_000 / makespan,
                "source_input_tokens_per_second": (
                    source_token_count * 1_000_000_000 / makespan
                ),
                "makespan_ns": makespan,
            }
        )
    body = {
        "schema_version": MAIN_TABLE_SCHEMA,
        "status": "PASS",
        "table_run_id": table_run_id,
        "input_bindings_payload_sha256": input_sha,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "experimental_unit": "history",
        "histories": list(APC_BASELINE_HISTORIES),
        "rows": rows,
        "paired_history_analysis": _paired_history_analysis(construction_rows),
        "quality_claim_status": (
            "DEVELOPMENT_QUALITY_QUALIFIED"
            if all(row["qa_status"] == "QUALIFIED" for row in rows)
            else "NQ_BLOCKS_FINAL_QUALITY_NON_DEGRADATION_CLAIM"
        ),
        "notes": [
            "four histories and 188 episodes per headline method",
            "pooled episode quantiles are descriptive; history is the experimental unit",
            "common open-loop arrival trace and K_LLM=2",
            "P(C=2)-aligned is an unsafe parallel reference",
            "QA is NQ if the common Quality v1 denominator is invalid",
            "cache results are observational unless reset/control is proven",
            "baseline and MemBind temporal run order was not fully counterbalanced",
            "development diagnostic; not held-out significance evidence",
        ],
    }
    return _seal(body)


def _mechanism(
    construction_rows: Sequence[Mapping[str, Any]], *, input_sha: str, table_run_id: str
) -> dict[str, Any]:
    rows = []
    for method in MEMBIND_V31_METHODS:
        selected = [
            row
            for row in construction_rows
            if row["method"] == method and row["history_id"] == REPRESENTATIVE_HISTORY
        ]
        if len(selected) != 1:
            raise _fail("mechanism block inventory incomplete")
        row = selected[0]
        admission = row["request_admission"]
        perf = _performance(row["freshness_ns"], row["makespan_ns"])
        lifecycle = row["lifecycle_diagnostics"]
        rows.append(
            {
                "method": method,
                "history_id": REPRESENTATIVE_HISTORY,
                "episode_count": row["episode_count"],
                "direct_violations": row["direct_violations"],
                **perf,
                "observed_max_llm_inflight": admission.get("observed_max_inflight"),
                "frontier_wait_mean_ns": lifecycle["frontier_wait_mean_ns"],
                "frontier_wait_p95_ns": lifecycle["frontier_wait_p95_ns"],
                "safe_work_fraction": lifecycle["safe_work_fraction"],
                "lifecycle_diagnostics_status": lifecycle[
                    "lifecycle_diagnostics_status"
                ],
                "prefix_cache_hit_rate": None,
                "prefill_diagnostics_status": "NOT_PRESENT_IN_SEALED_BLOCK_RESULT",
            }
        )
    body = {
        "schema_version": MECHANISM_SCHEMA,
        "status": "PASS",
        "table_run_id": table_run_id,
        "input_bindings_payload_sha256": input_sha,
        "history_id": REPRESENTATIVE_HISTORY,
        "claim_scope": "DESCRIPTIVE_ONE_HISTORY_MECHANISM_GATE",
        "cache_claim_scope": "OBSERVATIONAL",
        "rows": rows,
    }
    return _seal(body)


def _csv(table: Mapping[str, Any]) -> str:
    output = StringIO()
    fields = (
        "schema_version",
        "table_run_id",
        "table_payload_sha256",
        "method",
        "qa_accuracy",
        "qa_status",
        "evidence_recall_at_10",
        "direct_violations",
        "p95_freshness_ns",
        "successful_goodput_episodes_per_second",
        "makespan_ns",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for row in table["rows"]:
        writer.writerow(
            {
                "schema_version": MAIN_TABLE_SCHEMA,
                "table_run_id": table["table_run_id"],
                "table_payload_sha256": table["payload_sha256"],
                **{field: row.get(field) for field in fields if field in row},
            }
        )
    return output.getvalue()


def _markdown(table: Mapping[str, Any]) -> str:
    lines = [
        "# MemBind v3.1 Development Main Table",
        "",
        f"Schema: `{MAIN_TABLE_SCHEMA}`  ",
        f"Run: `{table['table_run_id']}`  ",
        f"Table payload SHA256: `{table['payload_sha256']}`",
        "",
        "| Method | QA Acc | Evidence R@10 | Direct Violations | P95 Freshness (s) | Goodput (eps/s) | Makespan (s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in table["rows"]:
        qa = "NQ" if row["qa_accuracy"] is None else f"{row['qa_accuracy']:.3f}"
        lines.append(
            f"| {row['method']} | {qa} | {row['evidence_recall_at_10']:.3f} | "
            f"{row['direct_violations']} | {row['p95_freshness_ns']/1e9:.6f} | "
            f"{row['successful_goodput_episodes_per_second']:.6f} | {row['makespan_ns']/1e9:.6f} |"
        )
    lines.extend(["", *[f"- {note}" for note in table["notes"]], ""])
    return "\n".join(lines)


def _report(table: Mapping[str, Any], mechanism: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# MemBind v3.1 Development Experiment Report",
            "",
            f"Status: `{table['status']}`  ",
            f"Run: `{table['table_run_id']}`  ",
            f"Input bindings SHA256: `{inputs['payload_sha256']}`  ",
            f"Main table SHA256: `{table['payload_sha256']}`  ",
            f"Mechanism ablation SHA256: `{mechanism['payload_sha256']}`",
            "",
            "This report is a deterministic projection of sealed development artifacts. Construction and Quality v1 were reduced separately; quality latency is excluded from construction metrics.",
            "",
            "The mechanism gate is one-history descriptive evidence. Missing frontier-wait, safe-work, and backend prefill fields remain explicitly unavailable rather than inferred from unrelated counters.",
            "",
            "The baseline and MemBind blocks were not fully counterbalanced in wall-clock order. These data are development diagnostics, not held-out statistical-significance evidence.",
            "",
        ]
    )


def reduce_development_results(
    *,
    table_run_id: str,
    baseline_acceptance: Mapping[str, object],
    baseline_results: Sequence[Mapping[str, object]],
    method_plan: Mapping[str, object],
    method_artifacts: Sequence[Mapping[str, object]],
    quality_report: Mapping[str, object],
    quality_rows: Sequence[Mapping[str, object]],
    workload_complexity: Mapping[str, object],
) -> dict[str, object]:
    """Verify all inputs and produce the exact seven-file in-memory result."""

    if not isinstance(table_run_id, str) or _RUN_ID.fullmatch(table_run_id) is None:
        raise _fail("table run id invalid")
    accepted = _verify_acceptance(baseline_acceptance)
    try:
        plan = verify_membind_v31_method_plan(method_plan)
    except ValueError as error:
        raise _fail(f"method plan invalid: {error}") from None
    if (
        plan.get("baseline_plan_payload_sha256") != accepted["plan_payload_sha256"]
        or plan.get("arrival_trace_sha256") != accepted["arrival_trace_sha256"]
        or plan.get("source_manifest_sha256") != accepted["source_manifest_sha256"]
        or plan.get("shared_execution_envelope_sha256")
        != accepted["shared_execution_envelope_sha256"]
        or plan.get("global_llm_admission_k") != 2
    ):
        raise _fail("method plan baseline binding invalid")
    workload = _verify_workload_complexity(
        workload_complexity,
        accepted=accepted,
        plan=plan,
    )
    baseline_rows, baseline_hashes = _baseline_rows(baseline_results, accepted)
    method_rows, method_hashes = _method_rows(method_artifacts, plan)
    construction_bindings = {**baseline_hashes, **method_hashes}
    quality_metrics, verified_quality_rows, verified_quality = _quality(
        quality_report,
        quality_rows,
        accepted=accepted,
        construction_bindings=construction_bindings,
    )
    input_body = {
        "schema_version": INPUT_SCHEMA,
        "status": "PASS",
        "table_run_id": table_run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "baseline_run_id": accepted["run_id"],
        "baseline_acceptance_payload_sha256": accepted["payload_sha256"],
        "baseline_plan_payload_sha256": accepted["plan_payload_sha256"],
        "baseline_block_result_payload_sha256s": accepted[
            "block_result_payload_sha256s"
        ],
        "method_run_id": plan["run_id"],
        "method_plan_payload_sha256": plan["payload_sha256"],
        "method_block_result_payload_sha256s": [
            row["result"]["payload_sha256"] for row in method_artifacts
        ],
        "quality_run_id": verified_quality["run_id"],
        "quality_report_payload_sha256": verified_quality["payload_sha256"],
        "quality_unit_payload_sha256s": [
            verified_quality_rows[(method, history)]["payload_sha256"]
            for method in QUALITY_NAMES.values()
            for history in APC_BASELINE_HISTORIES
        ],
        "quality_identity_sha256": accepted["quality_identity_sha256"],
        "quality_runtime_identity_sha256": accepted["quality_runtime_identity_sha256"],
        "methodology_sha256": plan["methodology_sha256"],
        "workplan_sha256": plan["workplan_sha256"],
        "source_manifest_sha256": accepted["source_manifest_sha256"],
        "arrival_trace_sha256": accepted["arrival_trace_sha256"],
        "shared_execution_envelope_sha256": accepted[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "workload_complexity_payload_sha256": workload["payload_sha256"],
    }
    inputs = _seal(input_body)
    all_rows = [*baseline_rows, *method_rows]
    per_history = _per_history(
        all_rows,
        verified_quality_rows,
        workload,
        input_sha=inputs["payload_sha256"],
        table_run_id=table_run_id,
    )
    mechanism = _mechanism(
        all_rows, input_sha=inputs["payload_sha256"], table_run_id=table_run_id
    )
    table = _main_table(
        all_rows,
        quality_metrics,
        workload,
        input_sha=inputs["payload_sha256"],
        table_run_id=table_run_id,
    )
    return {
        "INPUT_BINDINGS.json": inputs,
        "PER_HISTORY_RESULTS.jsonl": per_history,
        "MECHANISM_ABLATION.json": mechanism,
        "DEVELOPMENT_MAIN_TABLE.json": table,
        "DEVELOPMENT_MAIN_TABLE.csv": _csv(table),
        "DEVELOPMENT_MAIN_TABLE.md": _markdown(table),
        "EXPERIMENT_REPORT.md": _report(table, mechanism, inputs),
    }


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_development_outputs(root: Path, outputs: Mapping[str, object]) -> None:
    """Create one immutable output directory with exactly the frozen files."""

    if not isinstance(outputs, Mapping) or set(outputs) != set(OUTPUT_FILES):
        raise _fail("output inventory invalid")
    target = Path(root)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise _fail("output root exists") from None
    atomic_write_json(target / "INPUT_BINDINGS.json", outputs["INPUT_BINDINGS.json"])
    for row in outputs["PER_HISTORY_RESULTS.jsonl"]:
        append_jsonl_durable(target / "PER_HISTORY_RESULTS.jsonl", row)
    atomic_write_json(target / "MECHANISM_ABLATION.json", outputs["MECHANISM_ABLATION.json"])
    atomic_write_json(target / "DEVELOPMENT_MAIN_TABLE.json", outputs["DEVELOPMENT_MAIN_TABLE.json"])
    for name in ("DEVELOPMENT_MAIN_TABLE.csv", "DEVELOPMENT_MAIN_TABLE.md", "EXPERIMENT_REPORT.md"):
        value = outputs[name]
        if not isinstance(value, str):
            raise _fail("output text invalid")
        _atomic_write_text(target / name, value)


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def load_development_inputs(
    *,
    baseline_acceptance_path: Path,
    baseline_run_root: Path,
    method_plan_path: Path,
    method_run_root: Path,
    quality_root: Path,
    workload_complexity_path: Path,
) -> dict[str, object]:
    """Load and independently inspect the filesystem inputs used by the CLI."""

    acceptance = _read_json(baseline_acceptance_path, "baseline acceptance unreadable")
    baseline_results = [
        _read_json(
            Path(baseline_run_root)
            / "blocks"
            / f"block-{index:02d}"
            / "APC_ALIGNED_BLOCK_RESULT.json",
            "baseline result unreadable",
        )
        for index in range(12)
    ]
    plan = _read_json(method_plan_path, "method plan unreadable")
    method_artifacts: list[dict[str, object]] = []
    for index in range(6):
        block_root = Path(method_run_root) / "blocks" / f"block-{index:02d}"
        result = _read_json(block_root / "result.json", "method result unreadable")
        try:
            inspected = inspect_v31_block(block_root)
        except ValueError as error:
            raise _fail(f"method block artifact invalid: {error}") from None
        if (
            result.get("manifest_sha256") != inspected["manifest"].get("manifest_sha256")
            or result.get("checkpoint") != inspected["checkpoint"]
        ):
            raise _fail("method block result artifact binding invalid")
        method_artifacts.append({"result": result, "events": inspected["events"]})
    quality = Path(quality_root)
    report = _read_json(quality / "QUALITY_EVALUATION_V1_RESULTS.json", "quality report unreadable")
    public_paths = sorted(quality.glob("units/*/*/attempt-*/public.json"))
    rows = [_read_json(path, "quality unit unreadable") for path in public_paths]
    workload = _read_json(
        workload_complexity_path, "workload complexity unreadable"
    )
    return {
        "baseline_acceptance": acceptance,
        "baseline_results": baseline_results,
        "method_plan": plan,
        "method_artifacts": method_artifacts,
        "quality_report": report,
        "quality_rows": rows,
        "workload_complexity": workload,
    }


__all__ = [
    "DevelopmentReducerError",
    "OUTPUT_FILES",
    "load_development_inputs",
    "reduce_development_results",
    "write_development_outputs",
]
