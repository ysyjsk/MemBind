"""Offline-only reducers for v4 development and formal results.

The final reducer never imports a provider, graph driver, or live runner.  It
verifies every sealed input and frozen evidence binding before deriving the
fixed P7 artifact inventory.  Missing measurements remain ``NOT_AVAILABLE``;
construction is never rerun to fill a reporting gap.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file
from paper_eval.membind_v4.autoresearch import assess_candidate
from paper_eval.membind_v4.freeze import FORMAL_HISTORY_IDS, V4FreezeError, verify_frozen_method
from paper_eval.membind_v4.full_run import FULL_FAILURE_SCHEMA, FULL_RESULT_SCHEMA
from paper_eval.membind_v4.p0_binding import PREFIX_REFERENCE_SCHEMA


V4_FINAL_OUTPUT_FILES = (
    "V4_FULL_RESULT.json",
    "V4_MAIN_TABLE.json",
    "V4_MECHANISM_TABLE.json",
    "V4_CORRECTNESS_TABLE.json",
    "V4_QUALITY_OVERLAY.json",
    "V4_FINAL_REPORT.md",
)

_NOT_AVAILABLE = "NOT_AVAILABLE"
_EXPECTED_SOURCE_COUNTS = (49, 46, 44, 49)
_METHOD_ORDER = ("U0", "A0", "P(C=2)", "MemBind v3.1", "MemBind v4")
_BASELINE_NAMES = {
    "U0": "U0",
    "U0-aligned": "U0",
    "A0": "A0",
    "A0-aligned": "A0",
    "P(C=2)": "P(C=2)",
    "P(C=2)-aligned": "P(C=2)",
}
_WORK_FIELDS = (
    "llm_logical_calls",
    "llm_transport_attempts",
    "prompt_tokens",
    "completion_tokens",
    "embedding_calls",
    "db_operations",
    "node_count",
    "edge_count",
    "episode_count",
    "speculative_wasted_calls",
    "speculative_wasted_tokens",
)
_QUALITY_FIELDS = {
    "recall_at_1": ("recall_at_1", "recall_at_1_macro"),
    "recall_at_3": ("recall_at_3", "recall_at_3_macro"),
    "recall_at_5": ("recall_at_5", "recall_at_5_macro"),
    "recall_at_10": ("recall_at_10", "recall_at_10_macro"),
    "mrr": ("mrr", "mrr_macro"),
    "ndcg_at_10": ("ndcg_at_10", "ndcg_at_10_macro"),
    "reader_judge_qa": ("reader_judge_qa", "qa_accuracy"),
    "graph_quality": (
        "graph_quality",
        "edge_attributed_source_coverage_at_10_macro",
    ),
    "latest_valid": ("latest_valid", "latest_valid_macro"),
    "conflict": (
        "conflict",
        "conflicting_relation_group_count_macro",
    ),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANDIDATE_ID = re.compile(r"^c0[1-3]$")
_CANDIDATE_MANIFEST_SCHEMA = "membind.paper-eval-v4.candidate.v1"
_CANDIDATE_SUMMARY_SCHEMA = "membind.paper-eval-v4.summary.v1"


class V4ReducerError(ValueError):
    """Candidate or comparator artifact is malformed."""


def _fail(code: str) -> V4ReducerError:
    return V4ReducerError(code)


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V4ReducerError(f"artifact_unreadable:{path}") from error
    if not isinstance(value, dict):
        raise V4ReducerError(f"artifact_not_object:{path}")
    return value


def _read_labeled(path: Path, label: str) -> dict[str, Any]:
    try:
        return _read(path)
    except V4ReducerError as error:
        raise _fail(f"{label}_unreadable") from error


def _sealed(value: Mapping[str, object], label: str, *, schema: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(f"{label}_invalid")
    selected = deepcopy(dict(value))
    digest = selected.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != payload_sha256(selected):
        raise _fail(f"{label}_payload_hash_mismatch")
    if schema is not None and selected.get("schema_version") != schema:
        raise _fail(f"{label}_schema_invalid")
    selected["payload_sha256"] = digest
    return selected


def _seal(value: Mapping[str, object]) -> dict[str, Any]:
    selected = deepcopy(dict(value))
    if "payload_sha256" in selected:
        raise _fail("output_already_sealed")
    selected["payload_sha256"] = payload_sha256(selected)
    return selected


def _nonnegative(value: object, code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise _fail(code)
    return float(value)


def _count(value: object, code: str) -> int:
    number = _nonnegative(value, code)
    if not number.is_integer():
        raise _fail(code)
    return int(number)


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _available_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _nearest(values: Sequence[float], quantile: float) -> float | int:
    ordered = sorted(values)
    if not ordered:
        raise _fail("freshness_inventory_empty")
    selected = ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]
    return int(selected) if float(selected).is_integer() else selected


def _input_binding(path: Path, artifact: Mapping[str, object]) -> dict[str, object]:
    return {
        "absolute_path": str(path.resolve()),
        "file_sha256": sha256_file(path),
        "payload_sha256": artifact.get("payload_sha256"),
        "schema_version": artifact.get("schema_version"),
        "status": artifact.get("status"),
    }


def _verify_frozen_binding(
    frozen: Mapping[str, object],
    *,
    role: str,
    path: Path,
) -> None:
    evidence = frozen.get("evidence")
    value = evidence.get(role) if isinstance(evidence, Mapping) else None
    if not isinstance(value, Mapping):
        raise _fail(f"frozen_{role}_binding_missing")
    if value.get("sha256") != sha256_file(path):
        raise _fail(f"frozen_{role}_binding_hash_mismatch")
    bound_path = value.get("absolute_path")
    if isinstance(bound_path, str) and Path(bound_path).resolve() != path.resolve():
        raise _fail(f"frozen_{role}_binding_path_mismatch")


def _performance_payload(value: Mapping[str, object]) -> Mapping[str, object]:
    performance = value.get("performance")
    if isinstance(performance, Mapping):
        return performance
    block = value.get("block_result")
    if isinstance(block, Mapping) and isinstance(block.get("performance"), Mapping):
        return block["performance"]
    return {}


def _freshness(performance: Mapping[str, object]) -> list[float] | None:
    direct = performance.get("freshness_ns")
    if isinstance(direct, Sequence) and not isinstance(direct, (str, bytes)):
        return [_nonnegative(value, "freshness_invalid") for value in direct]
    per_source = performance.get("per_source")
    if isinstance(per_source, Sequence) and not isinstance(per_source, (str, bytes)):
        values: list[float] = []
        for row in per_source:
            if not isinstance(row, Mapping):
                raise _fail("per_source_performance_invalid")
            values.append(_nonnegative(row.get("freshness_ns"), "freshness_invalid"))
        return values
    return None


def _direct_violations(value: Mapping[str, object]) -> int:
    for owner, field in (
        (value, "direct_violation_count"),
        (value, "direct_violations"),
        (value.get("correctness"), "direct_violations_total"),
        (value.get("block_result"), "direct_violation_count"),
    ):
        if isinstance(owner, Mapping) and field in owner:
            return _count(owner[field], "direct_violation_count_invalid")
    return 0


def _one_measurement(value: Mapping[str, object], *, default_count: int | None = None) -> dict[str, object]:
    performance = _performance_payload(value)
    makespan = performance.get("makespan_ns")
    count = value.get(
        "source_count",
        value.get("episode_count", performance.get("published_episode_count", performance.get("episode_count", default_count))),
    )
    normalized_count = _count(count, "episode_count_invalid") if count is not None else None
    normalized_makespan: float | None = None
    if makespan is not None:
        normalized_makespan = _nonnegative(makespan, "makespan_invalid")
        if normalized_makespan <= 0:
            raise _fail("makespan_invalid")
    values = _freshness(performance)
    if values is not None and normalized_count is not None and len(values) != normalized_count:
        raise _fail("freshness_coverage_mismatch")
    return {
        "episode_count": normalized_count,
        "makespan_ns": normalized_makespan,
        "freshness_ns": values,
        "direct_violations": _direct_violations(value),
    }


def _aggregate_measurements(values: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not values:
        return {
            "status": _NOT_AVAILABLE,
            "episode_count": _NOT_AVAILABLE,
            "makespan_ns": _NOT_AVAILABLE,
            "goodput_episodes_per_second": _NOT_AVAILABLE,
            "p50_freshness_ns": _NOT_AVAILABLE,
            "p95_freshness_ns": _NOT_AVAILABLE,
            "p99_freshness_ns": _NOT_AVAILABLE,
            "direct_violations": _NOT_AVAILABLE,
        }
    normalized = [_one_measurement(value) for value in values]
    counts = [value["episode_count"] for value in normalized]
    makespans = [value["makespan_ns"] for value in normalized]
    count = sum(int(value) for value in counts) if all(value is not None for value in counts) else None
    makespan = sum(float(value) for value in makespans) if all(value is not None for value in makespans) else None
    freshness_rows = [value["freshness_ns"] for value in normalized]
    freshness: list[float] | None = None
    if all(isinstance(value, list) for value in freshness_rows):
        freshness = [item for row in freshness_rows for item in row]  # type: ignore[union-attr]
    if freshness is not None and count is not None and len(freshness) != count:
        raise _fail("freshness_coverage_mismatch")
    return {
        "status": "AVAILABLE",
        "episode_count": count if count is not None else _NOT_AVAILABLE,
        "makespan_ns": int(makespan) if makespan is not None and makespan.is_integer() else (makespan if makespan is not None else _NOT_AVAILABLE),
        "goodput_episodes_per_second": (
            count * 1_000_000_000 / makespan
            if count is not None and makespan is not None
            else _NOT_AVAILABLE
        ),
        "p50_freshness_ns": _nearest(freshness, 0.50) if freshness else _NOT_AVAILABLE,
        "p95_freshness_ns": _nearest(freshness, 0.95) if freshness else _NOT_AVAILABLE,
        "p99_freshness_ns": _nearest(freshness, 0.99) if freshness else _NOT_AVAILABLE,
        "direct_violations": sum(int(value["direct_violations"]) for value in normalized),
    }


def _verify_baseline_artifacts(binding: Mapping[str, object]) -> dict[str, list[dict[str, Any]]]:
    artifacts = binding.get("artifacts")
    rows = artifacts.get("baseline") if isinstance(artifacts, Mapping) else None
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise _fail("baseline_result_inventory_invalid")
    by_method: dict[str, list[dict[str, Any]]] = {name: [] for name in _METHOD_ORDER[:3]}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise _fail("baseline_result_binding_invalid")
        method = _BASELINE_NAMES.get(str(row.get("method")))
        if method is None:
            continue
        raw_path = row.get("absolute_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise _fail("baseline_result_path_invalid")
        path = Path(raw_path)
        if _sha(row.get("sha256"), "baseline_result_file_hash_invalid") != sha256_file(path):
            raise _fail(f"baseline_result_file_hash_mismatch:{index}")
        value = _sealed(_read_labeled(path, f"baseline_result_{index}"), f"baseline_result_{index}")
        if value.get("status") != "PASS":
            raise _fail("baseline_result_not_pass")
        by_method[method].append(value)
    return by_method


def _full_histories(full: Mapping[str, object]) -> list[dict[str, object]]:
    histories = full.get("histories")
    if isinstance(histories, (str, bytes)) or not isinstance(histories, Sequence):
        raise _fail("full_run_history_inventory_invalid")
    if len(histories) != 4:
        raise _fail("full_run_history_inventory_invalid")
    normalized: list[dict[str, object]] = []
    for index, (raw, history_id, source_count) in enumerate(
        zip(histories, FORMAL_HISTORY_IDS, _EXPECTED_SOURCE_COUNTS, strict=True)
    ):
        if not isinstance(raw, Mapping):
            raise _fail("full_run_history_invalid")
        if (
            raw.get("history_id") != history_id
            or raw.get("source_count") != source_count
            or not isinstance(raw.get("run_id"), str)
            or not raw.get("run_id")
            or not isinstance(raw.get("namespace"), str)
            or not raw.get("namespace")
        ):
            raise _fail("full_run_history_identity_drift")
        _sha(raw.get("result_payload_sha256"), "full_run_history_hash_invalid")
        result = raw.get("result")
        if not isinstance(result, Mapping):
            raise _fail("full_run_history_result_invalid")
        normalized.append({**dict(result), "source_count": source_count, "history_id": history_id})
    return normalized


def _verify_full_result(value: Mapping[str, object], frozen: Mapping[str, object]) -> tuple[dict[str, Any], list[dict[str, object]]]:
    schema = value.get("schema_version")
    if schema == FULL_FAILURE_SCHEMA:
        failure = _sealed(value, "full_run_result", schema=FULL_FAILURE_SCHEMA)
        if failure.get("status") != "FAILED_NON_MERGEABLE" or failure.get("formal_main_table_eligible") is not False:
            raise _fail("full_run_failure_invalid")
        _sha(failure.get("manifest_payload_sha256"), "full_run_manifest_hash_invalid")
        return failure, []
    full = _sealed(value, "full_run_result", schema=FULL_RESULT_SCHEMA)
    if (
        full.get("status") != "PASS"
        or full.get("frozen_method_payload_sha256") != frozen.get("payload_sha256")
        or full.get("history_ids") != list(FORMAL_HISTORY_IDS)
        or full.get("history_count") != 4
        or full.get("source_count") != 188
        or full.get("direct_violation_count") != 0
        or full.get("runner_mode") not in {"live", "fixture"}
        or not isinstance(full.get("formal_main_table_eligible"), bool)
    ):
        raise _fail("full_run_result_identity_drift")
    _sha(full.get("manifest_payload_sha256"), "full_run_manifest_hash_invalid")
    _sha(full.get("frozen_method_payload_sha256"), "frozen_method_hash_invalid")
    return full, _full_histories(full)


def _comparator(path: Path | None) -> tuple[dict[str, Any] | None, dict[str, object]]:
    if path is None:
        return None, {"status": _NOT_AVAILABLE}
    selected = Path(path)
    artifact = _sealed(_read_labeled(selected, "v31_result"), "v31_result")
    if artifact.get("status") != "PASS":
        raise _fail("v31_result_not_pass")
    measurement_owner = artifact.get("block_result")
    owner = measurement_owner if isinstance(measurement_owner, Mapping) else artifact
    metrics = _aggregate_measurements([owner])
    metrics["formal_comparator_eligible"] = artifact.get("formal_main_table_eligible") is True
    return artifact, metrics


def _ratio(reference: object, value: object) -> float | str:
    if not _available_number(reference) or not _available_number(value) or float(value) <= 0:
        return _NOT_AVAILABLE
    return float(reference) / float(value)


def _main_table(
    *,
    baseline: Mapping[str, Sequence[Mapping[str, object]]],
    v31: Mapping[str, object],
    v4: Mapping[str, object],
    eligible: bool,
    reasons: Sequence[str],
) -> dict[str, Any]:
    metrics: dict[str, dict[str, object]] = {
        method: _aggregate_measurements(list(baseline[method])) for method in _METHOD_ORDER[:3]
    }
    metrics["MemBind v3.1"] = dict(v31)
    metrics["MemBind v4"] = dict(v4)
    u0_span = metrics["U0"].get("makespan_ns")
    v31_span = metrics["MemBind v3.1"].get("makespan_ns")
    rows = []
    for method in _METHOD_ORDER:
        row = {"method": method, **metrics[method]}
        row["speedup_vs_u0"] = _ratio(u0_span, row.get("makespan_ns"))
        row["speedup_vs_v31"] = _ratio(v31_span, row.get("makespan_ns"))
        rows.append(row)
    return _seal(
        {
            "schema_version": "membind.paper-eval-v4.main-table.v1",
            "status": "FORMAL_MAIN_TABLE_ELIGIBLE" if eligible else "NON_FORMAL",
            "formal_main_table_eligible": eligible,
            "eligibility_reasons": list(reasons),
            "method_order": list(_METHOD_ORDER),
            "rows": rows,
        }
    )


def _sum_field(rows: Sequence[Mapping[str, object]], owner: str, field: str) -> int | float | str:
    if not rows:
        return _NOT_AVAILABLE
    values: list[float] = []
    for row in rows:
        selected = row.get(owner)
        if not isinstance(selected, Mapping) or field not in selected:
            return _NOT_AVAILABLE
        values.append(_nonnegative(selected[field], f"{owner}_{field}_invalid"))
    total = sum(values)
    return int(total) if total.is_integer() else total


def _sum_aliases(
    rows: Sequence[Mapping[str, object]],
    owner: str,
    fields: Sequence[str],
) -> int | float | str:
    if not rows:
        return _NOT_AVAILABLE
    values: list[float] = []
    for row in rows:
        selected = row.get(owner)
        if not isinstance(selected, Mapping):
            return _NOT_AVAILABLE
        present = next((field for field in fields if field in selected), None)
        if present is None:
            return _NOT_AVAILABLE
        values.append(_nonnegative(selected[present], f"{owner}_{present}_invalid"))
    total = sum(values)
    return int(total) if total.is_integer() else total


def _work_volume(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    aliases = {
        "llm_logical_calls": ("llm_logical_calls", "total_llm_logical_calls", "logical_call_count"),
        "llm_transport_attempts": ("llm_transport_attempts", "actual_transport_attempts"),
        "prompt_tokens": ("prompt_tokens",),
        "completion_tokens": ("completion_tokens",),
        "embedding_calls": ("embedding_calls",),
        "db_operations": ("db_operations",),
        "node_count": ("node_count",),
        "edge_count": ("edge_count", "relationship_count"),
        "episode_count": ("episode_count",),
        "speculative_wasted_calls": ("speculative_wasted_calls", "miss_wasted_calls"),
        "speculative_wasted_tokens": ("speculative_wasted_tokens", "miss_waste_tokens"),
    }
    for field in _WORK_FIELDS:
        owner = "final_graph" if field in {"node_count", "edge_count", "episode_count"} else "work_volume"
        result[field] = _sum_aliases(rows, owner, aliases[field])
    return result


def _mechanism(
    *,
    histories: Sequence[Mapping[str, object]],
    v31_available: bool,
    eligible: bool,
) -> dict[str, Any]:
    aliases = {
        "node_resolve_qualified": ("qualified_node_resolve_count",),
        "speculation_launched": ("speculation_launch_count", "speculation_launched_count"),
        "hit_count": ("semantic_hit_count",),
        "miss_count": ("semantic_miss_count",),
        "hidden_critical_time_ns": ("hidden_critical_time_ns",),
        "miss_waste_tokens": ("miss_waste_tokens",),
        "validation_overhead_ns": ("validation_overhead_ns",),
        "frontier_interference": ("frontier_interference_count",),
        "useful_token_throughput": ("useful_token_throughput",),
        "apc_cached_tokens": ("apc_cached_tokens",),
    }
    v4 = {name: _sum_aliases(histories, "telemetry", fields) for name, fields in aliases.items()}
    if v4["node_resolve_qualified"] == _NOT_AVAILABLE:
        hits = v4["hit_count"]
        misses = v4["miss_count"]
        if _available_number(hits) and _available_number(misses):
            v4["node_resolve_qualified"] = int(float(hits) + float(misses))
    useful = _sum_field(histories, "telemetry", "active_two_useful_ns")
    total = _sum_field(histories, "telemetry", "active_two_total_ns")
    v4["active_two_useful_fraction"] = (
        float(useful) / float(total)
        if _available_number(useful) and _available_number(total) and float(total) > 0
        else _NOT_AVAILABLE
    )
    v4["work_volume"] = _work_volume(histories)
    v31 = {
        "node_resolve_qualified": _NOT_AVAILABLE,
        "speculation_launched": 0 if v31_available else _NOT_AVAILABLE,
        "hit_count": 0 if v31_available else _NOT_AVAILABLE,
        "miss_count": 0 if v31_available else _NOT_AVAILABLE,
        "hidden_critical_time_ns": 0 if v31_available else _NOT_AVAILABLE,
        "miss_waste_tokens": 0 if v31_available else _NOT_AVAILABLE,
        "validation_overhead_ns": 0 if v31_available else _NOT_AVAILABLE,
        "frontier_interference": _NOT_AVAILABLE,
        "useful_token_throughput": _NOT_AVAILABLE,
        "active_two_useful_fraction": _NOT_AVAILABLE,
        "apc_cached_tokens": _NOT_AVAILABLE,
        "work_volume": {field: _NOT_AVAILABLE for field in _WORK_FIELDS},
    }
    return _seal(
        {
            "schema_version": "membind.paper-eval-v4.mechanism-table.v1",
            "status": "AVAILABLE" if histories else _NOT_AVAILABLE,
            "formal_main_table_eligible": eligible,
            "by_method": {"MemBind v3.1": v31, "MemBind v4": v4},
        }
    )


def _correctness(
    *,
    main_rows: Sequence[Mapping[str, object]],
    histories: Sequence[Mapping[str, object]],
    eligible: bool,
) -> dict[str, Any]:
    persistent = _sum_field(histories, "telemetry", "persistent_write_count")
    rows = []
    for row in main_rows:
        method = row["method"]
        values: dict[str, object] = {
            "method": method,
            "direct_violations": row.get("direct_violations", _NOT_AVAILABLE),
            "persistent_speculative_writes": _NOT_AVAILABLE,
        }
        if method == "MemBind v4":
            values["persistent_speculative_writes"] = persistent
        elif method == "MemBind v3.1" and row.get("status") == "AVAILABLE":
            values["persistent_speculative_writes"] = 0
        rows.append(values)
    return _seal(
        {
            "schema_version": "membind.paper-eval-v4.correctness-table.v1",
            "status": "PASS" if histories and persistent == 0 else (_NOT_AVAILABLE if not histories else "FAIL"),
            "formal_main_table_eligible": eligible,
            "rows": rows,
        }
    )


def _quality(path: Path | None, *, eligible: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    artifact: dict[str, Any] | None = None
    by_method: Mapping[str, object] = {}
    if path is not None:
        artifact = _sealed(_read_labeled(Path(path), "quality_overlay"), "quality_overlay")
        if artifact.get("status") != "PASS":
            raise _fail("quality_overlay_not_pass")
        summary = artifact.get("summary")
        selected = summary.get("by_method") if isinstance(summary, Mapping) else None
        if isinstance(selected, Mapping):
            by_method = selected
    normalized: dict[str, dict[str, object]] = {}
    for method in _METHOD_ORDER:
        aliases = {
            "MemBind v3.1": ("MemBind v3.1", "MemBind"),
            "MemBind v4": ("MemBind v4", "v4"),
        }.get(method, (method,))
        raw = next((by_method[name] for name in aliases if name in by_method), None)
        if not isinstance(raw, Mapping):
            normalized[method] = {field: _NOT_AVAILABLE for field in _QUALITY_FIELDS}
            continue
        values: dict[str, object] = {}
        for target, aliases in _QUALITY_FIELDS.items():
            values[target] = next((raw[name] for name in aliases if name in raw), _NOT_AVAILABLE)
            if values[target] != _NOT_AVAILABLE:
                _nonnegative(values[target], f"quality_{target}_invalid")
        normalized[method] = values
    comparable = []
    for metric in _QUALITY_FIELDS:
        before = normalized["MemBind v3.1"][metric]
        after = normalized["MemBind v4"][metric]
        if _available_number(before) and _available_number(after):
            delta = float(after) - float(before)
            non_degradation_margin = -delta if metric == "conflict" else delta
            comparable.append((metric, delta, non_degradation_margin))
    verdict = (
        "PASS_NON_DEGRADED"
        if comparable and all(margin >= 0 for _, _, margin in comparable)
        else ("DEGRADED" if comparable else _NOT_AVAILABLE)
    )
    return (
        _seal(
            {
                "schema_version": "membind.paper-eval-v4.quality-overlay.v1",
                "status": "AVAILABLE" if artifact is not None else _NOT_AVAILABLE,
                "formal_main_table_eligible": eligible,
                "by_method": normalized,
                "non_degradation": {
                    "verdict": verdict,
                    "deltas_v4_minus_v31": {name: delta for name, delta, _ in comparable},
                },
                "construction_rerun": False,
                "quality_latency_included_in_construction_metrics": False,
            }
        ),
        artifact,
    )


def _eligibility(
    *,
    full: Mapping[str, object],
    envelope: object,
    baseline: Mapping[str, Sequence[Mapping[str, object]]],
    v31: Mapping[str, object],
    histories: Sequence[Mapping[str, object]],
) -> list[str]:
    reasons: list[str] = []
    if full.get("schema_version") == FULL_FAILURE_SCHEMA:
        reasons.append("FULL_RUN_BLOCKED")
        return reasons
    if full.get("runner_mode") != "live":
        reasons.append("FULL_RUN_MODE_NOT_LIVE")
    if full.get("formal_main_table_eligible") is not True:
        reasons.append("FULL_RUN_DECLARED_INELIGIBLE")
    qualified_envelopes = {
        "FORMAL_LIVE_ENVELOPE_MATCH",
        "LIVE_ENVELOPE_MATCH",
        "SAME_LIVE_ENVELOPE_FORMAL_COMPARISON",
    }
    if envelope not in qualified_envelopes:
        reasons.append("MIXED_OR_UNQUALIFIED_COMPARISON_ENVELOPE")
    if any(
        sum(int(_one_measurement(row)["episode_count"] or 0) for row in baseline[method]) != 188
        for method in _METHOD_ORDER[:3]
    ):
        reasons.append("BASELINE_FORMAL_COVERAGE_INCOMPLETE")
    if (
        v31.get("status") != "AVAILABLE"
        or v31.get("episode_count") != 188
        or v31.get("formal_comparator_eligible") is not True
    ):
        reasons.append("V31_FORMAL_COMPARATOR_UNAVAILABLE")
    persistent = _sum_field(histories, "telemetry", "persistent_write_count")
    if persistent != 0:
        reasons.append("V4_CORRECTNESS_EVIDENCE_INCOMPLETE_OR_FAILED")
    return reasons


def _report(
    *,
    result: Mapping[str, object],
    table: Mapping[str, object],
    mechanism: Mapping[str, object],
    correctness: Mapping[str, object],
    quality: Mapping[str, object],
) -> str:
    rows = table.get("rows") if isinstance(table.get("rows"), Sequence) else []
    by_method = {
        str(row.get("method")): row for row in rows if isinstance(row, Mapping)
    }
    v4 = by_method.get("MemBind v4", {})
    speedup = v4.get("speedup_vs_v31", _NOT_AVAILABLE)
    if _available_number(speedup):
        conclusion = "YES" if float(speedup) > 1 else "NO"
        reason = f"measured makespan speedup versus v3.1 is {float(speedup):.6g}x"
    else:
        conclusion = _NOT_AVAILABLE
        reason = "a same-envelope v3.1 performance comparison is not available"
    eligibility = (
        "FORMAL_MAIN_TABLE_ELIGIBLE"
        if result.get("formal_main_table_eligible") is True
        else "NON_FORMAL"
    )
    lines = [
        "# MemBind v4 Final Report",
        "",
        f"Status: `{result.get('status')}`  ",
        f"Main-table status: `{eligibility}`  ",
        f"Candidate: `{result.get('candidate_id', _NOT_AVAILABLE)}`  ",
        "",
        "## Result",
        "",
        f"MemBind v4 better than v3.1: `{conclusion}`. The reducer found that {reason}.",
        "",
        "## Evidence",
        "",
        f"- Formal source coverage: `{result.get('source_count', _NOT_AVAILABLE)}`",
        f"- Direct violations: `{v4.get('direct_violations', _NOT_AVAILABLE)}`",
        f"- Mechanism status: `{mechanism.get('status', _NOT_AVAILABLE)}`",
        f"- Correctness status: `{correctness.get('status', _NOT_AVAILABLE)}`",
        f"- Quality status: `{quality.get('status', _NOT_AVAILABLE)}`",
        "",
        "## Eligibility",
        "",
        f"Reasons: `{', '.join(result.get('eligibility_reasons', [])) or 'NONE'}`",
        "",
        "All tables are deterministic projections of sealed inputs. No construction was rerun.",
        "",
    ]
    return "\n".join(lines)


def reduce_v4_final(
    *,
    frozen_method_path: Path,
    full_run_result_path: Path,
    baseline_binding_path: Path,
    prefix_reference_path: Path,
    v31_result_path: Path | None = None,
    quality_overlay_path: Path | None = None,
) -> dict[str, object]:
    """Reduce sealed P7 evidence into the fixed six-output inventory."""

    frozen_path = Path(frozen_method_path)
    try:
        frozen = verify_frozen_method(frozen_path)
    except (V4FreezeError, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fail(f"frozen_method_invalid:{error}") from error
    baseline_path = Path(baseline_binding_path)
    prefix_path = Path(prefix_reference_path)
    baseline_binding = _sealed(
        _read_labeled(baseline_path, "baseline_binding"), "baseline_binding"
    )
    prefix_reference = _sealed(
        _read_labeled(prefix_path, "prefix_reference"), "prefix_reference"
    )
    if baseline_binding.get("status") != "PASS" or prefix_reference.get("status") != "PASS":
        raise _fail("p0_evidence_not_pass")
    _verify_frozen_binding(frozen, role="baseline_binding", path=baseline_path)
    _verify_frozen_binding(frozen, role="prefix_reference", path=prefix_path)
    raw_full = _read_labeled(Path(full_run_result_path), "full_run_result")
    full, histories = _verify_full_result(raw_full, frozen)
    baseline = _verify_baseline_artifacts(baseline_binding)
    v31_artifact, v31_metrics = _comparator(v31_result_path)
    if v31_artifact is not None:
        registered = baseline_binding.get("artifacts")
        registered = registered.get("v3_1_success") if isinstance(registered, Mapping) else None
        if isinstance(registered, Mapping) and registered.get("sha256") != sha256_file(Path(v31_result_path)):
            raise _fail("v31_result_binding_hash_mismatch")

    v4_metrics = _aggregate_measurements(histories)
    identity = baseline_binding.get("identity_consistency")
    envelope = identity.get("status") if isinstance(identity, Mapping) else None
    reasons = _eligibility(
        full=full,
        envelope=envelope,
        baseline=baseline,
        v31=v31_metrics,
        histories=histories,
    )
    eligible = not reasons
    main = _main_table(
        baseline=baseline,
        v31=v31_metrics,
        v4=v4_metrics,
        eligible=eligible,
        reasons=reasons,
    )
    mechanism = _mechanism(
        histories=histories,
        v31_available=v31_artifact is not None,
        eligible=eligible,
    )
    correctness = _correctness(
        main_rows=main["rows"],
        histories=histories,
        eligible=eligible,
    )
    quality, quality_artifact = _quality(quality_overlay_path, eligible=eligible)
    status = (
        "BLOCKED_NON_FORMAL"
        if full.get("schema_version") == FULL_FAILURE_SCHEMA
        else ("PASS" if eligible else "PASS_NON_FORMAL")
    )
    final_result = _seal(
        {
            "schema_version": "membind.paper-eval-v4.final-result.v1",
            "status": status,
            "formal_main_table_eligible": eligible,
            "eligibility_reasons": reasons,
            "run_id": full.get("run_id"),
            "candidate_id": frozen.get("candidate_id"),
            "runner_mode": full.get("runner_mode", "blocked"),
            "source_count": full.get("source_count", 0),
            "performance": v4_metrics,
            "work_volume": _work_volume(histories),
            "baseline_performance": {
                method: main["rows"][index]
                for index, method in enumerate(_METHOD_ORDER[:3])
            },
            "v31_performance": v31_metrics,
            "input_bindings": {
                "frozen_method": _input_binding(frozen_path, frozen),
                "full_run_result": _input_binding(Path(full_run_result_path), full),
                "baseline_binding": _input_binding(baseline_path, baseline_binding),
                "prefix_reference": _input_binding(prefix_path, prefix_reference),
                "v31_result": (
                    _input_binding(Path(v31_result_path), v31_artifact)
                    if v31_result_path is not None and v31_artifact is not None
                    else {"status": _NOT_AVAILABLE}
                ),
                "quality_overlay": (
                    _input_binding(Path(quality_overlay_path), quality_artifact)
                    if quality_overlay_path is not None and quality_artifact is not None
                    else {"status": _NOT_AVAILABLE}
                ),
            },
            "derived_artifact_payload_sha256s": {
                "V4_MAIN_TABLE.json": main["payload_sha256"],
                "V4_MECHANISM_TABLE.json": mechanism["payload_sha256"],
                "V4_CORRECTNESS_TABLE.json": correctness["payload_sha256"],
                "V4_QUALITY_OVERLAY.json": quality["payload_sha256"],
            },
            "construction_rerun": False,
        }
    )
    report = _report(
        result=final_result,
        table=main,
        mechanism=mechanism,
        correctness=correctness,
        quality=quality,
    )
    return {
        "V4_FULL_RESULT.json": final_result,
        "V4_MAIN_TABLE.json": main,
        "V4_MECHANISM_TABLE.json": mechanism,
        "V4_CORRECTNESS_TABLE.json": correctness,
        "V4_QUALITY_OVERLAY.json": quality,
        "V4_FINAL_REPORT.md": report,
    }


def _atomic_write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
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


def write_v4_final_outputs(root: Path, outputs: Mapping[str, object]) -> None:
    """Publish exactly the P7 inventory, rejecting any existing drift."""

    if tuple(outputs) != V4_FINAL_OUTPUT_FILES:
        raise _fail("final_output_inventory_invalid")
    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    extras = {path.name for path in target.iterdir()} - set(V4_FINAL_OUTPUT_FILES)
    if extras:
        raise _fail("existing_output_inventory_invalid")
    for name in V4_FINAL_OUTPUT_FILES:
        expected = outputs[name]
        path = target / name
        if name.endswith(".json"):
            if not isinstance(expected, Mapping):
                raise _fail("final_json_output_invalid")
            _sealed(expected, f"output_{name}")
            if path.exists() and _read_labeled(path, f"existing_{name}") != expected:
                raise _fail(f"existing_output_drift:{name}")
        else:
            if not isinstance(expected, str):
                raise _fail("final_report_output_invalid")
            try:
                expected.encode("ascii")
            except UnicodeEncodeError as error:
                raise _fail("final_report_not_ascii") from error
            if path.exists() and path.read_text(encoding="ascii") != expected:
                raise _fail(f"existing_output_drift:{name}")
    for name in V4_FINAL_OUTPUT_FILES:
        path = target / name
        if path.exists():
            continue
        value = outputs[name]
        if name.endswith(".json"):
            atomic_write_json(path, value)  # type: ignore[arg-type]
        else:
            _atomic_write_text(path, str(value))


def _candidate_ratio(value: object, reference: object) -> float | None:
    try:
        left = float(value)
        right = float(reference)
    except (TypeError, ValueError):
        return None
    if right == 0:
        return None
    return left / right


def _reference(prefix_reference: Mapping[str, Any], source_count: int) -> Mapping[str, Any]:
    if source_count == 20:
        # A1's aligned 0..19 reference is a separate development-only sealed
        # artifact; it must never be inserted into the original 6/12
        # PREFIX_REFERENCE envelope.
        if prefix_reference.get("schema_version") != "membind.paper-eval-v4.a1-development-reference.v1":
            raise _fail("a1_reference_schema_invalid")
        performance = prefix_reference.get("performance")
        if not isinstance(performance, Mapping):
            raise _fail("a1_reference_performance_missing")
        return performance
    if source_count not in {6, 12}:
        raise _fail("candidate_source_count_invalid")
    prefix = "sources_0_5" if source_count == 6 else "sources_0_11"
    selected = prefix_reference.get("prefixes", {}).get(prefix, {})
    if not isinstance(selected, Mapping) or selected.get("source_count") != source_count:
        raise _fail("prefix_reference_identity_drift")
    methods = selected.get("methods", {}) if isinstance(selected, Mapping) else {}
    value = methods.get("MemBind") if isinstance(methods, Mapping) else None
    if not isinstance(value, Mapping):
        raise _fail("membind_reference_missing")
    return value


def _verify_a1_reduction_binding(
    *,
    summary: Mapping[str, object],
    binding: Mapping[str, object],
    reference: Mapping[str, object],
    history_id: str,
    audit_path_override: Path | None = None,
    amendment_path_override: Path | None = None,
) -> None:
    """Recheck the immutable A1 sidecars before deriving a candidate result."""

    if summary.get("protocol_amendment") != "A1":
        raise _fail("a1_candidate_identity_missing")
    if binding.get("protocol_amendment_id") != "A1" or binding.get("source_count") != 20:
        raise _fail("a1_candidate_identity_drift")
    audit_path = (
        str(audit_path_override.resolve())
        if audit_path_override is not None
        else binding.get("audit_absolute_path")
    )
    amendment_path = (
        str(amendment_path_override.resolve())
        if amendment_path_override is not None
        else binding.get("amendment_absolute_path")
    )
    if not isinstance(audit_path, str) or not isinstance(amendment_path, str):
        raise _fail("a1_candidate_sidecar_binding_missing")
    try:
        # Import lazily: the production runner imports the candidate ledger,
        # while the reducer remains usable for all legacy 6/12 artifacts.
        from paper_eval.membind_v4.production_runner import verify_a1_protocol_amendment

        checked = verify_a1_protocol_amendment(
            Path(audit_path), Path(amendment_path), history_id=history_id
        )
    except Exception as error:
        if isinstance(error, V4ReducerError):
            raise
        raise _fail(f"a1_sidecar_binding_invalid:{error}") from None
    for field in (
        "audit_file_sha256",
        "audit_payload_sha256",
        "amendment_file_sha256",
        "amendment_payload_sha256",
        "arrival_trace_sha256",
        "source_inventory_sha256",
        "shared_execution_envelope_sha256",
    ):
        if field in binding and binding.get(field) != checked.get(field):
            raise _fail("a1_candidate_sidecar_binding_drift")
    identity_pairs = (
        ("arrival_trace_sha256", "arrival_trace_sha256"),
        ("source_manifest_sha256", "source_inventory_sha256"),
        ("shared_execution_envelope_sha256", "shared_execution_envelope_sha256"),
    )
    for reference_field, binding_field in identity_pairs:
        if reference.get(reference_field) != binding.get(binding_field):
            raise _fail("a1_candidate_reference_identity_drift")


def reduce_candidate(
    *,
    candidate_root: Path,
    reference_path: Path,
    a1_audit_path: Path | None = None,
    a1_amendment_path: Path | None = None,
) -> dict[str, Any]:
    root = Path(candidate_root).resolve()
    candidate_path = root / "candidate.json"
    summary_path = root / "summary.json"
    selected_reference_path = Path(reference_path).resolve()
    candidate = _sealed(
        _read_labeled(candidate_path, "candidate_manifest"),
        "candidate_manifest",
        schema=_CANDIDATE_MANIFEST_SCHEMA,
    )
    summary = _sealed(
        _read_labeled(summary_path, "candidate_summary"),
        "candidate_summary",
        schema=_CANDIDATE_SUMMARY_SCHEMA,
    )
    candidate_id = summary.get("candidate_id")
    source_count = summary.get("source_count")
    history_id = summary.get("history_id")
    if (
        not isinstance(candidate_id, str)
        or _CANDIDATE_ID.fullmatch(candidate_id) is None
        or candidate.get("candidate_id") != candidate_id
        or root.name != candidate_id
        or isinstance(source_count, bool)
        or not isinstance(source_count, int)
        or candidate.get("source_count") != source_count
        or not isinstance(history_id, str)
        or not history_id
    ):
        raise _fail("candidate_summary_identity_drift")
    reference_schema = (
        "membind.paper-eval-v4.a1-development-reference.v1"
        if source_count == 20
        else PREFIX_REFERENCE_SCHEMA
    )
    prefix_reference = _sealed(
        _read_labeled(selected_reference_path, "prefix_reference"),
        "prefix_reference",
        schema=reference_schema,
    )
    if prefix_reference.get("history_id") != history_id:
        raise _fail("candidate_summary_identity_drift")
    a1_binding = summary.get("a1_binding")
    if source_count == 20:
        if summary.get("protocol_amendment") != "A1" or not isinstance(a1_binding, Mapping):
            raise _fail("a1_candidate_identity_missing")
        if a1_binding.get("protocol_amendment_id") != "A1" or a1_binding.get("source_count") != 20:
            raise _fail("a1_candidate_identity_drift")
        for label, path_key, file_key, payload_key in (
            ("audit", "audit_absolute_path", "audit_file_sha256", "audit_payload_sha256"),
            (
                "amendment",
                "amendment_absolute_path",
                "amendment_file_sha256",
                "amendment_payload_sha256",
            ),
        ):
            selected = a1_binding.get(path_key)
            if not isinstance(selected, str):
                raise _fail("a1_candidate_sidecar_binding")
            sidecar = Path(selected)
            try:
                body = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise _fail("a1_candidate_sidecar_binding") from error
            if not isinstance(body, Mapping):
                raise _fail("a1_candidate_sidecar_binding")
            digest = body.get("payload_sha256")
            unsigned = dict(body)
            unsigned.pop("payload_sha256", None)
            if (
                digest != a1_binding.get(payload_key)
                or payload_sha256(unsigned) != digest
                or sha256_file(sidecar) != a1_binding.get(file_key)
            ):
                raise _fail("a1_sidecar_binding_invalid")
    if candidate.get("status") not in {"RUNNING", "COMPLETED"}:
        raise _fail("candidate_manifest_status_invalid")
    valid_reference_statuses = {"PASS"}
    if source_count == 20:
        valid_reference_statuses.add("PASS_DEVELOPMENT_ONLY")
    if prefix_reference.get("status") not in valid_reference_statuses:
        raise _fail("prefix_reference_status_invalid")
    result = summary.get("result") if isinstance(summary.get("result"), Mapping) else {}
    if (
        ("source_count" in result and result.get("source_count") != source_count)
        or ("stream_id" in result and result.get("stream_id") != history_id)
    ):
        raise _fail("candidate_summary_identity_drift")
    reference = _reference(prefix_reference, source_count)
    if source_count == 20:
        _verify_a1_reduction_binding(
            summary=summary,
            binding=a1_binding,  # type: ignore[arg-type]
            reference=prefix_reference,
            history_id=history_id,
            audit_path_override=a1_audit_path,
            amendment_path_override=a1_amendment_path,
        )
    performance = result.get("performance") if isinstance(result, Mapping) else {}
    if not isinstance(performance, Mapping):
        performance = {}
    makespan = performance.get("makespan_ns", summary.get("makespan_ns"))
    freshness = performance.get("p95_freshness_ns", summary.get("p95_freshness_ns"))
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.candidate-reduction.v1",
        "candidate_id": summary.get("candidate_id"),
        "source_count": summary.get("source_count"),
        "status": summary.get("status"),
        "input_bindings": {
            "candidate": _input_binding(candidate_path, candidate),
            "summary": _input_binding(summary_path, summary),
            "prefix_reference": _input_binding(
                selected_reference_path, prefix_reference
            ),
            **(
                {"a1": deepcopy(dict(a1_binding))}
                if source_count == 20 and isinstance(a1_binding, Mapping)
                else {}
            ),
        },
        "mechanism": {
            "qualified_node_resolve_count": summary.get("qualified_node_resolve_count", 0),
            "speculation_launch_count": summary.get("speculation_launch_count", 0),
            "exact_validation_completed_count": summary.get(
                "exact_validation_completed_count", 0
            ),
            "semantic_hit_count": summary.get("semantic_hit_count", 0),
            "semantic_miss_count": summary.get("semantic_miss_count", 0),
            "overlap_count": summary.get("overlap_count", 0),
            "hidden_critical_time_ns": summary.get("hidden_critical_time_ns", 0),
            "direct_violation_count": summary.get("direct_violation_count", 0),
        },
        "performance": {
            "makespan_ns": makespan,
            "p95_freshness_ns": freshness,
            "reference_makespan_ns": reference.get("makespan_ns"),
            "reference_p95_freshness_ns": reference.get("freshness_ns_p95"),
            "makespan_ratio": _candidate_ratio(makespan, reference.get("makespan_ns")),
            "freshness_p95_ratio": _candidate_ratio(
                freshness, reference.get("freshness_ns_p95")
            ),
        },
    }
    decision_input = {
        **dict(summary),
        "freshness_p95_ratio": body["performance"]["freshness_p95_ratio"] or 1.0,
        "makespan_ratio": body["performance"]["makespan_ratio"] or 1.0,
    }
    body["decision"] = assess_candidate(decision_input)
    body["payload_sha256"] = payload_sha256(body)
    atomic_write_json(root / "reduction.json", body)
    return body


__all__ = [
    "V4_FINAL_OUTPUT_FILES",
    "V4ReducerError",
    "reduce_candidate",
    "reduce_v4_final",
    "write_v4_final_outputs",
]
