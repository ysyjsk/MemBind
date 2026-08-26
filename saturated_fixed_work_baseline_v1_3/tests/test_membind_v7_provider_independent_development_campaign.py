from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.provider_independent_development_campaign import (
    ProviderIndependentDevelopmentCampaignError,
    materialize_provider_development_artifacts,
    record_provider_development_success,
    validate_provider_development_campaign_identity,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.strict_development_campaign import (
    load_strict_development_protocol,
)


PROJECT = Path(__file__).resolve().parents[1]
PROTOCOL = PROJECT / "v7/BAILIAN_QWEN3_MAX_STRICT_V7_DEVELOPMENT_PROTOCOL_V2.json"
METHOD_SELECTION = PROJECT / "v7/METHOD_SELECTION.json"
PROVIDER_KIND = "COMPOSITE_DEVELOPMENT_STRICT_SCHEMA_TEMPORARY"
CONSTRUCTION = {
    "authority": "alibaba-bailian-openai-compatible-strict-schema-selected-v1",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen3-max-2026-01-23",
    "structured_output_mode": "json_schema",
    "strict_json_schema": True,
}
EMBEDDING = {
    "authority": "siliconflow-openai-compatible-v1",
    "base_url": "https://api.siliconflow.cn/v1",
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "dimension": 1024,
    "dimension_policy": "EXACT_NO_TRUNCATION",
}


def _identity() -> dict[str, object]:
    return {
        "schema_version": "membind.v7.development-campaign-identity.v2",
        "run_id": "v7-development-strict-test",
        "campaign_scope": "TEMPORARY_PROVIDER_DEVELOPMENT",
        "provider_identity_kind": PROVIDER_KIND,
        "construction": dict(CONSTRUCTION),
        "embedding": dict(EMBEDDING),
        "observer_harness": {
            "status": "PASS",
            "source_sha256": {"strict-runner.py": "a" * 64},
        },
        "formal_r1_r3_eligible": False,
        "live_treatment_authorized": False,
        "provider_swap_requires_new_formal_campaign": True,
        "treatment_calls": 0,
        "response_replay_calls": 0,
    }


def _characterization() -> dict[str, object]:
    return {
        "pair_analyses": [],
        "certificate_confusion": {"STABLE/SAME": 1},
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
        "decision_input": {
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
            "stable_prediction_count": 1,
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
        },
    }


def test_strict_protocol_binds_runtime_and_exact_2_6_6_workload() -> None:
    frozen = load_strict_development_protocol(PROTOCOL)

    assert frozen["provider_identity_kind"] == PROVIDER_KIND
    assert frozen["strict_runtime_freeze"]["path"] == (
        "BAILIAN_QWEN3_MAX_STRICT_DEVELOPMENT_RUNTIME_FREEZE.json"
    )
    assert frozen["construction"]["model"] == "qwen3-max-2026-01-23"
    assert frozen["construction"]["strict_json_schema"] is True
    assert frozen["embedding"]["dimension"] == 1024
    assert frozen["workload"]["r1_r2"]["source_count"] == 2
    assert [row["source_count"] for row in frozen["workload"]["r3_blocks"]] == [6, 6]
    assert frozen["formal_r1_r3_eligible"] is False
    assert frozen["live_treatment_authorized"] is False
    assert frozen["provider_swap_requires_new_formal_campaign"] is True
    assert frozen["artifact_permissions"] == {
        "directory_mode": "0700",
        "json_file_mode": "0600",
        "journal_file_mode": "0600",
    }


def test_identity_validator_accepts_explicit_strict_identity_and_rejects_aliasing() -> None:
    validate_provider_development_campaign_identity(
        _identity(),
        expected_provider_identity_kind=PROVIDER_KIND,
        expected_construction_identity=CONSTRUCTION,
        expected_embedding_identity=EMBEDDING,
    )

    drifted = _identity()
    drifted["construction"] = {
        **CONSTRUCTION,
        "authority": EMBEDDING["authority"],
    }
    with pytest.raises(
        ProviderIndependentDevelopmentCampaignError,
        match="identity",
    ):
        validate_provider_development_campaign_identity(
            drifted,
            expected_provider_identity_kind=PROVIDER_KIND,
            expected_construction_identity=CONSTRUCTION,
            expected_embedding_identity=EMBEDDING,
        )


def test_provider_independent_materializer_preserves_formal_root(tmp_path: Path) -> None:
    method = tmp_path / "METHOD_SELECTION.json"
    method.write_bytes(METHOD_SELECTION.read_bytes())
    digest = hashlib.sha256(method.read_bytes()).hexdigest()
    blocks = [
        {
            "block_id": block_id,
            "status": "OBSERVER_ONLY",
            "source_count": 6,
            "real_graphiti_evidence": True,
            "transitions": [],
            "pairs": [],
        }
        for block_id in ("R3-A", "R3-B")
    ]
    output = tmp_path / "strict-development"

    result = materialize_provider_development_artifacts(
        output,
        r1={"status": "PASS_WITH_GUARDS", "real_graphiti_evidence": True},
        r2={"status": "OBSERVER_ONLY", "real_graphiti_evidence": True},
        blocks=blocks,
        characterization=_characterization(),
        campaign_identity=_identity(),
        expected_provider_identity_kind=PROVIDER_KIND,
        expected_construction_identity=CONSTRUCTION,
        expected_embedding_identity=EMBEDDING,
        scientific_method_selection_path=method,
        expected_scientific_method_selection_sha256=digest,
    )

    assert hashlib.sha256(method.read_bytes()).hexdigest() == digest
    assert not (output / "METHOD_SELECTION.json").exists()
    assert result["development_method_selection"]["live_treatment_authorized"] is False
    manifest = json.loads((output / "MANIFEST.json").read_text(encoding="ascii"))
    identity = manifest["campaign_identity"]
    assert identity["provider_identity_kind"] == PROVIDER_KIND
    assert identity["construction"]["authority"] == CONSTRUCTION["authority"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in output.glob("*.json")
    )


def test_success_journal_uses_manifest_seal_instead_of_progress_event() -> None:
    class Journal:
        def __init__(self) -> None:
            self.manifests: list[str] = []

        def record_success(self, *, manifest_sha256: str) -> None:
            self.manifests.append(manifest_sha256)

        def record_progress(self, **_kwargs: object) -> None:
            raise AssertionError("success must not be recorded as block progress")

    journal = Journal()
    record_provider_development_success(
        journal,
        {"status": "SEALED", "manifest_sha256": "a" * 64},
    )

    assert journal.manifests == ["a" * 64]
