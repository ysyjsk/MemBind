"""Deterministic aggregation and rendering for the development baselines.

The caller must supply already-verified U0/A0/P(C=2) rows and the sealed
graph-quality overlay report.  This module performs no filesystem or network
I/O, which keeps metric derivation independently testable.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .artifacts import payload_sha256
from .baseline_suite import DEVELOPMENT_HISTORIES


METHODS = ("U0", "A0", "P(C=2)")
WORK_FIELDS = (
    "llm_logical_calls",
    "llm_input_tokens",
    "llm_output_tokens",
    "llm_transport_attempts",
    "embedding_calls",
    "embedding_items",
    "db_operations",
    "db_transactions",
    "candidate_query_count",
    "candidate_count",
)

_REPORT_RUN_ID = re.compile(r"^report-[a-z0-9][a-z0-9-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DevelopmentBaselineReportError(ValueError):
    """The final development report input is incomplete or inconsistent."""


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise DevelopmentBaselineReportError(f"{field} is invalid")
    return value


def _number(value: object, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise DevelopmentBaselineReportError(f"{field} is invalid")
    return float(value)


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DevelopmentBaselineReportError(f"{field} is invalid")
    return value


def _probability(value: object, *, field: str) -> float:
    result = _number(value, field=field)
    if not 0.0 <= result <= 1.0:
        raise DevelopmentBaselineReportError(f"{field} is invalid")
    return result


def _nearest_rank(values: Sequence[int], probability: float) -> int:
    if not values:
        raise DevelopmentBaselineReportError("latency inventory is empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _distribution(values: Sequence[int]) -> dict[str, int | float]:
    observed = [
        _nonnegative_int(value, field="freshness sample") for value in values
    ]
    if not observed:
        raise DevelopmentBaselineReportError("latency inventory is empty")
    ordered = sorted(observed)
    p50 = _nearest_rank(ordered, 0.50)
    p99 = _nearest_rank(ordered, 0.99)
    return {
        "count": len(ordered),
        "mean": sum(ordered) / len(ordered),
        "p50": p50,
        "p90": _nearest_rank(ordered, 0.90),
        "p95": _nearest_rank(ordered, 0.95),
        "p99": p99,
        "max": ordered[-1],
        "tail_amplification": p99 / p50 if p50 else 0.0,
    }


def _quality_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(report, Mapping) or report.get("status") != "PASS":
        raise DevelopmentBaselineReportError("graph-quality report is not PASS")
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        raise DevelopmentBaselineReportError("graph-quality summary is missing")
    if summary.get("heldout_data_accessed") is not False:
        raise DevelopmentBaselineReportError("graph-quality held-out policy drift")
    by_method = summary.get("by_method")
    if not isinstance(by_method, Mapping) or set(by_method) != set(METHODS):
        raise DevelopmentBaselineReportError("graph-quality method inventory drift")
    projected: dict[str, Any] = {}
    for method in METHODS:
        value = by_method[method]
        if not isinstance(value, Mapping):
            raise DevelopmentBaselineReportError("graph-quality method row is invalid")
        question_count = _nonnegative_int(
            value.get("question_count"), field="graph-quality question count"
        )
        valid_count = _nonnegative_int(
            value.get("valid_judge_count"), field="valid Judge count"
        )
        invalid_count = _nonnegative_int(
            value.get("invalid_judge_count"), field="invalid Judge count"
        )
        if question_count != len(DEVELOPMENT_HISTORIES) or (
            valid_count + invalid_count != question_count
        ):
            raise DevelopmentBaselineReportError(
                "graph-quality Judge denominator is inconsistent"
            )
        qa = value.get("qa_accuracy")
        projected[method] = {
            "question_count": question_count,
            "valid_judge_count": valid_count,
            "invalid_judge_count": invalid_count,
            "qa_accuracy": (
                None if qa is None else _probability(qa, field="graph-quality QA")
            ),
            "edge_attributed_source_coverage_at_10_macro": _probability(
                value.get("edge_attributed_source_coverage_at_10_macro"),
                field="edge-attributed source coverage",
            ),
        }
    return {
        "claim_label": _text(summary.get("claim_label"), field="claim label"),
        "quality_identity": deepcopy(summary.get("quality_identity")),
        "runtime_identity_sha256": _text(
            summary.get("runtime_identity_sha256"),
            field="runtime identity SHA256",
        ),
        "by_method": projected,
    }


def _method_summary(
    method: str,
    rows: Sequence[Mapping[str, Any]],
    graph_quality: Mapping[str, Any],
) -> dict[str, Any]:
    if len(rows) != len(DEVELOPMENT_HISTORIES):
        raise DevelopmentBaselineReportError("baseline history inventory is incomplete")
    if tuple(row.get("history_id") for row in rows) != DEVELOPMENT_HISTORIES:
        raise DevelopmentBaselineReportError("baseline history order drift")
    freshness: list[int] = []
    work = {field: 0 for field in WORK_FIELDS}
    graph_nodes = 0
    graph_relationships = 0
    makespan_ns = 0
    episode_count = 0
    qa_values: list[float] = []
    recall_values: list[float] = []
    backlogs: list[int] = []
    active_calls: list[int] = []
    overlap: list[bool] = []
    worker_counts: list[int] = []
    direct_violations: list[int] = []
    direct_statuses: set[str] = set()
    history_rows: list[dict[str, Any]] = []
    for row in rows:
        if row.get("method") != method:
            raise DevelopmentBaselineReportError("baseline method identity drift")
        metrics = row.get("metrics")
        final_graph = row.get("final_graph")
        schedule = row.get("schedule_summary")
        volume = row.get("work_volume")
        samples = row.get("freshness_samples_ns")
        if (
            not isinstance(metrics, Mapping)
            or not isinstance(final_graph, Mapping)
            or not isinstance(schedule, Mapping)
            or not isinstance(volume, Mapping)
            or not isinstance(samples, Sequence)
            or isinstance(samples, (str, bytes))
        ):
            raise DevelopmentBaselineReportError("baseline row is incomplete")
        missing_work_fields = [field for field in WORK_FIELDS if field not in volume]
        if missing_work_fields:
            raise DevelopmentBaselineReportError(
                "baseline work volume is incomplete: "
                + ", ".join(missing_work_fields)
            )
        count = _nonnegative_int(row.get("episode_count"), field="episode count")
        observed_samples = [
            _nonnegative_int(value, field="freshness sample") for value in samples
        ]
        if count < 1 or len(observed_samples) != count:
            raise DevelopmentBaselineReportError("episode latency count drift")
        episode_count += count
        freshness.extend(observed_samples)
        makespan_ns += _nonnegative_int(
            metrics.get("makespan_ns"), field="makespan"
        )
        qa = _probability(metrics.get("qa_accuracy"), field="QA accuracy")
        recall = _probability(
            metrics.get("evidence_recall_at_10"), field="Evidence Recall@10"
        )
        qa_values.append(qa)
        recall_values.append(recall)
        backlog = metrics.get("max_backlog")
        if backlog is not None:
            backlogs.append(_nonnegative_int(backlog, field="max backlog"))
        direct = metrics.get("direct_violations")
        if direct is not None:
            direct_violations.append(
                _nonnegative_int(direct, field="direct violations")
            )
        direct_statuses.add(
            _text(
                metrics.get("direct_violations_status", "MEASURED"),
                field="direct violation status",
            )
        )
        for field in WORK_FIELDS:
            work[field] += _nonnegative_int(volume[field], field=field)
        graph_nodes += _nonnegative_int(
            final_graph.get("node_count"), field="final graph node count"
        )
        graph_relationships += _nonnegative_int(
            final_graph.get("relationship_count"),
            field="final graph relationship count",
        )
        if final_graph.get("episode_names_match_expected") is not True:
            raise DevelopmentBaselineReportError("final episode corpus drift")
        active_calls.append(
            _nonnegative_int(
                schedule.get("max_active_calls"), field="max active calls"
            )
        )
        worker_counts.append(
            _nonnegative_int(
                schedule.get("configured_worker_count"),
                field="configured worker count",
            )
        )
        overlap_value = schedule.get("whole_update_interval_overlap_observed")
        if type(overlap_value) is not bool:
            raise DevelopmentBaselineReportError("overlap evidence is invalid")
        overlap.append(overlap_value)
        history_rows.append(
            {
                "history_id": row["history_id"],
                "episode_count": count,
                "qa_accuracy": qa,
                "evidence_recall_at_10": recall,
                "p95_freshness_ns": _nearest_rank(observed_samples, 0.95),
                "p99_freshness_ns": _nearest_rank(observed_samples, 0.99),
                "makespan_ns": metrics["makespan_ns"],
                "max_backlog": backlog,
                "node_count": final_graph["node_count"],
                "relationship_count": final_graph["relationship_count"],
                "result_payload_sha256": _text(
                    row.get("result_payload_sha256"), field="result SHA256"
                ),
            }
        )
    if episode_count < 1 or makespan_ns < 1:
        raise DevelopmentBaselineReportError("baseline aggregate is empty")
    graph = graph_quality[method]
    return {
        "history_count": len(rows),
        "episode_count": episode_count,
        "qa_accuracy_macro": sum(qa_values) / len(qa_values),
        "evidence_recall_at_10_macro": sum(recall_values) / len(recall_values),
        "graph_quality_qa_accuracy": graph["qa_accuracy"],
        "graph_quality_valid_judge_count": graph["valid_judge_count"],
        "graph_quality_invalid_judge_count": graph["invalid_judge_count"],
        "edge_attributed_source_coverage_at_10_macro": graph[
            "edge_attributed_source_coverage_at_10_macro"
        ],
        "freshness_ns": _distribution(freshness),
        "makespan_ns": makespan_ns,
        "successful_goodput_episodes_per_second": (
            episode_count / (makespan_ns / 1_000_000_000)
        ),
        "max_backlog": max(backlogs) if backlogs else None,
        "max_backlog_status": (
            "OBSERVED" if backlogs else "NOT_APPLICABLE_SERIAL_BASELINE"
        ),
        "direct_violations": (
            sum(direct_violations)
            if len(direct_violations) == len(rows)
            else None
        ),
        "direct_violations_statuses": sorted(direct_statuses),
        "configured_worker_counts": sorted(set(worker_counts)),
        "observed_max_active_calls": max(active_calls),
        "overlap_observed": any(overlap),
        "work_volume": work,
        "final_graph": {
            "node_count_sum": graph_nodes,
            "relationship_count_sum": graph_relationships,
        },
        "histories": history_rows,
    }


def build_development_baseline_report(
    *,
    report_run_id: str,
    native_run_id: str,
    suite_run_id: str,
    overlay_run_id: str,
    baseline_rows: Sequence[Mapping[str, Any]],
    graph_quality_report: Mapping[str, Any],
    artifact_paths: Mapping[str, str],
) -> dict[str, Any]:
    """Aggregate the fixed 12 development rows without changing metrics."""

    if _REPORT_RUN_ID.fullmatch(report_run_id) is None:
        raise DevelopmentBaselineReportError("report run id is invalid")
    for value, field in (
        (native_run_id, "native run id"),
        (suite_run_id, "suite run id"),
        (overlay_run_id, "overlay run id"),
    ):
        _text(value, field=field)
    rows = tuple(baseline_rows)
    expected = tuple(
        (method, history_id)
        for method in METHODS
        for history_id in DEVELOPMENT_HISTORIES
    )
    if tuple((row.get("method"), row.get("history_id")) for row in rows) != expected:
        raise DevelopmentBaselineReportError("baseline result inventory drift")
    paths = {key: _text(value, field=f"artifact path {key}") for key, value in artifact_paths.items()}
    if set(paths) != {"native", "suite", "graph_quality"}:
        raise DevelopmentBaselineReportError("artifact path inventory drift")
    quality = _quality_summary(graph_quality_report)
    methods = {
        method: _method_summary(
            method,
            [row for row in rows if row["method"] == method],
            quality["by_method"],
        )
        for method in METHODS
    }
    if len({methods[method]["episode_count"] for method in METHODS}) != 1:
        raise DevelopmentBaselineReportError("baseline workload size is unfair")
    u0_work = methods["U0"]["work_volume"]
    for method in METHODS:
        ratios: dict[str, float | None] = {}
        for field in WORK_FIELDS:
            denominator = u0_work[field]
            ratios[field] = (
                methods[method]["work_volume"][field] / denominator
                if denominator
                else None
            )
        methods[method]["work_volume_ratio_vs_u0"] = ratios
    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v3.development-baseline-report.v1",
        "status": "PASS",
        "report_run_id": report_run_id,
        "native_run_id": native_run_id,
        "suite_run_id": suite_run_id,
        "overlay_run_id": overlay_run_id,
        "data_role": "DEVELOPMENT_EXPOSED",
        "heldout_data_accessed": False,
        "data_access_disclosure": {
            "evaluated_role": "DEVELOPMENT_EXPOSED",
            "live_graph_quality_input": "ISOLATED_FOUR_RECORD_ARTIFACT",
            "live_graph_quality_combined_container_opened": False,
            "input_materialization_scanned_combined_container": True,
            "project_lifetime_no_combined_container_scan_claim": False,
            "pilot_or_final_records_evaluated": False,
        },
        "method_order": list(METHODS),
        "history_order": list(DEVELOPMENT_HISTORIES),
        "metric_policy": {
            "primary": [
                "qa_accuracy_macro",
                "evidence_recall_at_10_macro",
                "direct_violations",
                "freshness_ns.p95",
                "successful_goodput_episodes_per_second",
                "makespan_ns",
            ],
            "secondary": ["freshness_ns.p99", "max_backlog"],
            "graph_quality": "predefined_read_only_diagnostic_overlay",
        },
        "claim_labels": [
            "PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED",
            quality["claim_label"],
        ],
        "graph_quality_identity": quality["quality_identity"],
        "graph_quality_runtime_identity_sha256": quality[
            "runtime_identity_sha256"
        ],
        "methods": methods,
        "artifact_paths": paths,
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def _seconds(value: object) -> str:
    return f"{_number(value, field='duration') / 1_000_000_000:.3f}"


def _score(value: object) -> str:
    if value is None:
        return "N/A"
    return f"{_number(value, field='score'):.3f}"


def render_development_baseline_markdown(report: Mapping[str, Any]) -> str:
    """Render a reviewer-facing Markdown report from the sealed JSON data."""

    if not isinstance(report, Mapping) or report.get("status") != "PASS":
        raise DevelopmentBaselineReportError("report is not renderable")
    methods = report.get("methods")
    if not isinstance(methods, Mapping) or tuple(methods) != METHODS:
        raise DevelopmentBaselineReportError("report method inventory drift")
    lines = [
        "# MemBind 三基础 Baseline Development 实验报告",
        "",
        "本报告汇总 Native U0、Async-Serial A0 与 Whole-Update Parallel P(C=2) "
        "在同一组 development/calibration histories 上的结果。没有访问 PILOT 或 "
        "FINAL_PAPER_TEST；因此这是系统 characterization 与方法设计依据，不是最终论文显著性结论。",
        "",
        "## 运行身份",
        "",
        f"- Native run: `{report['native_run_id']}`",
        f"- Three-baseline suite: `{report['suite_run_id']}`",
        f"- Graph-quality overlay: `{report['overlay_run_id']}`",
        f"- Report payload SHA256: `{report['payload_sha256']}`",
        "- Claim boundary: `PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`",
        "- Graph QA boundary: "
        "`PROTOCOL_RUBRIC_COMPATIBLE_LOCAL_JUDGE_DIAGNOSTIC`",
        "",
        "## 数据访问边界",
        "",
        "本次 live graph-quality evaluation 只打开固定四条记录、188 episodes 的 "
        "`DEVELOPMENT_EXPOSED` 独立 artifact，不打开 combined LongMemEval container，"
        "也未评估任何已分配为 PILOT 或 FINAL_PAPER_TEST 的记录。",
        "",
        "该独立 artifact 的一次性 materialization 曾从 combined source container "
        "导出四个预先指定的 development IDs。因此本报告不作“项目生命周期从未扫描 "
        "combined container”的更强声明；这里的 `heldout_data_accessed=false` 仅表示本次 "
        "evaluation 没有评估 PILOT/FINAL role 数据。",
        "",
        "## 核心结果",
        "",
        "| Method | Episodes | QA Accuracy | Session Evidence Recall@10 | Graph-native QA | Edge source coverage@10 | P95 freshness (s) | P99 freshness (s) | Makespan (s) | Goodput (ep/s) | Max backlog |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for method in METHODS:
        value = methods[method]
        lines.append(
            "| "
            f"{method} | {value['episode_count']} | "
            f"{_score(value['qa_accuracy_macro'])} | "
            f"{_score(value['evidence_recall_at_10_macro'])} | "
            f"{_score(value['graph_quality_qa_accuracy'])} | "
            f"{_score(value['edge_attributed_source_coverage_at_10_macro'])} | "
            f"{_seconds(value['freshness_ns']['p95'])} | "
            f"{_seconds(value['freshness_ns']['p99'])} | "
            f"{_seconds(value['makespan_ns'])} | "
            f"{value['successful_goodput_episodes_per_second']:.6f} | "
            f"{value['max_backlog'] if value['max_backlog'] is not None else 'N/A'} |"
        )
    lines.extend(
        [
            "",
            "这里的 `QA Accuracy` 与 `Session Evidence Recall@10` 是三方法共同的冻结 "
            "session-reader/Judge 路径；`Graph-native QA` 是完成 construction 后统一执行的 "
            "top-20 temporal facts + top-20 entity summaries 只读诊断 overlay。Edge source "
            "coverage 不是官方 Session Evidence Recall@10，不能混写。",
            "",
            "当前 suite 的 arrival timestamp 语义不同：U0 在每次 serial service 前记录 "
            "arrival，A0/P 则先发出整个 history 的 intent burst。因此本轮 P95/P99 不能计算跨方法 "
            "freshness delta；这些值只描述各 execution mode 的 observed 行为。相同 188 episodes "
            "下的 aggregate makespan/goodput 只能作为 burst-drain capacity 的 descriptive "
            "directional signal，不是 open-loop online latency 或显著性结论。",
            "",
            "## 调度与正确性证据",
            "",
            "| Method | Workers | Observed max active updates | Whole-update overlap | Direct violations | Direct-violation status |",
            "|---|---|---:|---|---:|---|",
        ]
    )
    for method in METHODS:
        value = methods[method]
        lines.append(
            f"| {method} | {','.join(str(item) for item in value['configured_worker_counts'])} | "
            f"{value['observed_max_active_calls']} | "
            f"{'yes' if value['overlap_observed'] else 'no'} | "
            f"{value['direct_violations'] if value['direct_violations'] is not None else 'N/A'} | "
            f"{', '.join(value['direct_violations_statuses'])} |"
        )
    lines.extend(
        [
            "",
            "A0 的目标是观察 caller 去阻塞以后是否形成 freshness backlog；它不是吞吐优化。"
            "P(C=2) 只证明粗粒度 whole-update 并发在当前 development screening 中的系统行为。"
            "如果 direct violations 未测量，报告保留 N/A，不能把它解释为 0。",
            "",
            "## Work Volume 与图规模",
            "",
            "| Method | LLM calls | Input tokens | Output tokens | Embedding calls/items | DB operations/transactions | Candidate count | Nodes | Relationships |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for method in METHODS:
        value = methods[method]
        work = value["work_volume"]
        graph = value["final_graph"]
        lines.append(
            f"| {method} | {work['llm_logical_calls']} | {work['llm_input_tokens']} | "
            f"{work['llm_output_tokens']} | {work['embedding_calls']}/{work['embedding_items']} | "
            f"{work['db_operations']}/{work['db_transactions']} | {work['candidate_count']} | "
            f"{graph['node_count_sum']} | {graph['relationship_count_sum']} |"
        )
    lines.extend(
        [
            "",
            "性能差异必须与 work volume 和最终图规模一起解释。若某方法通过少做 LLM、"
            "embedding、DB 或 graph work 获得加速，不能无条件称为 pure scheduling speedup。",
            "",
            "## Per-history 结果",
            "",
            "| Method | History | Episodes | QA | R@10 | P95 freshness (s) | P99 freshness (s) | Makespan (s) | Max backlog | Nodes | Relationships |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for method in METHODS:
        for row in methods[method]["histories"]:
            lines.append(
                f"| {method} | `{row['history_id']}` | {row['episode_count']} | "
                f"{_score(row['qa_accuracy'])} | {_score(row['evidence_recall_at_10'])} | "
                f"{_seconds(row['p95_freshness_ns'])} | {_seconds(row['p99_freshness_ns'])} | "
                f"{_seconds(row['makespan_ns'])} | "
                f"{row['max_backlog'] if row['max_backlog'] is not None else 'N/A'} | "
                f"{row['node_count']} | {row['relationship_count']} |"
            )
    paths = report["artifact_paths"]
    lines.extend(
        [
            "",
            "## 原始制品",
            "",
            f"- Native U0: `{paths['native']}`",
            f"- A0/P suite: `{paths['suite']}`",
            f"- Graph-quality overlay: `{paths['graph_quality']}`",
            "",
            "Level-0 JSONL 与每个 checkpoint/result 是可离线重算的 source of truth；本报告只"
            "是这些 sealed artifacts 的确定性投影。Reader/Judge 原文保留在 git-ignored 私密制品中。",
            "",
            "## 解释边界",
            "",
            "- 这 4 个问题都是 development/calibration 数据，不能据此报告论文置信区间或显著性。",
            "- 本地 Qwen Reader/Judge 与公开 LongMemEval/Zep 的 GPT-4o 配置不同，因此只能写 "
            "`PROTOCOL_ALIGNED_NOT_NUMERICALLY_MATCHED`。",
            "- 低 QA 若同时伴随高 Session Evidence Recall@10，应归因到 retrieval 之后的 "
            "context assembly、Reader 或 Judge 路径，不能表述为 Graphiti 丢失了 gold sessions。",
            "- Graph-native overlay 是预定义诊断，不替换冻结主指标，也不访问 gold answer 进行 retrieval。",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "DevelopmentBaselineReportError",
    "METHODS",
    "WORK_FIELDS",
    "build_development_baseline_report",
    "render_development_baseline_markdown",
]
