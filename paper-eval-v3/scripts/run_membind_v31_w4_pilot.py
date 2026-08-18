#!/usr/bin/env python3
"""Run one fresh, bounded, diagnostic-only MemBind v3.1 W=4 pilot.

The script intentionally has no resume mode: a partial output root is
non-reusable.  Start a new run id after any failure so formal v3.1 artifacts
and the old failed attempt remain untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.membind_v31.optimization_live import execute_w4_pilot
from paper_eval.membind_v31.optimization_pilot import (
    BIND_WORKERS,
    COMPILE_WORKERS,
    GLOBAL_LLM_ADMISSION_K,
    LOOKAHEAD,
    PILOT_HISTORY,
    build_w4_pilot_contract,
    derive_w4_pilot_cache_salt,
    derive_w4_pilot_namespace,
)
from paper_eval.membind_v31.production_executor import (
    ProductionExecutorPaths,
    _default_episode_builder,
    _default_env_loader,
    load_development_episodes,
)
from paper_eval.membind_v31.freezer import load_v31_state_cut_certification
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan
from paper_eval.membind_v31.live_block import production_v31_live_hooks


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-id",
        default="membind-v31-opt-w4-20260818-001",
        help="fresh run id; use a new id after any failed attempt",
    )
    parser.add_argument(
        "--attempt-id",
        default=None,
        help="optional attempt id (defaults to <run-id>-attempt-001)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="fresh output root (defaults under artifacts/.../optimization/pilots)",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=None,
        help="repository root; useful when invoked outside the checkout",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate plan/source/certification/identity without opening a runtime",
    )
    return parser


def _implementation_identity(project_root: Path) -> str:
    files = {
        "optimization_pilot": project_root / "src/paper_eval/membind_v31/optimization_pilot.py",
        "optimization_live": project_root / "src/paper_eval/membind_v31/optimization_live.py",
        "coordinator": project_root / "src/paper_eval/membind_v31/coordinator.py",
        "request_runtime": project_root / "src/paper_eval/membind_v31/request_runtime.py",
        "live_runtime": project_root / "src/paper_eval/membind_v31/live_runtime.py",
    }
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"implementation files missing: {','.join(missing)}")
    return payload_sha256({name: sha256_file(path) for name, path in files.items()})


def _load_inputs(paths: ProductionExecutorPaths, plan: dict[str, object]):
    builder = _default_episode_builder(paths.legacy_root)
    histories = load_development_episodes(
        development_input=paths.development_input,
        verified_plan=plan,
        episode_builder=builder,
    )
    episodes = tuple(histories[PILOT_HISTORY][:12])
    if len(episodes) != 12:
        raise RuntimeError("pilot source prefix unavailable")
    env = _default_env_loader(paths.env_file)
    certification = load_v31_state_cut_certification(paths.freeze_paths)
    return episodes, env, certification


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository_root = (
        Path(args.repository_root).resolve()
        if args.repository_root is not None
        else _repository_root()
    )
    paths = ProductionExecutorPaths.from_repository(repository_root)
    plan_path = paths.control_root / "V31_METHOD_PLAN.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan = verify_membind_v31_method_plan(plan)
    run_id = str(args.run_id)
    attempt_id = str(args.attempt_id or f"{run_id}-attempt-001")
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root is not None
        else paths.control_root / "optimization" / "pilots" / f"{run_id}"
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
    implementation_sha256 = _implementation_identity(paths.project_root)
    print(
        json.dumps(
            {
                "status": "PILOT_AUTHORIZED",
                "run_id": run_id,
                "attempt_id": attempt_id,
                "output_root": str(output_root),
                "namespace": namespace,
                "source_sequences": list(range(12)),
                "compile_workers": COMPILE_WORKERS,
                "lookahead": LOOKAHEAD,
                "bind_workers": BIND_WORKERS,
                "global_llm_admission_k": GLOBAL_LLM_ADMISSION_K,
                "parent_plan_payload_sha256": plan["payload_sha256"],
                "implementation_sha256": implementation_sha256,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "PILOT_PREFLIGHT_PASS",
                    "output_root": str(output_root),
                    "contract_payload_sha256": contract["payload_sha256"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    try:
        result = asyncio.run(
            execute_w4_pilot(
                contract=contract,
                verified_formal_plan=plan,
                episodes=episodes,
                env=env,
                output_root=output_root,
                state_cut_certification=certification,
                implementation_sha256=implementation_sha256,
                hooks=production_v31_live_hooks(),
            )
        )
    except BaseException as error:
        print(
            json.dumps(
                {
                    "status": "PILOT_STOPPED",
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
                "status": "PILOT_PASS_DIAGNOSTIC_ONLY",
                "result": str(output_root / "result.json"),
                "queue_diagnostic": str(output_root / "QUEUE_DIAGNOSTIC.json"),
                "p95_freshness_ns": result["performance"].get("p95_freshness_ns"),
                "makespan_ns": result["performance"].get("makespan_ns"),
                "direct_violation_count": result["direct_violation_count"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
