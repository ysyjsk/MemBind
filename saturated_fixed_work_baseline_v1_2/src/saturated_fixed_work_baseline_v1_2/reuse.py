"""Explicit compatibility evidence for modules reused from the pinned checkout."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Mapping


def import_paper_eval_module(repository_root: Path, module: str) -> ModuleType:
    source = str(repository_root / "paper-eval-v3/src")
    if source not in sys.path:
        sys.path.insert(0, source)
    return importlib.import_module(module)


def import_validation_module(repository_root: Path, module: str) -> ModuleType:
    source = str(repository_root / "membind-validation/src")
    if source not in sys.path:
        sys.path.insert(0, source)
    return importlib.import_module(module)


def _definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def collect_reuse_compatibility(repository_root: Path) -> dict[str, object]:
    legacy = repository_root / "membind-validation/src"
    requirements: Mapping[str, tuple[Path, str]] = {
        "install_native_characterization_instrumentation": (
            legacy / "native_characterization_instrumentation.py",
            "install_native_characterization_instrumentation",
        ),
        "TraceRecorder": (legacy / "native_characterization_tracing.py", "TraceRecorder"),
        "DurableJsonlEnvelopeWriter": (
            legacy / "native_characterization_tracing.py",
            "DurableJsonlEnvelopeWriter",
        ),
        "interval_union_ns": (
            legacy / "native_characterization_tracing.py",
            "interval_union_ns",
        ),
        "exclusive_duration_ns": (
            legacy / "native_characterization_tracing.py",
            "exclusive_duration_ns",
        ),
        "critical_path_ns": (
            legacy / "native_characterization_tracing.py",
            "critical_path_ns",
        ),
        "install_c2_measurement_adapter": (
            legacy / "native_characterization_c2_measurement.py",
            "install_c2_measurement_adapter",
        ),
    }
    cache: dict[Path, set[str]] = {}
    symbols: dict[str, bool] = {}
    paths: dict[str, str] = {}
    for public_name, (path, definition) in requirements.items():
        cache.setdefault(path, _definitions(path))
        symbols[public_name] = definition in cache[path]
        paths[public_name] = str(path.relative_to(repository_root))
    return {
        "schema_version": "membind.saturated-fixed-work.reuse.v1",
        "symbols": symbols,
        "paths": paths,
        "compatible": all(symbols.values()),
    }


__all__ = [
    "collect_reuse_compatibility",
    "import_paper_eval_module",
    "import_validation_module",
]

