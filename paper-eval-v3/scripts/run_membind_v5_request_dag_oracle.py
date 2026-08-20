from __future__ import annotations

import argparse
from pathlib import Path

from paper_eval.membind_v5_oracle import (
    build_request_dag,
    load_trace_bundle,
    write_analysis_artifacts,
)


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = (
    PROJECT
    / "artifacts/paper_eval/membind_v4/mseg/q0/membind-v31-opt-w4-q0-20260820-001"
)
DEFAULT_OUTPUT = PROJECT / "artifacts/paper_eval/membind_v4/postmortem"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline v5 request DAG oracle")
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    trace_root = args.trace_root
    bundle = load_trace_bundle(
        llm_path=trace_root / "llm.jsonl",
        events_path=trace_root / "events.jsonl",
        manifest_path=trace_root / "manifest.json",
    )
    dag = build_request_dag(bundle)
    result = write_analysis_artifacts(bundle, dag, args.output_root)
    print(result["opportunity"]["decision"]["decision"])
    print(f"requests={len(bundle.requests)} nodes={len(dag.nodes)} edges={len(dag.edges)}")
    print(f"output_root={args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
