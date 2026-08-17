"""Shared, content-safe observability contract for Native and later methods.

The module is intentionally pure: it does not contact Graphiti, vLLM, Neo4j,
Reader, or Judge services.  Live runners append raw (sanitized) streams and
use these functions to derive reproducible per-episode and per-history views.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping, Sequence


OBSERVABILITY_SCHEMA_VERSION = "membind.paper-eval-v3.unified-observability.v1"
RAW_STREAMS = (
    "spans.jsonl",
    "events.jsonl",
    "llm.jsonl",
    "embedding.jsonl",
    "db.jsonl",
    "graph_work.jsonl",
    "queue.jsonl",
    "quality.jsonl",
    "resource.jsonl",
)

PRIMARY_METRICS = (
    "qa_accuracy",
    "evidence_recall_at_10",
    "direct_violations",
    "p95_freshness_ns",
    "successful_goodput",
    "makespan_ns",
)
SECONDARY_METRICS = ("p99_freshness_ns", "max_backlog")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FIELDS = {
    "answer",
    "authorization",
    "body",
    "content",
    "cypher",
    "exception_message",
    "messages",
    "parameters",
    "params",
    "prompt",
    "question",
    "query",
    "raw_prompt",
    "raw_response",
    "reference",
    "system_prompt",
    "traceback",
    "user_prompt",
}

# These names identify content-bearing payloads.  A prompt *label* (for
# example ``prompt_name=extract_nodes``) is safe metadata and is intentionally
# allowed; the contract rejects the prompt text itself, not the phase label.
_FORBIDDEN_SUBSTRINGS = (
    "raw_prompt",
    "user_prompt",
    "system_prompt",
    "prompt_text",
    "prompt_content",
    "raw_response",
    "response_text",
    "answer_text",
    "question_text",
)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be nonempty")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class ObservabilityIdentity:
    """Stable identity attached to every stream record."""

    run_id: str
    history_id: str
    question_id: str
    episode_id: str
    source_sequence: int
    method: str
    repeat_id: int

    def __post_init__(self) -> None:
        for field in ("run_id", "history_id", "question_id", "episode_id", "method"):
            _require_text(getattr(self, field), field)
        _nonnegative_int(self.source_sequence, "source_sequence")
        _nonnegative_int(self.repeat_id, "repeat_id")
        if self.question_id != self.history_id:
            raise ValueError("question_id must equal history_id for LongMemEval history runs")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validate_content_safe(value: Any, *, path: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).casefold()
            if name in _FORBIDDEN_FIELDS or name in {"question", "answer"} or any(
                token in name for token in _FORBIDDEN_SUBSTRINGS
            ):
                raise ValueError(f"content-bearing field is forbidden: {path}.{key}")
            _validate_content_safe(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_content_safe(child, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (str, int, bool)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError(f"unsupported evidence scalar at {path}")


def validate_raw_quality_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a redacted quality record.

    "Raw" means the complete structured evidence can be reconstructed from a
    private, access-controlled source; the durable public projection contains
    hashes, lengths, parsed labels, status, and timing, never prompt/answer
    content.
    """

    if not isinstance(value, Mapping):
        raise ValueError("quality evidence must be an object")
    _validate_content_safe(value)
    result = dict(value)
    for field in ("question_sha256", "answer_sha256", "judge_output_sha256"):
        if field in result and not _SHA256_RE.fullmatch(str(result[field])):
            raise ValueError(f"{field} must be a lowercase SHA256")
    return result


def _duration(span: Mapping[str, Any]) -> tuple[int, int, int]:
    start = _nonnegative_int(span.get("start_ns"), "span.start_ns")
    end = _nonnegative_int(span.get("end_ns"), "span.end_ns")
    if end < start:
        raise ValueError("span.end_ns precedes span.start_ns")
    return start, end, end - start


def _interval_union(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    total = 0
    current_start: int | None = None
    current_end: int | None = None
    for start, end in ordered:
        if current_start is None:
            current_start, current_end = start, end
        elif start > int(current_end):
            total += int(current_end) - int(current_start)
            current_start, current_end = start, end
        else:
            current_end = max(int(current_end), end)
    if current_start is not None:
        total += int(current_end) - int(current_start)
    return total


def _nearest_rank(values: Sequence[int], probability: float) -> int:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return 0
    rank = max(1, math.ceil(float(probability) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _distribution(values: Sequence[int]) -> dict[str, int | float | None]:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "mean": 0,
            "p50": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
            "max": 0,
            "tail_amplification": None,
        }
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
        "tail_amplification": (p99 / p50 if p50 > 0 else None),
    }


def _safe_metadata(span: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = span.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("span.metadata must be an object")
    _validate_content_safe(metadata)
    return metadata


def validate_observability_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one public stream row without accepting content payloads."""

    if not isinstance(value, Mapping):
        raise ValueError("observability record must be an object")
    record = dict(value)
    _validate_content_safe(record)
    required = (
        "run_id",
        "history_id",
        "question_id",
        "episode_id",
        "source_sequence",
        "method",
        "repeat_id",
        "stream",
    )
    if any(field not in record for field in required):
        raise ValueError("observability record identity is incomplete")
    identity = ObservabilityIdentity(
        run_id=record["run_id"],
        history_id=record["history_id"],
        question_id=record["question_id"],
        episode_id=record["episode_id"],
        source_sequence=record["source_sequence"],
        method=record["method"],
        repeat_id=record["repeat_id"],
    )
    stream = record["stream"]
    if stream not in {name.removesuffix(".jsonl") for name in RAW_STREAMS}:
        raise ValueError("observability stream is invalid")
    record.update(identity.to_dict())
    return record


def project_operation_views(
    spans: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Project one sanitized span sequence into deterministic operation views."""

    views: dict[str, list[dict[str, Any]]] = {
        "spans": [],
        "llm": [],
        "embedding": [],
        "db": [],
        "errors": [],
        "events": [],
    }
    for span in spans:
        if not isinstance(span, Mapping):
            raise ValueError("span must be an object")
        safe = dict(span)
        _validate_content_safe(safe)
        phase = safe.get("phase")
        if not isinstance(phase, str) or not phase:
            raise ValueError("span.phase must be nonempty")
        views["spans"].append(safe)
        if phase in {"llm", "llm-transport"}:
            views["llm"].append(safe)
        elif phase in {"embedding", "candidate-embedding"}:
            views["embedding"].append(safe)
        elif phase in {"database", "database-transaction"}:
            views["db"].append(safe)
        else:
            views["events"].append(safe)
        if safe.get("status") not in {None, "ok", "success", "completed"}:
            views["errors"].append(safe)
    return views


def derive_queue_metrics(
    events: Sequence[Mapping[str, Any]],
    *,
    serial_baseline: bool = False,
) -> dict[str, Any]:
    """Derive queue diagnostics from state-change samples.

    ``queue_area_ns_items`` is the integral of queue depth over wall time.  It
    is intentionally not inferred from episode latency.  A serial U0 run has
    no offered-load queue, so it receives an explicit not-applicable status.
    """

    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise ValueError("queue events must be a sequence")
    normalized: list[tuple[int, int]] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise ValueError("queue event must be an object")
        timestamp = _nonnegative_int(event.get("timestamp_ns"), "timestamp_ns")
        depth = _nonnegative_int(event.get("queue_depth"), "queue_depth")
        if normalized and timestamp < normalized[-1][0]:
            raise ValueError("queue event timestamps are not monotonic")
        normalized.append((timestamp, depth))
    status = (
        "NOT_APPLICABLE_SERIAL_BASELINE"
        if serial_baseline
        else ("UNAVAILABLE_NO_EVENTS" if not normalized else "OBSERVED")
    )
    depths = [depth for _, depth in normalized]
    area = 0
    duration = 0
    for (start, depth), (end, _next_depth) in zip(normalized, normalized[1:]):
        duration += end - start
        area += depth * (end - start)
    return {
        "status": status,
        "sample_count": len(normalized),
        "queue_area_ns_items": area,
        "observation_duration_ns": duration,
        "mean_backlog": (area / duration if duration else 0.0),
        "p95_backlog": _nearest_rank(depths, 0.95) if depths else None,
        "max_backlog": None if serial_baseline or not depths else max(depths),
    }


def validate_attempt_outcomes(
    *,
    expected_sequences: Sequence[int],
    outcomes: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Require exactly one terminal outcome for every expected source."""

    expected = [int(value) for value in expected_sequences]
    if expected != list(range(len(expected))) or len(set(expected)) != len(expected):
        raise ValueError("expected source sequences are not contiguous")
    observed: dict[int, str] = {}
    allowed = {"published", "failed", "censored"}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise ValueError("attempt outcome must be an object")
        sequence = outcome.get("source_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise ValueError("attempt outcome source sequence is invalid")
        if sequence in observed or sequence not in expected:
            raise ValueError("attempt outcome source sequence is duplicate/unexpected")
        status = outcome.get("status")
        if status not in allowed:
            raise ValueError("attempt outcome status is invalid")
        observed[sequence] = str(status)
    if set(observed) != set(expected):
        raise ValueError("attempt outcomes are incomplete")
    return {
        "expected": len(expected),
        "published": sum(status == "published" for status in observed.values()),
        "failed": sum(status == "failed" for status in observed.values()),
        "censored": sum(status == "censored" for status in observed.values()),
    }


def _metadata_count(metadata: Mapping[str, Any], name: str) -> int:
    value = metadata.get(name, 0)
    if value is None:
        return 0
    return _nonnegative_int(value, f"span.metadata.{name}")


def _validate_queue_event(value: Mapping[str, Any]) -> dict[str, int | None]:
    if not isinstance(value, Mapping):
        raise ValueError("queue event must be an object")
    names = (
        "arrival_ts_ns",
        "enqueue_ts_ns",
        "service_start_ts_ns",
        "publication_ts_ns",
        "terminal_ts_ns",
    )
    result: dict[str, int | None] = {}
    for name in names:
        raw = value.get(name)
        if raw is None:
            result[name] = None
        else:
            result[name] = _nonnegative_int(raw, name)
    depth = _nonnegative_int(value.get("queue_depth_at_enqueue", 0), "queue_depth_at_enqueue")
    result["queue_depth_at_enqueue"] = depth
    present = [result[name] for name in names if result[name] is not None]
    if present != sorted(present):
        raise ValueError("queue timestamps are not monotonic")
    return result


def derive_episode_metrics(
    *,
    identity: ObservabilityIdentity,
    spans: Sequence[Mapping[str, Any]],
    queue_event: Mapping[str, Any],
    graph_work: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive one episode without summing nested phase durations."""

    if not spans:
        raise ValueError("episode requires at least one span")
    root = [span for span in spans if span.get("phase") == "add-episode"]
    if len(root) != 1:
        raise ValueError("episode requires exactly one add-episode root")
    root_start, root_end, _ = _duration(root[0])
    queue = _validate_queue_event(queue_event)
    if queue["service_start_ts_ns"] is None:
        raise ValueError("service_start_ts_ns is required for a completed episode")
    if queue["publication_ts_ns"] is None or queue["terminal_ts_ns"] is None:
        raise ValueError("publication and terminal timestamps are required")
    arrival = queue["arrival_ts_ns"]
    if arrival is None:
        raise ValueError("arrival_ts_ns is required")
    phase_intervals: dict[str, list[tuple[int, int]]] = {}
    phase_work: dict[str, dict[str, int]] = {}
    for span in spans:
        phase = _require_text(span.get("phase"), "span.phase")
        start, end, _ = _duration(span)
        phase_intervals.setdefault(phase, []).append((start, end))
        metadata = _safe_metadata(span)
        work = phase_work.setdefault(phase, {})
        for field in ("input_tokens", "output_tokens", "text_count", "candidate_count", "candidate_query_count"):
            if field in metadata:
                work[field] = work.get(field, 0) + _metadata_count(metadata, field)
    phase_metrics: dict[str, Any] = {}
    for phase, intervals in sorted(phase_intervals.items()):
        duration = _interval_union(intervals)
        phase_metrics[phase] = {
            "duration_ns": duration,
            "span_count": len(intervals),
            **phase_work.get(phase, {}),
        }
    graph = dict(graph_work or {})
    _validate_content_safe(graph)
    for before, after, delta in (
        ("nodes_before", "nodes_after", "node_delta"),
        ("relationships_before", "relationships_after", "relationship_delta"),
        ("episodic_nodes_before", "episodic_nodes_after", "episodic_node_delta"),
        ("entity_nodes_before", "entity_nodes_after", "entity_node_delta"),
    ):
        if before in graph and after in graph:
            graph[delta] = _nonnegative_int(graph[after], after) - _nonnegative_int(graph[before], before)
    latency = {
        "queue_delay": int(queue["service_start_ts_ns"]) - int(arrival),
        "service": int(queue["publication_ts_ns"]) - int(queue["service_start_ts_ns"]),
        "freshness": int(queue["publication_ts_ns"]) - int(arrival),
        "terminal": int(queue["terminal_ts_ns"]) - int(arrival),
    }
    if any(value < 0 for value in latency.values()):
        raise ValueError("derived latency is negative")
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "identity": identity.to_dict(),
        "root_span": {"start_ns": root_start, "end_ns": root_end},
        "latency_ns": latency,
        "queue": queue,
        "phase_metrics": phase_metrics,
        "graph_work": graph,
        "status": "completed",
    }


def aggregate_history_metrics(
    *,
    identity: ObservabilityIdentity,
    episode_metrics: Sequence[Mapping[str, Any]],
    quality: Mapping[str, Any] | None = None,
    direct_violations: int = 0,
    serial_baseline: bool = False,
) -> dict[str, Any]:
    """Build Level-2 history metrics from Level-1 episode rows.

    ``serial_baseline`` is explicit because an observed depth of zero is not
    equivalent to an absent queue.  U0 uses the former's not-applicable
    semantics; concurrent methods can retain an observed numeric backlog.
    """

    if not episode_metrics:
        raise ValueError("history requires episode metrics")
    if direct_violations < 0:
        raise ValueError("direct_violations must be nonnegative")
    identities = [row.get("identity") for row in episode_metrics]
    if any(not isinstance(value, Mapping) for value in identities):
        raise ValueError("episode identity is missing")
    if any(
        value.get("history_id") != identity.history_id
        or value.get("question_id") != identity.question_id
        or value.get("method") != identity.method
        or value.get("repeat_id") != identity.repeat_id
        for value in identities
    ):
        raise ValueError("episode identity does not match history")
    ordered = sorted(episode_metrics, key=lambda row: int(row["identity"]["source_sequence"]))
    sequences = [int(row["identity"]["source_sequence"]) for row in ordered]
    if sequences != list(range(len(ordered))):
        raise ValueError("episode source sequences are not contiguous")
    freshness = [int(row["latency_ns"]["freshness"]) for row in ordered]
    service = [int(row["latency_ns"]["service"]) for row in ordered]
    queue_delay = [int(row["latency_ns"]["queue_delay"]) for row in ordered]
    terminal = [int(row["latency_ns"]["terminal"]) for row in ordered]
    arrivals = [int(row["queue"]["arrival_ts_ns"]) for row in ordered]
    terminals = [int(row["queue"]["terminal_ts_ns"]) for row in ordered]
    makespan = max(terminals) - min(arrivals)
    goodput = len(ordered) * 1_000_000_000 / makespan if makespan > 0 else 0.0
    work: dict[str, int] = {}
    graph_totals: dict[str, int] = {}
    for row in ordered:
        for phase_name, phase in row.get("phase_metrics", {}).items():
            span_count = int(phase.get("span_count", 0))
            if phase_name == "llm":
                work["llm_logical_calls"] = work.get("llm_logical_calls", 0) + span_count
                for source, target in (
                    ("input_tokens", "llm_input_tokens"),
                    ("output_tokens", "llm_output_tokens"),
                ):
                    if source in phase:
                        work[target] = work.get(target, 0) + int(phase[source])
            elif phase_name == "llm-transport":
                work["llm_transport_attempts"] = work.get("llm_transport_attempts", 0) + span_count
            elif phase_name == "embedding":
                work["embedding_calls"] = work.get("embedding_calls", 0) + span_count
                if "text_count" in phase:
                    work["embedding_items"] = work.get("embedding_items", 0) + int(phase["text_count"])
            elif phase_name == "candidate-embedding":
                work["candidate_embedding_spans"] = work.get("candidate_embedding_spans", 0) + span_count
                if "text_count" in phase:
                    work["candidate_embedding_items"] = work.get("candidate_embedding_items", 0) + int(phase["text_count"])
            elif phase_name == "database":
                work["db_operations"] = work.get("db_operations", 0) + span_count
            elif phase_name == "database-transaction":
                work["db_transactions"] = work.get("db_transactions", 0) + span_count
            for field in ("candidate_count", "candidate_query_count"):
                if field in phase:
                    work[field] = work.get(field, 0) + int(phase[field])
        for field, value in row.get("graph_work", {}).items():
            if field.endswith("_delta") and isinstance(value, int):
                graph_totals[field] = graph_totals.get(field, 0) + value
    backlog = [int(row["queue"].get("queue_depth_at_enqueue", 0)) for row in ordered]
    backlog_status = (
        "NOT_APPLICABLE_SERIAL_BASELINE"
        if serial_baseline
        else ("OBSERVED" if backlog else "UNAVAILABLE_NO_EVENTS")
    )
    safe_quality = validate_raw_quality_evidence(dict(quality or {}))
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "identity": {
            "run_id": identity.run_id,
            "history_id": identity.history_id,
            "question_id": identity.question_id,
            "method": identity.method,
            "repeat_id": identity.repeat_id,
        },
        "episode_count": len(ordered),
        "metrics": {
            "qa_accuracy": safe_quality.get("qa_accuracy"),
            "evidence_recall_at_10": safe_quality.get("evidence_recall_at_10"),
            "direct_violations": direct_violations,
            "p95_freshness_ns": _nearest_rank(freshness, 0.95),
            "p99_freshness_ns": _nearest_rank(freshness, 0.99),
            "successful_goodput": goodput,
            "successful_goodput_unit": "episodes_per_second",
            "makespan_ns": makespan,
            "max_backlog": None if serial_baseline or not backlog else max(backlog),
            "max_backlog_status": backlog_status,
        },
        "latency_distributions": {
            "queue_delay_ns": _distribution(queue_delay),
            "service_ns": _distribution(service),
            "freshness_ns": _distribution(freshness),
            "terminal_ns": _distribution(terminal),
        },
        "work_volume": work,
        "graph_work": graph_totals,
        "quality": safe_quality,
        "episode_metrics": [dict(row) for row in ordered],
    }


__all__ = [
    "OBSERVABILITY_SCHEMA_VERSION",
    "PRIMARY_METRICS",
    "SECONDARY_METRICS",
    "RAW_STREAMS",
    "ObservabilityIdentity",
    "aggregate_history_metrics",
    "derive_queue_metrics",
    "derive_episode_metrics",
    "project_operation_views",
    "validate_attempt_outcomes",
    "validate_observability_record",
    "validate_raw_quality_evidence",
]
