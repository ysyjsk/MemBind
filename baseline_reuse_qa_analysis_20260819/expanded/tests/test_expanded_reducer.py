from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from expanded_analysis import ExpandedAnalysisError, reduce_expanded_rows  # noqa: E402


def _row(method: str, history_id: str, question_id: str, correct: bool) -> dict[str, object]:
    return {
        "method": method,
        "history_id": history_id,
        "question_id": question_id,
        "judge_valid": True,
        "correct": correct,
        "reader_valid": True,
        "retrieval_metrics": {"recall_at_1": 1.0, "recall_at_3": 1.0, "recall_at_5": 1.0, "recall_at_10": 1.0, "mrr": 1.0, "ndcg_at_10": 1.0},
    }


def _invalid_row(method: str, history_id: str, question_id: str) -> dict[str, object]:
    row = _row(method, history_id, question_id, False)
    row.update({
        "judge_valid": False,
        "correct": None,
        "reader_valid": False,
        "failure_category": "READER_INVALID",
    })
    return row


class ExpandedReducerTests(unittest.TestCase):
    def test_reduces_paired_questions_and_histories(self) -> None:
        rows = []
        for history in ("h1", "h2"):
            for question in ("q1", "q2"):
                rows.extend([
                    _row("U0", history, f"{history}-{question}", question == "q1"),
                    _row("P(C=2)", history, f"{history}-{question}", question == "q1"),
                ])
        result = reduce_expanded_rows(rows)
        self.assertEqual(result["question_count"], 4)
        self.assertEqual(result["methods"]["U0"]["accuracy"], 0.5)
        self.assertEqual(result["methods"]["P(C=2)"]["accuracy"], 0.5)
        self.assertEqual(result["paired"]["agreement_count"], 4)

    def test_rejects_unpaired_question_inventory(self) -> None:
        with self.assertRaisesRegex(ExpandedAnalysisError, "PAIRED_QUESTION_INVENTORY_MISMATCH"):
            reduce_expanded_rows([_row("U0", "h1", "q1", True), _row("P(C=2)", "h1", "q2", True)])

    def test_invalid_is_in_primary_accuracy_denominator_and_separate_from_discordance(self) -> None:
        rows = [
            _row("U0", "h1", "q1", True),
            _row("P(C=2)", "h1", "q1", True),
            _row("U0", "h1", "q2", True),
            _invalid_row("P(C=2)", "h1", "q2"),
        ]
        result = reduce_expanded_rows(rows)
        pc2 = result["methods"]["P(C=2)"]
        self.assertEqual(pc2["accuracy"], 0.5)
        self.assertEqual(pc2["valid_only_accuracy"], 1.0)
        self.assertEqual(pc2["reader_invalid_count"], 1)
        self.assertEqual(pc2["judge_invalid_count"], 0)
        self.assertEqual(result["paired"]["jointly_valid_pair_count"], 1)
        self.assertEqual(result["paired"]["invalid_pair_count"], 1)
        self.assertEqual(result["paired"]["discordant_count"], 0)


if __name__ == "__main__":
    unittest.main()
