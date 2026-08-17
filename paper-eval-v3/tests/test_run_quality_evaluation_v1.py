"""TDD lifecycle contracts for the read-only Quality Evaluation v1 runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_quality_evaluation_v1.py"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_quality_evaluation_v1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_partitions_exact_u0_gate_before_a0_and_pc2() -> None:
    module = _module()
    targets = tuple(
        SimpleNamespace(method=method, history_id=history)
        for method in ("U0", "A0", "P(C=2)")
        for history in HISTORIES
    )

    u0, remaining = module._partition_targets(targets)

    assert [(value.method, value.history_id) for value in u0] == [
        ("U0", history) for history in HISTORIES
    ]
    assert [(value.method, value.history_id) for value in remaining] == [
        (method, history)
        for method in ("A0", "P(C=2)")
        for history in HISTORIES
    ]


def test_attempt_selection_resumes_unsealed_stage_but_advances_after_failure(
    tmp_path: Path,
) -> None:
    module = _module()
    unit = tmp_path / "u0" / HISTORIES[0]
    action, ordinal, root, public = module._select_attempt_root(unit)
    assert (action, ordinal, public) == ("RUN", 1, None)

    root.mkdir(parents=True)
    (root / "reader_stage.json").write_text("{}")
    assert module._select_attempt_root(unit)[:2] == ("RUN", 1)

    (root / "failure.json").write_text("{}")
    assert module._select_attempt_root(unit)[:2] == ("RUN", 2)


def test_attempt_selection_restores_sealed_public_without_live_resampling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    unit = tmp_path / "u0" / HISTORIES[0]
    root = unit / "attempt-001"
    root.mkdir(parents=True)
    (root / "private_bundle.json").write_text("{}")
    restored = {"payload_sha256": "a" * 64}
    calls: list[Path] = []

    def fake_restore(path: Path) -> dict:
        calls.append(path)
        return restored

    monkeypatch.setattr(module, "load_or_restore_quality_v1_bundle", fake_restore)
    action, ordinal, observed_root, public = module._select_attempt_root(unit)

    assert (action, ordinal, observed_root, public) == (
        "REUSE",
        1,
        root,
        restored,
    )
    assert calls == [root]


def test_attempt_inventory_fails_closed_on_noncontiguous_or_unknown_entries(
    tmp_path: Path,
) -> None:
    module = _module()
    unit = tmp_path / "u0" / HISTORIES[0]
    (unit / "attempt-002").mkdir(parents=True)
    with pytest.raises(ValueError, match="attempt inventory"):
        module._select_attempt_root(unit)
