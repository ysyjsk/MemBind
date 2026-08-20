from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from result_analysis import build_result_analysis  # noqa: E402


def _row(method: str, question_id: str, label: bool | None, quote: bool = True) -> dict[str, object]:
    return {
        "method": method,
        "question_id": question_id,
        "history_id": "h1",
        "failure_category": "SUCCESS" if label is True else "READER_INVALID",
        "reader": {"finish_reason": "stop", "model": "Qwen/Qwen3-32B"} if label is not None else {},
        "judge": {"status": "SUCCESS", "parse_status": "YES" if label else "NO", "label": label, "model": "Qwen/Qwen3-32B"} if label is not None else {},
        "retrieval": {"context_json": "exact quote" if quote else ""},
        "retrieval_metrics": {"gold_ranks": [1], "recall_at_10": 1.0},
    }


class ResultAnalysisTests(unittest.TestCase):
    def test_invalid_is_operationally_separate_from_semantic_failure(self) -> None:
        rows = [_row("U0", "q1", True), _row("P(C=2)", "q1", None)]
        inventory = {"q1": {"history_id": "h1", "question": "q", "reference_answer": "a", "gold_evidence_quotes": ["exact quote"]}}
        result = build_result_analysis(rows, inventory)
        self.assertEqual(result["methods"]["P(C=2)"]["invalid_count"], 1)
        self.assertEqual(result["methods"]["P(C=2)"]["primary_accuracy"], 0.0)
        self.assertEqual(result["paired"]["invalid_pair_count"], 1)

    def test_context_diagnostic_does_not_claim_session_recall_failure(self) -> None:
        rows = [_row("U0", "q1", False, quote=False), _row("P(C=2)", "q1", True, quote=False)]
        inventory = {"q1": {"history_id": "h1", "question": "q", "reference_answer": "a", "gold_evidence_quotes": ["exact quote"]}}
        result = build_result_analysis(rows, inventory)
        self.assertEqual(result["evidence_diagnostics"]["q1"]["per_method"]["U0"]["gold_recall_at_10"], 1.0)
        self.assertFalse(result["evidence_diagnostics"]["q1"]["per_method"]["U0"]["gold_quote_in_context"])


if __name__ == "__main__":
    unittest.main()
