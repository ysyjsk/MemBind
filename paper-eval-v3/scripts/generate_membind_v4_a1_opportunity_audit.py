#!/usr/bin/env python3
"""Materialize the sealed, read-only A1 opportunity audit.

The audit is derived from the v3.1 feasibility event ledger.  It never calls
an adapter, provider, graph database, or model.  The resulting JSON is a
sealed sidecar used only to authorize the registered c01/20-source
development prefix; it is not a performance reference.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402


REQUIRED_EVENTS = (
    "ARRIVAL",
    "COMPILE_STARTED",
    "PREPARED_DURABLE",
    "BIND_STARTED",
    "COMMIT_RETURNED",
    "PUBLICATION_DURABLE",
)
HISTORY_ID = "07741c45"


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _timestamp(event: dict[str, Any]) -> int:
    value = event.get("timestamp_ns")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("event_timestamp_invalid")
    return int(value)


def _arrival_target_timestamp(event: dict[str, Any]) -> int | None:
    telemetry = event.get("telemetry")
    if isinstance(telemetry, dict) and isinstance(telemetry.get("arrival_time_ns"), int):
        return int(telemetry["arrival_time_ns"])
    return None


def _llm_reference(llm_path: Path, *, source_count: int, makespan_ns: int) -> dict[str, Any]:
    """Read sealed public response usage for the untreated reference."""

    try:
        lines = llm_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError("llm_trace_unreadable") from error
    total = 0
    responses = 0
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != {"record", "record_sha256"}:
            raise ValueError("llm_trace_row_invalid")
        record = row["record"]
        if not isinstance(record, dict) or row["record_sha256"] != payload_sha256(record):
            raise ValueError("llm_trace_hash_mismatch")
        value = record.get("row")
        if not isinstance(value, dict):
            raise ValueError("llm_trace_row_invalid")
        if value.get("event_type") != "llm_transport_response":
            continue
        source = value.get("source_sequence")
        tokens = value.get("total_tokens")
        if (
            isinstance(source, bool)
            or not isinstance(source, int)
            or source < 0
            or isinstance(tokens, bool)
            or not isinstance(tokens, int)
            or tokens < 0
        ):
            raise ValueError("llm_trace_response_invalid")
        if source >= source_count:
            continue
        total += tokens
        responses += 1
    if responses == 0 or makespan_ns <= 0:
        raise ValueError("llm_trace_reference_incomplete")
    return {
        "absolute_path": str(llm_path.resolve()),
        "file_sha256": sha256_file(llm_path),
        "successful_response_count": responses,
        "successful_token_count": total,
        "useful_token_count": total,
        "useful_token_throughput_tokens_per_second": total / (makespan_ns / 1e9),
        "derivation": "sum of sealed llm_transport_response.total_tokens; untreated v3.1 has no speculative MISS waste",
    }


def build_audit(
    *,
    events_path: Path,
    block_manifest_path: Path,
    top_manifest_path: Path,
    method_plan_path: Path,
    llm_trace_path: Path | None = None,
) -> dict[str, Any]:
    block_manifest = _read_json(block_manifest_path)
    top_manifest = _read_json(top_manifest_path)
    method_plan = _read_json(method_plan_path)
    expected_history = block_manifest.get("history_id")
    if expected_history != HISTORY_ID:
        raise ValueError("history_id_invalid")
    source_count = block_manifest.get("source_count")
    if source_count != 49:
        raise ValueError("source_count_invalid")
    source_hashes = block_manifest.get("source_sha256s")
    if not isinstance(source_hashes, list) or len(source_hashes) != source_count:
        raise ValueError("source_inventory_invalid")
    canonical_trace = method_plan.get("arrival_traces", {}).get(HISTORY_ID, {})
    if not isinstance(canonical_trace, dict):
        raise ValueError("canonical_history_trace_missing")
    history_arrival_trace = canonical_trace.get("history_arrival_trace_sha256")
    if history_arrival_trace != block_manifest.get("history_arrival_trace_sha256"):
        raise ValueError("history_arrival_trace_identity_invalid")
    if method_plan.get("source_manifest_sha256") != block_manifest.get(
        "source_manifest_sha256"
    ):
        raise ValueError("source_manifest_identity_invalid")

    by_source: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    event_count = 0
    event_hash_failures: list[int] = []
    with events_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("event"), dict):
                raise ValueError(f"event_row_invalid:{line_number}")
            event = dict(row["event"])
            source = event.get("source_sequence")
            event_type = event.get("event_type")
            if isinstance(source, bool) or not isinstance(source, int):
                raise ValueError(f"event_source_invalid:{line_number}")
            if source < 0 or source >= source_count or event_type not in REQUIRED_EVENTS:
                raise ValueError(f"event_identity_invalid:{line_number}")
            expected_hash = payload_sha256(event)
            if row.get("event_sha256") != expected_hash:
                event_hash_failures.append(line_number)
            if event_type in by_source[source]:
                raise ValueError(f"duplicate_event:{source}:{event_type}")
            by_source[source][event_type] = event
            event_count += 1
    if event_hash_failures:
        raise ValueError(f"event_hash_mismatch:{event_hash_failures}")
    if event_count != source_count * len(REQUIRED_EVENTS):
        raise ValueError("event_count_invalid")

    rows: list[dict[str, Any]] = []
    opportunity_sources: list[int] = []
    previous_publication: int | None = None
    for source in range(source_count):
        events = by_source[source]
        if set(events) != set(REQUIRED_EVENTS):
            raise ValueError(f"event_set_invalid:{source}")
        source_hash = events["ARRIVAL"].get("source_sha256")
        if source_hash != source_hashes[source]:
            raise ValueError(f"source_hash_invalid:{source}")
        if any(events[event].get("source_sha256") != source_hash for event in REQUIRED_EVENTS):
            raise ValueError(f"cross_event_source_hash_invalid:{source}")
        # The observed event timestamp is the primary arrival identity, which
        # keeps the development reference exactly aligned with PREFIX_REFERENCE.
        # The coordinator's target/telemetry timestamp is retained separately.
        arrival_event_ns = _timestamp(events["ARRIVAL"])
        arrival_target_ns = _arrival_target_timestamp(events["ARRIVAL"])
        arrival_ns = arrival_event_ns
        prepared_ns = _timestamp(events["PREPARED_DURABLE"])
        publication_ns = _timestamp(events["PUBLICATION_DURABLE"])
        predecessor_publication_ns = previous_publication
        arrival_lead_ns = (
            predecessor_publication_ns - arrival_ns
            if predecessor_publication_ns is not None
            else None
        )
        prepared_lead_ns = (
            predecessor_publication_ns - prepared_ns
            if predecessor_publication_ns is not None
            else None
        )
        arrival_target_lead_ns = (
            predecessor_publication_ns - arrival_target_ns
            if predecessor_publication_ns is not None and arrival_target_ns is not None
            else None
        )
        potential = prepared_lead_ns is not None and prepared_lead_ns > 0
        if potential:
            opportunity_sources.append(source)
        rows.append(
            {
                "source_sequence": source,
                "source_sha256": source_hash,
                "arrival_event_timestamp_ns": arrival_event_ns,
                "arrival_target_timestamp_ns": arrival_target_ns,
                "arrival_timestamp_ns": arrival_ns,
                "compile_started_timestamp_ns": _timestamp(events["COMPILE_STARTED"]),
                "prepared_durable_timestamp_ns": prepared_ns,
                "bind_started_timestamp_ns": _timestamp(events["BIND_STARTED"]),
                "commit_returned_timestamp_ns": _timestamp(events["COMMIT_RETURNED"]),
                "predecessor_publication_durable_timestamp_ns": predecessor_publication_ns,
                "publication_durable_timestamp_ns": publication_ns,
                "arrival_lead_ns": arrival_lead_ns,
                "arrival_target_lead_ns": arrival_target_lead_ns,
                "prepared_lead_ns": prepared_lead_ns,
                "potential_opportunity": potential,
            }
        )
        previous_publication = publication_ns

    prefix_counts = {
        "sources_0_5": sum(row["potential_opportunity"] for row in rows[:6]),
        "sources_0_11": sum(row["potential_opportunity"] for row in rows[:12]),
        "sources_0_19": sum(row["potential_opportunity"] for row in rows[:20]),
        "full_49": sum(row["potential_opportunity"] for row in rows),
    }
    expected = {
        "sources_0_5": 0,
        "sources_0_11": 0,
        "sources_0_19": 7,
        "full_49": 22,
    }
    checks = {
        "prefix_counts_match": prefix_counts == expected,
        "first_opportunity_match": (opportunity_sources[0] if opportunity_sources else None)
        == 12,
        "event_rows_complete": event_count == 294,
        "source_inventory_match": source_hashes == [row["source_sha256"] for row in rows],
    }
    if not all(checks.values()):
        raise ValueError(f"audit_expectation_failed:{checks}")

    development_rows = [
        {
            "source_sequence": row["source_sequence"],
            "arrival_timestamp_ns": row["arrival_timestamp_ns"],
            "publication_timestamp_ns": row["publication_durable_timestamp_ns"],
            "freshness_ns": row["publication_durable_timestamp_ns"]
            - row["arrival_timestamp_ns"],
            "service_latency_ns": row["publication_durable_timestamp_ns"]
            - row["bind_started_timestamp_ns"],
        }
        for row in rows[:20]
    ]
    freshness = [int(row["freshness_ns"]) for row in development_rows]
    horizon = development_rows[-1]["publication_timestamp_ns"] - development_rows[0][
        "arrival_timestamp_ns"
    ]
    selected_llm_path = (
        events_path.parent / "llm.jsonl" if llm_trace_path is None else Path(llm_trace_path)
    )
    llm_reference = _llm_reference(
        selected_llm_path,
        source_count=20,
        makespan_ns=horizon,
    )
    development_reference = {
        "status": "PASS_DEVELOPMENT_ONLY",
        "formal_main_table_eligible": False,
        "source_count": 20,
        "source_sequences": list(range(20)),
        "makespan_ns": horizon,
        "goodput_episodes_per_second": 20 / (horizon / 1e9),
        "freshness_ns_mean": sum(freshness) / len(freshness),
        "freshness_ns_p50": _percentile(freshness, 0.50),
        "freshness_ns_p95": _percentile(freshness, 0.95),
        "freshness_ns_max": max(freshness),
        "source_rows": development_rows,
        "frontier_service_ns": [row["service_latency_ns"] for row in development_rows],
        "frontier_p95_service_ns": _percentile(
            [row["service_latency_ns"] for row in development_rows], 0.95
        ),
        "llm_reference": llm_reference,
        "derivation": "directly sliced from sealed v3.1 MemBind event timestamps; no v4 treatment and no opportunity filtering",
    }

    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.a1-opportunity-audit.v1",
        "status": "SEALED_DEVELOPMENT_EVIDENCE",
        "formal_main_table_eligible": False,
        "protocol_amendment_id": "A1",
        "history_id": HISTORY_ID,
        "development_source_count": 20,
        "source_count": 49,
        "first_opportunity_source": opportunity_sources[0],
        "opportunity_sources": opportunity_sources,
        "opportunity_counts": prefix_counts,
        "prefix_opportunity_counts": prefix_counts,
        "expected_opportunity_counts": expected,
        "expected_checks": checks,
        "input": {
            "events_absolute_path": str(events_path.resolve()),
            "events_file_sha256": sha256_file(events_path),
            "llm_trace_absolute_path": str(selected_llm_path.resolve()),
            "llm_trace_file_sha256": llm_reference["file_sha256"],
            "block_manifest_absolute_path": str(block_manifest_path.resolve()),
            "block_manifest_file_sha256": sha256_file(block_manifest_path),
            "block_manifest_sha256": block_manifest.get("manifest_sha256"),
            "top_manifest_absolute_path": str(top_manifest_path.resolve()),
            "top_manifest_file_sha256": sha256_file(top_manifest_path),
            "top_manifest_payload_sha256": top_manifest.get("payload_sha256"),
            "method_plan_absolute_path": str(method_plan_path.resolve()),
            "method_plan_file_sha256": sha256_file(method_plan_path),
            "method_plan_payload_sha256": method_plan.get("payload_sha256"),
        },
        "history_arrival_trace_sha256": history_arrival_trace,
        "arrival_trace_sha256": history_arrival_trace,
        "source_manifest_sha256": block_manifest.get("source_manifest_sha256"),
        "source_inventory_sha256": block_manifest.get("source_manifest_sha256"),
        "shared_execution_envelope_sha256": block_manifest.get(
            "shared_execution_envelope_sha256"
        ),
        "execution_identity_sha256": block_manifest.get("execution_identity_sha256"),
        "provider_execution_envelope_sha256": top_manifest.get(
            "provider_execution_envelope_sha256"
        ),
        "plan_payload_sha256": block_manifest.get("plan_payload_sha256"),
        "state_cut_certification_sha256": block_manifest.get(
            "state_cut_certification_sha256"
        ),
        "policy": block_manifest.get("policy"),
        "global_llm_admission_k": block_manifest.get("global_llm_admission_k"),
        "lookahead": block_manifest.get("lookahead"),
        "compile_workers": block_manifest.get("compile_workers"),
        "source_sha256s": source_hashes,
        "source_rows": rows,
        "development_reference_0_19": development_reference,
        "derivation": {
            "arrival_timestamp_rule": "ARRIVAL.event.timestamp_ns (observed arrival identity)",
            "arrival_target_timestamp_rule": "ARRIVAL.telemetry.arrival_time_ns when present (coordinator target, retained but not used for the primary lead)",
            "publication_timestamp_rule": "PUBLICATION_DURABLE.event.timestamp_ns",
            "prepared_timestamp_rule": "PREPARED_DURABLE.event.timestamp_ns",
            "arrival_lead_formula": "publication(i-1) - arrival(i)",
            "prepared_lead_formula": "publication(i-1) - prepared(i)",
            "potential_opportunity_formula": "prepared_lead_ns > 0",
            "treatment_independent": True,
        },
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v31/feasibility/membind-v31-feasibility-20260819-004/block-00/events.jsonl",
    )
    parser.add_argument(
        "--block-manifest",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v31/feasibility/membind-v31-feasibility-20260819-004/block-00/manifest.json",
    )
    parser.add_argument(
        "--top-manifest",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v31/feasibility/membind-v31-feasibility-20260819-004/MANIFEST.json",
    )
    parser.add_argument(
        "--method-plan",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v31/V31_METHOD_PLAN.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json",
    )
    args = parser.parse_args(argv)
    artifact = build_audit(
        events_path=args.events.resolve(),
        block_manifest_path=args.block_manifest.resolve(),
        top_manifest_path=args.top_manifest.resolve(),
        method_plan_path=args.method_plan.resolve(),
    )
    atomic_write_json(args.output.resolve(), artifact)
    print(json.dumps({"output": str(args.output.resolve()), "payload_sha256": artifact["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
