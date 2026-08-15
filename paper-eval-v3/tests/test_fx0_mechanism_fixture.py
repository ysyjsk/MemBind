"""RED/GREEN contract tests for the offline FX0 mechanism fixture lane.

These tests intentionally use a tiny production-path double only to exercise
the harness contract; the harness never supplies a simplified MemBind method.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.fx0_mechanism_fixture import (
    CONTROLLED_PROVIDER_NAMES,
    PRODUCTION_CONTROLLED_PROVIDER_NAMES,
    FX0_REQUIRED_FAILURE_MODES,
    FX0_REQUIRED_TRANSITIONS,
    ControlledNondeterminism,
    Fx0ExecutionCase,
    Fx0FixtureCase,
    Fx0FixtureSpec,
    MechanismOutcome,
    Fx0FixtureError,
    build_fx0_artifact,
    run_fx0_fixture,
    verify_fx0_artifact,
)


PARENT_SHA = "4b81c89b33d407f04fc20862a81eab6badba16d0d61d98331cbe188d1bb4f41e"
AMENDMENT_SHA = "b" * 64
POINTER_SHA = "c" * 64


def test_controlled_provider_hash_covers_transaction_and_publication_schedules():
    base = ControlledNondeterminism(
        transaction_io_schedule={"fail_after_callback_attempts": []},
        publication_sink_schedule={"actions_by_source": ["APPEND"]},
    )
    retry = replace(
        base,
        transaction_io_schedule={"fail_after_callback_attempts": [1]},
    )
    dropped = replace(
        base,
        publication_sink_schedule={"actions_by_source": ["DROP"]},
    )

    projection = base.production_hash_projection()
    assert set(projection) == {
        "llm_responses_sha256",
        "embeddings_sha256",
        "logical_times_sha256",
        "initial_graph_state_sha256",
        "candidate_sets_sha256",
        "transaction_io_schedule_sha256",
        "publication_sink_schedule_sha256",
    }
    assert payload_sha256(projection) != payload_sha256(
        retry.production_hash_projection()
    )
    assert payload_sha256(projection) != payload_sha256(
        dropped.production_hash_projection()
    )
    assert PRODUCTION_CONTROLLED_PROVIDER_NAMES[-2:] == (
        "TRANSACTION_IO_SCHEDULE",
        "PUBLICATION_SINK_SCHEDULE",
    )
    assert CONTROLLED_PROVIDER_NAMES == (
        "LLM_RESPONSES",
        "EMBEDDINGS",
        "LOGICAL_TIME",
        "INITIAL_GRAPH_STATE",
        "CANDIDATE_SETS",
    )


@dataclass
class ProductionPathDouble:
    """Test-only stand-in exposing the required production adapter boundary."""

    outcomes: dict[str, MechanismOutcome]
    production_path_identity = {
        "status": "PLACEHOLDER_NOT_FROZEN",
        "method": "M_STAR",
        "identity_sha256": None,
    }

    def execute_fixture_case(self, case, providers):
        assert isinstance(case, Fx0ExecutionCase)
        assert not hasattr(case, "expected_status")
        assert not hasattr(case, "expected_canonical_logical_state")
        assert not hasattr(case, "expected_publication_history")
        assert isinstance(providers, ControlledNondeterminism)
        return self.outcomes[case.case_id]


def _spec(cases=None):
    if cases is None:
        built = []
        for i, transition in enumerate(FX0_REQUIRED_TRANSITIONS):
            error_codes = (None,)
            if transition == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED":
                error_codes = ("CONFLICTING_DUPLICATE_UUID",)
            elif transition == "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION":
                error_codes = tuple(FX0_REQUIRED_FAILURE_MODES)
            for suffix, error_code in enumerate(error_codes):
                case_id = f"case-{i}-{suffix}"
                status = "FAIL_CLOSED" if error_code else "PASS"
                history = () if error_code else (
                    {
                        "source_sequence": 0,
                        "event": "publish",
                        "transition": transition,
                    },
                )
                built.append(Fx0FixtureCase(
                    case_id=case_id,
                    transition=transition,
                    source_sequence=0,
                    source={"text": transition},
                    providers=ControlledNondeterminism(
                        llm_responses={"response": transition},
                        embeddings={"embedding": [float(i)]},
                        logical_times=("2026-01-01T00:00:00Z",),
                        initial_state={"nodes": [], "relationships": []},
                        candidate_sets=({"candidate": transition},),
                    ),
                    expected_status=status,
                    expected_error_code=error_code,
                    expected_canonical_logical_state={
                        "nodes": [{"logical_key": case_id, "labels": ["Entity"]}],
                        "relationships": [],
                    },
                    expected_publication_history=history,
                ))
        cases = tuple(built)
    return Fx0FixtureSpec(
        run_id="fx0-offline-20260815-001",
        parent_protocol_sha256=PARENT_SHA,
        amendment_sha256=AMENDMENT_SHA,
        current_stage_pointer_sha256=POINTER_SHA,
        production_path_identity={
            "status": "PLACEHOLDER_NOT_FROZEN",
            "method": "M_STAR",
            "identity_sha256": None,
        },
        cases=tuple(cases),
    )


def _mechanism(spec):
    return ProductionPathDouble(
        {
            case.case_id: MechanismOutcome(
                case_id=case.case_id,
                status=case.expected_status,
                error_code=case.expected_error_code,
                canonical_logical_state=case.expected_canonical_logical_state,
                publication_history=case.expected_publication_history,
            )
            for case in spec.cases
        }
    )


def test_fx0_transition_inventory_requires_all_coverage_and_not_fixed_count():
    spec = _spec()
    result = run_fx0_fixture(spec, _mechanism(spec))
    assert result["framework_verdict"] == "HARNESS_SELF_TEST_PASS"
    assert result["fixture_count"] > len(FX0_REQUIRED_TRANSITIONS)
    assert result["fixture_count_policy"] == "TRANSITION_COVERAGE_NOT_FIXED_COUNT"
    assert set(result["covered_transitions"]) == set(FX0_REQUIRED_TRANSITIONS)


def test_fx0_adapter_receives_execution_input_without_private_oracle():
    spec = _spec()
    seen = []

    class OracleIsolatedAdapter(ProductionPathDouble):
        def execute_fixture_case(self, case, providers):
            seen.append(case)
            return super().execute_fixture_case(case, providers)

    result = run_fx0_fixture(
        spec,
        OracleIsolatedAdapter(_mechanism(spec).outcomes),
    )
    assert result["framework_verdict"] == "HARNESS_SELF_TEST_PASS"
    assert len(seen) == len(spec.cases)
    assert all(isinstance(case, Fx0ExecutionCase) for case in seen)
    assert all(set(vars(case)) == {"case_id", "source_sequence", "source"} for case in seen)


def test_fx0_requires_production_path_identity_and_rejects_legacy_inheritance():
    spec = _spec()
    assert spec.legacy_authority_inheritance is False

    class MissingIdentity:
        def execute_fixture_case(self, case, providers):
            raise AssertionError("must not execute")

    bad = MissingIdentity()
    with pytest.raises(Fx0FixtureError, match="production path identity"):
        run_fx0_fixture(spec, bad)


def test_fx0_supports_async_production_path_adapter():
    spec = _spec()

    class AsyncAdapter(ProductionPathDouble):
        async def execute_fixture_case(self, case, providers):
            return super().execute_fixture_case(case, providers)

    result = run_fx0_fixture(spec, AsyncAdapter(_mechanism(spec).outcomes))
    assert result["framework_verdict"] == "HARNESS_SELF_TEST_PASS"


def test_fx0_exactly_compares_canonical_state_and_publication_history():
    spec = _spec()
    mechanism = _mechanism(spec)
    outcome = mechanism.outcomes[spec.cases[0].case_id]
    mechanism.outcomes[spec.cases[0].case_id] = MechanismOutcome(
        case_id=outcome.case_id,
        status="PASS",
        error_code=None,
        canonical_logical_state={"nodes": [], "relationships": []},
        publication_history=outcome.publication_history,
    )
    with pytest.raises(Fx0FixtureError, match="canonical logical state parity"):
        run_fx0_fixture(spec, mechanism)


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("FAIL_CLOSED", "CONFLICTING_DUPLICATE_UUID"),
        ("FAIL_CLOSED", "LOST_PUBLICATION"),
        ("FAIL_CLOSED", "DUPLICATE_PUBLICATION"),
        ("FAIL_CLOSED", "PARTIAL_PUBLICATION"),
    ],
)
def test_fx0_negative_transition_outcomes_are_explicit(status, error_code):
    transition = {
        "CONFLICTING_DUPLICATE_UUID": "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
        "LOST_PUBLICATION": "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
        "DUPLICATE_PUBLICATION": "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
        "PARTIAL_PUBLICATION": "LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
    }[error_code]
    replacement = Fx0FixtureCase(
        case_id="negative",
        transition=transition,
        source_sequence=0,
        source={"text": transition},
        providers=ControlledNondeterminism(),
        expected_status=status,
        expected_error_code=error_code,
        expected_canonical_logical_state={"nodes": [], "relationships": []},
        expected_publication_history=(),
    )
    base = _spec()
    kept = tuple(
        case
        for case in base.cases
        if not (
            case.transition == transition
            and case.expected_error_code == error_code
        )
    )
    spec = _spec((*kept, replacement))
    artifact = build_fx0_artifact(spec, _mechanism(spec), git_commit="deadbeef")
    assert artifact["payload"]["framework_verdict"] == "HARNESS_SELF_TEST_PASS"
    assert artifact["payload"]["framework_evidence_scope"] == (
        "HARNESS_SELF_TEST_WITH_TEST_DOUBLE_ONLY"
    )
    assert artifact["payload"]["m_star_mechanism_correctness_claim_authorized"] is False
    assert artifact["payload"]["m_star_exact_parity_qualification"] == "NOT_EXECUTED"
    assert artifact["payload"]["authority"]["s5_live_execution_authorized"] is False
    verify_fx0_artifact(artifact)


def test_fx0_detects_lost_duplicate_and_partial_publication():
    spec = _spec()
    mechanism = _mechanism(spec)
    case = spec.cases[0]
    good = mechanism.outcomes[case.case_id]
    mechanism.outcomes[case.case_id] = MechanismOutcome(
        case_id=case.case_id,
        status="PASS",
        error_code=None,
        canonical_logical_state=good.canonical_logical_state,
        publication_history=(
            {"source_sequence": 0, "event": "publish", "transition": case.transition},
            {"source_sequence": 0, "event": "publish", "transition": case.transition},
        ),
    )
    with pytest.raises(Fx0FixtureError, match="publication history parity"):
        run_fx0_fixture(spec, mechanism)


def test_fx0_artifact_bindings_and_authority_are_sealed():
    spec = _spec()
    artifact = build_fx0_artifact(spec, _mechanism(spec), git_commit="deadbeef")
    payload = verify_fx0_artifact(artifact)["payload"]
    assert payload["input_bindings"] == {
        "parent_protocol_sha256": PARENT_SHA,
        "amendment_sha256": AMENDMENT_SHA,
        "current_stage_pointer_sha256": POINTER_SHA,
    }
    assert payload["legacy_authority_inheritance"] is False
    assert payload["performance_claims_authorized"] is False
    assert payload["m_star_exact_parity_qualification"] == "NOT_EXECUTED"
    assert payload["authority"] == {
        "fx0_offline_design_authorized": True,
        "fx0_live_execution_authorized": False,
        "s5_offline_design_authorized": True,
        "s5_live_execution_authorized": False,
        "pilot_execution_authorized": False,
        "formal_execution_authorized": False,
        "model_call_authorized": False,
        "neo4j_read_authorized": False,
        "neo4j_mutation_authorized": False,
        "current_stage_pointer_update_authorized": False,
    }


def test_fx0_verifier_recomputes_case_coverage_and_rejects_tampering():
    spec = _spec()
    artifact = build_fx0_artifact(spec, _mechanism(spec), git_commit="deadbeef")
    tampered = copy.deepcopy(artifact)
    tampered["payload"]["case_results"][0]["transition"] = "RELATION_RESOLUTION"
    tampered["payload"]["case_results_sha256"] = payload_sha256(
        tampered["payload"]["case_results"]
    )
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(Fx0FixtureError, match="coverage"):
        verify_fx0_artifact(tampered)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: rows[1].update(case_id=rows[0]["case_id"]),
        lambda rows: rows[0].update(source_sequence=-1),
        lambda rows: rows[0].update(expected_error_code="LOST_PUBLICATION"),
        lambda rows: next(
            row
            for row in rows
            if row["transition"] == "CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED"
        ).update(expected_status="PASS"),
        lambda rows: next(
            row for row in rows if row["expected_status"] == "FAIL_CLOSED"
        ).update(expected_error_code="UNREGISTERED_FAILURE"),
    ],
)
def test_fx0_verifier_rejects_contradictory_or_malformed_case_rows(mutate):
    artifact = build_fx0_artifact(_spec(), _mechanism(_spec()), git_commit="deadbeef")
    tampered = copy.deepcopy(artifact)
    rows = tampered["payload"]["case_results"]
    mutate(rows)
    tampered["payload"]["case_results_sha256"] = payload_sha256(rows)
    tampered["payload_sha256"] = payload_sha256(tampered["payload"])
    with pytest.raises(Fx0FixtureError):
        verify_fx0_artifact(tampered)
