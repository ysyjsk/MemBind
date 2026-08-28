#!/usr/bin/env python3
"""Verify that a Native/V6.1 result pair differs only by the declared method."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--v61", type=Path, required=True)
    args = parser.parse_args()
    native = load(args.native.resolve())
    v61 = load(args.v61.resolve())

    checks = {
        "schema": native.get("schema_version") == v61.get("schema_version") == "membind.8b-experiment-contract.v1",
        "profile": native.get("profile_id") == v61.get("profile_id") == "local-qwen3-8b-awq-dualreplica-v1",
        "method_labels": native.get("method") == "NATIVE" and v61.get("method") == "V6_1",
        "comparison_class": native.get("comparison_class") == v61.get("comparison_class"),
        "platform": native.get("platform_manifest", {}).get("payload_sha256") == v61.get("platform_manifest", {}).get("payload_sha256"),
        "workload": native.get("workload_manifest", {}).get("file_sha256") == v61.get("workload_manifest", {}).get("file_sha256"),
        "endpoint_set": normalized_endpoints(native) == normalized_endpoints(v61),
        "embedding": native.get("embedding_identity") == v61.get("embedding_identity"),
        "decoding": native.get("decoding_contract") == v61.get("decoding_contract"),
        "cache_protocol": native.get("cache_protocol") == v61.get("cache_protocol"),
        "fresh_namespaces": native.get("namespace") != v61.get("namespace"),
        "native_namespace_empty": native.get("freshness_contract", {}).get(
            "namespace_initial_counts"
        )
        == {"node_count": 0, "relationship_count": 0},
        "v61_namespace_empty": v61.get("freshness_contract", {}).get(
            "namespace_initial_counts"
        )
        == {"node_count": 0, "relationship_count": 0},
        "native_router": native.get("routing", {}).get("router", {}).get("policy") == "capacity_weighted_least_outstanding",
        "v61_router": v61.get("routing", {}).get("router", {}).get("policy")
        in {
            "semantic_phase_elastic_affinity",
            "frontier_critical_path_resource_scheduler_v1",
        },
        "v61_idle_spillover": (
            (
                v61.get("routing", {}).get("router", {}).get("policy")
                == "semantic_phase_elastic_affinity"
                and v61.get("routing", {}).get("router", {}).get("spillover")
                == "idle_replica_only"
            )
            or (
                v61.get("routing", {}).get("router", {}).get("policy")
                == "frontier_critical_path_resource_scheduler_v1"
                and v61.get("routing", {}).get("router", {}).get("spillover")
                == "critical_path_earliest_finish"
            )
        ),
        "v61_exact_handoff": v61.get("routing", {}).get("router", {}).get("handoff_payload") == "exact_extraction_transcript_no_kv_transfer",
    }
    headline = native.get("comparison_class") == "HEADLINE_DUAL_RESOURCE_MATCHED"
    checks["headline_endpoint_count"] = (len(normalized_endpoints(native)) == 2) if headline else True
    result = {
        "schema_version": "membind.8b-fairness-check.v1",
        "ok": all(checks.values()),
        "native_manifest": str(args.native.resolve()),
        "v61_manifest": str(args.v61.resolve()),
        "checks": checks,
        "allowed_differences": [
            "method",
            "routing.policy",
            "routing.phase_visibility",
            "routing.phase_bindings",
            "routing.idle_replica_spillover",
            "runner_implementation",
            "namespace",
            "run_id",
            "created_unix",
            "payload_sha256",
        ],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
