"""Offline contract tests for the S5 A0 and P(C=2) native adapters.

The tests inject the only construction operation, ``native_add_episode``.  No
model, network, Graphiti client, or Neo4j service is constructed in this lane.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import fields

import pytest

from paper_eval.s5_native_method_adapters import (
    A0,
    P_STAR,
    S5AdapterError,
    S5EpisodeRef,
    S5MethodSpec,
    run_a0,
    run_p_c2,
    verify_s5_native_method_evidence,
)


NATIVE_PATH_SHA = "a" * 64


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
        self.events.append(dict(event))


def episodes(count: int = 4) -> tuple[S5EpisodeRef, ...]:
    return tuple(
        S5EpisodeRef(
            source_sequence=index,
            source_sha256=f"{index + 1:064x}",
            native_episode={"opaque_episode": index, "body": f"private-{index}"},
        )
        for index in range(count)
    )


def spec(method: str) -> S5MethodSpec:
    return S5MethodSpec(
        run_id=f"s5-{method.casefold().replace('*', 'star')}-offline-001",
        method=method,
        native_path_identity_sha256=NATIVE_PATH_SHA,
    )


@pytest.mark.asyncio
async def test_a0_is_fifo_single_worker_and_returns_after_durable_enqueue_ack():
    sink = DurableSink()
    clock = StepClock()
    calls: list[int] = []

    async def native_add_episode(native_episode: object) -> None:
        calls.append(int(native_episode["opaque_episode"]))
        await asyncio.sleep(0)

    evidence = await run_a0(
        spec=spec(A0),
        episodes=episodes(),
        native_add_episode=native_add_episode,
        persist_event=sink,
        clock_ns=clock,
    )

    verified = verify_s5_native_method_evidence(
        evidence, expected_spec=spec(A0), expected_episodes=episodes()
    )
    assert verified["status"] == "PASS"
    assert calls == [0, 1, 2, 3]
    assert sink.events == evidence["events"]
    assert evidence["summary"] == {
        "configured_worker_count": 1,
        "observed_worker_ids": [0],
        "max_active_calls": 1,
        "whole_update_interval_overlap_observed": False,
        "intent_count": 4,
        "caller_return_count": 4,
        "publication_count": 4,
    }

    by_source: dict[int, dict[str, dict[str, object]]] = {}
    for event in evidence["events"]:
        source = event.get("source_sequence")
        if isinstance(source, int):
            by_source.setdefault(source, {})[str(event["event_type"])] = event
    publications = []
    for source in range(4):
        intent = by_source[source]["intent"]
        returned = by_source[source]["caller_return"]
        publication = by_source[source]["publication"]
        assert intent["intent_timestamp_ns"] <= returned["durable_enqueue_ack_timestamp_ns"]
        assert returned["caller_return_timestamp_ns"] == returned["durable_enqueue_ack_timestamp_ns"]
        assert returned["caller_return_timestamp_ns"] <= publication["service_start_timestamp_ns"]
        assert publication["caller_return_timestamp_ns"] == returned["caller_return_timestamp_ns"]
        publications.append(publication["source_sequence"])
    assert publications == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_a0_never_starts_native_service_before_intent_is_durable():
    durable_sources: set[int] = set()
    call_sources: list[int] = []

    async def persist(event: Mapping[str, object]) -> None:
        await asyncio.sleep(0)
        if event["event_type"] == "intent":
            durable_sources.add(int(event["source_sequence"]))

    async def native_add_episode(native_episode: object) -> None:
        source = int(native_episode["opaque_episode"])
        assert source in durable_sources
        call_sources.append(source)

    evidence = await run_a0(
        spec=spec(A0),
        episodes=episodes(3),
        native_add_episode=native_add_episode,
        persist_event=persist,
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "PASS"
    assert call_sources == [0, 1, 2]


@pytest.mark.asyncio
async def test_p_c2_uses_exactly_two_workers_and_observes_whole_update_overlap():
    sink = DurableSink()
    clock = StepClock()
    entered: list[int] = []
    both_entered = asyncio.Event()

    async def native_add_episode(native_episode: object) -> None:
        entered.append(int(native_episode["opaque_episode"]))
        if len(entered) >= 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        await asyncio.sleep(0)

    evidence = await run_p_c2(
        spec=spec(P_STAR),
        episodes=episodes(4),
        native_add_episode=native_add_episode,
        persist_event=sink,
        clock_ns=clock,
    )

    verified = verify_s5_native_method_evidence(
        evidence, expected_spec=spec(P_STAR), expected_episodes=episodes(4)
    )
    assert verified["status"] == "PASS"
    assert evidence["summary"]["configured_worker_count"] == 2
    assert evidence["summary"]["observed_worker_ids"] == [0, 1]
    assert evidence["summary"]["max_active_calls"] == 2
    assert evidence["summary"]["whole_update_interval_overlap_observed"] is True
    assert len([e for e in evidence["events"] if e["event_type"] == "intent"]) == 4
    assert len([e for e in evidence["events"] if e["event_type"] == "publication"]) == 4


@pytest.mark.asyncio
async def test_p_c2_fails_closed_when_two_real_intervals_do_not_overlap():
    async def immediate_native_add_episode(_native_episode: object) -> None:
        return None

    evidence = await run_p_c2(
        spec=spec(P_STAR),
        episodes=episodes(2),
        native_add_episode=immediate_native_add_episode,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    assert evidence["status"] == "FAIL_CLOSED"
    assert evidence["failure_code"] == "WHOLE_UPDATE_OVERLAP_NOT_OBSERVED"
    assert evidence["events"][-1]["event_type"] == "treatment_failure"
    verify_s5_native_method_evidence(
        evidence, expected_spec=spec(P_STAR), expected_episodes=episodes(2)
    )


@pytest.mark.asyncio
async def test_p_c2_treatment_failure_classifies_every_scheduled_source():
    """A stopped queue is censored explicitly, never mistaken for lost work."""

    both_initial_workers_entered = asyncio.Event()
    failure_released = asyncio.Event()
    entered: list[int] = []

    async def one_worker_fails(native_episode: object) -> None:
        source = int(native_episode["opaque_episode"])
        entered.append(source)
        if len(entered) == 2:
            both_initial_workers_entered.set()
        await asyncio.wait_for(both_initial_workers_entered.wait(), timeout=1)
        if source == 0:
            failure_released.set()
            raise RuntimeError("private treatment failure")
        await failure_released.wait()
        # Let worker 0 durably establish the stop condition before this worker
        # can take another queued source.
        await asyncio.sleep(0)

    selected = episodes(49)
    evidence = await run_p_c2(
        spec=spec(P_STAR),
        episodes=selected,
        native_add_episode=one_worker_fails,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    verified = verify_s5_native_method_evidence(
        evidence, expected_spec=spec(P_STAR), expected_episodes=selected
    )
    terminals = [
        event for event in verified["events"] if event["event_type"] == "source_terminal"
    ]
    by_source = {event["source_sequence"]: event for event in terminals}

    assert entered == [0, 1]
    assert verified["status"] == "SCIENTIFIC_OUTCOME_COMPLETE"
    assert verified["mergeable"] is True
    assert len(terminals) == 49
    assert sorted(by_source) == list(range(49))
    assert by_source[0]["terminal_classification"] == "TREATMENT_FAILED"
    assert isinstance(by_source[0]["service_start_timestamp_ns"], int)
    assert (
        by_source[0]["terminal_timestamp_ns"]
        >= by_source[0]["service_start_timestamp_ns"]
    )
    assert by_source[1]["terminal_classification"] == "PUBLISHED"
    assert {
        by_source[source]["terminal_classification"] for source in range(2, 49)
    } == {"CENSORED_NOT_STARTED_AFTER_TREATMENT_FAILURE"}
    assert all(
        by_source[source]["service_start_timestamp_ns"] is None
        and by_source[source]["terminal_timestamp_ns"] is None
        for source in range(2, 49)
    )
    assert "private treatment failure" not in repr(verified)


@pytest.mark.asyncio
async def test_p_c2_scientific_outcome_fails_closed_on_terminal_accounting_loss():
    ready = asyncio.Event()

    async def one_worker_fails(native_episode: object) -> None:
        source = int(native_episode["opaque_episode"])
        if source == 1:
            ready.set()
        await ready.wait()
        if source == 0:
            raise RuntimeError("private treatment failure")
        await asyncio.sleep(0)

    selected = episodes(5)
    evidence = await run_p_c2(
        spec=spec(P_STAR),
        episodes=selected,
        native_add_episode=one_worker_fails,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    incomplete = {**evidence, "events": [dict(event) for event in evidence["events"]]}
    incomplete["events"] = [
        event
        for event in incomplete["events"]
        if not (
            event["event_type"] == "source_terminal"
            and event["source_sequence"] == 4
        )
    ]
    for sequence, event in enumerate(incomplete["events"]):
        event["event_sequence"] = sequence

    with pytest.raises(S5AdapterError, match="terminal_source_accounting_invalid"):
        verify_s5_native_method_evidence(
            incomplete, expected_spec=spec(P_STAR), expected_episodes=selected
        )

    missing_failure_interval = {
        **evidence,
        "events": [dict(event) for event in evidence["events"]],
    }
    failed = next(
        event
        for event in missing_failure_interval["events"]
        if event.get("terminal_classification") == "TREATMENT_FAILED"
    )
    failed["service_start_timestamp_ns"] = None
    with pytest.raises(S5AdapterError, match="failed_source_terminal_invalid"):
        verify_s5_native_method_evidence(
            missing_failure_interval,
            expected_spec=spec(P_STAR),
            expected_episodes=selected,
        )


@pytest.mark.asyncio
async def test_p_c2_does_not_return_scientific_outcome_without_durable_classification():
    ready = asyncio.Event()

    async def one_worker_fails(native_episode: object) -> None:
        source = int(native_episode["opaque_episode"])
        if source == 1:
            ready.set()
        await ready.wait()
        if source == 0:
            raise RuntimeError("private treatment failure")
        await asyncio.sleep(0)

    async def classification_sink_fails(event: Mapping[str, object]) -> None:
        if event["event_type"] == "source_terminal":
            raise OSError("private telemetry path")

    with pytest.raises(S5AdapterError, match="durable_evidence_unavailable"):
        await run_p_c2(
            spec=spec(P_STAR),
            episodes=episodes(5),
            native_add_episode=one_worker_fails,
            persist_event=classification_sink_fails,
            clock_ns=StepClock(),
        )


@pytest.mark.parametrize("method", [A0, P_STAR])
@pytest.mark.asyncio
async def test_native_treatment_failure_is_sanitized_and_durably_classified(method: str):
    async def failing_native_add_episode(_native_episode: object) -> None:
        raise RuntimeError("private provider text and credential-like data")

    runner = run_a0 if method == A0 else run_p_c2
    evidence = await runner(
        spec=spec(method),
        episodes=episodes(2),
        native_add_episode=failing_native_add_episode,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    assert evidence["status"] == (
        "FAIL_CLOSED" if method == A0 else "SCIENTIFIC_OUTCOME_COMPLETE"
    )
    assert evidence["mergeable"] is (method == P_STAR)
    assert evidence["failure_code"] == "NATIVE_ADD_EPISODE_FAILED"
    terminal = evidence["events"][-1]
    assert terminal["event_type"] == "treatment_failure"
    assert terminal["error_class"] == "builtins.RuntimeError"
    assert "error_message" not in terminal
    assert "private provider" not in repr(evidence)
    verify_s5_native_method_evidence(
        evidence, expected_spec=spec(method), expected_episodes=episodes(2)
    )


def test_spec_has_no_legacy_authority_or_namespace_input_surface():
    assert {field.name for field in fields(S5MethodSpec)} == {
        "run_id",
        "method",
        "native_path_identity_sha256",
    }
    with pytest.raises(TypeError):
        S5MethodSpec(
            run_id="s5-a0-offline-001",
            method=A0,
            native_path_identity_sha256=NATIVE_PATH_SHA,
            namespace="legacy",  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        S5MethodSpec(
            run_id="s5-a0-offline-001",
            method=A0,
            native_path_identity_sha256=NATIVE_PATH_SHA,
            authority={"live": True},  # type: ignore[call-arg]
        )


@pytest.mark.parametrize(
    "field_name",
    ["namespace", "authority", "api_key", "password", "prompt", "raw_response"],
)
def test_public_evidence_private_or_legacy_fields_fail_closed(field_name: str):
    invalid = {
        "schema_version": "membind.paper-eval-v3.s5-native-method-evidence.v1",
        "run_id": "s5-a0-offline-001",
        "method": A0,
        "native_path_identity_sha256": NATIVE_PATH_SHA,
        "status": "PASS",
        "mergeable": True,
        "failure_code": None,
        "events": [],
        "summary": {field_name: "forbidden"},
    }
    with pytest.raises(S5AdapterError, match="private_or_legacy_field"):
        verify_s5_native_method_evidence(
            invalid, expected_spec=spec(A0), expected_episodes=episodes(1)
        )


@pytest.mark.asyncio
async def test_invalid_method_binding_and_episode_identity_fail_before_native_call():
    called = False

    async def should_not_call(_native_episode: object) -> None:
        nonlocal called
        called = True

    with pytest.raises(S5AdapterError, match="method_binding_invalid"):
        await run_a0(
            spec=spec(P_STAR),
            episodes=episodes(2),
            native_add_episode=should_not_call,
            persist_event=DurableSink(),
            clock_ns=StepClock(),
        )
    malformed = (
        S5EpisodeRef(0, "1" * 64, object()),
        S5EpisodeRef(2, "2" * 64, object()),
    )
    with pytest.raises(S5AdapterError, match="source_sequence_not_contiguous"):
        await run_p_c2(
            spec=spec(P_STAR),
            episodes=malformed,
            native_add_episode=should_not_call,
            persist_event=DurableSink(),
            clock_ns=StepClock(),
        )
    assert called is False


@pytest.mark.asyncio
async def test_durable_sink_failure_stops_before_native_construction():
    called = False

    async def broken_sink(_event: Mapping[str, object]) -> None:
        raise OSError("private storage path")

    async def should_not_call(_native_episode: object) -> None:
        nonlocal called
        called = True

    with pytest.raises(S5AdapterError, match="durable_evidence_unavailable"):
        await run_a0(
            spec=spec(A0),
            episodes=episodes(1),
            native_add_episode=should_not_call,
            persist_event=broken_sink,
            clock_ns=StepClock(),
        )
    assert called is False


@pytest.mark.asyncio
async def test_verifier_rejects_safe_looking_shape_and_terminal_summary_drift():
    async def native_add_episode(_native_episode: object) -> None:
        await asyncio.sleep(0)

    expected_spec = spec(A0)
    expected_episodes = episodes(2)
    evidence = await run_a0(
        spec=expected_spec,
        episodes=expected_episodes,
        native_add_episode=native_add_episode,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )

    extra_summary = {**evidence, "summary": {**evidence["summary"], "friendly": 1}}
    with pytest.raises(S5AdapterError, match="summary_shape_invalid"):
        verify_s5_native_method_evidence(
            extra_summary,
            expected_spec=expected_spec,
            expected_episodes=expected_episodes,
        )

    extra_event = {**evidence, "events": [dict(item) for item in evidence["events"]]}
    extra_event["events"][0]["friendly"] = 1
    with pytest.raises(S5AdapterError, match="event_shape_invalid"):
        verify_s5_native_method_evidence(
            extra_event,
            expected_spec=expected_spec,
            expected_episodes=expected_episodes,
        )

    terminal_drift = {**evidence, "events": [dict(item) for item in evidence["events"]]}
    terminal_drift["events"][-1]["publication_count"] = 1
    with pytest.raises(S5AdapterError, match="terminal_summary_invalid"):
        verify_s5_native_method_evidence(
            terminal_drift,
            expected_spec=expected_spec,
            expected_episodes=expected_episodes,
        )


@pytest.mark.asyncio
async def test_verifier_recomputes_a0_and_p_timestamp_semantics():
    async def native_add_episode(_native_episode: object) -> None:
        await asyncio.sleep(0)

    a0_spec = spec(A0)
    a0_episodes = episodes(2)
    a0_evidence = await run_a0(
        spec=a0_spec,
        episodes=a0_episodes,
        native_add_episode=native_add_episode,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    invalid_a0 = {**a0_evidence, "events": [dict(item) for item in a0_evidence["events"]]}
    intent = next(item for item in invalid_a0["events"] if item["event_type"] == "intent")
    returned = next(
        item
        for item in invalid_a0["events"]
        if item["event_type"] == "caller_return"
        and item["source_sequence"] == intent["source_sequence"]
    )
    returned["durable_enqueue_ack_timestamp_ns"] = intent["intent_timestamp_ns"] - 1
    returned["caller_return_timestamp_ns"] = intent["intent_timestamp_ns"] - 1
    with pytest.raises(S5AdapterError, match="a0_caller_return_or_worker_invalid"):
        verify_s5_native_method_evidence(
            invalid_a0, expected_spec=a0_spec, expected_episodes=a0_episodes
        )

    entered: list[int] = []
    ready = asyncio.Event()

    async def overlapping(native_episode: object) -> None:
        entered.append(int(native_episode["opaque_episode"]))
        if len(entered) == 2:
            ready.set()
        await ready.wait()
        await asyncio.sleep(0)

    p_spec = spec(P_STAR)
    p_episodes = episodes(2)
    p_evidence = await run_p_c2(
        spec=p_spec,
        episodes=p_episodes,
        native_add_episode=overlapping,
        persist_event=DurableSink(),
        clock_ns=StepClock(),
    )
    invalid_p = {**p_evidence, "events": [dict(item) for item in p_evidence["events"]]}
    publication = next(
        item for item in invalid_p["events"] if item["event_type"] == "publication"
    )
    publication["caller_return_timestamp_ns"] = publication["publish_timestamp_ns"] - 1
    with pytest.raises(S5AdapterError, match="p_caller_return_invalid"):
        verify_s5_native_method_evidence(
            invalid_p, expected_spec=p_spec, expected_episodes=p_episodes
        )
