#!/usr/bin/env python3
"""Prepare or execute an explicitly authorized provider-independent V7 run.

The command accepts environment-variable names but never credential values.
Without ``--live`` it is provider-free and only seals the requested profile.
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
    DEFAULT_V7_PROVIDER_PROFILE,
    SILICONFLOW_CONSTRUCTION_MODEL,
    SILICONFLOW_EMBEDDING_MODEL,
    V7LiveConfig,
    V7ProviderLane,
    V7ProviderProfile,
    run_v7_live,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--method", default="OBSERVER_ONLY", choices=("OBSERVER_ONLY", "M0", "M1", "M2"))
    parser.add_argument("--gate", type=Path, dest="gate_path")
    parser.add_argument("--live", action="store_true", help="require method seal and call the injected live adapter")
    parser.add_argument("--provider-identity-kind", default=DEFAULT_V7_PROVIDER_PROFILE.identity_kind)
    parser.add_argument("--api-key-env", help="compatibility default for both provider lanes")
    parser.add_argument("--construction-api-key-env")
    parser.add_argument("--embedding-api-key-env")
    parser.add_argument("--construction-authority", default=DEFAULT_V7_PROVIDER_PROFILE.construction.authority)
    parser.add_argument("--embedding-authority", default=DEFAULT_V7_PROVIDER_PROFILE.embedding.authority)
    parser.add_argument("--construction-base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--embedding-base-url", default="https://api.siliconflow.cn/v1")
    parser.add_argument("--construction-model", default=SILICONFLOW_CONSTRUCTION_MODEL)
    parser.add_argument("--embedding-model", default=SILICONFLOW_EMBEDDING_MODEL)
    parser.add_argument("--embedding-dimension", type=int, default=1024)
    parser.add_argument("--source-count", type=int, default=2)
    parser.add_argument("--adapter", help="live adapter as module:function; required with --live")
    args = parser.parse_args()
    shared_key_env = args.api_key_env or "SILICONFLOW_API_KEY"
    profile = V7ProviderProfile(
        identity_kind=args.provider_identity_kind,
        construction=V7ProviderLane(
            authority=args.construction_authority,
            base_url=args.construction_base_url,
            model=args.construction_model,
            api_key_env=args.construction_api_key_env or shared_key_env,
        ),
        embedding=V7ProviderLane(
            authority=args.embedding_authority,
            base_url=args.embedding_base_url,
            model=args.embedding_model,
            api_key_env=args.embedding_api_key_env or shared_key_env,
            dimension=args.embedding_dimension,
        ),
    )
    config = V7LiveConfig(
        output_root=args.output_root,
        run_id=args.run_id,
        method=args.method,
        dry_run=not args.live,
        gate_path=args.gate_path,
        provider_profile=profile,
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
