"""TDD tests for the oracle-free M* FX0 production adapter."""

from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from paper_eval.fx0_mechanism_fixture import (
    ControlledNondeterminism,
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
    Fx0ExecutionCase,
    Fx0FixtureCase,
    Fx0FixtureSpec,
    MechanismOutcome,
    run_fx0_fixture,
)
from paper_eval.s5_mstar_production_adapter import (
    Fx0DecodedSource,
    S5MStarProductionAdapter,
    S5MStarProductionAdapterError,
)
from paper_eval.s5_mstar_production_core_identity import (
    build_s5_mstar_production_core_identity,
)


CORE_IDENTITY = build_s5_mstar_production_core_identity(
    graphiti_version="0.29.3",
    graphiti_commit="021d3a57d511f21b10adaf7fa923bd5c1fce5e9d",
    graphiti_semantic_api_sha256="a" * 64,
    graphiti_semantic_identity_artifact_sha256="b" * 64,
    runtime_factory_entrypoint="native_characterization_runtime.build_u0_graphiti_from_env",
    runtime_factory_source_sha256="c" * 64,
    pipeline_source_sha256="d" * 64,
    pipeline_test_source_sha256="e" * 64,
    adapter_source_sha256="f" * 64,
    adapter_test_source_sha256="1" * 64,
    semantic_runtime_source_sha256="2" * 64,
    semantic_runtime_test_source_sha256="3" * 64,
    semantic_binding_source_sha256="4" * 64,
    semantic_binding_test_source_sha256="5" * 64,
    durable_store_source_sha256="6" * 64,
    durable_store_test_source_sha256="7" * 64,
    runtime_config_sha256="8" * 64,
)
IDENTITY = {
    "status": "FROZEN",
    "method": "M_STAR",
    "identity_sha256": CORE_IDENTITY["identity_sha256"],
}


def _providers() -> ControlledNondeterminism:
    return ControlledNondeterminism(
        llm_responses={"entity": "Alice"},
        embeddings={"entity": [0.1, 0.2]},
        logical_times=("2026-01-01T00:00:00Z",),
        initial_state={"nodes": [], "relationships": []},
        candidate_sets=({"candidate": "Alice"},),
    )


def _case(*, status: str = "PASS", error_code: str | None = None) -> Fx0FixtureCase:
    return Fx0FixtureCase(
        case_id="fx0-case",
        transition=(
            "ENTITY_ALIAS_CANONICAL_MERGE"
            if status == "PASS"
            else "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED"
        ),
        source_sequence=0,
        source={"key": "Alice"},
        providers=_providers(),
        expected_status=status,
        expected_error_code=error_code,
        expected_canonical_logical_state=(
            {"nodes": [{"key": "Alice"}], "relationships": []}
            if status == "PASS"
            else {"nodes": [], "relationships": []}
        ),
        expected_publication_history=(
            ({"source_sequence": 0, "event": "publish"},)
            if status == "PASS"
            else ()
        ),
    )


def _adapter(*, fail: str | None = None, events: list[dict] | None = None):
    state = {"nodes": [], "relationships": []}
    history: list[dict] = []

    async def prepare(source, _logical_time, _providers):
        return {"key": source["key"]}

    async def bind(prepared, _logical_time, source_sequence, _prefix, _providers):
        if fail is not None:
            raise S5MStarProductionAdapterError(fail)
        state["nodes"] = [{"key": prepared["key"]}]
        history.append({"source_sequence": source_sequence, "event": "publish"})
        return {
            "canonical_logical_state": deepcopy(state),
            "publication_history": deepcopy(history),
        }

    def snapshot():
        return deepcopy(state), deepcopy(history)

    async def persist(event):
        if events is not None:
            events.append(dict(event))

    return S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=snapshot,
        persist_event=persist if events is not None else None,
    )


def test_adapter_receives_oracle_free_input_and_runs_shared_core() -> None:
    case = _case()
    seen: list[object] = []
    events: list[dict] = []
    adapter = _adapter(events=events)
    original_prepare = adapter.semantic_prepare

    async def recording_prepare(source, logical_time, providers):
        seen.append((source, providers))
        return await original_prepare(source, logical_time, providers)

    adapter.semantic_prepare = recording_prepare
    outcome = asyncio.run(adapter.execute_fixture_case(case.execution_input(), case.providers))
    assert isinstance(outcome, MechanismOutcome)
    assert outcome.status == "PASS"
    assert outcome.canonical_logical_state == case.expected_canonical_logical_state
    assert outcome.publication_history == tuple(case.expected_publication_history)
    assert set(seen[0][0]) == {"key"}
    assert events[-1]["event_type"] == "terminal_success"
    assert all("prompt" not in event for event in events)


def test_adapter_passes_exact_parity_through_fx0_comparator() -> None:
    cases = []
    for index, transition in enumerate(FX0_REQUIRED_TRANSITIONS):
        error_code = None
        status = "PASS"
        if transition == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED":
            error_code = "CONFLICTING_DUPLICATE_UUID"
            status = "FAIL_CLOSED"
        elif transition == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION":
            # The three registered failure modes are separate fixture rows.
            for mode in FX0_REQUIRED_FAILURE_MODES:
                cases.append(
                    Fx0FixtureCase(
                        case_id=f"case-{index}-{mode.lower()}",
                        transition=transition,
                        source_sequence=0,
                        source={"key": f"case-{index}-{mode.lower()}", "error_code": mode},
                        providers=_providers(),
                        expected_status="FAIL_CLOSED",
                        expected_error_code=mode,
                        expected_canonical_logical_state={"nodes": [], "relationships": []},
                        expected_publication_history=(),
                    )
                )
            continue
        key = f"case-{index}"
        cases.append(
            Fx0FixtureCase(
                case_id=key,
                transition=transition,
                source_sequence=0,
                source={"key": key, **({"error_code": error_code} if error_code else {})},
                providers=_providers(),
                expected_status=status,
                expected_error_code=error_code,
                expected_canonical_logical_state=(
                    {"nodes": [{"key": key}], "relationships": []}
                    if status == "PASS"
                    else {"nodes": [], "relationships": []}
                ),
                expected_publication_history=(
                    ({"source_sequence": 0, "event": "publish"},)
                    if status == "PASS"
                    else ()
                ),
            )
        )
    case = cases[0]
    state = {"nodes": [], "relationships": []}
    history: list[dict] = []

    async def prepare(source, _logical_time, _providers):
        if source.get("error_code"):
            state["nodes"] = []
            state["relationships"] = []
            history.clear()
        return dict(source)

    async def bind(prepared, _logical_time, source_sequence, _prefix, _providers):
        error_code = prepared.get("error_code")
        if error_code:
            raise S5MStarProductionAdapterError(error_code)
        state["nodes"] = [{"key": prepared["key"]}]
        history.clear()
        history.append({"source_sequence": source_sequence, "event": "publish"})
        return {
            "canonical_logical_state": deepcopy(state),
            "publication_history": deepcopy(history),
        }

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: (deepcopy(state), deepcopy(history)),
    )
    spec = Fx0FixtureSpec(
        run_id="fx0-mstar-adapter-001",
        parent_protocol_sha256="b" * 64,
        amendment_sha256="c" * 64,
        current_stage_pointer_sha256="d" * 64,
        production_path_identity=IDENTITY,
        cases=tuple(cases),
    )
    result = run_fx0_fixture(spec, adapter)
    assert result["framework_verdict"] == "HARNESS_SELF_TEST_PASS"
    assert all(row["exact_canonical_state_parity"] for row in result["case_results"])


def test_adapter_fail_closed_preserves_registered_transition_error() -> None:
    case = _case(status="FAIL_CLOSED", error_code="CONFLICTING_DUPLICATE_UUID")
    outcome = asyncio.run(
        _adapter(fail="CONFLICTING_DUPLICATE_UUID").execute_fixture_case(
            case.execution_input(), case.providers
        )
    )
    assert outcome.status == "FAIL_CLOSED"
    assert outcome.error_code == "CONFLICTING_DUPLICATE_UUID"
    assert outcome.canonical_logical_state == {"nodes": [], "relationships": []}


def test_adapter_rejects_private_observed_state_and_invalid_identity() -> None:
    with pytest.raises(S5MStarProductionAdapterError, match="IDENTITY"):
        S5MStarProductionAdapter(
            production_core_identity=CORE_IDENTITY,
            production_core_identity_sha256="0" * 64,
            semantic_prepare=lambda *_args: None,
            latest_state_bind=lambda *_args: None,
            snapshot=lambda: ({}, []),
        )

    state = {"nodes": [], "relationships": []}

    async def prepare(_source, _logical_time, _providers):
        return None

    async def bind(_prepared, _logical_time, _source, _prefix, _providers):
        return {"canonical_logical_state": {"prompt": "secret"}}

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: (state, []),
    )
    with pytest.raises(S5MStarProductionAdapterError, match="PRIVATE"):
        asyncio.run(adapter.execute_fixture_case(_case().execution_input(), _providers()))


def test_bind_return_cannot_override_independent_snapshot() -> None:
    async def prepare(_source, _logical_time, _providers):
        return None

    async def bind(_prepared, _logical_time, _source, _prefix, _providers):
        return {
            "canonical_logical_state": {"nodes": [{"key": "forged"}]},
            "publication_history": [{"source_sequence": 0, "event": "forged"}],
        }

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: ({"nodes": [], "relationships": []}, []),
    )
    outcome = asyncio.run(
        adapter.execute_fixture_case(_case().execution_input(), _providers())
    )
    assert outcome.canonical_logical_state == {"nodes": [], "relationships": []}
    assert outcome.publication_history == ()


def test_adapter_executes_multi_source_case_with_controlled_logical_times() -> None:
    state = {"nodes": [], "relationships": []}
    history: list[dict] = []
    entered: list[int] = []
    release = asyncio.Event()

    def decode(case, _providers):
        assert set(case.source) == {"operations"}
        return tuple(
            Fx0DecodedSource(
                source_sha256=f"{index + 40:064x}",
                opaque_source={"source": index},
                logical_time_ns=20_000 + index,
            )
            for index in range(2)
        )

    async def prepare(source, logical_time, _providers):
        entered.append(source["source"])
        if len(entered) == 2:
            release.set()
        await asyncio.wait_for(release.wait(), timeout=1)
        return {"source": source["source"], "logical_time": logical_time}

    async def bind(prepared, logical_time, source_sequence, prefix, _providers):
        assert prepared == {"source": source_sequence, "logical_time": logical_time}
        assert prefix == tuple(range(source_sequence))
        state["nodes"].append({"source": source_sequence, "at": logical_time})
        history.append({"source_sequence": source_sequence, "event": "publish"})

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: (deepcopy(state), deepcopy(history)),
        source_decoder=decode,
    )
    case = Fx0ExecutionCase(
        case_id="multi-source",
        source_sequence=0,
        source={"operations": [{"source": 0}, {"source": 1}]},
    )
    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(case, _providers())
    )
    assert execution.outcome.publication_history == (
        {"source_sequence": 0, "event": "publish"},
        {"source_sequence": 1, "event": "publish"},
    )
    assert execution.pipeline_evidence["summary"]["published_source_sequences"] == [
        0,
        1,
    ]
    assert execution.pipeline_evidence["summary"]["prepare_overlap_observed"] is True
    assert [
        row["logical_time_ns"]
        for row in execution.pipeline_evidence["events"]
        if row["event_type"] == "intent"
    ] == [20_000, 20_001]


def test_adapter_records_commit_completed_publication_recovery_as_second_attempt() -> None:
    state = {"nodes": [], "relationships": []}
    history: list[dict] = []
    sink_events: list[dict] = []
    failed_once = False
    recoveries: list[int] = []

    async def prepare(source, _logical_time, _providers):
        return dict(source)

    async def bind(prepared, _logical_time, source_sequence, _prefix, _providers):
        state["nodes"] = [{"key": prepared["key"]}]
        history.append({"source_sequence": source_sequence, "event": "publish"})

    async def sink(event):
        nonlocal failed_once
        if event["event_type"] == "publication" and not failed_once:
            failed_once = True
            raise OSError("journal gap")
        sink_events.append(dict(event))

    async def recover(source, _logical_time):
        recoveries.append(source.source_sequence)

    async def reset(_providers):
        return None

    adapter = S5MStarProductionAdapter(
        production_core_identity=CORE_IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: (deepcopy(state), deepcopy(history)),
        persist_event=sink,
        reset_case=reset,
        recover_publication=recover,
    )
    execution = asyncio.run(
        adapter.execute_fixture_case_with_evidence(
            _case().execution_input(), _providers()
        )
    )
    assert execution.outcome.status == "PASS"
    assert execution.attempt_count == 2
    assert execution.execution_shape["retry_replay_observed"] is True
    assert execution.execution_shape["publication_fault_detection_observed"] is True
    assert recoveries == [0]
    assert [event["event_type"] for event in sink_events][-2:] == [
        "publication",
        "terminal_success",
    ]
