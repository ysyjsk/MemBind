"""Offline RED/GREEN tests for the parameterized S6 P* scheduler."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping

import pytest

from paper_eval.s5_native_method_adapters import S5EpisodeRef
from paper_eval.s6_pstar_grid import (
    S6PStarError,
    S6PStarSpec,
    S6TreatmentFailure,
    run_s6_pstar,
    verify_s6_pstar_evidence,
)


class StepClock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1
        return self.value


class DurableSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def __call__(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


def _episodes(count: int) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            native_episode={"opaque_episode": index},
        )
        for index in range(count)
    )


def _spec(concurrency: int) -> S6PStarSpec:
    return S6PStarSpec(
        run_id=f"s6-07741c45-pstar-c{concurrency}-001",
        configured_concurrency=concurrency,
        execution_identity_sha256="a" * 64,
    )


@pytest.mark.asyncio
async def test_c1_is_whole_update_serial_not_a0_async_enqueue() -> None:
    calls: list[int] = []

    async def native_add_episode(native_episode: object) -> None:
        calls.append(int(native_episode["opaque_episode"]))

    selected = _episodes(4)
    evidence = await run_s6_pstar(
        spec=_spec(1),
        episodes=selected,
        native_add_episode=native_add_episode,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    assert verify_s6_pstar_evidence(
        evidence, expected_spec=_spec(1), expected_episodes=selected
    ) == evidence
    assert calls == [0, 1, 2, 3]
    assert evidence["status"] == "PASS"
    assert evidence["summary"] == {
        "configured_worker_count": 1,
        "observed_worker_ids": [0],
        "max_active_calls": 1,
        "whole_update_interval_overlap_observed": False,
        "intent_count": 4,
        "caller_return_count": 4,
        "publication_count": 4,
    }
    assert not any(
        event["event_type"] == "caller_return" for event in evidence["events"]
    )
    assert all(
        event["caller_return_timestamp_ns"] == event["publish_timestamp_ns"]
        for event in evidence["events"]
        if event["event_type"] == "publication"
    )


@pytest.mark.parametrize("concurrency", [2, 4, 8])
@pytest.mark.asyncio
async def test_c_gt_1_uses_exact_workers_and_proves_real_overlap(
    concurrency: int,
) -> None:
    entered: list[int] = []
    all_entered = asyncio.Event()

    async def overlapping(native_episode: object) -> None:
        entered.append(int(native_episode["opaque_episode"]))
        if len(entered) == concurrency:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), timeout=1)
        await asyncio.sleep(0)

    selected = _episodes(concurrency * 2)
    evidence = await run_s6_pstar(
        spec=_spec(concurrency),
        episodes=selected,
        native_add_episode=overlapping,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "PASS"
    assert evidence["summary"]["configured_worker_count"] == concurrency
    assert evidence["summary"]["observed_worker_ids"] == list(range(concurrency))
    assert evidence["summary"]["max_active_calls"] == concurrency
    assert evidence["summary"]["whole_update_interval_overlap_observed"] is True
    verify_s6_pstar_evidence(
        evidence,
        expected_spec=_spec(concurrency),
        expected_episodes=selected,
    )


@pytest.mark.asyncio
async def test_all_intents_are_durable_before_any_native_update() -> None:
    sink = DurableSink()
    selected = _episodes(4)
    call_count = 0

    async def native_add_episode(_native_episode: object) -> None:
        nonlocal call_count
        if call_count == 0:
            assert [event["event_type"] for event in sink.events] == ["intent"] * 4
        call_count += 1

    await run_s6_pstar(
        spec=_spec(1),
        episodes=selected,
        native_add_episode=native_add_episode,
        persist_event=sink,
        clock_ns=StepClock(),
    )
    assert call_count == 4


@pytest.mark.asyncio
async def test_treatment_failure_classifies_every_source_without_lost_work() -> None:
    concurrency = 4
    entered: list[int] = []
    all_entered = asyncio.Event()
    failure_released = asyncio.Event()

    async def one_fails(native_episode: object) -> None:
        source = int(native_episode["opaque_episode"])
        entered.append(source)
        if len(entered) == concurrency:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), timeout=1)
        if source == 0:
            failure_released.set()
            try:
                raise RuntimeError("private provider failure")
            except RuntimeError as error:
                raise S6TreatmentFailure() from error
        await failure_released.wait()
        await asyncio.sleep(0)

    selected = _episodes(10)
    evidence = await run_s6_pstar(
        spec=_spec(concurrency),
        episodes=selected,
        native_add_episode=one_fails,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    verified = verify_s6_pstar_evidence(
        evidence,
        expected_spec=_spec(concurrency),
        expected_episodes=selected,
    )
    terminals = {
        int(event["source_sequence"]): event
        for event in verified["events"]
        if event["event_type"] == "source_terminal"
    }

    assert verified["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert verified["mergeable"] is True
    assert sorted(terminals) == list(range(10))
    assert terminals[0]["terminal_classification"] == "TREATMENT_FAILED"
    assert {
        terminals[index]["terminal_classification"] for index in range(4, 10)
    } == {"CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"}
    assert "private provider failure" not in repr(verified)


@pytest.mark.parametrize(
    "error",
    [ConnectionError("vllm disconnected"), TimeoutError("provider timeout")],
)
@pytest.mark.asyncio
async def test_infrastructure_failure_is_not_merged_as_treatment(
    error: BaseException,
) -> None:
    async def infrastructure_failure(_native_episode: object) -> None:
        raise error

    with pytest.raises(type(error)):
        await run_s6_pstar(
            spec=_spec(1),
            episodes=_episodes(2),
            native_add_episode=infrastructure_failure,
            persist_event=DurableSink(),
            clock_ns=StepClock(),
        )


@pytest.mark.asyncio
async def test_c_gt_1_without_observed_overlap_is_not_a_calibration_result() -> None:
    async def immediate(_native_episode: object) -> None:
        return None

    selected = _episodes(2)
    evidence = await run_s6_pstar(
        spec=_spec(2),
        episodes=selected,
        native_add_episode=immediate,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "FAIL_CLOSED"
    assert evidence["mergeable"] is False
    assert evidence["failure_code"] == "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED"
    verify_s6_pstar_evidence(
        evidence, expected_spec=_spec(2), expected_episodes=selected
    )


@pytest.mark.parametrize("concurrency", [0, 3, 16])
def test_spec_rejects_nonmatrix_concurrency(concurrency: int) -> None:
    with pytest.raises(S6PStarError, match="configured_concurrency_invalid"):
        _spec(concurrency)


@pytest.mark.asyncio
async def test_verifier_recomputes_terminal_accounting_and_worker_proof() -> None:
    async def immediate(_native_episode: object) -> None:
        return None

    selected = _episodes(2)
    evidence = await run_s6_pstar(
        spec=_spec(1),
        episodes=selected,
        native_add_episode=immediate,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    missing_terminal = copy.deepcopy(evidence)
    missing_terminal["events"] = [
        event
        for event in missing_terminal["events"]
        if not (
            event["event_type"] == "source_terminal"
            and event["source_sequence"] == 1
        )
    ]
    for sequence, event in enumerate(missing_terminal["events"]):
        event["event_sequence"] = sequence

    with pytest.raises(S6PStarError, match="terminal_source_accounting_invalid"):
        verify_s6_pstar_evidence(
            missing_terminal,
            expected_spec=_spec(1),
            expected_episodes=selected,
        )

    summary_drift = copy.deepcopy(evidence)
    summary_drift["summary"]["max_active_calls"] = 8
    with pytest.raises(S6PStarError):
        verify_s6_pstar_evidence(
            summary_drift,
            expected_spec=_spec(1),
            expected_episodes=selected,
        )
