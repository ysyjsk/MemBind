"""Contract-level tests for the W=4 pilot command line entry point."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts/run_membind_v31_w4_pilot.py"
    spec = importlib.util.spec_from_file_location("membind_w4_pilot_cli_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_defaults_are_bounded_and_fresh_identity() -> None:
    module = _module()
    args = module._parser().parse_args([])
    assert args.run_id == "membind-v31-opt-w4-20260818-001"
    assert args.attempt_id is None
    assert args.output_root is None
    assert args.dry_run is False


def test_cli_implementation_identity_is_content_addressed(tmp_path: Path) -> None:
    module = _module()
    project = tmp_path / "paper-eval-v3"
    files = {
        "optimization_pilot": project / "src/paper_eval/membind_v31/optimization_pilot.py",
        "optimization_live": project / "src/paper_eval/membind_v31/optimization_live.py",
        "coordinator": project / "src/paper_eval/membind_v31/coordinator.py",
        "request_runtime": project / "src/paper_eval/membind_v31/request_runtime.py",
        "live_runtime": project / "src/paper_eval/membind_v31/live_runtime.py",
    }
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")
    first = module._implementation_identity(project)
    files["optimization_live"].write_text("changed", encoding="utf-8")
    second = module._implementation_identity(project)
    assert first != second


def test_legacy_env_loader_scopes_sibling_import_path() -> None:
    from paper_eval.membind_v31.production_executor import _default_env_loader

    repository_root = Path(__file__).parents[2]
    env_path = repository_root / "membind-validation/.env"
    before = list(sys.path)
    loaded = _default_env_loader(env_path)
    assert loaded
    assert sys.path == before
