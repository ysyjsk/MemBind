from __future__ import annotations

import copy
import json

import pytest
from mab_quality_v2_final_qa.contracts import assert_gold_blind
from mab_quality_v2_final_qa.dataset_adapter import (
    DatasetMappingError,
    MABDatasetAdapter,
)


def _official_like_record() -> dict:
    # MAB's context is an alternating date/turn-list sequence, not a list of pairs.
    context = [
        "Chat Time: 2023/05/01 (Mon) 09:00",
        [
            {"role": "user", "content": "I have a blue bike."},
            {"role": "assistant", "content": "Noted."},
        ],
        "Chat Time: 2023/05/02 (Tue) 10:00",
        [
            {"role": "user", "content": "I work on QA."},
            {"role": "assistant", "content": "Great."},
        ],
        "Chat Time: 2023/05/03 (Wed) 11:00",
        [
            {"role": "user", "content": "I moved to Suzhou."},
            {"role": "assistant", "content": "Okay."},
        ],
    ]
    return {
        "context_id": "ctx-fixture",
        "context": repr(context),
        "questions": ["Where do I live?", "What do I work on?"],
        "answers": [["Suzhou"], ["QA"]],
        "metadata": {
            "source": "longmemeval_s*",
            "question_ids": ["q0", "q1"],
            "qa_pair_ids": ["pair0", "pair1"],
            "question_dates": ["2023/05/04 (Thu) 09:00", "2023/05/04 (Thu) 09:00"],
            "question_types": ["single-session-user", "single-session-user"],
            "haystack_sessions": [
                [
                    [
                        {
                            "role": "user",
                            "content": "I moved to Suzhou.",
                            "has_answer": True,
                        },
                        {"role": "assistant", "content": "Okay.", "has_answer": False},
                    ]
                ],
                [
                    [
                        {
                            "role": "user",
                            "content": "I work on QA.",
                            "has_answer": True,
                        },
                        {"role": "assistant", "content": "Great.", "has_answer": False},
                    ]
                ],
            ],
        },
    }


def test_adapter_maps_official_shape_and_is_gold_blind() -> None:
    adapter = MABDatasetAdapter.from_records(
        [_official_like_record()], source="longmemeval*"
    )
    context = adapter.contexts[0]
    assert len(context.sessions) == 3
    assert len(context.qa_items) == 2
    assert context.qa_items[0].gold_session_ids == (context.sessions[2].session_id,)
    assert context.qa_items[1].gold_session_ids == (context.sessions[1].session_id,)
    assert_gold_blind(context.public_context().as_dict())
    assert "reference_answers" not in json.dumps(context.public_context().as_dict())
    assert adapter.manifest["session_chronology_available"] is True


def test_adapter_rejects_unqualified_chronology() -> None:
    record = _official_like_record()
    record["context"] = "unstructured text without session dates"
    with pytest.raises(DatasetMappingError, match="chronology"):
        MABDatasetAdapter.from_records([record])


def test_gold_blind_rejects_private_fields() -> None:
    with pytest.raises(ValueError, match="GOLD_LEAK_DETECTED"):
        assert_gold_blind({"payload": {"reference_answers": ["x"]}})


def test_public_context_hash_is_independent_of_private_answers() -> None:
    first = _official_like_record()
    second = copy.deepcopy(first)
    second["answers"] = [["private answer changed"], ["another private answer"]]
    first_context = MABDatasetAdapter.from_records([first]).contexts[0]
    second_context = MABDatasetAdapter.from_records([second]).contexts[0]
    assert first_context.context_sha256 == second_context.context_sha256
    assert (
        first_context.public_context().as_dict()
        == second_context.public_context().as_dict()
    )
