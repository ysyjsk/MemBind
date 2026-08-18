#!/usr/bin/env python3
"""Persist a read-only MemBind v3.1 phase critical-path diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path

from paper_eval.artifacts import atomic_write_json
from paper_eval.membind_v31.phase_critical_path import (
    analyze_phase_critical_path,
    render_phase_critical_path_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", type=Path, help="immutable queue.jsonl")
    parser.add_argument("--events", type=Path, help="immutable events.jsonl")
    parser.add_argument("--llm", type=Path, help="immutable llm.jsonl")
    parser.add_argument("--capacity", type=int, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    result = analyze_phase_critical_path(
        args.queue,
        events_path=args.events,
        llm_path=args.llm,
        admission_capacity=args.capacity,
    )
    atomic_write_json(args.output_json, result)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(render_phase_critical_path_report(result), encoding="utf-8")
    print(args.output_json)
    print(args.output_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
