"""Reproducible source identity for the shared 8B experiment stack."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
from typing import Any


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
    payload = {
        "name": name,
        "path": str(resolved),
        "files": files,
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


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
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = ["implementation_bundle"]
