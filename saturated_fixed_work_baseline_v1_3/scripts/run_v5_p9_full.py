#!/usr/bin/env python3
"""CLI for the real Graphiti 0.29.3 P9 V5 campaign."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from saturated_fixed_work_baseline_v1_3.membind_v5.p9_runner import (
    P9FullConfig,
    P9RunnerError,
    _verify_p8_seal,
    build_u0_runtime_with_endpoint_overrides,
    build_p9_parser,
    run_p9_full_live_async,
)


def _models(url: str, expected: str) -> dict[str, Any]:
    try:
        from saturated_fixed_work_baseline_v1_2.services import probe_model_catalog

        max_model_len = 65536 if expected == "qwen3-32b-fp8" else 32768
        return {
            "status": "PASS",
            **probe_model_catalog(
                url,
                expected_model=expected,
                expected_max_model_len=max_model_len,
            ),
        }
    except Exception as exc:
        return {"status": "FAIL", "url": url, "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}"}


def main(argv: list[str] | None = None) -> int:
    args = build_p9_parser().parse_args(argv)
    history_ids = (args.smoke_history,) if args.smoke else ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
    source_limit = args.smoke_sources if args.smoke else None
    try:
        config = P9FullConfig(
            repo_root=args.repo_root.resolve(),
            baseline_root=args.baseline_root.resolve(),
            state_path=args.state.resolve(),
            output_root=args.output_root.resolve(),
            run_id=args.run_id,
            p8_seal=args.p8_seal.resolve(),
            history_ids=history_ids,
            source_limit=source_limit,
            smoke=args.smoke,
            construction_base_url=args.construction_base_url,
            embedding_base_url=args.embedding_base_url,
        )
    except P9RunnerError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 2

    construction_base_url = config.construction_base_url or "http://10.87.5.247:8000/v1/"
    embedding_base_url = config.embedding_base_url or "http://10.87.5.247:8001/v1"
    construction = _models(construction_base_url.rstrip("/") + "/models", "qwen3-32b-fp8")
    embedding = _models(embedding_base_url.rstrip("/") + "/models", "qwen3-embedding-0.6b")
    if construction.get("status") != "PASS" or embedding.get("status") != "PASS":
        print(json.dumps({"status": "BLOCKED_HEALTH", "construction": construction, "embedding": embedding}, sort_keys=True))
        return 2

    from current_state_gate import LiveAction, require_live_action
    from saturated_fixed_work_baseline_v1_3.membind_v5.campaign import verify_baseline_reference

    try:
        require_live_action(LiveAction.MEMBIND_V5, state_path=config.state_path)
        baseline = verify_baseline_reference(config.baseline_root, allow_invalid_qa=True)
        p8 = _verify_p8_seal(config.p8_seal, baseline_reference=baseline)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_GATE", "error": f"{type(exc).__name__}:{str(exc)}"}, sort_keys=True))
        return 2

    if not args.execute_live:
        print(json.dumps({"status": "READY_FOR_P9", "mode": "smoke" if args.smoke else "full", "construction": construction, "embedding": embedding, "baseline_qa": baseline.get("qa_contract_status"), "command_validated": True}, sort_keys=True))
        return 0

    from live_outputs import export_canonical_graph
    from native_characterization_instrumentation import install_native_characterization_instrumentation
    from native_characterization_runtime import build_u0_graphiti_from_env
    from native_characterization_tracing import TraceRecorder
    from saturated_fixed_work_baseline_v1_2.dataset import load_episode_inputs

    def runtime_builder_factory(history_id: str, namespace: str):
        del history_id, namespace

        def build() -> Any:
            def check(action: Any, **kwargs: Any) -> Any:
                return require_live_action(action, state_path=config.state_path, **kwargs)

            def native_build() -> Any:
                return build_u0_graphiti_from_env(
                    authorization_checker=check,
                    live_action=LiveAction.MEMBIND_V5,
                )

            return build_u0_runtime_with_endpoint_overrides(
                native_build,
                construction_base_url=config.construction_base_url,
                embedding_base_url=config.embedding_base_url,
            )

        return build

    try:
        result = asyncio.run(
            run_p9_full_live_async(
                config,
                runtime_builder_factory=runtime_builder_factory,
                episode_loader=load_episode_inputs,
                instrumentation_installer=install_native_characterization_instrumentation,
                recorder_factory=TraceRecorder,
                graph_exporter=export_canonical_graph,
                authorization_checker=require_live_action,
            )
        )
    except KeyboardInterrupt:
        print(json.dumps({"status": "P9_LIVE_INTERRUPTED", "output_root": str(config.output_root)}, sort_keys=True))
        return 130
    except Exception as exc:
        print(json.dumps({"status": "P9_LIVE_FAILED", "error": f"{type(exc).__module__}.{type(exc).__qualname__}", "output_root": str(config.output_root)}, sort_keys=True))
        return 2
    print(json.dumps({"status": result["seal"]["status"], "output_root": result["root"], "history_count": len(result["histories"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
