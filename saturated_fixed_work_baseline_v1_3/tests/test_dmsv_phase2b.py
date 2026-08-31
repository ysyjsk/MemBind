from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.membind_v7.dmsv_phase2b import (
    build_dominant_request_delta_matrix,
    canonical_dedupe_request_identity,
    summarize_base_view_paths,
)


def _base_context() -> dict:
    return {
        "extracted_nodes": [
            {
                "id": 0,
                "name": "Alice",
                "entity_type": "Person",
                "entity_type_description": "person",
                "allowed_candidate_ids": [0, 1],
            }
        ],
        "existing_nodes": [
            {"candidate_id": 0, "name": "Alice", "entity_types": ["Person"], "summary": "old"},
            {"candidate_id": 1, "name": "Alicia", "entity_types": ["Person"], "summary": "related"},
        ],
        "episode_content": "Alice met Bob.",
        "previous_episodes": [{"content": "prior state", "timestamp": "2023-01-01"}],
    }


def test_base_view_audit_fails_closed_when_only_native_timing_is_partial() -> None:
    result = summarize_base_view_paths(
        {
            "BV-NATIVE": {
                "ready_before_need": False,
                "blocks_authoritative_publication": False,
                "snapshot_identity_proven": True,
                "lifecycle_proven": True,
                "maintenance_cost_proven": True,
            },
            "BV-VERSIONED": {
                "ready_before_need": None,
                "blocks_authoritative_publication": None,
                "snapshot_identity_proven": None,
                "lifecycle_proven": None,
                "maintenance_cost_proven": None,
            },
            "BV-PERSISTENT": {
                "ready_before_need": None,
                "blocks_authoritative_publication": None,
                "snapshot_identity_proven": None,
                "lifecycle_proven": None,
                "maintenance_cost_proven": None,
            },
        }
    )

    assert result["verdict"] == "BLOCKED"
    assert result["main_track_candidate"] is False
    assert [row["status"] for row in result["paths"]] == ["FAIL", "UNKNOWN", "UNKNOWN"]


def test_dedupe_request_identity_includes_full_prompt_and_epoch_closure() -> None:
    base = _base_context()
    identity = canonical_dedupe_request_identity(
        base,
        model_epoch="model-v1",
        config_epoch="config-v1",
        schema_epoch="schema-v1",
        index_epoch="index-v1",
    )
    changed = dict(base)
    changed["previous_episodes"] = [{"content": "different prior state", "timestamp": "2023-01-01"}]
    changed_identity = canonical_dedupe_request_identity(
        changed,
        model_epoch="model-v1",
        config_epoch="config-v1",
        schema_epoch="schema-v1",
        index_epoch="index-v1",
    )
    assert identity["request_digest"] != changed_identity["request_digest"]


def test_dominant_request_matrix_marks_every_structural_change_as_changed() -> None:
    rows = build_dominant_request_delta_matrix(_base_context())

    assert {row["change"] for row in rows} == {
        "candidate_payload",
        "topk_order",
        "topk_membership",
        "previous_episodes",
        "unresolved_membership_and_batch_shape",
        "current_episode_content",
        "model_epoch",
        "config_epoch",
        "schema_epoch",
        "index_epoch",
    }
    assert all(row["request_equal"] is False for row in rows)
