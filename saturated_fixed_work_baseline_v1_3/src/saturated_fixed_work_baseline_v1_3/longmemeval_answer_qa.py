"""Layered LongMemEval answer QA for already-built graph pairs.

The original state-QA lane intentionally used a strict temporal graph
predicate.  That predicate is useful for auditing representation of a
current value, but it is not the LongMemEval answer-accuracy metric.  This
module keeps the two questions separate:

* ``answer_evaluation`` scores the frozen Reader response with the pinned
  official Judge result (when available);
* ``state_diagnostic`` remains the conservative graph inspection result.

No function in this module reads source conversations or entity summaries as
answer evidence, and no lexical fallback is promoted to semantic truth.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any


class LongMemEvalAnswerQAError(ValueError):
    """The layered answer-QA contract is malformed."""


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?(?![A-Za-z0-9])")


def normalize_answer_text(value: Any) -> str:
    """Normalize only presentation details; do not infer semantic equivalence."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("×", "x").replace("–", "-").replace("—", "-")
    return " ".join(text.split())


def answer_surface_diagnostic(expected_answer: Any, reader_answer: str) -> dict[str, Any]:
    """Return a transparent lexical diagnostic for a Reader response.

    This is deliberately *not* a semantic score.  It is useful for debugging
    prompt/retrieval failures and for checking that numeric values use token
    boundaries rather than substring accidents.
    """

    if not isinstance(reader_answer, str) or not reader_answer.strip():
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_READER_ANSWER_INVALID")
    expected = normalize_answer_text(expected_answer)
    observed = normalize_answer_text(reader_answer)
    if not expected:
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_EXPECTED_ANSWER_EMPTY")
    if _NUMBER_RE.fullmatch(expected):
        match = any(
            normalize_answer_text(item.group(0)) == expected
            for item in _NUMBER_RE.finditer(observed)
        )
    else:
        match = expected in observed
    return {
        "status": "LEXICAL_MATCH" if match else "LEXICAL_NO_MATCH",
        "expected_answer_normalized": expected,
        "reader_answer_normalized": observed,
        "semantic_authority": "NONE",
        "official_judge_required": True,
    }


def evaluate_official_answer(
    *, expected_answer: Any, reader_answer: str, judge: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Project a qualified official Judge response into answer accuracy.

    ``judge`` is expected to be the private result from the existing
    LongMemEval Judge adapter.  A missing or invalid result is explicitly
    unscored; it is never converted to an incorrect answer.  This mirrors the
    repository's terminal Judge semantics and preserves a valid B0 denominator.
    """

    surface = answer_surface_diagnostic(expected_answer, reader_answer)
    if judge is None:
        return {
            "status": "UNSCORED_NO_OFFICIAL_JUDGE",
            "correct": None,
            "semantic_authority": "NONE",
            "judge_status": None,
            "surface_diagnostic": surface,
        }
    if not isinstance(judge, Mapping):
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_JUDGE_RESULT_INVALID")
    status = str(judge.get("status") or "")
    label = judge.get("label")
    if status == "SERVICE_ERROR":
        return {
            "status": "UNSCORED_JUDGE_SERVICE_ERROR",
            "correct": None,
            "semantic_authority": "NONE",
            "judge_status": status,
            "surface_diagnostic": surface,
        }
    if status == "INVALID_OUTPUT":
        return {
            "status": "UNSCORED_JUDGE_INVALID_OUTPUT",
            "correct": None,
            "semantic_authority": "NONE",
            "judge_status": status,
            "surface_diagnostic": surface,
        }
    if status != "SUCCESS" or type(label) is not bool:
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_JUDGE_RESULT_INCONSISTENT")
    return {
        "status": "PASS" if label else "FAIL",
        "correct": bool(label),
        "semantic_authority": "OFFICIAL_LONGMEMEVAL_JUDGE",
        "judge_status": status,
        "judge_label": bool(label),
        "surface_diagnostic": surface,
    }


def paired_answer_outcome(
    b0: Mapping[str, Any], b1: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare answer accuracy separately from graph-state diagnostics."""

    if not isinstance(b0, Mapping) or not isinstance(b1, Mapping):
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_PAIRED_ANSWER_INVALID")
    b0_status = str(b0.get("status") or "")
    b1_status = str(b1.get("status") or "")
    if not b0_status or not b1_status:
        raise LongMemEvalAnswerQAError("LONGMEMEVAL_PAIRED_ANSWER_STATUS_INVALID")
    b0_pass = b0_status == "PASS"
    b1_pass = b1_status == "PASS"
    return {
        "b0_answer_status": b0_status,
        "b1_answer_status": b1_status,
        "b0_answer_pass": b0_pass,
        "b1_answer_pass": b1_pass,
        "answer_divergence": b0_status != b1_status,
        "concrete_b1_answer_failure": b0_pass and b1_status == "FAIL",
        "answer_decision_authority": (
            "OFFICIAL_LONGMEMEVAL_JUDGE"
            if b0.get("semantic_authority") == "OFFICIAL_LONGMEMEVAL_JUDGE"
            and b1.get("semantic_authority") == "OFFICIAL_LONGMEMEVAL_JUDGE"
            else "NONE"
        ),
        "state_diagnostic_is_separate": True,
    }


__all__ = [
    "LongMemEvalAnswerQAError",
    "normalize_answer_text",
    "answer_surface_diagnostic",
    "evaluate_official_answer",
    "paired_answer_outcome",
]
