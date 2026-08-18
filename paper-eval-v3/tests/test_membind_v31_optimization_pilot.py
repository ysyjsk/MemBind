"""TDD for the isolated W=4 MemBind v3.1 optimization pilot contract."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from paper_eval.apc_aligned_baseline import build_apc_aligned_baseline_plan
from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v31.baseline_acceptance import EXPECTED_BASELINE_RUN_ID
from paper_eval.membind_v31.method_plan import build_membind_v31_live_plan
from paper_eval.membind_v31.optimization_pilot import (
    ARTIFACT_STATUS,
    MERGE_AUTHORITY,
    PILOT_SOURCE_COUNT,
    OptimizationPilotError,
    build_w4_pilot_checkpoint,
    build_w4_pilot_contract,
    build_w4_pilot_manifest,
    build_w4_pilot_result,
    derive_w4_pilot_cache_salt,
    derive_w4_pilot_namespace,
    verify_w4_pilot_contract,
)


HISTORIES = ("07741c45", "b6019101", "6071bd76", "a2f3aa27")
RUN_ID = "membind-v31-opt-w4-20260818-001"
ATTEMPT_ID = f"{RUN_ID}-attempt-001"


def _formal_plan() -> dict[str, object]:
    sources = {
        history: [f"{position * 1000 + sequence + 1:064x}" for sequence in range(15)]
        for position, history in enumerate(HISTORIES)
    }
    baseline = build_apc_aligned_baseline_plan(
        run_id=EXPECTED_BASELINE_RUN_ID,
        history_source_sha256s=sources,
        interarrival_ns=10,
        execution_envelope_sha256="a" * 64,
        service_reference_ns=12,
        normalized_offered_load=1.2,
    )
    return build_membind_v31_live_plan(
        run_id="membind-v31-dev-optimization-test",
        verified_baseline_plan=baseline,
        methodology_sha256="b" * 64,
        workplan_sha256="c" * 64,
    )


def _identity(formal: dict[str, object]) -> tuple[str, str]:
    namespace = derive_w4_pilot_namespace(RUN_ID)
    cache_salt = derive_w4_pilot_cache_salt(
        pilot_run_id=RUN_ID,
        namespace=namespace,
        parent_formal_plan_payload_sha256=str(formal["payload_sha256"]),
    )
    return namespace, cache_salt


def _contract(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    formal = _formal_plan()
    namespace, cache_salt = _identity(formal)
    contract = build_w4_pilot_contract(
        verified_formal_plan=formal,
        pilot_run_id=RUN_ID,
        attempt_id=ATTEMPT_ID,
        namespace=namespace,
        cache_salt_sha256=cache_salt,
        output_root=tmp_path / "pilot",
        compile_workers=2,
        lookahead=4,
        bind_workers=1,
        global_llm_admission_k=2,
    )
    return formal, contract


def _reseal(value: dict[str, object]) -> dict[str, object]:
    selected = deepcopy(value)
    selected.pop("payload_sha256", None)
    selected["payload_sha256"] = payload_sha256(selected)
    return selected


def test_w4_contract_is_exact_prefix_and_permanently_non_mergeable(tmp_path: Path) -> None:
    formal, contract = _contract(tmp_path)

    assert verify_w4_pilot_contract(contract, verified_formal_plan=formal) == contract
    assert contract["artifact_status"] == ARTIFACT_STATUS
    assert contract["merge_authority"] == MERGE_AUTHORITY
    assert contract["formal_main_table_eligible"] is False
    assert contract["heldout_data_accessed"] is False
    assert contract["parent_formal_plan_payload_sha256"] == formal["payload_sha256"]
    assert contract["source_count"] == PILOT_SOURCE_COUNT == 12
    assert contract["source_sequences"] == list(range(12))
    assert contract["source_sha256s"] == formal["history_source_sha256s"]["07741c45"][:12]
    assert contract["arrival_offsets_ns"] == formal["arrival_traces"]["07741c45"][
        "arrival_offsets_ns"
    ][:12]
    assert contract["compile_workers"] == 2
    assert contract["lookahead"] == 4
    assert contract["bind_workers"] == 1
    assert contract["global_llm_admission_k"] == 2
    assert contract["namespace"] not in {row["namespace"] for row in formal["blocks"]}
    assert contract["cache_salt_sha256"] not in {
        row["cache_salt_sha256"] for row in formal["blocks"]
    }


@pytest.mark.parametrize("lookahead", [2, 8])
def test_contract_rejects_non_w4_candidate(tmp_path: Path, lookahead: int) -> None:
    formal = _formal_plan()
    namespace, cache_salt = _identity(formal)

    with pytest.raises(OptimizationPilotError, match="pilot knob identity invalid"):
        build_w4_pilot_contract(
            verified_formal_plan=formal,
            pilot_run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            namespace=namespace,
            cache_salt_sha256=cache_salt,
            output_root=tmp_path / "pilot",
            compile_workers=2,
            lookahead=lookahead,
            bind_workers=1,
            global_llm_admission_k=2,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compile_workers", 4),
        ("bind_workers", 2),
        ("global_llm_admission_k", 4),
    ],
)
def test_contract_rejects_other_execution_knob_drift(
    tmp_path: Path, field: str, value: int
) -> None:
    formal = _formal_plan()
    namespace, cache_salt = _identity(formal)
    knobs = {
        "compile_workers": 2,
        "lookahead": 4,
        "bind_workers": 1,
        "global_llm_admission_k": 2,
    }
    knobs[field] = value

    with pytest.raises(OptimizationPilotError, match="pilot knob identity invalid"):
        build_w4_pilot_contract(
            verified_formal_plan=formal,
            pilot_run_id=RUN_ID,
            attempt_id=ATTEMPT_ID,
            namespace=namespace,
            cache_salt_sha256=cache_salt,
            output_root=tmp_path / "pilot",
            **knobs,
        )


def test_verifier_rejects_parent_source_and_arrival_identity_drift(tmp_path: Path) -> None:
    formal, contract = _contract(tmp_path)
    mutations = []
    parent = deepcopy(contract)
    parent["parent_formal_plan_payload_sha256"] = "d" * 64
    mutations.append(parent)
    source = deepcopy(contract)
    source["source_sha256s"][0] = "e" * 64
    mutations.append(source)
    arrival = deepcopy(contract)
    arrival["arrival_offsets_ns"][1] += 1
    mutations.append(arrival)

    for mutation in mutations:
        with pytest.raises(OptimizationPilotError, match="pilot contract identity drift"):
            verify_w4_pilot_contract(_reseal(mutation), verified_formal_plan=formal)


def test_contract_rejects_namespace_or_cache_salt_reuse(tmp_path: Path) -> None:
    formal = _formal_plan()
    namespace, cache_salt = _identity(formal)
    common = {
        "verified_formal_plan": formal,
        "pilot_run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "namespace": namespace,
        "cache_salt_sha256": cache_salt,
        "output_root": tmp_path / "pilot",
        "compile_workers": 2,
        "lookahead": 4,
        "bind_workers": 1,
        "global_llm_admission_k": 2,
    }

    with pytest.raises(OptimizationPilotError, match="pilot namespace reused"):
        build_w4_pilot_contract(**common, reserved_namespaces=(namespace,))
    with pytest.raises(OptimizationPilotError, match="pilot cache salt reused"):
        build_w4_pilot_contract(**common, reserved_cache_salts=(cache_salt,))


def test_contract_rejects_namespace_cache_identity_drift_and_existing_root(
    tmp_path: Path,
) -> None:
    formal = _formal_plan()
    namespace, cache_salt = _identity(formal)
    common = {
        "verified_formal_plan": formal,
        "pilot_run_id": RUN_ID,
        "attempt_id": ATTEMPT_ID,
        "output_root": tmp_path / "pilot",
        "compile_workers": 2,
        "lookahead": 4,
        "bind_workers": 1,
        "global_llm_admission_k": 2,
    }

    with pytest.raises(OptimizationPilotError, match="pilot namespace identity invalid"):
        build_w4_pilot_contract(
            **common,
            namespace=f"{namespace}-drift",
            cache_salt_sha256=cache_salt,
        )
    with pytest.raises(OptimizationPilotError, match="pilot cache salt identity invalid"):
        build_w4_pilot_contract(
            **common,
            namespace=namespace,
            cache_salt_sha256="f" * 64,
        )
    (tmp_path / "pilot").mkdir()
    with pytest.raises(OptimizationPilotError, match="pilot output root not fresh"):
        build_w4_pilot_contract(
            **common,
            namespace=namespace,
            cache_salt_sha256=cache_salt,
        )


def test_manifest_checkpoint_and_result_remain_diagnostic_only(tmp_path: Path) -> None:
    formal, contract = _contract(tmp_path)
    manifest = build_w4_pilot_manifest(
        contract,
        verified_formal_plan=formal,
        execution_identity_sha256="1" * 64,
        state_cut_certification_sha256="2" * 64,
        implementation_sha256="3" * 64,
    )
    initial = build_w4_pilot_checkpoint(manifest, source_states=["NEW"] * 12, event_count=0)
    complete = build_w4_pilot_checkpoint(
        manifest,
        source_states=["PUBLICATION_DURABLE"] * 12,
        event_count=72,
    )
    result = build_w4_pilot_result(
        manifest,
        checkpoint=complete,
        publication_source_sequences=list(range(12)),
        direct_violation_count=0,
        observed_max_inflight=2,
        p95_freshness_ns=1000,
        makespan_ns=2000,
    )

    for artifact in (manifest, initial, complete, result):
        assert artifact["artifact_status"] == ARTIFACT_STATUS
        assert artifact["merge_authority"] == MERGE_AUTHORITY
        assert artifact["formal_main_table_eligible"] is False
    assert initial["terminal_status"] == "PLANNED"
    assert complete["terminal_status"] == "COMPLETED"
    assert complete["completed_source_prefix"] == 11
    assert result["status"] == "PASS"
    assert result["publication_source_sequences"] == list(range(12))


@pytest.mark.parametrize(
    ("publication", "violations", "inflight", "checkpoint_complete"),
    [
        (list(range(11)), 0, 2, True),
        (list(range(12)), 1, 2, True),
        (list(range(12)), 0, 3, True),
        (list(range(12)), 0, 2, False),
    ],
)
def test_result_rejects_incomplete_or_unsafe_candidate(
    tmp_path: Path,
    publication: list[int],
    violations: int,
    inflight: int,
    checkpoint_complete: bool,
) -> None:
    formal, contract = _contract(tmp_path)
    manifest = build_w4_pilot_manifest(
        contract,
        verified_formal_plan=formal,
        execution_identity_sha256="1" * 64,
        state_cut_certification_sha256="2" * 64,
        implementation_sha256="3" * 64,
    )
    states = ["PUBLICATION_DURABLE"] * (12 if checkpoint_complete else 11)
    if not checkpoint_complete:
        states.append("NEW")
    checkpoint = build_w4_pilot_checkpoint(manifest, source_states=states, event_count=72)

    with pytest.raises(OptimizationPilotError, match="pilot result invalid"):
        build_w4_pilot_result(
            manifest,
            checkpoint=checkpoint,
            publication_source_sequences=publication,
            direct_violation_count=violations,
            observed_max_inflight=inflight,
            p95_freshness_ns=1000,
            makespan_ns=2000,
        )
