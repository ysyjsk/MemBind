#!/usr/bin/env python3
"""Seal a public, immutable platform manifest after a successful live preflight."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing {name}; source local_env.sh first")
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_catalog(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return rows


def git_identity(root: Path) -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    return {"head": head, "dirty_paths": status}


def package_versions() -> dict[str, str | None]:
    values = {}
    for package in ("vllm", "torch", "httpx", "openai", "neo4j"):
        try:
            values[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            values[package] = None
    return values


def endpoint(
    *, endpoint_id: str, role: str, host: str, port: str, gpu: str, utilization: str
) -> dict[str, Any]:
    policy_id = os.environ.get("MEMBIND_DEPLOYMENT_POLICY_ID", "P0_QWEN3_8B_AWQ")
    sampling = {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "logical_seed": "request_identity_sha256_uint32",
    }
    if policy_id == "P0_QWEN3_8B_AWQ":
        sampling.update({"min_p": 0, "presence_penalty": 1.5})
    elif policy_id == "P1_QWEN25_7B_AWQ":
        sampling["repetition_penalty"] = 1.05
    else:
        raise RuntimeError(f"unknown deployment policy: {policy_id}")
    return {
        "id": endpoint_id,
        "role": role,
        "base_url": f"http://{host}:{port}/v1",
        "served_model": required("MEMBIND_LLM_MODEL_NAME"),
        "physical_gpu": int(gpu),
        "max_model_len": int(required("MEMBIND_LLM_MAX_MODEL_LEN")),
        "max_num_seqs": int(required("MEMBIND_LLM_MAX_NUM_SEQS")),
        "max_num_batched_tokens": int(required("MEMBIND_LLM_MAX_BATCHED_TOKENS")),
        "gpu_memory_utilization": float(utilization),
        "tensor_parallel_size": 1,
        "scheduling_policy": "fcfs",
        "rope": {
            "type": "yarn",
            "factor": 2.0,
            "original_max_position_embeddings": 32768,
            "rope_theta": 1000000,
        },
        "structured_outputs_backend": "xgrammar",
        "structured_outputs_config": {
            "backend": "xgrammar",
            "disable_any_whitespace": True,
        },
        "json_separators": [", ", ": "],
        "prefix_caching": True,
        "chunked_prefill": True,
        "thinking": False,
        "sampling": sampling,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", type=Path, required=True)
    args = parser.parse_args()

    preflight = load_json(args.preflight.resolve())
    if preflight.get("mode") != "live" or preflight.get("ok") is not True:
        raise RuntimeError("profile manifest requires a successful live preflight")
    profile_id = required("MEMBIND_PROFILE_ID")
    if preflight.get("profile_id") != profile_id:
        raise RuntimeError("preflight profile identity does not match the active environment")

    repo_root = Path(required("MEMBIND_REPO_ROOT")).resolve()
    runtime_root = Path(required("MEMBIND_8B_RUNTIME_DIR")).resolve()
    profile_root = Path(required("MEMBIND_PROFILE_ROOT")).resolve()
    llm_model_root = Path(required("MEMBIND_LLM_MODEL_DIR")).resolve()
    embed_model_root = Path(required("MEMBIND_EMBED_MODEL_DIR")).resolve()
    llm_model_manifest = load_json(llm_model_root / ".membind-model-manifest.json")
    embed_model_manifest = load_json(embed_model_root / ".membind-model-manifest.json")
    native_route = load_json(Path(required("MEMBIND_NATIVE_ROUTING_CONFIG")))
    static_role_route = load_json(Path(required("MEMBIND_STATIC_ROLE_ROUTING_CONFIG")))
    v61_route = load_json(Path(required("MEMBIND_V61_ROUTING_CONFIG")))
    single_route = load_json(Path(required("MEMBIND_SINGLE_GPU_ROUTING_CONFIG")))
    checks = preflight["checks"]

    created_unix = time.time()
    payload: dict[str, Any] = {
        "schema_version": "membind.local-runtime-profile.v2",
        "profile_id": profile_id,
        "formal_experiment_identity": profile_id,
        "platform_status": "LIVE_VALIDATED_RESOURCE_MATCHED",
        "platform_formal_eligible": True,
        "created_unix": created_unix,
        "data_root": required("MEMBIND_DATA_ROOT"),
        "python_environment": required("MEMBIND_ENV"),
        "software": {
            "packages": package_versions(),
            "driver": checks["software"].get("driver"),
        },
        "hardware": checks["gpu_inventory"],
        "deployment_policy_id": os.environ.get("MEMBIND_DEPLOYMENT_POLICY_ID", "P0_QWEN3_8B_AWQ"),
        "llm_model": {
            "source_model": required("MEMBIND_LLM_SOURCE_MODEL"),
            "revision": required("MEMBIND_LLM_MODEL_REVISION"),
            "served_model": required("MEMBIND_LLM_MODEL_NAME"),
            "path": str(llm_model_root),
            "catalog_manifest": llm_model_manifest,
            "config_sha256": checks["llm_model_manifest"].get("config_sha256"),
            "tokenizer_sha256": checks["llm_model_manifest"].get("tokenizer_sha256"),
        },
        "llm_endpoints": [
            endpoint(
                endpoint_id="native-replica",
                role="authoritative-native-default",
                host=required("MEMBIND_NATIVE_LLM_HOST"),
                port=required("MEMBIND_NATIVE_LLM_PORT"),
                gpu=required("MEMBIND_NATIVE_LLM_GPU"),
                utilization=required("MEMBIND_NATIVE_LLM_GPU_MEMORY_UTILIZATION"),
            ),
            endpoint(
                endpoint_id="prepare-replica",
                role="prepare-default",
                host=required("MEMBIND_PREPARE_LLM_HOST"),
                port=required("MEMBIND_PREPARE_LLM_PORT"),
                gpu=required("MEMBIND_PREPARE_LLM_GPU"),
                utilization=required("MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION"),
            ),
        ],
        "observed_llm_capacity": {
            "native-replica": checks["native_kv_capacity"],
            "prepare-replica": checks["prepare_kv_capacity"],
        },
        "embedding": {
            "source_model": "Qwen/Qwen3-Embedding-0.6B",
            "served_model": required("MEMBIND_EMBED_MODEL_NAME"),
            "path": str(embed_model_root),
            "catalog_manifest": embed_model_manifest,
            "base_url": f"http://{required('MEMBIND_EMBED_HOST')}:{required('MEMBIND_EMBED_PORT')}/v1",
            "physical_gpu": int(required("MEMBIND_EMBED_GPU")),
            "dimension": int(required("MEMBIND_EMBED_DIMENSION")),
            "dtype": "bfloat16",
            "max_model_len": int(required("MEMBIND_EMBED_MAX_MODEL_LEN")),
            "max_num_seqs": int(required("MEMBIND_EMBED_MAX_NUM_SEQS")),
            "max_num_batched_tokens": int(required("MEMBIND_EMBED_MAX_BATCHED_TOKENS")),
            "gpu_memory_utilization": float(required("MEMBIND_EMBED_GPU_MEMORY_UTILIZATION")),
            "observed_capacity": checks["embedding_kv_capacity"],
        },
        "gpu1_colocation_budget": checks["ports_gpu_budget"],
        "neo4j": {
            "uri": required("MEMBIND_NEO4J_URI"),
            "database": os.environ.get("NEO4J_DATABASE", "neo4j"),
            "preflight": checks["neo4j_read_only_canary"],
        },
        "routing_contracts": {
            "native_dual_resource_matched": native_route,
            "native_dual_static_role": static_role_route,
            # Keep the previous key for already-sealed manifests, while
            # explicitly naming the current autoresearch candidate.
            "v61_dual_critical_path": v61_route,
            "v61_dual_elastic_affinity": v61_route,
            "single_gpu_ablation": single_route,
        },
        "fairness_contract": {
            "headline_comparison_class": "HEADLINE_B0_DUAL_RESOURCE_MATCHED",
            "headline_native_arm": "native-serial-dual",
            "headline_native_state_evolution": "B0_SERIAL_STATEFUL_ORDERED_PUBLICATION",
            "relaxed_order_upper_bound_arm": "native-parallel-dual",
            "relaxed_order_upper_bound_class": "RELAXED_ORDER_B1_UPPER_BOUND",
            "relaxed_order_may_change_state_evolution": True,
            "headline_endpoint_set_equal": True,
            "headline_model_checkpoint_equal": True,
            "headline_embedding_equal": True,
            "native_baseline_phase_blind": True,
            "native_baseline_work_conserving": True,
            "native_b0_strict_source_order": True,
            "v61_preserves_b0_state_and_publication_order": True,
            "v61_only_allowed_system_difference": (
                "frontier_critical_path_resource_scheduler_or_semantic_phase_elastic_affinity"
                "_and_exact_transcript_handoff"
            ),
            "tensor_parallel_2_not_used": True,
            "legacy_14b_results_cross_profile_only": True,
            "fresh_native_8b_required": True,
            "fresh_vector_index_and_namespace_required": True,
        },
        "runtime_preflight": preflight,
        "runtime_file_catalog": file_catalog(Path(os.environ.get("MEMBIND_RUNTIME_DIR", str(runtime_root))).resolve()),
        "git": git_identity(repo_root),
        "secrets_omitted": [
            "MEMBIND_LOCAL_API_KEY",
            "NEO4J_PASSWORD",
            "CONSTRUCTION_LLM_API_KEY",
            "EMBEDDING_API_KEY",
        ],
    }
    payload_sha256 = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    sealed = {**payload, "payload_sha256": payload_sha256}
    rendered = json.dumps(sealed, indent=2, sort_keys=True) + "\n"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(created_unix))
    target = profile_root / f"platform_manifest.{stamp}.{payload_sha256[:12]}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    file_sha256 = sha256_file(target)
    latest = profile_root / "latest.json"
    latest_tmp = profile_root / "latest.json.tmp"
    latest_tmp.write_text(
        json.dumps(
            {
                "schema_version": "membind.profile-pointer.v1",
                "profile_id": profile_id,
                "manifest_path": str(target),
                "payload_sha256": payload_sha256,
                "file_sha256": file_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    latest_tmp.replace(latest)
    print(
        json.dumps(
            {"path": str(target), "payload_sha256": payload_sha256, "file_sha256": file_sha256},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
