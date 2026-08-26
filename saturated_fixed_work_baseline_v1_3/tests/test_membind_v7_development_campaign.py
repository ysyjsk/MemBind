from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.development_campaign import (
    DevelopmentCampaignError,
    build_development_failure,
    build_development_selection,
    load_development_protocol,
    materialize_development_artifacts,
    verify_development_source_bindings,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.gates import (
    evaluate_opportunity_gates,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.observer_campaign import (
    verify_observer_manifest,
)


PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL = PROJECT / "v7/BAILIAN_SILICONFLOW_V7_DEVELOPMENT_PROTOCOL.json"
ROOT_METHOD = PROJECT / "v7/METHOD_SELECTION.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity() -> dict[str, object]:
    return {
        "schema_version": "membind.v7.development-campaign-identity.v1",
        "run_id": "v7-development-test",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": "COMPOSITE_ENGINEERING_ONLY",
        "construction": {
            "authority": "alibaba-bailian-openai-compatible-engineering-json-object-v1",
            "model": "qwen3.5-35b-a3b",
        },
        "embedding": {
            "authority": "siliconflow-openai-compatible-v1",
            "model": "Qwen/Qwen3-Embedding-0.6B",
            "dimension": 1024,
        },
        "observer_harness": {
            "status": "PASS",
            "source_sha256": {"development_campaign.py": "a" * 64},
        },
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "treatment_calls": 0,
        "response_replay_calls": 0,
    }


def _decision_input(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "membind.v7.r3-decision-input.v1",
        "real_graphiti_evidence": True,
        "observer_harness_bound": True,
        "independent_block_count": 2,
        "source_count_per_block": 6,
        "selected_operator": "node_cosine",
        "selected_seam": "graphiti.add_episode.pre_process_episode_data",
        "core_assumptions_supported": True,
        "t6b_status": "SUPPORTED_WITH_GUARD",
        "false_stable_count": 0,
        "false_unaffected_count": 0,
        "stable_prediction_count": 2,
        "early_memory_specific": True,
        "csp": 0.4,
        "csp_preregistered_min": 0.1,
        "sca_within_bound": True,
        "meaningful_reconvergence": True,
        "gross_saved_cp_lb_ns": 1_000,
        "certificate_cost_ub_ns": 100,
        "repair_cost_ub_ns": 100,
        "required_online_headroom_ns": 100,
        "m1_sufficient": True,
        "m2_extension_eligible": False,
        "replay_allowed": False,
        "sealed_manifest_sha256": "0" * 64,
    }
    value.update(overrides)
    return value


def _characterization(**decision_overrides: object) -> dict[str, object]:
    return {
        "pair_analyses": [{"source_sequence": 1, "read_rows": []}],
        "certificate_confusion": {"STABLE/SAME": 2},
        "false_unaffected_count": 0,
        "csp": 0.4,
        "semantic_change_amplification": {
            "direct_work_ns": 10,
            "affected_work_ns": 10,
            "sca_work": 1.0,
        },
        "reconvergence": {"mean_rate": 1.0},
        "critical_opportunity": {
            "status": "COMPLETE",
            "gross_saved_cp_lb_ns": 1_000,
        },
        "decision_input": _decision_input(**decision_overrides),
    }


def test_development_protocol_binds_composite_provider_and_exact_2_6_6_workload() -> None:
    frozen = load_development_protocol(PROTOCOL)

    assert frozen["campaign_scope"] == "TEMPORARY_PROVIDER_DEVELOPMENT"
    assert frozen["composite_provider_freeze"]["sha256"] == (
        "428826c09bf0ed33e72cbdb220721e0714124222c740f7af37a0d158538d4742"
    )
    assert frozen["workload"]["local_file_sha256"] == (
        "97fd80207f3419fc57c3684db824334224546d6bdd62c17ef52cd116eec9ffc8"
    )
    assert frozen["workload"]["r1_r2"]["source_count"] == 2
    assert [row["source_count"] for row in frozen["workload"]["r3_blocks"]] == [6, 6]
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["live_treatment_authorized"] is False
    assert frozen["provider_swap_requires_new_formal_campaign"] is True

    construction = frozen["construction"]
    embedding = frozen["embedding"]
    assert construction["authority"] != embedding["authority"]
    assert construction["model"] == "qwen3.5-35b-a3b"
    assert embedding["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert embedding["dimension"] == 1024


def test_source_hash_drift_fails_before_any_external_call(tmp_path: Path) -> None:
    source = tmp_path / "bound.py"
    source.write_text("FROZEN = True\n", encoding="ascii")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    calls = {"provider": 0, "database": 0}

    assert verify_development_source_bindings(tmp_path, {"bound.py": expected}) == {
        "bound.py": expected
    }
    source.write_text("FROZEN = False\n", encoding="ascii")
    with pytest.raises(DevelopmentCampaignError, match="source hash"):
        verify_development_source_bindings(tmp_path, {"bound.py": expected})
    assert calls == {"provider": 0, "database": 0}


def test_development_selection_never_authorizes_formal_or_live_treatment() -> None:
    provisional = evaluate_opportunity_gates(_decision_input())
    assert provisional["selected_method"] == "M1"

    selected = build_development_selection(provisional)

    assert selected["status"] == "DEVELOPMENT_SELECTED"
    assert selected["implementation_authorized"] is True
    assert selected["selected_method"] == "M1"
    assert selected["live_treatment_authorized"] is False
    assert selected["formal_r1_r3_eligible"] is False
    assert selected["provider_swap_requires_new_formal_campaign"] is True
    assert "treatment_authorized" not in selected
    assert "authorized" not in selected


@pytest.mark.parametrize(
    "overrides",
    [
        {"false_stable_count": 1},
        {"false_unaffected_count": 1},
        {"stable_prediction_count": 0},
    ],
)
def test_false_stability_or_no_stable_prediction_seals_development_null(
    overrides: dict[str, object],
) -> None:
    provisional = evaluate_opportunity_gates(_decision_input(**overrides))
    selected = build_development_selection(provisional)

    assert provisional["selected_method"] == "NULL"
    assert selected["status"] == "DEVELOPMENT_NULL"
    assert selected["implementation_authorized"] is False
    assert selected["selected_method"] == "NULL"
    assert selected["live_treatment_authorized"] is False


def test_development_materializer_seals_without_scientific_method_selection(
    tmp_path: Path,
) -> None:
    root_method = tmp_path / "METHOD_SELECTION.json"
    root_method.write_bytes(ROOT_METHOD.read_bytes())
    before = _sha256(root_method)
    raw_blocks = [
        {
            "block_id": block_id,
            "status": "OBSERVER_ONLY",
            "real_graphiti_evidence": True,
            "source_count": 6,
            "namespace": f"private-{block_id}",
            "transitions": [
                {
                    "source_sequence": 0,
                    "delta": {
                        "before": {"name_embedding": [0.1, 0.2]},
                        "after": {"name_embedding": [0.3, 0.4]},
                    },
                }
            ],
            "pairs": [
                {
                    "source_sequence": 1,
                    "old_build": {
                        "messages": ["private prompt"],
                        "response": "private response",
                    },
                    "fresh_build": {"api_key": "private credential"},
                    "semantic_dag": {"status": "COMPLETE", "nodes": []},
                }
            ],
        }
        for block_id in ("R3-A", "R3-B")
    ]
    output = tmp_path / "development"

    result = materialize_development_artifacts(
        output,
        r1={"status": "PASS_WITH_GUARDS", "real_graphiti_evidence": True},
        r2={
            "status": "OBSERVER_ONLY",
            "real_graphiti_evidence": True,
            "delta": {"name_embedding": [0.1, 0.2]},
            "pair_analysis": {"false_stable_count": 0},
        },
        blocks=raw_blocks,
        characterization=_characterization(),
        campaign_identity=_identity(),
        scientific_method_selection_path=root_method,
        expected_scientific_method_selection_sha256=before,
    )

    assert _sha256(root_method) == before
    assert result["scientific_method_selection_sha256_before"] == before
    assert result["scientific_method_selection_sha256_after"] == before
    assert not (output / "METHOD_SELECTION.json").exists()
    assert (output / "PROVISIONAL_GATE_RESULT.json").is_file()
    assert (output / "DEVELOPMENT_METHOD_SELECTION.json").is_file()
    assert verify_observer_manifest(output)["status"] == "PASS"

    provisional = json.loads(
        (output / "PROVISIONAL_GATE_RESULT.json").read_text(encoding="ascii")
    )
    decision = json.loads((output / "R3_DECISION_INPUT.json").read_text(encoding="ascii"))
    expected_gate = evaluate_opportunity_gates(decision)
    assert provisional["gate_evaluation"] == expected_gate
    assert provisional["live_treatment_authorized"] is False
    selected = json.loads(
        (output / "DEVELOPMENT_METHOD_SELECTION.json").read_text(encoding="ascii")
    )
    assert selected["implementation_authorized"] is True
    assert selected["live_treatment_authorized"] is False

    persisted = "\n".join(
        path.read_text(encoding="ascii") for path in sorted(output.glob("*.json"))
    )
    for forbidden in (
        "private prompt",
        "private response",
        "private credential",
        '"name_embedding"',
        '"fact_embedding"',
        '"api_key"',
        '"messages"',
    ):
        assert forbidden not in persisted


def test_provider_failure_is_invalid_attempt_without_gate_evaluation() -> None:
    failure = build_development_failure(
        run_id="v7-development-provider-failure",
        error=TimeoutError("private request body"),
        protocol_sha256="a" * 64,
        scientific_method_selection_sha256="b" * 64,
        completed_block_count=1,
    )

    assert failure["status"] == "FAILED_CLOSED"
    assert failure["attempt_validity"] == "INVALID_FOR_DEVELOPMENT_GATES"
    assert failure["gate_outcome"] == "NOT_EVALUATED"
    assert failure["development_method_selection_materialized"] is False
    assert failure["live_treatment_authorized"] is False
    assert "private request body" not in json.dumps(failure, sort_keys=True)
