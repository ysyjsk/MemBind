from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.artifacts import AttemptStore
from saturated_fixed_work_baseline_v1_2.contracts import EpisodeInput, ResumeIdentity
from saturated_fixed_work_baseline_v1_2.instrumentation import (
    TerminalProbe,
    execute_instrumented_block,
    metric_dictionary,
)
from saturated_fixed_work_baseline_v1_2.schedules import (
    Method,
    run_b0_native_serial,
    run_b1_naive_whole_update_async,
)


def _episodes(method: Method, count: int = 3) -> tuple[EpisodeInput, ...]:
    return tuple(
        EpisodeInput(
            history_id="07741c45",
            session_id=f"s{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time="2023-01-01T00:00:00Z",
            body=f"body-{index}",
            namespace=f"v1_2/{method.value}/07741c45/integration",
        )
        for index in range(count)
    )


def _identity(method: Method) -> ResumeIdentity:
    return ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256="4" * 64,
        config_sha256="5" * 64,
        cache_sha256="6" * 64,
        namespace=f"v1_2/{method.value}/07741c45/integration",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(Method))
async def test_schedule_event_stream_proves_method_specific_feeder_contract(
    method: Method,
) -> None:
    events: list[dict[str, Any]] = []

    async def add(episode: EpisodeInput) -> int:
        await asyncio.sleep(0)
        return episode.source_sequence

    runner = (
        run_b0_native_serial
        if method is Method.B0_NATIVE_SERIAL
        else run_b1_naive_whole_update_async
    )
    await runner(_episodes(method), add, event_sink=events.append)
    created_positions = [
        index for index, event in enumerate(events) if event["event"] == "TASK_CREATED"
    ]
    return_positions = [
        index for index, event in enumerate(events) if event["event"] == "CALLER_RETURN"
    ]
    if method is Method.B0_NATIVE_SERIAL:
        assert created_positions == []
        submit_positions = [
            index for index, event in enumerate(events) if event["event"] == "SUBMIT"
        ]
        assert return_positions[0] < submit_positions[1]
        assert return_positions[1] < submit_positions[2]
    else:
        assert len(created_positions) == 3
        assert max(created_positions) < min(return_positions)
        assert sum(event["event"] == "APPLICATION_GATE" for event in events) == 0
        assert sum(event["event"] == "ARTIFICIAL_SLEEP" for event in events) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method", list(Method))
async def test_instrumented_block_separates_build_from_validation(
    tmp_path: Path, method: Method
) -> None:
    store = AttemptStore.create(tmp_path / method.value, _identity(method))
    graph = {"episodes": []}
    clock_value = 0

    def clock() -> int:
        nonlocal clock_value
        clock_value += 10
        return clock_value

    async def add(episode: EpisodeInput) -> None:
        graph["episodes"].append(episode.source_hash)
        await asyncio.sleep(0)

    snapshots = 0

    async def snapshot() -> dict[str, Any]:
        nonlocal snapshots
        snapshots += 1
        return {"episodes": sorted(graph["episodes"])}

    result = await execute_instrumented_block(
        method=method,
        episodes=_episodes(method),
        add_episode=add,
        store=store,
        snapshot_graph=snapshot,
        terminal_probe=lambda: TerminalProbe.clean(),
        service_idle=lambda: True,
        clock=clock,
    )
    assert result["valid"] is True
    assert result["episode_count"] == 3
    assert result["t0_ns"] < result["t_durable_complete_ns"] < result["t_validated_seal_ns"]
    assert result["build_makespan_ns"] == result["t_durable_complete_ns"] - result["t0_ns"]
    assert result["validation_seal_latency_ns"] == result["t_validated_seal_ns"] - result["t_durable_complete_ns"]
    assert snapshots == 2
    assert store.verify_seal()["status"] == "VALIDATED_SEALED"


def test_metric_dictionary_has_required_metadata_and_no_missing_zero() -> None:
    dictionary = metric_dictionary()
    required = {
        "name",
        "version",
        "level",
        "unit",
        "better_direction",
        "formula",
        "numerator",
        "denominator",
        "source",
        "clock",
        "attribution_scope",
        "availability",
        "core_validity_gate",
        "interpretation",
    }
    assert {"build_makespan_s", "submission_span_s", "drain_tail_s", "llm_input_tokens"}.issubset(dictionary)
    assert all(set(row) == required for row in dictionary.values())
    assert all(
        not (row["availability"] != "MEASURED" and row.get("value") == 0)
        for row in dictionary.values()
    )
