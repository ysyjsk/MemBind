#!/usr/bin/env python3
"""Offline within-version MEG opportunity oracle.

This module consumes one already sealed real MEG capture.  It deliberately
does not import a Graphiti client, start a service, change admission, or
reconstruct any stale state.  The only counterfactual is a deterministic
list schedule over the observed semantic DAG with a fixed two-slot LLM
resource.

The capture's request spans contain admission residence (they overlap while
requests wait behind K=2).  The llm.jsonl start/terminal pair is therefore
the physical service interval used by the resource oracle.  Both values are
retained in the output so that waiting is not silently reported as model
work.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402


RUN_ID = "membind-v31-opt-w4-meg-runtime-observe-20260821-011"
SOURCE_COUNT = 12
LLM_K = 2
DEFAULT_CAPTURE = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_runtime_instrumentation"
    / RUN_ID
)
DEFAULT_OUTPUT = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/meg_runtime_oracle"
    / "meg-runtime-oracle-20260821-011"
)


class OracleError(ValueError):
    """Raised when the sealed capture cannot support the offline oracle."""


@dataclass
class Task:
    task_id: str
    operator_id: str
    request_id: str | None
    source_sequence: int
    operator_type: str
    classification: str
    resource_class: str
    resource_basis: str
    duration_ns: int
    request_span_duration_ns: int
    active_service_duration_ns: int
    observed_ready_ns: int
    observed_start_ns: int
    observed_end_ns: int
    observed_submit_ns: int | None
    parents: set[str] = field(default_factory=set)
    children: set[str] = field(default_factory=set)
    release_ns: int = 0
    publication_descendants: set[int] = field(default_factory=set)
    downstream_tail_ns: int = 0


def _seal(body: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    result["payload_sha256"] = payload_sha256(result)
    return result


def _percentile(values: list[int | float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    left = math.floor(position)
    right = math.ceil(position)
    if left == right:
        return ordered[left]
    weight = position - left
    return ordered[left] + (ordered[right] - ordered[left]) * weight


def _stats(values: list[int | float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
        "sum": sum(values),
        "mean": statistics.mean(values) if values else None,
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }


def _read_wrapped_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            wrapper = json.loads(line)
        except json.JSONDecodeError as error:
            raise OracleError(f"{path.name}:invalid_json:{line_number}") from error
        if set(wrapper) != {"record", "record_sha256"}:
            raise OracleError(f"{path.name}:invalid_wrapper:{line_number}")
        record = wrapper["record"]
        if wrapper["record_sha256"] != payload_sha256(record):
            raise OracleError(f"{path.name}:record_hash_mismatch:{line_number}")
        row = record.get("row") if isinstance(record, dict) else None
        rows.append(row if isinstance(row, dict) else record)
    return rows


def _event_times(capture: dict[str, Any]) -> dict[str, dict[str, int]]:
    by_id: dict[str, dict[str, int]] = defaultdict(dict)
    for event in capture["events"]:
        operator_id = event.get("semantic_operator_id")
        event_type = str(event.get("event_type"))
        if not operator_id or not event_type.startswith("OPERATOR_"):
            continue
        if event_type in by_id[operator_id]:
            raise OracleError(f"duplicate_operator_event:{operator_id}:{event_type}")
        by_id[operator_id][event_type] = int(event["timestamp_ns"])
    required = {"OPERATOR_MATERIALIZED", "OPERATOR_READY", "OPERATOR_START", "OPERATOR_END"}
    for operator_id, times in by_id.items():
        if set(times) != required:
            raise OracleError(f"operator_event_set_incomplete:{operator_id}")
        if not (
            times["OPERATOR_MATERIALIZED"]
            <= times["OPERATOR_READY"]
            <= times["OPERATOR_START"]
            <= times["OPERATOR_END"]
        ):
            raise OracleError(f"operator_event_order_invalid:{operator_id}")
    return by_id


def _resource_for(operator: dict[str, Any], has_request: bool) -> tuple[str, str]:
    """Return an evidence-backed resource class and its derivation rule."""

    typ = str(operator["semantic_operator_type"])
    if has_request:
        return "LLM", "production request span is present; request enters LLM admission"
    if typ in {
        "NODE_CANDIDATE_READ",
        "EDGE_CANDIDATE_READ",
        "PERSIST_AND_PUBLISH",
        "SOURCE_PUBLICATION",
    }:
        return "DB", "candidate/effect boundary is an observed Neo4j-facing operator"
    if typ in {
        "DETERMINISTIC_SIMILARITY",
        "IDENTITY_MATERIALIZATION",
        "UNRESOLVED_SET_FORMATION",
        "EDGE_RESOLUTION_GROUP",
    }:
        return "CPU", "deterministic/orchestration operator has no production request span"
    if typ == "NODE_ATTRIBUTE_SUMMARY_BATCH":
        return "OPAQUE", "no independent request span; embedding/cache/backend resource is not observable"
    return "OPAQUE", "resource is not independently observable in the sealed capture"


def _llm_events(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        request_id = row.get("request_id")
        event_type = row.get("event_type")
        timestamp = row.get("timestamp_ns")
        if not request_id or event_type not in {
            "llm_request_submitted",
            "llm_request_start",
            "llm_request_terminal",
        }:
            continue
        result[str(request_id)][str(event_type)] = int(timestamp)
    required = {"llm_request_submitted", "llm_request_start", "llm_request_terminal"}
    for request_id, values in result.items():
        if set(values) != required:
            raise OracleError(f"llm_request_event_set_incomplete:{request_id}")
        if not (
            values["llm_request_submitted"]
            <= values["llm_request_start"]
            <= values["llm_request_terminal"]
        ):
            raise OracleError(f"llm_request_event_order_invalid:{request_id}")
    return result


def _source_boundaries(
    lifecycle: list[dict[str, Any]], publications: list[dict[str, Any]]
) -> tuple[dict[int, int], dict[int, int]]:
    arrivals: dict[int, int] = {}
    durable: dict[int, int] = {}
    for row in lifecycle:
        source = row.get("source_sequence")
        if source is None:
            continue
        source = int(source)
        if row.get("event_type") == "arrival":
            arrivals.setdefault(source, int(row["timestamp_ns"]))
        if row.get("event_type") == "publication_durable":
            durable[source] = max(durable.get(source, 0), int(row["timestamp_ns"]))
    publication = {
        int(row["source_sequence"]): int(row["timestamp_ns"])
        for row in publications
        if row.get("source_sequence") is not None
    }
    if set(arrivals) != set(range(SOURCE_COUNT)) or set(publication) != set(range(SOURCE_COUNT)):
        raise OracleError("source_boundary_incomplete")
    return arrivals, publication


def _source_publication_tasks(tasks: dict[str, Task]) -> dict[int, str]:
    result: dict[int, str] = {}
    for task_id, task in tasks.items():
        if task.operator_type == "SOURCE_PUBLICATION":
            if task.source_sequence in result:
                raise OracleError(f"duplicate_source_publication:{task.source_sequence}")
            result[task.source_sequence] = task_id
    if set(result) != set(range(SOURCE_COUNT)):
        raise OracleError("source_publication_task_incomplete")
    return result


def _build_tasks(
    capture: dict[str, Any],
    lifecycle: list[dict[str, Any]],
    llm_rows: list[dict[str, Any]],
) -> tuple[dict[str, Task], dict[str, str], dict[str, dict[str, Any]], dict[int, int], dict[int, int]]:
    event_times = _event_times(capture)
    llm_times = _llm_events(llm_rows)
    arrivals, observed_publications = _source_boundaries(lifecycle, capture["publication_events"])
    operators = {str(item["semantic_operator_id"]): item for item in capture["operators"]}
    spans_by_operator: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for span in capture["request_spans"]:
        spans_by_operator[str(span["semantic_operator_id"])].append(span)
    for spans in spans_by_operator.values():
        spans.sort(key=lambda item: (int(item["start_ns"]), str(item["request_id"])))

    tasks: dict[str, Task] = {}
    operator_completion: dict[str, str] = {}
    request_meta: dict[str, dict[str, Any]] = {}

    for operator_id, operator in operators.items():
        times = event_times[operator_id]
        spans = spans_by_operator.get(operator_id, [])
        resource, basis = _resource_for(operator, bool(spans))
        if spans:
            previous_task: str | None = None
            for ordinal, span in enumerate(spans):
                request_id = str(span["request_id"])
                if request_id not in llm_times:
                    raise OracleError(f"llm_events_missing:{request_id}")
                request_span_duration = int(span["end_ns"]) - int(span["start_ns"])
                active_duration = (
                    llm_times[request_id]["llm_request_terminal"]
                    - llm_times[request_id]["llm_request_start"]
                )
                if request_span_duration <= 0 or active_duration <= 0:
                    raise OracleError(f"request_duration_invalid:{request_id}")
                task_id = f"request:{request_id}"
                parents = set()
                if previous_task is not None:
                    parents.add(previous_task)
                task = Task(
                    task_id=task_id,
                    operator_id=operator_id,
                    request_id=request_id,
                    source_sequence=int(operator["source_sequence"]),
                    operator_type=str(operator["semantic_operator_type"]),
                    classification=str(operator["classification"]),
                    resource_class=resource,
                    resource_basis=basis,
                    duration_ns=active_duration,
                    request_span_duration_ns=request_span_duration,
                    active_service_duration_ns=active_duration,
                    observed_ready_ns=times["OPERATOR_READY"] if ordinal == 0 else llm_times[spans[ordinal - 1]["request_id"]]["llm_request_terminal"],
                    observed_start_ns=int(span["start_ns"]),
                    observed_end_ns=int(span["end_ns"]),
                    observed_submit_ns=llm_times[request_id]["llm_request_submitted"],
                    parents=parents,
                    release_ns=arrivals[int(operator["source_sequence"])],
                )
                tasks[task_id] = task
                request_meta[request_id] = {
                    "request_id": request_id,
                    "task_id": task_id,
                    "operator_id": operator_id,
                    "source_sequence": int(operator["source_sequence"]),
                    "operator_type": str(operator["semantic_operator_type"]),
                    "ready_evidence_ns": task.observed_ready_ns,
                    "submit_ns": task.observed_submit_ns,
                    "start_ns": llm_times[request_id]["llm_request_start"],
                    "terminal_ns": llm_times[request_id]["llm_request_terminal"],
                    "request_span_duration_ns": request_span_duration,
                    "active_service_duration_ns": active_duration,
                }
                previous_task = task_id
            assert previous_task is not None
            operator_completion[operator_id] = previous_task
        else:
            task_id = f"operator:{operator_id}"
            duration = times["OPERATOR_END"] - times["OPERATOR_START"]
            if duration <= 0:
                raise OracleError(f"operator_duration_invalid:{operator_id}")
            tasks[task_id] = Task(
                task_id=task_id,
                operator_id=operator_id,
                request_id=None,
                source_sequence=int(operator["source_sequence"]),
                operator_type=str(operator["semantic_operator_type"]),
                classification=str(operator["classification"]),
                resource_class=resource,
                resource_basis=basis,
                duration_ns=duration,
                request_span_duration_ns=0,
                active_service_duration_ns=0,
                observed_ready_ns=times["OPERATOR_READY"],
                observed_start_ns=times["OPERATOR_START"],
                observed_end_ns=times["OPERATOR_END"],
                observed_submit_ns=None,
                release_ns=arrivals[int(operator["source_sequence"])],
            )
            operator_completion[operator_id] = task_id

    # Direct semantic dependencies become task dependencies.  A request chain
    # already carries the previous subrequest edge; only the first request of
    # an operator needs the direct parent edges.
    first_task_by_operator: dict[str, str] = {}
    # Request IDs are global and are not guaranteed to start at zero per
    # operator, so identify the first subrequest by observed readiness/order.
    for operator_id in operators:
        candidates = [task for task in tasks.values() if task.operator_id == operator_id]
        first = min(candidates, key=lambda task: (task.observed_ready_ns, task.observed_start_ns, task.task_id))
        first_task_by_operator[operator_id] = first.task_id
    for operator_id, operator in operators.items():
        first_task = tasks[first_task_by_operator[operator_id]]
        for parent_operator_id in operator.get("parent_semantic_operator_ids", []):
            parent_id = str(parent_operator_id)
            if parent_id not in operator_completion:
                raise OracleError(f"parent_operator_missing:{operator_id}:{parent_id}")
            first_task.parents.add(operator_completion[parent_id])

    publication_tasks = _source_publication_tasks(tasks)
    # Exact publication order is a fixed effect contract, not an optional
    # scheduling choice.  State-derived operators additionally require the
    # exact predecessor publication before becoming legal.
    for source in range(1, SOURCE_COUNT):
        tasks[publication_tasks[source]].parents.add(publication_tasks[source - 1])
    for task in tasks.values():
        if task.classification == "STATE_DERIVED" and task.source_sequence > 0:
            task.parents.add(publication_tasks[task.source_sequence - 1])
    for task in tasks.values():
        for parent in task.parents:
            if parent not in tasks:
                raise OracleError(f"task_parent_missing:{task.task_id}:{parent}")
            tasks[parent].children.add(task.task_id)
    # The source-arrival release is explicit; the graph and exact-version
    # release edges are the legal readiness conditions for counterfactuals.
    for task in tasks.values():
        task.release_ns = arrivals[task.source_sequence]
    return tasks, operator_completion, request_meta, arrivals, observed_publications


def _topological(tasks: dict[str, Task]) -> list[str]:
    indegree = {task_id: len(task.parents) for task_id, task in tasks.items()}
    ready = sorted(task_id for task_id, value in indegree.items() if value == 0)
    result: list[str] = []
    while ready:
        task_id = ready.pop(0)
        result.append(task_id)
        for child in sorted(tasks[task_id].children):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()
    if len(result) != len(tasks):
        raise OracleError("task_graph_cycle")
    return result


def _descendant_publications(tasks: dict[str, Task]) -> None:
    order = _topological(tasks)
    for task_id in reversed(order):
        task = tasks[task_id]
        publications: set[int] = set()
        if task.operator_type == "SOURCE_PUBLICATION":
            publications.add(task.source_sequence)
        # The publication-order edge from source i to i+1 is an exact effect
        # constraint, but it is not a within-version descendant.  Criticality
        # for a request in source i stops at publication i.
        same_source_children = [
            child_id for child_id in task.children
            if tasks[child_id].source_sequence == task.source_sequence
        ]
        for child_id in same_source_children:
            publications.update(tasks[child_id].publication_descendants)
        task.publication_descendants = publications
        task.downstream_tail_ns = max(
            (tasks[child_id].duration_ns + tasks[child_id].downstream_tail_ns for child_id in same_source_children),
            default=0,
        )


def _capacity_evidence(tasks: dict[str, Task]) -> dict[str, Any]:
    # The real capture certifies a shared K=2 LLM gate, but it does not expose
    # a shared DB/CPU/embedding semaphore.  A serialized operator interval is
    # therefore evidence of dependency timing, not evidence of a capacity-1
    # backend.  Non-LLM classes remain non-binding in the oracle; their
    # dependencies and observed service durations are still retained.
    return {
        "LLM": {
            "capacity": LLM_K,
            "basis": "pinned global_llm_admission_k=2 and llm start/terminal peak of 2",
        },
        "DB": {"capacity": 1_000_000, "basis": "no shared DB capacity contract is observable; non-binding dependency resource"},
        "CPU": {"capacity": 1_000_000, "basis": "no shared CPU capacity contract is observable; non-binding dependency resource"},
        "OPAQUE": {"capacity": 1_000_000, "basis": "no independent embedding/backend capacity is observable; non-binding opaque resource"},
    }


def _priority(policy: str, task: Task) -> tuple[Any, ...]:
    if policy == "CACHE_AFFINE":
        return (task.observed_submit_ns if task.observed_submit_ns is not None else task.observed_start_ns, task.task_id)
    if policy == "FIFO":
        return (task.observed_ready_ns, task.task_id)
    if policy == "PUBLICATION_CRITICALITY_FIRST":
        return (-task.downstream_tail_ns, -task.duration_ns, task.observed_ready_ns, task.task_id)
    raise OracleError(f"unknown_policy:{policy}")


def _schedule(tasks: dict[str, Task], policy: str, capacities: dict[str, Any]) -> dict[str, Any]:
    remaining = set(tasks)
    running: dict[str, int] = {}
    finish_times: dict[str, int] = {}
    start_times: dict[str, int] = {}
    admissions: list[dict[str, Any]] = []
    t0 = min(task.release_ns for task in tasks.values())
    now = t0

    def done(task_id: str) -> bool:
        return task_id in finish_times

    while remaining or running:
        for task_id, end_ns in list(running.items()):
            if end_ns <= now:
                finish_times[task_id] = end_ns
                del running[task_id]
        started_any = True
        while started_any:
            started_any = False
            ready = [
                tasks[task_id]
                for task_id in remaining
                if tasks[task_id].release_ns <= now and all(done(parent) for parent in tasks[task_id].parents)
            ]
            for resource_class, info in capacities.items():
                slots = int(info["capacity"]) - sum(
                    tasks[task_id].resource_class == resource_class for task_id in running
                )
                if slots <= 0:
                    continue
                candidates = [task for task in ready if task.resource_class == resource_class]
                candidates.sort(key=lambda task: _priority(policy if resource_class == "LLM" else "FIFO", task))
                selected = candidates[:slots]
                if resource_class == "LLM" and selected:
                    candidate_ids = [task.task_id for task in sorted(candidates, key=lambda item: _priority(policy, item))]
                    for task in selected:
                        admissions.append({
                            "timestamp_ns": now,
                            "policy": policy,
                            "candidate_count": len(candidates),
                            "candidate_task_ids": candidate_ids,
                            "selected_task_id": task.task_id,
                            "active_before": LLM_K - slots,
                            "free_slots_before": slots,
                            "choice_set_ge_2": len(candidates) >= 2,
                            "choice_affects_publication_completion": len(candidates) > slots and len({tasks[item].downstream_tail_ns for item in candidate_ids}) > 1,
                        })
                for task in selected:
                    remaining.remove(task.task_id)
                    start_times[task.task_id] = now
                    running[task.task_id] = now + max(1, task.duration_ns)
                    started_any = True
                if selected:
                    ready = [task for task in ready if task.task_id not in {item.task_id for item in selected}]
        if running:
            now = min(running.values())
            continue
        if remaining:
            future = [
                task.release_ns
                for task_id in remaining
                if not all(done(parent) for parent in tasks[task_id].parents)
            ]
            release_points = [tasks[task_id].release_ns for task_id in remaining if tasks[task_id].release_ns > now]
            if release_points:
                now = min(release_points)
                continue
            # If no resource could run a task and no future release exists,
            # the graph/resource model is inconsistent rather than waiting.
            raise OracleError(f"schedule_deadlock:{policy}:{len(remaining)}:{len(future)}")
    return {
        "policy": policy,
        "task_start_ns": start_times,
        "task_end_ns": finish_times,
        "admission_events": admissions,
        "publication_times_ns": {
            task.source_sequence: finish_times[task_id]
            for task_id, task in tasks.items()
            if task.operator_type == "SOURCE_PUBLICATION"
        },
    }


def _actual_choice_sets(
    tasks: dict[str, Task],
    request_meta: dict[str, dict[str, Any]],
    observed_publications: dict[int, int],
    llm_k: int = LLM_K,
) -> dict[str, Any]:
    request_tasks = {task.request_id: task for task in tasks.values() if task.request_id is not None}
    ordered = sorted(
        request_meta.values(), key=lambda row: (int(row["submit_ns"]), str(row["request_id"]))
    )
    submitted: set[str] = set()
    decisions: list[dict[str, Any]] = []
    inversions: list[dict[str, Any]] = []
    for row in ordered:
        timestamp = int(row["submit_ns"])
        rid = str(row["request_id"])
        task = request_tasks[rid]
        candidates: list[Task] = []
        for other in request_tasks.values():
            if other.request_id in submitted:
                continue
            release = other.observed_ready_ns
            if other.classification == "STATE_DERIVED" and other.source_sequence > 0:
                release = max(release, observed_publications[other.source_sequence - 1])
            # First request readiness is OPERATOR_READY.  A later subrequest
            # uses the prior terminal event, never submit/start inference.
            if release <= timestamp:
                candidates.append(other)
        version_candidates = [candidate for candidate in candidates if candidate.source_sequence == task.source_sequence]
        active = sum(
            int(meta["start_ns"]) <= timestamp < int(meta["terminal_ns"])
            for meta in request_meta.values()
        )
        free_slots = max(0, llm_k - active)
        max_tail = max((candidate.downstream_tail_ns for candidate in version_candidates), default=0)
        tails = {candidate.downstream_tail_ns for candidate in version_candidates}
        choice_affects = len(version_candidates) > free_slots and len(tails) > 1
        selected_tail = task.downstream_tail_ns
        higher = max(
            (candidate for candidate in version_candidates if candidate.task_id != task.task_id),
            key=lambda candidate: (candidate.downstream_tail_ns, candidate.duration_ns),
            default=None,
        )
        inversion = bool(choice_affects and higher is not None and selected_tail < higher.downstream_tail_ns)
        decision = {
            "timestamp_ns": timestamp,
            "selected_request_id": rid,
            "selected_task_id": task.task_id,
            "selected_operator_id": task.operator_id,
            "candidate_count": len(candidates),
            "candidate_request_ids": [candidate.request_id for candidate in sorted(candidates, key=lambda item: (item.observed_ready_ns, item.task_id))],
            "candidate_operator_ids": [candidate.operator_id for candidate in sorted(candidates, key=lambda item: (item.observed_ready_ns, item.task_id))],
            "within_version_candidate_count": len(version_candidates),
            "within_version_candidate_request_ids": [candidate.request_id for candidate in sorted(version_candidates, key=lambda item: (item.observed_ready_ns, item.task_id))],
            "within_version_candidate_operator_ids": [candidate.operator_id for candidate in sorted(version_candidates, key=lambda item: (item.observed_ready_ns, item.task_id))],
            "active_request_count": active,
            "free_slots": free_slots,
            "choice_set_ge_2": len(version_candidates) >= 2,
            "global_choice_set_ge_2": len(candidates) >= 2,
            "choice_affects_publication_completion": choice_affects,
            "global_choice_affects_publication_completion": len(candidates) > free_slots and len({candidate.downstream_tail_ns for candidate in candidates}) > 1,
            "selected_publication_criticality_tail_ns": selected_tail,
            "max_candidate_publication_criticality_tail_ns": max_tail,
            "publication_criticality_inversion": inversion,
        }
        decisions.append(decision)
        if inversion and higher is not None:
            high_meta = request_meta[str(higher.request_id)]
            high_start = int(high_meta["start_ns"])
            inversion_duration = max(0, high_start - timestamp)
            penalty = max(0, higher.downstream_tail_ns - selected_tail)
            inversions.append({
                "decision_timestamp_ns": timestamp,
                "selected_request_id": rid,
                "waiting_higher_criticality_request_id": higher.request_id,
                "selected_operator_id": task.operator_id,
                "waiting_operator_id": higher.operator_id,
                "inversion_duration_ns": inversion_duration,
                "selected_service_duration_ns": task.active_service_duration_ns,
                "waiting_service_duration_ns": higher.active_service_duration_ns,
                "theoretical_publication_penalty_ns": penalty,
                "selected_tail_ns": selected_tail,
                "waiting_tail_ns": higher.downstream_tail_ns,
            })
        submitted.add(rid)
    return {
        "decisions": decisions,
        "choice_set_count": sum(item["choice_set_ge_2"] for item in decisions),
        "choice_affecting_count": sum(item["choice_affects_publication_completion"] for item in decisions),
        "global_choice_set_count": sum(item["global_choice_set_ge_2"] for item in decisions),
        "global_choice_affecting_count": sum(item["global_choice_affects_publication_completion"] for item in decisions),
        "inversions": inversions,
        "inversion_count": len(inversions),
        "definition": "The within-version candidate count is restricted to the selected request's source/memory version and requires OPERATOR_READY, exact predecessor publication for STATE_DERIVED work, and any prior subrequest completion. Global candidates are retained separately. Queue depth, STATE ready width, and active request count are not used as candidate substitutes.",
    }


def _path_metrics(tasks: dict[str, Task], arrivals: dict[int, int], observed_publications: dict[int, int]) -> dict[int, dict[str, Any]]:
    publication_tasks = _source_publication_tasks(tasks)
    order = _topological(tasks)
    earliest: dict[str, int] = {}
    path_duration: dict[str, int] = {}
    for task_id in order:
        task = tasks[task_id]
        parent_end = max((earliest[parent] for parent in task.parents), default=arrivals[task.source_sequence])
        earliest[task_id] = max(arrivals[task.source_sequence], parent_end) + task.duration_ns
        path_duration[task_id] = task.duration_ns + max((path_duration[parent] for parent in task.parents), default=0)
    by_source: dict[int, dict[str, Any]] = {}
    for source, task_id in publication_tasks.items():
        task = tasks[task_id]
        source_tasks = [item for item in tasks.values() if item.source_sequence == source]
        llm = [item for item in source_tasks if item.resource_class == "LLM"]
        work = sum(item.active_service_duration_ns for item in llm)
        residence = sum(item.request_span_duration_ns for item in llm)
        pub_earliest = earliest[task_id]
        latency = observed_publications[source] - arrivals[source]
        # path_duration is retained internally for DAG checks, but a source's
        # span is the earliest legal publication relative to that source's
        # arrival.  This avoids charging source i with work before its arrival
        # merely because the exact publication-order chain is represented in
        # the global graph.
        critical_path = max(0, pub_earliest - arrivals[source])
        by_source[source] = {
            "source_sequence": source,
            "arrival_ns": arrivals[source],
            "observed_publication_ns": observed_publications[source],
            "observed_publication_latency_ns": latency,
            "dependency_critical_path_ns": critical_path,
            "dependency_critical_path_includes_exact_predecessor_publication": source > 0,
            "dag_earliest_publication_ns_without_resource_contention": pub_earliest,
            "llm_request_count": len(llm),
            "llm_total_work_ns": work,
            "llm_request_span_residence_total_ns": residence,
            "llm_k2_resource_lower_bound_ns": math.ceil(work / LLM_K),
            "combined_lower_bound_ns": max(critical_path, math.ceil(work / LLM_K)),
            "llm_service_duration_stats_ns": _stats([item.active_service_duration_ns for item in llm]),
            "llm_request_span_duration_stats_ns": _stats([item.request_span_duration_ns for item in llm]),
        }
    return by_source


def _schedule_summary(
    schedule: dict[str, Any],
    source_metrics: dict[int, dict[str, Any]],
    arrivals: dict[int, int],
) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for source in range(SOURCE_COUNT):
        end = int(schedule["publication_times_ns"][source])
        actual = int(source_metrics[source]["observed_publication_latency_ns"])
        oracle_latency = end - arrivals[source]
        result[source] = {
            "source_sequence": source,
            "publication_makespan_ns": end,
            "publication_latency_ns": oracle_latency,
            "observed_publication_latency_ns": actual,
            "absolute_gap_vs_observed_ns": actual - oracle_latency,
            "relative_gap_vs_observed": (actual - oracle_latency) / actual if actual else 0.0,
        }
    return result


def _render_markdown(documents: dict[str, dict[str, Any] | str]) -> dict[str, str]:
    oracle = documents["MEG_WITHIN_VERSION_ORACLE.json"]
    comparison = documents["MEG_PUBLICATION_SCHEDULE_COMPARISON.json"]
    choice = documents["MEG_LLM_ADMISSION_CHOICE_SET.json"]
    inversion = documents["MEG_PUBLICATION_CRITICALITY_INVERSION.json"]
    decision = documents["MEG_WITHIN_VERSION_DECISION.json"]
    assert isinstance(oracle, dict) and isinstance(comparison, dict) and isinstance(choice, dict) and isinstance(inversion, dict) and isinstance(decision, dict)
    lines = [
        "# Within-Version MEG Opportunity Oracle", "",
        f"- run: `{oracle['run_id']}`", f"- capture: `{oracle['input_capture_payload_sha256']}`", "",
        "## Necessary Conditions", "",
        *[f"- {key}: `{value}`" for key, value in oracle["theoretical_necessary_conditions"].items()], "",
        "## Resource Model", "",
        "Resource capacities are fixed from the capture: LLM K=2; DB/CPU/OPAQUE are non-binding because no shared capacity contract is observable. OPAQUE means no independent embedding/backend span was observed.", "",
        "## Source Results", "",
        "| source | observed latency ns | critical path ns | LLM work ns | K=2 LB ns | CACHE_AFFINE ns | FIFO ns | criticality-first ns |", "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for source in range(SOURCE_COUNT):
        row = comparison["per_source"][str(source)]
        base = oracle["per_source"][str(source)]
        lines.append(f"| {source} | {base['observed_publication_latency_ns']} | {base['dependency_critical_path_ns']} | {base['llm_total_work_ns']} | {base['llm_k2_resource_lower_bound_ns']} | {row['CACHE_AFFINE']['publication_latency_ns']} | {row['FIFO']['publication_latency_ns']} | {row['PUBLICATION_CRITICALITY_FIRST']['publication_latency_ns']} |")
    lines.extend([
        "", "## Admission Choice Sets", "",
        f"- decisions: `{len(choice['decisions'])}`", f"- decisions with >=2 legal-ready candidates: `{choice['choice_set_count']}`", f"- completion-affecting choice sets: `{choice['choice_affecting_count']}`", "",
        "A legal-ready candidate is certified by OPERATOR_READY, exact predecessor publication for STATE_DERIVED work, and prior subrequest completion. Queue depth and active count are reported only as context.", "",
        "## Criticality Inversions", "",
        f"- inversion count: `{inversion['inversion_count']}`", f"- duration ns: `{inversion['duration_stats_ns']}`", f"- involved service ns: `{inversion['involved_service_stats_ns']}`", f"- theoretical penalty ns: `{inversion['penalty_stats_ns']}`", "",
        "## Decision", "", f"`{decision['decision']}`", "", decision["decision_reason"], "",
        "No scheduler or admission policy was implemented; all schedules are offline projections over the sealed trace.", "",
    ])
    return {
        "MEG_WITHIN_VERSION_ORACLE.md": "\n".join(lines),
        "MEG_PUBLICATION_SCHEDULE_COMPARISON.md": "\n".join([
            "# Publication Schedule Comparison", "",
            f"Decision: `{decision['decision']}`", "",
            f"Aggregate observed-to-criticality-first improvement: `{comparison['aggregate']['PUBLICATION_CRITICALITY_FIRST']['relative_improvement']}`",
            f"P50/P95 relative improvement: `{comparison['aggregate']['PUBLICATION_CRITICALITY_FIRST']['relative_improvement_p50']}` / `{comparison['aggregate']['PUBLICATION_CRITICALITY_FIRST']['relative_improvement_p95']}`", "",
        ]),
        "MEG_LLM_ADMISSION_CHOICE_SET.md": "\n".join([
            "# LLM Admission Choice Sets", "",
            f"- legal-ready decisions: `{choice['choice_set_count']}`", f"- completion-affecting decisions: `{choice['choice_affecting_count']}`", "",
            choice["definition"], "",
        ]),
        "MEG_PUBLICATION_CRITICALITY_INVERSION.md": "\n".join([
            "# Publication-Criticality Inversions", "",
            f"- count: `{inversion['inversion_count']}`", f"- duration: `{inversion['duration_stats_ns']}`", f"- penalty: `{inversion['penalty_stats_ns']}`", "",
        ]),
        "MEG_WITHIN_VERSION_DECISION.md": "\n".join([
            "# Within-Version MEG Decision", "", f"DECISION: `{decision['decision']}`", "", decision["decision_reason"], "",
            "Theoretical, admission-controllable, and backend/uncontrollable headroom are reported separately.", "",
        ]),
    }


def build_documents(capture_root: Path) -> dict[str, dict[str, Any] | str]:
    capture_root = capture_root.resolve()
    paths = {name: capture_root / name for name in [
        "MEG_RUNTIME_CAPTURE.json", "MEG_RUNTIME_CAPTURE_RESULT.json", "MEG_RUNTIME_CAPTURE_CONTRACT.json", "lifecycle.jsonl", "llm.jsonl", "queue.jsonl",
    ]}
    if any(not path.is_file() for path in paths.values()):
        missing = next(name for name, path in paths.items() if not path.is_file())
        raise OracleError(f"input_missing:{missing}")
    capture = json.loads(paths["MEG_RUNTIME_CAPTURE.json"].read_text(encoding="utf-8"))
    result = json.loads(paths["MEG_RUNTIME_CAPTURE_RESULT.json"].read_text(encoding="utf-8"))
    contract = json.loads(paths["MEG_RUNTIME_CAPTURE_CONTRACT.json"].read_text(encoding="utf-8"))
    lifecycle = _read_wrapped_jsonl(paths["lifecycle.jsonl"])
    llm_rows = _read_wrapped_jsonl(paths["llm.jsonl"])
    _read_wrapped_jsonl(paths["queue.jsonl"])
    declared = capture.get("payload_sha256")
    body = dict(capture)
    body.pop("payload_sha256", None)
    if declared != payload_sha256(body):
        raise OracleError("capture_payload_hash_mismatch")
    if result.get("status") != "PASS_REAL_MEG_RUNTIME_OBSERVE_ONLY":
        raise OracleError("runtime_capture_not_pass")
    if result.get("source_sequences") != list(range(SOURCE_COUNT)):
        raise OracleError("source_sequences_not_0_11")
    if not all(result.get("gates", {}).values()):
        raise OracleError("runtime_gate_failed")
    if result.get("scope", {}).get("shadow_reads") != 0 or result["scope"].get("scheduler_changed") or result["scope"].get("semantic_path_changed"):
        raise OracleError("non_interference_gate_failed")
    if result.get("metrics", {}).get("request_lineage_coverage") != 1.0:
        raise OracleError("request_lineage_not_complete")
    if capture.get("failure_records"):
        raise OracleError("capture_contains_failure_records")
    if contract.get("global_llm_admission_k") != LLM_K or contract.get("admission_policy") != "CACHE_AFFINE":
        raise OracleError("unexpected_admission_contract")

    tasks, operator_completion, request_meta, arrivals, observed_publications = _build_tasks(capture, lifecycle, llm_rows)
    _descendant_publications(tasks)
    capacities = _capacity_evidence(tasks)
    source_metrics = _path_metrics(tasks, arrivals, observed_publications)
    schedules = {
        policy: _schedule(tasks, policy, capacities)
        for policy in ("CACHE_AFFINE", "FIFO", "PUBLICATION_CRITICALITY_FIRST")
    }
    comparisons = {
        policy: _schedule_summary(schedules[policy], source_metrics, arrivals)
        for policy in schedules
    }
    per_source_comparison: dict[str, dict[str, Any]] = {}
    for source in range(SOURCE_COUNT):
        per_source_comparison[str(source)] = {
            policy: comparisons[policy][source] for policy in schedules
        }
    # Actual publication is always the certified capture, not the replayed
    # CACHE_AFFINE projection.  The latter is retained to expose schedule
    # assumptions without replacing the measured result.
    improvements: dict[str, dict[str, Any]] = {}
    observed_values = [source_metrics[source]["observed_publication_latency_ns"] for source in range(SOURCE_COUNT)]
    for policy in schedules:
        oracle_values = [comparisons[policy][source]["publication_latency_ns"] for source in range(SOURCE_COUNT)]
        relative = [
            (observed_values[index] - oracle_values[index]) / observed_values[index]
            if observed_values[index] else 0.0
            for index in range(SOURCE_COUNT)
        ]
        improvements[policy] = {
            "absolute_improvement_ns": sum(observed_values) - sum(oracle_values),
            "relative_improvement": (sum(observed_values) - sum(oracle_values)) / sum(observed_values),
            "relative_improvement_p50": _percentile(relative, 0.50),
            "relative_improvement_p95": _percentile(relative, 0.95),
            "per_source_relative_improvement": relative,
        }
    choices = _actual_choice_sets(tasks, request_meta, observed_publications)
    inversions = choices["inversions"]
    inversion_doc = {
        "schema_version": "membind.meg.within-version.publication-criticality-inversion.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "inversion_count": len(inversions),
        "duration_stats_ns": _stats([item["inversion_duration_ns"] for item in inversions]),
        "involved_service_stats_ns": _stats([
            item["selected_service_duration_ns"] + item["waiting_service_duration_ns"] for item in inversions
        ]),
        "penalty_stats_ns": _stats([item["theoretical_publication_penalty_ns"] for item in inversions]),
        "inversions": inversions,
        "interpretation": "An inversion is counted only when CACHE_AFFINE selected a lower publication-tail request while a higher-tail request was legal-ready, a K=2 slot was contested, and the two tails differ. The penalty is a conservative downstream-tail difference, not a measured speedup.",
    }
    theoretical = {
        "at_least_two_legal_ready_llm_requests_observed": choices["choice_set_count"] > 0,
        "choice_sets_that_can_affect_publication_completion": choices["choice_affecting_count"] > 0,
        "observed_cache_affine_policy": True,
        "exact_version_and_effect_constraints_preserved": True,
        "publication_order_preserved": True,
        "state_ready_width_not_used_as_llm_choice_set": True,
        "queue_depth_not_used_as_llm_choice_set": True,
        "active_request_count_not_used_as_llm_choice_set": True,
    }
    aggregate = {
        policy: improvements[policy] for policy in schedules
    }
    # The gate is intentionally conservative: a mechanism needs a repeatable
    # >=5% aggregate and both tail quantiles, not merely a large theoretical
    # lower-bound gap caused by backend residence.
    criticality = aggregate["PUBLICATION_CRITICALITY_FIRST"]
    fifo = aggregate["FIFO"]
    admission_delta = sum(
        max(0, min(
            source_metrics[source]["observed_publication_latency_ns"],
            comparisons["FIFO"][source]["publication_latency_ns"],
        ) - comparisons["PUBLICATION_CRITICALITY_FIRST"][source]["publication_latency_ns"])
        for source in range(SOURCE_COUNT)
    )
    theoretical_gap = sum(
        max(0, source_metrics[source]["observed_publication_latency_ns"] - source_metrics[source]["combined_lower_bound_ns"])
        for source in range(SOURCE_COUNT)
    )
    stable_headroom = (
        criticality["relative_improvement"] >= 0.05
        and (criticality["relative_improvement_p50"] or 0) >= 0.05
        and (criticality["relative_improvement_p95"] or 0) >= 0.05
    )
    if stable_headroom and admission_delta >= 0.5 * max(1, theoretical_gap) and inversion_doc["inversion_count"] > 0:
        decision = "GO_DESIGN_PUBLICATION_AWARE_ADMISSION"
        reason = "A stable >=5% criticality-first improvement is present in aggregate and both tail quantiles, with completion-affecting legal-ready choice sets and repeatable criticality inversions."
    elif theoretical_gap > 0 and admission_delta < 0.5 * max(1, theoretical_gap):
        decision = "STOP_LLM_ADMISSION_NOT_CAUSAL"
        reason = "The theoretical lower-bound gap is dominated by dependency/backend schedule slack; the legal-ready LLM ordering delta is not the majority of available headroom."
    else:
        decision = "STOP_WITHIN_VERSION_MEG_SCHEDULING_LOW_HEADROOM"
        reason = "The offline publication-criticality-first schedule does not provide stable, mechanism-supporting improvement over the observed capture under the fixed K=2 contract."
    decision_doc = {
        "schema_version": "membind.meg.within-version.decision.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "decision": decision,
        "decision_reason": reason,
        "thresholds": {
            "stable_relative_improvement": 0.05,
            "required_p50": 0.05,
            "required_p95": 0.05,
            "admission_share_of_theoretical_gap": 0.5,
        },
        "headroom": {
            "theoretical_headroom_ns": theoretical_gap,
            "admission_controllable_headroom_ns": admission_delta,
            "backend_or_uncontrollable_headroom_ns": max(0, theoretical_gap - admission_delta),
            "dependency_serialization_floor_ns": sum(max(0, source_metrics[s]["dependency_critical_path_ns"] - source_metrics[s]["llm_k2_resource_lower_bound_ns"]) for s in range(SOURCE_COUNT)),
            "k2_resource_floor_ns": sum(max(0, source_metrics[s]["llm_k2_resource_lower_bound_ns"] - source_metrics[s]["dependency_critical_path_ns"]) for s in range(SOURCE_COUNT)),
            "backend_service_variance_cv": statistics.pstdev([item["active_service_duration_ns"] for item in request_meta.values()]) / statistics.mean([item["active_service_duration_ns"] for item in request_meta.values()]),
            "cache_locality_vs_publication_criticality_conflict_count": inversion_doc["inversion_count"],
        },
        "prohibited_next_actions": ["new live run", "scheduler implementation", "admission modification", "SHADOW_READ", "stale-state probe", "parameter sweep"],
    }

    oracle_doc = _seal({
        "schema_version": "membind.meg.within-version.oracle.v1",
        "status": "PASS_OFFLINE_WITHIN_VERSION_MEG_ORACLE",
        "run_id": result["run_id"],
        "history_id": result["history_id"],
        "mode": result["mode"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "theoretical_necessary_conditions": theoretical,
        "resource_capacities": capacities,
        "task_count": len(tasks),
        "operator_count": len(capture["operators"]),
        "production_request_count": len(request_meta),
        "service_duration_definition": {
            "request_span_duration_ns": "request_spans end_ns-start_ns; includes admission residence and is retained as observed residence",
            "active_service_duration_ns": "llm.jsonl llm_request_terminal-llm_request_start; used as physical LLM work for K=2 scheduling",
            "non_llm_duration_ns": "OPERATOR_END-OPERATOR_START",
        },
        "resource_class_rules": {task.task_id: {"resource_class": task.resource_class, "basis": task.resource_basis} for task in tasks.values()},
        "nodes": [
            {
                "task_id": task.task_id,
                "semantic_operator_id": task.operator_id,
                "request_id": task.request_id,
                "semantic_operator_type": task.operator_type,
                "classification": task.classification,
                "source_sequence": task.source_sequence,
                "direct_dependency_task_ids": sorted(task.parents),
                "ready_ns": task.observed_ready_ns,
                "observed_start_ns": task.observed_start_ns,
                "observed_end_ns": task.observed_end_ns,
                "resource_class": task.resource_class,
                "resource_basis": task.resource_basis,
                "production_request_lineage": task.request_id,
                "observed_service_duration_ns": task.duration_ns,
                "request_span_duration_ns": task.request_span_duration_ns,
                "active_service_duration_ns": task.active_service_duration_ns,
                "descendant_publication_source_sequences": sorted(task.publication_descendants),
                "downstream_publication_tail_ns": task.downstream_tail_ns,
            }
            for task in sorted(tasks.values(), key=lambda item: item.task_id)
        ],
        "per_source": {str(source): source_metrics[source] for source in range(SOURCE_COUNT)},
        "input_hashes": {name: sha256_file(path) for name, path in paths.items()},
    })
    comparison_doc = _seal({
        "schema_version": "membind.meg.within-version.publication-schedule-comparison.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        "policies": ["CACHE_AFFINE", "FIFO", "PUBLICATION_CRITICALITY_FIRST"],
        "policy_definitions": {
            "CACHE_AFFINE": "offline replay priority uses observed request submission order; cache metadata is not reinterpreted",
            "FIFO": "earliest OPERATOR_READY legal-ready task first",
            "PUBLICATION_CRITICALITY_FIRST": "highest explicit descendant-to-publication tail first; not a production scheduler",
        },
        "per_source": per_source_comparison,
        "aggregate": aggregate,
        "admission_controllable_headroom_ns": admission_delta,
    })
    choice_doc = _seal({
        "schema_version": "membind.meg.within-version.llm-admission-choice-set.v1",
        "run_id": result["run_id"],
        "input_capture_payload_sha256": capture["payload_sha256"],
        **{key: value for key, value in choices.items() if key != "inversions"},
    })
    decision_doc = _seal(decision_doc)
    docs: dict[str, dict[str, Any] | str] = {
        "MEG_WITHIN_VERSION_ORACLE.json": oracle_doc,
        "MEG_PUBLICATION_SCHEDULE_COMPARISON.json": comparison_doc,
        "MEG_LLM_ADMISSION_CHOICE_SET.json": choice_doc,
        "MEG_PUBLICATION_CRITICALITY_INVERSION.json": _seal(inversion_doc),
        "MEG_WITHIN_VERSION_DECISION.json": decision_doc,
    }
    docs.update(_render_markdown(docs))
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    output = args.output_root.resolve()
    if output.exists():
        raise OracleError("oracle_output_not_fresh")
    documents = build_documents(args.capture_root)
    output.mkdir(parents=True, exist_ok=False)
    for name, value in documents.items():
        destination = output / name
        if isinstance(value, dict):
            atomic_write_json(destination, value)
        else:
            destination.write_text(value, encoding="utf-8")
    decision = documents["MEG_WITHIN_VERSION_DECISION.json"]
    assert isinstance(decision, dict)
    print(json.dumps({"output_root": str(output), "decision": decision["decision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
