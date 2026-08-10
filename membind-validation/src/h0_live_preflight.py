"""State-gated, read-only readiness checks for a live H0 construction service.

The probe performs no generation. It records only sanitized status, identity,
length, and content-hash evidence, and it never retries a failed connection.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx

from h0_runtime import (
    H0CandidateConfig,
    H0CheckpointStore,
    H0InfrastructureError,
    H0ManifestError,
    authorize_h0_live_entry,
    canonical_json_bytes,
    canonical_json_sha256,
    load_h0_registry,
)


PROTOCOL_VERSION = "current-validation-v1.3"
EXPECTED_VLLM_VERSION = "0.26.0"
EXPECTED_SERVED_MODEL_ID = "qwen3-32b-fp8"
EXPECTED_CONTEXT_LIMIT = 40960
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_INDEX_SCHEMA_VERSION = "membind.h0.offline-artifacts.v2"
_ARTIFACT_SET_ID = "v1_3_harness_r6"
_EXECUTION_HARNESS_REVISION = 6
_ARTIFACT_SET_REL = f"artifacts/h0_manifest_sets/{_ARTIFACT_SET_ID}"
_RESOLVED_INDEX_REL = (
    f"{_ARTIFACT_SET_REL}/resolved_manifest_index_v1_3_harness_r6.json"
)
_SHARED_ARTIFACT_NAMES = {
    "prompt_bundle",
    "schema_bundle",
    "semantic_guardrail",
    "http_retry",
    "vllm_launch",
    "execution_source_bundle",
}
_RESOLVED_MANIFEST_NAMES = {"shared_base", "Q1", "Q2", "Q3"}
_RESOLVED_BINDING_NAMES = {
    "resolved_client_implementation_sha256",
    "prompt_bundle_sha256",
    "http_pool_and_timeout_config_sha256",
    "retry_implementation_sha256",
    "upstream_schema_sha256",
    "effective_schema_sha256",
    "json_object_injected_schema_sha256",
    "semantic_guardrail_manifest_sha256",
    "vllm_launch_manifest_sha256",
}
_BINDING_TO_SHARED_ARTIFACT = {
    "prompt_bundle_sha256": "prompt_bundle",
    "http_pool_and_timeout_config_sha256": "http_retry",
    "retry_implementation_sha256": "http_retry",
    "upstream_schema_sha256": "schema_bundle",
    "effective_schema_sha256": "schema_bundle",
    "json_object_injected_schema_sha256": "schema_bundle",
    "semantic_guardrail_manifest_sha256": "semantic_guardrail",
    "vllm_launch_manifest_sha256": "vllm_launch",
}
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


@dataclass(frozen=True)
class H0AuthorizedRuntimeDefinition:
    """Safe, fully bound inputs needed by H0 client and graph factories."""

    identity: dict[str, Any]
    candidate: H0CandidateConfig
    semantic_guardrail: dict[str, Any]
    semantic_guardrail_path: str
    semantic_guardrail_sha256: str
    embedding_namespace: dict[str, Any]
    embedding_manifest_path: str
    embedding_manifest_sha256: str
    resolved_artifacts: dict[str, dict[str, str]]
    definition_sha256: str


class H0ReadinessCheckpointSink:
    """Persist each readiness event before a live preflight can continue."""

    def __init__(self, store: H0CheckpointStore) -> None:
        self.store = store
        self.persisted: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        check = str(event.get("check") or "unknown")
        ordinal = len(self.store.index.get("segments", []))
        persisted = self.store.record_segment(
            "readiness_check",
            f"{ordinal:03d}-{check}",
            event,
        )
        self.persisted.append(persisted)
        if event.get("failure_code") in {
            "vllm_unreachable",
            "vllm_service_unavailable",
        }:
            self.store.mark_infrastructure_interruption("vllm_unreachable")


def _readiness_urls(base_url: str) -> tuple[str, str, str, str]:
    parsed = urlsplit(str(base_url).strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise H0ManifestError("invalid H0 construction base URL")
    path = parsed.path.rstrip("/")
    if path != "/v1":
        raise H0ManifestError("H0 construction base URL must end with /v1")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    canonical_base = f"{origin}/v1/"
    return canonical_base, f"{origin}/version", f"{origin}/v1/models", f"{origin}/health"


def _require_sha256(value: Any, field: str) -> str:
    text = str(value or "")
    if _SHA256_RE.fullmatch(text) is None:
        raise H0ManifestError(f"invalid H0 authorization hash: {field}")
    return text


def _authorized_artifact_path(root: Path, value: Any, field: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip():
        raise H0ManifestError(f"missing authorized artifact path: {field}")
    relative = Path(value)
    if relative.is_absolute():
        raise H0ManifestError(f"authorized artifact path must be relative: {field}")
    root = root.resolve()
    path = (root / relative).resolve()
    try:
        normalized = path.relative_to(root).as_posix()
    except ValueError:
        raise H0ManifestError(f"authorized artifact path escapes root: {field}") from None
    if normalized != relative.as_posix() or not path.is_file():
        raise H0ManifestError(f"authorized artifact path is missing or noncanonical: {field}")
    return path, normalized


def _load_bound_json(
    root: Path,
    authorization: Mapping[str, Any],
    *,
    path_field: str,
    hash_field: str,
) -> tuple[dict[str, Any], str, str]:
    path, relative = _authorized_artifact_path(root, authorization.get(path_field), path_field)
    expected = _require_sha256(authorization.get(hash_field), hash_field)
    try:
        encoded = path.read_bytes()
    except OSError:
        raise H0ManifestError(
            f"authorized artifact cannot be read: {path_field}"
        ) from None
    actual = hashlib.sha256(encoded).hexdigest()
    if actual != expected:
        raise H0ManifestError(f"authorized artifact hash mismatch: {hash_field}")
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise H0ManifestError(f"authorized artifact is invalid JSON: {path_field}") from None
    if not isinstance(value, dict):
        raise H0ManifestError(f"authorized artifact is not an object: {path_field}")
    if encoded != canonical_json_bytes(value):
        raise H0ManifestError(
            f"authorized artifact is not canonical JSON: {path_field}"
        )
    _assert_safe_artifact(value, location=path_field)
    return value, relative, actual


def _assert_safe_artifact(value: Any, *, location: str) -> None:
    """Reject credential and raw-model material before using a bound artifact."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in _FORBIDDEN_ARTIFACT_KEYS:
                raise H0ManifestError(f"unsafe authorized artifact field: {location}.{key}")
            _assert_safe_artifact(child, location=f"{location}.{key}")
        return
    if isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _assert_safe_artifact(child, location=f"{location}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if "bearer " in lowered or ".env" in lowered or "gpt55_temporary" in lowered:
            raise H0ManifestError(f"unsafe authorized artifact value: {location}")


def _validate_reference(root: Path, raw: Any, *, label: str) -> dict[str, str]:
    if not isinstance(raw, Mapping) or set(raw) != {"path", "sha256"}:
        raise H0ManifestError(f"invalid H0 artifact reference: {label}")
    path, relative = _authorized_artifact_path(root, raw.get("path"), f"{label}.path")
    digest = _require_sha256(raw.get("sha256"), f"{label}.sha256")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise H0ManifestError(f"H0 artifact reference hash mismatch: {label}")
    return {"path": relative, "sha256": digest}


def _validate_current_index_header(index: Mapping[str, Any]) -> None:
    if index.get("schema_version") != _INDEX_SCHEMA_VERSION:
        raise H0ManifestError("resolved manifest index schema is invalid")
    if index.get("protocol_version") != PROTOCOL_VERSION:
        raise H0ManifestError("resolved manifest index protocol is invalid")
    if index.get("artifact_set_id") != _ARTIFACT_SET_ID:
        raise H0ManifestError("resolved manifest index artifact set is invalid")
    if index.get("execution_harness_revision") != _EXECUTION_HARNESS_REVISION:
        raise H0ManifestError("resolved manifest index harness revision is invalid")
    flags_exact = (
        index.get("status") == "offline_resolved_not_live_authorized"
        and index.get("live_h0_candidate_authorized") is False
        and index.get("unresolved_fields") == []
        and index.get("source_specs_immutable") is True
        and index.get("secrets_persisted") is False
    )
    if not flags_exact:
        raise H0ManifestError("resolved manifest index flags are invalid")


def _validated_current_index_references(
    root: Path, index: Mapping[str, Any]
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    shared_index = index.get("shared_artifacts")
    if not isinstance(shared_index, Mapping) or set(shared_index) != _SHARED_ARTIFACT_NAMES:
        raise H0ManifestError("resolved manifest index shared artifacts are incomplete")
    indexed_shared: dict[str, dict[str, str]] = {}
    for name in sorted(_SHARED_ARTIFACT_NAMES):
        reference = _validate_reference(
            root, shared_index[name], label=f"shared_artifacts.{name}"
        )
        expected = (
            f"{_ARTIFACT_SET_REL}/manifests/{name}_v1_3."
            f"{reference['sha256']}.json"
        )
        if reference["path"] != expected:
            raise H0ManifestError(
                f"resolved shared artifact namespace is invalid: {name}"
            )
        indexed_shared[name] = reference

    resolved_index = index.get("resolved_manifests")
    if (
        not isinstance(resolved_index, Mapping)
        or set(resolved_index) != _RESOLVED_MANIFEST_NAMES
    ):
        raise H0ManifestError("resolved manifest index candidate set is incomplete")
    indexed_resolved: dict[str, dict[str, str]] = {}
    for name in sorted(_RESOLVED_MANIFEST_NAMES):
        reference = _validate_reference(
            root, resolved_index[name], label=f"resolved_manifests.{name}"
        )
        expected = (
            f"{_ARTIFACT_SET_REL}/resolved_candidates/{name}."
            f"{reference['sha256']}.json"
        )
        if reference["path"] != expected:
            raise H0ManifestError(
                f"resolved candidate namespace is invalid: {name}"
            )
        indexed_resolved[name] = reference
    return indexed_shared, indexed_resolved


def load_authorized_h0_runtime_identity(
    authorization: Mapping[str, Any],
    *,
    root: str | Path,
) -> dict[str, Any]:
    """Resolve the construction identity through three cross-hashed artifacts."""

    root = Path(root).resolve()
    if authorization.get("resolved_manifest_index_path") != _RESOLVED_INDEX_REL:
        raise H0ManifestError("resolved manifest index namespace is invalid")
    index, index_path, index_sha = _load_bound_json(
        root,
        authorization,
        path_field="resolved_manifest_index_path",
        hash_field="resolved_manifest_index_sha256",
    )
    _validate_current_index_header(index)
    candidate_id = str(authorization.get("candidate_id") or "")
    phase = str(authorization.get("phase") or "")
    if candidate_id not in {"Q1", "Q2", "Q3"} or phase not in {"H0-A", "H0-B", "H0-C"}:
        raise H0ManifestError("authorized H0 candidate or phase is invalid")
    authorized_candidate_sha = _require_sha256(
        authorization.get("resolved_candidate_manifest_sha256"),
        "resolved_candidate_manifest_sha256",
    )
    expected_candidate_path = (
        f"{_ARTIFACT_SET_REL}/resolved_candidates/{candidate_id}."
        f"{authorized_candidate_sha}.json"
    )
    if authorization.get("resolved_candidate_manifest_path") != expected_candidate_path:
        raise H0ManifestError("resolved candidate namespace is invalid")
    authorized_shared_sha = _require_sha256(
        authorization.get("resolved_shared_base_manifest_sha256"),
        "resolved_shared_base_manifest_sha256",
    )
    expected_shared_path = (
        f"{_ARTIFACT_SET_REL}/resolved_candidates/shared_base."
        f"{authorized_shared_sha}.json"
    )
    if authorization.get("resolved_shared_base_manifest_path") != expected_shared_path:
        raise H0ManifestError("resolved shared-base namespace is invalid")

    candidate, candidate_path, candidate_sha = _load_bound_json(
        root,
        authorization,
        path_field="resolved_candidate_manifest_path",
        hash_field="resolved_candidate_manifest_sha256",
    )
    shared, shared_path, shared_sha = _load_bound_json(
        root,
        authorization,
        path_field="resolved_shared_base_manifest_path",
        hash_field="resolved_shared_base_manifest_sha256",
    )
    for artifact in (index, candidate, shared):
        if artifact.get("protocol_version") != PROTOCOL_VERSION:
            raise H0ManifestError("resolved artifact protocol mismatch")
    if candidate.get("schema_version") != "membind.h0.resolved-candidate.v1":
        raise H0ManifestError("resolved candidate schema is invalid")
    if shared.get("schema_version") != "membind.h0.resolved-shared-host-base.v1":
        raise H0ManifestError("resolved shared-base schema is invalid")
    if candidate.get("status") != "offline_resolved_not_live_authorized":
        raise H0ManifestError("resolved candidate status is invalid")
    if shared.get("status") != "offline_resolved_not_live_authorized":
        raise H0ManifestError("resolved shared-base status is invalid")
    if candidate.get("candidate_id") != candidate_id:
        raise H0ManifestError("resolved candidate ID mismatch")
    if candidate.get("live_eligible") is not False or shared.get("live_eligible") is not False:
        raise H0ManifestError("offline resolved wrappers must remain live-ineligible")
    if candidate.get("resolved_shared_base_sha256") != shared_sha:
        raise H0ManifestError("candidate/shared-base cross-binding mismatch")
    _, resolved = _validated_current_index_references(root, index)
    expected_entries = {
        candidate_id: {"path": candidate_path, "sha256": candidate_sha},
        "shared_base": {"path": shared_path, "sha256": shared_sha},
    }
    for name, expected in expected_entries.items():
        if resolved.get(name) != expected:
            raise H0ManifestError(f"resolved manifest index cross-binding mismatch: {name}")
    source_base = shared.get("source_base")
    construction = source_base.get("construction") if isinstance(source_base, dict) else None
    if not isinstance(construction, dict):
        raise H0ManifestError("resolved shared base has no construction identity")
    identity = {
        "candidate_id": candidate_id,
        "phase": phase,
        "artifact_set_id": _ARTIFACT_SET_ID,
        "execution_harness_revision": _EXECUTION_HARNESS_REVISION,
        "resolved_manifest_index_sha256": index_sha,
        "resolved_candidate_manifest_sha256": candidate_sha,
        "resolved_shared_base_manifest_sha256": shared_sha,
        "base_url": construction.get("base_url"),
        "served_model_id": construction.get("served_model_id"),
        "vllm_version": construction.get("vllm_version"),
        "context_limit": construction.get("context_limit"),
    }
    return _validate_resolved_identity(
        authorization,
        identity,
        candidate_id=candidate_id,
        phase=phase,
    )


def _load_json_reference(
    root: Path,
    reference: Mapping[str, Any],
    *,
    label: str,
    canonical: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    validated = _validate_reference(root, reference, label=label)
    path = root / validated["path"]
    try:
        encoded = path.read_bytes()
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise H0ManifestError(f"invalid bound JSON artifact: {label}") from None
    if not isinstance(value, dict):
        raise H0ManifestError(f"bound JSON artifact is not an object: {label}")
    _assert_safe_artifact(value, location=label)
    if canonical and encoded != canonical_json_bytes(value):
        raise H0ManifestError(f"bound JSON artifact is not canonical: {label}")
    return value, validated


def _validated_resolved_bindings(
    *,
    root: Path,
    index: Mapping[str, Any],
    candidate: Mapping[str, Any],
    shared: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    indexed_shared, _ = _validated_current_index_references(root, index)

    candidate_bindings = candidate.get("resolved_shared_artifacts")
    shared_bindings = shared.get("resolved_artifacts")
    if (
        not isinstance(candidate_bindings, Mapping)
        or not isinstance(shared_bindings, Mapping)
        or set(candidate_bindings) != _RESOLVED_BINDING_NAMES
        or set(shared_bindings) != _RESOLVED_BINDING_NAMES
        or dict(candidate_bindings) != dict(shared_bindings)
    ):
        raise H0ManifestError("resolved artifact bindings are incomplete or inconsistent")
    validated = {
        name: _validate_reference(
            root, shared_bindings[name], label=f"resolved_artifacts.{name}"
        )
        for name in sorted(_RESOLVED_BINDING_NAMES)
    }
    for binding_name, shared_name in _BINDING_TO_SHARED_ARTIFACT.items():
        if validated[binding_name] != indexed_shared[shared_name]:
            raise H0ManifestError(
                f"resolved artifact/index cross-binding mismatch: {binding_name}"
            )
    client_binding = validated["resolved_client_implementation_sha256"]
    if client_binding["path"] != "src/h0_runtime.py":
        raise H0ManifestError("resolved H0 client implementation path is invalid")
    candidate_execution_sources = candidate.get("execution_source_bundle")
    shared_execution_sources = shared.get("execution_source_bundle")
    if not (
        isinstance(candidate_execution_sources, Mapping)
        and isinstance(shared_execution_sources, Mapping)
        and dict(candidate_execution_sources) == dict(shared_execution_sources)
    ):
        raise H0ManifestError("resolved execution source binding is inconsistent")
    execution_source_reference = _validate_reference(
        root,
        shared_execution_sources,
        label="execution_source_bundle",
    )
    if execution_source_reference != indexed_shared["execution_source_bundle"]:
        raise H0ManifestError("execution source/index cross-binding mismatch")
    source_manifest, execution_source_reference = _load_json_reference(
        root,
        execution_source_reference,
        label="execution_source_bundle",
        canonical=True,
    )
    from h0_artifacts import build_h0_execution_source_bundle_manifest

    if source_manifest != build_h0_execution_source_bundle_manifest(root):
        raise H0ManifestError("execution source bundle differs from current sources")
    validated["execution_source_bundle_sha256"] = execution_source_reference
    return validated


def _candidate_config(
    candidate_id: str,
    model: str,
    context_limit: int,
    raw: Mapping[str, Any],
) -> H0CandidateConfig:
    if raw.get("seed_policy") != "fixed_20260806":
        raise H0ManifestError("candidate seed policy is invalid")
    top_k_raw = raw.get("top_k")
    min_p_raw = raw.get("min_p")
    if not isinstance(top_k_raw, Mapping) or not isinstance(min_p_raw, Mapping):
        raise H0ManifestError("candidate sampling options are invalid")
    if candidate_id == "Q1":
        sampling_exact = (
            dict(top_k_raw)
            == {"status": "not_sent_by_client_contract", "value": None}
            and dict(min_p_raw)
            == {"status": "not_sent_by_client_contract", "value": None}
        )
    else:
        sampling_exact = (
            dict(top_k_raw)
            == {"status": "must_be_observed_in_request_payload", "value": 20}
            and dict(min_p_raw)
            == {"status": "must_be_observed_in_request_payload", "value": 0}
        )
    if not sampling_exact:
        raise H0ManifestError("candidate sampling option contract is invalid")
    try:
        return H0CandidateConfig(
            candidate_id=candidate_id,
            model=model,
            structured_output_mode=str(raw["structured_output_mode"]),
            temperature=float(raw["temperature"]),
            top_p=float(raw["top_p"]),
            top_k=top_k_raw.get("value"),
            min_p=min_p_raw.get("value"),
            seed=20260806,
            requested_max_tokens=int(raw["requested_max_tokens"]),
            context_limit=int(context_limit),
            safety_margin_tokens=int(raw["safety_margin_tokens"]),
        )
    except (KeyError, TypeError, ValueError):
        raise H0ManifestError("candidate configuration cannot be constructed") from None


def load_authorized_h0_runtime_definition(
    authorization: Mapping[str, Any],
    *,
    root: str | Path,
) -> H0AuthorizedRuntimeDefinition:
    """Load candidate, semantic, and embedding inputs only from bound artifacts."""

    root_path = Path(root).resolve()
    identity = load_authorized_h0_runtime_identity(authorization, root=root_path)
    index, _, _ = _load_bound_json(
        root_path,
        authorization,
        path_field="resolved_manifest_index_path",
        hash_field="resolved_manifest_index_sha256",
    )
    candidate_wrapper, _, _ = _load_bound_json(
        root_path,
        authorization,
        path_field="resolved_candidate_manifest_path",
        hash_field="resolved_candidate_manifest_sha256",
    )
    shared_wrapper, _, _ = _load_bound_json(
        root_path,
        authorization,
        path_field="resolved_shared_base_manifest_path",
        hash_field="resolved_shared_base_manifest_sha256",
    )
    _validate_current_index_header(index)

    registry = load_h0_registry(root_path)
    registered = next(
        item for item in registry.candidates if item.candidate_id == identity["candidate_id"]
    )
    if candidate_wrapper.get("candidate_configuration") != registered.spec:
        raise H0ManifestError("resolved candidate configuration differs from registry")
    if candidate_wrapper.get("source_delta_spec") != {
        "path": registered.path,
        "sha256": registered.sha256,
    }:
        raise H0ManifestError("resolved candidate source binding is invalid")
    if (
        shared_wrapper.get("source_base") != registry.base_spec
        or shared_wrapper.get("source_base_spec")
        != {"path": registry.base_spec_path, "sha256": registry.base_spec_sha256}
        or shared_wrapper.get("unresolved_fields") != []
    ):
        raise H0ManifestError("resolved shared-base source binding is invalid")

    resolved = _validated_resolved_bindings(
        root=root_path,
        index=index,
        candidate=candidate_wrapper,
        shared=shared_wrapper,
    )
    semantic_reference = resolved["semantic_guardrail_manifest_sha256"]
    semantic_guardrail, semantic_reference = _load_json_reference(
        root_path,
        semantic_reference,
        label="semantic_guardrail",
        canonical=True,
    )
    from h0_artifacts import build_h0_semantic_guardrail_manifest

    if semantic_guardrail != build_h0_semantic_guardrail_manifest(root_path):
        raise H0ManifestError("semantic guardrail differs from frozen calibration inputs")

    embedding_raw = registry.base_spec.get("embedding")
    if not isinstance(embedding_raw, Mapping):
        raise H0ManifestError("shared base has no embedding identity binding")
    embedding_reference = {
        "path": embedding_raw.get("manifest"),
        "sha256": embedding_raw.get("manifest_sha256"),
    }
    embedding_manifest, embedding_reference = _load_json_reference(
        root_path,
        embedding_reference,
        label="embedding_manifest",
        canonical=False,
    )
    from embedding_identity import validate_embedding_model_manifest

    try:
        embedding_namespace = validate_embedding_model_manifest(
            embedding_manifest, require_ready=True
        ).to_dict()
    except (TypeError, ValueError):
        raise H0ManifestError("bound embedding identity manifest is invalid") from None

    candidate = _candidate_config(
        identity["candidate_id"],
        identity["served_model_id"],
        identity["context_limit"],
        registered.spec,
    )
    projection = {
        "identity": identity,
        "candidate": asdict(candidate),
        "semantic_guardrail_path": semantic_reference["path"],
        "semantic_guardrail_sha256": semantic_reference["sha256"],
        "semantic_guardrail_projection_sha256": canonical_json_sha256(
            semantic_guardrail
        ),
        "embedding_manifest_path": embedding_reference["path"],
        "embedding_manifest_sha256": embedding_reference["sha256"],
        "embedding_namespace": embedding_namespace,
        "resolved_artifacts": resolved,
    }
    return H0AuthorizedRuntimeDefinition(
        identity=deepcopy(identity),
        candidate=candidate,
        semantic_guardrail=deepcopy(semantic_guardrail),
        semantic_guardrail_path=semantic_reference["path"],
        semantic_guardrail_sha256=semantic_reference["sha256"],
        embedding_namespace=deepcopy(embedding_namespace),
        embedding_manifest_path=embedding_reference["path"],
        embedding_manifest_sha256=embedding_reference["sha256"],
        resolved_artifacts=deepcopy(resolved),
        definition_sha256=canonical_json_sha256(projection),
    )


def _validate_resolved_identity(
    authorization: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    candidate_id: str,
    phase: str,
) -> dict[str, Any]:
    if authorization.get("candidate_id") != candidate_id or authorization.get("phase") != phase:
        raise H0ManifestError("H0 authorization candidate or phase mismatch")
    required_hashes = (
        "resolved_manifest_index_sha256",
        "resolved_candidate_manifest_sha256",
        "resolved_shared_base_manifest_sha256",
    )
    validated: dict[str, Any] = {}
    for field in required_hashes:
        authorized = _require_sha256(authorization.get(field), field)
        observed = _require_sha256(identity.get(field), field)
        if authorized != observed:
            raise H0ManifestError(f"authorized resolved identity mismatch: {field}")
        validated[field] = authorized
    if identity.get("candidate_id") != candidate_id or identity.get("phase") != phase:
        raise H0ManifestError("resolved identity candidate or phase mismatch")
    if identity.get("artifact_set_id") != _ARTIFACT_SET_ID:
        raise H0ManifestError("resolved identity artifact set mismatch")
    if identity.get("execution_harness_revision") != _EXECUTION_HARNESS_REVISION:
        raise H0ManifestError("resolved identity harness revision mismatch")
    base_url = identity.get("base_url")
    if not isinstance(base_url, str):
        raise H0ManifestError("resolved identity has no construction endpoint")
    canonical_base, _, _, _ = _readiness_urls(base_url)
    if identity.get("served_model_id") != EXPECTED_SERVED_MODEL_ID:
        raise H0ManifestError("resolved served model differs from frozen protocol")
    if identity.get("vllm_version") != EXPECTED_VLLM_VERSION:
        raise H0ManifestError("resolved vLLM version differs from frozen protocol")
    if identity.get("context_limit") != EXPECTED_CONTEXT_LIMIT:
        raise H0ManifestError("resolved context limit differs from frozen protocol")
    return {
        **validated,
        "candidate_id": candidate_id,
        "phase": phase,
        "artifact_set_id": _ARTIFACT_SET_ID,
        "execution_harness_revision": _EXECUTION_HARNESS_REVISION,
        "base_url": canonical_base,
        "served_model_id": EXPECTED_SERVED_MODEL_ID,
        "vllm_version": EXPECTED_VLLM_VERSION,
        "context_limit": EXPECTED_CONTEXT_LIMIT,
    }


def _event_binding(
    *,
    stage_attempt_id: str,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "stage_attempt_id": stage_attempt_id,
        "candidate_id": identity["candidate_id"],
        "phase": identity["phase"],
        "resolved_manifest_index_sha256": identity[
            "resolved_manifest_index_sha256"
        ],
        "resolved_candidate_manifest_sha256": identity[
            "resolved_candidate_manifest_sha256"
        ],
        "resolved_shared_base_manifest_sha256": identity[
            "resolved_shared_base_manifest_sha256"
        ],
        "construction_endpoint_sha256": hashlib.sha256(
            str(identity["base_url"]).encode("utf-8")
        ).hexdigest(),
    }


def _response_event(
    *,
    check: str,
    path: str,
    response: httpx.Response,
    qualified: bool,
    binding: Mapping[str, Any],
    failure_code: str | None = None,
) -> dict[str, Any]:
    content = bytes(response.content)
    event: dict[str, Any] = {
        "schema_version": "membind.h0.readiness-event.v1",
        "protocol_version": PROTOCOL_VERSION,
        "check": check,
        "method": "GET",
        "path": path,
        "http_status": response.status_code,
        "response_sha256": hashlib.sha256(content).hexdigest(),
        "response_byte_length": len(content),
        "qualified": bool(qualified),
        "candidate_advance_allowed": False,
        **dict(binding),
    }
    if failure_code is not None:
        event["failure_code"] = failure_code
    return event


def _connection_failure_event(
    *, check: str, path: str, binding: Mapping[str, Any], failure_code: str
) -> dict[str, Any]:
    return {
        "schema_version": "membind.h0.readiness-event.v1",
        "protocol_version": PROTOCOL_VERSION,
        "check": check,
        "method": "GET",
        "path": path,
        "http_status": None,
        "response_sha256": None,
        "response_byte_length": 0,
        "qualified": False,
        "failure_code": failure_code,
        "candidate_advance_allowed": False,
        **dict(binding),
    }


async def run_h0_readiness_preflight(
    *,
    state_path: str | Path,
    stage_attempt_id: str,
    candidate_id: str,
    phase: str,
    credential_loader: Callable[[], Mapping[str, Any]],
    resolved_identity_loader: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    authorization_checker: Callable[..., Any] = authorize_h0_live_entry,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    progress_sink: Callable[[dict[str, Any]], Any],
) -> dict[str, Any]:
    """Authorize first, then verify exact vLLM and served-model identities."""

    if not stage_attempt_id.strip():
        raise H0ManifestError("H0 readiness requires a stage attempt ID")
    if not callable(progress_sink):
        raise H0ManifestError("H0 readiness requires a durable progress sink")
    authorization = authorization_checker(
        state_path=state_path,
        candidate_id=candidate_id,
        phase=phase,
    )
    if not isinstance(authorization, Mapping):
        raise H0ManifestError("H0 authorization did not return manifest bindings")
    loaded_identity = resolved_identity_loader(authorization)
    if not isinstance(loaded_identity, Mapping):
        raise H0ManifestError("H0 resolved identity loader returned invalid data")
    identity = _validate_resolved_identity(
        authorization,
        loaded_identity,
        candidate_id=candidate_id,
        phase=phase,
    )
    binding = _event_binding(stage_attempt_id=stage_attempt_id, identity=identity)
    credentials = credential_loader()
    if not isinstance(credentials, Mapping):
        raise H0ManifestError("H0 credential loader must return a mapping")
    base_url = credentials.get("base_url")
    api_key = credentials.get("api_key")
    if not isinstance(base_url, str) or not isinstance(api_key, str) or not api_key:
        raise H0ManifestError("H0 construction endpoint credentials are incomplete")
    credential_base, _, _, _ = _readiness_urls(base_url)
    if credential_base != identity["base_url"]:
        raise H0ManifestError("construction endpoint does not match resolved manifest")
    _, version_url, models_url, health_url = _readiness_urls(identity["base_url"])
    transport = (
        transport_factory()
        if transport_factory is not None
        else httpx.AsyncHTTPTransport(retries=0)
    )
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0)
    safe_events: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        transport=transport,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        observed_version: str | None = None
        observed_model: str | None = None
        for check, url, path in (
            ("vllm_version", version_url, "/version"),
            ("served_model", models_url, "/v1/models"),
            ("health", health_url, "/health"),
        ):
            try:
                response = await client.get(url, headers=headers)
            except httpx.TransportError as exc:
                event = _connection_failure_event(
                    check=check,
                    path=path,
                    binding=binding,
                    failure_code="vllm_unreachable",
                )
                safe_events.append(event)
                progress_sink(dict(event))
                raise H0InfrastructureError(
                    "vllm_unreachable: stop_and_report"
                ) from exc

            if response.status_code != 200:
                infrastructure_status = response.status_code == 429 or 500 <= response.status_code <= 599
                failure_code = (
                    "vllm_service_unavailable"
                    if infrastructure_status
                    else "readiness_http_failure"
                )
                event = _response_event(
                    check=check,
                    path=path,
                    response=response,
                    qualified=False,
                    binding=binding,
                    failure_code=failure_code,
                )
                safe_events.append(event)
                progress_sink(dict(event))
                if infrastructure_status:
                    raise H0InfrastructureError(
                        "vllm_unreachable: stop_and_report"
                    )
                raise H0ManifestError(
                    f"H0 readiness HTTP failure: path={path} status={response.status_code}"
                )
            payload: Any = None
            if check != "health":
                try:
                    payload = response.json()
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    event = _response_event(
                        check=check,
                        path=path,
                        response=response,
                        qualified=False,
                        binding=binding,
                        failure_code="readiness_invalid_json",
                    )
                    safe_events.append(event)
                    progress_sink(dict(event))
                    raise H0ManifestError("H0 readiness response is invalid JSON")

            failure_code: str | None = None
            if check == "vllm_version":
                observed_version = (
                    str(payload.get("version")) if isinstance(payload, dict) else None
                )
                if observed_version != identity["vllm_version"]:
                    failure_code = "vllm_version_mismatch"
            elif check == "served_model":
                data = payload.get("data") if isinstance(payload, dict) else None
                model_ids = [
                    str(item.get("id"))
                    for item in data
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ] if isinstance(data, list) else []
                if identity["served_model_id"] in model_ids:
                    observed_model = str(identity["served_model_id"])
                else:
                    failure_code = "served_model_mismatch"
            event = _response_event(
                check=check,
                path=path,
                response=response,
                qualified=failure_code is None,
                binding=binding,
                failure_code=failure_code,
            )
            safe_events.append(event)
            progress_sink(dict(event))
            if failure_code == "vllm_version_mismatch":
                raise H0ManifestError("vLLM version mismatch")
            if failure_code == "served_model_mismatch":
                raise H0ManifestError("served model mismatch")

    return {
        "schema_version": "membind.h0.readiness-preflight.v1",
        "protocol_version": PROTOCOL_VERSION,
        "candidate_id": candidate_id,
        "phase": phase,
        "status": "ready",
        "stage_attempt_id": stage_attempt_id,
        "vllm_version": observed_version,
        "served_model_id": observed_model,
        "context_limit": identity["context_limit"],
        "checks": safe_events,
        "configured_transport_retries": 0,
        "observed_request_attempt_count": len(safe_events),
        "generation_requests": 0,
        "tokenize_gate": "mandatory_per_finalized_prompt_before_completion",
        "authorized_candidate_execution_ready": True,
        "candidate_advance_allowed": False,
        **binding,
        "secrets_persisted": False,
        "raw_responses_persisted": False,
    }
