"""Deterministic reducer for the V6 full-history matched campaign.

The reducer consumes only sealed attempt artifacts.  It never reads provider
state and never infers request-level work from global vLLM counters.  A
comparison is valid only when both arms completed the same frozen history and
the proof artifacts agree on the runtime invariants.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class V6ComparisonError(ValueError):
    """One or more attempt artifacts cannot form a matched comparison."""


def _read(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V6ComparisonError(f"missing or invalid artifact: {path}") from exc
    if not isinstance(value, dict):
        raise V6ComparisonError(f"artifact must be an object: {path}")
    return value


def _attempt(root: str | Path, *, history_id: str) -> dict[str, Any]:
    resolved = Path(root).resolve()
    result = _read(resolved, f"histories/{history_id}/history_result.json")
    proof = _read(resolved, "proof.json")
    seal = _read(resolved, "seal.json")
    manifest = _read(resolved, "manifest.json")
    if result.get("status") != "PASS" or seal.get("status") != "V6_PROBE_SEALED":
        raise V6ComparisonError(f"attempt is not successfully sealed: {resolved}")
    if result.get("history_id") != history_id or result.get("source_count", 0) <= 0:
        raise V6ComparisonError(f"history identity mismatch: {resolved}")
    source_count = int(result["source_count"])
    if int(result.get("durable_frontier", -1)) != source_count - 1:
        raise V6ComparisonError(f"durable frontier is incomplete: {resolved}")
    frontier = proof.get("frontier", {})
    provider = proof.get("provider", {})
    if frontier.get("status") != "PASS" or int(frontier.get("durable_frontier", -1)) != source_count - 1:
        raise V6ComparisonError(f"frontier proof failed: {resolved}")
    capacity = int(provider.get("capacity", 0))
    if (
        provider.get("status") != "PASS"
        or int(provider.get("max_outstanding", -1)) > capacity
        or int(provider.get("max_future_outstanding", -1)) > capacity - 1
    ):
        raise V6ComparisonError(f"provider proof failed: {resolved}")
    endpoint = manifest.get("endpoint_identity")
    if endpoint != {
        "construction": "http://10.87.5.247:8000/v1/",
        "embedding": "http://10.87.5.247:8001/v1",
    }:
        raise V6ComparisonError(f"attempt endpoint is not frozen 8000/8001: {resolved}")
    logical = result.get("logical_work_summary", {})
    transport = result.get("transport_evidence", {})
    if proof.get("frontier", {}).get("status") != "PASS" or proof.get("provider", {}).get("status") != "PASS":
        raise V6ComparisonError(f"proof status is not PASS: {resolved}")
    return {
        "root": str(resolved),
        "history_id": history_id,
        "policy": result.get("policy"),
        "method": result.get("method"),
        "claim_status": result.get("claim_status"),
        "source_count": source_count,
        "durable_frontier": int(result["durable_frontier"]),
        "timer_s": int(result["lifecycle"]["build_makespan_ns"]) / 1_000_000_000,
        "provider_call_count": int(result.get("provider_call_count", 0)),
        "request_observation_count": int(result.get("request_observation_count", 0)),
        "transport_attempt_count": int(transport.get("attempt_count", 0)),
        "transport_usage_observed_count": int(transport.get("usage_observed_count", 0)),
        "transport_finish_reason_observed_count": int(transport.get("finish_reason_observed_count", 0)),
        "transport_finish_reasons": sorted(str(item) for item in transport.get("finish_reasons", [])),
        "transport_error_count": int(transport.get("transport_error_count", 0)),
        "logical_work": {
            "captured": int(logical.get("logical_captured", 0)),
            "consumed": int(logical.get("logical_consumed", 0)),
            "duplicates": int(logical.get("duplicates", 0)),
            "unconsumed": int(logical.get("unconsumed", 0)),
        },
        "replay_proof": proof.get("replay", {}),
        "request_proof": proof.get("request", {}),
        "provider_proof": provider,
        "frontier_proof": frontier,
        "overlap_pairs": len(result.get("overlap_evidence", {}).get("overlap_pairs", [])),
        "overlap": bool(result.get("overlap_evidence", {}).get("future_prepare_overlapped_native", False)),
    }


def _pair(control: Mapping[str, Any], candidate: Mapping[str, Any], *, order: str) -> dict[str, Any]:
    if control["policy"] != "matched-control" or candidate["policy"] != "v6":
        raise V6ComparisonError("each pair must contain matched-control and v6 arms")
    if control["history_id"] != candidate["history_id"] or control["source_count"] != candidate["source_count"]:
        raise V6ComparisonError("control and candidate workload identities differ")
    delta = float(control["timer_s"]) - float(candidate["timer_s"])
    return {
        "order": order,
        "control": dict(control),
        "candidate": dict(candidate),
        "timer_delta_s_control_minus_candidate": delta,
        "candidate_reduction_pct": (100.0 * delta / float(control["timer_s"])) if control["timer_s"] else None,
    }


def reduce_main_campaign(
    *,
    control_first_control: str | Path,
    control_first_candidate: str | Path,
    candidate_first_candidate: str | Path,
    candidate_first_control: str | Path,
    history_id: str = "6071bd76",
) -> dict[str, Any]:
    """Reduce the two counterbalanced full-history pairs into one artifact."""

    c1 = _attempt(control_first_control, history_id=history_id)
    v1 = _attempt(control_first_candidate, history_id=history_id)
    v2 = _attempt(candidate_first_candidate, history_id=history_id)
    c2 = _attempt(candidate_first_control, history_id=history_id)
    pairs = [_pair(c1, v1, order="control_then_candidate"), _pair(c2, v2, order="candidate_then_control")]
    all_attempts = [c1, v1, v2, c2]
    if len({item["source_count"] for item in all_attempts}) != 1 or len({item["history_id"] for item in all_attempts}) != 1:
        raise V6ComparisonError("the four arms are not one matched history")
    candidate_attempts = [v1, v2]
    replay_exact = all(
        item["logical_work"]["duplicates"] == 0
        and item["logical_work"]["unconsumed"] == 0
        and item["logical_work"]["captured"] == item["logical_work"]["consumed"]
        for item in candidate_attempts
    )
    provider_caps = {
        "capacity": sorted({int(item["provider_proof"]["capacity"]) for item in all_attempts}),
        "max_outstanding": max(int(item["provider_proof"]["max_outstanding"]) for item in all_attempts),
        "max_future_outstanding": max(int(item["provider_proof"]["max_future_outstanding"]) for item in all_attempts),
    }
    deltas = [float(pair["timer_delta_s_control_minus_candidate"]) for pair in pairs]
    if not replay_exact:
        raise V6ComparisonError("candidate replay is not exact single-consume")
    ordered_deltas = sorted(deltas)
    middle = len(ordered_deltas) // 2
    median_delta = ordered_deltas[middle] if len(ordered_deltas) % 2 else (ordered_deltas[middle - 1] + ordered_deltas[middle]) / 2
    return {
        "schema_version": "membind.v6.main-comparison.v1",
        "status": "PASS",
        "claim_status": "QUALIFICATION_ONLY",
        "claim_boundary": "single development history; two counterbalanced pairs; no multi-history or QA correctness claim",
        "history_id": history_id,
        "source_count": c1["source_count"],
        "endpoint_identity": {
            "construction": "http://10.87.5.247:8000/v1/",
            "embedding": "http://10.87.5.247:8001/v1",
        },
        "pairs": pairs,
        "paired_timer_summary": {
            "deltas_s_control_minus_candidate": deltas,
            "mean_delta_s": sum(deltas) / len(deltas),
            "median_delta_s": median_delta,
            "min_delta_s": min(deltas),
            "max_delta_s": max(deltas),
        },
        "mechanism_evidence": {
            "candidate_replay_exact_single_consume": replay_exact,
            "candidate_replay_captured": [item["logical_work"]["captured"] for item in candidate_attempts],
            "candidate_replay_consumed": [item["logical_work"]["consumed"] for item in candidate_attempts],
            "candidate_request_matches": [int(item["request_proof"].get("match_count", 0)) for item in candidate_attempts],
            "candidate_request_misses": [int(item["request_proof"].get("miss_count", 0)) for item in candidate_attempts],
            "candidate_transport_attempts": [item["transport_attempt_count"] for item in candidate_attempts],
            "control_transport_attempts": [c1["transport_attempt_count"], c2["transport_attempt_count"]],
            "future_native_overlap": [item["candidate"]["overlap"] for item in pairs],
            "overlap_pairs": [item["candidate"]["overlap_pairs"] for item in pairs],
        },
        "provider_invariants": {
            **provider_caps,
            "all_frontier_proofs_pass": all(item["frontier_proof"].get("status") == "PASS" for item in all_attempts),
            "all_provider_proofs_pass": all(item["provider_proof"].get("status") == "PASS" for item in all_attempts),
        },
        "transport_evidence": {
            "all_usage_observed": all(item["transport_usage_observed_count"] == item["transport_attempt_count"] for item in all_attempts),
            "all_finish_reasons_observed": all(item["transport_finish_reason_observed_count"] == item["transport_attempt_count"] for item in all_attempts),
            "transport_errors": sum(item["transport_error_count"] for item in all_attempts),
            "finish_reasons_by_attempt": [item["transport_finish_reasons"] for item in all_attempts],
        },
        "qa": {"status": "INVALID_RETAINED", "quality_claim": False},
        "attempt_roots": [item["root"] for item in all_attempts],
    }


__all__ = ["V6ComparisonError", "reduce_main_campaign"]
