"""TDD contracts for bounded MemBind-v1 compile/bind overlap."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from paper_eval.membind_v1.delta import PreparedNodeArtifact
from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.graphiti_adapter import MemBindV1BindObservation
from paper_eval.membind_v1.runner import run_membind_v1
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v1.store import MemBindV1AttemptStore, inspect_membind_v1_attempt


def _record(sequence: int) -> SourceRecord:
    return SourceRecord.create(
        source_sequence=sequence,
        episode_uuid=f"episode-{sequence}",
        group_id="runner-test",
        reference_time_ns=100 + sequence,
        source_filter="message",
        episode_projection={"body": f"body-{sequence}", "name": f"episode-{sequence}"},
    )


def _inputs(count: int = 3):
    log = SourceLog.create([_record(index) for index in range(count)])
    return tuple(
        build_compile_input(
            log.record(index),
            EvidenceFence.capture(log, target_source_sequence=index, last_n=10),
        )
        for index in range(count)
    ), log


def _artifact(compile_input) -> PreparedNodeArtifact:
    source = compile_input.source
    return PreparedNodeArtifact.create(
        source_sequence=source.source_sequence,
        source_sha256=source.source_sha256,
        evidence_prefix_sha256=compile_input.evidence.evidence_prefix_sha256,
        episode_projection_sha256=source.episode_projection_sha256,
        operation_identity_sha256="1" * 64,
        model_identity_sha256="2" * 64,
        prompt_identity_sha256="3" * 64,
        schema_identity_sha256="4" * 64,
        config_identity_sha256="5" * 64,
        extracted_nodes=[{"name": f"N{source.source_sequence}", "uuid": f"n{source.source_sequence}"}],
        node_episode_index_map={f"n{source.source_sequence}": [0]},
    )


@dataclass
class _FakeAdapter:
    compile_inputs: list[int]
    binds: list[int]
    active: int = 0
    observed_max_active: int = 0

    async def prepare(self, compile_input):
        sequence = compile_input.source.source_sequence
        self.compile_inputs.append(sequence)
        self.active += 1
        self.observed_max_active = max(self.observed_max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return _artifact(compile_input)

    async def bind(self, compile_input, artifact, *, logical_time_ns: int):
        sequence = compile_input.source.source_sequence
        self.binds.append(sequence)
        self.active += 1
        self.observed_max_active = max(self.observed_max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return MemBindV1BindObservation(
            source_sequence=sequence,
            resolved_node_count=1,
            resolved_edge_count=0,
            invalidated_edge_count=0,
            commit_result_type="tuple",
        )


def test_runner_overlaps_one_next_compile_with_current_bind_and_publishes_in_order(tmp_path) -> None:
    compile_inputs, log = _inputs()
    store = MemBindV1AttemptStore.create(
        tmp_path / "attempt",
        run_id="mv1-runner-test-001",
        namespace="pev3-mv1-runner-test-001-u0-runner-a001",
        source_sha256s=tuple(record.source_sha256 for record in log.records),
        source_manifest_sha256=log.inventory_sha256,
        execution_identity_sha256="a" * 64,
    )
    adapter = _FakeAdapter([], [])

    result = asyncio.run(
        run_membind_v1(
            compile_inputs=compile_inputs,
            logical_time_ns=(1_700_000_000_000_000_000,) * len(compile_inputs),
            arrival_time_ns=(0, 0, 0),
            adapter=adapter,
            store=store,
        )
    )

    assert result["status"] == "PASS"
    assert adapter.binds == [0, 1, 2]
    assert sorted(adapter.compile_inputs) == [0, 1, 2]
    assert adapter.observed_max_active >= 2
    checked = inspect_membind_v1_attempt(tmp_path / "attempt")
    assert checked["checkpoint"]["status"] == "complete"
    assert checked["checkpoint"]["published_frontier"] == 2
    assert result["observed_bounds"]["prepared_lookahead"] == 1


def test_runner_resumes_verified_prepared_prefix_without_repreparing_it(tmp_path) -> None:
    """A recovery binds durable artifacts and prepares only the missing suffix."""

    compile_inputs, log = _inputs()
    root = tmp_path / "attempt"
    initial_store = MemBindV1AttemptStore.create(
        root,
        run_id="mv1-runner-test-003",
        namespace="pev3-mv1-runner-test-003-u0-runner-a001",
        source_sha256s=tuple(record.source_sha256 for record in log.records),
        source_manifest_sha256=log.inventory_sha256,
        execution_identity_sha256="a" * 64,
    )
    # Simulate a process failure after two artifact writes but before the first
    # bind.  `open_existing` verifies the on-disk manifest, event log, and
    # each artifact before handing the attempt to the runner.
    for sequence in (0, 1):
        initial_store.record_intent(sequence)
        initial_store.record_prepare_started(sequence)
        initial_store.persist_prepared(_artifact(compile_inputs[sequence]))

    store = MemBindV1AttemptStore.open_existing(root)
    adapter = _FakeAdapter([], [])

    result = asyncio.run(
        run_membind_v1(
            compile_inputs=compile_inputs,
            logical_time_ns=(1_700_000_000_000_000_000,) * len(compile_inputs),
            arrival_time_ns=(0, 0, 0),
            adapter=adapter,
            store=store,
        )
    )

    assert result["status"] == "PASS"
    assert result["resumed_prepared_source_count"] == 2
    assert adapter.compile_inputs == [2]
    assert adapter.binds == [0, 1, 2]
    checked = inspect_membind_v1_attempt(root)
    assert checked["checkpoint"]["status"] == "complete"
    assert checked["checkpoint"]["published_frontier"] == 2


def test_runner_rejects_noncontiguous_compile_inputs_before_mutating_store(tmp_path) -> None:
    compile_inputs, log = _inputs()
    store = MemBindV1AttemptStore.create(
        tmp_path / "attempt",
        run_id="mv1-runner-test-002",
        namespace="pev3-mv1-runner-test-002-u0-runner-a001",
        source_sha256s=tuple(record.source_sha256 for record in log.records),
        source_manifest_sha256=log.inventory_sha256,
        execution_identity_sha256="a" * 64,
    )
    adapter = _FakeAdapter([], [])

    try:
        asyncio.run(
            run_membind_v1(
                compile_inputs=(compile_inputs[1], compile_inputs[2]),
                logical_time_ns=(1, 2),
                arrival_time_ns=(0, 0),
                adapter=adapter,
                store=store,
            )
        )
    except ValueError as error:
        assert "source" in str(error)
    else:
        raise AssertionError("runner accepted a nonzero source prefix")
    assert adapter.compile_inputs == []
    assert inspect_membind_v1_attempt(tmp_path / "attempt")["events"] == []


def test_runner_emits_aligned_durable_lifecycle_events_without_exposing_artifact_content(tmp_path) -> None:
    async def scenario() -> list[tuple[str, int, int]]:
        compile_inputs, log = _inputs(2)
        store = MemBindV1AttemptStore.create(
            tmp_path / "attempt",
            run_id="mv1-runner-test-003",
            namespace="pev3-mv1-runner-test-003-u0-runner-a001",
            source_sha256s=tuple(record.source_sha256 for record in log.records),
            source_manifest_sha256=log.inventory_sha256,
            execution_identity_sha256="a" * 64,
        )
        observed: list[tuple[str, int, int]] = []

        async def observer(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
            observed.append((event_type, source_sequence, timestamp_ns))

        await run_membind_v1(
            compile_inputs=compile_inputs,
            logical_time_ns=(1_700_000_000_000_000_000,) * 2,
            arrival_time_ns=(0, 0),
            adapter=_FakeAdapter([], []),
            store=store,
            lifecycle_observer=observer,
        )
        return observed

    observed = asyncio.run(scenario())

    assert [event_type for event_type, _source, _timestamp in observed] == [
        "ARRIVAL",
        "ENQUEUED",
        "SERVICE_STARTED",
        "ARRIVAL",
        "ENQUEUED",
        "PUBLICATION_DURABLE",
        "SERVICE_STARTED",
        "PUBLICATION_DURABLE",
    ]
    assert all("artifact" not in repr(row) for row in observed)


def test_live_runner_samples_native_equivalent_wall_clock_at_each_bind(tmp_path) -> None:
    compile_inputs, log = _inputs(2)
    store = MemBindV1AttemptStore.create(
        tmp_path / "attempt",
        run_id="mv1-runner-test-wall-clock",
        namespace="pev3-mv1-runner-wall-clock-a001",
        source_sha256s=tuple(record.source_sha256 for record in log.records),
        source_manifest_sha256=log.inventory_sha256,
        execution_identity_sha256="a" * 64,
    )
    observed: list[int] = []

    class CapturingAdapter(_FakeAdapter):
        async def bind(self, compile_input, artifact, *, logical_time_ns: int):
            observed.append(logical_time_ns)
            return await super().bind(
                compile_input,
                artifact,
                logical_time_ns=logical_time_ns,
            )

    wall_times = iter((1_700_000_000_000_000_101, 1_700_000_000_000_000_202))
    asyncio.run(
        run_membind_v1(
            compile_inputs=compile_inputs,
            logical_time_ns=None,
            logical_clock_ns=lambda: next(wall_times),
            arrival_time_ns=(0, 0),
            adapter=CapturingAdapter([], []),
            store=store,
        )
    )

    assert observed == [
        1_700_000_000_000_000_101,
        1_700_000_000_000_000_202,
    ]


def test_runner_resume_does_not_repeat_arrival_or_enqueue_for_prepared_sources(tmp_path) -> None:
    compile_inputs, log = _inputs()
    root = tmp_path / "attempt"
    initial_store = MemBindV1AttemptStore.create(
        root,
        run_id="mv1-runner-test-004",
        namespace="pev3-mv1-runner-test-004-u0-runner-a001",
        source_sha256s=tuple(record.source_sha256 for record in log.records),
        source_manifest_sha256=log.inventory_sha256,
        execution_identity_sha256="a" * 64,
    )
    for sequence in (0, 1):
        initial_store.record_intent(sequence)
        initial_store.record_prepare_started(sequence)
        initial_store.persist_prepared(_artifact(compile_inputs[sequence]))

    observed: list[tuple[str, int, int]] = []

    async def observer(event_type: str, source_sequence: int, timestamp_ns: int) -> None:
        observed.append((event_type, source_sequence, timestamp_ns))

    asyncio.run(
        run_membind_v1(
            compile_inputs=compile_inputs,
            logical_time_ns=(1_700_000_000_000_000_000,) * len(compile_inputs),
            arrival_time_ns=(0, 0, 0),
            adapter=_FakeAdapter([], []),
            store=MemBindV1AttemptStore.open_existing(root),
            lifecycle_observer=observer,
        )
    )

    arrivals_and_enqueues = [
        (event_type, source_sequence)
        for event_type, source_sequence, _timestamp_ns in observed
        if event_type in {"ARRIVAL", "ENQUEUED"}
    ]
    assert arrivals_and_enqueues == [("ARRIVAL", 2), ("ENQUEUED", 2)]
    assert [
        (event_type, source_sequence)
        for event_type, source_sequence, _timestamp_ns in observed
        if source_sequence in {0, 1}
    ] == [
        ("SERVICE_STARTED", 0),
        ("PUBLICATION_DURABLE", 0),
        ("SERVICE_STARTED", 1),
        ("PUBLICATION_DURABLE", 1),
    ]
