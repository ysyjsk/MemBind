"""CLI contract for explicit executor injection into v3.1 orchestration."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_membind_v31_orchestration.py"


def _module():
    spec = importlib.util.spec_from_file_location("run_membind_v31_orchestration", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_requires_explicit_executor_factory_and_forwards_only_paths(tmp_path, monkeypatch) -> None:
    module = _module()
    hooks = object()
    observed = {}

    def fake_load(value):
        observed["entrypoint"] = value
        return hooks

    def fake_run(**kwargs):
        observed["run"] = kwargs
        return {
            "status": "PASS",
            "attempt_id": "v31-attempt-dev-001",
            "payload_sha256": "a" * 64,
        }

    monkeypatch.setattr(module, "load_executor_hooks", fake_load)
    monkeypatch.setattr(
        module,
        "run_v31_orchestration",
        fake_run,
    )

    assert module.main(
        [
            "--control-root",
            str(tmp_path / "control"),
            "--attempt-root",
            str(tmp_path / "attempt"),
            "--attempt-id",
            "v31-attempt-dev-001",
            "--executor-factory",
            "fake.module:build_hooks",
            "--formal-block-limit",
            "4",
        ]
    ) == 0
    assert observed["entrypoint"] == "fake.module:build_hooks"
    assert observed["run"]["hooks"] is hooks
    assert observed["run"]["control_root"] == tmp_path / "control"
    assert observed["run"]["attempt_root"] == tmp_path / "attempt"
    assert observed["run"]["formal_block_limit"] == 4


def test_cli_accepts_smoke_only_checkpoint_mode(tmp_path, monkeypatch) -> None:
    module = _module()
    observed = {}

    monkeypatch.setattr(module, "load_executor_hooks", lambda _value: object())

    def fake_run(**kwargs):
        observed["run"] = kwargs
        return {
            "status": "SMOKE_PASS_PROBE_REQUIRED",
            "attempt_id": "v31-attempt-dev-001",
            "payload_sha256": "a" * 64,
        }

    monkeypatch.setattr(module, "run_v31_orchestration", fake_run)

    assert module.main(
        [
            "--control-root",
            str(tmp_path / "control"),
            "--attempt-root",
            str(tmp_path / "attempt"),
            "--attempt-id",
            "v31-attempt-dev-001",
            "--executor-factory",
            "fake.module:build_hooks",
            "--formal-block-limit",
            "0",
        ]
    ) == 0
    assert observed["run"]["formal_block_limit"] == 0
