"""Single-use, read-only authority for the bounded retry-005 diagnosis."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from .artifacts import payload_sha256


AUTHORITY_SCHEMA = "membind.paper-eval-v3.s4-edge-diagnosis-authority.v1"
CONSUMPTION_SCHEMA = (
    "membind.paper-eval-v3.s4-edge-diagnosis-authority-consumption.v1"
)
OUTPUT_RELPATH = (
    "artifacts/paper_eval/native/S4_EDGE_IDENTITY_DIAGNOSIS_RETRY_005.json"
)
PROMPT_CACHE_RELPATH = (
    "runtime/private/s4-d0-remap-07741c45-20260815-005/prompt.jsonl"
)
EMBEDDING_CACHE_RELPATH = (
    "runtime/private/s4-d0-remap-07741c45-20260815-005/embedding.jsonl"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_FIELDS = {
    "capture_canonical_graph_sha256",
    "capture_phase_result_sha256",
    "dataset_sha256",
    "embedding_cache_sha256",
    "prompt_cache_sha256",
    "replay_checkpoint_sha256",
    "replay_events_sha256",
    "replay_phase_result_sha256",
    "split_sha256",
}
_SOURCE_FIELDS = {
    "authority",
    "controller",
    "diagnosis",
    "dry_run",
    "production",
    "test",
}
_AUTHORITY = {
    "read_only_source_7_diagnosis_authorized": True,
    "cleanup_authorized": False,
    "retry_006_authorized": False,
    "qualification_authorized": False,
    "s5_authorized": False,
}


def _sha(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} is not a lowercase SHA256")
    return value


def _hash_inventory(
    value: object, *, fields: set[str], label: str
) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} hash inventory shape drift")
    return {
        name: _sha(value[name], field=f"{label} {name}")
        for name in sorted(fields)
    }


def build_diagnosis_authority(
    *,
    source_hash: str,
    episode_manifest_sha256: str,
    evidence_sha256: Mapping[str, str],
    source_sha256: Mapping[str, str],
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": AUTHORITY_SCHEMA,
        "status": "AUTHORIZED_SINGLE_USE_READ_ONLY",
        "execution_identity": {
            "attempt_id": "005",
            "history_id": "07741c45",
            "namespace": "pev3-s4-d0-replay-20260815-005",
            "replay_run_id": "s4-d0-replay-20260815-005",
            "source_sequence": 7,
            "source_hash": _sha(source_hash, field="source-7 hash"),
            "episode_manifest_sha256": _sha(
                episode_manifest_sha256, field="episode manifest"
            ),
        },
        "evidence_sha256": _hash_inventory(
            evidence_sha256, fields=_EVIDENCE_FIELDS, label="evidence"
        ),
        "source_sha256": _hash_inventory(
            source_sha256, fields=_SOURCE_FIELDS, label="source"
        ),
        "private_cache": {
            "prompt_relpath": PROMPT_CACHE_RELPATH,
            "embedding_relpath": EMBEDDING_CACHE_RELPATH,
            "read_only": True,
            "reportable_contents": False,
        },
        "output_relpath": OUTPUT_RELPATH,
        "authority": dict(_AUTHORITY),
    }
    body["authority_sha256"] = payload_sha256(body)
    return body


def verify_diagnosis_authority(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("diagnosis authority is not a mapping")
    selected = deepcopy(dict(value))
    declared_hash = _sha(
        selected.pop("authority_sha256", None), field="diagnosis authority"
    )
    if payload_sha256(selected) != declared_hash:
        raise ValueError("diagnosis authority hash drift")
    selected["authority_sha256"] = declared_hash
    identity = selected.get("execution_identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "attempt_id",
        "episode_manifest_sha256",
        "history_id",
        "namespace",
        "replay_run_id",
        "source_hash",
        "source_sequence",
    }:
        raise ValueError("diagnosis execution identity shape drift")
    if {
        key: identity[key]
        for key in (
            "attempt_id",
            "history_id",
            "namespace",
            "replay_run_id",
            "source_sequence",
        )
    } != {
        "attempt_id": "005",
        "history_id": "07741c45",
        "namespace": "pev3-s4-d0-replay-20260815-005",
        "replay_run_id": "s4-d0-replay-20260815-005",
        "source_sequence": 7,
    }:
        raise ValueError("diagnosis execution identity drift")
    _sha(identity.get("source_hash"), field="source-7 hash")
    _sha(identity.get("episode_manifest_sha256"), field="episode manifest")
    _hash_inventory(
        selected.get("evidence_sha256"),
        fields=_EVIDENCE_FIELDS,
        label="evidence",
    )
    _hash_inventory(
        selected.get("source_sha256"), fields=_SOURCE_FIELDS, label="source"
    )
    if (
        selected.get("schema_version") != AUTHORITY_SCHEMA
        or selected.get("status") != "AUTHORIZED_SINGLE_USE_READ_ONLY"
        or selected.get("private_cache")
        != {
            "prompt_relpath": PROMPT_CACHE_RELPATH,
            "embedding_relpath": EMBEDDING_CACHE_RELPATH,
            "read_only": True,
            "reportable_contents": False,
        }
        or selected.get("output_relpath") != OUTPUT_RELPATH
        or selected.get("authority") != _AUTHORITY
    ):
        raise ValueError("diagnosis authority scope drift")
    return selected


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o664)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_diagnosis_authority_exclusive(
    path: Path, authority: Mapping[str, Any]
) -> None:
    _write_exclusive(Path(path), verify_diagnosis_authority(authority))


def consume_diagnosis_authority(
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
    output_path: Path,
) -> dict[str, Any]:
    selected = verify_diagnosis_authority(authority)
    body: dict[str, Any] = {
        "schema_version": CONSUMPTION_SCHEMA,
        "status": "CONSUMED",
        "consumed_action": "S4_RETRY_005_SOURCE_7_READ_ONLY_DIAGNOSIS",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_sha256": selected["authority_sha256"],
        "source_hash": selected["execution_identity"]["source_hash"],
        "episode_manifest_sha256": selected["execution_identity"][
            "episode_manifest_sha256"
        ],
    }
    body["consumption_sha256"] = payload_sha256(body)
    _write_exclusive(Path(output_path), body)
    return body


def verify_diagnosis_authority_consumption(
    value: Mapping[str, Any],
    *,
    authority: Mapping[str, Any],
    authority_file_sha256: str,
) -> dict[str, Any]:
    selected_authority = verify_diagnosis_authority(authority)
    if not isinstance(value, Mapping):
        raise ValueError("diagnosis authority consumption is not a mapping")
    selected = deepcopy(dict(value))
    declared_hash = _sha(
        selected.pop("consumption_sha256", None), field="authority consumption"
    )
    if payload_sha256(selected) != declared_hash:
        raise ValueError("diagnosis authority consumption hash drift")
    selected["consumption_sha256"] = declared_hash
    expected = {
        "schema_version": CONSUMPTION_SCHEMA,
        "status": "CONSUMED",
        "consumed_action": "S4_RETRY_005_SOURCE_7_READ_ONLY_DIAGNOSIS",
        "authority_file_sha256": _sha(
            authority_file_sha256, field="authority file"
        ),
        "authority_sha256": selected_authority["authority_sha256"],
        "source_hash": selected_authority["execution_identity"]["source_hash"],
        "episode_manifest_sha256": selected_authority["execution_identity"][
            "episode_manifest_sha256"
        ],
        "consumption_sha256": declared_hash,
    }
    if selected != expected:
        raise ValueError("diagnosis authority consumption binding drift")
    return selected
