"""Official-rubric fidelity and auditable parser tests for LongMemEval."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.backends.base import JudgeBackendResult  # noqa: E402
from evaluation.benchmarks.longmemeval import (  # noqa: E402
    LongMemEvalAdapter,
    official_compatible_label,
    parse_audit_label,
)
from evaluation.schemas import EvaluationItem, EvaluationStatus  # noqa: E402
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt  # noqa: E402


class _Backend:
    model = "qwen3-32b-fp8"
    config_hash = "c" * 64

    def __init__(self, raw_output: str = "YES") -> None:
        self.raw_output = raw_output
        self.prompts: list[str] = []

    async def judge(self, prompt: str) -> JudgeBackendResult:
        self.prompts.append(prompt)
        return JudgeBackendResult.success(raw_output=self.raw_output, retry_count=0)


class _FailedBackend:
    model = "qwen3-32b-fp8"
    config_hash = "d" * 64

    async def judge(self, prompt: str) -> JudgeBackendResult:
        return JudgeBackendResult.service_error(
            retry_count=2,
            error_class="openai.APIConnectionError",
        )


def _item(question_type: str, *, hypothesis: str = "candidate", abstention: bool = False) -> EvaluationItem:
    return EvaluationItem(
        item_id=f"item-{question_type}",
        benchmark="longmemeval",
        question_id=f"q-{question_type}",
        question_type=question_type,
        question="What is the answer?",
        reference_answer="OpenAI",
        hypothesis=hypothesis,
        abstention=abstention,
    )


class OfficialRubricFidelityTests(TestCase):
    def test_knowledge_update_keeps_official_previous_plus_updated_semantics(self) -> None:
        prompt = get_anscheck_prompt(
            "knowledge-update",
            "Where do I work now?",
            "OpenAI",
            "I previously worked at Google, but now I work at OpenAI.",
        )
        self.assertIn("previous information along with an updated answer", prompt)
        self.assertIn("updated answer is the required answer", prompt)
        self.assertIn("I previously worked at Google, but now I work at OpenAI.", prompt)

        old_only = get_anscheck_prompt(
            "knowledge-update", "Where do I work now?", "OpenAI", "Google"
        )
        self.assertIn("previous information along with an updated answer", old_only)
        self.assertIn("Model Response: Google", old_only)

    def test_official_question_type_routes_are_preserved(self) -> None:
        generic = get_anscheck_prompt("single-session-user", "Q", "A", "R")
        assistant = get_anscheck_prompt("single-session-assistant", "Q", "A", "R")
        multi = get_anscheck_prompt("multi-session", "Q", "A", "R")
        temporal = get_anscheck_prompt("temporal-reasoning", "Q", "A", "R")
        abstention = get_anscheck_prompt("single-session-user", "Q", "A", "R", True)
        self.assertEqual(generic, assistant)
        self.assertEqual(generic, multi)
        self.assertIn("do not penalize off-by-one errors", temporal)
        self.assertIn("unanswerable question", abstention)

    def test_pinned_official_prompt_hashes_cover_every_rubric_family(self) -> None:
        expected = {
            ("single-session-user", False): "c973231683d914de5192e37a06cbd1ba0d16c3c5dad99d9fb1242708b6a624d6",
            ("single-session-assistant", False): "c973231683d914de5192e37a06cbd1ba0d16c3c5dad99d9fb1242708b6a624d6",
            ("multi-session", False): "c973231683d914de5192e37a06cbd1ba0d16c3c5dad99d9fb1242708b6a624d6",
            ("temporal-reasoning", False): "68eece862c1e5d18c997191d6dd816a9f56e5ec3b8d04502df332fa71fdb6484",
            ("knowledge-update", False): "992fa870a148dc7958741db4e4d9590f0947b17e1516ecd8b6c6424fd38c6747",
            ("single-session-preference", False): "cac49761fd13dbf5e46b602c9a23867a4c96ad11729ebeb1f9846f85aa2bd15b",
            ("single-session-user", True): "879152708d282cd7102c4a39182451ec48da2bd424d2e29cea52fbf045b59593",
        }
        for (question_type, abstention), digest in expected.items():
            with self.subTest(question_type=question_type, abstention=abstention):
                prompt = get_anscheck_prompt(question_type, "Q", "A", "R", abstention)
                self.assertEqual(hashlib.sha256(prompt.encode("utf-8")).hexdigest(), digest)


class AuditParserTests(TestCase):
    def test_strict_parser_accepts_only_auditable_yes_no_forms(self) -> None:
        for raw in ("yes", "Yes.", "YES", "YES\n"):
            parsed = parse_audit_label(raw)
            self.assertEqual((parsed.label, parsed.normalized_output, parsed.parse_status), (True, "YES", "YES"))
        for raw in ("no", "No.", "NO\n"):
            parsed = parse_audit_label(raw)
            self.assertEqual((parsed.label, parsed.normalized_output, parsed.parse_status), (False, "NO", "NO"))
        for raw in ("", "   ", "maybe", "yes and no", "yesterday", "YES\nNO", "无法明确判断"):
            parsed = parse_audit_label(raw)
            self.assertIsNone(parsed.label)
            self.assertEqual(parsed.parse_status, "INVALID")

    def test_official_headline_parser_is_preserved_separately(self) -> None:
        self.assertTrue(official_compatible_label("perhaps YES, perhaps NO"))
        self.assertFalse(official_compatible_label("maybe"))
        parsed = parse_audit_label("perhaps YES, perhaps NO")
        self.assertIsNone(parsed.label)
        self.assertTrue(official_compatible_label("yesterday"))
        self.assertIsNone(parse_audit_label("yesterday").label)


class LongMemEvalAdapterTests(IsolatedAsyncioTestCase):
    async def test_adapter_routes_every_official_rubric_family(self) -> None:
        routes = (
            ("single-session-user", False),
            ("single-session-assistant", False),
            ("multi-session", False),
            ("temporal-reasoning", False),
            ("knowledge-update", False),
            ("single-session-preference", False),
            ("single-session-user", True),
        )
        for question_type, abstention in routes:
            with self.subTest(question_type=question_type, abstention=abstention):
                backend = _Backend("YES")
                value = _item(question_type, hypothesis="R", abstention=abstention)
                result = await LongMemEvalAdapter(backend).evaluate(value)
                self.assertEqual(
                    backend.prompts,
                    [get_anscheck_prompt(question_type, value.question, "OpenAI", "R", abstention)],
                )
                self.assertEqual(result.status, EvaluationStatus.SUCCESS)

    async def test_adapter_builds_official_prompt_and_never_exact_matches_hypothesis(self) -> None:
        backend = _Backend("Yes.")
        adapter = LongMemEvalAdapter(backend)
        value = _item(
            "knowledge-update",
            hypothesis="I previously worked at Google, but now I work at OpenAI.",
        )
        result = await adapter.evaluate(value)
        expected_prompt = get_anscheck_prompt(
            value.question_type,
            value.question,
            value.reference_answer,
            value.hypothesis,
            value.abstention,
        )
        self.assertEqual(backend.prompts, [expected_prompt])
        self.assertEqual(result.status, EvaluationStatus.SUCCESS)
        self.assertTrue(result.label)
        self.assertEqual(result.prompt_hash, hashlib.sha256(expected_prompt.encode("utf-8")).hexdigest())
        self.assertEqual(result.metadata["rubric_source"], "LongMemEval official get_anscheck_prompt")

    async def test_invalid_output_is_not_automatically_counted_as_wrong(self) -> None:
        result = await LongMemEvalAdapter(_Backend("maybe")).evaluate(_item("multi-session"))
        self.assertEqual(result.status, EvaluationStatus.INVALID_OUTPUT)
        self.assertFalse(result.label)
        self.assertEqual(result.parse_status, "INVALID")
        self.assertIsNone(result.metadata["audit_label"])
        self.assertTrue(result.metadata["parser_disagreement"])
        self.assertEqual(result.raw_output, "maybe")
        self.assertEqual(result.normalized_output, "maybe")
        self.assertFalse(result.metadata["official_compatible_label"])
        self.assertEqual(result.retry_count, 0)
        self.assertIsNone(result.error_class)

    async def test_knowledge_update_old_only_answer_is_judged_not_exact_matched(self) -> None:
        backend = _Backend("NO")
        value = EvaluationItem(
            item_id="ku-old-only",
            benchmark="longmemeval",
            question_id="ku-old-only",
            question_type="knowledge-update",
            question="Where do I work now?",
            reference_answer="OpenAI",
            hypothesis="Google",
        )
        result = await LongMemEvalAdapter(backend).evaluate(value)
        self.assertEqual(len(backend.prompts), 1)
        self.assertIn("Model Response: Google", backend.prompts[0])
        self.assertEqual(result.status, EvaluationStatus.SUCCESS)
        self.assertFalse(result.label)

    async def test_ambiguous_official_yes_is_audited_without_silent_repair(self) -> None:
        result = await LongMemEvalAdapter(_Backend("yes and no")).evaluate(_item("temporal-reasoning"))
        self.assertEqual(result.status, EvaluationStatus.INVALID_OUTPUT)
        self.assertTrue(result.label)
        self.assertIsNone(result.metadata["audit_label"])
        self.assertTrue(result.metadata["parser_disagreement"])
        self.assertEqual(result.raw_output, "yes and no")

    async def test_foreign_benchmark_or_unknown_task_fails_before_backend(self) -> None:
        backend = _Backend()
        foreign = EvaluationItem(
            item_id="foreign",
            benchmark="locomo",
            question_id="q",
            question_type="single-session-user",
            question="Q",
            reference_answer="A",
            hypothesis="H",
        )
        with self.assertRaises(ValueError):
            await LongMemEvalAdapter(backend).evaluate(foreign)
        with self.assertRaises(ValueError):
            await LongMemEvalAdapter(backend).evaluate(_item("unknown-task"))
        self.assertEqual(backend.prompts, [])

    async def test_service_error_remains_unscored_and_preserves_retry_bookkeeping(self) -> None:
        result = await LongMemEvalAdapter(_FailedBackend()).evaluate(_item("single-session-user"))
        self.assertEqual(result.status, EvaluationStatus.SERVICE_ERROR)
        self.assertIsNone(result.label)
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(result.error_class, "openai.APIConnectionError")
        self.assertEqual(result.parse_status, "NOT_RUN")
