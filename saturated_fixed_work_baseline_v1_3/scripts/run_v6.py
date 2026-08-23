#!/usr/bin/env python3
"""Executable V6 Graphiti control/request-stability qualification arm."""

from __future__ import annotations

import asyncio
import json
import sys

from saturated_fixed_work_baseline_v1_3.membind_v6.runner import (
    V6RunnerError,
    build_v6_parser,
    config_from_args,
    run_v6_live_async,
    v6_live_authorization_checker,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_v6_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
    except V6RunnerError as exc:
        print(json.dumps({"status": "V6_INVALID_CONFIG", "error": str(exc)}, sort_keys=True))
        return 2
    if not args.execute_live:
        print(
            json.dumps(
                {
                    "status": "V6_READY_FOR_LIVE_GATE",
                    "policy": config.policy,
                    "history_id": config.history_id,
                    "full_history": config.full_history,
                    "source_limit": config.source_limit,
                    "endpoint": {"construction": config.construction_base_url, "embedding": config.embedding_base_url},
                    "command_validated": True,
                },
                sort_keys=True,
            )
        )
        return 0

    from live_outputs import export_canonical_graph
    from native_characterization_instrumentation import install_native_characterization_instrumentation
    from native_characterization_runtime import build_u0_graphiti_from_env
    from native_characterization_tracing import TraceRecorder
    from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs

    def runtime_builder():
        return build_u0_graphiti_from_env(
            authorization_checker=v6_live_authorization_checker,
        )

    try:
        result = asyncio.run(
            run_v6_live_async(
                config,
                runtime_builder=runtime_builder,
                episode_loader=load_episode_inputs,
                instrumentation_installer=install_native_characterization_instrumentation,
                recorder_factory=TraceRecorder,
                graph_exporter=export_canonical_graph,
                authorization_checker=v6_live_authorization_checker,
            )
        )
    except KeyboardInterrupt:
        print(json.dumps({"status": "V6_LIVE_INTERRUPTED", "output_root": str(config.output_root)}, sort_keys=True))
        return 130
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "V6_LIVE_FAILED",
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error": str(exc)[:240],
                    "output_root": str(config.output_root),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"status": result["seal"]["status"], "claim_status": result["seal"]["claim_status"], "output_root": result["root"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
