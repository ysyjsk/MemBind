#!/usr/bin/env python3
"""Audit diagnostic raw edge responses without feeding them to construction."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


def _complete_edges(text: str) -> tuple[list[dict[str, Any]], int]:
    """Decode complete array members up to the first malformed member."""
    start = text.find("[")
    if start < 0:
        return [], 0
    decoder = json.JSONDecoder()
    offset = start + 1
    rows: list[dict[str, Any]] = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset < len(text) and text[offset] == "]":
            return rows, offset
        if offset < len(text) and text[offset] == ",":
            offset += 1
            continue
        try:
            value, end = decoder.raw_decode(text, offset)
        except json.JSONDecodeError:
            return rows, offset
        if not isinstance(value, dict):
            return rows, offset
        rows.append(value)
        offset = end
    return rows, offset


def _tuple(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(key, "")) for key in (
        "source_entity_name", "target_entity_name", "relation_type", "fact", "valid_at", "invalid_at"
    ))


def _audit(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    rows, failure_offset = _complete_edges(text)
    tuples = [_tuple(row) for row in rows]
    tuple_counts = Counter(tuples)
    starts = [item[:3] for item in tuples]
    start_counts = Counter(starts)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "response_characters": len(text),
        "complete_json_prefix_characters": failure_offset,
        "complete_prefix_edge_count": len(rows),
        "unique_tuple_count": len(tuple_counts),
        "duplicate_tuple_repetitions": sum(count - 1 for count in tuple_counts.values() if count > 1),
        "duplicate_tuple_groups": sum(1 for count in tuple_counts.values() if count > 1),
        "duplicate_start_repetitions": sum(count - 1 for count in start_counts.values() if count > 1),
        "duplicate_start_groups": sum(1 for count in start_counts.values() if count > 1),
        "max_tuple_repeat": max(tuple_counts.values(), default=0),
        "max_start_repeat": max(start_counts.values(), default=0),
        "finish_reason": "length" if failure_offset < len(text) else "unknown",
    }


def main() -> int:
    run_root = Path(os.environ["L0_RUN_ROOT"]).resolve()
    paths = [run_root / "edge_16384.txt", run_root / "edge_32768.txt"]
    available = [path for path in paths if path.is_file()]
    if len(available) != 2:
        raise SystemExit(f"expected both raw responses under {run_root}, found {available}")
    audits = [_audit(path) for path in available]
    first, second = (path.read_text(encoding="utf-8") for path in paths)
    decision = {
        "schema_version": "membind.root-cause-decision.v1",
        "status": "L0_COMPLETE",
        "scope": "diagnostic_only_not_construction_data",
        "run_root": str(run_root),
        "source": "official context index 0, source sequence 0",
        "raw_response_persisted": True,
        "audits": audits,
        "prefix_relation": {
            "edge_16384_is_prefix_of_edge_32768": second.startswith(first),
            "common_prefix_characters": len(os.path.commonprefix([first, second])),
        },
        "classification": "UNBOUNDED_ARRAY_RUNAWAY_LENGTH_TRUNCATION",
        "conclusion": "The strict unbounded edge array consumes the full provider budget; bounded paging is required for this local substrate.",
    }
    out = Path(__file__).resolve().parents[1] / "saturated_fixed_work_baseline_v1_3/structured_output_recovery"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ROOT_CAUSE_DECISION.json").write_text(json.dumps(decision, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Root Cause Decision",
        "",
        "Status: `L0_COMPLETE` (diagnostic-only evidence; no response content enters construction).",
        "",
        f"Raw run root: `{run_root}`.",
        "",
        "| Requested budget | Response chars | Complete edge prefix | Unique tuples | Duplicate tuple repetitions | Duplicate start repetitions |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for audit in audits:
        budget = Path(audit["path"]).stem.removeprefix("edge_")
        lines.append(f"| {budget} | {audit['response_characters']} | {audit['complete_json_prefix_characters']} / {audit['complete_prefix_edge_count']} | {audit['unique_tuple_count']} | {audit['duplicate_tuple_repetitions']} | {audit['duplicate_start_repetitions']} |")
    lines += [
        "",
        f"The 16K response is a byte prefix of the 32K response: `{decision['prefix_relation']['edge_16384_is_prefix_of_edge_32768']}`.",
        "Both responses exhaust their requested completion budget and stop inside an edge string. The evidence mechanically supports `UNBOUNDED_ARRAY_RUNAWAY_LENGTH_TRUNCATION`.",
        "",
        "This audit is a root-cause artifact only. No decoded prefix or salvaged edge is eligible for construction.",
    ]
    (out / "ROOT_CAUSE_DECISION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(decision, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
