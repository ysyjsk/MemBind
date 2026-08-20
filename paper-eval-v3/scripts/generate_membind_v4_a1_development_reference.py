#!/usr/bin/env python3
"""Extract the aligned 0..19 v3.1 development reference (read-only)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402


def _percentile(values: list[int], quantile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * weight)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object_required:{path}")
    return value


def build_reference(*, audit_path: Path, prefix_reference_path: Path) -> dict[str, Any]:
    audit = _read(audit_path)
    digest = audit.get("payload_sha256")
    unsigned = {key: value for key, value in audit.items() if key != "payload_sha256"}
    if not isinstance(digest, str) or digest != payload_sha256(unsigned):
        raise ValueError("audit_payload_hash_invalid")
    if audit.get("history_id") != "07741c45" or audit.get("source_count") != 49:
        raise ValueError("audit_identity_invalid")
    rows = audit.get("development_reference_0_19", {}).get("source_rows")
    if not isinstance(rows, list) or len(rows) != 20:
        raise ValueError("development_rows_invalid")
    rows = [dict(row) for row in rows]
    freshness = [int(row["freshness_ns"]) for row in rows]
    horizon = int(rows[-1]["publication_timestamp_ns"]) - int(
        rows[0]["arrival_timestamp_ns"]
    )

    # The existing 6/12 reference is an independent cross-check, not a source
    # of values for the 20-source projection.
    prefix = _read(prefix_reference_path)
    selected = prefix.get("method_sources", {}).get("MemBind", {}).get("prefixes", {})
    cross_checks: dict[str, Any] = {}
    for key, count in (("sources_0_5", 6), ("sources_0_11", 12)):
        expected = selected.get(key)
        if not isinstance(expected, dict):
            raise ValueError(f"prefix_reference_missing:{key}")
        subset = rows[:count]
        values = [int(row["freshness_ns"]) for row in subset]
        subset_horizon = int(subset[-1]["publication_timestamp_ns"]) - int(
            subset[0]["arrival_timestamp_ns"]
        )
        observed = {
            "source_count": count,
            "makespan_ns": subset_horizon,
            "freshness_ns_p95": _percentile(values, 0.95),
            "freshness_ns_p50": _percentile(values, 0.50),
        }
        cross_checks[key] = {
            "observed": observed,
            "expected": {
                "makespan_ns": expected.get("makespan_ns"),
                "freshness_ns_p95": expected.get("freshness_ns_p95"),
                "freshness_ns_p50": expected.get("freshness_ns_p50"),
            },
            "match": all(observed[name] == expected.get(name) for name in (
                "makespan_ns", "freshness_ns_p95", "freshness_ns_p50"
            )),
        }
    if not all(row["match"] for row in cross_checks.values()):
        raise ValueError("prefix_cross_check_failed")

    body: dict[str, Any] = {
        "schema_version": "membind.paper-eval-v4.a1-development-reference.v1",
        "status": "PASS_DEVELOPMENT_ONLY",
        "formal_main_table_eligible": False,
        "protocol_amendment_id": "A1",
        "history_id": "07741c45",
        "source_count": 20,
        "source_prefix": "0..19",
        "history_arrival_trace_sha256": audit.get("history_arrival_trace_sha256"),
        "arrival_trace_sha256": audit.get("arrival_trace_sha256"),
        "source_manifest_sha256": audit.get("source_manifest_sha256"),
        "shared_execution_envelope_sha256": audit.get("shared_execution_envelope_sha256"),
        "provider_execution_envelope_sha256": audit.get(
            "provider_execution_envelope_sha256"
        ),
        "execution_identity_sha256": audit.get("execution_identity_sha256"),
        "audit_binding": {
            "absolute_path": str(audit_path.resolve()),
            "file_sha256": sha256_file(audit_path),
            "payload_sha256": digest,
        },
        "prefix_reference_cross_checks": cross_checks,
        "performance": {
            "makespan_ns": horizon,
            "goodput_episodes_per_second": 20 / (horizon / 1e9),
            "freshness_ns_mean": sum(freshness) / len(freshness),
            "freshness_ns_p50": _percentile(freshness, 0.50),
            "freshness_ns_p95": _percentile(freshness, 0.95),
            "freshness_ns_max": max(freshness),
            "frontier_service_ns": [int(row["service_latency_ns"]) for row in rows],
            "frontier_p95_service_ns": audit.get("development_reference_0_19", {}).get(
                "frontier_p95_service_ns"
            ),
            "llm_reference": audit.get("development_reference_0_19", {}).get(
                "llm_reference"
            ),
        },
        "source_rows": rows,
        "limitations": [
            "This is a development trend reference extracted from untreated sealed v3.1 events.",
            "It is not a formal fair comparator and does not authorize a four-history run.",
            "The A1 live run must use its own fresh namespace and preserve the complete arrival trace.",
        ],
    }
    body["payload_sha256"] = payload_sha256(body)
    return body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json",
    )
    parser.add_argument(
        "--prefix-reference",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v4/PREFIX_REFERENCE.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT
        / "artifacts/paper_eval/membind_v4/protocol_amendment_a1/V4_A1_DEVELOPMENT_REFERENCE.json",
    )
    args = parser.parse_args(argv)
    artifact = build_reference(
        audit_path=args.audit.resolve(), prefix_reference_path=args.prefix_reference.resolve()
    )
    atomic_write_json(args.output.resolve(), artifact)
    print(json.dumps({"output": str(args.output.resolve()), "payload_sha256": artifact["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
