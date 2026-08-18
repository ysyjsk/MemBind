#!/usr/bin/env python3
"""Materialize, run, and reduce the bounded MemBind v3.1 probe.

The ``plan`` and ``analyze`` commands are offline.  ``run`` is the only
command that opens Graphiti/model/database services, and it writes a crash
decision immediately if the candidate fails before sealing its block.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
SOURCE = PROJECT / "src"
LEGACY = ROOT / "membind-validation"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
if str(LEGACY / "src") not in sys.path:
    sys.path.insert(0, str(LEGACY / "src"))

from paper_eval.artifacts import atomic_write_json, payload_sha256, sha256_file  # noqa: E402
from paper_eval.membind_v31.autoresearch import (  # noqa: E402
    MAX_CANDIDATES,
    PROBE_HISTORY,
    PROBE_SOURCE_COUNT,
    AutoresearchProbeError,
    append_results_tsv,
    assess_probe_candidate,
    build_autoresearch_probe_plan,
    derive_u0_prefix_reference,
    record_probe_crash,
)
from paper_eval.membind_v31.freezer import (  # noqa: E402
    V31FreezePaths,
    load_v31_state_cut_certification,
)
from paper_eval.membind_v31.live_block import execute_v31_live_block  # noqa: E402
from paper_eval.membind_v31.method_plan import verify_membind_v31_method_plan  # noqa: E402
from paper_eval.membind_v31.production_executor import (  # noqa: E402
    ProductionExecutorPaths,
    _default_env_loader,
    _default_episode_builder,
    load_development_episodes,
)


DEFAULT_CONTROL_ROOT = PROJECT / "artifacts/paper_eval/membind_v31"
DEFAULT_FORMAL_PLAN = DEFAULT_CONTROL_ROOT / "V31_METHOD_PLAN.json"
DEFAULT_BASELINE_RESULT = (
    PROJECT
    / "artifacts/paper_eval/apc_aligned_baseline/runs/"
    "apc-baseline-dev-20260817-001/blocks/block-00/APC_ALIGNED_BLOCK_RESULT.json"
)
DEFAULT_AUTORESEARCH_ROOT = DEFAULT_CONTROL_ROOT / "autoresearch"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AutoresearchProbeError(f"artifact unreadable: {Path(path).name}") from error
    if not isinstance(value, dict):
        raise AutoresearchProbeError("artifact invalid")
    return value


def _code_sha256() -> str:
    relative = (
        "src/paper_eval/membind_v31/admission.py",
        "src/paper_eval/membind_v31/coordinator.py",
        "src/paper_eval/membind_v31/graphiti_adapter.py",
        "src/paper_eval/membind_v31/live_block.py",
        "src/paper_eval/membind_v31/live_runtime.py",
        "src/paper_eval/membind_v31/request_runtime.py",
        "src/paper_eval/membind_v31/scheduler.py",
        "src/paper_eval/membind_v31/production_executor.py",
        "src/paper_eval/membind_v31/autoresearch.py",
    )
    return payload_sha256(
        {path: sha256_file(PROJECT / path) for path in relative}
    )


def _candidate_root(root: Path, candidate_id: str) -> Path:
    return Path(root) / "candidates" / candidate_id


def _last_parent_code(results_path: Path, current: str) -> str:
    if not results_path.is_file():
        return current
    lines = results_path.read_text(encoding="utf-8").splitlines()
    if len(lines) <= 1:
        return current
    fields = lines[-1].split("\t")
    header = lines[0].split("\t")
    try:
        return fields[header.index("code_sha256")]
    except (ValueError, IndexError):
        raise AutoresearchProbeError("results ledger invalid") from None


def _materialize(args: argparse.Namespace) -> dict[str, object]:
    formal = _read(args.formal_plan)
    try:
        verify_membind_v31_method_plan(formal)
    except ValueError as error:
        raise AutoresearchProbeError("formal plan invalid") from error
    probe, authorization = build_autoresearch_probe_plan(
        verified_formal_plan=formal,
        probe_run_id=args.probe_run_id,
        candidate_id=args.candidate_id,
    )
    reference = derive_u0_prefix_reference(_read(args.baseline_result))
    target = Path(args.probe_root)
    if target.exists() and any(target.iterdir()):
        raise AutoresearchProbeError("probe root already contains artifacts")
    target.mkdir(parents=True, exist_ok=True)
    (target / "candidates").mkdir(exist_ok=True)
    atomic_write_json(target / "PROBE_AUTHORIZATION.json", authorization)
    atomic_write_json(target / "PROBE_METHOD_PLAN.json", probe)
    atomic_write_json(target / "BASELINE_PREFIX_REFERENCE.json", reference)
    atomic_write_json(
        target / "PROGRAM.json",
        {
            "schema_version": "membind.paper-eval-v3.membind-v31-autoresearch-program.v1",
            "status": "FROZEN",
            "history_id": PROBE_HISTORY,
            "source_count": PROBE_SOURCE_COUNT,
            "max_candidates": MAX_CANDIDATES,
            "fixed_metric": ["p95_freshness_ns", "makespan_ns"],
            "fixed_knobs": {"compile_workers": 2, "lookahead": 2, "global_llm_admission_k": 2},
            "merge_authority": "NONE_NON_MERGEABLE_DEVELOPMENT_PROBE",
            "parent_formal_plan_payload_sha256": formal["payload_sha256"],
        },
    )
    return {
        "status": "PASS",
        "probe_root": str(target),
        "candidate_id": args.candidate_id,
        "probe_plan_payload_sha256": probe["payload_sha256"],
        "authorization_payload_sha256": authorization["payload_sha256"],
        "reference_payload_sha256": reference["payload_sha256"],
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.probe_root)
    candidate_root = _candidate_root(root, args.candidate_id)
    if candidate_root.exists():
        raise AutoresearchProbeError("candidate root already exists")
    probe = _read(root / "PROBE_METHOD_PLAN.json")
    authorization = _read(root / "PROBE_AUTHORIZATION.json")
    formal = _read(args.formal_plan)
    if authorization.get("candidate_id") != args.candidate_id:
        raise AutoresearchProbeError("candidate authorization mismatch")
    try:
        verify_membind_v31_method_plan(probe)
    except ValueError as error:
        raise AutoresearchProbeError("probe plan invalid") from error
    paths = ProductionExecutorPaths.from_repository(ROOT)
    formal_episodes = load_development_episodes(
        development_input=paths.development_input,
        verified_plan=formal,
        episode_builder=_default_episode_builder(paths.legacy_root),
    )
    episodes = formal_episodes[PROBE_HISTORY][:PROBE_SOURCE_COUNT]
    env = _default_env_loader(paths.env_file)
    certification = load_v31_state_cut_certification(paths.freeze_paths)
    candidate_root.mkdir(parents=True, exist_ok=False)
    block_root = candidate_root / "block"
    code = _code_sha256()
    try:
        import asyncio

        result = asyncio.run(
            execute_v31_live_block(
                verified_plan=probe,
                block_index=0,
                episodes=episodes,
                env=env,
                block_root=block_root,
                state_cut_certification=certification,
                compile_workers=2,
                lookahead=2,
            )
        )
    except Exception as error:
        decision = record_probe_crash(
            candidate_id=args.candidate_id,
            code_sha256=code,
            parent_code_sha256=_last_parent_code(root / "results.tsv", code),
            error_class=f"{type(error).__module__}.{type(error).__qualname__}",
            description=args.description,
        )
        atomic_write_json(candidate_root / "CANDIDATE_DECISION.json", decision)
        append_results_tsv(root / "results.tsv", decision)
        raise
    atomic_write_json(candidate_root / "CANDIDATE_RESULT.json", result)
    return {
        "status": "PASS",
        "candidate_id": args.candidate_id,
        "candidate_result_payload_sha256": result["payload_sha256"],
        "code_sha256": code,
        "candidate_root": str(candidate_root),
    }


def _analyze(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.probe_root)
    candidate_root = _candidate_root(root, args.candidate_id)
    result_path = candidate_root / "block/result.json"
    if not result_path.is_file():
        result_path = candidate_root / "CANDIDATE_RESULT.json"
    result = _read(result_path)
    comparator = _read(root / "BASELINE_PREFIX_REFERENCE.json")
    code = args.code_sha256 or _code_sha256()
    decision = assess_probe_candidate(
        candidate_id=args.candidate_id,
        candidate_result=result,
        comparator=comparator,
        code_sha256=code,
        parent_code_sha256=args.parent_code_sha256
        or _last_parent_code(root / "results.tsv", code),
        description=args.description,
    )
    atomic_write_json(candidate_root / "CANDIDATE_DECISION.json", decision)
    append_results_tsv(root / "results.tsv", decision)
    return {
        "status": "PASS",
        "candidate_id": args.candidate_id,
        "decision": decision["status"],
        "artifact_status": decision["artifact_status"],
        "semantic_status": decision["semantic_status"],
        "p95_ratio": decision["p95_ratio"],
        "makespan_ratio": decision["makespan_ratio"],
        "engineering_review_required": decision["engineering_review_required"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "analyze"))
    parser.add_argument("--formal-plan", type=Path, default=DEFAULT_FORMAL_PLAN)
    parser.add_argument("--baseline-result", type=Path, default=DEFAULT_BASELINE_RESULT)
    parser.add_argument("--probe-root", type=Path, default=DEFAULT_AUTORESEARCH_ROOT / "membind-v31-ar-20260818-001")
    parser.add_argument("--probe-run-id", default="membind-v31-ar-20260818-c00")
    parser.add_argument("--candidate-id", default="c00")
    parser.add_argument("--description", default="unchanged implementation reference")
    parser.add_argument("--code-sha256")
    parser.add_argument("--parent-code-sha256")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = _materialize(args)
        elif args.command == "run":
            result = _run(args)
        else:
            result = _analyze(args)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_class": f"{type(error).__module__}.{type(error).__qualname__}",
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
