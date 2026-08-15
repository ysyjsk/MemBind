"""TDD contract for the shared offline/live M* scheduling core.

The callbacks are controlled test doubles.  The pipeline must schedule them;
it must not implement Graphiti extraction, resolution, or storage semantics.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping

import pytest

from paper_eval.s5_mstar_pipeline import (
    MStarPipelineError,
    MStarSource,
    MStarSpec,
    run_mstar_pipeline,
    verify_mstar_pipeline_evidence,
)


CORE_SHA = "a" * 64


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


def _spec() -> MStarSpec:
    return MStarSpec(
        run_id="s5-mstar-offline-001",
        production_core_identity_sha256=CORE_SHA,
        prepare_concurrency=2,
    )


def _sources(count: int = 4) -> tuple[MStarSource, ...]:
    return tuple(
        MStarSource(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            opaque_source={"source": index, "private_body": f"private-{index}"},
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_explicit_logical_operation_times_do_not_depend_on_telemetry_clock() -> None:
    sink = DurableSink()
    sources = tuple(
        MStarSource(
            source_sequence=index,
            source_sha256=f"{index + 20:064x}",
            opaque_source={"source": index},
            logical_time_ns=10_000 + index,
        )
        for index in range(2)
    )
    entered = 0
    release = asyncio.Event()
    observed: list[tuple[int, int]] = []

    async def prepare(source: object, logical_time_ns: int) -> object:
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        return source

    async def bind(
        _prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        _visible_prefix: tuple[int, ...],
    ) -> None:
        observed.append((source_sequence, logical_time_ns))

    evidence = await run_mstar_pipeline(
        spec=_spec(),
        sources=sources,
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=sink,
        clock_ns=StepClock(),
    )
    verify_mstar_pipeline_evidence(
        evidence, expected_spec=_spec(), expected_sources=sources
    )
    assert observed == [(0, 10_000), (1, 10_001)]
    assert [
        event["logical_time_ns"]
        for event in evidence["events"]
        if event["event_type"] == "intent"
    ] == [10_000, 10_001]


@pytest.mark.asyncio
async def test_publication_journal_failure_recovers_after_commit_without_rebinding() -> None:
    sink = DurableSink()
    failed_once = False
    recoveries: list[int] = []
    bind_calls: list[int] = []

    async def flaky_sink(event: Mapping[str, object]) -> None:
        nonlocal failed_once
        if event["event_type"] == "publication" and not failed_once:
            failed_once = True
            raise OSError("simulated journal gap")
        await sink(event)

    async def prepare(source: object, _logical_time_ns: int) -> object:
        await asyncio.sleep(0)
        return source["source"]

    async def bind(
        prepared: object,
        _logical_time_ns: int,
        source_sequence: int,
        _visible_prefix: tuple[int, ...],
    ) -> None:
        bind_calls.append(int(prepared))

    async def recover(source: MStarSource, _logical_time_ns: int) -> None:
        recoveries.append(source.source_sequence)

    evidence = await run_mstar_pipeline(
        spec=MStarSpec(
            run_id="s5-mstar-recovery-001",
            production_core_identity_sha256=CORE_SHA,
            prepare_concurrency=2,
            require_prepare_overlap=False,
        ),
        sources=(MStarSource(0, "a" * 64, {"source": 0}),),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=flaky_sink,
        clock_ns=StepClock(),
        recover_publication=recover,
    )
    assert evidence["status"] == "PASS"
    assert bind_calls == [0]
    assert recoveries == [0]
    assert [event["event_type"] for event in sink.events][-2:] == [
        "publication",
        "terminal_success",
    ]


@pytest.mark.asyncio
async def test_parallel_prepare_binds_and_publishes_in_source_order() -> None:
    sink = DurableSink()
    entered: list[int] = []
    release = asyncio.Event()
    prepare_times: dict[int, int] = {}
    bind_times: dict[int, int] = {}
    bound: list[int] = []
    active_binds = 0
    max_active_binds = 0

    async def prepare(source: object, logical_time_ns: int) -> object:
        sequence = int(source["source"])
        prepare_times[sequence] = logical_time_ns
        entered.append(sequence)
        if len(entered) == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        # Make source 1 finish before source 0; publication must still be 0, 1.
        if sequence == 0:
            await asyncio.sleep(0)
        return {"prepared_sequence": sequence, "private": "not-public"}

    async def bind(
        prepared: object,
        logical_time_ns: int,
        source_sequence: int,
        visible_publication_prefix: tuple[int, ...],
    ) -> None:
        nonlocal active_binds, max_active_binds
        assert prepared["prepared_sequence"] == source_sequence
        assert visible_publication_prefix == tuple(range(source_sequence))
        bind_times[source_sequence] = logical_time_ns
        active_binds += 1
        max_active_binds = max(max_active_binds, active_binds)
        await asyncio.sleep(0)
        bound.append(source_sequence)
        active_binds -= 1

    evidence = await run_mstar_pipeline(
        spec=_spec(),
        sources=_sources(),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=sink,
        clock_ns=StepClock(),
    )

    verified = verify_mstar_pipeline_evidence(
        evidence, expected_spec=_spec(), expected_sources=_sources()
    )
    assert verified["status"] == "PASS"
    assert bound == [0, 1, 2, 3]
    assert prepare_times == bind_times
    assert max_active_binds == 1
    assert evidence["summary"] == {
        "configured_prepare_concurrency": 2,
        "observed_prepare_worker_ids": [0, 1],
        "max_active_prepare": 2,
        "prepare_overlap_observed": True,
        "max_active_bind": 1,
        "intent_count": 4,
        "prepared_count": 4,
        "publication_count": 4,
        "published_source_sequences": [0, 1, 2, 3],
        "fallback_count": 0,
    }
    assert evidence["events"] == sink.events
    assert "private" not in repr(evidence)


@pytest.mark.asyncio
async def test_prepare_failure_poison_cancels_and_awaits_workers_before_terminal() -> None:
    sink = DurableSink()
    blocked_cancelled = asyncio.Event()
    source_one_started = asyncio.Event()
    bind_calls: list[int] = []

    async def prepare(source: object, _logical_time_ns: int) -> object:
        sequence = int(source["source"])
        if sequence == 0:
            await asyncio.wait_for(source_one_started.wait(), timeout=1)
            raise RuntimeError("private provider output")
        try:
            source_one_started.set()
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            blocked_cancelled.set()
            raise

    async def bind(
        _prepared: object,
        _logical_time_ns: int,
        source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
    ) -> None:
        bind_calls.append(source_sequence)

    evidence = await run_mstar_pipeline(
        spec=_spec(),
        sources=_sources(),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=sink,
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "FAIL_CLOSED"
    assert evidence["failure_code"] == "SEMANTIC_PREPARE_FAILED"
    assert evidence["events"][-1]["event_type"] == "terminal_failure"
    assert evidence["events"][-1]["error_class"] == "builtins.RuntimeError"
    assert blocked_cancelled.is_set()
    assert bind_calls == []
    assert not any(
        event["event_type"] == "publication"
        for event in evidence["events"]
    )
    assert "private provider" not in repr(evidence)
    verify_mstar_pipeline_evidence(
        evidence, expected_spec=_spec(), expected_sources=_sources()
    )


@pytest.mark.asyncio
async def test_bind_failure_keeps_only_durable_published_prefix_and_no_late_events() -> None:
    sink = DurableSink()
    bind_calls: list[int] = []

    async def prepare(source: object, _logical_time_ns: int) -> object:
        await asyncio.sleep(0)
        return int(source["source"])

    async def bind(
        prepared: object,
        _logical_time_ns: int,
        source_sequence: int,
        visible_publication_prefix: tuple[int, ...],
    ) -> None:
        assert prepared == source_sequence
        assert visible_publication_prefix == tuple(range(source_sequence))
        bind_calls.append(source_sequence)
        if source_sequence == 1:
            raise LookupError("private database detail")

    evidence = await run_mstar_pipeline(
        spec=_spec(),
        sources=_sources(),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=sink,
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "FAIL_CLOSED"
    assert evidence["failure_code"] == "LATEST_STATE_BIND_FAILED"
    assert evidence["summary"]["published_source_sequences"] == [0]
    assert bind_calls == [0, 1]
    terminal_index = next(
        index
        for index, event in enumerate(evidence["events"])
        if event["event_type"] == "terminal_failure"
    )
    assert terminal_index == len(evidence["events"]) - 1
    assert not any(
        event["event_type"] == "publication"
        and event["source_sequence"] >= 1
        for event in evidence["events"]
    )
    verify_mstar_pipeline_evidence(
        evidence, expected_spec=_spec(), expected_sources=_sources()
    )


@pytest.mark.asyncio
async def test_durable_event_failure_aborts_before_semantic_work() -> None:
    called = False

    async def broken_sink(_event: Mapping[str, object]) -> None:
        raise OSError("private path")

    async def prepare(_source: object, _logical_time_ns: int) -> object:
        nonlocal called
        called = True

    async def bind(
        _prepared: object,
        _logical_time_ns: int,
        _source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
    ) -> None:
        raise AssertionError("not reached")

    with pytest.raises(MStarPipelineError, match="durable_evidence_unavailable"):
        await run_mstar_pipeline(
            spec=_spec(),
            sources=_sources(2),
            semantic_prepare=prepare,
            latest_state_bind=bind,
            persist_event=broken_sink,
            clock_ns=StepClock(),
        )
    assert called is False


@pytest.mark.asyncio
async def test_prepare_event_failure_does_not_leave_an_unresolved_worker_future() -> None:
    calls = 0

    async def flaky_sink(_event: Mapping[str, object]) -> None:
        nonlocal calls
        calls += 1
        # All durable intents are accepted; the first prepare-start event is not.
        if calls == 3:
            raise OSError("journal unavailable")

    async def prepare(_source: object, _logical_time_ns: int) -> object:
        raise AssertionError("prepare must not run after its start event fails")

    async def bind(
        _prepared: object,
        _logical_time_ns: int,
        _source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
    ) -> None:
        raise AssertionError("bind must not run")

    with pytest.raises(MStarPipelineError, match="durable_evidence_unavailable"):
        await asyncio.wait_for(
            run_mstar_pipeline(
                spec=_spec(),
                sources=_sources(2),
                semantic_prepare=prepare,
                latest_state_bind=bind,
                persist_event=flaky_sink,
                clock_ns=StepClock(),
            ),
            timeout=1,
        )


@pytest.mark.asyncio
async def test_verifier_rejects_order_fallback_shape_and_private_drift() -> None:
    release = asyncio.Event()
    entered = 0

    async def prepare(source: object, _logical_time_ns: int) -> object:
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()
        return int(source["source"])

    async def bind(
        _prepared: object,
        _logical_time_ns: int,
        _source_sequence: int,
        _visible_publication_prefix: tuple[int, ...],
    ) -> None:
        await asyncio.sleep(0)

    evidence = await run_mstar_pipeline(
        spec=_spec(),
        sources=_sources(2),
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    mutations = []
    reordered = copy.deepcopy(evidence)
    publications = [
        event for event in reordered["events"] if event["event_type"] == "publication"
    ]
    publications[0]["source_sequence"] = 1
    mutations.append(reordered)
    fallback = copy.deepcopy(evidence)
    fallback["summary"]["fallback_count"] = 1
    mutations.append(fallback)
    extra = copy.deepcopy(evidence)
    extra["events"][0]["friendly"] = True
    mutations.append(extra)
    private = copy.deepcopy(evidence)
    private["summary"]["prompt"] = "forbidden"
    mutations.append(private)

    for invalid in mutations:
        with pytest.raises(MStarPipelineError):
            verify_mstar_pipeline_evidence(
                invalid, expected_spec=_spec(), expected_sources=_sources(2)
            )


def test_spec_and_source_reject_legacy_aliases_or_invalid_identity() -> None:
    with pytest.raises(MStarPipelineError):
        MStarSpec("s5-m2-offline-001", CORE_SHA, 2, method="M2")
    with pytest.raises(MStarPipelineError):
        MStarSpec("s5-mstar-offline-001", "x", 2)
    with pytest.raises(MStarPipelineError):
        MStarSource(-1, "b" * 64, object())


@pytest.mark.asyncio
async def test_fx0_single_case_mode_uses_same_core_without_claiming_prepare_overlap() -> None:
    spec = MStarSpec(
        run_id="s5-mstar-fx0-single-001",
        production_core_identity_sha256=CORE_SHA,
        prepare_concurrency=2,
        require_prepare_overlap=False,
    )
    sources = _sources(1)

    async def prepare(source: object, _logical_time_ns: int) -> object:
        return int(source["source"])

    async def bind(
        prepared: object,
        _logical_time_ns: int,
        source_sequence: int,
        visible_publication_prefix: tuple[int, ...],
    ) -> None:
        assert prepared == source_sequence
        assert visible_publication_prefix == ()

    evidence = await run_mstar_pipeline(
        spec=spec,
        sources=sources,
        semantic_prepare=prepare,
        latest_state_bind=bind,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    assert evidence["status"] == "PASS"
    assert evidence["summary"]["observed_prepare_worker_ids"] == [0]
    assert evidence["summary"]["prepare_overlap_observed"] is False
    verify_mstar_pipeline_evidence(evidence, expected_spec=spec, expected_sources=sources)
