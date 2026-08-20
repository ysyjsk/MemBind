#!/usr/bin/env python3
"""Run the single fixed 12-source MemBind-VDC capture-only measurement.

The run uses the frozen v3.1 envelope (W=4, K=2, unchanged arrivals) and
replaces only the adapter with a factorized NodeResolve capture/Probe overlay.
It does not execute the VDC candidate or produce main-table evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPOSITORY = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = REPOSITORY / "membind-validation"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
if str(LEGACY / "src") not in sys.path:
    sys.path.insert(1, str(LEGACY / "src"))

from paper_eval.membind_v31.freezer import load_v31_state_cut_certification  # noqa: E402
from paper_eval.membind_v31.live_block import production_v31_live_hooks  # noqa: E402
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan  # noqa: E402
from paper_eval.membind_v31.optimization_pilot import (  # noqa: E402
    BIND_WORKERS,
    COMPILE_WORKERS,
    GLOBAL_LLM_ADMISSION_K,
    LOOKAHEAD,
    build_w4_pilot_contract,
    derive_w4_pilot_cache_salt,
    derive_w4_pilot_namespace,
)
from paper_eval.membind_v31.production_executor import (  # noqa: E402
    ProductionExecutorPaths,
    _default_env_loader,
    _default_episode_builder,
    load_development_episodes,
)
from paper_eval.membind_v4.vdc.live_composition import (  # noqa: E402
    VDCObservationBundle,
    build_vdc_capture_composition,
)
from paper_eval.membind_v4.vdc.runner import (  # noqa: E402
    VDCRunnerError,
    execute_vdc_capture,
    implementation_identity,
)


HISTORY_ID = "07741c45"
RUN_ID_DEFAULT = "membind-v31-opt-w4-vdc-capture-20260820-001"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=RUN_ID_DEFAULT)
    parser.add_argument("--attempt-id", default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_inputs(paths: ProductionExecutorPaths, plan: dict[str, object]):
    builder = _default_episode_builder(paths.legacy_root)
    histories = load_development_episodes(
        development_input=paths.development_input,
        verified_plan=plan,
        episode_builder=builder,
    )
    episodes = tuple(histories[HISTORY_ID][:12])
    if len(episodes) != 12:
        raise VDCRunnerError("vdc_source_prefix_unavailable")
    return (
        episodes,
        _default_env_loader(paths.env_file),
        load_v31_state_cut_certification(paths.freeze_paths),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = Path(args.repository_root).resolve()
    paths = ProductionExecutorPaths.from_repository(repository)
    plan = verify_membind_v31_method_plan(
        json.loads((paths.control_root / "V31_METHOD_PLAN.json").read_text(encoding="utf-8"))
    )
    run_id = str(args.run_id)
    attempt_id = str(args.attempt_id or f"{run_id}-attempt-001")
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root is not None
        else paths.project_root / "artifacts/paper_eval/membind_v4/vdc_capture" / run_id
    )
    namespace = derive_w4_pilot_namespace(run_id)
    cache_salt = derive_w4_pilot_cache_salt(
        pilot_run_id=run_id,
        namespace=namespace,
        parent_formal_plan_payload_sha256=str(plan["payload_sha256"]),
    )
    episodes, env, certification = _load_inputs(paths, plan)
    contract = build_w4_pilot_contract(
        verified_formal_plan=plan,
        pilot_run_id=run_id,
        attempt_id=attempt_id,
        namespace=namespace,
        cache_salt_sha256=cache_salt,
        output_root=output_root,
        compile_workers=COMPILE_WORKERS,
        lookahead=LOOKAHEAD,
        bind_workers=BIND_WORKERS,
        global_llm_admission_k=GLOBAL_LLM_ADMISSION_K,
    )
    bundle = VDCObservationBundle()
    composition = build_vdc_capture_composition(
        bundle=bundle,
        base_hooks=production_v31_live_hooks(),
    )
    print(
        json.dumps(
            {
                "status": "VDC_CAPTURE_PREFLIGHT_PASS",
                "run_id": run_id,
                "attempt_id": attempt_id,
                "output_root": str(output_root),
                "namespace": namespace,
                "history_id": HISTORY_ID,
                "source_sequences": list(range(12)),
                "compile_workers": COMPILE_WORKERS,
                "lookahead": LOOKAHEAD,
                "bind_workers": BIND_WORKERS,
                "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
                "execution_policy_changed": False,
                "capture_only": True,
                "formal_main_table_eligible": False,
                "contract_payload_sha256": contract["payload_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        return 0
    try:
        result = asyncio.run(
            execute_vdc_capture(
                contract=contract,
                verified_formal_plan=plan,
                episodes=episodes,
                env=env,
                output_root=output_root,
                state_cut_certification=certification,
                implementation_sha256=implementation_identity(paths.project_root),
                composition=composition,
            )
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "VDC_CAPTURE_FAILED",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                    "error_code": str(error),
                    "output_root": str(output_root),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        return 1
    print(
        json.dumps(
            {
                "status": result["status"],
                "result": str(output_root / "VDC_CAPTURE_RESULT.json"),
                "bundle": str(output_root / "VDC_CAPTURE_BUNDLE.json"),
                "oracle": str(output_root / "VDC_CERTIFICATE_ORACLE.json"),
                "decision": str(output_root / "VDC_DECISION.md"),
                "oracle_status": result["oracle"]["decision"]["status"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
