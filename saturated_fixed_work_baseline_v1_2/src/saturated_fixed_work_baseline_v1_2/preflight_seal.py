"""Append-only L0 seal binding every prerequisite to immutable run evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .production_sampler import REQUIRED_PROBE_SOURCES, qualify_sampler_summary
from .run_manifest import RunManifestError, verify_run_artifacts


class PreflightSealError(ValueError):
    """L0 evidence is incomplete, mutable, or not bound to this run."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BOOLEAN_GATES = (
    "tests_all_green",
    "repository_identity_verified",
    "data_identity_verified",
    "provider_identity_verified",
    "qa_identity_verified",
    "historical_resource_match",
    "live_resource_envelope_verified",
)
_BOUND_PATHS = {
    "test_summary_sha256": "test_summary.json",
    "service_evidence_sha256": "service_evidence/l0_services.json",
    "warmup_evidence_sha256": "preflight/warmup_evidence.json",
    "idle_evidence_sha256": "preflight/idle_evidence.json",
    "sampler_qualification_sha256": "preflight/sampler_qualification.json",
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


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_UNREADABLE") from None


def _read_bound(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_INVALID") from None
    if not isinstance(value, dict):
        raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_INVALID")
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != _hash(candidate):
        raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_HASH_INVALID")
    return value


def _validate_evidence(root: Path, evidence: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(evidence)
    if any(selected.get(field) is not True for field in _BOOLEAN_GATES):
        raise PreflightSealError("PREFLIGHT_GATE_INCOMPLETE")
    for field, relative in _BOUND_PATHS.items():
        expected = selected.get(field)
        if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
            raise PreflightSealError("PREFLIGHT_BOUND_HASH_INVALID")
        if _file_hash(root / relative) != expected:
            raise PreflightSealError("PREFLIGHT_BOUND_ARTIFACT_CHANGED")

    test_summary = _read_bound(root / _BOUND_PATHS["test_summary_sha256"])
    if (
        test_summary.get("tests_all_green") is not True
        or test_summary.get("tdd_evidence_verified") is not True
        or test_summary.get("tdd_evidence_sha256")
        != _file_hash(root / "tdd_evidence.jsonl")
        or not isinstance(test_summary.get("required_tdd_stages"), list)
        or not test_summary["required_tdd_stages"]
    ):
        raise PreflightSealError("PREFLIGHT_TEST_GATE_INVALID")

    service = _read_bound(root / _BOUND_PATHS["service_evidence_sha256"])
    service_gates = (
        "construction_canary_passed",
        "embedding_canary_passed",
        "neo4j_canary_passed",
        "construction_cache_salt_passed",
        "embedding_cache_salt_passed",
        "no_other_clients",
    )
    if service.get("status") != "PASS" or any(
        service.get(field) is not True for field in service_gates
    ):
        raise PreflightSealError("PREFLIGHT_SERVICE_GATE_INVALID")

    warmup = _read_bound(root / _BOUND_PATHS["warmup_evidence_sha256"])
    if (
        warmup.get("status") != "PASS"
        or warmup.get("manifest_verified") is not True
        or warmup.get("disjoint_from_formal_data") is not True
        or warmup.get("construction_warmup_passed") is not True
        or warmup.get("embedding_warmup_passed") is not True
    ):
        raise PreflightSealError("PREFLIGHT_WARMUP_GATE_INVALID")

    idle = _read_bound(root / _BOUND_PATHS["idle_evidence_sha256"])
    samples = idle.get("samples")
    if (
        idle.get("status") != "PASS"
        or idle.get("all_services_idle") is not True
        or idle.get("required_consecutive_samples") != 2
        or not isinstance(samples, list)
        or len(samples) != 2
        or any(not isinstance(row, Mapping) or row.get("idle") is not True for row in samples)
    ):
        raise PreflightSealError("PREFLIGHT_IDLE_GATE_INVALID")

    sampler = _read_bound(root / _BOUND_PATHS["sampler_qualification_sha256"])
    qualified = qualify_sampler_summary(sampler.get("summary", {}))
    if (
        sampler.get("status") != "PASS"
        or sampler.get("formal_run_authorized") is not True
        or sampler.get("failed_gates") != []
        or sampler.get("required_sources") != list(REQUIRED_PROBE_SOURCES)
        or qualified.get("formal_run_authorized") is not True
    ):
        raise PreflightSealError("PREFLIGHT_SAMPLER_GATE_INVALID")
    return selected


def write_preflight_seal(
    run_root: Path, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    root = run_root.resolve()
    path = root / "preflight/preflight_seal.json"
    if path.exists():
        raise PreflightSealError("PREFLIGHT_SEAL_ALREADY_EXISTS")
    try:
        manifest = verify_run_artifacts(root)
    except RunManifestError:
        raise PreflightSealError("BASE_RUN_MANIFEST_INVALID") from None
    selected = _validate_evidence(root, evidence)
    body = {
        "schema_version": "membind.saturated-fixed-work.preflight-seal.v1",
        "status": "PASS",
        "preflight_passed": True,
        "formal_run_authorized": True,
        "base_run_inventory_payload_sha256": manifest[
            "inventory_payload_sha256"
        ],
        "evidence_sha256": _hash(selected),
        "evidence": selected,
    }
    body["payload_sha256"] = _hash(body)
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise PreflightSealError("PREFLIGHT_SEAL_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return body


def verify_preflight_seal(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    try:
        value = json.loads(
            (root / "preflight/preflight_seal.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreflightSealError("PREFLIGHT_SEAL_UNREADABLE") from None
    if not isinstance(value, dict):
        raise PreflightSealError("PREFLIGHT_SEAL_INVALID")
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != _hash(candidate):
        raise PreflightSealError("PREFLIGHT_SEAL_HASH_INVALID")
    evidence = candidate.get("evidence")
    if (
        not isinstance(evidence, Mapping)
        or candidate.get("evidence_sha256") != _hash(evidence)
    ):
        raise PreflightSealError("PREFLIGHT_EVIDENCE_HASH_INVALID")
    _validate_evidence(root, evidence)
    try:
        manifest = verify_run_artifacts(root)
    except RunManifestError:
        raise PreflightSealError("BASE_RUN_MANIFEST_INVALID") from None
    if (
        candidate.get("status") != "PASS"
        or candidate.get("preflight_passed") is not True
        or candidate.get("formal_run_authorized") is not True
        or candidate.get("base_run_inventory_payload_sha256")
        != manifest["inventory_payload_sha256"]
    ):
        raise PreflightSealError("PREFLIGHT_SEAL_BASE_MISMATCH")
    return {
        "schema_version": "membind.saturated-fixed-work.preflight-verification.v1",
        "verified": True,
        "preflight_passed": True,
        "payload_sha256": observed,
    }


__all__ = [
    "PreflightSealError",
    "verify_preflight_seal",
    "write_preflight_seal",
]
