#!/usr/bin/env python3
"""Create one immutable run contract tied to a validated 8B platform manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


PROFILE = "local-qwen3-8b-awq-dualreplica-v1"
ROOT = Path(__file__).resolve().parents[2]
SFWB_SOURCE = ROOT / "saturated_fixed_work_baseline_v1_3/src"
for source in (
    SFWB_SOURCE,
    ROOT / "membind-validation/src",
    ROOT / "paper-eval-v3/src",
):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from saturated_fixed_work_baseline_v1_3.membind_v6_1.identity import (  # noqa: E402
    implementation_bundle,
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def normalized_endpoints(value: dict[str, Any]) -> list[tuple[str, str, str, int]]:
    return sorted(
        (
            str(row["id"]),
            str(row["base_url"]).rstrip("/"),
            str(row["served_model"]),
            int(row["physical_gpu"]),
        )
        for row in value["endpoint_set"]
    )


def namespace_counts(namespace: str) -> dict[str, int]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        os.environ["MEMBIND_NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    try:
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            row = session.run(
                """
                CALL () {
                  MATCH (n) WHERE n.group_id = $namespace
                  RETURN count(n) AS node_count
                }
                CALL () {
                  MATCH ()-[r]->() WHERE r.group_id = $namespace
                  RETURN count(r) AS relationship_count
                }
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--arm",
        required=True,
        choices=(
            "native-dual",
            "native-serial-dual",
            "native-parallel-dual",
            "native-static-role-dual",
            "v61-dual",
            "native-single",
            "v61-single",
        ),
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--platform-manifest", type=Path, required=True)
    parser.add_argument("--workload-manifest", type=Path, required=True)
    parser.add_argument("--runner-implementation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    experiment_root = Path(os.environ["MEMBIND_EXPERIMENT_ROOT"]).resolve()
    output = args.output.resolve()
    if experiment_root != output and experiment_root not in output.parents:
        raise RuntimeError(f"output must be inside {experiment_root}")
    if not args.namespace.startswith(f"{PROFILE}-"):
        raise RuntimeError("namespace is outside the 8B profile")
    if any(value in args.namespace.casefold() for value in ("14b", "32b", "fp8")):
        raise RuntimeError("namespace mixes another model identity")
    for path in (args.platform_manifest, args.workload_manifest, args.runner_implementation):
        if not path.resolve().is_file():
            raise FileNotFoundError(path)

    platform = read_json(args.platform_manifest.resolve())
    if (
        platform.get("profile_id") != PROFILE
        or platform.get("platform_formal_eligible") is not True
        or platform.get("platform_status") != "LIVE_VALIDATED_RESOURCE_MATCHED"
    ):
        raise RuntimeError("platform manifest is not eligible")
    route_name = (
        "native_dual_resource_matched"
        if args.arm in {"native-dual", "native-serial-dual", "native-parallel-dual"}
        else "native_dual_static_role"
        if args.arm == "native-static-role-dual"
        else "v61_dual_elastic_affinity"
        if args.arm == "v61-dual"
        else "single_gpu_ablation"
    )
    route_source: dict[str, Any]
    if args.arm == "v61-dual":
        route_path = Path(os.environ["MEMBIND_V61_ROUTING_CONFIG"]).resolve()
        route = read_json(route_path)
        platform_endpoints = {
            "endpoint_set": [
                {
                    "id": row["id"],
                    "base_url": row["base_url"],
                    "served_model": row["served_model"],
                    "physical_gpu": row["physical_gpu"],
                }
                for row in platform["llm_endpoints"]
            ]
        }
        if (
            route.get("schema_version") != "membind.routing-policy.v1"
            or route.get("profile_id") != PROFILE
            or normalized_endpoints(route) != normalized_endpoints(platform_endpoints)
        ):
            raise RuntimeError("V6.1 routing contract differs from the frozen platform endpoints")
        route_source = {
            "kind": "method_contract",
            "path": str(route_path),
            "file_sha256": sha256_file(route_path),
        }
    else:
        route = platform["routing_contracts"][route_name]
        route_source = {
            "kind": "platform_reference_contract",
            "route_name": route_name,
            "platform_payload_sha256": platform["payload_sha256"],
        }
    initial_counts = namespace_counts(args.namespace)
    if initial_counts != {"node_count": 0, "relationship_count": 0}:
        raise RuntimeError(f"namespace is not fresh: {initial_counts}")
    method = (
        "NATIVE_STATIC_ROLE"
        if args.arm == "native-static-role-dual"
        else "NATIVE"
        if args.arm.startswith("native")
        else "V6_1"
    )
    comparison_class = "HEADLINE_DUAL_RESOURCE_MATCHED" if args.arm.endswith("dual") else "SINGLE_GPU_ABLATION"
    implementation = implementation_bundle(args.runner_implementation.resolve())
    payload = {
        "schema_version": "membind.8b-experiment-contract.v1",
        "profile_id": PROFILE,
        "run_id": args.run_id,
        "namespace": args.namespace,
        "arm": args.arm,
        "method": method,
        "native_execution_semantics": (
            "B1_PARALLEL_EPISODES"
            if args.arm == "native-parallel-dual"
            else "B0_SERIAL_EPISODES"
            if args.arm in {"native-dual", "native-serial-dual", "native-static-role-dual"}
            else None
        ),
        "comparison_class": comparison_class,
        "platform_manifest": {
            "path": str(args.platform_manifest.resolve()),
            "file_sha256": sha256_file(args.platform_manifest.resolve()),
            "payload_sha256": platform["payload_sha256"],
        },
        "workload_manifest": {
            "path": str(args.workload_manifest.resolve()),
            "file_sha256": sha256_file(args.workload_manifest.resolve()),
        },
        "runner_implementation": {
            "path": str(args.runner_implementation.resolve()),
            "file_sha256": sha256_file(args.runner_implementation.resolve()),
        },
        "implementation_bundle": implementation,
        "routing": route,
        "routing_contract_source": route_source,
        "endpoint_set": route["endpoint_set"],
        "embedding_identity": platform["embedding"],
        "decoding_contract": {
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 20260806,
            "thinking": False,
            "structured_outputs_backend": "xgrammar",
            "max_completion_tokens": 32768,
            "sdk_max_retries": 0,
        },
        "cache_protocol": {
            "policy": "reset_then_identical_structured_warmup_v1",
            "reset_scope": ["native-replica", "prepare-replica"],
            "warmup_timed": False,
            "vllm_server_dev_mode": True,
        },
        "freshness_contract": {
            "namespace_initial_counts": initial_counts,
            "namespace_checked_unix": time.time(),
            "fresh_embedding_and_vector_indexes_required": True,
            "no_14b_or_32b_artifact_reuse": True,
        },
        "created_unix": time.time(),
    }
    payload["payload_sha256"] = canonical_hash(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({"path": str(output), "payload_sha256": payload["payload_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
