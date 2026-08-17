"""Offline RED/GREEN tests for the thin S6 wrapper over the M* core."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping

import pytest

from paper_eval.s5_mstar_pipeline import MStarSource
from paper_eval.s6_mstar_grid import (
    S6MStarGridError,
    S6MStarSpec,
    run_s6_mstar,
    verify_s6_mstar_evidence,
)


class StepClock:
    def __init__(self) -> None:
        self.value = 100

    def __call__(self) -> int:
        self.value += 1
        return self.value


class DurableSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def __call__(self, event: Mapping[str, object]) -> None:
        await asyncio.sleep(0)
        self.events.append(copy.deepcopy(dict(event)))


def _spec(concurrency: int) -> S6MStarSpec:
    return S6MStarSpec(
        run_id=f"s6-07741c45-mstar-c{concurrency}-001",
        configured_concurrency=concurrency,
        production_core_identity_sha256="a" * 64,
        execution_identity_sha256="b" * 64,
    )


def _sources(count: int) -> tuple[MStarSource, ...]:
    return tuple(
        MStarSource(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            opaque_source={"opaque_source": index},
            logical_time_ns=1_000 + index,
        )
        for index in range(count)
    )


@pytest.mark.parametrize("concurrency", [1, 2, 4, 8])
def test_spec_derives_the_only_valid_overlap_policy(concurrency: int) -> None:
    spec = _spec(concurrency)
    assert spec.require_prepare_overlap is (concurrency > 1)


@pytest.mark.parametrize("concurrency", [0, 3, 16])
def test_spec_rejects_nonmatrix_concurrency(concurrency: int) -> None:
    with pytest.raises(S6MStarGridError, match="configured_concurrency_invalid"):
        _spec(concurrency)


def test_spec_rejects_run_id_concurrency_drift() -> None:
    with pytest.raises(S6MStarGridError, match="run_id_invalid"):
        S6MStarSpec(
            run_id="s6-07741c45-mstar-c4-001",
            configured_concurrency=2,
            production_core_identity_sha256="a" * 64,
            execution_identity_sha256="b" * 64,
        )


@pytest.mark.parametrize("concurrency", [1, 2, 4, 8])
@pytest.mark.asyncio
async def test_wrapper_reuses_core_and_preserves_s6_identity_and_order(
    concurrency: int,
) -> None:
    sources = _sources(concurrency * 2)
    sink = DurableSink()
    entered: list[int] = []
    all_entered = asyncio.Event()
    bound: list[int] = []

    async def prepare(source: object, _logical_time_ns: int) -> object:
        sequence = int(source["opaque_source"])
        entered.append(sequence)
        if len(entered) == concurrency:
            all_entered.set()
        await asyncio.wait_for(all_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        return {"prepared": sequence}

    async def bind(
        prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        visible_prefix: tuple[int, ...],
    ) -> None:
        assert prepared["prepared"] == source_sequence
        assert logical_time_ns == 1_000 + source_sequence
        assert visible_prefix == tuple(range(source_sequence))
        bound.append(source_sequence)
        await asyncio.sleep(0)

    evidence = await run_s6_mstar(
        spec=_spec(concurrency),
        sources=sources,
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=sink,
        clock_ns=StepClock(),
    )

    assert verify_s6_mstar_evidence(
        evidence,
        expected_spec=_spec(concurrency),
        expected_sources=sources,
    ) == evidence
    assert evidence["status"] == "PASS"
    assert evidence["configured_concurrency"] == concurrency
    assert evidence["require_prepare_overlap"] is (concurrency > 1)
    assert evidence["summary"]["observed_prepare_worker_ids"] == list(
        range(concurrency)
    )
    assert evidence["summary"]["max_active_prepare"] == concurrency
    assert evidence["summary"]["prepare_overlap_observed"] is (concurrency > 1)
    assert evidence["summary"]["max_active_bind"] == 1
    assert bound == list(range(len(sources)))
    assert evidence["events"] == sink.events
    assert all(event["run_id"] == _spec(concurrency).run_id for event in sink.events)


@pytest.mark.asyncio
async def test_c_gt_1_without_prepare_overlap_fails_wrapper_qualification() -> None:
    async def immediate_prepare(source: object, _logical_time_ns: int) -> object:
        return source

    async def bind(*_args: object) -> None:
        return None

    async def immediate_sink(_event: Mapping[str, object]) -> None:
        return None

    with pytest.raises(S6MStarGridError, match="mstar_core_qualification_failed"):
        await run_s6_mstar(
            spec=_spec(2),
            sources=_sources(2),
            semantic_prepare=immediate_prepare,
            latest_state_bind=bind,
            persist_event=immediate_sink,
            clock_ns=StepClock(),
        )


@pytest.mark.asyncio
async def test_provider_failure_propagates_instead_of_becoming_calibration_result() -> None:
    async def disconnected(_source: object, _logical_time_ns: int) -> object:
        raise ConnectionError("vllm disconnected")

    async def bind(*_args: object) -> None:
        return None

    with pytest.raises(ConnectionError, match="vllm disconnected"):
        await run_s6_mstar(
            spec=_spec(1),
            sources=_sources(2),
            semantic_prepare=disconnected,
            latest_state_bind=bind,
            persist_event=DurableSink(),
            clock_ns=StepClock(),
        )


@pytest.mark.asyncio
async def test_verifier_rejects_outer_identity_and_inner_semantic_tamper() -> None:
    async def prepare(source: object, _logical_time_ns: int) -> object:
        return source

    async def bind(*_args: object) -> None:
        return None

    sources = _sources(2)
    evidence = await run_s6_mstar(
        spec=_spec(1),
        sources=sources,
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    for mutation in (
        lambda value: value.update(execution_identity_sha256="c" * 64),
        lambda value: value["events"][0].update(run_id="s6-wrong"),
        lambda value: value["summary"].update(max_active_bind=2),
    ):
        altered = copy.deepcopy(evidence)
        mutation(altered)
        with pytest.raises(S6MStarGridError):
            verify_s6_mstar_evidence(
                altered,
                expected_spec=_spec(1),
                expected_sources=sources,
            )
