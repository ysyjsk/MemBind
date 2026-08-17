"""Offline TDD coverage for one isolated live baseline block.

Every external service and legacy Graphiti adapter is replaced by a small
fake.  These tests exercise the composition boundary without opening a
socket, loading the real dataset, or mutating Neo4j.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from paper_eval.baseline_suite import build_baseline_suite_plan
from paper_eval.baseline_suite_artifacts import inspect_baseline_block
from paper_eval import baseline_suite_block_live as live


@dataclass(frozen=True)
class _Episode:
    question_id: str
    group_id: str
    session_id: str
    source_sequence: int
    source_hash: str
    reference_time: str
    body: str

    @property
    def name(self) -> str:
        return f"episode-{self.source_sequence}"


class _Driver:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.names: list[str] = []
        self.mismatch = mismatch

    async def build_indices_and_constraints(self) -> None:
        return None

    async def execute_query(self, _query: str, *, params: dict[str, str]) -> Any:
        assert params["group_id"].startswith("pev3-bs-")
        return SimpleNamespace(
            records=[
                {
                    "node_count": len(self.names),
                    "relationship_count": len(self.names),
                    "episode_names": list(self.names),
                }
            ]
        )


class _Graphiti:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.driver = _Driver(mismatch=mismatch)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _TraceRecorder:
    @contextmanager
    def episode_scope(self, *_args: Any):
        yield

    @contextmanager
    def span(self, *_args: Any, **_kwargs: Any):
        yield

    def episode_envelope(
        self, run_id: str, episode_id: str, source_sequence: int
    ) -> dict[str, Any]:
        start = 1_000 + source_sequence * 100
        return {
            "run_id": run_id,
            "episode_id": episode_id,
            "source_sequence": source_sequence,
            "spans": [
                {
                    "phase": "add-episode",
                    "start_ns": start,
                    "end_ns": start + 50,
                    "status": "ok",
                    "metadata": {},
                }
            ],
        }


class _Writer:
    """Match the legacy writer's create-on-first-write behavior."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, value: dict[str, Any]) -> None:
        with self.path.open("a", encoding="ascii") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")


class _Handle:
    def restore(self) -> None:
        return None


def _block(method: str) -> dict[str, Any]:
    plan = build_baseline_suite_plan("bs-live-test-001", mode="canary")
    return deepcopy(next(row for row in plan["blocks"] if row["method"] == method))


def _install_fake_legacy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_add: bool = False,
    namespace_mismatch: bool = False,
) -> _Graphiti:
    graphiti = _Graphiti(mismatch=namespace_mismatch)
    episodes = [
        _Episode(
            question_id="07741c45",
            group_id="source-group-must-be-replaced",
            session_id=f"session-{sequence}",
            source_sequence=sequence,
            source_hash=f"{sequence + 1:064x}",
            reference_time="2026-01-01",
            body=f"PRIVATE_EPISODE_BODY_{sequence}",
        )
        for sequence in range(2)
    ]

    class _LiveAction(Enum):
        NATIVE_CHARACTERIZATION_C0 = "native_characterization_c0"

    current_state_gate = ModuleType("current_state_gate")
    current_state_gate.LiveAction = _LiveAction

    dataset = ModuleType("dataset")
    dataset.load_json_records = lambda _path: [
        {
            "question_id": "07741c45",
            "question": "PRIVATE_QUESTION",
            "answer": "PRIVATE_REFERENCE_ANSWER",
        }
    ]
    dataset.build_episodes = lambda _record: list(episodes)

    async def add_episode(target: _Graphiti, episode: _Episode) -> None:
        if fail_add:
            raise ConnectionError("private failure details must not persist")
        target.driver.names.append(
            "foreign-episode" if namespace_mismatch else episode.name
        )

    graphiti_native = ModuleType("graphiti_native")
    graphiti_native.add_episode = add_episode
    graphiti_native.load_env_file = lambda _path: {
        "CONSTRUCTION_LLM_API_KEY": "PRIVATE_API_KEY"
    }

    runtime_module = ModuleType("native_characterization_runtime")
    runtime_module.build_u0_graphiti_from_env = lambda **_kwargs: SimpleNamespace(
        graphiti=graphiti
    )

    instrumentation = ModuleType("native_characterization_instrumentation")
    instrumentation.install_native_characterization_instrumentation = (
        lambda *_args: _Handle()
    )
    measurement = ModuleType("native_characterization_c2_measurement")
    measurement.install_c2_measurement_adapter = lambda *_args: _Handle()
    tracing = ModuleType("native_characterization_tracing")
    tracing.DurableJsonlEnvelopeWriter = _Writer
    tracing.TraceRecorder = _TraceRecorder

    modules = {
        "current_state_gate": current_state_gate,
        "dataset": dataset,
        "graphiti_native": graphiti_native,
        "native_characterization_runtime": runtime_module,
        "native_characterization_instrumentation": instrumentation,
        "native_characterization_c2_measurement": measurement,
        "native_characterization_tracing": tracing,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return graphiti


def _install_fake_schedule(
    monkeypatch: pytest.MonkeyPatch,
    observed_methods: list[str],
) -> None:
    async def schedule(
        *,
        method: str,
        run_id: str,
        episodes: tuple[Any, ...],
        native_add_episode: Any,
        persist_event: Any,
    ) -> dict[str, Any]:
        observed_methods.append(method)
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            event = {**event, "event_sequence": len(events)}
            events.append(event)
            await persist_event(event)

        for item in episodes:
            sequence = item.source_sequence
            base = sequence * 100
            identity = {
                "run_id": run_id,
                "method": method,
                "source_sequence": sequence,
                "source_sha256": item.source_sha256,
            }
            await emit(
                {
                    **identity,
                    "event_type": "intent",
                    "intent_timestamp_ns": base,
                }
            )
            if method == "A0":
                await emit(
                    {
                        **identity,
                        "event_type": "caller_return",
                        "durable_enqueue_ack_timestamp_ns": base + 1,
                        "caller_return_timestamp_ns": base + 1,
                    }
                )
            await native_add_episode(item.native_episode)
            await emit(
                {
                    **identity,
                    "event_type": "publication",
                    "service_start_timestamp_ns": base + 2,
                    "publish_timestamp_ns": base + 3,
                    "worker_id": sequence % 2,
                    "transaction_status": "committed",
                }
            )
        terminal = {
            "event_sequence": len(events),
            "event_type": "terminal_success",
            "run_id": run_id,
            "method": method,
            "expected_episode_count": len(episodes),
        }
        events.append(terminal)
        await persist_event(terminal)
        return {
            "status": "PASS",
            "run_id": run_id,
            "method": method,
            "events": events,
            "summary": {
                "configured_worker_count": 2 if method == "P(C=2)" else 1,
                "max_active_calls": 2 if method == "P(C=2)" else 1,
            },
        }

    monkeypatch.setattr(live, "execute_method_schedule", schedule)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["U0", "A0", "P(C=2)"])
async def test_canary_dispatches_exact_baseline_without_reader_or_judge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
) -> None:
    _install_fake_legacy(monkeypatch)
    observed: list[str] = []
    _install_fake_schedule(monkeypatch, observed)

    def forbidden_quality_adapter(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("canary must not construct Reader/Judge adapters")

    monkeypatch.setattr(live, "build_baseline_quality_adapters", forbidden_quality_adapter)
    result = await live.execute_baseline_block(
        block=_block(method),
        block_root=tmp_path / method.replace("/", "_"),
    )

    assert observed == [method]
    assert result["result"]["quality_status"] == "NOT_RUN_CANARY"


@pytest.mark.asyncio
async def test_construction_failure_is_sealed_via_store_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_legacy(monkeypatch, fail_add=True)
    _install_fake_schedule(monkeypatch, [])
    block = _block("U0")
    root = tmp_path / "failed"

    with pytest.raises(ConnectionError):
        await live.execute_baseline_block(block=block, block_root=root)

    observed = inspect_baseline_block(root, block)
    assert observed["status"] == "incomplete_non_mergeable"
    assert observed["phase"] == "failed"
    assert observed["result"]["payload"] == {
        "error_class": "builtins.ConnectionError",
        "failure_stage": "construction",
    }


@pytest.mark.asyncio
async def test_final_namespace_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_legacy(monkeypatch, namespace_mismatch=True)
    _install_fake_schedule(monkeypatch, [])
    block = _block("U0")
    root = tmp_path / "mismatch"

    with pytest.raises(live.BaselineSuiteLiveError, match="episode_mismatch"):
        await live.execute_baseline_block(block=block, block_root=root)

    observed = inspect_baseline_block(root, block)
    assert observed["status"] == "incomplete_non_mergeable"
    assert observed["phase"] == "failed"


@pytest.mark.asyncio
async def test_parallel_graph_work_is_explicitly_confounding_not_delta_attributed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_legacy(monkeypatch)
    _install_fake_schedule(monkeypatch, [])
    root = tmp_path / "parallel"

    result = await live.execute_baseline_block(
        block=_block("P(C=2)"), block_root=root
    )

    metrics = [
        json.loads(line)
        for line in (root / "telemetry/per_episode_metrics.jsonl")
        .read_text(encoding="ascii")
        .splitlines()
    ]
    assert metrics
    assert all(
        row["graph_work"]
        == {"attribution_status": "CONCURRENT_PREFIX_DELTA_CONFOUNDED"}
        for row in metrics
    )
    assert result["result"]["graph_work"] == {}


@pytest.mark.asyncio
async def test_completed_canary_hashes_every_stream_and_persists_no_private_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_legacy(monkeypatch)
    _install_fake_schedule(monkeypatch, [])
    root = tmp_path / "public"

    result = await live.execute_baseline_block(block=_block("U0"), block_root=root)

    hashes = result["result"]["telemetry_sha256"]
    assert set(hashes) == {
        *(f"{name}.jsonl" for name in live.TELEMETRY_STREAMS),
        "per_history_metrics.json",
    }
    assert all(len(value) == 64 for value in hashes.values())
    assert all(
        (root / "telemetry" / name).is_file()
        for name in hashes
        if name.endswith(".jsonl")
    )
    public_bytes = b"\n".join(
        path.read_bytes() for path in root.rglob("*") if path.is_file()
    )
    for forbidden in (
        b"PRIVATE_API_KEY",
        b"PRIVATE_EPISODE_BODY",
        b"PRIVATE_QUESTION",
        b"PRIVATE_REFERENCE_ANSWER",
    ):
        assert forbidden not in public_bytes


def test_sync_entrypoint_executes_and_closes_retrieval_graph_on_one_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loops: list[Any] = []

    class RetrievalGraph:
        async def close(self) -> None:
            import asyncio

            loops.append(asyncio.get_running_loop())

    retrieval_runtime = SimpleNamespace(graphiti=RetrievalGraph())
    native = ModuleType("graphiti_native")
    native.load_env_file = lambda _path: {}
    monkeypatch.setitem(sys.modules, "graphiti_native", native)
    read_only = ModuleType("paper_eval.s2_r0_live")
    read_only.build_read_only_graphiti = lambda **_kwargs: retrieval_runtime
    monkeypatch.setitem(sys.modules, "paper_eval.s2_r0_live", read_only)

    async def fake_execute(**kwargs: Any) -> dict[str, Any]:
        import asyncio

        assert kwargs["retrieval_runtime"] is retrieval_runtime
        loops.append(asyncio.get_running_loop())
        return {"status": "completed"}

    monkeypatch.setattr(live, "execute_baseline_block", fake_execute)
    block = _block("U0")
    block["mode"] = "development"
    block["episode_limit"] = None

    assert live.execute_baseline_block_sync(
        block=block, block_root=tmp_path / "sync"
    ) == {"status": "completed"}
    assert len(loops) == 2
    assert loops[0] is loops[1]
