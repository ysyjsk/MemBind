"""Offline content-safety contract for the shared baseline quality chain."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from paper_eval import baseline_suite_quality as quality


def test_suite_quality_reuses_frozen_two_sided_reader_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_reader_hash = "1" * 64
    judge_hash = "2" * 64
    frozen = {
        "payload": {
            "baseline_id": "native-graphiti-u0-reader-v2",
            "common_evaluation_policy": {
                "reader_config_sha256": parent_reader_hash,
                "judge_component_config_sha256": judge_hash,
            },
        }
    }

    class FakeReader:
        def __init__(self, *, useronly: bool, **_kwargs: Any) -> None:
            self.useronly = useronly
            self.config_sha256 = "3" * 64 if useronly else parent_reader_hash
            self.public_config = {"useronly": useronly}

    class FakeJudge:
        config_sha256 = judge_hash

    class FakeTransport:
        def __init__(self, **_kwargs: Any) -> None:
            pass

    monkeypatch.setattr(quality, "OfficialConSessionReader", FakeReader)
    monkeypatch.setattr(
        quality,
        "OpenAIChatCompletionsTransport",
        FakeTransport,
    )
    monkeypatch.setattr(
        quality,
        "build_qualified_qwen_judge",
        lambda **_kwargs: FakeJudge(),
    )

    adapters = quality.build_baseline_quality_adapters(
        env={
            "CONSTRUCTION_LLM_BASE_URL": "http://model.invalid/v1/",
            "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
            "CONSTRUCTION_LLM_API_KEY": "not-required",
        },
        frozen_baseline=frozen,
    )

    assert adapters["reader"].useronly is False
    assert adapters["quality_identity"] == {
        "baseline_id": "native-graphiti-u0-reader-v2",
        "reader_config_sha256": parent_reader_hash,
        "judge_config_sha256": judge_hash,
    }


@pytest.mark.asyncio
async def test_quality_chain_passes_private_inputs_live_but_returns_hash_only_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retrieved = ("session-2", "session-1")

    async def fake_retrieval(**kwargs: Any) -> Any:
        assert kwargs["query"] == "PRIVATE_QUESTION"
        return SimpleNamespace(
            retrieved_session_ids=retrieved,
            graphiti_search_calls=1,
            neo4j_read_requests=3,
        )

    monkeypatch.setattr(quality, "run_formal_session_retrieval", fake_retrieval)
    monkeypatch.setattr(quality, "corpus_identity_sha256", lambda _episodes: "c" * 64)
    monkeypatch.setattr(
        quality, "build_episode_bm25_search_config", lambda: "fake-bm25-config"
    )
    monkeypatch.setattr(
        quality,
        "evaluate_session_retrieval",
        lambda **_kwargs: SimpleNamespace(
            evidence_recall_at_10=1.0,
            gold_ranks=(1,),
        ),
    )
    monkeypatch.setattr(
        quality,
        "materialize_ranked_sessions",
        lambda **_kwargs: ("PRIVATE_SESSION_CONTENT",),
    )

    class ReaderResult:
        answer = "PRIVATE_READER_ANSWER"

        def to_artifact(self) -> dict[str, Any]:
            return {
                "status": "SUCCESS",
                "output_sha256": hashlib.sha256(self.answer.encode()).hexdigest(),
                "output_character_count": len(self.answer),
            }

    class Reader:
        async def answer(self, sessions: Any, **kwargs: Any) -> ReaderResult:
            assert sessions == ("PRIVATE_SESSION_CONTENT",)
            assert kwargs["question"] == "PRIVATE_QUESTION"
            return ReaderResult()

    class Judge:
        async def evaluate(self, *, hypothesis: str, inputs: Any) -> dict[str, Any]:
            assert hypothesis == "PRIVATE_READER_ANSWER"
            assert inputs.reference_answer == "PRIVATE_REFERENCE"
            return {
                "status": "SUCCESS",
                "label": True,
                "output_sha256": "d" * 64,
            }

    result = await quality.run_baseline_quality_chain(
        graph=object(),
        record={
            "haystack_session_ids": ["session-1", "session-2"],
            "answer_session_ids": ["session-2"],
            "question": "PRIVATE_QUESTION",
            "question_date": "2026-01-01",
            "question_type": "single-session-user",
            "answer": "PRIVATE_REFERENCE",
        },
        episodes=(object(),),
        history_id="07741c45",
        namespace="pev3-bs-test-u0-07741c45-a001",
        run_id="pev3-bs-test-u0-07741c45-a001",
        reader=Reader(),
        judge=Judge(),
    )

    assert result["qa_accuracy"] == 1.0
    assert result["retrieval"]["retrieved_session_ids_sha256"] == hashlib.sha256(
        json.dumps(list(retrieved), sort_keys=True).encode("utf-8")
    ).hexdigest()
    encoded = json.dumps(result, sort_keys=True)
    for forbidden in (
        "PRIVATE_QUESTION",
        "PRIVATE_REFERENCE",
        "PRIVATE_READER_ANSWER",
        "PRIVATE_SESSION_CONTENT",
    ):
        assert forbidden not in encoded
