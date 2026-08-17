"""Pure per-block metric derivation for the fresh aligned benchmark lane.

Lifecycle performance metrics are derived only from a complete, verified
``AlignedBlockArtifactStore`` event stream.  Quality and graph-correctness
metrics cannot be inferred from lifecycle boundaries, so callers must provide
an explicitly sealed projection bound to the same plan block and manifest.
This module never starts services, invokes a model, or mutates an artifact.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v1.aligned_artifacts import (
    inspect_aligned_block_artifacts,
    build_public_aligned_row,
)
from paper_eval.membind_v1.aligned_plan import (
    ALIGNED_METHODS,
    verify_aligned_development_plan,
)
from paper_eval.membind_v1.aligned_reduce import build_aligned_freshness_samples


QUALITY_AND_CORRECTNESS_SCHEMA = (
    "membind.paper-eval-v3.membind-v1-aligned-quality-correctness.v1"
)
NUMERICALLY_COMPARABLE = "NUMERICALLY_COMPARABLE"
GRAPH_NATIVE_PROTOCOL_DEGENERATE = "NQ_GRAPH_NATIVE_PROTOCOL_DEGENERATE"
_QUALITY_STATUSES = {
    NUMERICALLY_COMPARABLE,
    GRAPH_NATIVE_PROTOCOL_DEGENERATE,
}
_LIFECYCLE_TYPES = (
    "ARRIVAL",
    "ENQUEUED",
    "SERVICE_STARTED",
    "PUBLICATION_DURABLE",
)


class AlignedMetricsError(ValueError):
    """A complete block, quality projection, or derived metric is invalid."""


def _fail(code: str) -> AlignedMetricsError:
    return AlignedMetricsError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return value


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    result = _nonnegative_int(value, code)
    if result < 1:
        raise _fail(code)
    return result


def _number(value: object, code: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise _fail(code)
    return float(value)


def _probability(value: object, code: str) -> float:
    result = _number(value, code)
    if not 0.0 <= result <= 1.0:
        raise _fail(code)
    return result


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise _fail(code)
    try:
        int(value, 16)
    except ValueError:
        raise _fail(code) from None
    return value


def _nearest_rank(values: Sequence[int], probability: float) -> int:
    if not values:
        raise _fail("freshness sample inventory empty")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _plan_block(
    verified_plan: Mapping[str, object], block_index: object
) -> tuple[dict[str, Any], dict[str, object]]:
    try:
        plan = verify_aligned_development_plan(verified_plan)
    except ValueError:
        raise _fail("verified plan invalid") from None
    index = _nonnegative_int(block_index, "block index invalid")
    blocks = plan.get("blocks")
    if not isinstance(blocks, list) or index >= len(blocks):
        raise _fail("plan block invalid")
    block = blocks[index]
    if not isinstance(block, Mapping) or block.get("block_index") != index:
        raise _fail("plan block invalid")
    if block.get("method") not in ALIGNED_METHODS:
        raise _fail("plan block invalid")
    return plan, deepcopy(dict(block))


def _complete_inspection(
    root: Path,
    *,
    plan: Mapping[str, object],
    block: Mapping[str, object],
) -> dict[str, object]:
    try:
        inspected = inspect_aligned_block_artifacts(Path(root))
    except ValueError:
        raise _fail("aligned artifact invalid") from None
    manifest = _mapping(inspected.get("manifest"), "manifest invalid")
    checkpoint = _mapping(inspected.get("checkpoint"), "checkpoint invalid")
    # The existing inspect function verifies each local seal.  This explicit
    # projection check binds the root to the verified plan block before any
    # metric is exposed.
    expected = {
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "source_manifest_sha256": block["source_manifest_sha256"],
        "arrival_trace_sha256": block["arrival_trace_sha256"],
        "history_arrival_trace_sha256": block[
            "history_arrival_trace_sha256"
        ],
        "shared_execution_envelope_sha256": block[
            "shared_execution_envelope_sha256"
        ],
        "global_llm_admission_k": 2,
        "plan_payload_sha256": plan["payload_sha256"],
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise _fail("manifest plan block binding invalid")
    sources = plan["history_source_sha256s"].get(block["history_id"])
    if manifest.get("source_sha256s") != sources:
        raise _fail("manifest source coverage invalid")
    if checkpoint.get("terminal_status") != "COMPLETED" or checkpoint.get(
        "complete_coverage"
    ) is not True:
        raise _fail("complete coverage required")
    events = inspected.get("events")
    if not isinstance(events, list):
        raise _fail("lifecycle events invalid")
    return inspected


def _quality_body(
    *,
    plan: Mapping[str, object],
    block: Mapping[str, object],
    manifest: Mapping[str, object],
    qa_accuracy: float | None,
    evidence_recall_at_10: float,
    direct_violations: int,
    quality_status: str,
) -> dict[str, object]:
    if quality_status not in _QUALITY_STATUSES:
        raise _fail("quality status invalid")
    if qa_accuracy is None:
        if quality_status != GRAPH_NATIVE_PROTOCOL_DEGENERATE:
            raise _fail("QA accuracy missing for comparable quality status")
    else:
        qa_accuracy = _probability(qa_accuracy, "QA accuracy invalid")
    recall = _probability(evidence_recall_at_10, "Evidence Recall@10 invalid")
    violations = _nonnegative_int(direct_violations, "direct violations invalid")
    return {
        "schema_version": QUALITY_AND_CORRECTNESS_SCHEMA,
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "plan_payload_sha256": plan["payload_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "qa_accuracy": qa_accuracy,
        "evidence_recall_at_10": recall,
        "direct_violations": violations,
        "quality_status": quality_status,
    }


def build_aligned_quality_and_correctness(
    root: Path,
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    qa_accuracy: float | None,
    evidence_recall_at_10: float,
    direct_violations: int,
    quality_status: str,
) -> dict[str, object]:
    """Seal explicit quality/correctness evidence for one complete block."""

    plan, block = _plan_block(verified_plan, block_index)
    inspected = _complete_inspection(root, plan=plan, block=block)
    manifest = _mapping(inspected["manifest"], "manifest invalid")
    body = _quality_body(
        plan=plan,
        block=block,
        manifest=manifest,
        qa_accuracy=qa_accuracy,
        evidence_recall_at_10=evidence_recall_at_10,
        direct_violations=direct_violations,
        quality_status=quality_status,
    )
    return {**body, "quality_and_correctness_sha256": payload_sha256(body)}


def _verify_quality(
    value: Mapping[str, object],
    *,
    plan: Mapping[str, object],
    block: Mapping[str, object],
    manifest: Mapping[str, object],
) -> dict[str, object]:
    quality = deepcopy(dict(value))
    expected_keys = {
        "schema_version",
        "aligned_run_id",
        "block_index",
        "method",
        "history_id",
        "plan_payload_sha256",
        "manifest_sha256",
        "execution_identity_sha256",
        "qa_accuracy",
        "evidence_recall_at_10",
        "direct_violations",
        "quality_status",
        "quality_and_correctness_sha256",
    }
    if set(quality) != expected_keys or quality.get(
        "schema_version"
    ) != QUALITY_AND_CORRECTNESS_SCHEMA:
        raise _fail("quality projection invalid")
    stored = quality.get("quality_and_correctness_sha256")
    body = {
        key: item for key, item in quality.items() if key != "quality_and_correctness_sha256"
    }
    if not isinstance(stored, str) or stored != payload_sha256(body):
        raise _fail("quality projection hash invalid")
    expected = {
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block["block_index"],
        "method": block["method"],
        "history_id": block["history_id"],
        "plan_payload_sha256": plan["payload_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
    }
    if any(quality.get(key) != expected_value for key, expected_value in expected.items()):
        raise _fail("quality projection binding invalid")
    _quality_body(
        plan=plan,
        block=block,
        manifest=manifest,
        qa_accuracy=quality.get("qa_accuracy"),
        evidence_recall_at_10=quality.get("evidence_recall_at_10"),
        direct_violations=quality.get("direct_violations"),
        quality_status=quality.get("quality_status"),
    )
    return quality


def _lifecycle_rows(
    *,
    events: Sequence[Mapping[str, object]],
    source_sha256s: Sequence[str],
) -> list[dict[str, object]]:
    by_source: dict[int, dict[str, Mapping[str, object]]] = {
        sequence: {} for sequence in range(len(source_sha256s))
    }
    for event in events:
        if not isinstance(event, Mapping):
            raise _fail("lifecycle event invalid")
        sequence = event.get("source_sequence")
        event_type = event.get("event_type")
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence not in by_source
            or event_type not in _LIFECYCLE_TYPES
            or event_type in by_source[sequence]
        ):
            raise _fail("lifecycle coverage invalid")
        by_source[sequence][str(event_type)] = event
    rows: list[dict[str, object]] = []
    for sequence, source_sha256 in enumerate(source_sha256s):
        source_events = by_source[sequence]
        if set(source_events) != set(_LIFECYCLE_TYPES):
            raise _fail("lifecycle coverage invalid")
        timestamps = {
            event_type: _nonnegative_int(
                source_events[event_type].get("timestamp_ns"),
                "lifecycle timestamp invalid",
            )
            for event_type in _LIFECYCLE_TYPES
        }
        arrival = timestamps["ARRIVAL"]
        enqueue = timestamps["ENQUEUED"]
        service_start = timestamps["SERVICE_STARTED"]
        publication = timestamps["PUBLICATION_DURABLE"]
        if not arrival <= enqueue <= service_start <= publication:
            raise _fail("lifecycle timestamp order invalid")
        rows.append(
            {
                "source_sequence": sequence,
                "source_sha256": source_sha256,
                "arrival_timestamp_ns": arrival,
                "enqueue_timestamp_ns": enqueue,
                "service_start_timestamp_ns": service_start,
                "publication_timestamp_ns": publication,
                "terminal_timestamp_ns": publication,
                "queue_delay_ns": service_start - arrival,
                "service_latency_ns": publication - service_start,
                "arrival_to_publication_ns": publication - arrival,
            }
        )
    return rows


def _queue_depths(rows: Sequence[Mapping[str, object]]) -> list[int]:
    arrivals = [int(row["arrival_timestamp_ns"]) for row in rows]
    publications = [int(row["publication_timestamp_ns"]) for row in rows]
    # Logical arrival timestamps are frozen independently of JSONL append
    # order.  At an arrival instant, already published work is excluded;
    # arrivals at the same timestamp remain concurrently outstanding.
    return [
        sum(
            1
            for arrival, publication in zip(arrivals, publications, strict=True)
            if arrival <= current and publication > current
        )
        for current in arrivals
    ]


def derive_aligned_block_output(
    root: Path,
    *,
    verified_plan: Mapping[str, object],
    block_index: int,
    quality_and_correctness: Mapping[str, object],
) -> dict[str, object]:
    """Derive sealed public row and freshness samples from one complete block."""

    plan, block = _plan_block(verified_plan, block_index)
    inspected = _complete_inspection(root, plan=plan, block=block)
    manifest = _mapping(inspected["manifest"], "manifest invalid")
    quality = _verify_quality(
        quality_and_correctness,
        plan=plan,
        block=block,
        manifest=manifest,
    )
    source_sha256s = plan["history_source_sha256s"].get(block["history_id"])
    if not isinstance(source_sha256s, list) or not source_sha256s:
        raise _fail("source manifest invalid")
    events = inspected["events"]
    if not isinstance(events, list):
        raise _fail("lifecycle events invalid")
    rows = _lifecycle_rows(events=events, source_sha256s=source_sha256s)
    depths = _queue_depths(rows)
    for row, depth in zip(rows, depths, strict=True):
        row["queue_depth_at_arrival"] = depth
    freshness = [int(row["arrival_to_publication_ns"]) for row in rows]
    makespan = max(int(row["terminal_timestamp_ns"]) for row in rows) - min(
        int(row["arrival_timestamp_ns"]) for row in rows
    )
    if makespan < 1:
        raise _fail("makespan must be positive")
    qa_input = quality.get("qa_accuracy")
    metrics = {
        # A degenerate quality projection is rendered as an explicit NQ row by
        # main_table; its numeric field is a schema placeholder, never a score.
        "qa_accuracy": 0.0 if qa_input is None else float(qa_input),
        "evidence_recall_at_10": float(quality["evidence_recall_at_10"]),
        "direct_violations": int(quality["direct_violations"]),
        "p95_arrival_to_publication_ns": _nearest_rank(freshness, 0.95),
        "p99_arrival_to_publication_ns": _nearest_rank(freshness, 0.99),
        "successful_goodput_episodes_per_second": len(rows)
        * 1_000_000_000
        / makespan,
        "makespan_ns": makespan,
        "max_backlog": max(depths),
    }
    public_row = build_public_aligned_row(
        Path(root),
        verified_plan=plan,
        block_index=block_index,
        metrics=metrics,
        quality_status=str(quality["quality_status"]),
    )
    freshness_record = build_aligned_freshness_samples(
        verified_plan=plan,
        public_row=public_row,
        samples=[
            {
                "source_sequence": row["source_sequence"],
                "source_sha256": row["source_sha256"],
                "arrival_to_publication_ns": row["arrival_to_publication_ns"],
            }
            for row in rows
        ],
    )
    return {
        "schema_version": "membind.paper-eval-v3.membind-v1-aligned-metrics.v1",
        "status": "PASS",
        "aligned_run_id": block["aligned_run_id"],
        "block_index": block_index,
        "method": block["method"],
        "history_id": block["history_id"],
        "execution_identity_sha256": manifest["execution_identity_sha256"],
        "quality_and_correctness_sha256": quality[
            "quality_and_correctness_sha256"
        ],
        "quality_status": quality["quality_status"],
        "qa_accuracy_input": qa_input,
        "metrics": metrics,
        "per_source": rows,
        "public_row": public_row,
        "freshness_record": freshness_record,
    }


__all__ = [
    "AlignedMetricsError",
    "GRAPH_NATIVE_PROTOCOL_DEGENERATE",
    "NUMERICALLY_COMPARABLE",
    "QUALITY_AND_CORRECTNESS_SCHEMA",
    "build_aligned_quality_and_correctness",
    "derive_aligned_block_output",
]
