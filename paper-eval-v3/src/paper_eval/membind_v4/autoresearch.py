"""Small, append-only autoresearch ledger for the v4 lane.

The ledger intentionally contains public counters and hashes only.  Raw model
prompts/responses remain in the existing private traces, while every candidate
has an independent directory so a failed attempt is never merged.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from paper_eval.artifacts import atomic_write_json, payload_sha256
from paper_eval.membind_v4.telemetry import V4Telemetry


_CANDIDATES: dict[str, dict[str, object]] = {
    "c01": {
        "policy": "IDLE_SLOT_VALIDATED_SPEC",
        "phase_complementary": False,
        "global_k": 2,
        "speculation_distance": 1,
    },
    "c02": {
        "policy": "PHASE_COMPLEMENTARY_GATE",
        "phase_complementary": True,
        "global_k": 2,
        "speculation_distance": 1,
    },
    "c03": {
        "policy": "COST_AWARE_ADMISSION",
        "phase_complementary": True,
        "global_k": 2,
        "speculation_distance": 1,
    },
}


def candidate_config(candidate_id: str) -> dict[str, object]:
    if candidate_id not in _CANDIDATES:
        raise ValueError("candidate_unknown")
    return {
        "schema_version": "membind.paper-eval-v4.candidate.v1",
        "candidate_id": candidate_id,
        **deepcopy(_CANDIDATES[candidate_id]),
    }


def summarize_events(events: Iterable[Mapping[str, object]]) -> dict[str, object]:
    rows = [dict(event) for event in events]
    counts = Counter(str(row.get("event_type")) for row in rows)
    hits = counts.get("semantic_hit", 0)
    misses = counts.get("semantic_miss", 0)
    qualified = hits + misses
    return {
        "schema_version": "membind.paper-eval-v4.summary.v1",
        "event_count": len(rows),
        "event_counts": dict(sorted(counts.items())),
        "qualified_node_resolve_count": qualified,
        # A semantic HIT/MISS is emitted only after the stale response has
        # been checked against the exact call.  Persist that proof explicitly
        # so decision code never has to infer the mechanism gate from a name.
        "exact_validation_completed_count": qualified,
        "semantic_hit_count": hits,
        "semantic_miss_count": misses,
        "semantic_hit_rate": hits / qualified if qualified else None,
        "overlap_count": counts.get("speculation_overlap", 0),
        "speculation_launch_count": counts.get("speculation_launched", 0),
        "direct_violation_count": counts.get("direct_violation", 0),
    }


def assess_candidate(summary: Mapping[str, object]) -> dict[str, object]:
    """Apply the pre-registered c01/c02/c03 decision gates."""

    if summary.get("status") not in {"PASS", "COMPLETED"}:
        return {"decision": "STOP_FAILURE", "reason": "candidate_failed"}
    candidate_id = summary.get("candidate_id")
    source_count = summary.get("source_count")
    if candidate_id not in _CANDIDATES:
        return {"decision": "STOP_FAILURE", "reason": "candidate_id_invalid"}
    if isinstance(source_count, bool) or source_count not in {6, 12}:
        return {"decision": "STOP_FAILURE", "reason": "source_count_invalid"}

    count_fields = (
        "direct_violation_count",
        "qualified_node_resolve_count",
        "speculation_launch_count",
        "exact_validation_completed_count",
        "semantic_hit_count",
        "semantic_miss_count",
        "overlap_count",
    )
    counts: dict[str, int] = {}
    for field in count_fields:
        value = summary.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return {
                "decision": "STOP_FAILURE",
                "reason": "mechanism_evidence_invalid",
                "field": field,
            }
        counts[field] = value

    if counts["direct_violation_count"] != 0:
        return {"decision": "STOP_AND_FIX_CORRECTNESS", "reason": "direct_violation"}
    qualified = counts["qualified_node_resolve_count"]
    launches = counts["speculation_launch_count"]
    validations = counts["exact_validation_completed_count"]
    hits = counts["semantic_hit_count"]
    misses = counts["semantic_miss_count"]
    overlaps = counts["overlap_count"]
    if qualified == 0:
        return {
            "decision": "STOP_V4_NODE_RESOLVE",
            "reason": "no_qualified_node_resolve",
        }
    if launches == 0:
        return {
            "decision": "STOP_MECHANISM_NOT_TRIGGERED",
            "reason": "no_speculation_launched",
        }
    if validations == 0:
        return {
            "decision": "STOP_MECHANISM_NOT_TRIGGERED",
            "reason": "no_exact_validation_completed",
        }
    if (
        qualified != hits + misses
        or validations != qualified
        or launches < qualified
        or overlaps > launches
    ):
        return {
            "decision": "STOP_FAILURE",
            "reason": "mechanism_evidence_inconsistent",
        }
    if overlaps == 0:
        return {"decision": "STOP_V4_NODE_RESOLVE", "reason": "no_real_overlap"}

    try:
        frontier_ratio = float(summary.get("frontier_p95_service_ratio", 1.0) or 1.0)
        freshness_ratio = float(summary.get("freshness_p95_ratio", 1.0) or 1.0)
        makespan_ratio = float(summary.get("makespan_ratio", 1.0) or 1.0)
    except (TypeError, ValueError):
        return {"decision": "STOP_FAILURE", "reason": "performance_evidence_invalid"}
    if any(
        not math.isfinite(value) or value <= 0
        for value in (frontier_ratio, freshness_ratio, makespan_ratio)
    ):
        return {"decision": "STOP_FAILURE", "reason": "performance_evidence_invalid"}

    # The six-source prefix is a trend/mechanism screen only.  It can admit
    # the same candidate to its registered decision prefix, never freeze it.
    if source_count == 6:
        if frontier_ratio > 1.05:
            if candidate_id == "c03":
                return {
                    "decision": "STOP_NO_MEASURABLE_GAIN",
                    "reason": "candidate_budget_exhausted",
                }
            return {
                "decision": "TUNE_ONCE",
                "reason": "frontier_interference",
                "frontier_ratio": frontier_ratio,
            }
        if freshness_ratio > 1.05 or makespan_ratio > 1.05:
            return {
                "decision": "STOP_NO_MEASURABLE_GAIN",
                "reason": "negative_prefix_trend",
            }
        return {"decision": "EXTEND_TO_12", "reason": "mechanism_triggered"}

    # The semantic opportunity gate precedes every performance decision.  A
    # missing hidden-time measurement is intentionally equivalent to zero.
    if hits == 0:
        return {"decision": "STOP_V4_NODE_RESOLVE", "reason": "no_semantic_hit"}
    hidden_time = summary.get("hidden_critical_time_ns", 0)
    if (
        isinstance(hidden_time, bool)
        or not isinstance(hidden_time, (int, float))
        or hidden_time <= 0
    ):
        return {"decision": "STOP_V4_NODE_RESOLVE", "reason": "no_hidden_critical_time"}

    if frontier_ratio > 1.05:
        if candidate_id == "c03":
            return {
                "decision": "STOP_NO_MEASURABLE_GAIN",
                "reason": "candidate_budget_exhausted",
            }
        return {
            "decision": "TUNE_ONCE",
            "reason": "frontier_interference",
            "frontier_ratio": frontier_ratio,
        }
    if freshness_ratio <= 0.95 or makespan_ratio <= 0.95:
        return {
            "decision": "FREEZE",
            "reason": "pre_registered_gain",
            "freshness_ratio": freshness_ratio,
            "makespan_ratio": makespan_ratio,
        }
    return {"decision": "STOP_NO_MEASURABLE_GAIN", "reason": "no_pre_registered_gain"}


class CandidateStore:
    """Durable candidate writer with one fresh root per candidate."""

    def __init__(self, root: Path, candidate_id: str, source_count: int) -> None:
        self.root = root
        self.candidate_id = candidate_id
        self.source_count = source_count
        self.events_path = root / "events.jsonl"
        self._events: list[dict[str, object]] = []

    @classmethod
    def create(cls, root: Path | str, candidate_id: str, *, source_count: int) -> "CandidateStore":
        if candidate_id not in _CANDIDATES:
            raise ValueError("candidate_unknown")
        if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count <= 0:
            raise ValueError("source_count_invalid")
        candidate_root = Path(root).resolve() / "candidates" / candidate_id
        if candidate_root.exists() and any(candidate_root.iterdir()):
            raise ValueError("candidate_root_exists")
        candidate_root.mkdir(parents=True, exist_ok=False)
        body = {
            **candidate_config(candidate_id),
            "status": "RUNNING",
            "source_count": source_count,
            "created_at_ns": time.time_ns(),
        }
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(candidate_root / "candidate.json", body)
        return cls(candidate_root, candidate_id, source_count)

    def event(self, event_type: str, **fields: object) -> None:
        validated = V4Telemetry().record(event_type, **fields)
        self._load_running_manifest()
        row = {**validated, "event_sequence": len(self._events)}
        self._events.append(row)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
            stream.flush()

    def _load_running_manifest(self) -> dict[str, object]:
        path = self.root / "candidate.json"
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("candidate_manifest_unreadable") from error
        if not isinstance(body, dict):
            raise ValueError("candidate_manifest_invalid")
        digest = body.pop("payload_sha256", None)
        if not isinstance(digest, str) or digest != payload_sha256(body):
            raise ValueError("candidate_manifest_payload_hash_mismatch")
        if (
            body.get("candidate_id") != self.candidate_id
            or body.get("source_count") != self.source_count
        ):
            raise ValueError("candidate_manifest_identity_drift")
        if body.get("status") != "RUNNING":
            raise ValueError("candidate_manifest_already_terminal")
        if (self.root / "summary.json").exists() or (self.root / "failure.json").exists():
            raise ValueError("candidate_terminal_artifact_exists")
        return body

    def _write_terminal(
        self,
        *,
        artifact_path: Path,
        artifact: Mapping[str, object],
        status: str,
    ) -> None:
        body = self._load_running_manifest()
        atomic_write_json(artifact_path, artifact)
        body["status"] = status
        body["payload_sha256"] = payload_sha256(body)
        atomic_write_json(self.root / "candidate.json", body)

    def finalize(self, *, status: str, **fields: object) -> dict[str, object]:
        summary = {**summarize_events(self._events), "status": status, **fields}
        summary["candidate_id"] = self.candidate_id
        summary["source_count"] = self.source_count
        summary["payload_sha256"] = payload_sha256(summary)
        self._write_terminal(
            artifact_path=self.root / "summary.json",
            artifact=summary,
            status="COMPLETED",
        )
        return summary

    def failure(self, error: BaseException, **fields: object) -> dict[str, object]:
        body = {
            "schema_version": "membind.paper-eval-v4.failure.v1",
            "status": "FAILED_NON_MERGEABLE",
            "candidate_id": self.candidate_id,
            "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
            "error_code": str(error),
            **fields,
        }
        body["payload_sha256"] = payload_sha256(body)
        self._write_terminal(
            artifact_path=self.root / "failure.json",
            artifact=body,
            status="FAILED_NON_MERGEABLE",
        )
        return body


__all__ = ["CandidateStore", "assess_candidate", "candidate_config", "summarize_events"]
