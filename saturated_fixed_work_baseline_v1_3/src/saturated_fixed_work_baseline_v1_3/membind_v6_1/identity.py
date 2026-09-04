"""Reproducible source identity for the shared 8B experiment stack."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_members(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        candidate
        for candidate in path.rglob("*.py")
        if candidate.is_file() and "__pycache__" not in candidate.parts
    )


def _component(name: str, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    members = _source_members(resolved)
    if not members:
        raise FileNotFoundError(f"source component has no Python files: {resolved}")
    files = {
        str(member.relative_to(resolved) if resolved.is_dir() else member.name): _sha256(member)
        for member in members
    }
    # Hash only logical component identity and relative file content.  The
    # absolute checkout path is retained below as locator metadata, because it
    # must not make identical source trees hash differently across worktrees.
    hashed_payload = {
        "name": name,
        "files": files,
    }
    payload_sha256 = hashlib.sha256(
        json.dumps(hashed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **hashed_payload,
        "path": str(resolved),
        "payload_sha256": payload_sha256,
    }


def _module_source(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        raise ModuleNotFoundError(name)
    origin = Path(spec.origin).resolve()
    if spec.submodule_search_locations:
        return Path(next(iter(spec.submodule_search_locations))).resolve()
    return origin


def implementation_bundle(runner: str | Path) -> dict[str, Any]:
    """Hash every source component that can alter a measured attempt."""

    package_root = Path(__file__).resolve().parent
    project_root = package_root.parents[3]
    components = [
        _component("membind_v6_1", package_root),
        _component("runner", Path(runner)),
        _component(
            "mab8192_adapter",
            project_root
            / "mab_quality_v2_final_qa/src/"
            "mab_quality_v2_final_qa/mab8192_adapter.py",
        ),
        _component(
            "attempt_preparation",
            project_root / "scripts/local_runtime_8b_dual/prepare_measured_attempt.py",
        ),
        _component(
            "qa_resume",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/"
            "run_mab_v13_qa_resume.py",
        ),
        _component(
            "qa_core",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/run_mab_v13_live.py",
        ),
        _component(
            "formal_manifest",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/"
            "formal_three_arm_harness.py",
        ),
        _component(
            "formal_runner",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/run_formal_three_arm.py",
        ),
        _component(
            "formal_finalizer",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/"
            "finalize_formal_three_arm.py",
        ),
        _component(
            "qualification_runner",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/"
            "run_upstream_l2_qualification.py",
        ),
        _component(
            "qualification_finalizer",
            project_root
            / "saturated_fixed_work_baseline_v1_3/scripts/"
            "finalize_upstream_qualification.py",
        ),
        _component(
            "mab_live_runner",
            project_root
            / "saturated_fixed_work_baseline_v1_3/src/"
            "saturated_fixed_work_baseline_v1_3/mab_live_runner.py",
        ),
        _component(
            "artifact_materializer",
            project_root
            / "saturated_fixed_work_baseline_v1_3/src/"
            "saturated_fixed_work_baseline_v1_3/artifact_materializer.py",
        ),
    ]
    for module_name in (
        "graphiti_core",
        "graphiti_native",
        "native_characterization_instrumentation",
        "native_characterization_tracing",
        "live_outputs",
        "mab_quality_v2_final_qa",
        "paper_eval",
    ):
        components.append(_component(module_name, _module_source(module_name)))
    versions = {}
    for distribution in ("graphiti-core", "neo4j", "openai", "pydantic"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "NOT_INSTALLED"
    payload = {
        "schema_version": "membind.v6.1.implementation-bundle.v1",
        "components": components,
        "distribution_versions": versions,
    }
    hashed_payload = {
        **payload,
        "components": [
            {key: value for key, value in component.items() if key != "path"}
            for component in components
        ],
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(hashed_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def source_epoch_errors(
    *,
    expected_head: str | None,
    expected_source_bundle_sha256: str | None,
    observed: Mapping[str, Any],
) -> list[str]:
    """Return fail-closed violations for an immutable measured source epoch."""

    errors: list[str] = []
    observed_head = observed.get("git_head")
    dirty_paths = observed.get("dirty_paths")
    observed_bundle = observed.get("source_bundle_sha256")
    if expected_head and observed_head != expected_head:
        errors.append(f"HEAD drift: expected {expected_head}, observed {observed_head}")
    if dirty_paths:
        errors.append(f"source tree is dirty: {dirty_paths}")
    if expected_source_bundle_sha256 and observed_bundle != expected_source_bundle_sha256:
        errors.append(
            "source bundle drift: "
            f"expected {expected_source_bundle_sha256}, observed {observed_bundle}"
        )
    return errors


def current_source_epoch(runner: str | Path, *, root: str | Path | None = None) -> dict[str, Any]:
    """Capture the current git and implementation identity for a checkout."""

    cwd = Path(root).resolve() if root is not None else None
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty_paths = subprocess.run(
        ["git", "status", "--short"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    bundle = implementation_bundle(runner)
    return {
        "git_head": head,
        "dirty_paths": dirty_paths,
        "source_bundle_sha256": bundle["payload_sha256"],
    }


def require_source_epoch(
    runner: str | Path,
    *,
    expected_head: str,
    expected_source_bundle_sha256: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Require clean checkout and exact HEAD/source bundle before measurement."""

    observed = current_source_epoch(runner, root=root)
    errors = source_epoch_errors(
        expected_head=expected_head,
        expected_source_bundle_sha256=expected_source_bundle_sha256,
        observed=observed,
    )
    if errors:
        raise RuntimeError("source epoch invalid: " + "; ".join(errors))
    return observed


__all__ = [
    "implementation_bundle",
    "source_epoch_errors",
    "current_source_epoch",
    "require_source_epoch",
]
