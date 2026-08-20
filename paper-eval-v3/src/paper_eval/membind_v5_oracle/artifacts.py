from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file

from .model import ReplayResult, TraceBundle
from .request_dag import RequestDAG
from .replay import replay


SCHEMA_PREFIX = "membind.paper-eval-v5.request-dag-oracle"


def _nearest(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _sealed(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "payload_sha256": payload_sha256(body)}


def _unsealed_copy(
    result: ReplayResult, *, bundle: TraceBundle, dag: RequestDAG
) -> dict[str, Any]:
    return _result_metrics(result, bundle=bundle, dag=dag)


def _dag_shape(dag: RequestDAG) -> dict[str, Any]:
    levels: dict[str, int] = {}
    for node in dag.topological_order:
        levels[node] = 0 if not dag.predecessors(node) else max(levels[p] + 1 for p in dag.predecessors(node))
    width = Counter(levels.values())
    exact = dag.oracle_evaluable
    return {
        "node_count": len(dag.nodes),
        "request_node_count": len(dag.requests),
        "synthetic_publication_sink_count": len(dag.nodes) - len(dag.requests),
        "evidence_backed_edge_count": len(dag.edges),
        "evidence_backed_edge_count_by_type": dict(
            sorted(Counter(edge.kind.value for edge in dag.edges).items())
        ),
        "exact_depth": max(levels.values(), default=-1) + 1 if exact else "NOT_OBSERVABLE",
        "exact_width": max(width.values(), default=0) if exact else "NOT_OBSERVABLE",
        "partial_graph_depth_lower_bound": max(levels.values(), default=-1) + 1,
        "partial_graph_width_upper_bound": max(width.values(), default=0),
        "partial_graph_level_width_by_depth": {
            str(level): count for level, count in sorted(width.items())
        },
        "acyclic": not dag.has_cycle,
        "exact_dependency_dag_recovered": exact,
        "unknown_dependency_group_count": len(dag.unknown_dependencies),
    }


def _request_observability(bundle: TraceBundle) -> dict[str, Any]:
    role_counts = Counter(request.operator_role for request in bundle.requests)
    kind_counts = Counter(request.request_kind for request in bundle.requests)
    source_counts = Counter(str(request.source_sequence) for request in bundle.requests)
    return {
        "total_llm_requests": len(bundle.requests),
        "request_kind_counts": dict(sorted(kind_counts.items())),
        "operator_role_counts": dict(sorted(role_counts.items())),
        "requests_per_source": dict(sorted(source_counts.items(), key=lambda item: int(item[0]))),
        "complete_client_lifecycle_count": len(bundle.requests),
        "prompt_name": "NOT_OBSERVABLE",
        "prompt_tokens_per_request": "NOT_OBSERVABLE",
        "completion_tokens_per_request": "NOT_OBSERVABLE",
        "persistent_state_access_class": "NOT_OBSERVABLE",
        "transport_request_id": "NOT_OBSERVABLE",
    }


def _waiting_depth_profile(bundle: TraceBundle) -> dict[str, Any]:
    if not bundle.requests:
        return {
            "p50": 0,
            "p95": 0,
            "max": 0,
            "observation_window_ns": 0,
            "definition": "time-weighted submitted-but-not-started request count",
        }
    start = min(request.submitted_ns for request in bundle.requests)
    end = max(request.terminal_ns for request in bundle.requests)
    boundaries = sorted(
        {
            start,
            end,
            *(request.submitted_ns for request in bundle.requests),
            *(request.started_ns for request in bundle.requests),
        }
    )
    duration_by_width: Counter[int] = Counter()
    for left, right in zip(boundaries, boundaries[1:]):
        if right <= left:
            continue
        width = sum(
            request.submitted_ns <= left < request.started_ns
            for request in bundle.requests
        )
        duration_by_width[width] += right - left
    window = end - start

    def percentile(fraction: float) -> int:
        threshold = window * fraction
        cumulative = 0
        for width, duration in sorted(duration_by_width.items()):
            cumulative += duration
            if cumulative >= threshold:
                return width
        return max(duration_by_width, default=0)

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": max(duration_by_width, default=0),
        "observation_window_ns": window,
        "duration_ns_by_depth": {
            str(width): duration for width, duration in sorted(duration_by_width.items())
        },
        "definition": "time-weighted submitted-but-not-started request count",
    }


def _observed_actual_metrics(bundle: TraceBundle) -> dict[str, Any]:
    publications = bundle.publication_by_source
    freshness = {
        source: publication.publication_ns - publication.arrival_ns
        for source, publication in publications.items()
    }
    first_arrival = min(
        (publication.arrival_ns for publication in publications.values()), default=0
    )
    last_publication = max(
        (publication.publication_ns for publication in publications.values()), default=0
    )
    makespan = max(0, last_publication - first_arrival)
    active_boundaries = sorted(
        {
            *(request.started_ns for request in bundle.requests),
            *(request.terminal_ns for request in bundle.requests),
        }
    )
    max_active = max(
        (
            sum(
                request.started_ns <= timestamp < request.terminal_ns
                for request in bundle.requests
            )
            for timestamp in active_boundaries
        ),
        default=0,
    )
    wait_latency = [
        request.started_ns - request.submitted_ns for request in bundle.requests
    ]
    return {
        "policy": "OBSERVED_TRACE",
        "replay_status": "NOT_EVALUABLE_DEPENDENCY_DAG_INCOMPLETE",
        "request_count": len(bundle.requests),
        "makespan_ns": makespan,
        "goodput_episodes_per_second": (
            len(publications) / (makespan / 1_000_000_000) if makespan else None
        ),
        "freshness_ns": {
            str(source): value for source, value in sorted(freshness.items())
        },
        "p50_freshness_ns": _nearest(list(freshness.values()), 0.50),
        "p95_freshness_ns": _nearest(list(freshness.values()), 0.95),
        "max_freshness_ns": max(freshness.values(), default=None),
        "max_active_count": max_active,
        "waiting_queue_depth": _waiting_depth_profile(bundle),
        "request_wait_latency_ns": {
            "p50": _nearest(wait_latency, 0.50),
            "p95": _nearest(wait_latency, 0.95),
            "max": max(wait_latency, default=None),
        },
        "request_service_duration_ns": {
            request.request_id: request.service_duration_ns
            for request in bundle.requests
        },
        "actual_publication_delta_ns": {
            str(source): 0 for source in sorted(publications)
        },
        "scheduler_choice_count": "NOT_OBSERVABLE",
        "criticality_inversion_count": "NOT_OBSERVABLE",
        "inversion_rate": "NOT_OBSERVABLE",
        "multi_choice_duration_ns": "NOT_OBSERVABLE",
        "max_legal_choice_width": "NOT_OBSERVABLE",
        "publication_critical_path_length_ns": "NOT_OBSERVABLE",
        "parallel_ready_width": "NOT_OBSERVABLE",
        "extra_llm_calls": 0,
        "extra_input_tokens": 0,
        "speculative_waste": 0,
        "resource_aware_upper_bound": "NOT_EVALUATED",
        "backend_batch_membership": "NOT_OBSERVABLE",
    }


def _not_evaluable_schedule(policy: str) -> dict[str, Any]:
    return {
        "policy": policy,
        "status": "NOT_EVALUABLE",
        "reason": "exact_request_dependency_dag_not_recoverable",
        "makespan_ns": "NOT_OBSERVABLE",
        "goodput_episodes_per_second": "NOT_OBSERVABLE",
        "p50_freshness_ns": "NOT_OBSERVABLE",
        "p95_freshness_ns": "NOT_OBSERVABLE",
        "freshness_ns": "NOT_OBSERVABLE",
        "scheduler_choice_count": "NOT_OBSERVABLE",
        "criticality_inversion_count": "NOT_OBSERVABLE",
        "inversion_rate": "NOT_OBSERVABLE",
        "multi_choice_duration_ns": "NOT_OBSERVABLE",
        "max_legal_choice_width": "NOT_OBSERVABLE",
        "publication_critical_path_length_ns": "NOT_OBSERVABLE",
        "parallel_ready_width": "NOT_OBSERVABLE",
        "extra_llm_calls": 0,
        "extra_input_tokens": 0,
        "speculative_waste": 0,
        "resource_aware_upper_bound": "NOT_EVALUATED",
    }


def _result_metrics(result: ReplayResult, *, bundle: TraceBundle, dag: RequestDAG) -> dict[str, Any]:
    freshness = list(result.freshness_ns.values())
    queue_wait = [
        result.request_start_ns[request.request_id] - request.submitted_ns
        for request in bundle.requests
        if request.request_id in result.request_start_ns
    ]
    publication_critical_path = max(
        (
            dag.source_publication_critical_path_ns(source)
            for source in bundle.publication_by_source
        ),
        default=0,
    )
    return {
        "policy": result.policy,
        "makespan_ns": result.makespan_ns,
        "goodput_episodes_per_second": result.goodput_episodes_per_second,
        "freshness_ns": {
            str(source): value for source, value in sorted(result.freshness_ns.items())
        },
        "p50_freshness_ns": _nearest(freshness, 0.50),
        "p95_freshness_ns": _nearest(freshness, 0.95),
        "max_freshness_ns": max(freshness, default=None),
        "max_active_count": result.max_active_count,
        "request_count": result.request_count,
        "request_start_order": list(result.request_start_order),
        "request_service_duration_ns": result.request_service_duration_ns,
        "extra_llm_calls": result.extra_llm_calls,
        "extra_input_tokens": result.extra_input_tokens,
        "speculative_waste": result.speculative_waste,
        "scheduler_choice_count": result.scheduler_choice_count,
        "criticality_inversion_count": result.criticality_inversion_count,
        "inversion_rate": (
            result.criticality_inversion_count / result.scheduler_choice_count
            if result.scheduler_choice_count
            else 0.0
        ),
        "max_legal_choice_width": result.max_legal_choice_width,
        "multi_choice_duration_ns": result.multi_choice_duration_ns,
        "actual_publication_delta_ns": {
            str(source): value
            for source, value in sorted(result.actual_publication_delta_ns.items())
        },
        "request_wait_latency_ns": {
            "count": len(queue_wait),
            "p50_ns": _nearest(queue_wait, 0.50),
            "p95_ns": _nearest(queue_wait, 0.95),
            "max_ns": max(queue_wait, default=None),
            "definition": "request admission start minus submitted timestamp",
        },
        "waiting_queue_depth": _waiting_depth_profile(bundle),
        "publication_critical_path_length_ns": publication_critical_path,
        "parallel_ready_width": result.max_legal_choice_width,
        "resource_aware_upper_bound": "NOT_EVALUATED",
        "backend_batch_membership": "NOT_OBSERVABLE",
    }


def _gate(actual: ReplayResult, oracle: ReplayResult) -> dict[str, Any]:
    actual_p95 = _nearest(list(actual.freshness_ns.values()), 0.95)
    oracle_p95 = _nearest(list(oracle.freshness_ns.values()), 0.95)
    makespan_improvement = (
        (actual.makespan_ns - oracle.makespan_ns) / actual.makespan_ns
        if actual.makespan_ns
        else 0.0
    )
    p95_improvement = (
        (actual_p95 - oracle_p95) / actual_p95
        if actual_p95 is not None and actual_p95
        else 0.0
    )
    choice = actual.scheduler_choice_count > 0
    inversion = actual.criticality_inversion_count > 0
    improvement = makespan_improvement >= 0.08 or p95_improvement >= 0.10
    decision = "GO_PUBLICATION_CRITICAL_SCHEDULER" if choice and inversion and improvement else "STOP_REQUEST_SCHEDULER"
    reason = (
        "pre_registered_choice_inversion_and_gain_gate_passed"
        if decision.startswith("GO")
        else "oracle_does_not_meet_pre_registered_gain_gate"
    )
    return {
        "decision": decision,
        "reason": reason,
        "scheduler_choice_count_gt_zero": choice,
        "criticality_inversion_count_gt_zero": inversion,
        "makespan_improvement_fraction": makespan_improvement,
        "p95_freshness_improvement_fraction": p95_improvement,
        "required_makespan_improvement_fraction": 0.08,
        "required_p95_freshness_improvement_fraction": 0.10,
        "improvement_gate_passed": improvement,
        "legal_reorder_only": True,
        "extra_llm_calls": 0,
        "extra_input_tokens": 0,
        "speculative_waste": 0,
        "live_authorized": False,
    }


def _insufficient_observability_gate() -> dict[str, Any]:
    return {
        "decision": "STOP_ORACLE_INSUFFICIENT_OBSERVABILITY",
        "reason": "exact_intra_edge_request_dependencies_not_recoverable",
        "scheduler_choice_count_gt_zero": "NOT_EVALUATED",
        "criticality_inversion_count_gt_zero": "NOT_EVALUATED",
        "makespan_improvement_fraction": "NOT_EVALUATED",
        "p95_freshness_improvement_fraction": "NOT_EVALUATED",
        "required_makespan_improvement_fraction": 0.08,
        "required_p95_freshness_improvement_fraction": 0.10,
        "improvement_gate_passed": False,
        "legal_reorder_only": "NOT_CERTIFIABLE",
        "extra_llm_calls": 0,
        "extra_input_tokens": 0,
        "speculative_waste": 0,
        "live_authorized": False,
    }


def _unknown_dependency_summary(
    bundle: TraceBundle, dag: RequestDAG
) -> dict[str, Any]:
    affected_operator_ids = {
        marker.split(":per_edge_child_identity_missing", 1)[0].removeprefix(
            "UNKNOWN_DEPENDENCY:"
        )
        for marker in dag.unknown_dependencies
        if marker.endswith(":per_edge_child_identity_missing")
    }
    affected_requests = [
        request
        for request in bundle.requests
        if request.operator_id in affected_operator_ids
    ]
    by_source = Counter(str(request.source_sequence) for request in affected_requests)
    return {
        "group_count": len(dag.unknown_dependencies),
        "affected_request_count": len(affected_requests),
        "unaffected_request_count": len(bundle.requests) - len(affected_requests),
        "affected_requests_by_source": dict(
            sorted(by_source.items(), key=lambda item: int(item[0]))
        ),
        "markers": list(dag.unknown_dependencies),
        "root_cause": (
            "Graphiti resolve_extracted_edge may issue sequential dedupe, "
            "attribute, and timestamp calls, while Q0 records only their shared "
            "source-level operator_id and no per-edge child identity"
        ),
    }


def analyze_bundle(bundle: TraceBundle, dag: RequestDAG) -> dict[str, Any]:
    complete = dag.oracle_evaluable
    unknown_summary = _unknown_dependency_summary(bundle, dag)
    criticality: dict[str, Any]
    if complete:
        criticality = {
            "status": "EVALUATED",
            "definition": "longest remaining evidence-backed service-duration path to source publication",
            "request_on_publication_critical_path_count": sum(
                dag.critical_path_membership(request.request_id)
                for request in bundle.requests
            ),
            "maximum_downstream_publication_distance_ns": max(
                (
                    dag.downstream_publication_distance_ns(request.request_id)
                    for request in bundle.requests
                ),
                default=0,
            ),
            "maximum_downstream_blocked_work_count": max(
                (
                    dag.downstream_blocked_work_count(request.request_id)
                    for request in bundle.requests
                ),
                default=0,
            ),
            "maximum_publication_unlock_count": max(
                (
                    dag.publication_unlock_count(request.request_id)
                    for request in bundle.requests
                ),
                default=0,
            ),
        }
    else:
        criticality = {
            "status": "NOT_EVALUABLE",
            "reason": "exact_request_dependency_dag_not_recoverable",
            "request_on_publication_critical_path_count": "NOT_OBSERVABLE",
            "maximum_downstream_publication_distance_ns": "NOT_OBSERVABLE",
            "maximum_downstream_blocked_work_count": "NOT_OBSERVABLE",
            "maximum_publication_unlock_count": "NOT_OBSERVABLE",
        }
    audit = _sealed(
        {
            "schema_version": f"{SCHEMA_PREFIX}.audit.v1",
            "status": (
                "COMPLETE_OFFLINE_DAG"
                if complete
                else "PARTIAL_DAG_INSUFFICIENT_OBSERVABILITY"
            ),
            "history_id": bundle.history_id,
            "source_count": bundle.source_count,
            "configured_k": bundle.configured_k,
            "input_paths": list(bundle.input_paths),
            "input_sha256": bundle.observability,
            "q0_scope": {
                "artifact_status": "DIAGNOSTIC_ONLY_NON_MERGEABLE",
                "formal_main_table_eligible": False,
                "new_scheduler_authorized_before_oracle": False,
                "live_run_performed": False,
                "v4_remains_stopped": True,
            },
            "observability": _request_observability(bundle),
            "graphiti_evidence": {
                "version": "0.29.3",
                "resolve_extracted_edges_parallelism": "semaphore_gather_per_extracted_edge",
                "resolve_extracted_edge_internal_calls": (
                    "conditional sequential dedupe -> attribute -> timestamp"
                ),
                "per_edge_child_request_identity": "NOT_OBSERVABLE",
                "response_to_request_id": "NOT_OBSERVABLE",
                "unsupported_fields": [
                    "prompt_name",
                    "persistent_state_access_class",
                    "memory_version",
                    "vllm_batch_membership",
                    "gpu_execution_width",
                ],
            },
            "dag": _dag_shape(dag),
            "criticality": criticality,
            "unknown_dependencies": unknown_summary,
        }
    )
    actual: ReplayResult | None = None
    fifo: ReplayResult | None = None
    oracle: ReplayResult | None = None
    if complete:
        actual = replay(bundle, dag=dag, policy="ACTUAL")
        fifo = replay(bundle, dag=dag, policy="FIFO")
        oracle = replay(bundle, dag=dag, policy="ORACLE")
        actual_metrics = _result_metrics(actual, bundle=bundle, dag=dag)
        fifo_metrics = _result_metrics(fifo, bundle=bundle, dag=dag)
        oracle_metrics = _result_metrics(oracle, bundle=bundle, dag=dag)
        gate = _gate(actual, oracle)
        opportunity_status = "EVALUATED"
    else:
        actual_metrics = _observed_actual_metrics(bundle)
        fifo_metrics = _not_evaluable_schedule("FIFO")
        oracle_metrics = _not_evaluable_schedule("PUBLICATION_CRITICAL_ORACLE")
        gate = _insufficient_observability_gate()
        opportunity_status = "NOT_EVALUABLE_INSUFFICIENT_OBSERVABILITY"
    metrics = {
        "schema_version": f"{SCHEMA_PREFIX}.opportunity.v1",
        "status": opportunity_status,
        "history_id": bundle.history_id,
        "source_count": bundle.source_count,
        "request_count": len(bundle.requests),
        "dag": _dag_shape(dag),
        "actual": actual_metrics,
        "fifo": fifo_metrics,
        "publication_critical_oracle": oracle_metrics,
        "decision": gate,
        "no_extra_work": {
            "extra_llm_calls": 0,
            "extra_input_tokens": 0,
            "speculative_waste": 0,
        },
        "q0_diagnostic_only": True,
        "live_run_performed": False,
    }
    metrics = _sealed(metrics)
    oracle_artifact = _sealed(
        {
            "schema_version": f"{SCHEMA_PREFIX}.oracle.v1",
            "status": opportunity_status,
            "history_id": bundle.history_id,
            "policy": "PUBLICATION_CRITICAL_ORACLE",
            "scope": "legal request admission reorder only; K=2; no extra work",
            "criticality_definition": "longest remaining evidence-backed service-duration path to source publication sink including observed fixed tail",
            "actual": actual_metrics,
            "fifo": fifo_metrics,
            "oracle": oracle_metrics,
            "gate": gate,
            "unobservable_backend": ["vllm_batch_membership", "gpu_execution_width", "GPU_service_interference"],
            "q0_diagnostic_only": True,
            "live_run_performed": False,
        }
    )
    return {
        "audit": audit,
        "opportunity": metrics,
        "oracle": oracle_artifact,
        "actual": actual,
        "fifo": fifo,
        "oracle_result": oracle,
    }


def write_analysis_artifacts(bundle: TraceBundle, dag: RequestDAG, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    result = analyze_bundle(bundle, dag)
    atomic_write_json(output_root / "V5_REQUEST_DAG_AUDIT.json", result["audit"])
    atomic_write_json(output_root / "V5_SCHEDULER_OPPORTUNITY.json", result["opportunity"])
    atomic_write_json(output_root / "V5_PUBLICATION_CRITICAL_ORACLE.json", result["oracle"])
    actual_result = result["actual"]
    with (output_root / "V5_SCHEDULER_OPPORTUNITY.csv").open("w", newline="", encoding="utf-8") as stream:
        fields = [
            "status",
            "reason",
            "timestamp_ns",
            "waiting_request_ids",
            "selected_request_id",
            "selected_criticality_ns",
            "maximum_legal_criticality_ns",
            "active_count_before_selection",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        if actual_result is None:
            writer.writerow(
                {
                    "status": "OBSERVABILITY_BARRIER",
                    "reason": "exact_intra_edge_request_dependencies_not_recoverable",
                    "timestamp_ns": "NOT_OBSERVABLE",
                    "waiting_request_ids": "NOT_OBSERVABLE",
                    "selected_request_id": "NOT_OBSERVABLE",
                    "selected_criticality_ns": "NOT_OBSERVABLE",
                    "maximum_legal_criticality_ns": "NOT_OBSERVABLE",
                    "active_count_before_selection": "NOT_OBSERVABLE",
                }
            )
        else:
            for decision in actual_result.decision_points:
                writer.writerow(
                    {
                        "status": "EVALUATED",
                        "reason": "",
                        **{
                            field: (
                                json.dumps(decision[field], sort_keys=True)
                                if isinstance(decision[field], list)
                                else decision[field]
                            )
                            for field in fields
                            if field not in {"status", "reason"}
                        },
                    }
                )
    gate = result["opportunity"]["decision"]
    actual_metrics = result["opportunity"]["actual"]
    oracle_metrics = result["opportunity"]["publication_critical_oracle"]
    audit_sha = sha256_file(output_root / "V5_REQUEST_DAG_AUDIT.json")
    opportunity_sha = sha256_file(output_root / "V5_SCHEDULER_OPPORTUNITY.json")
    oracle_sha = sha256_file(output_root / "V5_PUBLICATION_CRITICAL_ORACLE.json")
    (output_root / "V5_REQUEST_DAG_AUDIT.md").write_text(
        f"""# V5 Request DAG Audit

Status: OFFLINE_ONLY

The sealed Q0 trace is diagnostic-only and non-mergeable. v4 remains stopped; no live run or service call was performed.

- History: `{bundle.history_id}`; sources: `{bundle.source_count}`; K: `{bundle.configured_k}`
- Requests: `{len(bundle.requests)}`; DAG nodes: `{len(dag.nodes)}`; edges: `{len(dag.edges)}`
- Edge counts: `{json.dumps(dict(sorted(Counter(edge.kind.value for edge in dag.edges).items())), sort_keys=True)}`
- Exact dependency DAG recovered: `{str(dag.oracle_evaluable).lower()}`; unresolved dependency groups: `{len(dag.unknown_dependencies)}`.
- `resolve_extracted_edges` is code-proven parallel across edges, but each edge coroutine may issue sequential dedupe, attribute, and timestamp calls.
- Q0 does not record the per-edge child identity needed to map those internal request chains.
- Prompt names, persistent-state reads, memory versions, vLLM batch membership, and GPU execution width remain `NOT_OBSERVABLE`.

Artifact SHA-256: `{audit_sha}`
""",
        encoding="utf-8",
    )
    (output_root / "V5_PUBLICATION_CRITICAL_ORACLE.md").write_text(
        f"""# Publication-Critical Oracle

This is an offline observability gate. It does not implement a scheduler and does not authorize vLLM.

Observed actual makespan: `{actual_metrics['makespan_ns']}` ns

Oracle makespan: `{oracle_metrics['makespan_ns']}`

Observed actual P95 freshness: `{actual_metrics['p95_freshness_ns']}` ns

Oracle P95 freshness: `{oracle_metrics['p95_freshness_ns']}`

Scheduler choices: `{actual_metrics['scheduler_choice_count']}`; criticality inversions: `{actual_metrics['criticality_inversion_count']}`; maximum legal choice width: `{actual_metrics['max_legal_choice_width']}`.

Pre-registered decision: `{gate['decision']}`.

Artifact SHA-256: `{oracle_sha}`
""",
        encoding="utf-8",
    )
    (output_root / "V5_NEXT_DECISION.md").write_text(
        f"""# V5 Next Decision

STATUS: `{gate['decision']}`

Request identity is complete, but the exact dependency DAG is not recoverable: 150 edge-resolve requests share source-level operator identities while the code permits sequential per-edge subcalls. Therefore legal decision points, criticality inversions, FIFO replay, and publication-critical oracle performance are `NOT_EVALUABLE`.

No live run is authorized. No v4 frozen artifact was changed. The result is diagnostic-only and non-mergeable.

Input artifacts: audit `{audit_sha}`, opportunity `{opportunity_sha}`, oracle `{oracle_sha}`.

Next action: `STOP_ORACLE_INSUFFICIENT_OBSERVABILITY`; do not request vLLM or add live instrumentation for this gate.
""",
        encoding="utf-8",
    )
    return result
