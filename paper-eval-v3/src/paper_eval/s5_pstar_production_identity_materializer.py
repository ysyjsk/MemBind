"""Materialize the service-free P*(C=2) runtime and production identity.

P* deliberately executes Graphiti's whole ``add_episode`` operation with two
workers.  Its execution envelope is derived from the already qualified A0
runtime so model, embedding, Graphiti, Neo4j, workload, and failure policy stay
identical; only the method and scheduler policy change.  This module performs
no network, model, embedding, or database I/O.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_production_identity,
    verify_s5_production_identity,
)


RUNTIME_CONFIG_SCHEMA = "membind.paper-eval-v3.s5-pstar-runtime-config.v1"
_A0_SCHEMA = "membind.paper-eval-v3.s5-a0-runtime-config.v1"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ROLES = {
    "dataset_builder",
    "graphiti_native",
    "runtime_factory",
    "scheduler",
    "scheduler_test",
    "durable_store",
    "durable_store_test",
}
_METHOD_POLICY = {
    "configured_concurrency": 2,
    "scheduler": "WHOLE_UPDATE_TWO_WORKERS",
    "serial_source_order": False,
    "whole_update_parallel": True,
}
_FAILURE_POLICY = {
    "db_commit_idempotence_claimed": False,
    "failed_attempt_status": "incomplete_non_mergeable",
    "fresh_attempt_required": True,
    "resume_authorized": False,
}
_AUTHORITY = {
    "current_stage_pointer_update_authorized": False,
    "embedding_call_authorized": False,
    "formal_execution_authorized": False,
    "model_call_authorized": False,
    "neo4j_mutation_authorized": False,
    "neo4j_read_authorized": False,
    "pilot_execution_authorized": False,
    "s5_live_execution_authorized": False,
    "s5_read_only_preflight_authorized": False,
}
_CONSTRUCTION = {
    "dtype": "bfloat16",
    "enable_thinking": False,
    "max_model_len": 65536,
    "quantization": "fp8",
    "repository_revision": "aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df",
    "requested_max_tokens": 16384,
    "rope_parameters": {
        "factor": 2.0,
        "original_max_position_embeddings": 32768,
        "rope_theta": 1000000,
        "rope_type": "yarn",
    },
    "served_model_id": "qwen3-32b-fp8",
    "structured_output_mode": "json_schema",
    "vllm_version": "0.26.0",
}
_EMBEDDING = {
    "deployment_fingerprint": (
        "5f5a8400eeaa2f07d167d8b5b7e63d615945a8f54f506e02342840cd4e3fe626"
    ),
    "dimension": 1024,
    "dtype": "bfloat16",
    "instruction_policy": "none",
    "normalization": "l2",
    "pooling": "last_token",
    "served_model_id": "qwen3-embedding-0.6b",
}
_NEO4J = {
    "deployment": "local_non_docker_community",
    "live_readiness_checked_by_s0": False,
    "uri": "bolt://localhost:7687",
    "version": "5.26.0",
}
_PSTAR_PAYLOAD_FIELDS = {
    "authority",
    "baseline_id",
    "construction",
    "current_stage_pointer_file_sha256",
    "current_stage_pointer_payload_sha256",
    "derived_from_a0",
    "embedding",
    "episode_count",
    "failure_policy",
    "graphiti",
    "graphiti_semantic_api_sha256",
    "graphiti_semantic_identity_file_sha256",
    "history_id",
    "method",
    "method_policy",
    "native_baseline_freeze_file_sha256",
    "native_baseline_freeze_payload_sha256",
    "neo4j",
    "runtime_factory_entrypoint",
    "schema_version",
    "source_closure_sha256",
    "stage",
    "status",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "group_id",
    "messages",
    "namespace",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
}


class S5PStarMaterializationError(ValueError):
    """The P* execution envelope is malformed or no longer source-identical."""


def _fail(code: str) -> S5PStarMaterializationError:
    return S5PStarMaterializationError(code)


@dataclass(frozen=True)
class S5PStarMaterializationPaths:
    """Local immutable inputs whose bytes define the P* production path."""

    a0_runtime_config: Path
    current_stage_pointer: Path
    graphiti_native: Path
    runtime_factory: Path
    dataset_builder: Path
    scheduler: Path
    scheduler_test: Path
    durable_store: Path
    durable_store_test: Path


@dataclass(frozen=True)
class S5PStarMaterializationBundle:
    runtime_config: dict[str, Any]
    production_identity: dict[str, object]


def _public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_materialization_field")
            _public(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _public(child)


def _load(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(value, dict):
        raise _fail(code)
    return value


def _sealed(value: Mapping[str, Any], code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = deepcopy(dict(value))
    payload = artifact.get("payload")
    if (
        set(artifact)
        != {
            "protocol_version",
            "git_commit",
            "run_id",
            "status",
            "payload",
            "payload_sha256",
        }
        or not isinstance(payload, Mapping)
        or artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail(code)
    artifact["payload"] = deepcopy(dict(payload))
    return artifact, artifact["payload"]


def _file_sha(path: Path, code: str) -> str:
    digest = sha256_file(Path(path))
    if digest == "missing" or _SHA.fullmatch(digest) is None:
        raise _fail(code)
    return digest


def _source_closure(paths: S5PStarMaterializationPaths) -> dict[str, str]:
    return {
        role: _file_sha(getattr(paths, role), f"source_missing:{role}")
        for role in sorted(_SOURCE_ROLES)
    }


def _validate_a0_base(
    paths: S5PStarMaterializationPaths,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    a0, payload = _sealed(
        _load(paths.a0_runtime_config, "a0_runtime_config_invalid"),
        "a0_runtime_config_invalid",
    )
    pointer, pointer_payload = _sealed(
        _load(paths.current_stage_pointer, "current_stage_pointer_invalid"),
        "current_stage_pointer_invalid",
    )
    closure = _source_closure(paths)
    if (
        payload.get("schema_version") != _A0_SCHEMA
        or payload.get("stage") != "S5"
        or payload.get("status") != "FROZEN_IDENTITY_ONLY"
        or payload.get("method") != "A0"
        or payload.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or payload.get("history_id") != "07741c45"
        or payload.get("episode_count") != 49
        or payload.get("failure_policy") != _FAILURE_POLICY
        or payload.get("runtime_factory_entrypoint")
        != "native_characterization_runtime.build_u0_graphiti_from_env"
        or payload.get("source_closure_sha256") != closure
        or pointer_payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("current_stage_pointer_file_sha256")
        != _file_sha(paths.current_stage_pointer, "current_stage_pointer_missing")
        or payload.get("current_stage_pointer_payload_sha256")
        != pointer.get("payload_sha256")
    ):
        raise _fail("source_closure_or_a0_binding_invalid")
    graphiti = payload.get("graphiti")
    construction = payload.get("construction")
    embedding = payload.get("embedding")
    neo4j = payload.get("neo4j")
    if (
        graphiti
        != {"repository_commit": GRAPHITI_COMMIT, "version": GRAPHITI_VERSION}
        or not isinstance(construction, Mapping)
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("max_model_len") != 65536
        or construction.get("requested_max_tokens") != 16384
        or construction.get("structured_output_mode") != "json_schema"
        or construction.get("enable_thinking") is not False
        or not isinstance(embedding, Mapping)
        or embedding.get("served_model_id") != "qwen3-embedding-0.6b"
        or embedding.get("dimension") != 1024
        or not isinstance(neo4j, Mapping)
        or neo4j.get("uri") != "bolt://localhost:7687"
        or neo4j.get("version") != "5.26.0"
    ):
        raise _fail("a0_execution_envelope_invalid")
    return a0, payload, closure


def _validate_pstar_execution_envelope(payload: Mapping[str, Any]) -> None:
    graphiti = payload.get("graphiti")
    sha_fields = (
        "current_stage_pointer_file_sha256",
        "current_stage_pointer_payload_sha256",
        "graphiti_semantic_api_sha256",
        "graphiti_semantic_identity_file_sha256",
        "native_baseline_freeze_file_sha256",
        "native_baseline_freeze_payload_sha256",
    )
    if (
        set(payload) != _PSTAR_PAYLOAD_FIELDS
        or payload.get("authority") != _AUTHORITY
        or payload.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or payload.get("construction") != _CONSTRUCTION
        or payload.get("embedding") != _EMBEDDING
        or payload.get("episode_count") != 49
        or payload.get("failure_policy") != _FAILURE_POLICY
        or graphiti
        != {"repository_commit": GRAPHITI_COMMIT, "version": GRAPHITI_VERSION}
        or payload.get("history_id") != "07741c45"
        or payload.get("neo4j") != _NEO4J
        or payload.get("runtime_factory_entrypoint")
        != "native_characterization_runtime.build_u0_graphiti_from_env"
        or any(
            not isinstance(payload.get(field), str)
            or _SHA.fullmatch(str(payload[field])) is None
            for field in sha_fields
        )
    ):
        raise _fail("pstar_execution_envelope_invalid")


def verify_s5_pstar_runtime_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the public P* runtime artifact and its two-worker policy."""

    artifact, payload = _sealed(value, "pstar_runtime_config_invalid")
    source_closure = payload.get("source_closure_sha256")
    derived = payload.get("derived_from_a0")
    _validate_pstar_execution_envelope(payload)
    if (
        payload.get("schema_version") != RUNTIME_CONFIG_SCHEMA
        or payload.get("stage") != "S5"
        or payload.get("status") != "FROZEN_IDENTITY_ONLY"
        or payload.get("method") != "P*"
        or payload.get("method_policy") != _METHOD_POLICY
        or payload.get("failure_policy") != _FAILURE_POLICY
        or not isinstance(source_closure, Mapping)
        or set(source_closure) != _SOURCE_ROLES
        or any(
            not isinstance(digest, str) or _SHA.fullmatch(digest) is None
            for digest in source_closure.values()
        )
        or not isinstance(derived, Mapping)
        or set(derived) != {"file_sha256", "payload_sha256"}
        or any(
            not isinstance(derived.get(field), str)
            or _SHA.fullmatch(str(derived[field])) is None
            for field in ("file_sha256", "payload_sha256")
        )
    ):
        raise _fail("pstar_runtime_config_invalid")
    _public(payload)
    return artifact


def materialize_s5_pstar_production_identity(
    *,
    paths: S5PStarMaterializationPaths,
    git_commit: str,
    run_id: str,
) -> S5PStarMaterializationBundle:
    """Build the sealed P* config and hash-only generic production identity."""

    if not isinstance(paths, S5PStarMaterializationPaths):
        raise _fail("materialization_paths_invalid")
    if not isinstance(git_commit, str) or not git_commit:
        raise _fail("git_commit_invalid")
    if not isinstance(run_id, str) or not run_id:
        raise _fail("run_id_invalid")
    a0, a0_payload, closure = _validate_a0_base(paths)
    payload = deepcopy(a0_payload)
    payload.update(
        {
            "schema_version": RUNTIME_CONFIG_SCHEMA,
            "method": "P*",
            "method_policy": deepcopy(_METHOD_POLICY),
            "derived_from_a0": {
                "file_sha256": _file_sha(
                    paths.a0_runtime_config, "a0_runtime_config_missing"
                ),
                "payload_sha256": str(a0["payload_sha256"]),
            },
        }
    )
    runtime_config = verify_s5_pstar_runtime_config(
        finalize_envelope(
            payload=payload,
            protocol_version=PROTOCOL_VERSION,
            git_commit=git_commit,
            run_id=f"{run_id}-runtime-config",
        )
    )
    identity = build_s5_production_identity(
        method="P*",
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=closure["graphiti_native"],
        graphiti_semantic_api_sha256=str(
            runtime_config["payload"]["graphiti_semantic_api_sha256"]
        ),
        runtime_factory_entrypoint=str(
            runtime_config["payload"]["runtime_factory_entrypoint"]
        ),
        runtime_factory_source_sha256=closure["runtime_factory"],
        scheduler_source_sha256=closure["scheduler"],
        scheduler_test_source_sha256=closure["scheduler_test"],
        durable_store_source_sha256=closure["durable_store"],
        durable_store_test_source_sha256=closure["durable_store_test"],
        runtime_config_sha256=str(runtime_config["payload_sha256"]),
    )
    return S5PStarMaterializationBundle(
        runtime_config=runtime_config,
        production_identity=verify_s5_production_identity(identity),
    )


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_s5_pstar_materialization_exclusive(
    *,
    bundle: S5PStarMaterializationBundle,
    runtime_config_path: Path,
    production_identity_path: Path,
) -> None:
    """Persist both public artifacts without replacing any prior evidence."""

    if not isinstance(bundle, S5PStarMaterializationBundle):
        raise _fail("materialization_bundle_invalid")
    runtime = Path(runtime_config_path)
    identity = Path(production_identity_path)
    if runtime.exists() or runtime.is_symlink() or identity.exists() or identity.is_symlink():
        raise FileExistsError("P* materialization output already exists")
    verify_s5_pstar_runtime_config(bundle.runtime_config)
    verify_s5_production_identity(bundle.production_identity)
    _write_exclusive(runtime, bundle.runtime_config)
    _write_exclusive(identity, bundle.production_identity)


__all__ = [
    "RUNTIME_CONFIG_SCHEMA",
    "S5PStarMaterializationBundle",
    "S5PStarMaterializationError",
    "S5PStarMaterializationPaths",
    "materialize_s5_pstar_production_identity",
    "verify_s5_pstar_runtime_config",
    "write_s5_pstar_materialization_exclusive",
]
