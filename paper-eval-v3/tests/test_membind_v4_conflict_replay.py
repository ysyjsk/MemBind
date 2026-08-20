"""Strictly offline replay tests for conflict-aware v4 admission."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.conflict_replay import (
    ConflictReplayError,
    OfflineGateEvidence,
    ReplayFactProvenance,
    ReplayObservation,
    build_conflict_offline_replay,
    derive_prepared_opportunity,
    evaluate_offline_gate,
    reduce_replay_observations,
)


PROJECT = Path(__file__).resolve().parents[1]
SEALED_ROOT = PROJECT / "artifacts/paper_eval"
A1_AUDIT = (
    SEALED_ROOT
    / "membind_v4/protocol_amendment_a1/V4_OPPORTUNITY_AUDIT_A1.json"
)
BLOCK_ROOT = (
    SEALED_ROOT
    / "membind_v31/feasibility/membind-v31-feasibility-20260819-004/block-00"
)
BLOCK_MANIFEST = BLOCK_ROOT / "manifest.json"
EVENTS = BLOCK_ROOT / "events.jsonl"
PREPARED_ROOT = BLOCK_ROOT / "private/prepared"
BASELINE_BINDING = SEALED_ROOT / "membind_v4/BASELINE_BINDING.json"
OLD_C01 = (
    SEALED_ROOT
    / "membind_v4/autoresearch/membind-v4-ar-20260819-c01-6-live/candidates/c01"
)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _provenance() -> ReplayFactProvenance:
    return ReplayFactProvenance(
        conflict_sha256=SHA_A,
        semantic_sha256=SHA_B,
        resource_sha256=SHA_C,
        value_sha256=SHA_D,
        exact_outcome_sha256=SHA_A,
    )


def _gate_evidence() -> OfflineGateEvidence:
    return OfflineGateEvidence(
        exact_validation_enforced=True,
        exact_validation_provenance_sha256=SHA_B,
        resource_policy_nonstarving=True,
        resource_policy_provenance_sha256=SHA_C,
    )


def _build(**overrides: object) -> dict[str, object]:
    arguments: dict[str, object] = {
        "audit_path": A1_AUDIT,
        "block_manifest_path": BLOCK_MANIFEST,
        "events_path": EVENTS,
        "prepared_dir": PREPARED_ROOT,
        "baseline_binding_path": BASELINE_BINDING,
        "old_c01_candidate_dir": OLD_C01,
        "source_count": 12,
    }
    arguments.update(overrides)
    return build_conflict_offline_replay(**arguments)


def test_prepared_opportunity_is_a_strict_temporal_relation() -> None:
    assert derive_prepared_opportunity(99, 100) == (1, True)
    assert derive_prepared_opportunity(100, 100) == (0, False)
    assert derive_prepared_opportunity(101, 100) == (-1, False)
    assert derive_prepared_opportunity(99, None) == (None, False)


def test_reducer_reports_conflict_and_resource_funnel() -> None:
    replay = reduce_replay_observations(
        [
            ReplayObservation(
                1,
                True,
                "LOW_CONFLICT",
                "LLM",
                True,
                True,
                "HIT",
                _provenance(),
            ),
            ReplayObservation(
                2,
                True,
                "LOW_CONFLICT",
                "NO_LLM",
                None,
                None,
                "HIT",
                ReplayFactProvenance(
                    conflict_sha256=SHA_A,
                    semantic_sha256=SHA_B,
                    exact_outcome_sha256=SHA_C,
                ),
            ),
            ReplayObservation(
                3,
                True,
                "HIGH_CONFLICT",
                "LLM",
                None,
                None,
                "MISS",
                ReplayFactProvenance(
                    conflict_sha256=SHA_A,
                    semantic_sha256=SHA_B,
                    exact_outcome_sha256=SHA_C,
                ),
            ),
            ReplayObservation(
                4,
                True,
                "UNKNOWN",
                "LLM",
                None,
                None,
                None,
                ReplayFactProvenance(
                    conflict_sha256=SHA_A,
                    semantic_sha256=SHA_B,
                ),
            ),
            ReplayObservation(5, False, None, None, None, None, None, None),
        ]
    )

    assert replay["counts"] == {
        "total_future_prepared_opportunities": 4,
        "low_conflict_count": 2,
        "high_conflict_count": 1,
        "unknown_conflict_count": 1,
        "llm_required_count": 3,
        "resource_admissible_count": 1,
        "would_launch_count": 1,
    }
    assert replay["opportunity_funnel"] == {
        "before_conflict_filter": 4,
        "after_conflict_filter": 2,
        "after_llm_required_filter": 1,
        "after_resource_admission": 1,
        "after_value_admission": 1,
    }
    assert replay["exact_outcomes"]["LOW_CONFLICT"] == {
        "observed_count": 2,
        "hit_count": 2,
        "miss_count": 0,
        "hit_rate": 1.0,
    }
    assert replay["exact_outcomes"]["HIGH_CONFLICT"]["hit_rate"] == 0.0
    assert replay["exact_outcomes"]["UNKNOWN"]["hit_rate"] is None


def test_reducer_fails_closed_when_value_gate_evidence_is_absent() -> None:
    replay = reduce_replay_observations(
        [
            ReplayObservation(
                1,
                True,
                "LOW_CONFLICT",
                "LLM",
                True,
                None,
                "HIT",
                ReplayFactProvenance(
                    conflict_sha256=SHA_A,
                    semantic_sha256=SHA_B,
                    resource_sha256=SHA_C,
                    exact_outcome_sha256=SHA_D,
                ),
            )
        ]
    )

    assert replay["counts"]["resource_admissible_count"] == 1
    assert replay["counts"]["would_launch_count"] == 0
    assert replay["opportunity_funnel"]["after_resource_admission"] == 1
    assert replay["opportunity_funnel"]["after_value_admission"] == 0


@pytest.mark.parametrize(
    "observation,error",
    [
        (
            ReplayObservation(1, False, "LOW_CONFLICT", None, None, None, None, None),
            "observation_conflict_without_opportunity",
        ),
        (
            ReplayObservation(
                1, True, "LOW_CONFLICT", "LLM", True, True, "HIT", None
            ),
            "observation_provenance_missing",
        ),
        (
            ReplayObservation(
                1,
                True,
                "HIGH_CONFLICT",
                "LLM",
                True,
                None,
                "MISS",
                _provenance(),
            ),
            "observation_resource_without_low_llm",
        ),
        (
            ReplayObservation(
                1,
                True,
                "LOW_CONFLICT",
                "LLM",
                False,
                True,
                "HIT",
                _provenance(),
            ),
            "observation_value_without_resource",
        ),
    ],
)
def test_replay_observation_enforces_stage_consistency(
    observation: ReplayObservation, error: str
) -> None:
    with pytest.raises(ConflictReplayError, match=error):
        observation.verify()


def test_registered_0_11_replay_is_sealed_and_has_no_opportunity() -> None:
    def forbidden_classifier(
        _frontier: dict[str, object], _candidate: dict[str, object]
    ) -> str:
        raise AssertionError("classifier must not run without a timing opportunity")

    replay = _build(classifier=forbidden_classifier)

    assert replay["schema_version"] == "membind.paper-eval-v4.conflict-offline-replay.v1"
    assert replay["status"] == "PASS_OFFLINE_REPLAY"
    assert replay["history_id"] == "07741c45"
    assert replay["source_sequences"] == list(range(12))
    assert replay["counts"] == {
        "total_future_prepared_opportunities": 0,
        "low_conflict_count": 0,
        "high_conflict_count": 0,
        "unknown_conflict_count": 0,
        "llm_required_count": 0,
        "resource_admissible_count": 0,
        "would_launch_count": 0,
    }
    assert replay["opportunity_funnel"] == {
        "before_conflict_filter": 0,
        "after_conflict_filter": 0,
        "after_llm_required_filter": 0,
        "after_resource_admission": 0,
        "after_value_admission": 0,
    }
    assert replay["gate"] == {
        "decision": "STOP_CONFLICT_AWARE_NODE_RESOLVE",
        "final_outcome": "STOP_V4_NODE_RESOLVE",
        "live_authorized": False,
        "reason": "low_conflict_opportunities_zero",
    }
    assert replay["input_identity"]["audit_file_sha256"] == (
        "f7f1355f5be72eec8cd1b161c62acdfe9d92a951bbdd8831923bc25d1f73d1a0"
    )
    assert replay["input_identity"]["block_manifest_file_sha256"] == (
        "9794ab843b2643ee52171cfad2f24a7cb154b3c94832270d7b211d23caec95a2"
    )
    assert replay["input_identity"]["events_file_sha256"] == (
        "7b9383010a3d595faaf00548b807e4ae85b85f19d1bfc4415775595a4031bdec"
    )
    assert replay["event_evidence"]["validated_event_count"] == 49 * 6
    assert replay["event_evidence"]["audit_rows_match_derived_events"] is True
    assert len(replay["prepared_artifacts"]) == 12
    assert replay["prepared_artifacts"][0]["artifact_sha256"] == (
        "a9aecb4dd39c2d7a6e63e5e73764c92b0932a70215bbd3b30c3ad700988511be"
    )
    assert replay["payload_sha256"] == payload_sha256(
        {key: value for key, value in replay.items() if key != "payload_sha256"}
    )
    assert replay["optional_fact_evidence"] == {
        "status": "not_reached",
        "reason": "no_future_prepared_opportunities",
        "facts_used": False,
    }
    assert replay["provenance_completeness"] == {
        "conflict_complete": None,
        "semantic_complete": None,
        "resource_complete": None,
        "value_complete": None,
        "selectivity_complete": None,
    }


def test_replay_inventories_frozen_baseline_and_old_blind_c01() -> None:
    replay = _build()

    baseline = replay["read_only_evidence_inventory"]["frozen_baselines"]
    assert baseline["binding_file_sha256"] == (
        "488fb6952bebdd2d90cf2074ad72d1c5e1e617bb99d4e1623ff9aff0134fb015"
    )
    assert [item["method"] for item in baseline["traces"]] == [
        "U0-aligned",
        "A0-aligned",
        "P(C=2)-aligned",
    ]
    assert baseline["usable_facts"] == {
        "history_id": "07741c45",
        "methods": ["U0-aligned", "A0-aligned", "P(C=2)-aligned"],
        "all_status_pass": True,
        "formal_comparison_eligible": False,
        "reason": "mixed_execution_envelopes",
    }
    old = replay["read_only_evidence_inventory"]["old_blind_c01_6_source"]
    assert old["manifest_sha256"] == (
        "2093f1328ddf858a6329d24954baa0e0255cada3879d94a2c4aeef106889f802"
    )
    assert old["usable_facts"] == {
        "source_count": 6,
        "qualified_node_resolve_count": 0,
        "speculation_launch_count": 0,
        "semantic_hit_count": 0,
        "semantic_miss_count": 0,
        "decision": "STOP_V4_NODE_RESOLVE",
        "reason": "no_qualified_node_resolve",
        "conflict_selectivity_available": False,
    }
    assert old["use_in_current_replay"] == "not_reached_no_timing_opportunity"


def test_replay_rejects_event_trace_hash_drift(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_bytes(EVENTS.read_bytes() + b"\n")

    with pytest.raises(ConflictReplayError, match="events_file_hash_mismatch"):
        _build(events_path=events)


def test_replay_rejects_resealed_event_row_drift(tmp_path: Path) -> None:
    rows = EVENTS.read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["event"]["timestamp_ns"] += 1
    rows[0] = json.dumps(first, sort_keys=True)
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(rows) + "\n", encoding="utf-8")
    audit = json.loads(A1_AUDIT.read_text(encoding="utf-8"))
    from paper_eval.artifacts import sha256_file

    audit["input"]["events_file_sha256"] = sha256_file(events)
    audit.pop("payload_sha256")
    audit["payload_sha256"] = payload_sha256(audit)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ConflictReplayError, match="event_hash_mismatch:1"):
        _build(audit_path=audit_path, events_path=events)


def _write_consistently_resealed_timing_fixture(
    tmp_path: Path,
    rows: list[str],
    *,
    source_row_updates: dict[str, int],
) -> tuple[Path, Path]:
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(rows) + "\n", encoding="utf-8")
    audit = json.loads(A1_AUDIT.read_text(encoding="utf-8"))
    from paper_eval.artifacts import sha256_file

    audit["input"]["events_file_sha256"] = sha256_file(events)
    audit["source_rows"][0].update(source_row_updates)
    audit.pop("payload_sha256")
    audit["payload_sha256"] = payload_sha256(audit)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    return audit_path, events


def test_replay_rejects_consistently_resealed_global_timestamp_regression(
    tmp_path: Path,
) -> None:
    rows = EVENTS.read_text(encoding="utf-8").splitlines()
    arrival = json.loads(rows[0])["event"]["timestamp_ns"]
    compile_row = json.loads(rows[1])
    compile_row["event"]["timestamp_ns"] = arrival - 1
    compile_row["event_sha256"] = payload_sha256(compile_row["event"])
    rows[1] = json.dumps(compile_row, sort_keys=True)
    audit_path, events = _write_consistently_resealed_timing_fixture(
        tmp_path,
        rows,
        source_row_updates={"compile_started_timestamp_ns": arrival - 1},
    )

    with pytest.raises(ConflictReplayError, match="event_timestamp_order_invalid:2"):
        _build(audit_path=audit_path, events_path=events)


def test_replay_rejects_consistently_resealed_lifecycle_reordering(
    tmp_path: Path,
) -> None:
    rows = EVENTS.read_text(encoding="utf-8").splitlines()
    compile_row = json.loads(rows[1])
    bind_row = json.loads(rows[3])
    compile_timestamp = compile_row["event"]["timestamp_ns"]
    bind_timestamp = bind_row["event"]["timestamp_ns"]
    compile_row["event"]["event_type"] = "BIND_STARTED"
    bind_row["event"]["event_type"] = "COMPILE_STARTED"
    compile_row["event_sha256"] = payload_sha256(compile_row["event"])
    bind_row["event_sha256"] = payload_sha256(bind_row["event"])
    rows[1] = json.dumps(compile_row, sort_keys=True)
    rows[3] = json.dumps(bind_row, sort_keys=True)
    audit_path, events = _write_consistently_resealed_timing_fixture(
        tmp_path,
        rows,
        source_row_updates={
            "compile_started_timestamp_ns": bind_timestamp,
            "bind_started_timestamp_ns": compile_timestamp,
        },
    )

    with pytest.raises(ConflictReplayError, match="event_lifecycle_order_invalid:0"):
        _build(audit_path=audit_path, events_path=events)


def test_replay_rejects_resealed_audit_derivation_drift(tmp_path: Path) -> None:
    audit = json.loads(A1_AUDIT.read_text(encoding="utf-8"))
    audit["derivation"]["potential_opportunity_formula"] = "prepared_lead_ns >= 0"
    audit.pop("payload_sha256")
    audit["payload_sha256"] = payload_sha256(audit)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(ConflictReplayError, match="audit_derivation_invalid"):
        _build(audit_path=audit_path)


def test_replay_rejects_prepared_artifact_identity_drift(tmp_path: Path) -> None:
    prepared_dir = tmp_path / "prepared"
    prepared_dir.mkdir()
    for source in range(12):
        document = json.loads(
            (PREPARED_ROOT / f"{source:08d}.json").read_text(encoding="utf-8")
        )
        if source == 4:
            document["source_sequence"] = 5
        (prepared_dir / f"{source:08d}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )

    with pytest.raises(ConflictReplayError, match="prepared_source_sequence_invalid:4"):
        _build(prepared_dir=prepared_dir)


def _selective_observations(
    *,
    resource: bool = True,
    value: bool | None = True,
    low_outcome: str | None = "HIT",
    high_outcome: str | None = "MISS",
    provenance: bool = True,
) -> list[ReplayObservation]:
    low_provenance = (
        ReplayFactProvenance(
            conflict_sha256=SHA_A,
            semantic_sha256=SHA_B,
            resource_sha256=SHA_C,
            value_sha256=SHA_D if value is not None else None,
            exact_outcome_sha256=SHA_A if low_outcome is not None else None,
        )
        if provenance
        else None
    )
    high_provenance = (
        ReplayFactProvenance(
            conflict_sha256=SHA_A,
            semantic_sha256=SHA_B,
            exact_outcome_sha256=SHA_C if high_outcome is not None else None,
        )
        if provenance
        else None
    )
    return [
        ReplayObservation(
            1,
            True,
            "LOW_CONFLICT",
            "LLM",
            resource,
            value if resource else None,
            low_outcome,
            low_provenance,
        ),
        ReplayObservation(
            2,
            True,
            "HIGH_CONFLICT",
            "LLM",
            None,
            None,
            high_outcome,
            high_provenance,
        ),
    ]


def test_p8_gate_stops_when_low_conflict_is_zero() -> None:
    reduced = reduce_replay_observations(
        [
            ReplayObservation(
                1,
                True,
                "HIGH_CONFLICT",
                "LLM",
                None,
                None,
                "MISS",
                ReplayFactProvenance(
                    conflict_sha256=SHA_A,
                    semantic_sha256=SHA_B,
                    exact_outcome_sha256=SHA_C,
                ),
            )
        ]
    )

    assert evaluate_offline_gate(reduced, _gate_evidence())["decision"] == (
        "STOP_CONFLICT_AWARE_NODE_RESOLVE"
    )


@pytest.mark.parametrize(
    "low_outcome,high_outcome",
    [("MISS", "HIT"), ("HIT", "HIT"), (None, "MISS")],
)
def test_p8_gate_stops_predictor_without_positive_selectivity(
    low_outcome: str | None, high_outcome: str | None
) -> None:
    reduced = reduce_replay_observations(
        _selective_observations(
            low_outcome=low_outcome,
            high_outcome=high_outcome,
        )
    )

    gate = evaluate_offline_gate(reduced, _gate_evidence())
    assert gate["decision"] == "STOP_CONFLICT_PREDICTOR"
    assert gate["live_authorized"] is False


def test_p8_gate_reports_resource_limit_after_selectivity_passes() -> None:
    reduced = reduce_replay_observations(
        _selective_observations(resource=False, value=None)
    )

    gate = evaluate_offline_gate(reduced, _gate_evidence())
    assert gate["decision"] == "RESOURCE_GATE_BUG_OR_POLICY_LIMIT"
    assert gate["live_authorized"] is False


def test_p8_gate_allows_single_live_candidate_only_when_every_gate_passes() -> None:
    reduced = reduce_replay_observations(_selective_observations())

    gate = evaluate_offline_gate(reduced, _gate_evidence())
    assert gate == {
        "decision": "GO_C01_CA_LIVE",
        "final_outcome": None,
        "live_authorized": True,
        "reason": "all_offline_gates_passed",
    }


def test_p8_gate_cannot_go_without_provenance_value_or_safety_evidence() -> None:
    no_value = reduce_replay_observations(_selective_observations(value=None))
    no_safety = OfflineGateEvidence(
        exact_validation_enforced=None,
        exact_validation_provenance_sha256=None,
        resource_policy_nonstarving=True,
        resource_policy_provenance_sha256=SHA_C,
    )

    assert evaluate_offline_gate(no_value, _gate_evidence())["live_authorized"] is False
    assert evaluate_offline_gate(
        reduce_replay_observations(_selective_observations()), no_safety
    )["live_authorized"] is False
    with pytest.raises(ConflictReplayError, match="observation_provenance_missing"):
        reduce_replay_observations(_selective_observations(provenance=False))
