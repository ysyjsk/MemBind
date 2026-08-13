"""Supplemental Judge view of whether C5 retrieval evidence answers the query.

This is deliberately not an end-to-end Reader score: no answer-generation
prompt is invented for C5.  The retrieved facts are the frozen system output
presented to LongMemEval's pinned official answer-check rubric.  Consequently
the result may diagnose an accuracy impact, but cannot change C5's three legal
headline interpretations.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Sequence

from evaluation.backends.base import BackendStatus, JudgeBackend
from evaluation.vendor.longmemeval_evaluate_qa import get_anscheck_prompt


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_YES_RE = re.compile(r"^\s*yes\s*[.!]?\s*$", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*no\s*[.!]?\s*$", re.IGNORECASE)


class C5SupplementalQAError(RuntimeError):
    """Sanitized supplemental-QA input/configuration failure."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise C5SupplementalQAError(code)
    return value


class C5EvidenceAnswerabilityEvaluator:
    """Apply the qualified Judge to retrieved facts as frozen system output."""

    def __init__(self, backend: JudgeBackend) -> None:
        self._backend = backend

    async def evaluate(
        self,
        *,
        question_id: str,
        question_type: str,
        question: str,
        reference_answer: str,
        retrieved_facts: Sequence[str],
        retrieval_payload_sha256: str,
    ) -> dict[str, object]:
        _validate_text(question_id, "question_id_invalid")
        task = _validate_text(question_type, "question_type_invalid")
        query = _validate_text(question, "question_invalid")
        answer = _validate_text(reference_answer, "reference_answer_invalid")
        if (
            not isinstance(retrieval_payload_sha256, str)
            or _SHA256_RE.fullmatch(retrieval_payload_sha256) is None
        ):
            raise C5SupplementalQAError("retrieval_payload_sha256_invalid")
        if isinstance(retrieved_facts, (str, bytes)) or any(
            not isinstance(item, str) for item in retrieved_facts
        ):
            raise C5SupplementalQAError("retrieved_facts_invalid")

        facts = [item.strip() for item in retrieved_facts if item.strip()]
        hypothesis = "\n".join(facts) if facts else "No relevant evidence was retrieved."
        try:
            prompt = get_anscheck_prompt(task, query, answer, hypothesis, False)
        except NotImplementedError:
            raise C5SupplementalQAError("question_type_unsupported") from None
        backend_result = await self._backend.judge(prompt)
        common: dict[str, object] = {
            "qa_surface": "retrieved_evidence_answerability",
            "question_id_sha256": _hash(question_id),
            "retrieval_payload_sha256": retrieval_payload_sha256,
            "retrieved_facts_sha256": _hash(hypothesis),
            "retrieved_fact_count": len(facts),
            "prompt_sha256": _hash(prompt),
            "judge_model": self._backend.model,
            "judge_config_sha256": self._backend.config_hash,
            "reader_generation_performed": False,
            "headline_interpretation_effect": "none",
        }
        if backend_result.status is BackendStatus.SERVICE_ERROR:
            return {
                **common,
                "status": "SERVICE_ERROR",
                "correct": None,
                "accuracy": None,
                "retry_count": backend_result.retry_count,
                "error_class": backend_result.error_class,
            }

        raw = backend_result.raw_output or ""
        if _YES_RE.fullmatch(raw):
            correct: bool | None = True
            status = "SUCCESS"
        elif _NO_RE.fullmatch(raw):
            correct = False
            status = "SUCCESS"
        else:
            correct = None
            status = "INVALID_OUTPUT"
        return {
            **common,
            "status": status,
            "correct": correct,
            "accuracy": float(correct) if isinstance(correct, bool) else None,
            "retry_count": backend_result.retry_count,
            "error_class": None,
        }


def qa_view_for_live_core(result: Mapping[str, object]) -> dict[str, object]:
    """Project an evaluator result onto the live core's no-raw-output schema."""

    allowed = (
        "qa_surface",
        "question_id_sha256",
        "retrieval_payload_sha256",
        "retrieved_facts_sha256",
        "retrieved_fact_count",
        "prompt_sha256",
        "judge_model",
        "judge_config_sha256",
        "reader_generation_performed",
        "headline_interpretation_effect",
        "status",
        "correct",
        "accuracy",
        "retry_count",
        "error_class",
    )
    return {key: result.get(key) for key in allowed}
