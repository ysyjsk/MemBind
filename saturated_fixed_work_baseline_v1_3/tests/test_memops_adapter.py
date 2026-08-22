from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.memops_adapter import (
    MemOpsAdapterError,
    build_episode_inputs,
    build_memops_qa_projection,
    build_memops_source_record,
    build_workload_identity,
    inspect_current_state,
    parse_memops_sample,
    select_memops_samples,
)
from saturated_fixed_work_baseline_v1_3.memops_qualification import (
    MemOpsQualificationError,
    _load_official_memops_evaluator,
    _MemOpsOfficialJudge,
    _official_memops_judge_entry,
    _write_new_json,
    b0_eligibility,
    compare_b0_b1,
    load_qualified_b0_result,
)


ROOT = Path("/data/predator/ly/third_party/MemOps")


def test_official_update_samples_are_selected_from_gold_only() -> None:
    samples = select_memops_samples(ROOT, limit=5)
    assert len(samples) == 5
    assert [sample.operation_type for sample in samples] == ["Update"] * 5
    assert [sample.sample_id for sample in samples] == ["A01", "A05", "A13", "A14", "A28"]
    for sample in samples:
        assert sample.transitions
        assert all(
            transition.old_value != transition.new_value
            and transition.old_segment_index != transition.new_segment_index
            for transition in sample.transitions
        )
        assert sample.questions
        assert all(question.evaluation_type == "CandidateDisambiguation" for question in sample.questions)


def test_segment_projection_preserves_order_and_bytes() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    episodes = build_episode_inputs(sample, "fresh-memops-test-b0")
    assert [episode.source_sequence for episode in episodes] == list(range(len(episodes)))
    assert [episode.session_id for episode in episodes] == [
        f"A01:segment:{index:03d}" for index in (1, 2, 3)
    ]
    assert episodes[0].reference_time < episodes[1].reference_time < episodes[2].reference_time
    assert "My official title when I started" in episodes[0].body
    assert "Senior Data Analyst" in episodes[1].body
    assert "Lead Data Analyst" in episodes[2].body
    assert len({episode.source_hash for episode in episodes}) == len(episodes)


def test_b0_b1_workload_identity_is_namespace_independent() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    b0 = build_episode_inputs(sample, "b0-fresh")
    b1 = build_episode_inputs(sample, "b1-fresh")
    assert [episode.body for episode in b0] == [episode.body for episode in b1]
    assert [episode.source_hash for episode in b0] == [episode.source_hash for episode in b1]
    assert build_workload_identity(sample, b0) == build_workload_identity(sample, b1)


def test_gold_qa_projection_retains_original_answer_and_rubric() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    public, private = build_memops_qa_projection(sample, sample.questions[0])
    assert "reference_answer" not in public
    assert public["question_type"] == "MemOps-CandidateDisambiguation"
    assert private["reference_answer"] == sample.questions[0].expected_answer
    assert private["judge_rubric"] == dict(sample.questions[0].judge_rubric)
    assert private["gold_session_ids"]


def test_qa_source_record_preserves_official_turn_shape_and_content() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    episodes = build_episode_inputs(sample, "qa-source-shape")
    record = build_memops_source_record(sample, episodes)
    conversations = sample.raw["conversations"]
    assert record["haystack_sessions"] == [row["dialogue"] for row in conversations]
    assert all(isinstance(session, list) and session for session in record["haystack_sessions"])


def test_current_state_inspection_does_not_use_uuid_equality() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    value = sample.latest_confirmed_value
    graph = {
        "entities": [],
        "edges": [
            {
                "fact": f"The current job title is {value}.",
                "invalid_at": None,
                "expired_at": None,
            }
        ],
    }
    result = inspect_current_state(sample, graph)
    assert result["status"] == "PASS"
    assert result["canonical_graph_used_for_uuid_equality"] is False


def test_current_state_requires_active_fact_not_entity_summary() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    graph = {
        "entities": [{"summary": f"Historical and current title: {sample.latest_confirmed_value}"}],
        "edges": [],
    }
    result = inspect_current_state(sample, graph)
    assert result["status"] == "FAIL"
    assert result["current_value_active"] is False
    assert result["current_value_in_entity_summary"] is True


def test_current_state_distinguishes_transition_fact_from_stale_current_fact() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    stale = sample.stale_confirmed_values[0]
    current = sample.latest_confirmed_value
    transition_only = {
        "entities": [],
        "edges": [{"fact": f"Title changed from {stale} to {current}."}],
    }
    transition_result = inspect_current_state(sample, transition_only)
    assert transition_result["status"] == "PASS"
    assert transition_result["active_transition_mentions"] == [stale.casefold()]

    stale_separate = {
        "entities": [],
        "edges": [
            {"fact": f"Current title is {current}."},
            {"fact": f"Current title is {stale}."},
        ],
    }
    stale_result = inspect_current_state(sample, stale_separate)
    assert stale_result["status"] == "AMBIGUOUS"
    assert stale_result["stale_active_mentions"] == [stale.casefold()]


def test_current_state_uses_observation_time_and_dynamic_conflict_group() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    current = sample.latest_confirmed_value
    stale = sample.stale_confirmed_values[-1]
    graph = {
        "entities": [],
        "edges": [
            {
                "source_entity_key": "user",
                "relation_type": "HAS_TITLE",
                "fact": f"Current title is {current}.",
                "valid_at": "2000-01-01T00:02:00Z",
                "invalid_at": "2025-01-01T00:00:00Z",
            },
            {
                "source_entity_key": "company",
                "relation_type": "PLANNED_TITLE",
                "fact": f"A planned label contains {stale}.",
            },
            {
                "source_entity_key": "user",
                "relation_type": "HAS_TITLE",
                "fact": f"Future title is {stale}.",
                "valid_at": "2025-01-01T00:00:00Z",
            },
        ],
    }
    result = inspect_current_state(sample, graph)
    assert result["status"] == "PASS"
    assert result["current_value_active"] is True
    assert result["stale_active_mentions"] == []


def test_official_memops_judge_entry_and_prompt_preserve_gold_rubric() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    qa = sample.questions[0]
    entry = _official_memops_judge_entry(sample, qa, "Senior Data Analyst")
    module = _load_official_memops_evaluator(ROOT)
    prompt = module.build_evaluation_prompt(
        entry,
        evidence_conversation=module.format_evidence_conversation(sample.raw),
    )
    assert entry["judge_rubric"] == dict(qa.judge_rubric)
    assert entry["gold_operations"] == sample.raw["operations"]
    assert entry["candidate_options"] == list(qa.candidate_options)
    assert "You are evaluating a MemOps lifecycle memory task" in prompt
    assert qa.expected_answer in prompt
    assert "CandidateDisambiguation" in prompt


def test_official_memops_judge_uses_official_parser() -> None:
    sample = select_memops_samples(ROOT, limit=1)[0]
    qa = sample.questions[0]

    class Transport:
        async def complete(self, request: dict[str, object]) -> SimpleNamespace:
            prompt = request["messages"][0]["content"]  # type: ignore[index]
            assert qa.expected_answer in prompt
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "answer_score": 1,
                        "judge_operation_detection": {"tp": 1, "fp": 0, "fn": 0},
                        "stale_value": 0,
                        "judge_provenance_support": 1,
                        "extra_detail_type": "none",
                        "reason": "confirmed current candidate",
                    }
                ),
                prompt_tokens=100,
                completion_tokens=20,
                finish_reason="stop",
            )

    judge = _MemOpsOfficialJudge(
        sample=sample,
        qa=qa,
        transport=Transport(),
        module=_load_official_memops_evaluator(ROOT),
    )
    result = asyncio.run(judge.evaluate(hypothesis=qa.expected_answer, inputs=None))
    assert result == {"status": "SUCCESS", "label": True}
    assert judge.last_metrics is not None
    assert judge.last_metrics["answer_score"] == 1
    assert judge.last_metrics["stale_value"] == 0
    assert judge.last_metrics["raw_prompt_persisted"] is False


def test_invalid_missing_cross_segment_update_fails_closed(tmp_path: Path) -> None:
    source = {
        "operation_type": "Update",
        "operations": [
            {
                "operation_id": "op1",
                "type": "update",
                "validity": "confirmed",
                "trigger_span": {"segment_index": 1},
                "target": {"target_id": "x", "target_name": "X"},
                "old_value": "a",
                "new_value": "b",
            }
        ],
        "conversations": [
            {"segment_index": 1, "dialogue": [{"role": "user", "content": "x"}]}
        ],
        "answer": [],
    }
    path = tmp_path / "X_update.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(MemOpsAdapterError, match="NO_CROSS_SEGMENT"):
        parse_memops_sample(path)


def test_b0_gate_uses_native_block_result_contract() -> None:
    result = {
        "status": "LIVE_COMPLETE",
        "methods": ["B0_NATIVE_SERIAL"],
        "sample_ids": ["A01"],
        "outputs": [
            {
                "sample_id": "A01",
                "metrics": {
                    "valid": True,
                    "episode_count": 3,
                    "created_sequences": [0, 1, 2],
                },
                "state_inspection": {"status": "PASS"},
                "qa_summary": {
                    "all_correct": True,
                    "graph_write_attempts": 0,
                    "construction_calls": 0,
                    "graph_mutated": False,
                },
            }
        ],
    }
    gate = b0_eligibility(result)
    assert gate["status"] == "B0_QUALIFIED"


def test_b0_gate_rejects_qa_mutation_or_incomplete_publication() -> None:
    result = {
        "status": "LIVE_COMPLETE",
        "methods": ["B0_NATIVE_SERIAL"],
        "sample_ids": ["A01"],
        "outputs": [
            {
                "sample_id": "A01",
                "metrics": {
                    "valid": True,
                    "episode_count": 3,
                    "created_sequences": [0, 1],
                },
                "state_inspection": {"status": "PASS"},
                "qa_summary": {
                    "all_correct": True,
                    "graph_write_attempts": 1,
                    "construction_calls": 0,
                    "graph_mutated": True,
                },
            }
        ],
    }
    gate = b0_eligibility(result)
    assert gate["status"] == "STOP_MEMOPS_GRAPHITI_B0_INELIGIBLE"
    assert {row["reason"] for row in gate["failures"]} == {
        "PUBLICATION_INCOMPLETE",
        "QA_NOT_ELIGIBLE",
    }


def _paired_output(method: str, *, state: str, qa_correct: bool) -> dict[str, object]:
    return {
        "sample_id": "A01",
        "method": method,
        "workload_sha256": "a" * 64,
        "state_inspection": {"status": state},
        "qa_summary": {"all_correct": qa_correct},
    }


def test_paired_gate_requires_state_divergence_not_qa_variance_alone() -> None:
    result = {
        "sample_ids": ["A01"],
        "outputs": [
            _paired_output("B0_NATIVE_SERIAL", state="PASS", qa_correct=True),
            _paired_output("B1_NAIVE_WHOLE_UPDATE_ASYNC", state="PASS", qa_correct=False),
        ],
    }
    assert compare_b0_b1(result)["status"] == "STOP_MEMOPS_NO_B1_DIVERGENCE"

    result["outputs"][1] = _paired_output(  # type: ignore[index]
        "B1_NAIVE_WHOLE_UPDATE_ASYNC", state="AMBIGUOUS", qa_correct=True
    )
    assert compare_b0_b1(result)["status"] == "GO_MEMOPS_B1_ATTACK_QUALIFIED"


def test_b1_loader_rechecks_append_only_b0_gate(tmp_path: Path) -> None:
    result = {
        "status": "LIVE_COMPLETE",
        "methods": ["B0_NATIVE_SERIAL"],
        "sample_ids": ["A01"],
        "outputs": [
            {
                "sample_id": "A01",
                "metrics": {"valid": True, "episode_count": 1, "created_sequences": [0]},
                "state_inspection": {"status": "PASS"},
                "qa_summary": {
                    "all_correct": True,
                    "graph_write_attempts": 0,
                    "construction_calls": 0,
                    "graph_mutated": False,
                },
            }
        ],
    }
    _write_new_json(tmp_path / "b0_result.json", result)
    _write_new_json(tmp_path / "b0_gate.json", {"status": "B0_QUALIFIED"})
    assert load_qualified_b0_result(tmp_path)["status"] == "LIVE_COMPLETE"

    (tmp_path / "b0_gate.json").write_text('{"status":"B0_QUALIFIED","payload_sha256":"bad"}\n')
    with pytest.raises(MemOpsQualificationError, match="ARTIFACT_HASH_MISMATCH"):
        load_qualified_b0_result(tmp_path)
