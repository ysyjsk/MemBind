from __future__ import annotations

from saturated_fixed_work_baseline_v1_3.longmemeval_state_qa import (
    inspect_longmemeval_current_state,
    paired_state_outcome,
    reader_diagnostic_verdict,
)


def test_active_current_fact_passes_without_entity_summary() -> None:
    graph = {
        "entities": [{"name": "shoe rack", "summary": "historical storage"}],
        "edges": [
            {
                "fact": "The old sneakers are currently in a shoe rack in my closet.",
                "source_entity_key": "user",
                "target_entity_key": "shoe rack",
                "relation_type": "STORED_IN",
                "valid_at": "2023-06-23T00:00:00Z",
                "invalid_at": None,
                "expired_at": None,
            }
        ],
    }
    result = inspect_longmemeval_current_state(
        graph,
        expected_answer="in a shoe rack in my closet",
        observation_time="2023-06-23T07:31:00Z",
    )
    assert result["status"] == "PASS"
    assert result["predicate_authoritative"] is True
    assert result["reader_answer_not_substituted"] is True


def test_entity_summary_cannot_make_current_state_pass() -> None:
    result = inspect_longmemeval_current_state(
        {
            "entities": [{"name": "shoe rack", "summary": "The sneakers are in a shoe rack."}],
            "edges": [],
        },
        expected_answer="in a shoe rack",
        observation_time="2023-06-23T07:31:00Z",
    )
    assert result["status"] == "SUMMARY_ONLY"
    assert result["current_value_active"] is False


def test_inactive_expected_fact_is_not_current() -> None:
    result = inspect_longmemeval_current_state(
        {
            "entities": [],
            "edges": [
                {
                    "fact": "The current count is 5.",
                    "source_entity_key": "user",
                    "relation_type": "HAS_COUNT",
                    "target_entity_key": "films",
                    "valid_at": "2023-01-01T00:00:00Z",
                    "invalid_at": "2023-06-01T00:00:00Z",
                }
            ],
        },
        expected_answer=5,
        observation_time="2023-06-23T07:31:00Z",
    )
    assert result["status"] == "STALE_ONLY"
    assert result["semantic_stale_value_status"] == "NOT_PROVABLE"


def test_numeric_match_uses_token_boundaries() -> None:
    result = inspect_longmemeval_current_state(
        {
            "entities": [],
            "edges": [
                {
                    "fact": "The model has 50 layers.",
                    "source_entity_key": "model",
                    "relation_type": "HAS_LAYERS",
                    "target_entity_key": "architecture",
                }
            ],
        },
        expected_answer=5,
        observation_time="2023-06-23T07:31:00Z",
    )
    assert result["status"] == "FAIL"
    assert result["active_expected_edge_count"] == 0


def test_numeric_match_without_question_anchor_is_not_provable() -> None:
    result = inspect_longmemeval_current_state(
        {
            "entities": [],
            "edges": [
                {
                    "fact": "Isaiah 53:5 is related to Luke 7:1-17.",
                    "source_entity_key": "isaiah 53:5",
                    "relation_type": "RELATED_TO",
                    "target_entity_key": "luke 7:1-17",
                }
            ],
        },
        expected_answer=5,
        observation_time="2023-06-23T07:31:00Z",
        question="How many MCU films did I watch?",
    )
    assert result["status"] == "NOT_PROVABLE"
    assert result["unrelated_expected_match_count"] == 1


def test_structural_multiplicity_is_ambiguous_not_silent_pass() -> None:
    result = inspect_longmemeval_current_state(
        {
            "entities": [],
            "edges": [
                {
                    "fact": "The current count is 5.",
                    "source_entity_key": "user",
                    "relation_type": "HAS_COUNT",
                    "target_entity_key": "films",
                },
                {
                    "fact": "The count was revised and is now 5.",
                    "source_entity_key": "user",
                    "relation_type": "HAS_COUNT",
                    "target_entity_key": "films",
                },
            ],
        },
        expected_answer=5,
        observation_time="2023-06-23T07:31:00Z",
    )
    assert result["status"] == "AMBIGUOUS"
    assert result["conflict_detection"] == "STRUCTURAL_GROUP_MULTIPLICITY_ONLY"


def test_reader_is_diagnostic_and_paired_gate_requires_b0_pass() -> None:
    diagnostic = reader_diagnostic_verdict("1300", "The current number is 1300.")
    assert diagnostic["expected_match"] is True
    assert diagnostic["semantic_authority"] == "DIRECT_GRAPH_PREDICATE"
    assert paired_state_outcome({"status": "PASS"}, {"status": "FAIL"})["b1_semantic_failure"] is True
    assert paired_state_outcome({"status": "AMBIGUOUS"}, {"status": "FAIL"})["b1_semantic_failure"] is False
