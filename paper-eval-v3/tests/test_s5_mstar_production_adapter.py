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
    S5MStarProductionAdapter,
    S5MStarProductionAdapterError,
)


IDENTITY = {
    "status": "FROZEN",
    "method": "M_STAR",
    "identity_sha256": "a" * 64,
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
        production_path_identity=IDENTITY,
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
        production_path_identity=IDENTITY,
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
            production_path_identity={
                **IDENTITY,
                "identity_sha256": "0" * 64,
            },
            production_core_identity_sha256=IDENTITY["identity_sha256"],
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
        production_path_identity=IDENTITY,
        production_core_identity_sha256=IDENTITY["identity_sha256"],
        semantic_prepare=prepare,
        latest_state_bind=bind,
        snapshot=lambda: (state, []),
    )
    with pytest.raises(S5MStarProductionAdapterError, match="PRIVATE"):
        asyncio.run(adapter.execute_fixture_case(_case().execution_input(), _providers()))
