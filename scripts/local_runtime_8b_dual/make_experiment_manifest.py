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
from saturated_fixed_work_baseline_v1_3.membind_v6_1.core import (  # noqa: E402
    MEMBIND_CORE_ROUTE_POLICY,
    core_identity,
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


def semantics_for_arm(arm: str) -> tuple[str, str, dict[str, Any]]:
    """Return method, comparison class, and the state/publication contract.

    B0 is the only semantics-preserving Native comparator.  B1 intentionally
    remains available as a relaxed-order ceiling, but can never be emitted as
    a headline baseline by accident.
    """
    if arm == "native-parallel-dual":
        return (
            "NATIVE",
            "RELAXED_ORDER_B1_UPPER_BOUND",
            {
                "mode": "RELAXED_ORDER_B1",
                "episode_concurrency": "parallel",
                "durable_publication_order": "not_guaranteed",
                "may_change_state_evolution": True,
                "dependency_free_early_execution": False,
            },
        )
    if arm == "native-static-role-dual":
        return (
            "NATIVE_STATIC_ROLE",
            "B0_STATIC_ROLE_ABLATION",
            {
                "mode": "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
                "episode_concurrency": 1,
                "durable_publication_order": "source_sequence_ascending",
                "may_change_state_evolution": False,
                "dependency_free_early_execution": False,
            },
        )
    if arm in {"native-dual", "native-serial-dual", "v61-dual"}:
        comparison = (
            "HEADLINE_B0_DUAL_RESOURCE_MATCHED"
            if arm in {"native-serial-dual", "v61-dual"}
            else "B0_LEGACY_ALIAS"
        )
        return (
            "NATIVE" if arm != "v61-dual" else "V6_1",
            comparison,
            {
                "mode": "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
                "episode_concurrency": 1,
                "durable_publication_order": "source_sequence_ascending",
                "authoritative_state_cut": "after_previous_source_durable_publication",
                "may_change_state_evolution": False,
                "dependency_free_early_execution": arm == "v61-dual",
                "early_operation_scope": (
                    "certified_dependency_free_prepare_or_replay_only"
                    if arm == "v61-dual"
                    else "none"
                ),
            },
        )
    if arm in {"native-single", "v61-single"}:
        return (
            "NATIVE" if arm == "native-single" else "V6_1",
            "SINGLE_GPU_ABLATION",
            {
                "mode": "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
                "episode_concurrency": 1,
                "durable_publication_order": "source_sequence_ascending",
                "may_change_state_evolution": False,
                "dependency_free_early_execution": arm == "v61-single",
            },
        )
    raise ValueError(f"unknown arm: {arm}")


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
    parser.add_argument(
        "--method-boundary",
        choices=("MEMBIND_CORE", "WORK_REDUCTION_EXTENSION"),
        help="Required for V6.1: distinguish semantics-preserving Core from work-changing extensions.",
    )
    parser.add_argument("--extension-id", help="Stable identifier for a non-Core extension/ablation.")
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
    method, comparison_class, state_contract = semantics_for_arm(args.arm)
    if args.arm == "v61-dual" and args.method_boundary is None:
        raise RuntimeError("--method-boundary is required for v61-dual; do not mix Core with extensions")
    if args.arm != "v61-dual" and args.method_boundary is not None:
        raise RuntimeError("--method-boundary is only accepted for v61-dual")
    if args.method_boundary == "WORK_REDUCTION_EXTENSION" and not args.extension_id:
        raise RuntimeError("--extension-id is required for WORK_REDUCTION_EXTENSION")
    if args.method_boundary == "MEMBIND_CORE" and args.extension_id:
        raise RuntimeError("Core contracts cannot carry an extension id")
    if (
        args.arm == "v61-dual"
        and args.method_boundary == "MEMBIND_CORE"
        and route.get("router", {}).get("policy") != MEMBIND_CORE_ROUTE_POLICY
    ):
        raise RuntimeError(
            "MemBind-Core contracts require the frozen semantic-phase elastic route"
        )
    is_b1 = comparison_class == "RELAXED_ORDER_B1_UPPER_BOUND"
    method_boundary = {
        "id": (
            "B1_RELAXED_ORDER_REFERENCE"
            if is_b1
            else args.method_boundary or "NATIVE_REFERENCE"
        ),
        "preserves_native_computation_semantics": not is_b1 and args.method_boundary != "WORK_REDUCTION_EXTENSION",
        "preserves_native_work": not is_b1 and args.method_boundary != "WORK_REDUCTION_EXTENSION",
        "allowed_transformations": (
            [
                "dependency_aware_prepare_execution_overlap",
                "dependency_aware_admission_and_work_conserving_partition_dispatch",
                "exact_certified_replay_of_dependency_free_extraction",
                "ordered_authoritative_publication",
            ]
            if args.method_boundary == "MEMBIND_CORE"
            else []
        ),
        "excluded_from_core": [
            "summary_bypass",
            "predicate_pushdown",
            "grounded_or_deterministic_materialization",
            "any_reduction_or_replacement_of_native_provider_work",
        ],
        "extension_id": args.extension_id,
    }
    implementation = implementation_bundle(args.runner_implementation.resolve())
    payload = {
        "schema_version": "membind.8b-experiment-contract.v1",
        "profile_id": PROFILE,
        "run_id": args.run_id,
        "namespace": args.namespace,
        "arm": args.arm,
        "method": method,
        # Kept for consumers of the v1 schema; the structured contract below
        # is authoritative and is populated for Native, V6.1, and ablations.
        "native_execution_semantics": state_contract["mode"],
        "state_evolution_contract": state_contract,
        "method_boundary": method_boundary,
        "core_identity": (
            core_identity()
            if args.arm == "v61-dual" and args.method_boundary == "MEMBIND_CORE"
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
