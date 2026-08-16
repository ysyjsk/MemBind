"""Materialize the real A0 code/config/workload identity without live I/O.

The module reads only frozen local artifacts and source files.  It does not
load private environment settings, construct Graphiti, or contact a model or
database.  The raw identity intentionally remains unqualified; later stages
must use the separate qualification, preflight, and single-use authority chain.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256, sha256_file
from .s1_live import EXPECTED_S1_HISTORY_ID, load_fixed_history
from .s5_production_runner import (
    GRAPHITI_COMMIT,
    GRAPHITI_VERSION,
    build_s5_production_identity,
    verify_s5_production_identity,
)


RUNTIME_CONFIG_SCHEMA = "membind.paper-eval-v3.s5-a0-runtime-config.v1"
MATERIALIZATION_SCHEMA = (
    "membind.paper-eval-v3.s5-a0-production-identity-materialization.v1"
)
EXPECTED_EPISODE_COUNT = 49
RUNTIME_FACTORY_ENTRYPOINT = (
    "native_characterization_runtime.build_u0_graphiti_from_env"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^s5-a0-[a-z0-9][a-z0-9-]{2,127}$")
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
_AUTHORITY = {
    "model_call_authorized": False,
    "embedding_call_authorized": False,
    "neo4j_read_authorized": False,
    "neo4j_mutation_authorized": False,
    "s5_read_only_preflight_authorized": False,
    "s5_live_execution_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
    "current_stage_pointer_update_authorized": False,
}
_SOURCE_FIELDS = {
    "graphiti_native": "graphiti_native_source_sha256",
    "runtime_factory": "runtime_factory_source_sha256",
    "scheduler": "scheduler_source_sha256",
    "scheduler_test": "scheduler_test_source_sha256",
    "durable_store": "durable_store_source_sha256",
    "durable_store_test": "durable_store_test_source_sha256",
}


class S5A0MaterializationError(ValueError):
    """A frozen A0 input or materialized identity failed closed."""


def _fail(code: str) -> S5A0MaterializationError:
    return S5A0MaterializationError(code)


@dataclass(frozen=True)
class S5A0MaterializationPaths:
    """Every local input whose bytes define the A0 production identity."""

    native_baseline_freeze: Path
    current_stage_pointer: Path
    graphiti_semantic_identity: Path
    dataset: Path
    frozen_split: Path
    dataset_builder: Path
    graphiti_native: Path
    runtime_factory: Path
    scheduler: Path
    scheduler_test: Path
    durable_store: Path
    durable_store_test: Path


@dataclass(frozen=True)
class S5A0ProductionIdentityBundle:
    """Public artifacts plus opaque native episodes retained only in memory."""

    runtime_config: dict[str, Any]
    production_identity: dict[str, object]
    materialization: dict[str, Any]
    native_episodes: tuple[object, ...]


def _assert_public(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_materialization_field")
            _assert_public(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _assert_public(child)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _file_sha(path: Path, code: str) -> str:
    digest = sha256_file(Path(path))
    if digest == "missing":
        raise _fail(code)
    return digest


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    return _mapping(value, code)


def _sealed_envelope(value: object, code: str) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = _mapping(value, code)
    payload = _mapping(artifact.get("payload"), code)
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
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail(code)
    artifact["payload"] = payload
    return artifact, payload


def _verify_freeze(
    path: Path,
    *,
    dataset_sha256: str,
    dataset_builder_sha256: str,
    graphiti_native_sha256: str,
    runtime_factory_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    artifact, payload = _sealed_envelope(
        _load_json(path, "native_freeze_invalid"), "native_freeze_invalid"
    )
    native = _mapping(payload.get("native_construction"), "native_freeze_invalid")
    critical = _mapping(payload.get("critical_source_sha256"), "native_freeze_invalid")
    common = _mapping(payload.get("common_evaluation_policy"), "native_freeze_invalid")
    if (
        payload.get("schema_version")
        != "membind.paper-eval-v3.native-baseline-v2-freeze.v1"
        or payload.get("status") != "PASS"
        or payload.get("baseline_id") != "native-graphiti-u0-reader-v2"
        or native.get("history_id") != EXPECTED_S1_HISTORY_ID
        or native.get("episode_count") != EXPECTED_EPISODE_COUNT
        or common.get("dataset_sha256") != dataset_sha256
        or critical.get("dataset_builder") != dataset_builder_sha256
        or critical.get("direct_add_episode") != graphiti_native_sha256
        or critical.get("u0_runtime") != runtime_factory_sha256
    ):
        raise _fail("native_freeze_binding_mismatch")
    construction = _mapping(native.get("construction"), "native_freeze_invalid")
    embedding = _mapping(native.get("embedding"), "native_freeze_invalid")
    graphiti = _mapping(native.get("graphiti"), "native_freeze_invalid")
    neo4j = _mapping(native.get("neo4j"), "native_freeze_invalid")
    if (
        graphiti
        != {"version": GRAPHITI_VERSION, "repository_commit": GRAPHITI_COMMIT}
        or construction.get("served_model_id") != "qwen3-32b-fp8"
        or construction.get("vllm_version") != "0.26.0"
        or construction.get("max_model_len") != 65536
        or construction.get("requested_max_tokens") != 16384
        or construction.get("structured_output_mode") != "json_schema"
        or construction.get("enable_thinking") is not False
        or embedding.get("served_model_id") != "qwen3-embedding-0.6b"
        or embedding.get("dimension") != 1024
        or neo4j.get("version") != "5.26.0"
    ):
        raise _fail("native_freeze_runtime_mismatch")
    return artifact, native, _file_sha(path, "native_freeze_missing")


def _verify_pointer(
    path: Path, *, freeze_file_sha256: str, freeze_payload_sha256: str
) -> tuple[dict[str, Any], str]:
    artifact, payload = _sealed_envelope(
        _load_json(path, "current_pointer_invalid"), "current_pointer_invalid"
    )
    if (
        payload.get("schema_version")
        != "membind.paper-eval-v3.current-stage-pointer.v2"
        or payload.get("current_stage") != "S3_CONFIGURATION_FROZEN"
        or payload.get("native_baseline_v2_freeze_file_sha256")
        != freeze_file_sha256
        or payload.get("native_baseline_v2_freeze_payload_sha256")
        != freeze_payload_sha256
        or payload.get("pilot_execution_authorized") is not False
        or payload.get("s4_live_execution_authorized") is not False
    ):
        raise _fail("current_pointer_binding_mismatch")
    return artifact, _file_sha(path, "current_pointer_missing")


def _verify_semantic_identity(path: Path) -> tuple[dict[str, Any], str]:
    artifact = _load_json(path, "semantic_identity_invalid")
    payload_hash = artifact.get("payload_sha256")
    payload = {key: value for key, value in artifact.items() if key != "payload_sha256"}
    projection = artifact.get("identity_projection")
    if (
        payload_hash != payload_sha256(payload)
        or not isinstance(projection, Mapping)
        or artifact.get("identity_sha256") != payload_sha256(projection)
        or artifact.get("graphiti_version") != GRAPHITI_VERSION
        or artifact.get("graphiti_commit") != GRAPHITI_COMMIT
        or artifact.get("status")
        != "OBSERVED_PINNED_LOCAL_INSTALL_NOT_LIVE_AUTHORITY"
        or artifact.get("model_call_authorized") is not False
        or artifact.get("neo4j_read_authorized") is not False
        or artifact.get("neo4j_mutation_authorized") is not False
        or artifact.get("s5_live_execution_authorized") is not False
    ):
        raise _fail("semantic_identity_binding_mismatch")
    _sha(artifact.get("identity_sha256"), "semantic_identity_invalid")
    return artifact, _file_sha(path, "semantic_identity_missing")


def _load_builder(path: Path, source_sha256: str) -> ModuleType:
    module_name = (
        "_membind_s5_dataset_"
        + source_sha256[:12]
        + "_"
        + payload_sha256(str(Path(path).resolve()))[:12]
    )
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise _fail("dataset_builder_import_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise _fail("dataset_builder_import_invalid") from None
    return module


def _episodes(
    *, dataset: Path, split: Path, builder_path: Path, builder_sha256: str
) -> tuple[object, ...]:
    try:
        history = load_fixed_history(dataset, split)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise _fail("frozen_workload_invalid") from None
    builder = getattr(_load_builder(builder_path, builder_sha256), "build_episodes", None)
    if not callable(builder):
        raise _fail("dataset_builder_callable_missing")
    try:
        selected = tuple(builder(dict(history)))
    except Exception:
        raise _fail("episode_materialization_invalid") from None
    sequences = [getattr(item, "source_sequence", None) for item in selected]
    hashes = [getattr(item, "source_hash", None) for item in selected]
    if (
        len(selected) != EXPECTED_EPISODE_COUNT
        or sequences != list(range(EXPECTED_EPISODE_COUNT))
        or any(not isinstance(value, str) or _SHA256.fullmatch(value) is None for value in hashes)
        or len(set(hashes)) != EXPECTED_EPISODE_COUNT
    ):
        raise _fail("episode_source_manifest_invalid")
    return selected


def _build_bundle(
    *, paths: S5A0MaterializationPaths, git_commit: str, run_id: str
) -> S5A0ProductionIdentityBundle:
    if not isinstance(paths, S5A0MaterializationPaths):
        raise _fail("materialization_paths_invalid")
    if not isinstance(git_commit, str) or not re.fullmatch(r"[0-9a-f]{7,40}", git_commit):
        raise _fail("git_commit_invalid")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        raise _fail("run_id_invalid")

    file_hashes = {
        field: _file_sha(Path(getattr(paths, field)), f"input_missing:{field}")
        for field in paths.__dataclass_fields__
    }
    freeze, native, freeze_file_sha = _verify_freeze(
        paths.native_baseline_freeze,
        dataset_sha256=file_hashes["dataset"],
        dataset_builder_sha256=file_hashes["dataset_builder"],
        graphiti_native_sha256=file_hashes["graphiti_native"],
        runtime_factory_sha256=file_hashes["runtime_factory"],
    )
    pointer, pointer_file_sha = _verify_pointer(
        paths.current_stage_pointer,
        freeze_file_sha256=freeze_file_sha,
        freeze_payload_sha256=str(freeze["payload_sha256"]),
    )
    semantic, semantic_file_sha = _verify_semantic_identity(
        paths.graphiti_semantic_identity
    )
    native_episodes = _episodes(
        dataset=paths.dataset,
        split=paths.frozen_split,
        builder_path=paths.dataset_builder,
        builder_sha256=file_hashes["dataset_builder"],
    )
    source_hashes = tuple(str(getattr(item, "source_hash")) for item in native_episodes)
    source_manifest_sha = payload_sha256(
        [
            {"source_sequence": index, "source_sha256": digest}
            for index, digest in enumerate(source_hashes)
        ]
    )

    source_closure = {
        role: file_hashes[role]
        for role in (*_SOURCE_FIELDS, "dataset_builder")
    }
    runtime_payload = {
        "schema_version": RUNTIME_CONFIG_SCHEMA,
        "stage": "S5",
        "status": "FROZEN_IDENTITY_ONLY",
        "method": "A0",
        "baseline_id": "native-graphiti-u0-reader-v2",
        "history_id": EXPECTED_S1_HISTORY_ID,
        "episode_count": EXPECTED_EPISODE_COUNT,
        "construction": deepcopy(native["construction"]),
        "embedding": deepcopy(native["embedding"]),
        "graphiti": deepcopy(native["graphiti"]),
        "neo4j": deepcopy(native["neo4j"]),
        "runtime_factory_entrypoint": RUNTIME_FACTORY_ENTRYPOINT,
        "method_policy": {
            "configured_concurrency": 1,
            "scheduler": "FIFO_SINGLE_WORKER",
            "serial_source_order": True,
        },
        "failure_policy": {
            "failed_attempt_status": "incomplete_non_mergeable",
            "resume_authorized": False,
            "fresh_attempt_required": True,
            "db_commit_idempotence_claimed": False,
        },
        "native_baseline_freeze_file_sha256": freeze_file_sha,
        "native_baseline_freeze_payload_sha256": freeze["payload_sha256"],
        "current_stage_pointer_file_sha256": pointer_file_sha,
        "current_stage_pointer_payload_sha256": pointer["payload_sha256"],
        "graphiti_semantic_identity_file_sha256": semantic_file_sha,
        "graphiti_semantic_api_sha256": semantic["identity_sha256"],
        "source_closure_sha256": dict(sorted(source_closure.items())),
        "authority": deepcopy(_AUTHORITY),
    }
    _assert_public(runtime_payload)
    runtime_config = finalize_envelope(
        payload=runtime_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=f"{run_id}-runtime-config",
    )

    identity = build_s5_production_identity(
        method="A0",
        graphiti_version=GRAPHITI_VERSION,
        graphiti_commit=GRAPHITI_COMMIT,
        graphiti_native_source_sha256=file_hashes["graphiti_native"],
        graphiti_semantic_api_sha256=str(semantic["identity_sha256"]),
        runtime_factory_entrypoint=RUNTIME_FACTORY_ENTRYPOINT,
        runtime_factory_source_sha256=file_hashes["runtime_factory"],
        scheduler_source_sha256=file_hashes["scheduler"],
        scheduler_test_source_sha256=file_hashes["scheduler_test"],
        durable_store_source_sha256=file_hashes["durable_store"],
        durable_store_test_source_sha256=file_hashes["durable_store_test"],
        runtime_config_sha256=str(runtime_config["payload_sha256"]),
    )
    verify_s5_production_identity(identity)

    materialization_payload = {
        "schema_version": MATERIALIZATION_SCHEMA,
        "stage": "S5",
        "status": "MATERIALIZED_IDENTITY_ONLY",
        "method": "A0",
        "qualification_status": "IDENTITY_ONLY_UNQUALIFIED",
        "production_identity_sha256": identity["identity_sha256"],
        "runtime_config": {
            "payload_sha256": runtime_config["payload_sha256"],
            "run_id": runtime_config["run_id"],
        },
        "native_baseline_freeze": {
            "file_sha256": freeze_file_sha,
            "payload_sha256": freeze["payload_sha256"],
        },
        "current_stage_pointer": {
            "file_sha256": pointer_file_sha,
            "payload_sha256": pointer["payload_sha256"],
        },
        "graphiti_semantic_identity": {
            "file_sha256": semantic_file_sha,
            "payload_sha256": semantic["payload_sha256"],
            "identity_sha256": semantic["identity_sha256"],
        },
        "dataset": {
            "file_sha256": file_hashes["dataset"],
            "frozen_split_file_sha256": file_hashes["frozen_split"],
            "episode_builder_source_sha256": file_hashes["dataset_builder"],
        },
        "source_closure_sha256": dict(sorted(source_closure.items())),
        "source_closure_digest": payload_sha256(dict(sorted(source_closure.items()))),
        "workload": {
            "history_id": EXPECTED_S1_HISTORY_ID,
            "episode_count": EXPECTED_EPISODE_COUNT,
            "ordered_source_sha256": list(source_hashes),
            "source_manifest_sha256": source_manifest_sha,
        },
        "authority": deepcopy(_AUTHORITY),
    }
    _assert_public(materialization_payload)
    materialization = finalize_envelope(
        payload=materialization_payload,
        protocol_version=PROTOCOL_VERSION,
        git_commit=git_commit,
        run_id=run_id,
    )
    return S5A0ProductionIdentityBundle(
        runtime_config=runtime_config,
        production_identity=identity,
        materialization=materialization,
        native_episodes=native_episodes,
    )


def materialize_s5_a0_production_identity(
    *, paths: S5A0MaterializationPaths, git_commit: str, run_id: str
) -> S5A0ProductionIdentityBundle:
    """Rebuild A0 identity inputs from the exact frozen local workload."""

    return _build_bundle(paths=paths, git_commit=git_commit, run_id=run_id)


def verify_s5_a0_production_identity_materialization(
    *,
    materialization: Mapping[str, Any],
    runtime_config: Mapping[str, Any],
    production_identity: Mapping[str, object],
    paths: S5A0MaterializationPaths,
) -> dict[str, Any]:
    """Re-materialize from disk and reject any artifact or input-byte drift."""

    artifact, _payload = _sealed_envelope(
        materialization, "materialization_invalid"
    )
    config, _config_payload = _sealed_envelope(
        runtime_config, "runtime_config_invalid"
    )
    try:
        identity = verify_s5_production_identity(production_identity)
    except ValueError:
        raise _fail("production_identity_invalid") from None
    expected = _build_bundle(
        paths=paths,
        git_commit=str(artifact["git_commit"]),
        run_id=str(artifact["run_id"]),
    )
    if (
        artifact != expected.materialization
        or config != expected.runtime_config
        or identity != expected.production_identity
    ):
        raise _fail("materialized_input_or_artifact_drift")
    return artifact


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_s5_a0_production_identity_materialization_exclusive(
    *,
    bundle: S5A0ProductionIdentityBundle,
    runtime_config_path: Path,
    production_identity_path: Path,
    materialization_path: Path,
    paths: S5A0MaterializationPaths,
) -> dict[str, str]:
    """Verify and exclusively persist the three authority-free A0 artifacts."""

    if not isinstance(bundle, S5A0ProductionIdentityBundle):
        raise _fail("materialization_bundle_invalid")
    verify_s5_a0_production_identity_materialization(
        materialization=bundle.materialization,
        runtime_config=bundle.runtime_config,
        production_identity=bundle.production_identity,
        paths=paths,
    )
    outputs = (
        Path(runtime_config_path),
        Path(production_identity_path),
        Path(materialization_path),
    )
    if len(set(outputs)) != 3:
        raise _fail("materialization_output_paths_invalid")
    for output in outputs:
        if output.exists():
            raise FileExistsError(output)
    _write_exclusive(outputs[0], bundle.runtime_config)
    _write_exclusive(outputs[1], bundle.production_identity)
    _write_exclusive(outputs[2], bundle.materialization)
    return {
        "runtime_config_file_sha256": _file_sha(outputs[0], "runtime_config_write_failed"),
        "production_identity_file_sha256": _file_sha(
            outputs[1], "production_identity_write_failed"
        ),
        "materialization_file_sha256": _file_sha(
            outputs[2], "materialization_write_failed"
        ),
    }


__all__ = [
    "EXPECTED_EPISODE_COUNT",
    "MATERIALIZATION_SCHEMA",
    "RUNTIME_CONFIG_SCHEMA",
    "S5A0MaterializationError",
    "S5A0MaterializationPaths",
    "S5A0ProductionIdentityBundle",
    "materialize_s5_a0_production_identity",
    "verify_s5_a0_production_identity_materialization",
    "write_s5_a0_production_identity_materialization_exclusive",
]
