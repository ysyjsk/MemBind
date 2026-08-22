from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.p9_runner import (
    P9FullConfig,
    _ScopedLLMClient,
    _install_p9_context_budget_adapter,
    _p9_effective_max_tokens,
    build_p9_live_command,
    build_p9_parser,
    run_frontier_history_async,
    _native_previous_window,
)
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import CapacityAuthority
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.binder import NativeBindingScope
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.provider_admission import provider_scope, FrontierAwareLLMClient
from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.transcript import TranscriptStore


@pytest.mark.asyncio
async def test_full_frontier_executor_allows_future_prepare_to_overlap_native() -> None:
    authority = CapacityAuthority.from_runtime(2, 2)
    native_started = asyncio.Event()
    intervals: dict[tuple[str, int], tuple[float, float]] = {}
    published: list[int] = []

    async def prepare(sequence: int) -> dict[str, int]:
        start = asyncio.get_running_loop().time()
        if sequence == 1:
            await asyncio.sleep(0.01)
        else:
            await asyncio.sleep(0)
        end = asyncio.get_running_loop().time()
        intervals[("prepare", sequence)] = (start, end)
        return {"source_sequence": sequence}

    async def publish(sequence: int, value: dict[str, int]) -> None:
        assert value["source_sequence"] == sequence
        start = asyncio.get_running_loop().time()
        native_started.set()
        await asyncio.sleep(0.03)
        end = asyncio.get_running_loop().time()
        intervals[("native", sequence)] = (start, end)
        published.append(sequence)

    result = await run_frontier_history_async(3, prepare, publish, authority=authority)
    assert published == [0, 1, 2]
    assert result.durable_frontier == 2
    assert result.overlap_evidence["future_prepare_overlapped_native"] is True
    assert intervals[("prepare", 1)][0] < intervals[("native", 0)][1]


@pytest.mark.asyncio
async def test_full_frontier_failure_does_not_advance_durable_frontier() -> None:
    authority = CapacityAuthority.from_runtime(2, 2)
    published: list[int] = []

    async def prepare(sequence: int) -> int:
        return sequence

    async def publish(sequence: int, _value: int) -> None:
        published.append(sequence)
        if sequence == 1:
            raise RuntimeError("native failure")

    with pytest.raises(RuntimeError, match="native failure"):
        await run_frontier_history_async(3, prepare, publish, authority=authority)
    assert published == [0, 1]


def test_p9_command_is_real_runner_and_cli_shape_is_parseable(tmp_path: Path) -> None:
    config = P9FullConfig(
        repo_root=tmp_path,
        baseline_root=tmp_path / "baseline",
        state_path=tmp_path / "V5_CURRENT_STATE.json",
        output_root=tmp_path / "p9",
        run_id="p9-test-run",
    )
    command = build_p9_live_command(config)
    assert "run_v5_p9_full.py" in command
    assert "run_v5_campaign.py" not in command
    assert "--execute-live" in command
    assert "saturated_fixed_work_baseline_v1_2/src" in command
    assert "membind-validation/src" in command
    parsed = build_p9_parser().parse_args(
        [
            "--repo-root", str(tmp_path),
            "--baseline-root", str(tmp_path / "baseline"),
            "--state", str(tmp_path / "state.json"),
            "--p8-seal", str(tmp_path / "seal.json"),
            "--output-root", str(tmp_path / "p9"),
            "--run-id", "p9-test-run",
            "--execute-live",
        ]
    )
    assert parsed.execute_live is True
    assert parsed.smoke is False


def test_p9_full_config_defaults_to_all_frozen_histories() -> None:
    config = P9FullConfig(
        repo_root=Path("."),
        baseline_root=Path("baseline"),
        state_path=Path("state"),
        output_root=Path("output"),
        run_id="p9-test-run",
    )
    assert config.history_ids == ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
    assert config.source_limit is None


def test_p9_failure_schema_is_sanitized() -> None:
    failure_fields = {
        "schema_version",
        "status",
        "run_id",
        "history_ids",
        "completed_history_ids",
        "error_type",
        "native_characterization_c5_reused",
    }
    assert "traceback" not in failure_fields
    assert "raw_prompt" not in failure_fields
    assert "raw_response" not in failure_fields


def test_p9_resume_is_limited_to_matching_started_marker(tmp_path: Path) -> None:
    started = tmp_path / "p9"
    started.mkdir()
    (started / "campaign_started.json").write_text(
        '{"run_id":"p9-test-run","status":"P9_LIVE_STARTED"}\n',
        encoding="utf-8",
    )
    entries = {item.name for item in started.iterdir()}
    assert entries == {"campaign_started.json"}
    marker = __import__("json").loads((started / "campaign_started.json").read_text())
    assert marker["run_id"] == "p9-test-run"
    assert marker["status"] == "P9_LIVE_STARTED"
    (started / "campaign_failure.json").write_text('{"status":"P9_LIVE_FAILED"}\n', encoding="utf-8")
    assert {item.name for item in started.iterdir()} == {"campaign_started.json", "campaign_failure.json"}


def test_preparation_previous_window_matches_pinned_graphiti_limit() -> None:
    episodes = [
        type("Episode", (), {"source_sequence": index, "reference_time": f"2026-01-{index + 1:02d}T00:00:00Z"})()
        for index in range(20)
    ]
    assert [row.source_sequence for row in _native_previous_window(episodes, 0)] == []
    assert [row.source_sequence for row in _native_previous_window(episodes, 5)] == [0, 1, 2, 3, 4]
    assert [row.source_sequence for row in _native_previous_window(episodes, 19)] == list(range(9, 19))


def test_preparation_previous_window_filters_future_valid_at_and_returns_chronological() -> None:
    rows = [
        type("Episode", (), {"source_sequence": 0, "reference_time": "2026-01-05T00:00:00Z"})(),
        type("Episode", (), {"source_sequence": 1, "reference_time": "2026-01-02T00:00:00Z"})(),
        type("Episode", (), {"source_sequence": 2, "reference_time": "2026-01-01T00:00:00Z"})(),
        type("Episode", (), {"source_sequence": 3, "reference_time": "2026-01-04T00:00:00Z"})(),
    ]
    assert [row.source_sequence for row in _native_previous_window(rows, 3)] == [2, 1]


def test_p9_context_budget_adapter_retries_only_wire_budget() -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls: list[int] = []

        async def create(self, **kwargs):
            self.calls.append(int(kwargs["max_tokens"]))
            if len(self.calls) == 1:
                raise RuntimeError(
                    "This model's maximum context length is 65536 tokens. However, you requested "
                    "16384 output tokens and your prompt contains at least 49153 input tokens, "
                    "for a total of at least 65537 tokens."
                )
            return {"ok": True}

    transport = Transport()
    llm = type("LLM", (), {"client": type("Client", (), {"chat": type("Chat", (), {"completions": type("Completions", (), {"_inner": transport})()})()})(), "max_tokens": 16384})()
    restore = _install_p9_context_budget_adapter(llm)
    try:
        result = asyncio.run(transport.create(max_tokens=16384))
    finally:
        restore()
    assert result == {"ok": True}
    assert transport.calls == [16384, 16351]
    assert _p9_effective_max_tokens(RuntimeError("unrelated"), 16384) is None


@pytest.mark.asyncio
async def test_scoped_client_routes_capture_and_replay_without_provider_replay() -> None:
    class Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_response(self, messages, **kwargs):
            self.calls += 1
            return {"ok": kwargs["prompt_name"]}

    authority = CapacityAuthority.from_runtime(2, 2)
    store = TranscriptStore()
    capture_delegate = Delegate()
    replay_delegate = Delegate()
    capture = FrontierAwareLLMClient(
        capture_delegate,
        store=store,
        arbiter=__import__("saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission", fromlist=["AdmissionArbiter"]).AdmissionArbiter(authority),
        mode="capture",
        durable_frontier=lambda: -1,
        client_identity={"class": "Delegate", "source_hash": "p9-test"},
    )
    replay = FrontierAwareLLMClient(
        replay_delegate,
        store=store,
        arbiter=__import__("saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission", fromlist=["AdmissionArbiter"]).AdmissionArbiter(authority),
        mode="replay",
        durable_frontier=lambda: -1,
        client_identity={"class": "Delegate", "source_hash": "p9-test"},
    )
    scoped = _ScopedLLMClient(capture, replay)
    request = [{"role": "user", "content": "same"}]
    with provider_scope(region="PREPARE", source_sequence=0):
        await scoped.generate_response(request, prompt_name="extract_nodes.extract_message")
    with NativeBindingScope(store, source_sequence=0):
        with provider_scope(region="NATIVE", source_sequence=0):
            await scoped.generate_response(request, prompt_name="extract_nodes.extract_message")
    assert capture_delegate.calls == 1
    assert replay_delegate.calls == 0
    assert replay.provider_calls[-1]["admitted"] is False


@pytest.mark.asyncio
async def test_concurrent_capture_source_identity_is_context_local() -> None:
    class Delegate:
        async def generate_response(self, messages, **kwargs):
            await asyncio.sleep(0.005)
            return {"source": kwargs["prompt_name"]}

    from saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission import AdmissionArbiter

    authority = CapacityAuthority.from_runtime(4, 4)
    store = TranscriptStore()
    client = FrontierAwareLLMClient(
        Delegate(),
        store=store,
        arbiter=AdmissionArbiter(authority),
        mode="capture",
        durable_frontier=lambda: -1,
        client_identity={"class": "Delegate", "source_hash": "concurrency-test"},
    )

    async def one(source_sequence: int) -> None:
        with provider_scope(region="PREPARE", source_sequence=source_sequence):
            await client.generate_response(
                [{"role": "user", "content": f"source-{source_sequence}"}],
                prompt_name="extract_nodes.extract_message",
            )

    await asyncio.gather(*(one(index) for index in range(4)))
    identities = sorted(item.identity.source_sequence for item in store._items.values())
    assert identities == [0, 1, 2, 3]
