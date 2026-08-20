#!/usr/bin/env python3
"""Run the bounded, development-only SiliconFlow compatibility probe.

The credential must be supplied through ``SILICONFLOW_API_KEY`` in the
process environment.  This command never reads or mutates the frozen vLLM
``.env`` and never creates a Neo4j namespace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT / "src"))

from paper_eval.membind_v4.siliconflow_probe import (  # noqa: E402
    SiliconFlowProbeError,
    run_siliconflow_probe,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT / "artifacts/paper_eval/membind_v4/siliconflow_probe",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(argv)
    api_key = os.environ.get("SILICONFLOW_API_KEY", "")
    try:
        result = run_siliconflow_probe(
            api_key=api_key,
            output_root=args.output_root,
            timeout_seconds=args.timeout,
        )
    except SiliconFlowProbeError as error:
        print(f"SILICONFLOW_PROBE_FAILED:{error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "provider": result["provider"],
                "formal_main_table_eligible": result["formal_main_table_eligible"],
                "artifact": str((args.output_root / "SILICONFLOW_PROBE.json").resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
