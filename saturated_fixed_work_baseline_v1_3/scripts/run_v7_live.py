#!/usr/bin/env python3
"""Prepare or execute an explicitly authorized V7 live-runner invocation.

The command never accepts an API key argument. Set ``SILICONFLOW_API_KEY`` in
the process environment on the GPU host. Without ``--live`` this command is a
provider-free dry run and can be used to validate paths and manifests.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from saturated_fixed_work_baseline_v1_3.membind_v7.live_runner import (
    SILICONFLOW_CONSTRUCTION_MODEL,
    SILICONFLOW_EMBEDDING_MODEL,
    V7LiveConfig,
    run_v7_live,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--method", default="OBSERVER_ONLY", choices=("OBSERVER_ONLY", "M0", "M1", "M2"))
    parser.add_argument("--gate", type=Path, dest="gate_path")
    parser.add_argument("--live", action="store_true", help="require method seal and call the injected live adapter")
    parser.add_argument("--api-key-env", default="SILICONFLOW_API_KEY")
    parser.add_argument("--construction-base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--embedding-base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--construction-model", default=SILICONFLOW_CONSTRUCTION_MODEL)
    parser.add_argument("--embedding-model", default=SILICONFLOW_EMBEDDING_MODEL)
    parser.add_argument("--source-count", type=int, default=2)
    parser.add_argument("--adapter", help="live adapter as module:function; required with --live")
    args = parser.parse_args()
    config = V7LiveConfig(
        output_root=args.output_root,
        run_id=args.run_id,
        method=args.method,
        dry_run=not args.live,
        gate_path=args.gate_path,
        api_key_env=args.api_key_env,
        construction_base_url=args.construction_base_url,
        embedding_base_url=args.embedding_base_url,
        construction_model=args.construction_model,
        embedding_model=args.embedding_model,
        source_count=args.source_count,
    )
    provider_call = None
    if args.live:
        if not args.adapter or ":" not in args.adapter:
            parser.error("--adapter module:function is required with --live")
        module_name, function_name = args.adapter.split(":", 1)
        adapter = getattr(importlib.import_module(module_name), function_name, None)
        if not callable(adapter):
            parser.error("--adapter target is not callable")
        provider_call = lambda: adapter(config)
    result = run_v7_live(config, provider_call=provider_call)
    print(result["status"])
    print(result["provider_calls"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
