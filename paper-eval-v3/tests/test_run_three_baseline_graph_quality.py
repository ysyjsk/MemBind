"""RED-first lifecycle tests for the graph-quality command entry point."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from pathlib import Path
from types import ModuleType

import pytest


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "scripts/run_three_baseline_graph_quality.py"


def _script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_three_baseline_graph_quality", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Closable:
    def __init__(self, name: str, events: list[tuple[str, int]]) -> None:
        self.name = name
        self.events = events

    async def aclose(self) -> None:
        self.events.append((f"close:{self.name}", id(asyncio.get_running_loop())))


def test_runtime_is_built_before_the_async_client_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script_module()
    events: list[tuple[str, int | None]] = []
    runtime = object()

    def fake_build_runtime(*, env: object) -> object:
        assert env == {"identity": "frozen"}
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop_identity = None
        else:  # pragma: no cover - this is the regression being guarded
            pytest.fail("Graphiti runtime was built inside an active event loop")
        events.append(("build-runtime", loop_identity))
        return runtime

    async def fake_client_lifecycle(**kwargs: object) -> dict[str, object]:
        assert kwargs["runtime"] is runtime
        events.append(("run-clients", id(asyncio.get_running_loop())))
        return {"payload_sha256": "a" * 64}

    monkeypatch.setattr(module, "build_graph_quality_runtime", fake_build_runtime)
    monkeypatch.setattr(module, "_build_clients_run_live_and_close", fake_client_lifecycle, raising=False)

    result = module._build_run_live_and_close(
        overlay_run_id="gq-test-001",
        targets=(),
        records={},
        env={"identity": "frozen"},
    )
    if inspect.isawaitable(result):
        result.close()
        pytest.fail("runtime/client lifecycle entry point must be synchronous")

    assert result == {"payload_sha256": "a" * 64}
    assert events[0] == ("build-runtime", None)
    assert events[1][0] == "run-clients"
    assert events[1][1] is not None


def test_live_execution_and_all_client_closes_share_one_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script_module()
    events: list[tuple[str, int]] = []

    async def fake_run_live(**_kwargs: object) -> dict[str, object]:
        events.append(("run", id(asyncio.get_running_loop())))
        return {"payload_sha256": "a" * 64}

    monkeypatch.setattr(module, "_run_live", fake_run_live)
    components = {
        name: _Closable(name, events)
        for name in ("runtime", "transport", "judge")
    }

    result = asyncio.run(
        module._run_live_and_close(
            overlay_run_id="gq-test-001",
            targets=(),
            records={},
            reader=object(),
            **components,
        )
    )

    assert result == {"payload_sha256": "a" * 64}
    assert [name for name, _loop in events] == [
        "run",
        "close:judge",
        "close:transport",
        "close:runtime",
    ]
    assert len({loop for _name, loop in events}) == 1


def test_cleanup_failure_is_not_swallowed_and_remaining_clients_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _script_module()
    closed: list[str] = []

    async def fake_run_live(**_kwargs: object) -> dict[str, object]:
        return {"payload_sha256": "a" * 64}

    class FailingJudge:
        async def aclose(self) -> None:
            closed.append("judge")
            raise RuntimeError("expected cleanup failure")

    class RecordingClose:
        def __init__(self, name: str) -> None:
            self.name = name

        async def aclose(self) -> None:
            closed.append(self.name)

    monkeypatch.setattr(module, "_run_live", fake_run_live)

    with pytest.raises(BaseExceptionGroup, match="cleanup"):
        asyncio.run(
            module._run_live_and_close(
                overlay_run_id="gq-test-001",
                targets=(),
                records={},
                runtime=RecordingClose("runtime"),
                reader=object(),
                transport=RecordingClose("transport"),
                judge=FailingJudge(),
            )
        )

    assert closed == ["judge", "transport", "runtime"]
