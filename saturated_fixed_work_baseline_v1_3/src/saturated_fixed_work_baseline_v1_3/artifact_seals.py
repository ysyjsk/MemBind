"""Append-only construction/QA seal helpers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class SealError(ValueError):
    """A seal cannot be created or verified."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({key: child for key, child in value.items() if key != "seal_sha256"})).hexdigest()


def seal_construction_block(
    root: str | Path,
    *,
    identity: Mapping[str, Any],
    required_members: Sequence[str],
) -> dict[str, Any]:
    block_root = Path(root).resolve()
    if not isinstance(identity, Mapping) or not identity:
        raise SealError("seal identity is missing")
    if not isinstance(required_members, Sequence) or isinstance(required_members, (str, bytes)) or not required_members:
        raise SealError("seal members are missing")
    members: dict[str, str] = {}
    for raw_name in required_members:
        name = str(raw_name)
        path = (block_root / name).resolve()
        if block_root not in path.parents or not path.is_file():
            raise SealError(f"missing seal member: {name}")
        members[name] = _sha(path)
    seal = {
        "schema_version": "membind.v1.3.construction-seal.v1",
        "status": "CONSTRUCTION_SEALED",
        "identity": dict(identity),
        "members": members,
    }
    seal["seal_sha256"] = _payload_hash(seal)
    destination = block_root / "construction_seal.json"
    if destination.exists():
        raise SealError("construction seal already exists")
    destination.write_text(json.dumps(seal, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return seal


def verify_seal(root: str | Path, seal: Mapping[str, Any] | None = None) -> dict[str, Any]:
    block_root = Path(root).resolve()
    if seal is None:
        path = block_root / "construction_seal.json"
        if not path.is_file():
            raise SealError("construction seal is missing")
        try:
            seal = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SealError("construction seal is invalid") from exc
    if not isinstance(seal, Mapping) or seal.get("status") != "CONSTRUCTION_SEALED":
        raise SealError("construction seal status is invalid")
    if seal.get("seal_sha256") != _payload_hash(seal):
        raise SealError("construction seal hash mismatch")
    members = seal.get("members")
    if not isinstance(members, Mapping) or not members:
        raise SealError("construction seal members are invalid")
    for name, expected in members.items():
        path = (block_root / str(name)).resolve()
        if block_root not in path.parents or not path.is_file():
            raise SealError(f"missing seal member: {name}")
        if _sha(path) != expected:
            raise SealError(f"seal member hash mismatch: {name}")
    return {"status": "PASS", "member_count": len(members), "seal_sha256": str(seal["seal_sha256"])}


__all__ = ["SealError", "seal_construction_block", "verify_seal"]
