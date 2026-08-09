"""Construction checkpoint manifest and non-secret vLLM launch identity tools."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_FLAGS = {
    "--api-key",
    "--admin-key",
    "--hf-token",
    "--huggingface-token",
}


def _normalized_files(files: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise TypeError("manifest entries must be objects")
        raw_path = str(item.get("path") or "")
        path = PurePosixPath(raw_path)
        if not raw_path or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"manifest path must be relative: {raw_path!r}")
        canonical_path = path.as_posix()
        if canonical_path in seen:
            raise ValueError(f"duplicate manifest path: {canonical_path}")
        seen.add(canonical_path)
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ValueError(f"invalid file size for {canonical_path}")
        sha256 = str(item.get("sha256") or "")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError(f"file hash must be a lowercase SHA256: {canonical_path}")
        normalized.append(
            {"path": canonical_path, "size": int(size), "sha256": sha256}
        )
    return sorted(normalized, key=lambda item: item["path"])


def manifest_fingerprint(files: Iterable[dict[str, Any]]) -> str:
    """Hash a canonical relative-path/size/content-hash file manifest."""

    encoded = json.dumps(
        _normalized_files(files),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_directory_manifest(
    root: str | Path,
    *,
    expected_paths: Sequence[str],
) -> dict[str, Any]:
    """Hash exactly the expected checkpoint files and report missing entries."""

    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    requested = _normalized_files(
        {"path": path, "size": 0, "sha256": "0" * 64}
        for path in expected_paths
    )
    files = []
    missing = []
    for item in requested:
        path = root / item["path"]
        if not path.is_file():
            missing.append(item["path"])
            continue
        files.append(
            {
                "path": item["path"],
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "root_realpath": str(root),
        "files": files,
        "missing_paths": missing,
        "manifest_fingerprint": manifest_fingerprint(files),
    }


def sanitize_vllm_argv(argv: Sequence[str]) -> list[str]:
    """Remove authentication options while retaining all runtime semantics."""

    safe = []
    skip_next = False
    for raw in argv:
        value = str(raw)
        if skip_next:
            skip_next = False
            continue
        name = value.split("=", 1)[0].casefold()
        if name in _SECRET_FLAGS:
            skip_next = "=" not in value
            continue
        safe.append(value)
    return safe


def launch_fingerprint(argv: Sequence[str]) -> str:
    """Hash the ordered non-secret vLLM argv contract."""

    encoded = json.dumps(
        sanitize_vllm_argv(argv),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _argv_option(argv: Sequence[str], name: str) -> str | None:
    for index, value in enumerate(argv):
        if value == name and index + 1 < len(argv):
            return str(argv[index + 1])
        if value.startswith(name + "="):
            return value.split("=", 1)[1]
    return None


def collect_vllm_process_evidence(
    proc_root: str | Path = "/proc",
    *,
    port: int = 8000,
) -> list[dict[str, Any]]:
    """Collect non-secret argv evidence for vLLM processes on one port."""

    proc_root = Path(proc_root)
    results = []
    for process_dir in sorted(
        (path for path in proc_root.iterdir() if path.name.isdigit()),
        key=lambda path: int(path.name),
    ):
        try:
            argv = [
                item
                for item in process_dir.joinpath("cmdline")
                .read_bytes()
                .decode("utf-8")
                .split("\0")
                if item
            ]
        except (OSError, UnicodeDecodeError):
            continue
        if "vllm" not in " ".join(argv).casefold():
            continue
        if _argv_option(argv, "--port") != str(port):
            continue
        safe_argv = sanitize_vllm_argv(argv)
        safe_environment: dict[str, str] = {}
        try:
            entries = process_dir.joinpath("environ").read_bytes().split(b"\0")
        except OSError:
            entries = []
        allowed_environment = {
            "CUDA_VISIBLE_DEVICES",
            "VLLM_USE_V1",
            "VLLM_SERVER_DEV_MODE",
            "VLLM_WORKER_MULTIPROC_METHOD",
        }
        for entry in entries:
            key, separator, value = entry.partition(b"=")
            if not separator:
                continue
            name = key.decode("utf-8", errors="ignore")
            if name in allowed_environment:
                safe_environment[name] = value.decode("utf-8", errors="replace")
        results.append(
            {
                "pid": int(process_dir.name),
                "safe_argv": safe_argv,
                "launch_fingerprint": launch_fingerprint(safe_argv),
                "selected_environment": dict(sorted(safe_environment.items())),
            }
        )
    return results


def compare_deployment_manifest(
    expected: Iterable[dict[str, Any]],
    actual: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare checkpoint files by relative path, exact byte size, and SHA256."""

    expected_files = _normalized_files(expected)
    actual_files = _normalized_files(actual)
    expected_by_path = {item["path"]: item for item in expected_files}
    actual_by_path = {item["path"]: item for item in actual_files}
    missing = sorted(expected_by_path.keys() - actual_by_path.keys())
    unexpected = sorted(actual_by_path.keys() - expected_by_path.keys())
    changed = sorted(
        path
        for path in expected_by_path.keys() & actual_by_path.keys()
        if expected_by_path[path] != actual_by_path[path]
    )
    return {
        "exact_match": not missing and not unexpected and not changed,
        "expected_fingerprint": manifest_fingerprint(expected_files),
        "actual_fingerprint": manifest_fingerprint(actual_files),
        "missing_paths": missing,
        "unexpected_paths": unexpected,
        "changed_paths": changed,
    }
