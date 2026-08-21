"""Append-only L1 qualification advancement over the immutable run manifest."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .run_manifest import RunManifestError, verify_run_artifacts


class QualificationSealError(ValueError):
    """L1 qualification evidence is incomplete, changed, or not append-only."""


_BOOLEAN_GATES = (
    "preflight_passed",
    "instrumentation_aa_qualified",
    "b0_a_valid",
    "b0_b_valid",
    "b1_valid",
    "b0_schedule_contract",
    "b1_schedule_contract",
    "qa_read_only_passed",
    "canonical_diffs_emitted",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(evidence)
    if (
        any(selected.get(field) is not True for field in _BOOLEAN_GATES)
        or selected.get("serial_serial_12_scope")
        != "12_EPISODE_QUALIFICATION_ONLY"
        or not isinstance(selected.get("qualification_root"), str)
        or not selected["qualification_root"]
    ):
        raise QualificationSealError("QUALIFICATION_GATE_INCOMPLETE")
    return selected


def write_qualification_seal(
    run_root: Path, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    root = run_root.resolve()
    try:
        manifest = verify_run_artifacts(root)
    except RunManifestError:
        raise QualificationSealError("BASE_RUN_MANIFEST_INVALID") from None
    selected = _validate_evidence(evidence)
    body = {
        "schema_version": "membind.saturated-fixed-work.qualification-seal.v1",
        "status": "QUALIFIED",
        "qualification_passed": True,
        "base_run_inventory_payload_sha256": manifest[
            "inventory_payload_sha256"
        ],
        "evidence_sha256": _hash(selected),
        "evidence": selected,
    }
    body["payload_sha256"] = _hash(body)
    path = root / "qualification/qualification_seal.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise QualificationSealError("QUALIFICATION_SEAL_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return body


def verify_qualification_seal(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    path = root / "qualification/qualification_seal.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise QualificationSealError("QUALIFICATION_SEAL_UNREADABLE") from None
    if not isinstance(value, dict):
        raise QualificationSealError("QUALIFICATION_SEAL_INVALID")
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != _hash(candidate):
        raise QualificationSealError("QUALIFICATION_SEAL_HASH_INVALID")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, Mapping) or candidate.get("evidence_sha256") != _hash(
        evidence
    ):
        raise QualificationSealError("QUALIFICATION_EVIDENCE_HASH_INVALID")
    _validate_evidence(evidence)
    try:
        manifest = verify_run_artifacts(root)
    except RunManifestError:
        raise QualificationSealError("BASE_RUN_MANIFEST_INVALID") from None
    if (
        candidate.get("status") != "QUALIFIED"
        or candidate.get("qualification_passed") is not True
        or candidate.get("base_run_inventory_payload_sha256")
        != manifest["inventory_payload_sha256"]
    ):
        raise QualificationSealError("QUALIFICATION_SEAL_BASE_MISMATCH")
    return {
        "schema_version": "membind.saturated-fixed-work.qualification-verification.v1",
        "verified": True,
        "qualification_passed": True,
        "payload_sha256": observed,
        "base_run_inventory_payload_sha256": manifest[
            "inventory_payload_sha256"
        ],
    }


__all__ = [
    "QualificationSealError",
    "verify_qualification_seal",
    "write_qualification_seal",
]
