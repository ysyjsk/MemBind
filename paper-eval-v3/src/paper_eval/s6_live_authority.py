"""Service-free preflight and single-use authority for one S6 matrix cell."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import payload_sha256
from .s6_calibration_contract import (
    verify_s6_cell_identity,
    verify_s6_matrix_freeze,
)


PREFLIGHT_SCHEMA = "membind.paper-eval-v3.s6-live-preflight.v1"
AUTHORITY_SCHEMA = "membind.paper-eval-v3.s6-live-authority.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s6-live-authority-consumption.v1"
STAGE = "S6_DEVELOPMENT_ONLY_CONCURRENCY_CALIBRATION"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_NAMES = {
    "authority",
    "calibration_contract",
    "block_controller",
    "method_runner",
    "block_postprocess",
    "authority_test",
    "production_runtime",
}
_PRIVATE_FIELDS = {
    "api_key",
    "authorization",
    "body",
    "content",
    "credential",
    "episode",
    "messages",
    "password",
    "prompt",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}
_AUTHORITY_SCOPE = {
    "single_use": True,
    "construction_call_authorized": True,
    "embedding_call_authorized": True,
    "neo4j_read_authorized": True,
    "neo4j_mutation_authorized": True,
    "s6_development_calibration_block_authorized": True,
    "namespace_cleanup_authorized": False,
    "next_cell_authorized": False,
    "current_stage_pointer_update_authorized": False,
    "pilot_execution_authorized": False,
    "final_paper_test_execution_authorized": False,
}
_PREFLIGHT_AUTHORITY = {"s6_block_authority_creation_authorized": True}
_PREFLIGHT_DENIAL = {"s6_block_authority_creation_authorized": False}


class S6LiveAuthorityError(ValueError):
    """An S6 preflight or one-cell authority failed closed."""


def _fail(code: str) -> S6LiveAuthorityError:
    return S6LiveAuthorityError(code)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _git_commit(value: object) -> str:
    if not isinstance(value, str) or _GIT_COMMIT.fullmatch(value) is None:
        raise _fail("git_commit_invalid")
    return value


def _reject_private(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str) or key.casefold() in _PRIVATE_FIELDS:
                raise _fail("private_field_forbidden")
            _reject_private(child)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            _reject_private(child)


def _write_exclusive(path: Path, value: Mapping[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written == 0:
                raise OSError("short write while sealing S6 authority evidence")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _cell(value: object) -> dict[str, object]:
    try:
        return verify_s6_cell_identity(_mapping(value, "cell_invalid"))
    except Exception:
        raise _fail("cell_identity_invalid")


def _selected_cell(freeze: Mapping[str, object], cell_index: int) -> dict[str, object]:
    try:
        artifact = verify_s6_matrix_freeze(freeze)
    except Exception:
        raise _fail("matrix_freeze_invalid") from None
    if (
        isinstance(cell_index, bool)
        or not isinstance(cell_index, int)
        or cell_index < 0
        or cell_index >= len(artifact["payload"]["cells"])
    ):
        raise _fail("cell_index_invalid")
    selected = artifact["payload"]["cells"][cell_index]
    return _cell(selected)


def _matrix_binding(
    freeze: Mapping[str, object], matrix_file_sha256: str
) -> dict[str, str]:
    try:
        artifact = verify_s6_matrix_freeze(freeze)
    except Exception:
        raise _fail("matrix_freeze_invalid") from None
    return {
        "file_sha256": _sha(matrix_file_sha256, "matrix_file_sha256_invalid"),
        "payload_sha256": _sha(
            artifact.get("payload_sha256"), "matrix_payload_sha256_invalid"
        ),
        "matrix_sha256": _sha(
            artifact["payload"].get("matrix_sha256"), "matrix_sha256_invalid"
        ),
    }


def _verify_matrix_binding(value: object) -> dict[str, str]:
    selected = _mapping(value, "matrix_binding_invalid")
    if set(selected) != {"file_sha256", "payload_sha256", "matrix_sha256"}:
        raise _fail("matrix_binding_invalid")
    return {
        key: _sha(selected.get(key), f"matrix_{key}_invalid")
        for key in ("file_sha256", "payload_sha256", "matrix_sha256")
    }


def _workload(episode_source_sha256s: Sequence[str]) -> dict[str, object]:
    if isinstance(episode_source_sha256s, (str, bytes)) or not isinstance(
        episode_source_sha256s, Sequence
    ):
        raise _fail("source_manifest_invalid")
    source_hashes = tuple(episode_source_sha256s)
    if not source_hashes:
        raise _fail("source_manifest_invalid")
    for digest in source_hashes:
        _sha(digest, "source_manifest_invalid")
    manifest = [
        {"source_sequence": index, "source_sha256": digest}
        for index, digest in enumerate(source_hashes)
    ]
    return {
        "source_count": len(source_hashes),
        "source_manifest_sha256": payload_sha256(manifest),
    }


def _verify_workload(value: object) -> dict[str, object]:
    workload = _mapping(value, "workload_invalid")
    count = workload.get("source_count")
    if (
        set(workload) != {"source_count", "source_manifest_sha256"}
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 1
    ):
        raise _fail("workload_invalid")
    _sha(workload.get("source_manifest_sha256"), "source_manifest_sha256_invalid")
    return workload


def _source_inventory(value: object) -> dict[str, str]:
    selected = _mapping(value, "source_inventory_invalid")
    if set(selected) != _SOURCE_NAMES:
        raise _fail("source_inventory_invalid")
    return {
        key: _sha(selected.get(key), f"source_{key}_invalid")
        for key in sorted(_SOURCE_NAMES)
    }


def _observations(value: object) -> dict[str, object]:
    selected = _mapping(value, "observations_invalid")
    if set(selected) != {
        "construction",
        "embedding",
        "neo4j_connectivity",
        "namespace",
        "namespace_state",
    }:
        raise _fail("observations_invalid")
    construction = _mapping(selected.get("construction"), "construction_invalid")
    embedding = _mapping(selected.get("embedding"), "embedding_invalid")
    namespace_state = _mapping(
        selected.get("namespace_state"), "namespace_state_invalid"
    )
    if set(construction) != {
        "status",
        "served_model_id",
        "vllm_version",
        "max_model_len",
    }:
        raise _fail("construction_invalid")
    if set(embedding) != {"status", "served_model_id"}:
        raise _fail("embedding_invalid")
    if set(namespace_state) != {"node_count", "relationship_count"}:
        raise _fail("namespace_state_invalid")
    for key in ("node_count", "relationship_count"):
        count = namespace_state.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise _fail("namespace_state_invalid")
    if not isinstance(selected.get("neo4j_connectivity"), bool) or not isinstance(
        selected.get("namespace"), str
    ):
        raise _fail("observations_invalid")
    selected["construction"] = construction
    selected["embedding"] = embedding
    selected["namespace_state"] = namespace_state
    return selected


def _observation_failures(
    observations: Mapping[str, object], cell: Mapping[str, object]
) -> list[str]:
    failures: list[str] = []
    if observations["construction"] != {
        "status": "PASS",
        "served_model_id": "qwen3-32b-fp8",
        "vllm_version": "0.26.0",
        "max_model_len": 65536,
    }:
        failures.append("construction_service_identity_or_readiness_failed")
    if observations["embedding"] != {
        "status": "PASS",
        "served_model_id": "qwen3-embedding-0.6b",
    }:
        failures.append("embedding_service_identity_or_readiness_failed")
    if observations["neo4j_connectivity"] is not True:
        failures.append("neo4j_connectivity_failed")
    if observations["namespace"] != cell["namespace"]:
        failures.append("namespace_binding_failed")
    if observations["namespace_state"] != {
        "node_count": 0,
        "relationship_count": 0,
    }:
        failures.append("namespace_not_empty")
    return failures


def evaluate_s6_live_preflight(
    *,
    matrix_freeze: Mapping[str, object],
    matrix_file_sha256: str,
    cell_index: int,
    episode_source_sha256s: Sequence[str],
    execution_identity_sha256: str,
    observations: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate only readiness, identity, source volume, and namespace emptiness."""

    cell = _selected_cell(matrix_freeze, cell_index)
    selected_observations = _observations(observations)
    failures = _observation_failures(selected_observations, cell)
    payload = {
        "schema_version": PREFLIGHT_SCHEMA,
        "stage": STAGE,
        "verdict": "PASS" if not failures else "FAIL",
        "cell": cell,
        "matrix": _matrix_binding(matrix_freeze, matrix_file_sha256),
        "workload": _workload(episode_source_sha256s),
        "execution_identity_sha256": _sha(
            execution_identity_sha256, "execution_identity_invalid"
        ),
        "observations": selected_observations,
        "failures": failures,
        "authority": deepcopy(
            _PREFLIGHT_AUTHORITY if not failures else _PREFLIGHT_DENIAL
        ),
    }
    _reject_private(payload)
    return _verify_preflight_payload(payload)


def _verify_preflight_payload(value: object) -> dict[str, object]:
    payload = _mapping(value, "preflight_payload_invalid")
    if set(payload) != {
        "schema_version",
        "stage",
        "verdict",
        "cell",
        "matrix",
        "workload",
        "execution_identity_sha256",
        "observations",
        "failures",
        "authority",
    }:
        raise _fail("preflight_payload_shape_invalid")
    cell = _cell(payload.get("cell"))
    matrix = _verify_matrix_binding(payload.get("matrix"))
    workload = _verify_workload(payload.get("workload"))
    execution_identity = _sha(
        payload.get("execution_identity_sha256"), "execution_identity_invalid"
    )
    observations = _observations(payload.get("observations"))
    failures = _observation_failures(observations, cell)
    expected_verdict = "PASS" if not failures else "FAIL"
    expected_authority = _PREFLIGHT_AUTHORITY if not failures else _PREFLIGHT_DENIAL
    if (
        payload.get("schema_version") != PREFLIGHT_SCHEMA
        or payload.get("stage") != STAGE
        or payload.get("verdict") != expected_verdict
        or payload.get("failures") != failures
        or payload.get("authority") != expected_authority
    ):
        raise _fail("preflight_semantics_invalid")
    payload.update(
        cell=cell,
        matrix=matrix,
        workload=workload,
        execution_identity_sha256=execution_identity,
        observations=observations,
    )
    _reject_private(payload)
    return payload


def verify_s6_live_preflight(value: Mapping[str, object]) -> dict[str, object]:
    artifact = _mapping(value, "preflight_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("preflight_envelope_shape_invalid")
    payload = _verify_preflight_payload(artifact.get("payload"))
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit", ""))) is None
        or artifact.get("run_id") != f"{payload['cell']['run_id']}-preflight"
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("preflight_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def finalize_s6_live_preflight(
    *, output_path: Path, evaluation: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    payload = _verify_preflight_payload(evaluation)
    artifact = verify_s6_live_preflight(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": _git_commit(git_commit),
            "run_id": f"{payload['cell']['run_id']}-preflight",
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


def build_s6_live_authority(
    *,
    matrix_freeze: Mapping[str, object],
    matrix_file_sha256: str,
    cell_index: int,
    episode_source_sha256s: Sequence[str],
    preflight: Mapping[str, object],
    preflight_file_sha256: str,
    execution_identity_sha256: str,
    source_sha256: Mapping[str, str],
) -> dict[str, object]:
    """Build a draft authority without contacting Graphiti, services, or Neo4j."""

    cell = _selected_cell(matrix_freeze, cell_index)
    matrix = _matrix_binding(matrix_freeze, matrix_file_sha256)
    workload = _workload(episode_source_sha256s)
    try:
        selected_preflight = verify_s6_live_preflight(preflight)
    except S6LiveAuthorityError:
        raise _fail("preflight_invalid") from None
    preflight_payload = selected_preflight["payload"]
    if preflight_payload["matrix"]["file_sha256"] != matrix["file_sha256"]:
        raise _fail("matrix_file_binding_invalid")
    if preflight_payload["matrix"] != matrix:
        raise _fail("matrix_binding_invalid")
    if preflight_payload["cell"] != cell:
        raise _fail("preflight_cell_binding_invalid")
    if preflight_payload["workload"] != workload:
        raise _fail("source_manifest_binding_invalid")
    execution_identity = _sha(
        execution_identity_sha256, "execution_identity_invalid"
    )
    if preflight_payload["execution_identity_sha256"] != execution_identity:
        raise _fail("execution_identity_binding_invalid")
    if (
        preflight_payload["verdict"] != "PASS"
        or preflight_payload["authority"] != _PREFLIGHT_AUTHORITY
    ):
        raise _fail("preflight_not_pass")
    sources = _source_inventory(source_sha256)
    payload = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": STAGE,
        "status": "AUTHORIZED_SINGLE_USE",
        "matrix": matrix,
        "cell": cell,
        "workload": workload,
        "preflight": {
            "file_sha256": _sha(
                preflight_file_sha256, "preflight_file_sha256_invalid"
            ),
            "payload_sha256": selected_preflight["payload_sha256"],
        },
        "execution_identity_sha256": execution_identity,
        "source_sha256": sources,
        "source_closure_sha256": payload_sha256(sources),
        "authority": deepcopy(_AUTHORITY_SCOPE),
    }
    return verify_s6_live_authority(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": "UNSEALED",
            "run_id": f"{cell['run_id']}-authority-draft",
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
    )


def _verify_authority_payload(value: object) -> dict[str, object]:
    payload = _mapping(value, "authority_payload_invalid")
    if set(payload) != {
        "schema_version",
        "stage",
        "status",
        "matrix",
        "cell",
        "workload",
        "preflight",
        "execution_identity_sha256",
        "source_sha256",
        "source_closure_sha256",
        "authority",
    }:
        raise _fail("authority_payload_shape_invalid")
    matrix = _verify_matrix_binding(payload.get("matrix"))
    cell = _cell(payload.get("cell"))
    workload = _verify_workload(payload.get("workload"))
    preflight = _mapping(payload.get("preflight"), "preflight_binding_invalid")
    if set(preflight) != {"file_sha256", "payload_sha256"}:
        raise _fail("preflight_binding_invalid")
    preflight = {
        "file_sha256": _sha(
            preflight.get("file_sha256"), "preflight_file_sha256_invalid"
        ),
        "payload_sha256": _sha(
            preflight.get("payload_sha256"), "preflight_payload_sha256_invalid"
        ),
    }
    sources = _source_inventory(payload.get("source_sha256"))
    if (
        payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != STAGE
        or payload.get("status") != "AUTHORIZED_SINGLE_USE"
        or payload.get("authority") != _AUTHORITY_SCOPE
        or payload.get("source_closure_sha256") != payload_sha256(sources)
    ):
        raise _fail("authority_semantics_invalid")
    payload.update(
        matrix=matrix,
        cell=cell,
        workload=workload,
        preflight=preflight,
        execution_identity_sha256=_sha(
            payload.get("execution_identity_sha256"), "execution_identity_invalid"
        ),
        source_sha256=sources,
    )
    _reject_private(payload)
    return payload


def verify_s6_live_authority(value: Mapping[str, object]) -> dict[str, object]:
    artifact = _mapping(value, "authority_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("authority_envelope_shape_invalid")
    payload = _verify_authority_payload(artifact.get("payload"))
    git_commit = artifact.get("git_commit")
    if git_commit != "UNSEALED" and _GIT_COMMIT.fullmatch(str(git_commit)) is None:
        raise _fail("authority_envelope_invalid")
    expected_run_ids = {
        f"{payload['cell']['run_id']}-authority-draft",
        f"{payload['cell']['run_id']}-authority",
    }
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("run_id") not in expected_run_ids
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("authority_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def verify_s6_live_authority_binding(
    authority: Mapping[str, object],
    *,
    matrix_freeze: Mapping[str, object],
    matrix_file_sha256: str,
) -> dict[str, object]:
    selected = verify_s6_live_authority(authority)
    cell_index = int(selected["payload"]["cell"]["cell_index"])
    if (
        selected["payload"]["matrix"]
        != _matrix_binding(matrix_freeze, matrix_file_sha256)
        or selected["payload"]["cell"]
        != _selected_cell(matrix_freeze, cell_index)
    ):
        raise _fail("authority_matrix_binding_invalid")
    return selected


def finalize_s6_live_authority(
    *, output_path: Path, authority: Mapping[str, object], git_commit: str
) -> dict[str, object]:
    payload = _verify_authority_payload(authority)
    artifact = verify_s6_live_authority(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": _git_commit(git_commit),
            "run_id": f"{payload['cell']['run_id']}-authority",
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


def _verify_consumption_payload(value: object) -> dict[str, object]:
    payload = _mapping(value, "consumption_payload_invalid")
    if set(payload) != {
        "schema_version",
        "stage",
        "consumed_action",
        "matrix",
        "cell",
        "workload",
        "preflight",
        "execution_identity_sha256",
        "source_sha256",
        "source_closure_sha256",
        "authority_file_sha256",
        "authority_payload_sha256",
        "further_live_authority",
    }:
        raise _fail("consumption_payload_shape_invalid")
    matrix = _verify_matrix_binding(payload.get("matrix"))
    cell = _cell(payload.get("cell"))
    workload = _verify_workload(payload.get("workload"))
    preflight = _mapping(payload.get("preflight"), "preflight_binding_invalid")
    if set(preflight) != {"file_sha256", "payload_sha256"}:
        raise _fail("preflight_binding_invalid")
    preflight = {
        key: _sha(preflight.get(key), f"preflight_{key}_invalid")
        for key in ("file_sha256", "payload_sha256")
    }
    sources = _source_inventory(payload.get("source_sha256"))
    if (
        payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != STAGE
        or payload.get("consumed_action") != "S6_DEVELOPMENT_CALIBRATION_BLOCK"
        or payload.get("source_closure_sha256") != payload_sha256(sources)
        or payload.get("further_live_authority") is not False
    ):
        raise _fail("consumption_semantics_invalid")
    payload.update(
        matrix=matrix,
        cell=cell,
        workload=workload,
        preflight=preflight,
        execution_identity_sha256=_sha(
            payload.get("execution_identity_sha256"), "execution_identity_invalid"
        ),
        source_sha256=sources,
        authority_file_sha256=_sha(
            payload.get("authority_file_sha256"), "authority_file_sha256_invalid"
        ),
        authority_payload_sha256=_sha(
            payload.get("authority_payload_sha256"),
            "authority_payload_sha256_invalid",
        ),
    )
    _reject_private(payload)
    return payload


def verify_s6_live_authority_consumption(
    value: Mapping[str, object],
) -> dict[str, object]:
    artifact = _mapping(value, "consumption_invalid")
    if set(artifact) != {
        "protocol_version",
        "git_commit",
        "run_id",
        "status",
        "payload",
        "payload_sha256",
    }:
        raise _fail("consumption_envelope_shape_invalid")
    payload = _verify_consumption_payload(artifact.get("payload"))
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or _GIT_COMMIT.fullmatch(str(artifact.get("git_commit", ""))) is None
        or artifact.get("run_id")
        != f"{payload['cell']['run_id']}-authority-consumption"
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
    ):
        raise _fail("consumption_envelope_invalid")
    artifact["payload"] = payload
    return artifact


def consume_s6_live_authority(
    *,
    authority: Mapping[str, object],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
) -> dict[str, object]:
    selected = verify_s6_live_authority(authority)
    authority_payload = selected["payload"]
    payload = {
        "schema_version": CONSUMPTION_SCHEMA,
        "stage": STAGE,
        "consumed_action": "S6_DEVELOPMENT_CALIBRATION_BLOCK",
        "matrix": deepcopy(authority_payload["matrix"]),
        "cell": deepcopy(authority_payload["cell"]),
        "workload": deepcopy(authority_payload["workload"]),
        "preflight": deepcopy(authority_payload["preflight"]),
        "execution_identity_sha256": authority_payload[
            "execution_identity_sha256"
        ],
        "source_sha256": deepcopy(authority_payload["source_sha256"]),
        "source_closure_sha256": authority_payload["source_closure_sha256"],
        "authority_file_sha256": _sha(
            authority_file_sha256, "authority_file_sha256_invalid"
        ),
        "authority_payload_sha256": selected["payload_sha256"],
        "further_live_authority": False,
    }
    artifact = verify_s6_live_authority_consumption(
        {
            "protocol_version": PROTOCOL_VERSION,
            "git_commit": _git_commit(git_commit),
            "run_id": f"{authority_payload['cell']['run_id']}-authority-consumption",
            "status": "finalized",
            "payload": payload,
            "payload_sha256": payload_sha256(payload),
        }
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


__all__ = [
    "AUTHORITY_SCHEMA",
    "CONSUMPTION_SCHEMA",
    "PREFLIGHT_SCHEMA",
    "S6LiveAuthorityError",
    "build_s6_live_authority",
    "consume_s6_live_authority",
    "evaluate_s6_live_preflight",
    "finalize_s6_live_authority",
    "finalize_s6_live_preflight",
    "verify_s6_live_authority",
    "verify_s6_live_authority_binding",
    "verify_s6_live_authority_consumption",
    "verify_s6_live_preflight",
]
