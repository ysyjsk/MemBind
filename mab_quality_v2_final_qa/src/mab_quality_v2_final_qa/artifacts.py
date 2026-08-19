"""Atomic, append-only artifacts owned by the new QA directory."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_paths(paths: Iterable[str | Path]) -> dict[str, Any]:
    """Hash regular files under protected roots without writing anywhere."""

    manifest: dict[str, str] = {}
    roots: list[str] = []
    for raw_root in paths:
        root = Path(raw_root).resolve()
        roots.append(str(root))
        if root.is_file():
            manifest[str(root)] = file_sha256(root)
            continue
        if not root.exists():
            continue
        for child in sorted(item for item in root.rglob("*") if item.is_file()):
            manifest[str(child.resolve())] = file_sha256(child)
    body = {"roots": roots, "files": manifest}
    body["snapshot_sha256"] = canonical_sha256(body)
    return body


def assert_snapshot_unchanged(snapshot: Mapping[str, Any]) -> None:
    files = snapshot.get("files")
    if not isinstance(files, Mapping):
        raise TypeError("protected snapshot is malformed")
    for name, expected in files.items():
        path = Path(str(name))
        if not path.exists() or file_sha256(path) != expected:
            raise RuntimeError(f"PROTECTED_ARTIFACT_CHANGED:{path}")


def atomic_write_bytes(path: str | Path, payload: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=str(destination.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


class ArtifactStore:
    """Own a fresh run root and expose only atomic/append-only writes."""

    def __init__(
        self, root: str | Path, *, protected_roots: Iterable[str | Path] = ()
    ) -> None:
        self.root = Path(root).resolve()
        protected = [Path(value).resolve() for value in protected_roots]
        if any(self.root == path or path in self.root.parents for path in protected):
            raise ValueError("MAB artifact root overlaps a protected historical root")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str | Path) -> Path:
        candidate = (self.root / relative).resolve()
        if self.root != candidate and self.root not in candidate.parents:
            raise ValueError("artifact path escapes the owned run root")
        return candidate

    def write_json(self, relative: str | Path, value: Any) -> Path:
        destination = self.path(relative)
        atomic_write_json(destination, value)
        return destination

    def append_jsonl(self, relative: str | Path, value: Mapping[str, Any]) -> Path:
        destination = self.path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        line = (
            json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        with destination.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return destination

    def read_jsonl(self, relative: str | Path) -> list[dict[str, Any]]:
        destination = self.path(relative)
        if not destination.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            destination.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"invalid JSONL at {destination}:{line_number}"
                ) from error
            if not isinstance(item, dict):
                raise TypeError(
                    f"JSONL row is not an object at {destination}:{line_number}"
                )
            rows.append(item)
        return rows

    def write_manifest(
        self, relative: str | Path, body: Mapping[str, Any]
    ) -> dict[str, Any]:
        content = dict(body)
        content["payload_sha256"] = canonical_sha256(content)
        self.write_json(relative, content)
        return content


__all__ = [
    "ArtifactStore",
    "assert_snapshot_unchanged",
    "atomic_write_json",
    "file_sha256",
    "snapshot_paths",
]
