"""Lifecycle regression tests for the APC-aligned baseline command."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_apc_aligned_baselines.py"


def _script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_apc_aligned_baselines", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_execution_and_runtime_close_share_one_event_loop(monkeypatch) -> None:
    module = _script_module()
    observed: list[tuple[str, int]] = []

    class Runtime:
        async def aclose(self) -> None:
            observed.append(("close", id(asyncio.get_running_loop())))

    async def fake_run(**_kwargs: object) -> dict[str, object]:
        observed.append(("run", id(asyncio.get_running_loop())))
        return {"status": "PASS", "completed_block_indices": [0, 1, 2]}

    monkeypatch.setattr(module, "_run", fake_run)
    result = asyncio.run(
        module._run_and_close(
            read_runtime=Runtime(),
            run_root=Path("unused"),
            plan={},
            workload={},
            env={},
            execution_identity_sha256="a" * 64,
            block_indices=(0, 1, 2),
        )
    )

    assert result["status"] == "PASS"
    assert [name for name, _loop in observed] == ["run", "close"]
    assert len({loop for _name, loop in observed}) == 1
