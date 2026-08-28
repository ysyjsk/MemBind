#!/usr/bin/env python3
"""Run isolated local-Qwen MAB construction blocks for V6.1 development."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
VALIDATION = ROOT / "membind-validation"
PAPER = ROOT / "paper-eval-v3"
MAB = ROOT / "mab_quality_v2_final_qa"
for source in (SFWB / "src", VALIDATION / "src", PAPER / "src", MAB / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab_main_dataset import build_authority, build_workload_manifest  # noqa: E402
from mab_quality_v2_final_qa.workload_contract import WorkloadManifest  # noqa: E402
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import run_mab_construction_async  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (  # noqa: E402
    LOCAL_PROFILE_ID,
    assert_namespace_identity,
    build_local_u0_runtime,
    close_local_u0_runtime,
    install_local_context_budget_adapter,
    local_frozen_config,
    public_runtime_environment,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (  # noqa: E402
    run_mab_v61_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.executor import (  # noqa: E402
    JIT_EXECUTION_STRATEGY,
    STAGED_EXECUTION_STRATEGY,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy  # noqa: E402


METHOD_MAP = {"B0": "B0", "B1": "B1", "V6_0": "V6", "V6_1": "V6_1"}


def _install_local_instrumentation(graphiti: Any, recorder: Any) -> Any:
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )

    instrumentation = install_native_characterization_instrumentation(graphiti, recorder)
    try:
        restore_budget = install_local_context_budget_adapter(graphiti.llm_client)
    except BaseException:
        instrumentation.restore()
        raise
    restored = False

    class CombinedInstrumentation:
        def restore(self) -> None:
            nonlocal restored
            if restored:
                return
            restored = True
            try:
                restore_budget()
            finally:
                instrumentation.restore()

    return CombinedInstrumentation()


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return {"head": head, "dirty_paths": status}


async def _namespace_counts(namespace: str) -> dict[str, int]:
    runtime = build_local_u0_runtime()
    try:
        result = await runtime.graphiti.driver.execute_query(
            """
            CALL { MATCH (n) WHERE n.group_id = $group_id RETURN count(n) AS node_count }
            CALL { MATCH ()-[r]->() WHERE r.group_id = $group_id RETURN count(r) AS relationship_count }
            RETURN node_count, relationship_count
            """,
            params={"group_id": namespace},
        )
        rows = getattr(result, "records", ())
        if not rows:
            raise RuntimeError("namespace count query returned no rows")
        return {
            "node_count": int(rows[0].get("node_count") or 0),
            "relationship_count": int(rows[0].get("relationship_count") or 0),
        }
    finally:
        await close_local_u0_runtime(runtime)


def _context_inputs(authority: Mapping[str, Any], context_index: int, session_limit: int | None):
    contexts = tuple(authority["contexts"])
    context = contexts[context_index]
    public_authority = {key: value for key, value in authority.items() if key != "contexts"}
    full = build_workload_manifest(context, public_authority, scope="FORMAL")
    if session_limit is None:
        manifest = full
    else:
        manifest = WorkloadManifest.from_episodes(
            context_id=context.context_id,
            episodes=full.episodes[:session_limit],
            dataset_revision=full.dataset_revision,
            dataset_file_sha256=full.dataset_file_sha256,
            scope="ENGINEERING_DIAGNOSTIC",
            expected_episode_count=None,
        )
    inputs = tuple(
        SimpleNamespace(**episode.to_dict(), session_id=session.session_id)
        for episode, session in zip(
            manifest.episodes, context.sessions[: len(manifest.episodes)], strict=True
        )
    )
    return context, manifest, inputs, public_authority


async def _main(args: argparse.Namespace) -> int:
    if os.environ.get("MEMBIND_PROFILE_ID") != LOCAL_PROFILE_ID:
        raise RuntimeError("source scripts/local_runtime/activate.sh before running the campaign")
    if not args.methods:
        raise ValueError("at least one method is required")
    unsupported = sorted(set(args.methods) - set(METHOD_MAP))
    if unsupported:
        raise ValueError(f"methods not implemented in this runner yet: {unsupported}")
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    ledger = output_root / "campaign_ledger.jsonl"
    environment = public_runtime_environment(repo_root=ROOT)
    campaign_manifest = {
        "schema_version": "membind.v6.1.local-campaign.v1",
        "profile_id": LOCAL_PROFILE_ID,
        "run_id": args.run_id,
        "contexts": list(args.contexts),
        "session_limit": args.session_limit,
        "methods": list(args.methods),
        "v6_1_policy": (
            V61Policy(
                lookahead=args.lookahead,
                future_cap=args.future_cap,
                native_future_quota=args.native_future_quota,
            ).to_dict()
            if "V6_1" in args.methods
            else None
        ),
        "v6_1_execution_strategy": (
            args.execution_strategy if "V6_1" in args.methods else None
        ),
        "runtime": environment,
        "git": _git_identity(),
        "dataset": str((MAB / "data" / "official_5_contexts.json").resolve()),
        "created_at_unix": time.time(),
    }
    manifest_path = output_root / f"campaign_manifest.{args.run_id}.json"
    _write_new(manifest_path, campaign_manifest)
    authority = build_authority(MAB / "data" / "official_5_contexts.json")
    frozen_config = local_frozen_config()
    policy = V61Policy(
        lookahead=args.lookahead,
        future_cap=args.future_cap,
        native_future_quota=args.native_future_quota,
    )

    from native_characterization_tracing import TraceRecorder
    from live_outputs import export_canonical_graph

    for context_index in args.contexts:
        context, workload, inputs, public_authority = _context_inputs(
            authority, context_index, args.session_limit
        )
        for method in args.methods:
            attempt_id = uuid.uuid4().hex[:12]
            namespace = (
                f"{LOCAL_PROFILE_ID}-v61mab-{args.run_id}-c{context_index}-"
                f"{method.casefold().replace('_', '-')}-{attempt_id}"
            )
            assert_namespace_identity(namespace)
            counts = await _namespace_counts(namespace)
            if counts != {"node_count": 0, "relationship_count": 0}:
                raise RuntimeError("fresh local namespace is not empty")
            attempt_root = output_root / f"context-{context_index}" / method / attempt_id
            block_root = attempt_root / "block"
            start = {
                "event": "ATTEMPT_START",
                "profile_id": LOCAL_PROFILE_ID,
                "run_id": args.run_id,
                "context_index": context_index,
                "context_id": context.context_id,
                "method": method,
                "legacy_method": METHOD_MAP[method],
                "namespace": namespace,
                "attempt_id": attempt_id,
                "episode_count": len(inputs),
                "policy": policy.to_dict() if method == "V6_1" else None,
                "execution_strategy": (
                    args.execution_strategy if method == "V6_1" else None
                ),
                "started_at_unix": time.time(),
                "started_at_ns": time.monotonic_ns(),
            }
            attempt_root.mkdir(parents=True, exist_ok=False)
            _write_new(attempt_root / "attempt.json", start)
            _append(ledger, start)
            try:
                common = {
                    "run_id": args.run_id,
                    "context_id": context.context_id,
                    "namespace": namespace,
                    "episodes": inputs,
                    "runtime_builder": build_local_u0_runtime,
                    "instrumentation_installer": _install_local_instrumentation,
                    "recorder_factory": TraceRecorder,
                    "graph_exporter": export_canonical_graph,
                    "output_root": block_root,
                    "authority": public_authority,
                    "workload_manifest": workload,
                    "frozen_config": frozen_config,
                    "environment": environment,
                    "preflight": {
                        "status": "PASS",
                        "profile_id": LOCAL_PROFILE_ID,
                        "namespace_initial_counts": counts,
                    },
                }
                if method == "V6_1":
                    result = await run_mab_v61_construction_async(
                        policy=policy,
                        execution_strategy=args.execution_strategy,
                        **common,
                    )
                else:
                    result = await run_mab_construction_async(
                        method=METHOD_MAP[method],
                        **common,
                    )
                verify_seal(block_root)
                complete = {
                    **start,
                    "event": "ATTEMPT_COMPLETE",
                    "status": "PASS",
                    "ended_at_unix": time.time(),
                    "ended_at_ns": time.monotonic_ns(),
                    "build_makespan_ns": result.get("t_build_ns"),
                    "construction_seal": str((block_root / "construction_seal.json").resolve()),
                    "evidence_limit": (
                        "V6_0_LEGACY_PROVIDER_PROOF_VACUOUS"
                        if method == "V6_0"
                        else None
                    ),
                }
                _write_new(attempt_root / "complete.json", complete)
                _append(ledger, complete)
            except BaseException as exc:
                failure = {
                    **start,
                    "event": "ATTEMPT_FAILURE",
                    "status": "FAILED",
                    "ended_at_unix": time.time(),
                    "ended_at_ns": time.monotonic_ns(),
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error": str(exc)[:1000],
                }
                _write_new(attempt_root / "failure.json", failure)
                _append(ledger, failure)
                if not args.continue_on_error:
                    raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("/data/predator/ly/Mem/experiments/local-qwen3-14b-awq-v1/v6_1_mab"),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--contexts", type=int, nargs="+", default=[0])
    parser.add_argument("--session-limit", type=int)
    parser.add_argument("--methods", nargs="+", choices=tuple(METHOD_MAP))
    parser.add_argument("--lookahead", type=int, default=2)
    parser.add_argument("--future-cap", type=int, default=1)
    parser.add_argument("--native-future-quota", type=int, default=0)
    parser.add_argument(
        "--execution-strategy",
        choices=(STAGED_EXECUTION_STRATEGY, JIT_EXECUTION_STRATEGY),
        default=STAGED_EXECUTION_STRATEGY,
    )
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()
    if any(index not in range(5) for index in args.contexts):
        parser.error("context indices must be in 0..4")
    if args.session_limit is not None and args.session_limit <= 0:
        parser.error("--session-limit must be positive")
    try:
        return asyncio.run(_main(args))
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error": str(exc)[:1000],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
