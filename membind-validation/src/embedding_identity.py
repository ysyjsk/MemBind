"""Assess whether an embedding endpoint exposes an immutable model identity."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "membind.embedding_identity_probe.v1"
MANIFEST_SCHEMA_VERSION = "membind.embedding_model_fingerprint.v1"
_UNUSABLE_IDENTITIES = {"", "unknown", "unreported", "endpoint-unreported"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_FIELDS = (
    "served_model_id",
    "identity_value",
    "dimension",
    "dtype",
    "pooling",
    "normalization",
    "instruction_policy",
    "input_transform",
)


def assess_endpoint_identity(
    models_payload: dict[str, Any],
    version_payload: dict[str, Any],
    *,
    expected_model: str,
) -> dict[str, Any]:
    models = [
        model
        for model in (models_payload.get("data") or [])
        if isinstance(model, dict) and model.get("id") == expected_model
    ]
    if len(models) != 1:
        raise ValueError(
            "embedding endpoint must expose exactly one expected served model"
        )
    model = models[0]
    revision = model.get("revision") or model.get("model_revision")
    if revision is not None:
        revision = str(revision).strip()
    if revision is not None and (
        revision.casefold() in _UNUSABLE_IDENTITIES
        or revision == expected_model
        or revision.startswith(("http://", "https://"))
    ):
        revision = None

    result = {
        "schema_version": SCHEMA_VERSION,
        "probe_mode": "read_only_endpoint_metadata",
        "served_model_id": str(model.get("id")),
        "reported_model_root": (
            str(model["root"]) if model.get("root") is not None else None
        ),
        "reported_max_model_len": model.get("max_model_len"),
        "vllm_version": version_payload.get("version"),
        "endpoint_reported_revision": revision,
        "rejected_identity_sources": [
            "served_alias",
            "endpoint_url",
            "model_root_path",
            "vllm_version",
            "behavior_probe",
        ],
        "required_identity_sources": [
            "endpoint_reported_revision",
            "operator_supplied_immutable_deployment_fingerprint",
        ],
    }
    if revision is None:
        result.update(
            {
                "status": "blocked_missing_immutable_identity",
                "identity_kind": None,
                "identity_value": None,
                "blocks_v2_live_integration": True,
                "required_operator_action": (
                    "supply a SHA256 deployment fingerprint derived from the immutable "
                    "model/config/tokenizer/weight-index manifest and launch config"
                ),
            }
        )
    else:
        result.update(
            {
                "status": "endpoint_revision_available",
                "identity_kind": "endpoint_revision",
                "identity_value": revision,
                "blocks_v2_live_integration": False,
                "required_operator_action": None,
            }
        )
    assert_safe_identity_artifact(result)
    return result


def assert_safe_identity_artifact(value: Any) -> None:
    forbidden = {"authorization", "api_key", "apikey", "headers"}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).casefold().replace("-", "_") in forbidden:
                    raise ValueError(f"unsafe identity probe field: {key}")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and "bearer " in item.casefold():
            raise ValueError("unsafe credential value in identity probe")

    visit(value)


def build_operator_fingerprint_manifest(
    *,
    operator_fingerprint: str,
    namespace: dict[str, Any],
    field_evidence: dict[str, Any],
    endpoint_observation: dict[str, Any],
    fingerprint_scope: str = "operator_supplied_deployment_directory_tree_sha256",
) -> dict[str, Any]:
    """Build a provenance-bearing deployment manifest without hiding unknowns."""
    from embedding_cache import EmbeddingNamespace

    fingerprint = str(operator_fingerprint).strip()
    if not _SHA256_RE.fullmatch(fingerprint):
        raise ValueError("operator deployment fingerprint must be a lowercase SHA256")
    parsed_namespace = EmbeddingNamespace.from_dict(namespace)
    if parsed_namespace.identity_kind != "deployment_fingerprint":
        raise ValueError("operator manifest requires deployment_fingerprint identity")
    if parsed_namespace.identity_value != fingerprint:
        raise ValueError("operator fingerprint does not match namespace identity")
    if parsed_namespace.dimension != 1024:
        raise ValueError("operator embedding manifest dimension must be 1024")
    if not isinstance(field_evidence, dict):
        raise TypeError("embedding field evidence must be a dictionary")

    unresolved = []
    normalized_evidence: dict[str, dict[str, Any]] = {}
    namespace_dict = parsed_namespace.to_dict()
    for field in _EVIDENCE_FIELDS:
        evidence = field_evidence.get(field)
        if not isinstance(evidence, dict):
            raise ValueError(f"embedding manifest lacks field evidence for {field}")
        status = str(evidence.get("status") or "").strip()
        source = str(evidence.get("source") or "").strip()
        if not status or not source:
            raise ValueError(f"embedding field evidence is incomplete for {field}")
        normalized = dict(evidence)
        normalized["status"] = status
        normalized["source"] = source
        if status == "unresolved":
            unresolved.append(field)
        elif normalized.get("value") != namespace_dict[field]:
            raise ValueError(
                f"embedding evidence value does not match namespace field {field}"
            )
        normalized_evidence[field] = normalized

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "identity": {
            "kind": "deployment_fingerprint",
            "value": fingerprint,
            "algorithm": "sha256",
            "provenance": "operator_supplied",
            "scope": str(fingerprint_scope),
        },
        "endpoint_observation": dict(endpoint_observation),
        "namespace": namespace_dict,
        "namespace_sha256": parsed_namespace.sha256,
        "field_evidence": normalized_evidence,
        "unresolved_fields": sorted(unresolved),
        "gate_status": (
            "blocked_unresolved_runtime_config" if unresolved else "pass"
        ),
    }
    assert_safe_identity_artifact(manifest)
    validate_embedding_model_manifest(manifest, require_ready=False)
    return manifest


def validate_embedding_model_manifest(
    value: Any,
    *,
    require_ready: bool = True,
) -> Any:
    """Validate all cross-field bindings and return the immutable namespace."""
    from embedding_cache import EmbeddingNamespace

    if not isinstance(value, dict):
        raise TypeError("embedding model fingerprint manifest must be an object")
    assert_safe_identity_artifact(value)
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("unsupported embedding model fingerprint schema")
    identity = value.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("embedding model fingerprint manifest lacks identity")
    fingerprint = str(identity.get("value") or "")
    if (
        identity.get("kind") != "deployment_fingerprint"
        or identity.get("algorithm") != "sha256"
        or not _SHA256_RE.fullmatch(fingerprint)
    ):
        raise ValueError("embedding manifest has an invalid deployment fingerprint")

    try:
        namespace = EmbeddingNamespace.from_dict(value["namespace"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("embedding manifest has an invalid namespace") from exc
    if namespace.identity_kind != "deployment_fingerprint":
        raise ValueError("embedding manifest namespace has the wrong identity kind")
    if namespace.identity_value != fingerprint:
        raise ValueError("operator fingerprint does not match namespace identity")
    if namespace.dimension != 1024:
        raise ValueError("formal correctness embedding namespace dimension must be 1024")
    if value.get("namespace_sha256") != namespace.sha256:
        raise ValueError("embedding manifest namespace hash mismatch")

    evidence = value.get("field_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("embedding manifest lacks field evidence")
    unresolved = []
    namespace_dict = namespace.to_dict()
    for field in _EVIDENCE_FIELDS:
        item = evidence.get(field)
        if not isinstance(item, dict):
            raise ValueError(f"embedding manifest lacks field evidence for {field}")
        status = str(item.get("status") or "").strip()
        source = str(item.get("source") or "").strip()
        if not status or not source:
            raise ValueError(f"embedding field evidence is incomplete for {field}")
        if status == "unresolved":
            unresolved.append(field)
        elif item.get("value") != namespace_dict[field]:
            raise ValueError(
                f"embedding evidence value does not match namespace field {field}"
            )

    unresolved = sorted(unresolved)
    if value.get("unresolved_fields") != unresolved:
        raise ValueError("embedding manifest unresolved field list mismatch")
    expected_gate = "blocked_unresolved_runtime_config" if unresolved else "pass"
    if value.get("gate_status") != expected_gate:
        raise ValueError("embedding manifest gate status mismatch")
    endpoint = value.get("endpoint_observation")
    if not isinstance(endpoint, dict):
        raise ValueError("embedding manifest lacks endpoint observation")
    observed_model = endpoint.get("served_model_id")
    if observed_model is not None and observed_model != namespace.served_model_id:
        raise ValueError("endpoint model does not match embedding namespace")
    if require_ready and unresolved:
        raise ValueError(
            "embedding manifest has unresolved runtime config: "
            + ", ".join(unresolved)
        )
    return namespace


def write_embedding_model_manifest(
    value: dict[str, Any], output: str | Path
) -> dict[str, Any]:
    validate_embedding_model_manifest(value, require_ready=False)
    output = Path(output)
    payload = {
        **value,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(
            json.dumps(
                payload,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return payload


def write_identity_probe(value: dict[str, Any], output: str | Path) -> dict[str, Any]:
    assert_safe_identity_artifact(value)
    output = Path(output)
    payload = {
        **value,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(
            json.dumps(
                payload,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    return payload
