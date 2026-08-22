from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v5.p9_runner import (
    _DurableJsonl,
    _verify_p8_seal,
    P9FullConfig,
    P9RunnerError,
    _ScopedLLMClient,
    _install_p9_context_budget_adapter,
    _p9_effective_max_tokens,
    _persist_partial_native_trace,
    _persist_transport_evidence,
    _transport_attempt_rows,
    _transport_evidence_summary,
    _run_history_live_async,
    ALTERNATE_CONSTRUCTION_BASE_URL,
    ALTERNATE_EMBEDDING_BASE_URL,
    build_u0_runtime_with_endpoint_overrides,
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


@pytest.mark.asyncio
async def test_partial_frontier_journal_survives_native_failure(tmp_path: Path) -> None:
    journal = _DurableJsonl(tmp_path / "frontier.jsonl")
    authority = CapacityAuthority.from_runtime(2, 2)

    async def prepare(sequence: int) -> int:
        return sequence

    async def publish(sequence: int, _value: int) -> None:
        if sequence == 1:
            raise RuntimeError("native failure")

    with pytest.raises(RuntimeError, match="native failure"):
        await run_frontier_history_async(
            3,
            prepare,
            publish,
            authority=authority,
            event_sink=journal.append,
        )
    journal.close()
    rows = [__import__("json").loads(line) for line in (tmp_path / "frontier.jsonl").read_text().splitlines()]
    assert [row["ordinal"] for row in rows] == list(range(len(rows)))
    assert rows[0]["previous_sha256"] == "0" * 64
    assert all(row.get("payload_sha256") for row in rows)
    assert [row["source_sequence"] for row in rows if row["event"] == "PUBLICATION_DURABLE"] == [0]
    assert any(row["event"] == "FAILURE" and row["source_sequence"] == 1 for row in rows)
    assert not any(row["event"] == "PUBLICATION_DURABLE" and row["source_sequence"] == 1 for row in rows)


def test_durable_jsonl_rejects_frontier_jump_or_duplicate(tmp_path: Path) -> None:
    journal = _DurableJsonl(tmp_path / "frontier.jsonl")
    journal.append({"event": "PUBLICATION_DURABLE", "source_sequence": 0})
    with pytest.raises(P9RunnerError, match="durable frontier must advance by one"):
        journal.append({"event": "PUBLICATION_DURABLE", "source_sequence": 2})
    journal.close()
    rows = [__import__("json").loads(line) for line in (tmp_path / "frontier.jsonl").read_text().splitlines()]
    assert [row["source_sequence"] for row in rows] == [0]


def test_partial_native_trace_is_materialized_without_overwriting_existing_artifact(tmp_path: Path) -> None:
    class Recorder:
        def episode_envelope(self, run_id: str, episode_id: str, source_sequence: int) -> dict[str, object]:
            del run_id, episode_id
            return {"source_sequence": source_sequence, "spans": [{"source_sequence": source_sequence}]} if source_sequence < 2 else {"source_sequence": source_sequence, "spans": []}

    episodes = [type("Episode", (), {"name": f"episode-{index}", "source_sequence": index})() for index in range(3)]
    target = tmp_path / "native_trace.jsonl"
    _persist_partial_native_trace(target, Recorder(), "run-1", episodes)
    rows = [__import__("json").loads(line) for line in target.read_text().splitlines()]
    assert [row["source_sequence"] for row in rows] == [0, 1]
    _persist_partial_native_trace(target, Recorder(), "run-1", episodes)
    assert [__import__("json").loads(line)["source_sequence"] for line in target.read_text().splitlines()] == [0, 1]


def test_transport_evidence_distinguishes_response_finish_reason_from_logical_failure(tmp_path: Path) -> None:
    recorder = SimpleNamespace(
        records=[
            SimpleNamespace(
                phase="llm-transport",
                source_sequence=3,
                start_ns=10,
                end_ns=20,
                status="ok",
                error_code=None,
                metadata={
                    "attempt_index": 0,
                    "input_tokens": 100,
                    "output_tokens": 16,
                    "usage_observed": True,
                    "finish_reason_observed": True,
                    "finish_reason": "length",
                },
            ),
            SimpleNamespace(
                phase="llm",
                source_sequence=3,
                start_ns=10,
                end_ns=21,
                status="error",
                error_code="json.JSONDecodeError",
                metadata={"retry_count": 0},
            ),
        ]
    )
    rows = _transport_attempt_rows(recorder)
    summary = _transport_evidence_summary(rows)
    assert rows[0]["finish_reason"] == "length"
    assert summary["complete"] is True
    assert summary["finish_reasons"] == ["length"]
    summary = _persist_transport_evidence(tmp_path, recorder)
    assert summary["attempt_count"] == 1
    assert (tmp_path / "transport_attempts.jsonl").is_file()
    assert (tmp_path / "transport_evidence.json").is_file()


@pytest.mark.asyncio
async def test_p9_initialization_failure_closes_journals_and_writes_history_failure(tmp_path: Path) -> None:
    pytest.importorskip("graphiti_core")
    config = P9FullConfig(
        repo_root=tmp_path,
        baseline_root=tmp_path / "baseline",
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "output",
        run_id="p9-init-failure",
        history_ids=("07741c45",),
        source_limit=1,
        smoke=True,
    )
    episode = type("Episode", (), {"source_sequence": 0})()

    async def broken_runtime_builder() -> object:
        raise RuntimeError("runtime construction failed")

    with pytest.raises(RuntimeError, match="runtime construction failed"):
        await _run_history_live_async(
            config=config,
            history_id="07741c45",
            namespace="membind-v5-p9-init-failure",
            runtime_builder=broken_runtime_builder,
            episode_loader=lambda *_args: [episode],
            instrumentation_installer=lambda *_args: None,
            recorder_factory=lambda: object(),
            graph_exporter=lambda *_args: {},
            history_root=tmp_path / "history",
        )
    failure = __import__("json").loads((tmp_path / "history" / "failure.json").read_text())
    assert failure["status"] == "P9_HISTORY_FAILED"
    assert failure["error_type"] == "builtins.RuntimeError"
    for name in ("frontier.jsonl", "admission.jsonl", "raw_events.jsonl"):
        assert (tmp_path / "history" / name).is_file()


@pytest.mark.asyncio
async def test_p9_lifecycle_sink_binds_formal_start_to_final_durable() -> None:
    lifecycle: list[dict[str, object]] = []
    authority = CapacityAuthority.from_runtime(2, 2)

    async def prepare(sequence: int) -> int:
        await asyncio.sleep(0)
        return sequence

    async def publish(_sequence: int, _value: int) -> None:
        await asyncio.sleep(0)

    result = await run_frontier_history_async(
        2,
        prepare,
        publish,
        authority=authority,
        lifecycle_sink=lifecycle.append,
    )
    assert lifecycle[0]["event"] == "FORMAL_START"
    assert lifecycle[-1]["event"] == "TIMER_STOP"
    assert lifecycle[-1]["t_durable_complete_ns"] <= lifecycle[-1]["timer_stop_ns"]
    assert result.execution.timer_start_ns <= lifecycle[-1]["t_durable_complete_ns"]


@pytest.mark.asyncio
async def test_p9_lifecycle_sink_records_abort_without_durable_advance() -> None:
    lifecycle: list[dict[str, object]] = []
    authority = CapacityAuthority.from_runtime(2, 2)

    async def prepare(sequence: int) -> int:
        return sequence

    async def publish(sequence: int, _value: int) -> None:
        if sequence == 0:
            raise RuntimeError("native failure")

    with pytest.raises(RuntimeError, match="native failure"):
        await run_frontier_history_async(2, prepare, publish, authority=authority, lifecycle_sink=lifecycle.append)
    assert lifecycle[0]["event"] == "FORMAL_START"
    assert lifecycle[-1]["event"] == "TIMER_ABORT"
    assert lifecycle[-1]["durable_frontier"] == -1


@pytest.mark.asyncio
async def test_durable_frontier_sink_updates_before_next_prepare_is_released() -> None:
    authority = CapacityAuthority.from_runtime(2, 2)
    frontier = {"value": -1}
    source_one_started = asyncio.Event()
    observed_frontiers: list[int] = []

    async def prepare(sequence: int) -> int:
        if sequence == 1:
            source_one_started.set()
            while frontier["value"] < 0:
                await asyncio.sleep(0)
            observed_frontiers.append(frontier["value"])
        return sequence

    async def publish(sequence: int, _value: int) -> None:
        if sequence == 0:
            await source_one_started.wait()

    def on_durable(sequence: int) -> None:
        frontier["value"] = int(sequence)

    result = await run_frontier_history_async(
        2,
        prepare,
        publish,
        authority=authority,
        durable_frontier_sink=on_durable,
    )
    assert result.durable_frontier == 1
    assert observed_frontiers == [0]


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


def test_p9_alternate_gpu_endpoint_pair_is_cli_parseable_and_bound(tmp_path: Path) -> None:
    config = P9FullConfig(
        repo_root=tmp_path,
        baseline_root=tmp_path / "baseline",
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "p9",
        run_id="p9-gpu0-smoke",
        construction_base_url="http://10.87.5.247:8002/v1/",
        embedding_base_url="http://10.87.5.247:8003/v1",
    )
    command = build_p9_live_command(config)
    assert "--construction-base-url http://10.87.5.247:8002/v1/" in command
    assert "--embedding-base-url http://10.87.5.247:8003/v1" in command
    tokens = shlex.split(command)
    script_index = next(index for index, token in enumerate(tokens) if token.endswith("run_v5_p9_full.py"))
    parsed = build_p9_parser().parse_args(tokens[script_index + 1 :])
    assert parsed.construction_base_url == "http://10.87.5.247:8002/v1/"
    assert parsed.embedding_base_url == "http://10.87.5.247:8003/v1"


def test_p9_endpoint_overrides_must_be_a_pair(tmp_path: Path) -> None:
    with pytest.raises(P9RunnerError, match="overrides must provide"):
        P9FullConfig(
            repo_root=tmp_path,
            baseline_root=tmp_path / "baseline",
            state_path=tmp_path / "state.json",
            output_root=tmp_path / "p9",
            run_id="p9-gpu0-smoke",
            construction_base_url="http://10.87.5.247:8002/v1/",
        )


def test_endpoint_override_helper_is_temporary_and_fail_closed() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parents[2] / "membind-validation/src"))
    import native_characterization_runtime as runtime_module

    original_env = {name: os.environ.get(name) for name in ("CONSTRUCTION_LLM_BASE_URL", "EMBEDDING_BASE_URL")}
    original_constants = (runtime_module.CONSTRUCTION_BASE_URL, runtime_module.EMBEDDING_BASE_URL)
    observed: dict[str, str] = {}

    def builder() -> object:
        observed["construction_env"] = os.environ["CONSTRUCTION_LLM_BASE_URL"]
        observed["embedding_env"] = os.environ["EMBEDDING_BASE_URL"]
        observed["construction_constant"] = runtime_module.CONSTRUCTION_BASE_URL
        observed["embedding_constant"] = runtime_module.EMBEDDING_BASE_URL
        return object()

    assert build_u0_runtime_with_endpoint_overrides(
        builder,
        construction_base_url=ALTERNATE_CONSTRUCTION_BASE_URL,
        embedding_base_url=ALTERNATE_EMBEDDING_BASE_URL,
    ) is not None
    assert observed["construction_env"] == ALTERNATE_CONSTRUCTION_BASE_URL
    assert observed["embedding_env"] == ALTERNATE_EMBEDDING_BASE_URL
    assert (runtime_module.CONSTRUCTION_BASE_URL, runtime_module.EMBEDDING_BASE_URL) == original_constants
    for name, value in original_env.items():
        assert os.environ.get(name) == value

    with pytest.raises(P9RunnerError, match="qualified GPU0 pair"):
        build_u0_runtime_with_endpoint_overrides(
            builder,
            construction_base_url="http://127.0.0.1:9999/v1/",
            embedding_base_url=ALTERNATE_EMBEDDING_BASE_URL,
        )


def test_current_corrected_p9_queue_command_targets_real_cli_and_parses() -> None:
    queue_file = Path(__file__).parents[1] / "artifacts/sfwb-v1-3-v5-queue-20260822-032328/p9_full_queue_corrected.json"
    body = __import__("json").loads(queue_file.read_text(encoding="utf-8"))
    command = str(body["full_command"])
    assert "run_v5_p9_full.py" in command
    assert "run_v5_campaign.py" not in command
    tokens = shlex.split(command)
    script_index = next(index for index, token in enumerate(tokens) if token.endswith("run_v5_p9_full.py"))
    parsed = build_p9_parser().parse_args(tokens[script_index + 1 :])
    assert parsed.execute_live is True
    assert parsed.smoke is False
    assert parsed.run_id == "p9-full-20260822"


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


def test_p8_gate_requires_seal_identity_and_complete_artifact(tmp_path: Path) -> None:
    root = tmp_path / "p8"
    root.mkdir()
    required = {
        "frontier.jsonl",
        "admission.jsonl",
        "logical_work_summary.json",
        "native_trace.jsonl",
        "block_metrics.json",
        "canonical_graph.json",
        "lifecycle.json",
        "live_authority.json",
        "capacity_authority.json",
        "oracle_binding_summary.json",
    }
    for name in required:
        (root / name).write_text("{}\n", encoding="utf-8")
    baseline = {"formal_run_seal_sha256": "baseline-hash"}
    seal = {
        "schema_version": "membind.v5.p8-seal.v1",
        "status": "P8_LIVE_SEALED",
        "method": "V5_VERSIONED_ORACLE_HOIST",
        "namespace": "membind-v5-p8-test-abc",
        "source_count": 2,
        "build_makespan_ns": 1,
    }
    (root / "seal.json").write_text(__import__("json").dumps(seal), encoding="utf-8")
    (root / "manifest.json").write_text(
        __import__("json").dumps(
            {
                "status": "PASS",
                "method": seal["method"],
                "namespace": seal["namespace"],
                "source_count": 2,
                "native_graphiti_path": "Graphiti.add_episode",
                "baseline_reference": baseline,
            }
        ),
        encoding="utf-8",
    )
    verified = _verify_p8_seal(root / "seal.json", baseline_reference=baseline)
    assert verified["artifact_completeness"] == "PASS"
    seal["status"] = "P8_LIVE_STARTED"
    (root / "seal.json").write_text(__import__("json").dumps(seal), encoding="utf-8")
    with pytest.raises(P9RunnerError, match="P8 seal identity"):
        _verify_p8_seal(root / "seal.json", baseline_reference=baseline)


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


@pytest.mark.asyncio
async def test_scoped_multiplexer_does_not_mutate_shared_proxy_source_identity() -> None:
    class Delegate:
        async def generate_response(self, messages, **kwargs):
            await asyncio.sleep(0.001)
            return {"source": kwargs["prompt_name"]}

    AdmissionArbiter = __import__(
        "saturated_fixed_work_baseline_v1_3.membind_v5.runtime.core.admission",
        fromlist=["AdmissionArbiter"],
    ).AdmissionArbiter
    authority = CapacityAuthority.from_runtime(4, 4)
    store = TranscriptStore()
    capture = FrontierAwareLLMClient(
        Delegate(),
        store=store,
        arbiter=AdmissionArbiter(authority),
        mode="capture",
        durable_frontier=lambda: -1,
        client_identity={"class": "Delegate", "source_hash": "multiplexer-test"},
    )
    replay = FrontierAwareLLMClient(
        Delegate(),
        store=store,
        arbiter=AdmissionArbiter(authority),
        mode="replay",
        durable_frontier=lambda: -1,
        client_identity={"class": "Delegate", "source_hash": "multiplexer-test"},
    )
    scoped = _ScopedLLMClient(capture, replay)

    async def one(source_sequence: int) -> None:
        with provider_scope(region="PREPARE", source_sequence=source_sequence):
            await scoped.generate_response(
                [{"role": "user", "content": f"source-{source_sequence}"}],
                prompt_name="extract_nodes.extract_message",
            )

    await asyncio.gather(*(one(index) for index in range(4)))
    assert capture._proxy.source_sequence == 0
    assert replay._proxy.source_sequence == 0
