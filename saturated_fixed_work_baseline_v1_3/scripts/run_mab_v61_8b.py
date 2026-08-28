#!/usr/bin/env python3
"""Run resource-matched 8B Native, strong-baseline, and V6.1 blocks."""

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
from saturated_fixed_work_baseline_v1_3.membind_v6_1.executor import (  # noqa: E402
    DUAL_STREAMING_EXECUTION_STRATEGY,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (  # noqa: E402
    implementation_bundle,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.mab import (  # noqa: E402
    run_mab_v61_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.policy import V61Policy  # noqa: E402
from saturated_fixed_work_baseline_v1_3.membind_v6_1.routing import (  # noqa: E402
    validate_route_evidence,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (  # noqa: E402
    install_local_context_budget_adapter,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (  # noqa: E402
    PROFILE_ID_8B,
    assert_8b_namespace_identity,
    build_8b_u0_runtime,
    close_8b_u0_runtime,
    frozen_8b_config,
    load_8b_platform_manifest,
    load_8b_routing_contract,
    public_8b_environment,
)


METHODS = {
    "NATIVE_SERIAL": {
        "legacy_method": "B0",
        "route_env": "MEMBIND_NATIVE_ROUTING_CONFIG",
        "contract_arm": "native-serial-dual",
    },
    "NATIVE_PARALLEL": {
        "legacy_method": "B1",
        "route_env": "MEMBIND_NATIVE_ROUTING_CONFIG",
        "contract_arm": "native-parallel-dual",
    },
    "STATIC_ROLE": {
        "legacy_method": "B0",
        "route_env": "MEMBIND_STATIC_ROLE_ROUTING_CONFIG",
        "contract_arm": "native-static-role-dual",
    },
    "V6_1": {
        "legacy_method": "V6_1",
        "route_env": "MEMBIND_V61_ROUTING_CONFIG",
        "contract_arm": "v61-dual",
    },
}


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        if isinstance(value, list):
            for row in value:
                handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        else:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return {"head": head, "dirty_paths": status}


def _namespace_counts(namespace: str) -> dict[str, int]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["MEMBIND_NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            row = session.run(
                """
                CALL () { MATCH (n) WHERE n.group_id = $namespace RETURN count(n) AS node_count }
                CALL () { MATCH ()-[r]->() WHERE r.group_id = $namespace RETURN count(r) AS relationship_count }
                RETURN node_count, relationship_count
                """,
                namespace=namespace,
            ).single(strict=True)
        return {
            "node_count": int(row["node_count"]),
            "relationship_count": int(row["relationship_count"]),
        }
    finally:
        driver.close()


def _context_inputs(
    authority: Mapping[str, Any], context_index: int, session_limit: int | None
) -> tuple[Any, WorkloadManifest, tuple[Any, ...], dict[str, Any]]:
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


def _install_instrumentation(graphiti: Any, recorder: Any) -> Any:
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


def _write_workload(path: Path, workload: WorkloadManifest) -> None:
    body = workload.jsonl()
    if not body.strip() or len(workload.manifest_sha256) != 64:
        raise RuntimeError("workload manifest identity is invalid")
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")


def _create_run_contract(
    *,
    method: str,
    run_id: str,
    namespace: str,
    platform_manifest: Path,
    workload_manifest: Path,
    output: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts/local_runtime_8b_dual/make_experiment_manifest.py"),
        "--arm",
        str(METHODS[method]["contract_arm"]),
        "--run-id",
        run_id,
        "--namespace",
        namespace,
        "--platform-manifest",
        str(platform_manifest),
        "--workload-manifest",
        str(workload_manifest),
        "--runner-implementation",
        str(Path(__file__).resolve()),
        "--output",
        str(output),
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    value = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("profile_id") != PROFILE_ID_8B:
        raise RuntimeError("8B run contract identity is invalid")
    return value


def _reusable_reference(
    output_root: Path,
    *,
    method: str,
    platform_payload_sha256: str,
    workload_file_sha256: str,
) -> dict[str, Any] | None:
    """Resolve one fully sealed non-V6.1 reference for an identical contract."""

    if method == "V6_1":
        return None
    runner_hash = _sha256(Path(__file__).resolve())
    implementation_hash = implementation_bundle(Path(__file__).resolve())["payload_sha256"]
    expected_arm = METHODS[method]["contract_arm"]
    for contract_path in sorted(output_root.glob(f"context-*/{method}/*/run_contract.json")):
        attempt_root = contract_path.parent
        complete_path = attempt_root / "complete.json"
        route_proof_path = attempt_root / "route_proof.json"
        route_seal_path = attempt_root / "route_seal.json"
        block_root = attempt_root / "block"
        if not complete_path.is_file() or not route_proof_path.is_file() or not route_seal_path.is_file():
            continue
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            complete = json.loads(complete_path.read_text(encoding="utf-8"))
            route_proof = json.loads(route_proof_path.read_text(encoding="utf-8"))
            route_seal = json.loads(route_seal_path.read_text(encoding="utf-8"))
            route_seal_hash = hashlib.sha256(
                json.dumps(
                    {key: value for key, value in route_seal.items() if key != "seal_sha256"},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            route_members_valid = route_seal.get("status") == "ROUTE_SEALED" and all(
                (attempt_root / name).is_file()
                and _sha256(attempt_root / name) == expected
                for name, expected in route_seal.get("members", {}).items()
            )
            matches = (
                contract.get("profile_id") == PROFILE_ID_8B
                and contract.get("arm") == expected_arm
                and contract.get("platform_manifest", {}).get("payload_sha256")
                == platform_payload_sha256
                and contract.get("workload_manifest", {}).get("file_sha256")
                == workload_file_sha256
                and contract.get("runner_implementation", {}).get("file_sha256")
                == runner_hash
                and contract.get("implementation_bundle", {}).get("payload_sha256")
                == implementation_hash
                and complete.get("status") == "PASS"
                and route_proof.get("status") == "PASS"
                and route_seal.get("seal_sha256") == route_seal_hash
                and route_members_valid
            )
            if not matches:
                continue
            verify_seal(block_root)
            return {
                "attempt_root": str(attempt_root.resolve()),
                "attempt_id": complete.get("attempt_id"),
                "namespace": complete.get("namespace"),
                "build_makespan_ns": complete.get("build_makespan_ns"),
                "construction_seal": complete.get("construction_seal"),
                "route_seal_sha256": complete.get("route_seal_sha256"),
                "run_contract_payload_sha256": contract.get("payload_sha256"),
            }
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
    return None


def _seal_route_evidence(
    attempt_root: Path,
    *,
    events: list[dict[str, Any]],
    route_runtime: Mapping[str, Any],
    block_root: Path,
    run_contract: Path,
    semantic_shortcuts: list[dict[str, Any]],
    extraction_diagnostics: list[dict[str, Any]] | None = None,
    node_resolution_evidence: list[dict[str, Any]] | None = None,
    attempt_preparation: Path | None = None,
) -> dict[str, Any]:
    transport_path = block_root / "transport_trace.jsonl"
    transport_count = sum(1 for line in transport_path.read_text(encoding="utf-8").splitlines() if line)
    endpoint_ids = [str(row["id"]) for row in route_runtime["endpoint_set"]]
    proof = validate_route_evidence(
        events,
        policy=str(route_runtime["policy"]),
        endpoint_ids=endpoint_ids,
        transport_attempt_count=transport_count,
        capacity_weights={
            str(row["id"]): float(row["capacity_weight"])
            for row in route_runtime["endpoint_set"]
        },
        logical_group_events=(
            route_runtime.get("edge_group_events")
            if route_runtime.get("policy") == "semantic_phase_edge_call_affinity"
            else route_runtime.get("logical_group_events")
        ),
    )
    if route_runtime.get("balanced") is not True:
        raise RuntimeError("route runtime has leaked endpoint counters")
    event_path = attempt_root / "route_events.jsonl"
    runtime_path = attempt_root / "route_runtime.json"
    proof_path = attempt_root / "route_proof.json"
    shortcuts_path = attempt_root / "semantic_shortcuts.jsonl"
    extraction_path = attempt_root / "extraction_diagnostics.jsonl"
    node_resolution_path = attempt_root / "node_resolution_evidence.jsonl"
    _write_new(event_path, events)
    _write_new(runtime_path, dict(route_runtime))
    _write_new(proof_path, proof)
    _write_new(shortcuts_path, semantic_shortcuts)
    _write_new(extraction_path, extraction_diagnostics or [])
    _write_new(node_resolution_path, node_resolution_evidence or [])
    members = {
        "run_contract.json": _sha256(run_contract),
        "route_events.jsonl": _sha256(event_path),
        "route_runtime.json": _sha256(runtime_path),
        "route_proof.json": _sha256(proof_path),
        "semantic_shortcuts.jsonl": _sha256(shortcuts_path),
        "extraction_diagnostics.jsonl": _sha256(extraction_path),
        "node_resolution_evidence.jsonl": _sha256(node_resolution_path),
        "block/construction_seal.json": _sha256(block_root / "construction_seal.json"),
    }
    if attempt_preparation is not None:
        members["attempt_preparation.json"] = _sha256(attempt_preparation)
    seal = {
        "schema_version": "membind.v6.1.route-seal.v1",
        "status": "ROUTE_SEALED",
        "members": members,
    }
    seal["seal_sha256"] = hashlib.sha256(
        json.dumps(seal, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _write_new(attempt_root / "route_seal.json", seal)
    return seal


async def _main(args: argparse.Namespace) -> int:
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID_8B:
        raise RuntimeError("source scripts/local_runtime_8b_dual/activate.sh before running")
    platform_path, platform = load_8b_platform_manifest()
    output_root = args.output_root.resolve()
    experiment_root = Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]).resolve()
    if experiment_root != output_root and experiment_root not in output_root.parents:
        raise RuntimeError("8B output root is outside the isolated experiment root")
    output_root.mkdir(parents=True, exist_ok=True)
    ledger = output_root / "campaign_ledger.jsonl"
    authority = build_authority(MAB / "data/official_5_contexts.json")
    policy = V61Policy(
        lookahead=args.lookahead,
        future_cap=args.future_cap,
        native_future_quota=args.native_future_quota,
    )
    routes = {
        method: load_8b_routing_contract(os.environ[str(METHODS[method]["route_env"])])
        for method in args.methods
    }
    campaign_manifest = {
        "schema_version": "membind.v6.1.8b-campaign.v1",
        "profile_id": PROFILE_ID_8B,
        "platform_manifest": {
            "path": str(platform_path),
            "payload_sha256": platform["payload_sha256"],
        },
        "run_id": args.run_id,
        "contexts": list(args.contexts),
        "session_limit": args.session_limit,
        "methods": list(args.methods),
        "method_contracts": {
            method: {
                **METHODS[method],
                "routing": routes[method],
                "execution_strategy": (
                    DUAL_STREAMING_EXECUTION_STRATEGY if method == "V6_1" else None
                ),
            }
            for method in args.methods
        },
        "v6_1_policy": policy.to_dict() if "V6_1" in args.methods else None,
        "git": _git_identity(),
        "dataset": str((MAB / "data/official_5_contexts.json").resolve()),
        "created_at_unix": time.time(),
    }
    _write_new(output_root / f"campaign_manifest.{args.run_id}.json", campaign_manifest)

    from live_outputs import export_canonical_graph
    from native_characterization_tracing import TraceRecorder

    for context_index in args.contexts:
        context, workload, inputs, public_authority = _context_inputs(
            authority, context_index, args.session_limit
        )
        workload_body = workload.jsonl()
        workload_bytes = (
            workload_body if workload_body.endswith("\n") else workload_body + "\n"
        ).encode("utf-8")
        workload_file_sha256 = hashlib.sha256(workload_bytes).hexdigest()
        for method in args.methods:
            reference = (
                None
                if args.force_reference_rerun
                else _reusable_reference(
                    output_root,
                    method=method,
                    platform_payload_sha256=str(platform["payload_sha256"]),
                    workload_file_sha256=workload_file_sha256,
                )
            )
            if reference is not None:
                reused = {
                    "event": "REFERENCE_REUSED",
                    "status": "PASS",
                    "profile_id": PROFILE_ID_8B,
                    "run_id": args.run_id,
                    "context_index": context_index,
                    "context_id": context.context_id,
                    "method": method,
                    "episode_count": len(inputs),
                    "platform_payload_sha256": platform["payload_sha256"],
                    "workload_file_sha256": workload_file_sha256,
                    "reused_at_unix": time.time(),
                    **reference,
                }
                _append(ledger, reused)
                print(json.dumps(reused, ensure_ascii=False, sort_keys=True), flush=True)
                continue
            attempt_id = uuid.uuid4().hex[:12]
            namespace = (
                f"{PROFILE_ID_8B}-v61mab-{args.run_id}-c{context_index}-"
                f"{method.casefold().replace('_', '-')}-{attempt_id}"
            )
            assert_8b_namespace_identity(namespace)
            counts = _namespace_counts(namespace)
            if counts != {"node_count": 0, "relationship_count": 0}:
                raise RuntimeError("fresh 8B namespace is not empty")
            attempt_root = output_root / f"context-{context_index}" / method / attempt_id
            block_root = attempt_root / "block"
            attempt_root.mkdir(parents=True, exist_ok=False)
            workload_path = attempt_root / "workload_manifest.jsonl"
            _write_workload(workload_path, workload)
            contract_path = attempt_root / "run_contract.json"
            contract = _create_run_contract(
                method=method,
                run_id=args.run_id,
                namespace=namespace,
                platform_manifest=platform_path,
                workload_manifest=workload_path,
                output=contract_path,
            )
            attempt_preparation_path = attempt_root / "attempt_preparation.json"
            start = {
                "event": "ATTEMPT_START",
                "profile_id": PROFILE_ID_8B,
                "run_id": args.run_id,
                "context_index": context_index,
                "context_id": context.context_id,
                "method": method,
                "legacy_method": METHODS[method]["legacy_method"],
                "namespace": namespace,
                "attempt_id": attempt_id,
                "episode_count": len(inputs),
                "routing_policy": routes[method]["router"]["policy"],
                "execution_strategy": (
                    DUAL_STREAMING_EXECUTION_STRATEGY if method == "V6_1" else None
                ),
                "attempt_phase": "ATTEMPT_PREPARATION",
                "run_contract_sha256": contract["payload_sha256"],
                "started_at_unix": time.time(),
                "started_at_ns": time.monotonic_ns(),
            }
            _write_new(attempt_root / "attempt.json", start)
            _append(ledger, start)
            route_events: list[dict[str, Any]] = []
            runtime_holder: dict[str, Any] = {}
            failure_phase = "ATTEMPT_PREPARATION"

            def runtime_builder() -> Any:
                if runtime_holder:
                    raise RuntimeError("attempt runtime builder was called more than once")
                runtime = build_8b_u0_runtime(
                    routing_contract=routes[method],
                    route_event_sink=route_events.append,
                    enable_grounded_summary_materialization=(method == "V6_1"),
                    enable_endpoint_schema_grounding=(method == "V6_1"),
                    enable_work_conserving_edge_admission=(method == "V6_1"),
                    # The adaptive controller was rejected at r66a. Keep it
                    # opt-in for an explicitly named ablation so the default
                    # V6.1 substrate remains the retained fixed admission path.
                    enable_adaptive_edge_admission=False,
                )
                runtime_holder["runtime"] = runtime
                return runtime

            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(
                            ROOT
                            / "scripts/local_runtime_8b_dual/prepare_measured_attempt.py"
                        ),
                        "--attempt-id",
                        attempt_id,
                        "--output",
                        str(attempt_preparation_path),
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                preparation = json.loads(
                    attempt_preparation_path.read_text(encoding="utf-8")
                )
                if (
                    preparation.get("status") != "PASS"
                    or preparation.get("attempt_id") != attempt_id
                    or preparation.get("cache_policy")
                    != "reset_then_identical_structured_warmup_v1"
                ):
                    raise RuntimeError("attempt preparation evidence is invalid")
                failure_phase = "MEASURED_CONSTRUCTION"
                environment = public_8b_environment(
                    routes[method],
                    repo_root=ROOT,
                    enable_grounded_summary_materialization=(method == "V6_1"),
                    enable_endpoint_schema_grounding=(method == "V6_1"),
                    enable_work_conserving_edge_admission=(method == "V6_1"),
                    enable_adaptive_edge_admission=False,
                )
                frozen_config = frozen_8b_config(
                    routes[method],
                    enable_grounded_summary_materialization=(method == "V6_1"),
                    enable_endpoint_schema_grounding=(method == "V6_1"),
                    enable_work_conserving_edge_admission=(method == "V6_1"),
                    enable_adaptive_edge_admission=False,
                )
                common = {
                    "run_id": args.run_id,
                    "context_id": context.context_id,
                    "namespace": namespace,
                    "episodes": inputs,
                    "runtime_builder": runtime_builder,
                    "instrumentation_installer": _install_instrumentation,
                    "recorder_factory": TraceRecorder,
                    "graph_exporter": export_canonical_graph,
                    "output_root": block_root,
                    "authority": public_authority,
                    "workload_manifest": workload,
                    "frozen_config": frozen_config,
                    "environment": environment,
                    "preflight": {
                        "status": "PASS",
                        "profile_id": PROFILE_ID_8B,
                        "platform_manifest_sha256": platform["payload_sha256"],
                        "run_contract_sha256": contract["payload_sha256"],
                        "namespace_initial_counts": counts,
                    },
                }
                if method == "V6_1":
                    result = await run_mab_v61_construction_async(
                        policy=policy,
                        execution_strategy=DUAL_STREAMING_EXECUTION_STRATEGY,
                        **common,
                    )
                else:
                    result = await run_mab_construction_async(
                        method=str(METHODS[method]["legacy_method"]), **common
                    )
                runtime = runtime_holder.get("runtime")
                if runtime is None:
                    raise RuntimeError("8B attempt did not construct its runtime")
                await close_8b_u0_runtime(runtime)
                verify_seal(block_root)
                route_runtime = runtime._membind_route_client.route_evidence()
                route_seal = _seal_route_evidence(
                    attempt_root,
                    events=route_events,
                    route_runtime=route_runtime,
                    block_root=block_root,
                    run_contract=contract_path,
                    semantic_shortcuts=list(
                        getattr(runtime.llm_client, "_membind_semantic_shortcuts", ()) or ()
                    ),
                    extraction_diagnostics=list(
                        getattr(runtime.llm_client, "_membind_extraction_diagnostics", ()) or ()
                    ),
                    node_resolution_evidence=list(
                        getattr(runtime, "_membind_candidate_provenance_evidence", ()) or ()
                    ),
                    attempt_preparation=attempt_preparation_path,
                )
                complete = {
                    **start,
                    "event": "ATTEMPT_COMPLETE",
                    "status": "PASS",
                    "ended_at_unix": time.time(),
                    "ended_at_ns": time.monotonic_ns(),
                    "build_makespan_ns": result.get("t_build_ns"),
                    "construction_seal": str((block_root / "construction_seal.json").resolve()),
                    "route_seal_sha256": route_seal["seal_sha256"],
                }
                _write_new(attempt_root / "complete.json", complete)
                _append(ledger, complete)
            except BaseException as exc:
                runtime = runtime_holder.get("runtime")
                if runtime is not None:
                    try:
                        await close_8b_u0_runtime(runtime)
                    except BaseException:
                        pass
                    route_runtime = runtime._membind_route_client.route_evidence()
                    diagnostics = {
                        "schema_version": "membind.v6.1.failed-attempt-diagnostics.v1",
                        "profile_id": PROFILE_ID_8B,
                        "method": method,
                        "attempt_id": attempt_id,
                        "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                        "route_runtime": route_runtime,
                        "llm_call_events": list(
                            getattr(runtime.llm_client, "call_events", ()) or ()
                        ),
                        "llm_failure_events": list(
                            getattr(runtime.llm_client, "failure_events", ()) or ()
                        ),
                        "semantic_shortcuts": list(
                            getattr(
                                runtime.llm_client,
                                "_membind_semantic_shortcuts",
                                (),
                            )
                            or ()
                        ),
                        "extraction_diagnostics": list(
                            getattr(
                                runtime.llm_client,
                                "_membind_extraction_diagnostics",
                                (),
                            )
                            or ()
                        ),
                        "node_resolution_evidence": list(
                            getattr(
                                runtime,
                                "_membind_candidate_provenance_evidence",
                                (),
                            )
                            or ()
                        ),
                        "secrets_and_content_omitted": True,
                    }
                    _write_new(attempt_root / "failed_route_events.jsonl", route_events)
                    _write_new(
                        attempt_root / "extraction_diagnostics.jsonl",
                        diagnostics["extraction_diagnostics"],
                    )
                    _write_new(attempt_root / "failure_diagnostics.json", diagnostics)
                failure = {
                    **start,
                    "event": "ATTEMPT_FAILURE",
                    "status": "FAILED",
                    "ended_at_unix": time.time(),
                    "ended_at_ns": time.monotonic_ns(),
                    "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                    "error": str(exc)[:1000],
                    "failure_phase": failure_phase,
                    "route_event_count": len(route_events),
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
        default=Path(
            "/data/predator/ly/Mem/experiments/"
            "local-qwen3-8b-awq-dualreplica-v1/v6_1_mab"
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--contexts", type=int, nargs="+", default=[0])
    parser.add_argument("--session-limit", type=int)
    parser.add_argument("--methods", nargs="+", required=True, choices=tuple(METHODS))
    parser.add_argument("--lookahead", type=int, default=2)
    parser.add_argument("--future-cap", type=int, default=1)
    parser.add_argument("--native-future-quota", type=int, default=0)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--force-reference-rerun", action="store_true")
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
