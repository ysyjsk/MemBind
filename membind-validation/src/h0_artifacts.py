"""Build Protocol v1.3 H0 manifests from frozen local evidence.

The builders are offline-only: they inspect pinned Python sources, the frozen
calibration view, and existing sanitized runtime evidence.  They never read
``.env`` or contact construction, embedding, Neo4j, or remote SSH services.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import importlib.metadata
import inspect
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import graphiti_core.prompts as graphiti_prompts
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate
from graphiti_core.prompts.dedupe_nodes import NodeResolutions
from graphiti_core.prompts.extract_edges import (
    BatchEdgeTimestamps,
    EdgeTimestamps,
    ExtractedEdges,
)
from graphiti_core.prompts.extract_nodes import ExtractedEntities, SummarizedEntities
from graphiti_core.prompts.summarize_nodes import Summary, SummaryDescription
from graphiti_core.prompts.summarize_sagas import SagaSummary

from h0_runtime import (
    ArtifactBinding,
    H0ManifestError,
    H0Registry,
    canonical_json_bytes,
    canonical_json_sha256,
    load_h0_calibration_corpus,
    load_h0_registry,
    normalize_entity_name,
    prepare_h0_prompt,
    sha256_file,
)


SCHEMA_VERSION = "membind.h0.offline-artifacts.v2"
VERIFICATION_SCHEMA_VERSION = "membind.h0.offline-artifact-verification.v3"
H0_ARTIFACT_SET_ID = "v1_3_harness_r5"
H0_EXECUTION_HARNESS_REVISION = 5
H0_ARTIFACT_SET_REL = f"artifacts/h0_manifest_sets/{H0_ARTIFACT_SET_ID}"
H0_RESOLVED_MANIFEST_INDEX_REL = (
    f"{H0_ARTIFACT_SET_REL}/resolved_manifest_index_v1_3_harness_r5.json"
)
SPLIT_REL = "artifacts/dataset/frozen_split_v1_3.json"
RUNTIME_EVIDENCE_REL = "artifacts/environment/v3_construction_runtime_evidence_20260809.json"

_VLLM_RUNTIME_FIELDS = {
    "default_chat_template_kwargs",
    "dtype",
    "engine",
    "max_model_len",
    "model_root",
    "model_runner",
    "quantization",
    "served_model_name",
    "structured_outputs_config",
    "vllm_version",
}
_STRUCTURED_OUTPUTS_CONFIG = {
    "backend": "auto",
    "disable_additional_properties": False,
    "disable_any_whitespace": False,
    "enable_in_reasoning": False,
    "reasoning_parser": "",
    "reasoning_parser_plugin": "",
}
_PINNED_VLLM_RUNTIME = {
    "default_chat_template_kwargs": {"enable_thinking": False},
    "dtype": "bfloat16",
    "engine": "V1",
    "max_model_len": 40960,
    "model_runner": "V2",
    "quantization": "fp8",
    "served_model_name": "qwen3-32b-fp8",
    "structured_outputs_config": _STRUCTURED_OUTPUTS_CONFIG,
    "vllm_version": "0.26.0",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SHARED_ARTIFACT_NAMES = (
    "prompt_bundle",
    "schema_bundle",
    "semantic_guardrail",
    "http_retry",
    "vllm_launch",
    "execution_source_bundle",
)
_RESOLVED_MANIFEST_NAMES = ("shared_base", "Q1", "Q2", "Q3")
_FORBIDDEN_ARTIFACT_KEYS = {
    "api_key",
    "authorization",
    "env_dump",
    "environment_dump",
    "environ",
    "messages",
    "process_environment",
    "raw_prompt",
    "raw_prompts",
    "raw_response",
    "raw_responses",
}

# Exact local Python import graph reachable from the explicit H0 control plane.
# Keeping this allowlist source-bound makes additions visible in code review and
# prevents a broad filesystem scan from accidentally capturing private lanes.
H0_EXECUTION_SOURCE_PATHS = tuple(
    sorted(
        {
            "src/canonicalize_graph.py",
            "src/current_state_gate.py",
            "src/dataset.py",
            "src/deterministic_search.py",
            "src/embedding_identity.py",
            "src/graphiti_native.py",
            "src/h0_artifacts.py",
            "src/h0_bootstrap.py",
            "src/h0_completion.py",
            "src/h0_control.py",
            "src/h0_credentials.py",
            "src/h0_embedding.py",
            "src/h0_executor.py",
            "src/h0_full_history_completion.py",
            "src/h0_full_history_live.py",
            "src/h0_graphiti_adapter.py",
            "src/h0_harness_recovery.py",
            "src/h0_live_preflight.py",
            "src/h0_live_runner.py",
            "src/h0_neo4j.py",
            "src/h0_phase_runner.py",
            "src/h0_phase_state.py",
            "src/h0_repair_admission.py",
            "src/h0_runtime.py",
            "src/h0_stage_readiness.py",
            "src/h0_state_transition.py",
            "src/instrumentation.py",
            "src/live_outputs.py",
            "src/live_runtime.py",
            "src/retrieval_eval.py",
            "src/structured_output.py",
            "src/tracing.py",
        }
    )
)


class H0ArtifactVerificationError(RuntimeError):
    """Raised when staged H0 files are unsafe, incomplete, or not reproducible."""


def _generator_sha256() -> str:
    return sha256_file(Path(__file__))


def build_h0_prompt_bundle_manifest(root: str | Path) -> dict[str, Any]:
    """Hash every source file in the installed pinned Graphiti prompt package."""

    del root
    prompt_root = Path(graphiti_prompts.__file__).resolve().parent
    files = [
        {
            "path": f"graphiti_core/prompts/{path.relative_to(prompt_root).as_posix()}",
            "sha256": sha256_file(path),
        }
        for path in sorted(prompt_root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    return {
        "schema_version": "membind.h0.prompt-bundle.v1",
        "protocol_version": "current-validation-v1.3",
        "graphiti_version": importlib.metadata.version("graphiti-core"),
        "graphiti_commit": "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
        "file_count": len(files),
        "files": files,
        "bundle_projection_sha256": canonical_json_sha256(files),
        "generator_source_sha256": _generator_sha256(),
    }


_REACHABLE_RESPONSE_MODELS = (
    ExtractedEntities,
    ExtractedEdges,
    NodeResolutions,
    SummarizedEntities,
    EdgeDuplicate,
    EdgeTimestamps,
    BatchEdgeTimestamps,
    Summary,
    SummaryDescription,
    SagaSummary,
)


def _contains_explicit_single_zero(schema: Any) -> bool:
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict) and "episode_indices" in properties:
            field = properties["episode_indices"]
            required = schema.get("required")
            return (
                isinstance(field, dict)
                and field.get("minItems") == 1
                and field.get("maxItems") == 1
                and field.get("items") == {"type": "integer", "const": 0}
                and isinstance(required, list)
                and "episode_indices" in required
                and "default" not in field
            )
        return any(_contains_explicit_single_zero(child) for child in schema.values())
    if isinstance(schema, list):
        return any(_contains_explicit_single_zero(child) for child in schema)
    return False


def build_h0_schema_bundle_manifest(root: str | Path) -> dict[str, Any]:
    """Bind upstream, effective, and Q3-injected schemas for every call model."""

    del root
    models: dict[str, dict[str, Any]] = {}
    for response_model in _REACHABLE_RESPONSE_MODELS:
        prepared = prepare_h0_prompt(
            [SimpleNamespace(role="user", content="schema-bundle-placeholder")],
            response_model,
            "json_object",
        )
        models[response_model.__name__] = {
            "qualified_name": (
                f"{response_model.__module__}.{response_model.__qualname__}"
            ),
            "upstream_schema_sha256": prepared.schema.upstream_schema_sha256,
            "effective_schema_sha256": prepared.schema.effective_schema_sha256,
            "json_object_injected_schema_sha256": prepared.injected_schema_sha256,
            "episode_indices_explicit_single_zero": _contains_explicit_single_zero(
                prepared.schema.effective_schema
            ),
        }
    return {
        "schema_version": "membind.h0.schema-bundle.v1",
        "protocol_version": "current-validation-v1.3",
        "schema_policy": "episode_indices_explicit_exactly_single_zero",
        "model_count": len(models),
        "models": models,
        "bundle_projection_sha256": canonical_json_sha256(models),
        "generator_source_sha256": _generator_sha256(),
    }


def build_h0_semantic_guardrail_manifest(root: str | Path) -> dict[str, Any]:
    """Freeze four source-0 nonempty canaries from calibration input hashes only."""

    root = Path(root).resolve()
    split_path = root / SPLIT_REL
    split = json.loads(split_path.read_text(encoding="utf-8"))
    corpus = load_h0_calibration_corpus(split_path, split["source_path"])
    expected: dict[str, dict[str, Any]] = {}
    for question_id in corpus.question_ids:
        episode = corpus.episodes[question_id][0]
        lines = [line.strip() for line in episode.body.splitlines() if line.strip()]
        has_explicit_speaker_role = any(
            line.startswith("[")
            and "] " in line
            and bool(line.split("] ", 1)[1].strip())
            for line in lines
        )
        if not has_explicit_speaker_role:
            raise ValueError(
                "semantic canary requires nonempty rendered input with an explicit "
                f"speaker role: {question_id}:0"
            )
        call_key = f"{question_id}:0:extract_nodes.extract_message"
        expected[call_key] = {
            "source_episode_sha256": episode.source_hash,
            "minimum_entity_count": 1,
            "minimum_distinct_normalized_entity_name_count": 1,
            "audit_basis": "rendered_message_has_explicit_speaker_role",
        }
    calls = list(expected)
    normalization_source = inspect.getsource(normalize_entity_name)
    return {
        "schema_version": "membind.h0.semantic-guardrail.v1",
        "protocol_version": "current-validation-v1.3",
        "data_scope": "calibration_only",
        "split_manifest": SPLIT_REL,
        "split_manifest_sha256": sha256_file(split_path),
        "source_sha256": split["source_sha256"],
        "candidate_outputs_used_to_set_invariants": False,
        "invariants_frozen_before_candidate_execution": True,
        "normalization": "NFKC_strip_collapse_whitespace_casefold_v1",
        "normalization_implementation_sha256": canonical_json_sha256(
            normalization_source
        ),
        "expected_nonempty_call_ids": expected,
        "minimum_entity_count_by_call": {
            call: value["minimum_entity_count"] for call, value in expected.items()
        },
        "minimum_distinct_normalized_entity_name_count_by_call": {
            call: value["minimum_distinct_normalized_entity_name_count"]
            for call, value in expected.items()
        },
        "expected_episode_indices": [0],
        "entity_names_must_be_nonblank": True,
        "duplicate_normalized_entity_names_forbidden": True,
        "cross_call_constant_detection_groups": [calls],
        "forbidden_default_payload_sha256": [
            canonical_json_sha256({"extracted_entities": []})
        ],
        "evaluation_data_used": False,
        "raw_inputs_persisted": False,
        "generator_source_sha256": _generator_sha256(),
    }


def build_h0_http_retry_manifest(root: str | Path) -> dict[str, Any]:
    """Freeze every transport limit and both possible retry layers."""

    root = Path(root).resolve()
    import graphiti_core.llm_client.client as graphiti_client
    import graphiti_core.llm_client.openai_generic_client as generic_client

    return {
        "schema_version": "membind.h0.http-retry.v1",
        "protocol_version": "current-validation-v1.3",
        "openai_version": importlib.metadata.version("openai"),
        "httpx_version": importlib.metadata.version("httpx"),
        "tenacity_version": importlib.metadata.version("tenacity"),
        "openai_sdk_platform": "Linux",
        "openai_sdk_max_retries": 0,
        "http_client_trust_env": False,
        "http_follow_redirects": False,
        "graphiti_public_retry_attempt_limit": 4,
        "candidate_failure_short_circuits_retry": True,
        "candidate_induced_retry_is_qualification_failure": True,
        "tokenize_transport_retries": 0,
        "timeout_seconds": {
            "connect": 5.0,
            "read": 600.0,
            "write": 600.0,
            "pool": 600.0,
        },
        "connection_limits": {
            "max_connections": 1000,
            "max_keepalive_connections": 100,
            "keepalive_expiry_seconds": 5.0,
        },
        "token_counter": {
            "endpoint": "/tokenize",
            "add_special_tokens": False,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        "implementation_sources": {
            relative: sha256_file(root / relative)
            for relative in H0_EXECUTION_SOURCE_PATHS
        } | {
            "graphiti_core/llm_client/client.py": sha256_file(
                Path(graphiti_client.__file__)
            ),
            "graphiti_core/llm_client/openai_generic_client.py": sha256_file(
                Path(generic_client.__file__)
            ),
        },
        "generator_source_sha256": _generator_sha256(),
    }


def build_h0_execution_source_bundle_manifest(root: str | Path) -> dict[str, Any]:
    """Bind every project source reachable from the mainline H0 control path."""

    root_path = Path(root).resolve()
    files: list[dict[str, str]] = []
    for relative in H0_EXECUTION_SOURCE_PATHS:
        path = root_path / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"H0 execution source is missing or symlinked: {relative}")
        files.append({"path": relative, "sha256": sha256_file(path)})
    return {
        "schema_version": "membind.h0.execution-source-bundle.v1",
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": H0_ARTIFACT_SET_ID,
        "execution_harness_revision": H0_EXECUTION_HARNESS_REVISION,
        "binding_policy": "explicit_mainline_h0_transitive_project_sources",
        "file_count": len(files),
        "files": files,
        "bundle_projection_sha256": canonical_json_sha256(files),
        "temporary_gpt_lane_included": False,
        "environment_file_included": False,
    }


def build_h0_vllm_launch_manifest(root: str | Path) -> dict[str, Any]:
    """Repackage only sanitized, already-proven construction launch fields."""

    root = Path(root).resolve()
    evidence_path = root / RUNTIME_EVIDENCE_REL
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    runtime = evidence.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("construction runtime evidence has no runtime object")
    missing = sorted(_VLLM_RUNTIME_FIELDS - set(runtime))
    unexpected = sorted(set(runtime) - _VLLM_RUNTIME_FIELDS)
    if missing or unexpected:
        raise ValueError(
            "unexpected runtime evidence fields: "
            f"missing={missing}, unexpected={unexpected}"
        )
    for name, expected in _PINNED_VLLM_RUNTIME.items():
        if runtime.get(name) != expected:
            raise ValueError(
                f"construction runtime evidence {name} does not match the H0 pin"
            )
    model_root = runtime.get("model_root")
    if not isinstance(model_root, str) or not model_root.strip():
        raise ValueError("construction runtime evidence model_root must be nonempty")
    return {
        "schema_version": "membind.h0.vllm-launch-manifest.v1",
        "protocol_version": "current-validation-v1.3",
        "evidence": {
            "path": RUNTIME_EVIDENCE_REL,
            "sha256": sha256_file(evidence_path),
            "classification": evidence.get("classification"),
        },
        "runtime": deepcopy(runtime),
        "runtime_field_allowlist": sorted(_VLLM_RUNTIME_FIELDS),
        "request_selected_backend": "unobserved",
        "raw_launch_command_persisted": False,
        "secrets_persisted": False,
        "generator_source_sha256": _generator_sha256(),
    }


def resolve_h0_manifests(
    registry: H0Registry,
    bindings: Mapping[str, ArtifactBinding | Mapping[str, str]],
) -> dict[str, Any]:
    return registry.resolve(bindings)


def _local_path_without_symlinks(root: Path, relative: str, *, label: str) -> Path:
    """Resolve one canonical project-relative path without following symlinks."""

    relative_path = Path(relative)
    if (
        not relative
        or relative_path.is_absolute()
        or relative_path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in relative_path.parts)
    ):
        raise H0ArtifactVerificationError(f"noncanonical local path: {label}")
    cursor = root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise H0ArtifactVerificationError(f"symlink forbidden: {label}")
    try:
        cursor.resolve().relative_to(root)
    except ValueError as exc:
        raise H0ArtifactVerificationError(
            f"local path escapes staged root: {label}"
        ) from exc
    return cursor


def _artifact_set_root(root: Path) -> Path:
    return _local_path_without_symlinks(
        root, H0_ARTIFACT_SET_REL, label="current_h0_artifact_set"
    )


def _write_canonical(
    root: Path,
    path: Path,
    value: Mapping[str, Any],
    *,
    label: str,
) -> str:
    encoded = canonical_json_bytes(value)
    digest = canonical_json_sha256(value)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise H0ArtifactVerificationError(
            f"local path escapes staged root: {label}"
        ) from exc
    path = _local_path_without_symlinks(root, relative, label=label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _local_path_without_symlinks(root, relative, label=label)
    if path.exists():
        if path.read_bytes() != encoded:
            raise FileExistsError(f"immutable H0 artifact differs: {path}")
    else:
        path.write_bytes(encoded)
    if sha256_file(path) != digest:
        raise RuntimeError(f"H0 artifact hash mismatch after write: {path}")
    return digest


def _assert_safe_artifact(value: Any, *, location: str) -> None:
    """Reject secret-bearing fields and raw model material recursively."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_ARTIFACT_KEYS:
                raise H0ArtifactVerificationError(
                    f"unsafe H0 artifact field at {location}: {key}"
                )
            _assert_safe_artifact(child, location=f"{location}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _assert_safe_artifact(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if (
            "bearer " in lowered
            or ".env" in lowered
            or "gpt55_temporary" in lowered
        ):
            raise H0ArtifactVerificationError(
                f"unsafe H0 artifact value at {location}"
            )


def _read_generated_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("ascii"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise H0ArtifactVerificationError(
            f"unreadable generated H0 artifact: {label}"
        ) from exc
    if not isinstance(value, dict):
        raise H0ArtifactVerificationError(
            f"generated H0 artifact is not an object: {label}"
        )
    _assert_safe_artifact(value, location=label)
    if encoded != canonical_json_bytes(value):
        raise H0ArtifactVerificationError(
            f"generated H0 artifact is not canonical JSON: {label}"
        )
    return value


def _require_reference(
    root: Path,
    raw: Any,
    *,
    expected_path: str,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(raw, Mapping) or set(raw) != {"path", "sha256"}:
        raise H0ArtifactVerificationError(f"invalid indexed reference: {label}")
    relative = str(raw.get("path") or "")
    digest = str(raw.get("sha256") or "")
    if relative != expected_path or _SHA256_RE.fullmatch(digest) is None:
        raise H0ArtifactVerificationError(f"invalid indexed path/hash: {label}")
    path = _local_path_without_symlinks(root, relative, label=label)
    if not path.is_file():
        raise H0ArtifactVerificationError(f"missing indexed artifact: {label}")
    actual = sha256_file(path)
    if actual != digest:
        raise H0ArtifactVerificationError(
            f"indexed artifact hash mismatch: {label}"
        )
    return path, digest


def _require_source_reference(
    root: Path,
    raw: Any,
    *,
    label: str,
) -> None:
    if not isinstance(raw, Mapping):
        raise H0ArtifactVerificationError(f"invalid frozen source reference: {label}")
    relative = str(raw.get("path") or "")
    digest = str(raw.get("sha256") or "")
    if not relative or _SHA256_RE.fullmatch(digest) is None:
        raise H0ArtifactVerificationError(f"invalid frozen source reference: {label}")
    path = _local_path_without_symlinks(root, relative, label=label)
    if not path.is_file() or sha256_file(path) != digest:
        raise H0ArtifactVerificationError(
            f"frozen source path/hash mismatch: {label}"
        )


def _expected_shared_manifests(root: Path) -> dict[str, dict[str, Any]]:
    return {
        "prompt_bundle": build_h0_prompt_bundle_manifest(root),
        "schema_bundle": build_h0_schema_bundle_manifest(root),
        "semantic_guardrail": build_h0_semantic_guardrail_manifest(root),
        "http_retry": build_h0_http_retry_manifest(root),
        "vllm_launch": build_h0_vllm_launch_manifest(root),
        "execution_source_bundle": build_h0_execution_source_bundle_manifest(root),
    }


def verify_h0_offline_artifacts(root: str | Path) -> dict[str, Any]:
    """Verify a staged offline artifact graph without contacting live services."""

    root = Path(root).resolve()
    h0_root = _artifact_set_root(root)
    index_path = _local_path_without_symlinks(
        root,
        H0_RESOLVED_MANIFEST_INDEX_REL,
        label="resolved_manifest_index_v1_3_harness_r5",
    )
    index = _read_generated_object(
        index_path, label="resolved_manifest_index_v1_3_harness_r5"
    )
    required_index = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": H0_ARTIFACT_SET_ID,
        "execution_harness_revision": H0_EXECUTION_HARNESS_REVISION,
        "status": "offline_resolved_not_live_authorized",
        "live_h0_candidate_authorized": False,
        "unresolved_fields": [],
        "source_specs_immutable": True,
        "secrets_persisted": False,
    }
    for field, expected in required_index.items():
        if index.get(field) != expected:
            raise H0ArtifactVerificationError(
                f"incomplete H0 artifact index field: {field}"
            )
    shared_references = index.get("shared_artifacts")
    resolved_references = index.get("resolved_manifests")
    if not isinstance(shared_references, Mapping) or set(shared_references) != set(
        _SHARED_ARTIFACT_NAMES
    ):
        raise H0ArtifactVerificationError("incomplete shared artifact index")
    if not isinstance(resolved_references, Mapping) or set(resolved_references) != set(
        _RESOLVED_MANIFEST_NAMES
    ):
        raise H0ArtifactVerificationError("incomplete resolved manifest index")

    indexed_paths: set[Path] = {index_path.resolve()}
    shared_objects: dict[str, dict[str, Any]] = {}
    for name in _SHARED_ARTIFACT_NAMES:
        reference = shared_references[name]
        digest = str(reference.get("sha256") if isinstance(reference, Mapping) else "")
        expected_path = (
            f"{H0_ARTIFACT_SET_REL}/manifests/{name}_v1_3.{digest}.json"
        )
        path, _ = _require_reference(
            root,
            reference,
            expected_path=expected_path,
            label=f"shared_artifacts.{name}",
        )
        indexed_paths.add(path)
        shared_objects[name] = _read_generated_object(path, label=name)

    resolved_objects: dict[str, dict[str, Any]] = {}
    for name in _RESOLVED_MANIFEST_NAMES:
        reference = resolved_references[name]
        digest = str(reference.get("sha256") if isinstance(reference, Mapping) else "")
        expected_path = (
            f"{H0_ARTIFACT_SET_REL}/resolved_candidates/{name}.{digest}.json"
        )
        path, _ = _require_reference(
            root,
            reference,
            expected_path=expected_path,
            label=f"resolved_manifests.{name}",
        )
        indexed_paths.add(path)
        resolved_objects[name] = _read_generated_object(path, label=name)

    generated_paths: set[Path] = set()
    for path in h0_root.rglob("*"):
        if path.is_symlink():
            raise H0ArtifactVerificationError(
                "symlink forbidden in current H0 artifact set"
            )
        if path.is_file():
            generated_paths.add(path)
    for path in sorted(generated_paths):
        if path not in indexed_paths:
            _read_generated_object(
                path,
                label=f"unindexed:{path.relative_to(root).as_posix()}",
            )
    if generated_paths != indexed_paths:
        missing = sorted(path.as_posix() for path in indexed_paths - generated_paths)
        extra = sorted(path.as_posix() for path in generated_paths - indexed_paths)
        raise H0ArtifactVerificationError(
            f"incomplete H0 artifact index: missing={missing}, unindexed={extra}"
        )

    expected_shared = _expected_shared_manifests(root)
    for name, expected in expected_shared.items():
        if shared_objects[name] != expected:
            raise H0ArtifactVerificationError(
                f"generated shared manifest differs from frozen inputs: {name}"
            )

    try:
        registry = load_h0_registry(root)
    except (H0ManifestError, OSError, ValueError) as exc:
        raise H0ArtifactVerificationError(
            "immutable source spec verification failed"
        ) from exc
    source_base = registry.base_spec
    _require_source_reference(
        root,
        {
            "path": source_base["construction"]["runtime_evidence"],
            "sha256": source_base["construction"]["runtime_evidence_sha256"],
        },
        label="source_base.construction.runtime_evidence",
    )
    _require_source_reference(
        root,
        {
            "path": source_base["embedding"]["manifest"],
            "sha256": source_base["embedding"]["manifest_sha256"],
        },
        label="source_base.embedding.manifest",
    )
    _require_source_reference(
        root,
        {
            "path": source_base["dataset"]["split_manifest"],
            "sha256": source_base["dataset"]["split_manifest_sha256"],
        },
        label="source_base.dataset.split_manifest",
    )

    expected_bindings = {
        "resolved_client_implementation_sha256": {
            "path": "src/h0_runtime.py",
            "sha256": sha256_file(root / "src/h0_runtime.py"),
        },
        "prompt_bundle_sha256": dict(shared_references["prompt_bundle"]),
        "http_pool_and_timeout_config_sha256": dict(shared_references["http_retry"]),
        "retry_implementation_sha256": dict(shared_references["http_retry"]),
        "upstream_schema_sha256": dict(shared_references["schema_bundle"]),
        "effective_schema_sha256": dict(shared_references["schema_bundle"]),
        "json_object_injected_schema_sha256": dict(
            shared_references["schema_bundle"]
        ),
        "semantic_guardrail_manifest_sha256": dict(
            shared_references["semantic_guardrail"]
        ),
        "vllm_launch_manifest_sha256": dict(shared_references["vllm_launch"]),
    }
    if set(expected_bindings) != set(registry.unresolved_fields):
        raise H0ArtifactVerificationError(
            "resolved binding registry does not contain exactly nine fields"
        )
    shared_wrapper = resolved_objects["shared_base"]
    if (
        shared_wrapper.get("status") != "offline_resolved_not_live_authorized"
        or shared_wrapper.get("live_eligible") is not False
        or shared_wrapper.get("unresolved_fields") != []
        or shared_wrapper.get("source_base_spec")
        != {"path": registry.base_spec_path, "sha256": registry.base_spec_sha256}
        or shared_wrapper.get("source_base") != registry.base_spec
        or shared_wrapper.get("resolved_artifacts") != expected_bindings
        or shared_wrapper.get("execution_source_bundle")
        != dict(shared_references["execution_source_bundle"])
    ):
        raise H0ArtifactVerificationError("resolved shared wrapper is incomplete")
    shared_wrapper_sha256 = canonical_json_sha256(shared_wrapper)
    if shared_wrapper_sha256 != resolved_references["shared_base"]["sha256"]:
        raise H0ArtifactVerificationError("resolved shared wrapper hash mismatch")

    candidates_by_id = {item.candidate_id: item for item in registry.candidates}
    for candidate_id in ("Q1", "Q2", "Q3"):
        candidate = candidates_by_id[candidate_id]
        wrapper = resolved_objects[candidate_id]
        valid = (
            wrapper.get("status") == "offline_resolved_not_live_authorized"
            and wrapper.get("live_eligible") is False
            and wrapper.get("candidate_id") == candidate_id
            and wrapper.get("source_delta_spec")
            == {"path": candidate.path, "sha256": candidate.sha256}
            and wrapper.get("resolved_shared_base_sha256") == shared_wrapper_sha256
            and wrapper.get("resolved_shared_artifacts") == expected_bindings
            and wrapper.get("candidate_configuration") == candidate.spec
            and wrapper.get("execution_source_bundle")
            == dict(shared_references["execution_source_bundle"])
        )
        if not valid:
            raise H0ArtifactVerificationError(
                f"resolved candidate wrapper is incomplete: {candidate_id}"
            )

    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": H0_ARTIFACT_SET_ID,
        "execution_harness_revision": H0_EXECUTION_HARNESS_REVISION,
        "status": "verified_offline_not_live_authorized",
        "index_path": index_path.relative_to(root).as_posix(),
        "index_sha256": sha256_file(index_path),
        "generated_json_file_count": len(generated_paths),
        "binding_count": len(expected_bindings) + 1,
        "resolved_wrapper_count": len(resolved_objects),
        "source_spec_count": 1 + len(registry.candidates),
        "execution_source_count": len(H0_EXECUTION_SOURCE_PATHS),
        "secret_scan_passed": True,
        "live_eligible": False,
    }


def write_h0_offline_artifacts(root: str | Path) -> dict[str, Any]:
    """Write all shared bundles and resolved wrappers, still live-ineligible."""

    root = Path(root).resolve()
    artifact_set_root = _artifact_set_root(root)
    builders = {
        "prompt_bundle": build_h0_prompt_bundle_manifest(root),
        "schema_bundle": build_h0_schema_bundle_manifest(root),
        "semantic_guardrail": build_h0_semantic_guardrail_manifest(root),
        "http_retry": build_h0_http_retry_manifest(root),
        "vllm_launch": build_h0_vllm_launch_manifest(root),
        "execution_source_bundle": build_h0_execution_source_bundle_manifest(root),
    }
    manifest_dir = artifact_set_root / "manifests"
    written: dict[str, dict[str, str]] = {}
    for name, manifest in builders.items():
        manifest_sha256 = canonical_json_sha256(manifest)
        path = manifest_dir / f"{name}_v1_3.{manifest_sha256}.json"
        digest = _write_canonical(
            root, path, manifest, label=f"shared_artifact.{name}"
        )
        written[name] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": digest,
        }
    bindings = {
        "resolved_client_implementation_sha256": ArtifactBinding(
            "src/h0_runtime.py", sha256_file(root / "src/h0_runtime.py")
        ),
        "prompt_bundle_sha256": ArtifactBinding(**written["prompt_bundle"]),
        "http_pool_and_timeout_config_sha256": ArtifactBinding(**written["http_retry"]),
        "retry_implementation_sha256": ArtifactBinding(**written["http_retry"]),
        "upstream_schema_sha256": ArtifactBinding(**written["schema_bundle"]),
        "effective_schema_sha256": ArtifactBinding(**written["schema_bundle"]),
        "json_object_injected_schema_sha256": ArtifactBinding(**written["schema_bundle"]),
        "semantic_guardrail_manifest_sha256": ArtifactBinding(
            **written["semantic_guardrail"]
        ),
        "vllm_launch_manifest_sha256": ArtifactBinding(**written["vllm_launch"]),
    }
    resolved = resolve_h0_manifests(load_h0_registry(root), bindings)
    execution_source_reference = dict(written["execution_source_bundle"])
    shared_manifest = resolved["shared_base"]["manifest"]
    shared_manifest["execution_source_bundle"] = execution_source_reference
    resolved["shared_base"]["sha256"] = canonical_json_sha256(shared_manifest)
    for envelope in resolved["candidates"].values():
        envelope["manifest"]["execution_source_bundle"] = execution_source_reference
        envelope["manifest"]["resolved_shared_base_sha256"] = resolved["shared_base"][
            "sha256"
        ]
        envelope["sha256"] = canonical_json_sha256(envelope["manifest"])
    resolved_dir = artifact_set_root / "resolved_candidates"
    resolved_files: dict[str, dict[str, str]] = {}
    shared = resolved["shared_base"]
    shared_path = resolved_dir / f"shared_base.{shared['sha256']}.json"
    _write_canonical(root, shared_path, shared["manifest"], label="resolved.shared_base")
    resolved_files["shared_base"] = {
        "path": shared_path.relative_to(root).as_posix(),
        "sha256": shared["sha256"],
    }
    for candidate_id, envelope in resolved["candidates"].items():
        path = resolved_dir / f"{candidate_id}.{envelope['sha256']}.json"
        _write_canonical(
            root, path, envelope["manifest"], label=f"resolved.{candidate_id}"
        )
        resolved_files[candidate_id] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": envelope["sha256"],
        }
    index = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": "current-validation-v1.3",
        "artifact_set_id": H0_ARTIFACT_SET_ID,
        "execution_harness_revision": H0_EXECUTION_HARNESS_REVISION,
        "status": "offline_resolved_not_live_authorized",
        "live_h0_candidate_authorized": False,
        "shared_artifacts": written,
        "resolved_manifests": resolved_files,
        "unresolved_fields": [],
        "source_specs_immutable": True,
        "secrets_persisted": False,
    }
    index_path = root / H0_RESOLVED_MANIFEST_INDEX_REL
    index_sha = _write_canonical(
        root, index_path, index, label="resolved_manifest_index_v1_3_harness_r5"
    )
    return {
        "index": index,
        "index_path": index_path.relative_to(root).as_posix(),
        "index_sha256": index_sha,
    }
