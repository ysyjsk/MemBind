#!/usr/bin/env python3
"""Recompute token/work totals from a completed V7-FRESH trace.

The first 6-source qualification predated the runner fix and summed logical
and transport usage together.  This tool writes a new correction artifact,
without modifying the original result or construction namespace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.run_root.resolve()
    result = json.loads((root / "RESULT.json").read_text(encoding="utf-8"))
    spans = [json.loads(line) for line in (root / "provider_events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    routes = json.loads((root / "ROUTE_EVENTS.json").read_text(encoding="utf-8"))["events"]
    logical = [row for row in spans if row.get("phase") == "llm" and row.get("operation_class") == "logical-call"]
    transport = [row for row in spans if row.get("phase") == "llm-transport"]
    def usage(rows: list[dict[str, Any]], key: str) -> int:
        return sum(int((row.get("metadata") or {}).get(key) or 0) for row in rows)
    work = dict(result.get("work_accounting") or {})
    work.update({
        "schema_version": "membind.v7b.work-accounting.v2",
        "accounting_status": "CORRECTED_TRANSPORT_DEDUPLICATED",
        "trace_span_count": len(spans),
        "llm_logical_calls": len(logical),
        "llm_transport_attempts": len(transport),
        "llm_input_tokens": usage(transport, "input_tokens"),
        "llm_output_tokens": usage(transport, "output_tokens"),
        "llm_logical_input_tokens": usage(logical, "input_tokens"),
        "llm_logical_output_tokens": usage(logical, "output_tokens"),
        "provider_calls_observed": len(transport),
        "physical_route_attempts": sum(1 for row in routes if row.get("event") == "LLM_ROUTE"),
        "route_endpoint_counts": {
            endpoint: sum(1 for row in routes if row.get("event") == "LLM_ROUTE" and row.get("endpoint_id") == endpoint)
            for endpoint in sorted({str(row.get("endpoint_id")) for row in routes if row.get("event") == "LLM_ROUTE"})
        },
        "route_region_counts": {
            region: sum(1 for row in routes if row.get("event") == "LLM_ROUTE" and row.get("region") == region)
            for region in sorted({str(row.get("region")) for row in routes if row.get("event") == "LLM_ROUTE"})
        },
        "route_scope_contract": "LOGICAL_REGION_LABELS_CAPACITY_WEIGHTED_POOL_NOT_HARD_AFFINITY",
    })
    corrected = {
        "schema_version": "membind.v7b.fresh-accounting-correction.v1",
        "status": "PASS",
        "method": "V7_FRESH",
        "run_id": result.get("run_id"),
        "source_count": result.get("source_count"),
        "namespace": result.get("namespace"),
        "original_result_sha256": _sha_file(root / "RESULT.json"),
        "provider_events_sha256": _sha_file(root / "provider_events.jsonl"),
        "work_accounting": work,
        "correction_reason": "original runner summed logical and physical token metadata; transport spans are the physical work denominator",
        "construction_result_unchanged": True,
        "quality_status": result.get("quality_status"),
    }
    out = root / "RESULT_ACCOUNTING_CORRECTED.json"
    if out.exists():
        raise SystemExit(f"refusing to overwrite existing artifact: {out}")
    with out.open("x", encoding="utf-8") as handle:
        json.dump(corrected, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
    print(json.dumps({"status": "PASS", "output": str(out), "llm_transport_attempts": len(transport), "llm_input_tokens": work["llm_input_tokens"], "llm_output_tokens": work["llm_output_tokens"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
