#!/usr/bin/env python3
"""Run the single authorized V4-MSEG-Q0 telemetry qualification measurement.

The command is measurement-only and non-mergeable. It uses the sealed v3.1
W=4 envelope and a fresh namespace, with no v4 scheduler or mechanism.
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

from paper_eval.artifacts import payload_sha256  # noqa: E402
from paper_eval.membind_v31.freezer import load_v31_state_cut_certification  # noqa: E402
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
    _default_episode_builder,
    _default_env_loader,
    load_development_episodes,
)
from paper_eval.membind_v31.live_block import production_v31_live_hooks  # noqa: E402
from paper_eval.membind_v4.mseg.observability import MSEGOperatorTraceObserver  # noqa: E402
from paper_eval.membind_v4.mseg.q0_runner import (  # noqa: E402
    Q0RunnerError,
    execute_q0_measurement,
    implementation_identity,
)
from paper_eval.membind_v4.mseg.qualification import build_q0_live_composition  # noqa: E402


HISTORY_ID = "07741c45"
RUN_ID_DEFAULT = "membind-v31-opt-w4-q0-20260820-001"
BASELINE_RUN_ID = "membind-v31-opt-w4-20260818-001"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=RUN_ID_DEFAULT)
    parser.add_argument("--attempt-id", default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--baseline-root", type=Path, default=None)
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
        raise Q0RunnerError("q0_source_prefix_unavailable")
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
        else paths.project_root
        / "artifacts/paper_eval/membind_v4/mseg/q0"
        / run_id
    )
    baseline_root = (
        Path(args.baseline_root).resolve()
        if args.baseline_root is not None
        else paths.control_root
        / "optimization/pilots"
        / BASELINE_RUN_ID
    )
    baseline_manifest_path = baseline_root / "manifest.json"
    baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))
    baseline_namespace = str(baseline_manifest["namespace"])
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
    observer = MSEGOperatorTraceObserver()
    composition = build_q0_live_composition(
        observer=observer,
        stream_id=HISTORY_ID,
        comparison_namespace=baseline_namespace,
        base_hooks=production_v31_live_hooks(),
    )
    print(
        json.dumps(
            {
                "status": "Q0_MEASUREMENT_AUTHORIZED",
                "run_id": run_id,
                "attempt_id": attempt_id,
                "output_root": str(output_root),
                "baseline_root": str(baseline_root),
                "baseline_namespace": baseline_namespace,
                "namespace": namespace,
                "history_id": HISTORY_ID,
                "source_sequences": list(range(12)),
                "compile_workers": COMPILE_WORKERS,
                "lookahead": LOOKAHEAD,
                "bind_workers": BIND_WORKERS,
                "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
                "execution_policy_changed": False,
                "contract_payload_sha256": contract["payload_sha256"],
                "new_mechanism_authorized": False,
                "new_scheduler_authorized": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "Q0_PREFLIGHT_PASS",
                "contract_payload_sha256": contract["payload_sha256"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    try:
        result = asyncio.run(
            execute_q0_measurement(
                contract=contract,
                verified_formal_plan=plan,
                episodes=episodes,
                env=env,
                output_root=output_root,
                state_cut_certification=certification,
                implementation_sha256=implementation_identity(paths.project_root),
                composition=composition,
                baseline_root=baseline_root,
            )
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "Q0_MEASUREMENT_FAILED",
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
                "result": str(output_root / "V4_MSEG_Q0_RESULT.json"),
                "reduced": str(output_root / "V4_MSEG_Q0_REDUCED.json"),
                "decision": str(output_root / "V4_MSEG_Q0_DECISION.md"),
                "post_q0_action": result["reduced"]["post_q0_action"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
