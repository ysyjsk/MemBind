"""Append-only materialization of run identities and frozen protocol inputs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
from pathlib import Path
from typing import Any, Mapping

from .audit import collect_repository_audit, validate_repository_identity
from .dataset import freeze_development_dataset, load_and_validate_qa_inventory
from .live import build_formal_plan
from .reuse import collect_reuse_compatibility


class RunManifestError(ValueError):
    """Run initialization or immutable manifest verification failed."""


_TDD_EVIDENCE_SCHEMA = "membind.saturated-fixed-work.tdd-evidence.v1"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _write_new(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    except FileExistsError:
        raise RunManifestError("RUN_ARTIFACT_ALREADY_EXISTS") from None
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write_new(
        path,
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        ).encode("utf-8")
        + b"\n",
    )


def _provider_envelope(repository_root: Path) -> dict[str, Any]:
    source = (
        repository_root
        / "paper-eval-v3/artifacts/paper_eval/membind_v31/PROVIDER_EXECUTION_ENVELOPE_XGRAMMAR_20260819.json"
    )
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunManifestError("PROVIDER_ENVELOPE_UNREADABLE") from None
    if not isinstance(value, dict):
        raise RunManifestError("PROVIDER_ENVELOPE_INVALID")
    candidate = dict(value)
    observed = candidate.pop("payload_sha256", None)
    if observed != _payload_hash(candidate):
        raise RunManifestError("PROVIDER_ENVELOPE_PAYLOAD_INVALID")
    return value


def _config_hashes(repository_root: Path) -> dict[str, Any]:
    protocol_root = repository_root / "saturated_fixed_work_baseline_v1_2"
    paths = (
        repository_root / "MemBind_SATURATED_FIXED_WORK_BASELINE_WORKPLAN_v1.2.md",
        protocol_root / "configs/protocol_v1_2.yaml",
        protocol_root / "configs/provider_envelope.json",
        protocol_root / "configs/resource_envelope.json",
        protocol_root / "configs/qa_contract.json",
    )
    if any(not path.is_file() for path in paths):
        raise RunManifestError("FROZEN_CONFIG_MISSING")
    return {
        "schema_version": "membind.saturated-fixed-work.config-hashes.v1",
        "files": {
            str(path.relative_to(repository_root)): _file_hash(path) for path in paths
        },
    }


def _validate_initial_run_root(root: Path) -> None:
    if not root.exists():
        return
    entries = tuple(root.iterdir())
    if not entries:
        return
    journal = root / "tdd_evidence.jsonl"
    if not journal.exists():
        raise RunManifestError("RUN_ROOT_ALREADY_INITIALIZED")
    allowed_names = {
        "tdd_evidence.jsonl",
        "test_summary.json",
        "STOP_WITH_EXTERNAL_DIAGNOSIS.json",
        "service_evidence",
    }
    if any(entry.name not in allowed_names for entry in entries):
        raise RunManifestError("RUN_ROOT_ALREADY_INITIALIZED")
    if journal.is_symlink() or not journal.is_file():
        raise RunManifestError("TDD_EVIDENCE_INVALID")
    service_evidence = root / "service_evidence"
    if service_evidence.exists() and (
        service_evidence.is_symlink() or not service_evidence.is_dir()
    ):
        raise RunManifestError("PRE_MANIFEST_SERVICE_EVIDENCE_INVALID")
    if service_evidence.is_dir() and any(
        path.is_symlink() or not path.is_file()
        for path in service_evidence.iterdir()
    ):
        raise RunManifestError("PRE_MANIFEST_SERVICE_EVIDENCE_INVALID")
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunManifestError("TDD_EVIDENCE_INVALID") from None
    if not rows or any(
        not isinstance(row, dict)
        or row.get("schema_version") != _TDD_EVIDENCE_SCHEMA
        or not isinstance(row.get("stage"), str)
        or not row["stage"]
        or row.get("event") not in {"RED", "GREEN"}
        or isinstance(row.get("exit_code"), bool)
        or not isinstance(row.get("exit_code"), int)
        for row in rows
    ):
        raise RunManifestError("TDD_EVIDENCE_INVALID")


def initialize_run_artifacts(
    *,
    repository_root: Path,
    run_root: Path,
    run_id: str,
    resource_envelope: Mapping[str, Any],
) -> dict[str, Any]:
    root = run_root.resolve()
    _validate_initial_run_root(root)
    root.mkdir(parents=True, exist_ok=True)
    audit = collect_repository_audit(repository_root)
    validate_repository_identity(audit)
    dataset = freeze_development_dataset(repository_root)
    qa = load_and_validate_qa_inventory(repository_root)
    qa_projection = {
        key: qa[key]
        for key in (
            "claim_scope",
            "source_path",
            "source_sha256",
            "question_count",
            "questions_per_history",
            "inventory_sha256",
            "inventory_file_sha256",
        )
    }
    audit_manifest = {
        **audit,
        "runner": {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "os": platform.platform(),
            "kernel": platform.release(),
            "python": platform.python_version(),
        },
        "dataset": dataset,
        "qa_inventory": qa_projection,
    }
    reuse = collect_reuse_compatibility(repository_root)
    if reuse.get("compatible") is not True:
        raise RunManifestError("REUSE_COMPATIBILITY_FAILED")
    plan = build_formal_plan(run_id)
    protocol = {
        "schema_version": "membind.saturated-fixed-work.protocol-manifest.v1",
        "protocol_version": "SATURATED_FIXED_WORK_CONSTRUCTION_PROTOCOL_V1_2",
        "run_id": run_id,
        "result_scope": "development / protocol-qualified / one run per method-history",
        "qualification_passed": False,
        "cache_isolation": "UNIQUE_REQUEST_CACHE_SALT_PER_BLOCK",
        "selection_rule": "FIRST_VALID_ATTEMPT",
        "formal_order": [
            {
                "ordinal": row.ordinal,
                "block_id": row.block_id,
                "history_id": row.history_id,
                "method": row.method.value,
                "attempt_ordinal": row.attempt_ordinal,
                "namespace": row.namespace,
                "cache_salt": row.cache_salt,
                "cache_salt_sha256": hashlib.sha256(
                    row.cache_salt.encode("ascii")
                ).hexdigest(),
            }
            for row in plan
        ],
    }
    provider = _provider_envelope(repository_root)
    resource = dict(resource_envelope)
    resource.setdefault(
        "schema_version", "membind.saturated-fixed-work.resource-envelope.v1"
    )
    configs = _config_hashes(repository_root)

    _write_json(root / "audit_manifest.json", audit_manifest)
    _write_json(root / "reuse_manifest.json", reuse)
    _write_json(root / "protocol_manifest.json", protocol)
    _write_json(root / "config_hashes.json", configs)
    _write_json(root / "provider_envelope.json", provider)
    _write_json(root / "resource_envelope.json", resource)
    _write_new(
        root / "RESOURCE_ENVELOPE_ID",
        (_payload_hash(resource) + "\n").encode("ascii"),
    )
    _write_new(root / "failed_attempts.jsonl", b"")
    (root / "service_evidence").mkdir(mode=0o700, exist_ok=True)

    inventory_names = (
        "audit_manifest.json",
        "reuse_manifest.json",
        "protocol_manifest.json",
        "config_hashes.json",
        "provider_envelope.json",
        "resource_envelope.json",
        "RESOURCE_ENVELOPE_ID",
        "failed_attempts.jsonl",
    )
    inventory = {
        "schema_version": "membind.saturated-fixed-work.run-manifest-inventory.v1",
        "run_id": run_id,
        "files": {name: _file_hash(root / name) for name in inventory_names},
    }
    inventory["payload_sha256"] = _payload_hash(inventory)
    _write_json(root / "run_manifest_inventory.json", inventory)
    return verify_run_artifacts(root)


def verify_run_artifacts(run_root: Path) -> dict[str, Any]:
    root = run_root.resolve()
    try:
        inventory = json.loads(
            (root / "run_manifest_inventory.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RunManifestError("RUN_MANIFEST_INVENTORY_UNREADABLE") from None
    if not isinstance(inventory, dict):
        raise RunManifestError("RUN_MANIFEST_INVENTORY_INVALID")
    candidate = dict(inventory)
    observed = candidate.pop("payload_sha256", None)
    if observed != _payload_hash(candidate):
        raise RunManifestError("RUN_MANIFEST_INVENTORY_HASH_INVALID")
    files = candidate.get("files")
    if not isinstance(files, dict) or any(
        not (root / name).is_file() or _file_hash(root / name) != expected
        for name, expected in files.items()
    ):
        raise RunManifestError("RUN_MANIFEST_HASH_MISMATCH")
    protocol = json.loads((root / "protocol_manifest.json").read_text(encoding="utf-8"))
    return {
        "verified": True,
        "run_id": candidate["run_id"],
        "formal_block_count": len(protocol["formal_order"]),
        "inventory_payload_sha256": observed,
    }


__all__ = [
    "RunManifestError",
    "initialize_run_artifacts",
    "verify_run_artifacts",
]
