"""TDD contract for the small U0 -> A0 -> P(C=2) command."""

from __future__ import annotations

import importlib.util
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_three_baselines.py"
HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
QUALITY_IDENTITY = {
    "baseline_id": "native-graphiti-u0-reader-v2",
    "reader_config_sha256": "1" * 64,
    "judge_config_sha256": "2" * 64,
}


def _module():
    spec = importlib.util.spec_from_file_location("run_three_baselines", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _u0(run_id: str = "nb-20260816-001") -> dict[str, Any]:
    return {
        "status": "VERIFIED_RESULT_ARTIFACTS_ONLY",
        "source_run_id": run_id,
        "payload_sha256": "f" * 64,
        "histories": [
            {
                "history_id": history,
                "episode_count": count,
                "quality_identity": QUALITY_IDENTITY,
                "quality_metrics": {
                    "qa_accuracy": float(index == 2),
                    "evidence_recall_at_10": 1.0,
                },
            }
            for index, (history, count) in enumerate(
                zip(HISTORIES, (49, 49, 46, 44), strict=True)
            )
        ],
        "quality_identity": QUALITY_IDENTITY,
    }


class World:
    def __init__(self) -> None:
        self.executed: list[tuple[str, str, int]] = []
        self.observed: dict[str, dict[str, Any]] = {}

    def verify_u0(self, _root: Path, run_id: str) -> dict[str, Any]:
        return _u0(run_id)

    def inspect(self, root: Path, block: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(self.observed[str(root)])
        assert value["block"] == block
        return value

    def execute(self, *, block: dict[str, Any], block_root: Path) -> dict[str, Any]:
        self.executed.append(
            (block["method"], block["history_id"], block["attempt_ordinal"])
        )
        block_root.mkdir(parents=True)
        payload = {
            "run_id": block["namespace"],
            "method": block["method"],
            "history_id": block["history_id"],
            "status": "PASS",
            "episode_count": 3,
            "metrics": {
                "qa_accuracy": 1.0,
                "evidence_recall_at_10": 1.0,
                "direct_violations": None,
                "p95_freshness_ns": 10,
                "p99_freshness_ns": 12,
                "successful_goodput": 1.0,
                "makespan_ns": 30,
                "max_backlog": 2,
            },
            "work_volume": {"llm_logical_calls": 3},
            "quality_status": "MEASURED",
            "quality_identity": deepcopy(QUALITY_IDENTITY),
        }
        self.observed[str(block_root)] = {
            "block": deepcopy(block),
            "status": "completed",
            "artifacts_verified": True,
            "result": {
                "result_payload_sha256": "a" * 64,
                "payload": payload,
            },
        }
        return {"status": "completed"}

    def hooks(self, module):
        return module.Hooks(
            verify_u0=self.verify_u0,
            inspect_block=self.inspect,
            execute_block=self.execute,
        )


def test_runs_only_remaining_a0_then_pc2_histories(tmp_path: Path) -> None:
    module = _module()
    world = World()

    result = module.run_remaining_baselines(
        run_id="bs-dev-001",
        reuse_u0_run="nb-20260816-001",
        runs_root=tmp_path / "runs",
        native_runs_root=tmp_path / "native",
        hooks=world.hooks(module),
    )

    assert world.executed == [
        (method, history, 1)
        for method in ("A0", "P(C=2)")
        for history in HISTORIES
    ]
    assert result["status"] == "PASS"
    assert result["execution_order"] == ["U0_REUSED", "A0", "P(C=2)"]
    assert result["u0"]["episode_count"] == 188
    assert result["fairness"]["quality_identity_verified"] is True
    assert result["fairness"]["quality_identity"] == QUALITY_IDENTITY
    assert result["u0"]["quality_metrics"]["qa_accuracy_macro"] == 0.25
    assert [row["method"] for row in result["blocks"]] == ["A0"] * 4 + [
        "P(C=2)"
    ] * 4


def test_restart_skips_completed_and_retries_partial_with_fresh_attempt(
    tmp_path: Path,
) -> None:
    module = _module()
    world = World()
    root = tmp_path / "runs"
    module.run_remaining_baselines(
        run_id="bs-dev-001",
        reuse_u0_run="nb-20260816-001",
        runs_root=root,
        native_runs_root=tmp_path / "native",
        hooks=world.hooks(module),
    )
    world.executed.clear()

    # Replace one completed attempt with a durable non-mergeable attempt.
    target = root / "bs-dev-001/blocks/pc2/a2f3aa27/attempt-001"
    block = world.observed[str(target)]["block"]
    world.observed[str(target)] = {
        "block": block,
        "status": "incomplete_non_mergeable",
        "artifacts_verified": False,
        "result": None,
    }

    module.run_remaining_baselines(
        run_id="bs-dev-001",
        reuse_u0_run="nb-20260816-001",
        runs_root=root,
        native_runs_root=tmp_path / "native",
        hooks=world.hooks(module),
    )

    assert world.executed == [("P(C=2)", "a2f3aa27", 2)]
    retry = root / "bs-dev-001/blocks/pc2/a2f3aa27/attempt-002"
    assert world.observed[str(retry)]["block"]["namespace"] != block["namespace"]


def test_live_failure_stops_before_later_blocks_and_keeps_safe_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    world = World()
    original = world.execute

    def fail_once(*, block: dict[str, Any], block_root: Path) -> dict[str, Any]:
        if block["method"] == "A0" and block["history_id"] == "6071bd76":
            raise ConnectionError("private transport detail")
        return original(block=block, block_root=block_root)

    hooks = world.hooks(module)
    monkeypatch.setattr(hooks, "execute_block", fail_once)
    with pytest.raises(ConnectionError):
        module.run_remaining_baselines(
            run_id="bs-dev-001",
            reuse_u0_run="nb-20260816-001",
            runs_root=tmp_path / "runs",
            native_runs_root=tmp_path / "native",
            hooks=hooks,
        )

    assert world.executed == [
        ("A0", "07741c45", 1),
        ("A0", "b6019101", 1),
    ]
    progress = (tmp_path / "runs/bs-dev-001/progress.json").read_text()
    assert "private transport detail" not in progress
    assert "builtins.ConnectionError" in progress


def test_script_is_one_small_entrypoint_without_canary_or_mstar() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "run_remaining_baselines" in source
    assert "canary" not in source.casefold()
    assert "M*" not in source
    assert "10.87.5.247" not in source
    assert "api_key" not in source.casefold()


def test_quality_identity_drift_stops_before_later_blocks(tmp_path: Path) -> None:
    module = _module()
    world = World()
    original = world.execute

    def drift(*, block: dict[str, Any], block_root: Path) -> dict[str, Any]:
        result = original(block=block, block_root=block_root)
        world.observed[str(block_root)]["result"]["payload"][
            "quality_identity"
        ]["reader_config_sha256"] = "9" * 64
        return result

    hooks = world.hooks(module)
    hooks.execute_block = drift
    with pytest.raises(RuntimeError, match="quality identity drift"):
        module.run_remaining_baselines(
            run_id="bs-dev-001",
            reuse_u0_run="nb-20260816-001",
            runs_root=tmp_path / "runs",
            native_runs_root=tmp_path / "native",
            hooks=hooks,
        )

    assert world.executed == [("A0", "07741c45", 1)]


def test_existing_suite_run_cannot_be_rebound_to_another_u0(
    tmp_path: Path,
) -> None:
    module = _module()
    world = World()
    runs_root = tmp_path / "runs"
    module.run_remaining_baselines(
        run_id="bs-dev-001",
        reuse_u0_run="nb-20260816-001",
        runs_root=runs_root,
        native_runs_root=tmp_path / "native",
        hooks=world.hooks(module),
    )

    with pytest.raises(RuntimeError, match="U0 source identity drift"):
        module.run_remaining_baselines(
            run_id="bs-dev-001",
            reuse_u0_run="nb-20260816-002",
            runs_root=runs_root,
            native_runs_root=tmp_path / "native",
            hooks=world.hooks(module),
        )
