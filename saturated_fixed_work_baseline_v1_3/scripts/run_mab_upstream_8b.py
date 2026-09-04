#!/usr/bin/env python3
"""Run one fresh formal MAB8192 cell over upstream Graphiti 0.29.3."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
SFWB = ROOT / "saturated_fixed_work_baseline_v1_3"
VALIDATION = ROOT / "membind-validation"
MAB = ROOT / "mab_quality_v2_final_qa"
PAPER = ROOT / "paper-eval-v3"
for source in (SFWB / "src", MAB / "src", PAPER / "src", VALIDATION / "src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from mab_quality_v2_final_qa.mab8192_adapter import (  # noqa: E402
    MAB8192_ADAPTER_VERSION,
    MAB8192Manifest,
    adapter_identity,
)
from mab_quality_v2_final_qa.mab_main_dataset import build_authority  # noqa: E402
from saturated_fixed_work_baseline_v1_3.artifact_seals import verify_seal  # noqa: E402
from saturated_fixed_work_baseline_v1_3.mab_live_runner import (  # noqa: E402
    run_mab_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (  # noqa: E402
    require_source_epoch,
    implementation_bundle,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.resource_credit import (  # noqa: E402
    ResourceCreditPolicy,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.routing import (  # noqa: E402
    validate_route_evidence,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_campaign import (  # noqa: E402
    run_upstream_membind_construction_async,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.upstream_runtime import (  # noqa: E402
    FORMAL_ARM_A,
    FORMAL_ARM_B,
    FORMAL_ARM_C,
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_formal_upstream_runtime,
    close_formal_upstream_runtime,
    resolve_deployment_policy,
)


DEPLOYMENT_POLICY = resolve_deployment_policy()
PROFILE_ID = DEPLOYMENT_POLICY.profile_id
MODEL = DEPLOYMENT_POLICY.served_model
SAMPLING = dict(DEPLOYMENT_POLICY.sampling)
METHODS = {
    FORMAL_ARM_A: {"route_env": "MEMBIND_NATIVE_ROUTING_CONFIG", "order": 0},
    FORMAL_ARM_C: {"route_env": "MEMBIND_V61_ROUTING_CONFIG", "order": 1},
    FORMAL_ARM_B: {"route_env": "MEMBIND_NATIVE_ROUTING_CONFIG", "order": 2},
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        if isinstance(value, list):
            for row in value:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        else:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(dict(value), stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _capture_resource_evidence(
    endpoint_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture one read-only, label-aware endpoint/GPU resource snapshot."""
    sample = re.compile(
        r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^\n]*\})?\s+"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NaN|Inf|-Inf)"
    )
    aliases = {
        "running": ("vllm:num_requests_running", "num_requests_running"),
        "waiting": ("vllm:num_requests_waiting", "num_requests_waiting"),
        "kv_cache_usage_perc": ("vllm:gpu_cache_usage_perc", "gpu_cache_usage_perc"),
        "generation_tokens": ("vllm:generation_tokens_total", "generation_tokens_total"),
    }
    endpoints: dict[str, Any] = {}
    for endpoint_id, port in (("native-replica", 18200), ("prepare-replica", 18201)):
        values: dict[str, float] = {}
        try:
            with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
                for line in response.read().decode("utf-8", "replace").splitlines():
                    match = sample.match(line.strip())
                    if match:
                        try:
                            metric_name = match.group(1) + (match.group(2) or "")
                            values.setdefault(metric_name, float(match.group(3)))
                        except ValueError:
                            pass
            def resolve(names: tuple[str, ...]) -> float | None:
                for name in names:
                    if name in values:
                        return values[name]
                    for key, value in values.items():
                        if key.startswith(name + "{"):
                            return value
                return None

            endpoint = dict((endpoint_identity or {}).get(endpoint_id, {}))
            endpoints[endpoint_id] = {
                "endpoint_id": endpoint_id,
                **endpoint,
                "port": port,
                "status": "PASS",
                "metrics": values,
                **{key: resolve(names) for key, names in aliases.items()},
            }
        except Exception as exc:
            endpoint = dict((endpoint_identity or {}).get(endpoint_id, {}))
            endpoints[endpoint_id] = {
                "endpoint_id": endpoint_id,
                **endpoint,
                "port": port,
                "status": "UNAVAILABLE",
                "metrics": {},
                **{key: None for key in aliases},
                "error": str(exc)[:300],
            }
    gpu: list[dict[str, Any]] = []
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,uuid,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True,
        )
        for line in completed.stdout.splitlines():
            index, uuid_value, used, total, utilization = [part.strip() for part in line.split(",", 4)]
            gpu.append({"index": int(index), "uuid": uuid_value, "memory_used_mib": int(used), "memory_total_mib": int(total), "utilization_gpu_pct": float(utilization)})
    except Exception as exc:
        gpu = [{"status": "UNAVAILABLE", "error": str(exc)[:300]}]
    return {
        "schema_version": "membind.resource-snapshot.v2",
        "captured_unix": time.time(),
        "endpoints": endpoints,
        "gpu": gpu,
    }


def _build_resource_evidence(
    *,
    cell_id: str,
    attempt_id: str,
    namespace: str,
    endpoint_identity: Mapping[str, Any],
    construction_start: Mapping[str, Any],
    periodic_samples: list[Mapping[str, Any]],
    construction_end: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal a common start/periodic/end resource trace for every measured cell."""

    snapshots = [dict(construction_start), *(dict(row) for row in periodic_samples), dict(construction_end)]
    endpoint_ids = set(endpoint_identity)
    for snapshot in snapshots:
        endpoint_ids.update(snapshot.get("endpoints", {}))
    metric_names = ("running", "waiting", "kv_cache_usage_perc", "generation_tokens")
    statistics: dict[str, Any] = {}
    counter_delta: dict[str, Any] = {}
    missing = 0
    total = 0
    for endpoint_id in sorted(endpoint_ids):
        endpoint_stats: dict[str, Any] = {}
        endpoint_values: dict[str, list[float]] = {name: [] for name in metric_names}
        for snapshot in snapshots:
            endpoint = snapshot.get("endpoints", {}).get(endpoint_id, {})
            for name in metric_names:
                total += 1
                value = endpoint.get(name)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    endpoint_values[name].append(float(value))
                else:
                    missing += 1
        for name, values in endpoint_values.items():
            endpoint_stats[name] = {
                "count": len(values),
                "peak": max(values) if values else None,
                "mean": sum(values) / len(values) if values else None,
            }
        statistics[endpoint_id] = endpoint_stats
        first = snapshots[0].get("endpoints", {}).get(endpoint_id, {}).get("generation_tokens")
        last = snapshots[-1].get("endpoints", {}).get(endpoint_id, {}).get("generation_tokens")
        counter_delta[endpoint_id] = {
            "generation_tokens": (
                float(last) - float(first)
                if isinstance(first, (int, float)) and isinstance(last, (int, float))
                else None
            ),
        }
    return {
        "schema_version": "membind.resource-evidence.v2",
        "status": "PASS",
        "cell_id": cell_id,
        "attempt_id": attempt_id,
        "namespace": namespace,
        "endpoint_identity": {str(key): dict(value) for key, value in endpoint_identity.items()},
        "construction_start": dict(construction_start),
        "samples": snapshots,
        "construction_end": dict(construction_end),
        "counter_delta": counter_delta,
        "statistics": statistics,
        "sampling_missingness_rate": missing / total if total else 1.0,
        "sample_count": len(snapshots),
        "created_unix": time.time(),
    }


async def _resource_sampler(
    stop: asyncio.Event,
    samples: list[dict[str, Any]],
    endpoint_identity: Mapping[str, Any],
) -> None:
    while not stop.is_set():
        samples.append(await asyncio.to_thread(_capture_resource_evidence, endpoint_identity))
        try:
            await asyncio.wait_for(stop.wait(), timeout=30.0)
        except TimeoutError:
            pass


def _persist_failure_transport_evidence(
    attempt_root: Path, runtime: Any
) -> dict[str, Any]:
    """Persist response diagnostics without changing upstream failure semantics."""

    raw_rows = getattr(runtime, "_membind_transport_telemetry", ()) or ()
    rows = [dict(row) for row in raw_rows if isinstance(row, Mapping)]
    if not rows:
        return {"row_count": 0, "last_transport_response": None}
    path = attempt_root / "failure_transport_telemetry.jsonl"
    _write_new(path, rows)
    return {
        "path": str(path),
        "row_count": len(rows),
        "last_transport_response": rows[-1],
    }


def _git_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return {"head": head, "dirty_paths": dirty}


def _context_inputs(
    authority: Mapping[str, Any], context_index: int
) -> tuple[Any, MAB8192Manifest, tuple[Any, ...], dict[str, Any]]:
    context = tuple(authority["contexts"])[context_index]
    public = {key: value for key, value in authority.items() if key != "contexts"}
    manifest = MAB8192Manifest.from_context(
        context, dataset_revision=str(public["revision"])
    )
    episodes = tuple(
        SimpleNamespace(
            context_id=chunk.context_id,
            source_sequence=chunk.global_sequence,
            original_source_sequence=chunk.source_sequence,
            episode_id=chunk.chunk_id,
            session_id=chunk.session_id,
            reference_time=chunk.reference_time,
            body=chunk.body,
            dataset_revision=chunk.dataset_revision,
            chunk_ordinal=chunk.chunk_ordinal,
            chunk_count=chunk.chunk_count,
            chunk_id=chunk.chunk_id,
            previous_chunk_id=chunk.previous_chunk_id,
            adapter_version=MAB8192_ADAPTER_VERSION,
        )
        for chunk in manifest.chunks
    )
    return context, manifest, episodes, public


def _namespace_counts(namespace: str) -> dict[str, int]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            row = session.run(
                """
                CALL () { MATCH (n) WHERE n.group_id = $namespace RETURN count(n) AS nodes }
                CALL () { MATCH ()-[r]->() WHERE r.group_id = $namespace RETURN count(r) AS edges }
                RETURN nodes, edges
                """,
                namespace=namespace,
            ).single(strict=True)
        return {"node_count": int(row["nodes"]), "relationship_count": int(row["edges"])}
    finally:
        driver.close()


def _install_instrumentation(graphiti: Any, recorder: Any) -> Any:
    from native_characterization_instrumentation import (
        install_native_characterization_instrumentation,
    )

    return install_native_characterization_instrumentation(graphiti, recorder)


def _run_contract(
    *,
    args: argparse.Namespace,
    context: Any,
    manifest: MAB8192Manifest,
    public_authority: Mapping[str, Any],
    platform_path: Path,
    platform: Mapping[str, Any],
    route_path: Path,
    route: Mapping[str, Any],
    namespace_counts: Mapping[str, int],
    source_epoch: Mapping[str, Any],
) -> dict[str, Any]:
    runner = Path(__file__).resolve()
    adapter_source = Path(inspect_path(MAB8192Manifest)).resolve()
    payload = {
        "schema_version": "membind.upstream-mab8192-run-contract.v1",
        "formal_eligible": True,
        "profile_id": PROFILE_ID,
        "run_id": args.run_id,
        "attempt_id": args.attempt_id,
        "namespace": args.namespace,
        "history_index": args.context_index,
        "history_id": context.context_id,
        "replicate_id": args.replicate_id,
        "arm": args.method,
        "arm_order": "A_THEN_C_THEN_B",
        "graphiti": {"version": GRAPHITI_VERSION, "commit": GRAPHITI_COMMIT},
        "deployment_policy_id": DEPLOYMENT_POLICY.policy_id,
        "model": MODEL,
        "sampling": dict(SAMPLING),
        "sdk_retries": 0,
        "adapter": adapter_identity(),
        "adapter_source_sha256": _file_sha256(adapter_source),
        "chunk_manifest_sha256": manifest.manifest_sha256,
        "dataset_authority_sha256": public_authority["authority_sha256"],
        "dataset_revision": public_authority["revision"],
        "platform": {
            "path": str(platform_path),
            "file_sha256": _file_sha256(platform_path),
            "payload_sha256": platform["payload_sha256"],
        },
        "routing": {
            "path": str(route_path),
            "file_sha256": _file_sha256(route_path),
            "contract": dict(route),
        },
        "implementation": implementation_bundle(runner),
        "source_epoch": dict(source_epoch),
        "runner_sha256": _file_sha256(runner),
        "git": _git_identity(),
        "freshness": {"namespace_initial_counts": dict(namespace_counts)},
        "failure_policy": "NO_RESUME_FORMAL_ATTEMPT",
        "created_unix": time.time(),
    }
    payload["payload_sha256"] = _canonical_sha256(payload)
    return payload


def inspect_path(value: Any) -> str:
    import inspect

    path = inspect.getsourcefile(value)
    if path is None:
        raise RuntimeError(f"source path unavailable: {value!r}")
    return path


async def _heartbeat(path: Path, identity: Mapping[str, Any], stop: asyncio.Event) -> None:
    while not stop.is_set():
        _write_atomic(
            path,
            {
                **dict(identity),
                "status": "RUNNING",
                "pid": os.getpid(),
                "updated_unix": time.time(),
                "updated_monotonic_ns": time.monotonic_ns(),
            },
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass


async def _prepare_attempt(attempt_id: str, output: Path) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "scripts/local_runtime_8b_dual/prepare_measured_attempt.py"),
        "--attempt-id",
        attempt_id,
        "--output",
        str(output),
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            "attempt preparation failed: "
            + stderr.decode("utf-8", errors="replace")[-1000:]
        )
    value = _read_json(output)
    if value.get("status") != "PASS" or value.get("attempt_id") != attempt_id:
        raise RuntimeError("attempt preparation evidence is invalid")
    value["stdout_tail"] = stdout.decode("utf-8", errors="replace")[-500:]
    return value


def _failure_class(exc: BaseException) -> str:
    name = f"{type(exc).__module__}.{type(exc).__qualname__}"
    text = f"{name}:{exc}".casefold()
    if any(value in text for value in ("binding", "identity", "manifest", "adapter")):
        return "IDENTITY"
    if any(value in text for value in ("neo4j", "serviceunavailable", "connection")):
        return "TRANSPORT_INFRASTRUCTURE"
    if "route" in text or "provider" in text:
        return "TRANSPORT_INFRASTRUCTURE"
    if "publication" in text or "frontier" in text:
        return "PUBLICATION"
    return "UPSTREAM_GRAPHITI"


async def _main(args: argparse.Namespace) -> int:
    if os.environ.get("MEMBIND_PROFILE_ID") != PROFILE_ID:
        raise RuntimeError("activate the selected deployment profile first")
    platform_path = args.platform_manifest.resolve()
    platform = _read_json(platform_path)
    if (
        platform.get("profile_id") != PROFILE_ID
        or platform.get("deployment_policy_id") != DEPLOYMENT_POLICY.policy_id
        or platform.get("llm_model", {}).get("served_model") != MODEL
        or platform.get("llm_model", {}).get("revision") != DEPLOYMENT_POLICY.revision
        or platform.get("platform_formal_eligible") is not True
        or platform.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
    ):
        raise RuntimeError("platform manifest is not formal eligible")
    expected_head = os.environ.get("MEMBIND_EXPECTED_GIT_HEAD")
    expected_bundle = os.environ.get("MEMBIND_EXPECTED_SOURCE_BUNDLE")
    if not expected_head or not expected_bundle:
        raise RuntimeError("measured source epoch binding is missing")
    source_epoch = require_source_epoch(
        Path(__file__).resolve(),
        expected_head=expected_head,
        expected_source_bundle_sha256=expected_bundle,
        root=ROOT,
    )
    output_root = args.output_root.resolve()
    experiment_root = Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]).resolve()
    if experiment_root != output_root and experiment_root not in output_root.parents:
        raise RuntimeError("output root is outside the isolated experiment root")
    if not args.namespace.startswith(f"{PROFILE_ID}-"):
        raise RuntimeError("namespace is outside the isolated profile")
    authority = build_authority(MAB / "data/official_5_contexts.json")
    context, manifest, episodes, public_authority = _context_inputs(
        authority, args.context_index
    )
    route_path = Path(os.environ[str(METHODS[args.method]["route_env"])]).resolve()
    route = _read_json(route_path)
    platform_endpoints = {
        (row["id"], row["base_url"].rstrip("/"), row["served_model"], row["physical_gpu"])
        for row in platform["llm_endpoints"]
    }
    route_endpoints = {
        (row["id"], row["base_url"].rstrip("/"), row["served_model"], row["physical_gpu"])
        for row in route["endpoint_set"]
    }
    if platform_endpoints != route_endpoints:
        raise RuntimeError("routing endpoint set differs from platform")
    endpoint_identity = {
        str(row["id"]): {
            key: row.get(key)
            for key in ("id", "base_url", "served_model", "physical_gpu", "role")
            if key in row
        }
        for row in platform["llm_endpoints"]
    }
    counts = _namespace_counts(args.namespace)
    if counts != {"node_count": 0, "relationship_count": 0}:
        raise RuntimeError(f"namespace is not fresh: {counts}")

    attempt_root = (
        output_root
        / f"history-{args.context_index}"
        / f"replicate-{args.replicate_id}"
        / args.method
        / args.attempt_id
    )
    attempt_root.mkdir(parents=True, exist_ok=False)
    block_root = attempt_root / "block"
    ledger = output_root / "campaign_ledger.jsonl"
    heartbeat_path = attempt_root / "heartbeat.json"
    identity = {
        "run_id": args.run_id,
        "attempt_id": args.attempt_id,
        "namespace": args.namespace,
        "history_index": args.context_index,
        "history_id": context.context_id,
        "replicate_id": args.replicate_id,
        "method": args.method,
    }
    cell_id = os.environ.get(
        "MEMBIND_CELL_ID",
        f"h{args.context_index}-r{args.replicate_id}-{args.method}",
    )
    start = {
        **identity,
        "event": "ATTEMPT_START",
        "status": "RUNNING",
        "pid": os.getpid(),
        "argv": sys.argv,
        "started_unix": time.time(),
    }
    _write_new(attempt_root / "attempt.json", start)
    _append(ledger, start)
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(heartbeat_path, identity, stop))
    runtime_holder: dict[str, Any] = {}
    resource_start: dict[str, Any] | None = None
    resource_samples: list[dict[str, Any]] = []
    resource_stop = asyncio.Event()
    resource_task: asyncio.Task[None] | None = None
    resource_path = block_root / "resource_evidence.json"
    try:
        preparation_path = attempt_root / "attempt_preparation.json"
        preparation = await _prepare_attempt(args.attempt_id, preparation_path)
        contract = _run_contract(
            args=args,
            context=context,
            manifest=manifest,
            public_authority=public_authority,
            platform_path=platform_path,
            platform=platform,
            route_path=route_path,
            route=route,
            namespace_counts=counts,
            source_epoch=source_epoch,
        )
        contract_path = attempt_root / "run_contract.json"
        _write_new(contract_path, contract)
        route_events: list[dict[str, Any]] = []

        def runtime_builder() -> Any:
            if runtime_holder:
                raise RuntimeError("runtime builder called more than once")
            runtime = build_formal_upstream_runtime(
                routing_contract=route,
                route_event_sink=route_events.append,
                arm=args.method,
            )
            runtime_holder["runtime"] = runtime
            return runtime

        from live_outputs import export_canonical_graph
        from native_characterization_tracing import TraceRecorder

        common = {
            "run_id": args.run_id,
            "context_id": context.context_id,
            "namespace": args.namespace,
            "episodes": episodes,
            "runtime_builder": runtime_builder,
            "instrumentation_installer": _install_instrumentation,
            "recorder_factory": TraceRecorder,
            "graph_exporter": export_canonical_graph,
            "output_root": block_root,
            "authority": public_authority,
            "workload_manifest": manifest,
            "frozen_config": {
                "run_contract_sha256": contract["payload_sha256"],
                "method": args.method,
                "adapter": adapter_identity(),
                "deployment_policy_id": DEPLOYMENT_POLICY.policy_id,
                "model": MODEL,
                "sampling": dict(SAMPLING),
                "resource_credit": (
                    ResourceCreditPolicy().to_dict() if args.method == FORMAL_ARM_C else None
                ),
            },
            "environment": {
                "profile_id": PROFILE_ID,
                "platform_payload_sha256": platform["payload_sha256"],
                "routing_policy": route["router"]["policy"],
                "endpoint_set": route["endpoint_set"],
            },
            "preflight": {
                "status": "PASS",
                "platform_payload_sha256": platform["payload_sha256"],
                "run_contract_sha256": contract["payload_sha256"],
                "namespace_initial_counts": counts,
                "attempt_preparation_status": preparation["status"],
            },
        }
        # Resource collection is observational and identical across arms.  It
        # starts immediately before construction, samples while requests run,
        # and stops only after construction returns.
        resource_start = await asyncio.to_thread(
            _capture_resource_evidence, endpoint_identity
        )
        resource_task = asyncio.create_task(
            _resource_sampler(resource_stop, resource_samples, endpoint_identity)
        )
        try:
            if args.method == FORMAL_ARM_C:
                result = await run_upstream_membind_construction_async(
                    policy=ResourceCreditPolicy(), **common
                )
            else:
                result = await run_mab_construction_async(method=args.method, **common)
        finally:
            resource_stop.set()
            if resource_task is not None:
                await asyncio.gather(resource_task, return_exceptions=True)
        resource_end = await asyncio.to_thread(
            _capture_resource_evidence, endpoint_identity
        )
        runtime = runtime_holder.get("runtime")
        if runtime is None:
            raise RuntimeError("measured attempt did not construct a runtime")
        verify_seal(block_root)
        _write_new(
            resource_path,
            _build_resource_evidence(
                cell_id=cell_id,
                attempt_id=args.attempt_id,
                namespace=args.namespace,
                endpoint_identity=endpoint_identity,
                construction_start=resource_start,
                periodic_samples=resource_samples,
                construction_end=resource_end,
            ),
        )
        route_runtime = runtime._membind_route_client.route_evidence()
        transport_count = len(runtime._membind_transport_telemetry)
        if transport_count != len(route_events):
            raise RuntimeError("route/transport attempt inventory mismatch")
        route_proof = validate_route_evidence(
            route_events,
            policy=str(route_runtime["policy"]),
            endpoint_ids=[str(row["id"]) for row in route_runtime["endpoint_set"]],
            transport_attempt_count=transport_count,
            capacity_weights={
                str(row["id"]): float(row.get("capacity_weight", 1.0))
                for row in route_runtime["endpoint_set"]
            },
            logical_group_events=route_runtime.get("logical_group_events"),
        )
        route_event_path = attempt_root / "route_events.jsonl"
        route_runtime_path = attempt_root / "route_runtime.json"
        route_proof_path = attempt_root / "route_proof.json"
        _write_new(route_event_path, route_events)
        _write_new(route_runtime_path, route_runtime)
        _write_new(route_proof_path, route_proof)
        members = {
            "attempt_preparation.json": _file_sha256(preparation_path),
            "run_contract.json": _file_sha256(contract_path),
            "route_events.jsonl": _file_sha256(route_event_path),
            "route_runtime.json": _file_sha256(route_runtime_path),
            "route_proof.json": _file_sha256(route_proof_path),
            "block/construction_seal.json": _file_sha256(
                block_root / "construction_seal.json"
            ),
            "block/resource_evidence.json": _file_sha256(resource_path),
        }
        route_seal = {
            "schema_version": "membind.upstream-route-seal.v1",
            "status": "ROUTE_SEALED",
            "members": members,
        }
        route_seal["seal_sha256"] = _canonical_sha256(route_seal)
        _write_new(attempt_root / "route_seal.json", route_seal)
        complete = {
            **identity,
            "event": "ATTEMPT_COMPLETE",
            "status": "PASS",
            "ended_unix": time.time(),
            "build_makespan_ns": result["t_build_ns"],
            "construction_seal": str(block_root / "construction_seal.json"),
            "route_seal_sha256": route_seal["seal_sha256"],
            "run_contract_sha256": contract["payload_sha256"],
        }
        _write_new(attempt_root / "complete.json", complete)
        _append(ledger, complete)
        _write_atomic(heartbeat_path, {**identity, "status": "COMPLETE", "updated_unix": time.time()})
        return 0
    except BaseException as exc:
        if resource_start is not None and not resource_path.exists():
            try:
                resource_end = await asyncio.to_thread(
                    _capture_resource_evidence, endpoint_identity
                )
                _write_new(
                    resource_path,
                    _build_resource_evidence(
                        cell_id=cell_id,
                        attempt_id=args.attempt_id,
                        namespace=args.namespace,
                        endpoint_identity=endpoint_identity,
                        construction_start=resource_start,
                        periodic_samples=resource_samples,
                        construction_end=resource_end,
                    ),
                )
            except BaseException:
                pass
        transport_failure_evidence: dict[str, Any] | None = None
        failed_runtime = runtime_holder.get("runtime")
        if failed_runtime is not None:
            try:
                transport_failure_evidence = _persist_failure_transport_evidence(
                    attempt_root, failed_runtime
                )
            except BaseException as evidence_exc:
                transport_failure_evidence = {
                    "status": "EVIDENCE_WRITE_FAILED",
                    "error_type": (
                        f"{type(evidence_exc).__module__}."
                        f"{type(evidence_exc).__qualname__}"
                    ),
                    "error": str(evidence_exc)[:500],
                }
        failure = {
            **identity,
            "event": "ATTEMPT_FAILURE",
            "status": "FAILED",
            "failure_class": _failure_class(exc),
            "error_type": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "error": str(exc)[:1000],
            "transport_failure_evidence": transport_failure_evidence,
            "ended_unix": time.time(),
        }
        _write_new(attempt_root / "failure.json", failure)
        _append(ledger, failure)
        _write_atomic(heartbeat_path, {**identity, "status": "FAILED", "updated_unix": time.time()})
        raise
    finally:
        stop.set()
        await asyncio.gather(heartbeat, return_exceptions=True)
        runtime = runtime_holder.get("runtime")
        if runtime is not None:
            try:
                await close_formal_upstream_runtime(runtime)
            except BaseException:
                pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--context-index", type=int, required=True, choices=range(5))
    parser.add_argument("--replicate-id", type=int, required=True, choices=range(3))
    parser.add_argument("--method", required=True, choices=tuple(METHODS))
    parser.add_argument("--platform-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        return asyncio.run(_main(args))
    except BaseException as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "failure_class": _failure_class(exc),
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
