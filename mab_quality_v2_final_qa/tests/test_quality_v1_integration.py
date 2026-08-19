from __future__ import annotations

import pytest
from mab_quality_v2_final_qa.compatibility import build_context_pack
from mab_quality_v2_final_qa.dataset_adapter import MABDatasetAdapter

from .test_contracts_adapter import _official_like_record

quality = pytest.importorskip("paper_eval.quality_evaluation_v1")
reader = pytest.importorskip("paper_eval.quality_evaluation_v1_reader")


def test_real_quality_v1_context_pack_and_prompt_are_gold_blind() -> None:
    context = MABDatasetAdapter.from_records([_official_like_record()]).contexts[0]
    qa = context.qa_items[0]
    episodes = tuple(
        quality.RetrievedEpisode(index, f"episode-{index}", session_id)
        for index, session_id in enumerate(qa.gold_session_ids, start=1)
    )
    pack = build_context_pack(
        context=context,
        question=qa.question,
        facts=(),
        episodes=episodes,
        quality_module=quality,
    )
    prompt = reader.render_quality_v1_prompt(
        context_json=pack.context_json,
        question_date=qa.question_date,
        question=qa.question,
    )
    assert pack.evidence_count > 0
    for forbidden in (
        "has_answer",
        "reference_answers",
        "gold_session_ids",
        "qa_pair_id",
    ):
        assert forbidden not in pack.context_json
        assert forbidden not in prompt
