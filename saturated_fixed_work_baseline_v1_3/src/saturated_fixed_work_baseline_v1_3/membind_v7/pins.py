"""Pin and sealed-source verification helpers for R0."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Iterable, Mapping


NATIVE_SUBJECT_PIN = "2832d94b56db72fcf993154bde47e16b31ade724"
V7_HARNESS_PIN = "bddc1c5627a2ed49d8503a8cbab2d457f022f543"
# Backward-compatible name used by older callers.  It identifies the native
# subject, never the whole repository containing the V7 harness.
MEMBIND_PIN = NATIVE_SUBJECT_PIN
GRAPHITI_PIN = "021d3a57d511f21b10adaf7fa923bd5c1fce5e9d"
GRAPHITI_VERSION = "0.29.3"

NATIVE_SUBJECT_PATHS = (
    "membind-validation/src/graphiti_native.py",
    "membind-validation/src/graphiti_membind.py",
    "membind-validation/src/instrumentation.py",
    "saturated_fixed_work_baseline_v1_3/src/saturated_fixed_work_baseline_v1_3/membind_adapter.py",
)
# Do not bind the entire validation harness tree: it intentionally evolves
# independently of the Native subject.  Native semantic files are listed
# explicitly in ``NATIVE_SUBJECT_PATHS`` above; callers can opt into an
# additional subtree when they have a genuinely isolated subject tree.
NATIVE_SUBJECT_TREES: tuple[str, ...] = ()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_blob_sha256(repo: Path, revision: str, path: str) -> str:
    content = subprocess.run(
        ["git", "-C", str(repo), "show", f"{revision}:{path}"],
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(content).hexdigest()


def _changed_paths(repo: Path, expected: str) -> set[str]:
    committed = _git(repo, "diff", "--name-only", f"{expected}..HEAD")
    working = _git(repo, "diff", "--name-only")
    staged = _git(repo, "diff", "--cached", "--name-only")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard")
    # Packaging regenerates ``*.egg-info`` metadata as a side effect of
    # installing the validation harness.  These files are not native subject
    # source and must not invalidate the independent semantic pin.
    return {
        path
        for value in (committed, working, staged, untracked)
        for path in value.splitlines()
        if path and ".egg-info/" not in path
    }


def _tree_identity(repo: Path, revision: str, path: str) -> str:
    return _git(repo, "rev-parse", f"{revision}:{path}")


def verify_membind_pin(
    repo: str | Path,
    *,
    expected: str = NATIVE_SUBJECT_PIN,
    native_paths: Iterable[str] = NATIVE_SUBJECT_PATHS,
    native_trees: Iterable[str] = NATIVE_SUBJECT_TREES,
) -> dict[str, object]:
    """Verify native subject identity independently from the V7 harness.

    The repository may advance with observer, certificate, and artifact code.
    Only changes to the explicitly listed native semantic files invalidate the
    subject pin; a harness commit therefore cannot self-block R0.
    """
    root = Path(repo)
    actual = _git(root, "rev-parse", "HEAD")
    paths = tuple(native_paths)
    trees = tuple(native_trees)
    subject_exists = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{expected}^{{commit}}"],
        check=False,
        capture_output=True,
    ).returncode == 0
    changed = _changed_paths(root, expected) if subject_exists else set()
    native_changed = sorted(
        path
        for path in changed
        if path in paths or any(path == tree or path.startswith(f"{tree}/") for tree in trees)
    )
    expected_hashes: dict[str, str] = {}
    actual_hashes: dict[str, str] = {}
    hash_mismatches: dict[str, dict[str, str | None]] = {}
    expected_trees: dict[str, str] = {}
    actual_trees: dict[str, str] = {}
    tree_mismatches: dict[str, dict[str, str | None]] = {}
    if subject_exists:
        for path in paths:
            expected_hash = _git_blob_sha256(root, expected, path)
            actual_path = root / path
            actual_hash = sha256_file(actual_path) if actual_path.exists() else "MISSING"
            expected_hashes[path] = expected_hash
            actual_hashes[path] = actual_hash
            if expected_hash != actual_hash:
                hash_mismatches[path] = {"expected": expected_hash, "actual": actual_hash}
        for path in trees:
            expected_tree = _tree_identity(root, expected, path)
            try:
                actual_tree = _tree_identity(root, "HEAD", path)
            except subprocess.CalledProcessError:
                actual_tree = "MISSING"
            expected_trees[path] = expected_tree
            actual_trees[path] = actual_tree
            if expected_tree != actual_tree:
                tree_mismatches[path] = {"expected": expected_tree, "actual": actual_tree}
    native_match = subject_exists and not native_changed and not hash_mismatches and not tree_mismatches
    return {
        "expected": expected,
        "actual": actual,
        "head_match": actual == expected,
        "native_subject_pin": expected,
        "harness_pin": V7_HARNESS_PIN,
        "harness_head": actual,
        "subject_commit_present": subject_exists,
        "native_subject_paths": list(paths),
        "native_subject_trees": list(trees),
        "native_changed_paths": native_changed,
        "expected_source_hashes": expected_hashes,
        "actual_source_hashes": actual_hashes,
        "source_hash_mismatches": hash_mismatches,
        "expected_tree_identities": expected_trees,
        "actual_tree_identities": actual_trees,
        "tree_identity_mismatches": tree_mismatches,
        "native_subject_match": native_match,
        "membind_pin_match": native_match,
        "match": native_match,
    }


def verify_source_hashes(paths: Mapping[str, str | Path], expected: Mapping[str, str]) -> dict[str, object]:
    actual = {name: sha256_file(path) for name, path in paths.items()}
    mismatches = {name: {"expected": expected.get(name), "actual": value} for name, value in actual.items() if expected.get(name) != value}
    return {"match": not mismatches, "actual": actual, "mismatches": mismatches}


__all__ = [
    "GRAPHITI_PIN",
    "GRAPHITI_VERSION",
    "MEMBIND_PIN",
    "NATIVE_SUBJECT_PATHS",
    "NATIVE_SUBJECT_PIN",
    "NATIVE_SUBJECT_TREES",
    "V7_HARNESS_PIN",
    "sha256_file",
    "verify_membind_pin",
    "verify_source_hashes",
]
