"""Method-scoped, single-use authority for one S5 live smoke.

This module is deliberately service-free.  A PASS read-only preflight may be
promoted into exactly one method/run/namespace authority, and that authority
must be durably consumed before the live controller constructs Graphiti.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import PROTOCOL_VERSION
from .artifacts import finalize_envelope, payload_sha256
from .s5_live_preflight import verify_s5_live_preflight
from .s5_production_identity_qualification import (
    S5ProductionIdentityQualificationError,
    bind_s5_production_identity_qualification,
    verify_s5_production_identity_qualification_binding,
)


AUTHORITY_SCHEMA = "membind.paper-eval-v3.s5-live-authority.v1"
CONSUMPTION_SCHEMA = "membind.paper-eval-v3.s5-live-authority-consumption.v1"
HISTORY_ID = "07741c45"
EPISODE_COUNT = 49

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_METHODS = ("A0", "P*", "M*")
_METHOD_RUN = {
    "A0": ("a0", 1),
    "P*": ("p-star", 2),
    "M*": ("mstar", 2),
}
_SOURCE_NAMES = {"authority", "controller", "result_verifier", "test"}
_PRIVATE_FIELDS = {
    "api_key",
    "answer",
    "body",
    "content",
    "credential",
    "episode",
    "messages",
    "password",
    "prompt",
    "question",
    "raw_output",
    "raw_response",
    "request",
    "response",
    "secret",
    "token",
}
_AUTHORITY_SCOPE = {
    "single_use": True,
    "model_call_authorized": True,
    "embedding_call_authorized": True,
    "neo4j_read_authorized": True,
    "neo4j_mutation_authorized": True,
    "s5_method_smoke_authorized": True,
    "next_method_authorized": False,
    "current_stage_pointer_update_authorized": False,
    "pilot_execution_authorized": False,
    "formal_execution_authorized": False,
}


class S5LiveAuthorityError(ValueError):
    """A live authority is malformed, over-broad, stale, or already used."""


def _fail(code: str) -> S5LiveAuthorityError:
    return S5LiveAuthorityError(code)


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(code)
    return deepcopy(dict(value))


def _sha(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
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
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _run(value: object, method: str) -> dict[str, object]:
    run = _mapping(value, "run_invalid")
    if set(run) != {
        "method",
        "run_id",
        "namespace",
        "history_id",
        "episode_count",
        "source_manifest_sha256",
        "configured_concurrency",
    }:
        raise _fail("run_shape_invalid")
    if method not in _METHOD_RUN:
        raise _fail("method_invalid")
    slug, concurrency = _METHOD_RUN[method]
    run_id = str(run.get("run_id", ""))
    namespace = str(run.get("namespace", ""))
    if (
        run.get("method") != method
        or re.fullmatch(rf"s5-{re.escape(slug)}-[0-9]{{8}}-[0-9]{{3}}", run_id)
        is None
        or namespace != f"pev3-{run_id}"
        or run.get("history_id") != HISTORY_ID
        or run.get("episode_count") != EPISODE_COUNT
        or _SHA256.fullmatch(str(run.get("source_manifest_sha256", ""))) is None
        or run.get("configured_concurrency") != concurrency
    ):
        raise _fail("run_identity_invalid")
    return run


def _sources(value: object) -> dict[str, str]:
    sources = _mapping(value, "source_inventory_invalid")
    if set(sources) != _SOURCE_NAMES:
        raise _fail("source_inventory_invalid")
    return {name: _sha(sources[name], f"source_{name}_invalid") for name in sorted(sources)}


def _predecessor(value: object, method: str) -> dict[str, object] | None:
    if method == "A0":
        if value is not None:
            raise _fail("a0_predecessor_forbidden")
        return None
    predecessor = _mapping(value, "predecessor_required")
    if set(predecessor) != {
        "method",
        "result_file_sha256",
        "result_payload_sha256",
        "verdict",
    }:
        raise _fail("predecessor_shape_invalid")
    expected_method = "A0" if method == "P*" else "P*"
    expected_verdict = "PASS" if method == "P*" else "SCIENTIFIC_OUTCOME_COMPLETE"
    if (
        predecessor.get("method") != expected_method
        or predecessor.get("verdict") != expected_verdict
    ):
        raise _fail("predecessor_order_invalid")
    _sha(predecessor.get("result_file_sha256"), "predecessor_file_invalid")
    _sha(predecessor.get("result_payload_sha256"), "predecessor_payload_invalid")
    return predecessor


def _fx0(
    value: object,
    method: str,
    identity_qualification: Mapping[str, object],
) -> dict[str, object] | None:
    if method != "M*":
        if value is not None:
            raise _fail("non_mstar_fx0_forbidden")
        return None
    fx0 = _mapping(value, "mstar_fx0_required")
    qualified_fx0 = identity_qualification.get("mstar_fx0")
    if not isinstance(qualified_fx0, Mapping):
        raise _fail("mstar_fx0_binding_invalid")
    if set(fx0) != {
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "production_parity_payload_sha256",
        "verdict",
    }:
        raise _fail("mstar_fx0_shape_invalid")
    for field in (
        "qualification_file_sha256",
        "qualification_payload_sha256",
        "production_parity_payload_sha256",
    ):
        _sha(fx0.get(field), f"mstar_fx0_{field}_invalid")
    if (
        fx0.get("verdict") != "PRODUCTION_PATH_EXACT_PARITY_PASS"
        or fx0.get("production_parity_payload_sha256")
        != qualified_fx0.get("fx0_artifact_payload_sha256")
        or fx0.get("qualification_file_sha256")
        != qualified_fx0.get("qualification_file_sha256")
        or fx0.get("qualification_payload_sha256")
        != qualified_fx0.get("qualification_payload_sha256")
    ):
        raise _fail("mstar_fx0_binding_invalid")
    return fx0


def build_s5_live_authority(
    *,
    method: str,
    run: Mapping[str, object],
    production_identity_qualification: Mapping[str, object],
    production_identity_qualification_file_sha256: str,
    preflight: Mapping[str, Any],
    preflight_file_sha256: str,
    current_stage_pointer_sha256: str,
    predecessor: Mapping[str, object] | None,
    fx0_qualification: Mapping[str, object] | None,
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    """Build a service-free authority draft from one sealed PASS preflight."""

    if method not in _METHODS:
        raise _fail("method_invalid")
    selected_run = _run(run, method)
    try:
        identity_qualification = bind_s5_production_identity_qualification(
            production_identity_qualification,
            file_sha256=production_identity_qualification_file_sha256,
        )
    except S5ProductionIdentityQualificationError:
        raise _fail("production_identity_qualification_invalid") from None
    if identity_qualification.get("method") != method:
        raise _fail("production_identity_qualification_method_mismatch")
    pointer = _sha(current_stage_pointer_sha256, "current_pointer_invalid")
    try:
        selected_preflight = verify_s5_live_preflight(preflight)
    except Exception:
        raise _fail("preflight_invalid") from None
    preflight_payload = selected_preflight["payload"]
    preflight_pointer = preflight_payload.get("current_stage_pointer")
    preflight_workload = preflight_payload.get("workload")
    preflight_predecessor = preflight_payload.get("predecessor")
    expected_predecessor = _predecessor(predecessor, method)
    expected_fx0 = _fx0(
        fx0_qualification, method, identity_qualification
    )
    predecessor_bound = (
        preflight_predecessor is None
        if expected_predecessor is None
        else isinstance(preflight_predecessor, Mapping)
        and preflight_predecessor.get("method")
        == expected_predecessor.get("method")
        and preflight_predecessor.get("verdict")
        == expected_predecessor.get("verdict")
        and preflight_predecessor.get("artifact_sha256")
        == expected_predecessor.get("result_file_sha256")
    )
    preflight_fx0 = preflight_payload.get("fx0_qualification")
    fx0_bound = (
        preflight_fx0 is None
        if expected_fx0 is None
        else isinstance(preflight_fx0, Mapping)
        and preflight_fx0.get("verdict")
        == expected_fx0.get("verdict")
        and preflight_fx0.get("qualification_payload_sha256")
        == expected_fx0.get("qualification_payload_sha256")
        and preflight_fx0.get("fx0_artifact_payload_sha256")
        == expected_fx0.get("production_parity_payload_sha256")
    )
    if (
        preflight_payload.get("method") != method
        or preflight_payload.get("run_id") != selected_run["run_id"]
        or preflight_payload.get("namespace") != selected_run["namespace"]
        or not isinstance(preflight_workload, Mapping)
        or preflight_workload
        != {
            "history_id": HISTORY_ID,
            "episode_count": EPISODE_COUNT,
            "source_manifest_sha256": selected_run["source_manifest_sha256"],
        }
        or preflight_payload.get("production_identity_qualification")
        != identity_qualification
        or not isinstance(preflight_pointer, Mapping)
        or preflight_pointer.get("file_sha256") != pointer
        or identity_qualification.get("current_stage_pointer", {}).get(
            "file_sha256"
        )
        != pointer
        or not predecessor_bound
        or not fx0_bound
        or preflight_payload.get("authority", {}).get(
            "s5_live_authority_creation_authorized"
        )
        is not True
    ):
        raise _fail("preflight_binding_mismatch")
    body = {
        "schema_version": AUTHORITY_SCHEMA,
        "stage": "S5_METHOD_SMOKE",
        "status": "AUTHORIZED_SINGLE_USE",
        "method": method,
        "run": selected_run,
        "production_identity_qualification": identity_qualification,
        "preflight_payload_sha256": selected_preflight["payload_sha256"],
        "preflight_file_sha256": _sha(
            preflight_file_sha256, "preflight_file_invalid"
        ),
        "current_stage_pointer_sha256": pointer,
        "predecessor": expected_predecessor,
        "fx0_qualification": expected_fx0,
        "source_sha256": _sources(source_sha256),
        "authority": deepcopy(_AUTHORITY_SCOPE),
    }
    return verify_s5_live_authority(
        finalize_envelope(
            payload=body,
            protocol_version=PROTOCOL_VERSION,
            git_commit="UNSEALED",
            run_id=f"{selected_run['run_id']}-authority-draft",
        )
    )


def verify_s5_live_authority(value: Mapping[str, Any]) -> dict[str, Any]:
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
    payload = _mapping(artifact.get("payload"), "authority_payload_invalid")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "status",
            "method",
            "run",
            "production_identity_qualification",
            "preflight_payload_sha256",
            "preflight_file_sha256",
            "current_stage_pointer_sha256",
            "predecessor",
            "fx0_qualification",
            "source_sha256",
            "authority",
        }
    ):
        raise _fail("authority_envelope_invalid")
    method = payload.get("method")
    if (
        method not in _METHODS
        or payload.get("schema_version") != AUTHORITY_SCHEMA
        or payload.get("stage") != "S5_METHOD_SMOKE"
        or payload.get("status") != "AUTHORIZED_SINGLE_USE"
    ):
        raise _fail("authority_identity_invalid")
    _run(payload.get("run"), str(method))
    for field in (
        "preflight_payload_sha256",
        "preflight_file_sha256",
        "current_stage_pointer_sha256",
    ):
        _sha(payload.get(field), f"{field}_invalid")
    try:
        identity_qualification = verify_s5_production_identity_qualification_binding(
            _mapping(
                payload.get("production_identity_qualification"),
                "production_identity_qualification_invalid",
            )
        )
    except S5ProductionIdentityQualificationError:
        raise _fail("production_identity_qualification_invalid") from None
    if identity_qualification.get("method") != method:
        raise _fail("production_identity_qualification_method_mismatch")
    _predecessor(payload.get("predecessor"), str(method))
    _fx0(
        payload.get("fx0_qualification"),
        str(method),
        identity_qualification,
    )
    _sources(payload.get("source_sha256"))
    if payload.get("authority") != _AUTHORITY_SCOPE:
        raise _fail("authority_scope_invalid")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def finalize_s5_live_authority(
    *,
    output_path: Path,
    authority: Mapping[str, Any],
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    artifact = verify_s5_live_authority(
        finalize_envelope(
            payload=_mapping(authority, "authority_payload_invalid"),
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), artifact)
    return artifact


def verify_s5_live_authority_consumption(
    value: Mapping[str, Any],
) -> dict[str, Any]:
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
    payload = _mapping(artifact.get("payload"), "consumption_payload_invalid")
    if (
        artifact.get("protocol_version") != PROTOCOL_VERSION
        or artifact.get("status") != "finalized"
        or artifact.get("payload_sha256") != payload_sha256(payload)
        or set(payload)
        != {
            "schema_version",
            "stage",
            "consumed_action",
            "method",
            "run",
            "authority_file_sha256",
            "authority_payload_sha256",
            "production_identity_sha256",
            "production_identity_qualification_payload_sha256",
            "further_live_authority",
        }
    ):
        raise _fail("consumption_envelope_invalid")
    method = payload.get("method")
    if (
        method not in _METHODS
        or payload.get("schema_version") != CONSUMPTION_SCHEMA
        or payload.get("stage") != "S5_METHOD_SMOKE"
        or payload.get("consumed_action") != f"S5_{method}_METHOD_SMOKE"
        or payload.get("further_live_authority") is not False
    ):
        raise _fail("consumption_identity_invalid")
    _run(payload.get("run"), str(method))
    for field in (
        "authority_file_sha256",
        "authority_payload_sha256",
        "production_identity_sha256",
        "production_identity_qualification_payload_sha256",
    ):
        _sha(payload.get(field), f"{field}_invalid")
    _reject_private(payload)
    artifact["payload"] = payload
    return artifact


def consume_s5_live_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
    git_commit: str,
    run_id: str,
) -> dict[str, Any]:
    selected = verify_s5_live_authority(authority)
    payload = selected["payload"]
    consumption = verify_s5_live_authority_consumption(
        finalize_envelope(
            payload={
                "schema_version": CONSUMPTION_SCHEMA,
                "stage": "S5_METHOD_SMOKE",
                "consumed_action": f"S5_{payload['method']}_METHOD_SMOKE",
                "method": payload["method"],
                "run": deepcopy(payload["run"]),
                "authority_file_sha256": _sha(
                    authority_file_sha256, "authority_file_invalid"
                ),
                "authority_payload_sha256": selected["payload_sha256"],
                "production_identity_sha256": payload[
                    "production_identity_qualification"
                ]["production_identity_sha256"],
                "production_identity_qualification_payload_sha256": payload[
                    "production_identity_qualification"
                ]["qualification_payload_sha256"],
                "further_live_authority": False,
            },
            protocol_version=PROTOCOL_VERSION,
            git_commit=str(git_commit),
            run_id=str(run_id),
        )
    )
    _write_exclusive(Path(output_path), consumption)
    return consumption


__all__ = [
    "AUTHORITY_SCHEMA",
    "CONSUMPTION_SCHEMA",
    "S5LiveAuthorityError",
    "build_s5_live_authority",
    "consume_s5_live_authority",
    "finalize_s5_live_authority",
    "verify_s5_live_authority",
    "verify_s5_live_authority_consumption",
]
