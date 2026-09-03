#!/usr/bin/env python3
"""Fail-closed validation for the isolated MemBind 8B dual-replica profile."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any, Callable

import httpx


EXPECTED_PROFILE = "local-qwen3-8b-awq-dualreplica-v1"
EXPECTED_MODEL = "qwen3-8b-awq"
EXPECTED_MODEL_REVISION = "4da05a8edb55c6046cce958586c33b61da07bb79"
EXPECTED_MODEL_DIGEST = "9426c790db40e413df2ce871c01d29f773dfffe82cb581c652ecb78f1e975d3a"
EXPECTED_LLM_WEIGHTS = {
    "model-00001-of-00002.safetensors": (
        4_853_922_024,
        "6e112429856bc65e3837a9f38d6f6b71ffdda832cb46299a12f4fa8f6352516e",
    ),
    "model-00002-of-00002.safetensors": (
        1_244_659_840,
        "20c2d6366ab85c90786ccdd829cd2b9e7d30ef3b2ebbb998280e7e4014b542ff",
    ),
}
EXPECTED_EMBED_WEIGHT = (
    1_191_586_416,
    "0437e45c94563b09e13cb7a64478fc406947a93cb34a7e05870fc8dcd48e23fd",
)
EXPECTED_LLM_METADATA = {
    "config.json": "7457674d8044143cd4159e47deecf28fd6698a9826569094b60d7bead8f351ee",
    "tokenizer.json": "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4",
    "tokenizer_config.json": "d5d09f07b48c3086c508b30d1c9114bd1189145b74e982a265350c923acd8101",
    "generation_config.json": "2325da0f15bb848e018c5ae071b7943332e9f871d6b60e2ed22ca97d4cb993d2",
}
EXPECTED_EMBED_METADATA = {
    "config.json": "b5bf1f51fc45be473a54718cef92448d90a1be001bf9b9a44b8c7f10a19feaa9",
    "tokenizer.json": "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a",
    "tokenizer_config.json": "253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0",
}
LEGACY_PORTS = {18100, 18101}


class Checks:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, name: str, ok: bool, **details: Any) -> None:
        self.rows[name] = {"ok": bool(ok), **details}

    def capture(self, name: str, function: Callable[[], dict[str, Any]]) -> None:
        try:
            row = function()
            self.add(name, bool(row.pop("ok", True)), **row)
        except BaseException as exc:
            self.add(name, False, error=f"{type(exc).__name__}: {exc}")

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(bool(row["ok"]) for row in self.rows.values())


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing environment variable {name}; source local_env.sh first")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def model_manifest_check(*, verify_content: bool) -> dict[str, Any]:
    root = Path(required("MEMBIND_LLM_MODEL_DIR")).resolve()
    manifest_path = root / ".membind-model-manifest.json"
    config_path = root / "config.json"
    tokenizer_path = root / "tokenizer.json"
    manifest = load_json(manifest_path)
    weights = sorted(root.glob("*.safetensors"))
    metadata_checks = {
        name: {
            "sha256": sha256_file(root / name),
            "expected_sha256": expected,
            "ok": (root / name).is_file() and sha256_file(root / name) == expected,
        }
        for name, expected in EXPECTED_LLM_METADATA.items()
    }
    weight_checks = {}
    for path in weights:
        expected = EXPECTED_LLM_WEIGHTS.get(path.name)
        observed_sha256 = sha256_file(path) if verify_content else None
        weight_checks[path.name] = {
            "bytes": path.stat().st_size,
            "expected_bytes": expected[0] if expected else None,
            "sha256": observed_sha256,
            "expected_sha256": expected[1] if expected else None,
            "content_hash_verified": verify_content,
            "ok": bool(expected)
            and path.stat().st_size == expected[0]
            and (not verify_content or observed_sha256 == expected[1]),
        }
    ok = (
        manifest.get("source_model") == "Qwen/Qwen3-8B-AWQ"
        and manifest.get("revision") == EXPECTED_MODEL_REVISION
        and manifest.get("sha256") == EXPECTED_MODEL_DIGEST
        and config_path.is_file()
        and tokenizer_path.is_file()
        and all(row["ok"] for row in metadata_checks.values())
        and len(weights) == 2
        and all(row["ok"] for row in weight_checks.values())
    )
    return {
        "ok": ok,
        "path": str(root),
        "source_model": manifest.get("source_model"),
        "revision": manifest.get("revision"),
        "catalog_sha256": manifest.get("sha256"),
        "config_sha256": sha256_file(config_path),
        "tokenizer_sha256": sha256_file(tokenizer_path),
        "metadata_checks": metadata_checks,
        "weight_files": [path.name for path in weights],
        "weight_checks": weight_checks,
        "content_hash_verified": verify_content,
    }


def embedding_manifest_check(*, verify_content: bool) -> dict[str, Any]:
    root = Path(required("MEMBIND_EMBED_MODEL_DIR")).resolve()
    manifest_path = root / ".membind-model-manifest.json"
    config_path = root / "config.json"
    weights = sorted(root.glob("*.safetensors"))
    manifest = load_json(manifest_path)
    metadata_checks = {
        name: {
            "sha256": sha256_file(root / name),
            "expected_sha256": expected,
            "ok": (root / name).is_file() and sha256_file(root / name) == expected,
        }
        for name, expected in EXPECTED_EMBED_METADATA.items()
    }
    weight = weights[0] if len(weights) == 1 else None
    observed_sha256 = sha256_file(weight) if verify_content and weight else None
    weight_ok = bool(weight) and weight.stat().st_size == EXPECTED_EMBED_WEIGHT[0]
    if verify_content:
        weight_ok = weight_ok and observed_sha256 == EXPECTED_EMBED_WEIGHT[1]
    return {
        "ok": (
            config_path.is_file()
            and len(weights) == 1
            and bool(manifest.get("sha256"))
            and weight_ok
            and all(row["ok"] for row in metadata_checks.values())
        ),
        "path": str(root),
        "catalog_sha256": manifest.get("sha256"),
        "config_sha256": sha256_file(config_path),
        "metadata_checks": metadata_checks,
        "weight_files": [path.name for path in weights],
        "weight_sha256": observed_sha256,
        "expected_weight_sha256": EXPECTED_EMBED_WEIGHT[1],
        "content_hash_verified": verify_content,
    }


def route_check() -> dict[str, Any]:
    native = load_json(Path(required("MEMBIND_NATIVE_ROUTING_CONFIG")))
    static_role = load_json(Path(required("MEMBIND_STATIC_ROLE_ROUTING_CONFIG")))
    v61 = load_json(Path(required("MEMBIND_V61_ROUTING_CONFIG")))
    single = load_json(Path(required("MEMBIND_SINGLE_GPU_ROUTING_CONFIG")))

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

    native_set = normalized_endpoints(native)
    static_role_set = normalized_endpoints(static_role)
    v61_set = normalized_endpoints(v61)
    single_set = normalized_endpoints(single)
    expected = sorted(
        [
            ("native-replica", "http://127.0.0.1:18200/v1", EXPECTED_MODEL, 0),
            ("prepare-replica", "http://127.0.0.1:18201/v1", EXPECTED_MODEL, 1),
        ]
    )
    bindings = v61.get("router", {}).get("bindings", {})
    v61_policy = v61.get("router", {}).get("policy")
    v61_policy_ok = (
        v61_policy == "semantic_phase_elastic_affinity"
        and v61.get("router", {}).get("spillover") == "idle_replica_only"
    ) or (
        v61_policy == "semantic_phase_capacity_balanced_affinity"
        and v61.get("router", {}).get("spillover")
        == "manifest_capacity_weighted_projected_load"
        and v61.get("router", {}).get("capacity_source")
        == "observed_kv_tokens_in_platform_manifest"
        and v61.get("router", {}).get("tie_break")
        == "semantic_phase_preferred_endpoint"
    ) or (
        v61_policy == "frontier_critical_path_resource_scheduler_v1"
        and v61.get("router", {}).get("spillover")
        == "critical_path_earliest_finish"
        and v61.get("router", {}).get("capacity_source")
        == "observed_endpoint_identity_and_measured_service_ewma"
        and v61.get("router", {}).get("work_estimate_source")
        == "admitted_request_tokens_and_service_duration"
        and v61.get("router", {}).get("critical_path_source")
        == "durable_frontier_phase_and_ready_task_slack"
        and v61.get("router", {}).get("tie_break")
        == "frontier_critical_then_preferred_endpoint_then_endpoint_id"
    ) or (
        v61_policy == "semantic_phase_logical_token_affinity"
        and v61.get("router", {}).get("spillover")
        == "logical_call_projected_token_debt"
        and v61.get("router", {}).get("capacity_source")
        == "observed_kv_tokens_in_platform_manifest"
        and v61.get("router", {}).get("work_estimate_source")
        == "admission_request_tokens"
        and v61.get("router", {}).get("affinity_scope")
        == "one_graphiti_logical_call_all_transport_expansions"
        and v61.get("router", {}).get("tie_break")
        == "semantic_phase_preferred_endpoint"
    )
    ok = (
        native.get("profile_id") == EXPECTED_PROFILE
        and static_role.get("profile_id") == EXPECTED_PROFILE
        and v61.get("profile_id") == EXPECTED_PROFILE
        and native_set == expected
        and static_role_set == expected
        and v61_set == expected
        and native.get("router", {}).get("policy") == "capacity_weighted_least_outstanding"
        and native.get("router", {}).get("phase_labels_visible") is False
        and native.get("router", {}).get("work_conserving") is True
        and static_role.get("router", {}).get("policy") == "graphiti_request_class_affinity"
        and static_role.get("constraints", {}).get("certified_capture_replay") is False
        and v61_policy_ok
        and bindings == {"PREPARE": "prepare-replica", "NATIVE": "native-replica"}
        and single_set == [expected[0]]
    )
    return {
        "ok": ok,
        "native_endpoints": native_set,
        "static_role_endpoints": static_role_set,
        "v61_endpoints": v61_set,
        "same_dual_endpoint_set": native_set == static_role_set == v61_set,
        "native_policy": native.get("router", {}).get("policy"),
        "static_role_policy": static_role.get("router", {}).get("policy"),
        "v61_policy": v61_policy,
        "single_gpu_ablation_endpoints": single_set,
    }


def gpu_inventory() -> dict[str, Any]:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        index, name, uuid, memory = [item.strip() for item in line.split(",", 3)]
        rows.append(
            {"index": int(index), "name": name, "uuid": uuid, "memory_mib": int(memory)}
        )
    topology = subprocess.run(
        ["nvidia-smi", "topo", "-m"], check=True, capture_output=True, text=True
    ).stdout
    topology = re.sub(r"\x1b\[[0-9;]*m", "", topology)
    relevant = {row["index"]: row for row in rows if row["index"] in {0, 1}}
    ok = (
        set(relevant) == {0, 1}
        and relevant[0]["name"] == relevant[1]["name"]
        and relevant[0]["uuid"] != relevant[1]["uuid"]
        and "PHB" in topology
    )
    return {"ok": ok, "gpus": rows, "topology": topology.strip()}


def identity_check() -> dict[str, Any]:
    profile = required("MEMBIND_PROFILE_ID")
    profile_root = Path(required("MEMBIND_PROFILE_ROOT")).resolve()
    experiment_root = Path(required("MEMBIND_EXPERIMENT_ROOT")).resolve()
    namespace_prefix = required("MEMBIND_NAMESPACE_PREFIX")
    legacy_profile = Path(required("MEMBIND_DATA_ROOT")) / "profiles/local-qwen3-14b-awq-v1"
    forbidden_fragments = ("qwen3-14b", "32b", "fp8")
    identity_text = " ".join(
        [profile, str(profile_root), str(experiment_root), namespace_prefix]
    ).casefold()
    ok = (
        profile == EXPECTED_PROFILE
        and profile_root.name == EXPECTED_PROFILE
        and experiment_root.name == EXPECTED_PROFILE
        and namespace_prefix == f"{EXPECTED_PROFILE}-"
        and profile_root != legacy_profile.resolve()
        and not any(fragment in identity_text for fragment in forbidden_fragments)
    )
    return {
        "ok": ok,
        "profile_id": profile,
        "profile_root": str(profile_root),
        "experiment_root": str(experiment_root),
        "namespace_prefix": namespace_prefix,
        "legacy_profile_root": str(legacy_profile),
    }


def ports_and_budget_check() -> dict[str, Any]:
    ports = {
        "native": int(required("MEMBIND_NATIVE_LLM_PORT")),
        "prepare": int(required("MEMBIND_PREPARE_LLM_PORT")),
        "embedding": int(required("MEMBIND_EMBED_PORT")),
    }
    prepare = float(required("MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION"))
    embedding = float(required("MEMBIND_EMBED_GPU_MEMORY_UTILIZATION"))
    maximum = float(required("MEMBIND_GPU1_MAX_COMBINED_UTILIZATION"))
    total = prepare + embedding
    ok = (
        len(set(ports.values())) == len(ports)
        and not (set(ports.values()) & LEGACY_PORTS)
        and required("MEMBIND_NATIVE_LLM_GPU") == "0"
        and required("MEMBIND_PREPARE_LLM_GPU") == "1"
        and required("MEMBIND_EMBED_GPU") == "1"
        and abs(total - maximum) < 1e-9
        and total <= 0.95
    )
    return {
        "ok": ok,
        "ports": ports,
        "legacy_ports_excluded": sorted(LEGACY_PORTS),
        "gpu1_prepare_utilization": prepare,
        "gpu1_embedding_utilization": embedding,
        "gpu1_combined_utilization": total,
        "gpu1_max_combined_utilization": maximum,
    }


def software_check() -> dict[str, Any]:
    versions = {}
    for package in ("vllm", "torch", "httpx", "openai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    driver = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()[0].strip()
    return {"ok": versions.get("vllm") == "0.26.0", "versions": versions, "driver": driver}


def tcp_listening(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((host, port)) == 0


def startup_availability_check() -> dict[str, Any]:
    endpoints = {
        "native": (required("MEMBIND_NATIVE_LLM_HOST"), int(required("MEMBIND_NATIVE_LLM_PORT"))),
        "prepare": (required("MEMBIND_PREPARE_LLM_HOST"), int(required("MEMBIND_PREPARE_LLM_PORT"))),
        "embedding": (required("MEMBIND_EMBED_HOST"), int(required("MEMBIND_EMBED_PORT"))),
    }
    occupied = [name for name, (host, port) in endpoints.items() if tcp_listening(host, port)]
    sessions = [
        required("MEMBIND_NATIVE_LLM_TMUX_SESSION"),
        required("MEMBIND_PREPARE_LLM_TMUX_SESSION"),
        required("MEMBIND_EMBED_TMUX_SESSION"),
    ]
    active_sessions = []
    for session in sessions:
        result = subprocess.run(
            ["tmux", "has-session", "-t", f"={session}"], capture_output=True
        )
        if result.returncode == 0:
            active_sessions.append(session)
    compute = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_uuid,used_memory,process_name",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().splitlines()
    legacy_active = [port for port in sorted(LEGACY_PORTS) if tcp_listening("127.0.0.1", port)]
    return {
        "ok": not occupied and not active_sessions and not compute and not legacy_active,
        "occupied_profile_ports": occupied,
        "active_profile_tmux_sessions": active_sessions,
        "active_compute_processes": compute,
        "active_legacy_ports": legacy_active,
        "remediation": "stop the old profile explicitly, then rerun startup preflight; this script never stops it",
    }


def api_models(client: httpx.Client, base_url: str, expected: str) -> dict[str, Any]:
    response = client.get(f"{base_url.rstrip('/')}/models")
    response.raise_for_status()
    ids = [str(row.get("id")) for row in response.json().get("data", [])]
    return {"ok": ids == [expected], "expected": [expected], "ids": ids}


def structured_probe(client: httpx.Client, base_url: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "replica": {"type": "string"}},
        "required": ["ok", "replica"],
        "additionalProperties": False,
    }
    response = client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json={
            "model": EXPECTED_MODEL,
            "messages": [{"role": "user", "content": "Return ok=true and replica='qwen3-8b-awq'."}],
            "temperature": 0,
            "top_p": 1,
            "seed": 20260806,
            "max_tokens": 64,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "profile_probe", "schema": schema, "strict": True},
            },
        },
    )
    response.raise_for_status()
    body = response.json()
    parsed = json.loads(body["choices"][0]["message"]["content"])
    expected = {"ok": True, "replica": EXPECTED_MODEL}
    return {
        "ok": parsed == expected and body["choices"][0].get("finish_reason") == "stop",
        "content": parsed,
        "finish_reason": body["choices"][0].get("finish_reason"),
    }


def embedding_probe(client: httpx.Client, base_url: str) -> dict[str, Any]:
    inputs = [f"MemBind 8B profile embedding probe {index}" for index in range(16)]
    response = client.post(
        f"{base_url.rstrip('/')}/embeddings",
        json={"model": required("MEMBIND_EMBED_MODEL_NAME"), "input": inputs},
    )
    response.raise_for_status()
    rows = response.json()["data"]
    dimensions = [len(row["embedding"]) for row in rows]
    expected = int(required("MEMBIND_EMBED_DIMENSION"))
    return {
        "ok": len(rows) == len(inputs) and all(value == expected for value in dimensions),
        "vectors": len(rows),
        "expected_vectors": len(inputs),
        "dimension": dimensions[0] if dimensions else None,
        "expected_dimension": expected,
    }


def pid_gpu_check(pidfile: Path, expected_gpu: str) -> dict[str, Any]:
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    values = {}
    for item in environ:
        if b"=" in item:
            key, value = item.split(b"=", 1)
            values[key.decode(errors="replace")] = value.decode(errors="replace")
    observed = values.get("CUDA_VISIBLE_DEVICES")
    return {"ok": observed == expected_gpu, "pid": pid, "expected_gpu": expected_gpu, "observed_gpu": observed}


def pid_command_check(
    pidfile: Path,
    *,
    expected_options: dict[str, str],
    expected_flags: tuple[str, ...],
) -> dict[str, Any]:
    pid = int(pidfile.read_text(encoding="utf-8").strip())
    arguments = [
        item.decode(errors="replace")
        for item in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if item
    ]
    observed_options: dict[str, str | None] = {}
    for option in expected_options:
        try:
            index = arguments.index(option)
            observed_options[option] = arguments[index + 1]
        except (ValueError, IndexError):
            observed_options[option] = None
    observed_flags = {flag: flag in arguments for flag in expected_flags}
    model_path = required(
        "MEMBIND_EMBED_MODEL_DIR" if "--runner" in expected_options else "MEMBIND_LLM_MODEL_DIR"
    )
    redacted = list(arguments)
    if "--api-key" in redacted:
        key_index = redacted.index("--api-key")
        if key_index + 1 < len(redacted):
            redacted[key_index + 1] = "<redacted>"
    command_sha256 = hashlib.sha256("\0".join(redacted).encode()).hexdigest()
    ok = (
        "serve" in arguments
        and model_path in arguments
        and all(observed_options[key] == value for key, value in expected_options.items())
        and all(observed_flags.values())
    )
    return {
        "ok": ok,
        "pid": pid,
        "expected_options": expected_options,
        "observed_options": observed_options,
        "observed_flags": observed_flags,
        "redacted_command_sha256": command_sha256,
    }


def log_capacity(path: Path, marker: str, minimum_tokens: int) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8", errors="replace")
    marker_index = content.rfind(marker)
    if marker_index < 0:
        raise RuntimeError(f"latest startup marker not found in {path}")
    latest = content[marker_index:]
    cache_values = [int(value.replace(",", "")) for value in re.findall(r"GPU KV cache size: ([0-9,]+) tokens", latest)]
    if not cache_values:
        raise RuntimeError(f"KV cache capacity not found after latest startup marker in {path}")
    observed = cache_values[-1]
    return {
        "ok": observed >= minimum_tokens,
        "path": str(path),
        "observed_kv_tokens": observed,
        "required_kv_tokens": minimum_tokens,
    }


def neo4j_check() -> dict[str, Any]:
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        required("MEMBIND_NEO4J_URI"),
        auth=(required("NEO4J_USER"), required("NEO4J_PASSWORD")),
    )
    try:
        driver.verify_connectivity()
        with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as session:
            value = session.run("RETURN 1 AS ok").single(strict=True)["ok"]
        return {"ok": value == 1, "uri": required("MEMBIND_NEO4J_URI"), "read_only_canary": value}
    finally:
        driver.close()


def run_static(checks: Checks, *, verify_model_content: bool) -> None:
    checks.capture("identity_isolation", identity_check)
    checks.capture("ports_gpu_budget", ports_and_budget_check)
    checks.capture(
        "llm_model_manifest",
        lambda: model_manifest_check(verify_content=verify_model_content),
    )
    checks.capture(
        "embedding_model_manifest",
        lambda: embedding_manifest_check(verify_content=verify_model_content),
    )
    checks.capture("routing_contract", route_check)
    checks.capture("gpu_inventory", gpu_inventory)
    checks.capture("software", software_check)


def run_live(checks: Checks, timeout: float) -> None:
    api_key = required("MEMBIND_LOCAL_API_KEY")
    native_url = f"http://{required('MEMBIND_NATIVE_LLM_HOST')}:{required('MEMBIND_NATIVE_LLM_PORT')}/v1"
    prepare_url = f"http://{required('MEMBIND_PREPARE_LLM_HOST')}:{required('MEMBIND_PREPARE_LLM_PORT')}/v1"
    embedding_url = f"http://{required('MEMBIND_EMBED_HOST')}:{required('MEMBIND_EMBED_PORT')}/v1"
    with httpx.Client(
        timeout=timeout,
        trust_env=False,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as client:
        checks.capture("native_models", lambda: api_models(client, native_url, EXPECTED_MODEL))
        checks.capture("prepare_models", lambda: api_models(client, prepare_url, EXPECTED_MODEL))
        checks.capture(
            "embedding_models",
            lambda: api_models(client, embedding_url, required("MEMBIND_EMBED_MODEL_NAME")),
        )
        checks.capture("native_structured_json", lambda: structured_probe(client, native_url))
        checks.capture("prepare_structured_json", lambda: structured_probe(client, prepare_url))
        checks.capture("embedding_dimension_batch16", lambda: embedding_probe(client, embedding_url))
    checks.capture(
        "native_pid_gpu",
        lambda: pid_gpu_check(Path(required("MEMBIND_RUN_ROOT")) / "native-llm.pid", "0"),
    )
    llm_flags = (
        "--enable-prefix-caching",
        "--enable-chunked-prefill",
    )
    common_llm_options = {
        "--served-model-name": required("MEMBIND_LLM_MODEL_NAME"),
        "--dtype": "auto",
        "--max-model-len": required("MEMBIND_LLM_MAX_MODEL_LEN"),
        "--max-num-seqs": required("MEMBIND_LLM_MAX_NUM_SEQS"),
        "--max-num-batched-tokens": required("MEMBIND_LLM_MAX_BATCHED_TOKENS"),
        "--scheduling-policy": "fcfs",
        "--seed": "20260806",
        "--structured-outputs-config": '{"backend":"xgrammar","disable_any_whitespace":true}',
        "--default-chat-template-kwargs": '{"enable_thinking":false}',
        "--hf-overrides": '{"rope_parameters":{"rope_type":"yarn","factor":1.6,"original_max_position_embeddings":40960,"rope_theta":1000000}}',
    }
    checks.capture(
        "native_process_contract",
        lambda: pid_command_check(
            Path(required("MEMBIND_RUN_ROOT")) / "native-llm.pid",
            expected_options={
                **common_llm_options,
                "--port": required("MEMBIND_NATIVE_LLM_PORT"),
                "--gpu-memory-utilization": required(
                    "MEMBIND_NATIVE_LLM_GPU_MEMORY_UTILIZATION"
                ),
            },
            expected_flags=llm_flags,
        ),
    )
    checks.capture(
        "prepare_pid_gpu",
        lambda: pid_gpu_check(Path(required("MEMBIND_RUN_ROOT")) / "prepare-llm.pid", "1"),
    )
    checks.capture(
        "prepare_process_contract",
        lambda: pid_command_check(
            Path(required("MEMBIND_RUN_ROOT")) / "prepare-llm.pid",
            expected_options={
                **common_llm_options,
                "--port": required("MEMBIND_PREPARE_LLM_PORT"),
                "--gpu-memory-utilization": required(
                    "MEMBIND_PREPARE_LLM_GPU_MEMORY_UTILIZATION"
                ),
            },
            expected_flags=llm_flags,
        ),
    )
    checks.capture(
        "embedding_pid_gpu",
        lambda: pid_gpu_check(Path(required("MEMBIND_RUN_ROOT")) / "embedding.pid", "1"),
    )
    checks.capture(
        "embedding_process_contract",
        lambda: pid_command_check(
            Path(required("MEMBIND_RUN_ROOT")) / "embedding.pid",
            expected_options={
                "--runner": "pooling",
                "--served-model-name": required("MEMBIND_EMBED_MODEL_NAME"),
                "--dtype": "bfloat16",
                "--port": required("MEMBIND_EMBED_PORT"),
                "--max-model-len": required("MEMBIND_EMBED_MAX_MODEL_LEN"),
                "--max-num-seqs": required("MEMBIND_EMBED_MAX_NUM_SEQS"),
                "--max-num-batched-tokens": required("MEMBIND_EMBED_MAX_BATCHED_TOKENS"),
                "--gpu-memory-utilization": required(
                    "MEMBIND_EMBED_GPU_MEMORY_UTILIZATION"
                ),
            },
            expected_flags=("--enable-chunked-prefill",),
        ),
    )
    log_root = Path(required("MEMBIND_LOG_ROOT"))
    checks.capture(
        "native_kv_capacity",
        lambda: log_capacity(
            log_root / "construction/native-qwen3-8b-awq.log",
            "starting native qwen3-8b-awq",
            int(required("MEMBIND_LLM_MAX_MODEL_LEN")),
        ),
    )
    checks.capture(
        "prepare_kv_capacity",
        lambda: log_capacity(
            log_root / "construction/prepare-qwen3-8b-awq.log",
            "starting prepare qwen3-8b-awq",
            int(required("MEMBIND_LLM_MAX_MODEL_LEN")),
        ),
    )
    checks.capture(
        "embedding_kv_capacity",
        lambda: log_capacity(
            log_root / "embedding/qwen3-embedding-0.6b.log",
            "starting qwen3-embedding-0.6b",
            int(required("MEMBIND_EMBED_MAX_MODEL_LEN")),
        ),
    )
    checks.capture("neo4j_read_only_canary", neo4j_check)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("static", "startup", "live"), default="static")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    started = time.time()
    checks = Checks()
    run_static(checks, verify_model_content=args.mode != "static")
    if args.mode == "startup":
        checks.capture("startup_availability", startup_availability_check)
    elif args.mode == "live":
        run_live(checks, args.timeout)
    result = {
        "schema_version": "membind.8b-dual.preflight.v1",
        "profile_id": os.environ.get("MEMBIND_PROFILE_ID"),
        "mode": args.mode,
        "ok": checks.ok,
        "started_unix": started,
        "elapsed_seconds": time.time() - started,
        "checks": checks.rows,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.output)
    print(rendered, end="")
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
