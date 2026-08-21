"""Append-only recovery seal for a previously active L0 resource STOP."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .resource_evidence import (
    ResourceEvidenceError,
    build_resource_envelope,
    require_resource_gate,
)


class StopSupersessionError(ValueError):
    """The STOP supersession or one of its bound inputs is invalid."""


SCHEMA_VERSION = "membind.saturated-fixed-work.stop-supersession.v1"
SUPERSESSION_NAME = "STOP_SUPERSEDED_BY_RESOURCE_RECOVERY.json"
STOP_NAME = "STOP_WITH_EXTERNAL_DIAGNOSIS.json"
_RESOURCE_PATHS = {
    "historical_provider": "service_evidence/historical_provider_resource.json",
    "live_provider": "service_evidence/live_provider_resource.json",
    "runner_neo4j": "service_evidence/runner_neo4j_resource.json",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _read_self_hashed(path: Path, *, code: str) -> tuple[dict[str, Any], str]:
    if path.is_symlink() or not path.is_file():
        raise StopSupersessionError(code)
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise StopSupersessionError(code) from None
    if not isinstance(value, dict):
        raise StopSupersessionError(code)
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if not isinstance(observed, str) or observed != _hash(candidate):
        raise StopSupersessionError(code)
    return value, hashlib.sha256(raw).hexdigest()


def _resource_inputs(
    root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    resources: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, str]] = {}
    for name, relative_path in _RESOURCE_PATHS.items():
        value, file_hash = _read_self_hashed(
            root / relative_path,
            code=f"RESOURCE_EVIDENCE_INVALID:{name}",
        )
        resources[name] = value
        bindings[name] = {
            "path": relative_path,
            "file_sha256": file_hash,
            "payload_sha256": str(value["payload_sha256"]),
        }
    try:
        envelope = build_resource_envelope(
            live_provider={
                key: value
                for key, value in resources["live_provider"].items()
                if key != "payload_sha256"
            },
            historical_provider={
                key: value
                for key, value in resources["historical_provider"].items()
                if key != "payload_sha256"
            },
            runner_neo4j={
                key: value
                for key, value in resources["runner_neo4j"].items()
                if key != "payload_sha256"
            },
        )
        gate = require_resource_gate(envelope)
    except (ResourceEvidenceError, KeyError, TypeError, ValueError):
        raise StopSupersessionError("RESOURCE_GATE_FAILED") from None
    return resources, bindings, {"gate": gate, "envelope_sha256": _hash(envelope)}


def _stop_binding(root: Path) -> dict[str, str]:
    value, file_hash = _read_self_hashed(
        root / STOP_NAME,
        code="STOP_DIAGNOSIS_INVALID",
    )
    if value.get("completed") is not False:
        raise StopSupersessionError("STOP_DIAGNOSIS_INVALID")
    return {
        "path": STOP_NAME,
        "file_sha256": file_hash,
        "payload_sha256": str(value["payload_sha256"]),
    }


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o444)
    except FileExistsError:
        raise StopSupersessionError("STOP_SUPERSESSION_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def materialize_stop_supersession(run_root: Path) -> dict[str, Any]:
    """Seal valid resource recovery without rewriting the historical STOP."""

    root = run_root.resolve()
    stop = _stop_binding(root)
    _, resources, resource_gate = _resource_inputs(root)
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "RESOURCE_IDENTITY_RECOVERED",
        "superseded_stop": stop,
        "resource_evidence": resources,
        "resource_envelope_sha256": resource_gate["envelope_sha256"],
        "resource_gate": resource_gate["gate"],
    }
    body["payload_sha256"] = _hash(body)
    _write_new(root / SUPERSESSION_NAME, body)
    return body


def verify_stop_supersession(run_root: Path) -> dict[str, Any]:
    """Revalidate the seal and every byte-exact input it authorizes."""

    root = run_root.resolve()
    seal, _ = _read_self_hashed(
        root / SUPERSESSION_NAME,
        code="STOP_SUPERSESSION_INVALID",
    )
    if (
        seal.get("schema_version") != SCHEMA_VERSION
        or seal.get("status") != "RESOURCE_IDENTITY_RECOVERED"
        or seal.get("superseded_stop") != _stop_binding(root)
    ):
        raise StopSupersessionError("STOP_SUPERSESSION_INVALID")
    _, resources, resource_gate = _resource_inputs(root)
    if (
        seal.get("resource_evidence") != resources
        or seal.get("resource_envelope_sha256") != resource_gate["envelope_sha256"]
        or seal.get("resource_gate") != resource_gate["gate"]
    ):
        raise StopSupersessionError("STOP_SUPERSESSION_INVALID")
    return {
        "schema_version": SCHEMA_VERSION,
        "verified": True,
        "payload_sha256": seal["payload_sha256"],
        "status": seal["status"],
    }


__all__ = [
    "SCHEMA_VERSION",
    "STOP_NAME",
    "SUPERSESSION_NAME",
    "StopSupersessionError",
    "materialize_stop_supersession",
    "verify_stop_supersession",
]
