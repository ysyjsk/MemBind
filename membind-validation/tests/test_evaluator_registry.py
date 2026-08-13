"""Offline contracts for benchmark-native evaluator dispatch."""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from evaluation.registry import (  # noqa: E402
    EvaluatorRegistry,
    UnknownEvaluatorError,
)
from evaluation.backends.base import BackendStatus, JudgeBackendResult  # noqa: E402
from evaluation.schemas import (  # noqa: E402
    EvaluationItem,
    EvaluationResult,
    EvaluationStatus,
    JudgeQualificationRecord,
)


def item() -> EvaluationItem:
    return EvaluationItem(
        item_id="item-1",
        benchmark="longmemeval",
        question_id="q-1",
        question_type="single-session-user",
        question="Where do I work?",
        reference_answer="OpenAI",
        hypothesis="You work at OpenAI.",
    )


class _Evaluator:
    def __init__(self) -> None:
        self.items: list[EvaluationItem] = []

    async def evaluate(self, value: EvaluationItem) -> str:
        self.items.append(value)
        return value.item_id


class EvaluatorRegistryTests(IsolatedAsyncioTestCase):
    async def test_register_get_and_evaluate_dispatch_exact_benchmark(self) -> None:
        registry = EvaluatorRegistry()
        evaluator = _Evaluator()
        registry.register("longmemeval", evaluator)

        self.assertIs(registry.get("longmemeval"), evaluator)
        self.assertEqual(await registry.evaluate("longmemeval", item()), "item-1")
        self.assertEqual(evaluator.items, [item()])

    async def test_unknown_evaluator_fails_closed_without_generic_fallback(self) -> None:
        registry = EvaluatorRegistry()
        with self.assertRaises(UnknownEvaluatorError):
            registry.get("unknown")
        with self.assertRaises(UnknownEvaluatorError):
            await registry.evaluate("unknown", item())

    async def test_registry_has_no_judge_model_or_benchmark_prompt_state(self) -> None:
        registry = EvaluatorRegistry()
        registry.register("longmemeval", _Evaluator())
        state = vars(registry)
        rendered = repr(state).lower()
        self.assertNotIn("qwen", rendered)
        self.assertNotIn("prompt", rendered)
        self.assertEqual(set(state), {"_evaluators"})

    async def test_duplicate_registration_fails_closed_without_replacement(self) -> None:
        registry = EvaluatorRegistry()
        first = _Evaluator()
        registry.register("longmemeval", first)
        with self.assertRaises(RuntimeError):
            registry.register("longmemeval", _Evaluator())
        self.assertIs(registry.get("longmemeval"), first)


class JudgeQualificationSchemaTests(TestCase):
    def test_future_human_audit_record_has_no_live_or_metric_side_effects(self) -> None:
        record = JudgeQualificationRecord(
            question_id="q-1",
            candidate_answer_id="answer-1",
            qwen_label=True,
            human_label=True,
            agreement=True,
        )
        self.assertTrue(record.agreement)
        self.assertNotIn("kappa", vars(record))
        with self.assertRaises(FrozenInstanceError):
            record.agreement = False  # type: ignore[misc]

    def test_qualification_record_rejects_inconsistent_agreement(self) -> None:
        with self.assertRaises(ValueError):
            JudgeQualificationRecord(
                question_id="q-1",
                candidate_answer_id="answer-1",
                qwen_label=True,
                human_label=False,
                agreement=True,
            )

    def test_qualification_record_is_strictly_typed_and_minimal(self) -> None:
        self.assertEqual(
            [value.name for value in fields(JudgeQualificationRecord)],
            [
                "question_id",
                "candidate_answer_id",
                "qwen_label",
                "human_label",
                "agreement",
            ],
        )
        invalid_values = (
            {"question_id": ""},
            {"candidate_answer_id": ""},
            {"qwen_label": 1},
            {"human_label": None},
            {"agreement": 1},
        )
        baseline = {
            "question_id": "q-1",
            "candidate_answer_id": "answer-1",
            "qwen_label": True,
            "human_label": True,
            "agreement": True,
        }
        for mutation in invalid_values:
            with self.subTest(mutation=mutation), self.assertRaises((TypeError, ValueError)):
                JudgeQualificationRecord(**(baseline | mutation))


class EvaluationResultSchemaTests(TestCase):
    def _result(self, **overrides: object) -> EvaluationResult:
        values: dict[str, object] = {
            "item_id": "item-1",
            "benchmark": "longmemeval",
            "scorer": "official-rubric",
            "judge_model": "qwen3-32b-fp8",
            "label": True,
            "status": EvaluationStatus.SUCCESS,
            "raw_output": "Yes.",
            "normalized_output": "YES",
            "parse_status": "YES",
            "retry_count": 0,
            "error_class": None,
            "prompt_hash": "a" * 64,
            "config_hash": "b" * 64,
            "metadata": {},
        }
        values.update(overrides)
        return EvaluationResult(**values)  # type: ignore[arg-type]

    def test_success_invalid_and_service_states_remain_distinct(self) -> None:
        success = self._result()
        invalid = self._result(
            label=False,
            status=EvaluationStatus.INVALID_OUTPUT,
            raw_output="maybe",
            normalized_output="maybe",
            parse_status="INVALID",
        )
        service = self._result(
            label=None,
            status=EvaluationStatus.SERVICE_ERROR,
            raw_output="",
            normalized_output="",
            parse_status="NOT_RUN",
            error_class="openai.APIConnectionError",
        )
        self.assertTrue(success.label)
        # INVALID keeps the pinned official headline label for compatibility,
        # but is excluded from aggregation by its non-SUCCESS status.
        self.assertFalse(invalid.label)
        self.assertIsNone(service.label)
        with self.assertRaises(FrozenInstanceError):
            success.label = False  # type: ignore[misc]

    def test_result_rejects_incoherent_status_combinations(self) -> None:
        invalid = (
            {"status": EvaluationStatus.SUCCESS, "label": None},
            {"status": EvaluationStatus.SUCCESS, "error_class": "error"},
            {"status": EvaluationStatus.INVALID_OUTPUT, "label": None},
            {"status": EvaluationStatus.INVALID_OUTPUT, "parse_status": "YES"},
            {"status": EvaluationStatus.INVALID_OUTPUT, "error_class": "error"},
            {"status": EvaluationStatus.SERVICE_ERROR, "label": False},
            {"status": EvaluationStatus.SERVICE_ERROR, "error_class": None},
            {"retry_count": 1.5},
            {"prompt_hash": "a"},
            {"config_hash": "z" * 64},
            {"metadata": []},
        )
        for mutation in invalid:
            with self.subTest(mutation=mutation), self.assertRaises((TypeError, ValueError)):
                self._result(**mutation)


class JudgeBackendResultSchemaTests(TestCase):
    def test_backend_result_rejects_incoherent_status_combinations(self) -> None:
        invalid = (
            {
                "status": BackendStatus.SUCCESS,
                "raw_output": None,
                "retry_count": 0,
                "error_class": None,
            },
            {
                "status": BackendStatus.SUCCESS,
                "raw_output": "YES",
                "retry_count": 0,
                "error_class": "error",
            },
            {
                "status": BackendStatus.SERVICE_ERROR,
                "raw_output": "NO",
                "retry_count": 0,
                "error_class": "error",
            },
            {
                "status": BackendStatus.SERVICE_ERROR,
                "raw_output": None,
                "retry_count": 0,
                "error_class": None,
            },
            {
                "status": BackendStatus.SERVICE_ERROR,
                "raw_output": None,
                "retry_count": -1,
                "error_class": "error",
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises((TypeError, ValueError)):
                JudgeBackendResult(**values)
