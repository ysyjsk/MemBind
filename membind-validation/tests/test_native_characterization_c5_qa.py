"""Offline contracts for C5's supplemental evidence-answerability Judge view."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.backends.base import JudgeBackendResult  # noqa: E402
import native_characterization_c5_qa as qa  # noqa: E402


class FakeBackend:
    model = "qwen3-32b-fp8"
    config_hash = "a" * 64

    def __init__(self, raw: str = "yes") -> None:
        self.raw = raw
        self.prompts: list[str] = []

    async def judge(self, prompt: str) -> JudgeBackendResult:
        self.prompts.append(prompt)
        return JudgeBackendResult.success(raw_output=self.raw, retry_count=0)


class C5SupplementalQATests(IsolatedAsyncioTestCase):
    async def test_uses_official_rubric_on_frozen_retrieved_evidence_without_reader(self) -> None:
        backend = FakeBackend("YES")
        evaluator = qa.C5EvidenceAnswerabilityEvaluator(backend)
        result = await evaluator.evaluate(
            question_id="07741c45",
            question_type="knowledge-update",
            question="Where do I work now?",
            reference_answer="OpenAI",
            retrieved_facts=["The user moved from Google to OpenAI."],
            retrieval_payload_sha256="b" * 64,
        )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result["correct"])
        self.assertEqual(result["qa_surface"], "retrieved_evidence_answerability")
        self.assertFalse(result["reader_generation_performed"])
        self.assertEqual(result["headline_interpretation_effect"], "none")
        self.assertIn("previous information along with an updated answer", backend.prompts[0])
        self.assertIn("Model Response: The user moved from Google to OpenAI.", backend.prompts[0])
        self.assertNotIn("raw_output", result)
        self.assertNotIn("OpenAI", repr(result))

    async def test_invalid_or_service_error_is_not_counted_as_incorrect(self) -> None:
        invalid = qa.C5EvidenceAnswerabilityEvaluator(FakeBackend("maybe"))
        invalid_result = await invalid.evaluate(
            question_id="07741c45",
            question_type="knowledge-update",
            question="Q",
            reference_answer="A",
            retrieved_facts=["fact"],
            retrieval_payload_sha256="b" * 64,
        )
        self.assertEqual(invalid_result["status"], "INVALID_OUTPUT")
        self.assertIsNone(invalid_result["correct"])
        self.assertIsNone(invalid_result["accuracy"])

    async def test_empty_retrieval_is_explicitly_judged_not_silently_skipped(self) -> None:
        backend = FakeBackend("NO")
        evaluator = qa.C5EvidenceAnswerabilityEvaluator(backend)
        result = await evaluator.evaluate(
            question_id="07741c45",
            question_type="knowledge-update",
            question="Q",
            reference_answer="A",
            retrieved_facts=[],
            retrieval_payload_sha256="b" * 64,
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertFalse(result["correct"])
        self.assertEqual(result["accuracy"], 0.0)
        self.assertEqual(result["retrieved_fact_count"], 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
