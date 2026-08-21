from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from saturated_fixed_work_baseline_v1_2.contracts import (
    Availability,
    ContractError,
    EpisodeInput,
    MetricValue,
    ResumeIdentity,
    validate_resume_identity,
)
from saturated_fixed_work_baseline_v1_2.correctness import (
    CorrectnessClass,
    classify_observation,
)
from saturated_fixed_work_baseline_v1_2.schedules import (
    Method,
    ScheduleContractError,
    run_b0_native_serial,
    run_b1_naive_whole_update_async,
)


def _episodes(count: int = 4) -> tuple[EpisodeInput, ...]:
    return tuple(
        EpisodeInput(
            history_id="07741c45",
            session_id=f"session-{index}",
            source_sequence=index,
            source_hash=f"{index + 1:064x}",
            reference_time=f"2023-01-{index + 1:02d}T00:00:00Z",
            body=f"body-{index}",
            namespace="v1_2/B0/07741c45/test-run",
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_b0_feeder_is_blocking_serial() -> None:
    events: list[tuple[str, int]] = []
    active = 0
    max_active = 0

    async def add(episode: EpisodeInput) -> str:
        nonlocal active, max_active
        events.append(("start", episode.source_sequence))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        events.append(("end", episode.source_sequence))
        return episode.source_hash

    result = await run_b0_native_serial(_episodes(), add)
    assert events == [
        (event, sequence)
        for sequence in range(4)
        for event in ("start", "end")
    ]
    assert max_active == 1
    assert result.method is Method.B0_NATIVE_SERIAL
    assert result.created_sequences == (0, 1, 2, 3)
    assert result.feeder_workload_await_count == 4


@pytest.mark.asyncio
async def test_b1_feeder_is_eager_nonblocking() -> None:
    release = asyncio.Event()
    started: list[int] = []

    async def add(episode: EpisodeInput) -> int:
        started.append(episode.source_sequence)
        await release.wait()
        return episode.source_sequence

    execution = asyncio.create_task(
        run_b1_naive_whole_update_async(_episodes(), add)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert started == [0, 1, 2, 3]
    assert not execution.done()
    release.set()
    result = await execution
    assert result.created_sequences == (0, 1, 2, 3)
    assert result.feeder_workload_await_count == 0
    assert result.application_gate_count == 0
    assert result.artificial_sleep_count == 0
    assert result.configured_max_inflight is None


@pytest.mark.asyncio
async def test_future_body_is_not_passed_to_earlier_call() -> None:
    episodes = _episodes(3)
    observed: dict[int, str] = {}

    async def add(episode: EpisodeInput) -> None:
        observed[episode.source_sequence] = episode.body

    await run_b1_naive_whole_update_async(episodes, add)
    assert observed == {0: "body-0", 1: "body-1", 2: "body-2"}


@pytest.mark.asyncio
async def test_b1_drains_every_task_and_fails_block_on_any_exception() -> None:
    terminal: list[int] = []

    async def add(episode: EpisodeInput) -> int:
        await asyncio.sleep(0)
        terminal.append(episode.source_sequence)
        if episode.source_sequence in {1, 3}:
            raise RuntimeError(f"failed-{episode.source_sequence}")
        return episode.source_sequence

    with pytest.raises(ScheduleContractError) as raised:
        await run_b1_naive_whole_update_async(_episodes(), add)
    assert terminal == [0, 1, 2, 3]
    assert raised.value.failed_sequences == (1, 3)
    assert len(raised.value.outcomes) == 4


def test_future_persistent_state_read_is_an_outcome_not_harness_invalidity() -> None:
    outcome = classify_observation(
        "future_persistent_state_read", direct_causal_evidence=True
    )
    assert outcome.classification is CorrectnessClass.DIRECT_SEMANTIC_VIOLATION
    assert outcome.protocol_valid is True


def test_future_source_payload_read_is_harness_violation() -> None:
    outcome = classify_observation(
        "future_source_payload_read", direct_causal_evidence=True
    )
    assert outcome.classification is CorrectnessClass.HARNESS_VIOLATION
    assert outcome.protocol_valid is False


def test_missing_metric_availability_cannot_become_zero() -> None:
    missing = MetricValue.unavailable(
        Availability.NOT_EXPOSED_BY_PINNED_STACK, "backend_has_no_counter"
    )
    assert missing.value is None
    with pytest.raises(ContractError, match="METRIC_VALUE_FORBIDDEN"):
        MetricValue(
            availability=Availability.NOT_EXPOSED_BY_PINNED_STACK,
            value=0.0,
            reason="backend_has_no_counter",
        )


def test_resume_identity_hash_drift_fails_closed() -> None:
    expected = ResumeIdentity(
        project_sha256="1" * 64,
        data_sha256="2" * 64,
        provider_sha256="3" * 64,
        resource_sha256="4" * 64,
        config_sha256="5" * 64,
        cache_sha256="6" * 64,
        namespace="v1_2/B0/07741c45/test-run",
    )
    validate_resume_identity(expected, expected)
    for field in (
        "project_sha256",
        "data_sha256",
        "provider_sha256",
        "resource_sha256",
        "config_sha256",
        "cache_sha256",
        "namespace",
    ):
        changed = replace(
            expected,
            **({field: "f" * 64} if field != "namespace" else {field: "other"}),
        )
        with pytest.raises(ContractError, match=f"RESUME_{field.upper()}_MISMATCH"):
            validate_resume_identity(expected, changed)
