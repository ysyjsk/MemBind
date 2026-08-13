"""Content-addressed provenance for Judge/Evaluator offline infrastructure."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from evaluation.backends.openai_compatible import canonical_json_sha256


LONGMEMEVAL_COMMIT = "9e0b455f4ef0e2ab8f2e582289761153549043fc"
LONGMEMEVAL_SOURCE_SHA256 = (
    "ecce9c4c79dc89d99534ac17b383a5cbb5b9f0c69ee98adaf0684742e3d95251"
)
LONGMEMEVAL_BLOB_SHA = "4732f3772b04a2b9069121ade304e6320494abc2"
TIMEM_COMMIT = "6d279a5f5d40ee229e1995df15c182cb2062c71c"
TIMEM_SOURCE_SHA256 = (
    "11cf1a281fd217fc65ff9681ff64f7d55f61c5f7cbec3136f5a8a928de99233c"
)
TIMEM_BLOB_SHA = "5cf4cd4c45a0c8cf1ba18dd50b4346516e15bfa9"
LONGMEMEVAL_VENDOR_AST_SHA256 = (
    "61836bc870cde12ca14cfae10d91f508eec3de6ed1f0d689fde37937083aa2a9"
)
LONGMEMEVAL_LICENSE_SHA256 = (
    "d3c4b9aa54759df6ded337978a6f3b55b75615e5e4525c3b82d7e2627d4b9732"
)

_LOCAL_FILES = {
    "vendor": "src/evaluation/vendor/longmemeval_evaluate_qa.py",
    "adapter": "src/evaluation/benchmarks/longmemeval.py",
    "backend": "src/evaluation/backends/openai_compatible.py",
    "backend_contract": "src/evaluation/backends/base.py",
    "registry": "src/evaluation/registry.py",
    "schemas": "src/evaluation/schemas.py",
    "longmemeval_license": "src/evaluation/vendor/LONGMEMEVAL_LICENSE",
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _function_ast_sha256(path: Path, function_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        raise ValueError("vendored rubric function identity is invalid")
    canonical = ast.dump(functions[0], include_attributes=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_vendor_module_scope(path: Path, function_name: str) -> None:
    """Reject executable additions outside the one explicitly vendored function."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed: list[ast.stmt] = []
    for index, node in enumerate(tree.body):
        if (
            index == 0
            and isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            allowed.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == function_name:
            allowed.append(node)
    if len(allowed) != len(tree.body):
        raise ValueError("vendored rubric module scope contains extra top-level code")


def _local_config() -> dict[str, Any]:
    return {
        "model": "qwen3-32b-fp8",
        "transport": "OpenAI-compatible /chat/completions",
        "temperature": 0,
        "max_tokens": 10,
        "n": 1,
        "effective_enable_thinking": False,
        "thinking_control_options": ["client_request", "server_side"],
        "sdk_hidden_retries": 0,
        "retryable_failures": ["timeout", "connection", "http_429", "http_5xx"],
        "live_requests_performed": False,
        "scientific_surface": "FUTURE_CONFIRMATION_INFRASTRUCTURE_ONLY",
    }


def build_judge_upstream_manifest(validation_root: Path) -> dict[str, Any]:
    """Bind upstream semantics, engineering reference, and local implementation."""

    root = Path(validation_root).resolve(strict=True)
    paths = {name: root / relative for name, relative in _LOCAL_FILES.items()}
    for path in paths.values():
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            raise ValueError("judge provenance source missing")
    _validate_vendor_module_scope(paths["vendor"], "get_anscheck_prompt")
    if (
        _function_ast_sha256(paths["vendor"], "get_anscheck_prompt")
        != LONGMEMEVAL_VENDOR_AST_SHA256
    ):
        raise ValueError("vendored LongMemEval rubric differs from pinned source scope")
    if _sha256_file(paths["longmemeval_license"]) != LONGMEMEVAL_LICENSE_SHA256:
        raise ValueError("vendored LongMemEval license notice differs from pinned source")
    config = _local_config()
    manifest = {
        "schema_version": "membind.judge-upstream-manifest.v1",
        "status": "offline_implementation",
        "scientific_surface": "FUTURE_CONFIRMATION_INFRASTRUCTURE_ONLY",
        "upstreams": {
            "longmemeval": {
                "repository": "xiaowu0162/LongMemEval",
                "commit_sha": LONGMEMEVAL_COMMIT,
                "source_path": "src/evaluation/evaluate_qa.py",
                "source_git_blob_sha": LONGMEMEVAL_BLOB_SHA,
                "source_sha256": LONGMEMEVAL_SOURCE_SHA256,
                "local_vendor_path": "src/evaluation/vendor/longmemeval_evaluate_qa.py",
                "local_vendor_sha256": _sha256_file(paths["vendor"]),
                "vendor_scope": "get_anscheck_prompt only",
                "vendor_scope_ast_sha256": LONGMEMEVAL_VENDOR_AST_SHA256,
                "vendor_equivalence": "python_ast_equivalent_to_pinned_source_scope",
                "full_upstream_file_vendored": False,
                "license": "MIT",
                "license_notice_path": _LOCAL_FILES["longmemeval_license"],
                "license_notice_sha256": LONGMEMEVAL_LICENSE_SHA256,
            },
            "timem": {
                "repository": "TiMEM-AI/TiMEM",
                "commit_sha": TIMEM_COMMIT,
                "source_path": "experiments/datasets/longmemeval_s/03_evaluation.py",
                "source_git_blob_sha": TIMEM_BLOB_SHA,
                "source_sha256": TIMEM_SOURCE_SHA256,
                "usage": "engineering reference only; source not vendored",
                "source_vendored": False,
            },
        },
        "provenance_roles": {
            "rubric_semantics": "LongMemEval official",
            "adapter_pattern": "TiMEM engineering reference",
            "judge_backend": "MemBind local Qwen3 adapter",
        },
        "local": {
            "implementation_files": {
                name: {"path": relative, "sha256": _sha256_file(paths[name])}
                for name, relative in _LOCAL_FILES.items()
            },
            "offline_default_request_policy": config,
            "offline_default_request_policy_hash": canonical_json_sha256(config),
            "runtime_backend_config_hash": None,
        },
        "network_evidence": {
            "real_judge_requests_performed": False,
            "real_external_requests_performed_by_tests": False,
        },
    }
    manifest["payload_sha256"] = canonical_json_sha256(manifest)
    return manifest


def canonical_manifest_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        + b"\n"
    )


def validate_judge_upstream_manifest(
    value: dict[str, Any], validation_root: Path
) -> dict[str, Any]:
    """Fail closed unless pins, payload, and local files all match exactly."""

    if not isinstance(value, dict):
        raise ValueError("judge manifest must be an object")
    longmem = value.get("upstreams", {}).get("longmemeval", {})
    timem = value.get("upstreams", {}).get("timem", {})
    roles = value.get("provenance_roles", {})
    local = value.get("local", {})
    config = _local_config()
    observed_seal = value.get("payload_sha256")
    candidate = dict(value)
    candidate.pop("payload_sha256", None)
    exact = (
        value.get("schema_version") == "membind.judge-upstream-manifest.v1"
        and value.get("status") == "offline_implementation"
        and value.get("scientific_surface")
        == "FUTURE_CONFIRMATION_INFRASTRUCTURE_ONLY"
        and longmem.get("repository") == "xiaowu0162/LongMemEval"
        and longmem.get("commit_sha") == LONGMEMEVAL_COMMIT
        and longmem.get("source_git_blob_sha") == LONGMEMEVAL_BLOB_SHA
        and longmem.get("source_sha256") == LONGMEMEVAL_SOURCE_SHA256
        and longmem.get("source_path") == "src/evaluation/evaluate_qa.py"
        and longmem.get("local_vendor_path") == _LOCAL_FILES["vendor"]
        and longmem.get("vendor_scope") == "get_anscheck_prompt only"
        and longmem.get("vendor_scope_ast_sha256") == LONGMEMEVAL_VENDOR_AST_SHA256
        and longmem.get("vendor_equivalence")
        == "python_ast_equivalent_to_pinned_source_scope"
        and longmem.get("full_upstream_file_vendored") is False
        and longmem.get("license") == "MIT"
        and longmem.get("license_notice_path") == _LOCAL_FILES["longmemeval_license"]
        and longmem.get("license_notice_sha256") == LONGMEMEVAL_LICENSE_SHA256
        and timem.get("repository") == "TiMEM-AI/TiMEM"
        and timem.get("commit_sha") == TIMEM_COMMIT
        and timem.get("source_git_blob_sha") == TIMEM_BLOB_SHA
        and timem.get("source_sha256") == TIMEM_SOURCE_SHA256
        and timem.get("source_path")
        == "experiments/datasets/longmemeval_s/03_evaluation.py"
        and timem.get("usage") == "engineering reference only; source not vendored"
        and timem.get("source_vendored") is False
        and roles.get("rubric_semantics") == "LongMemEval official"
        and roles.get("adapter_pattern") == "TiMEM engineering reference"
        and roles.get("judge_backend") == "MemBind local Qwen3 adapter"
        and local.get("offline_default_request_policy") == config
        and local.get("offline_default_request_policy_hash")
        == canonical_json_sha256(config)
        and local.get("runtime_backend_config_hash") is None
        and value.get("network_evidence", {}).get("real_judge_requests_performed")
        is False
        and value.get("network_evidence", {}).get(
            "real_external_requests_performed_by_tests"
        )
        is False
        and observed_seal == canonical_json_sha256(candidate)
    )
    if exact:
        root = Path(validation_root).resolve(strict=True)
        # The payload seal catches accidental byte drift. Exact regeneration
        # additionally rejects a malicious or mistaken re-seal of altered
        # paths, source identities, effective policy, or local file hashes.
        exact = value == build_judge_upstream_manifest(root)
    if not exact:
        raise ValueError("judge upstream manifest validation failed")
    return value


def write_judge_upstream_manifest(
    path: Path, validation_root: Path
) -> dict[str, Any]:
    """Write one canonical manifest exclusively; never overwrite evidence."""

    manifest = build_judge_upstream_manifest(validation_root)
    validate_judge_upstream_manifest(manifest, validation_root)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_manifest_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    return manifest
