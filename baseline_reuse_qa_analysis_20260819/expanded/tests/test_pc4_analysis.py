from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pc4_analysis import restore_pc4_gold_ranks, summarize_pc4_rows  # noqa: E402


class PC4AnalysisTests(unittest.TestCase):
    def test_partial_summary_counts_invalid_in_primary_denominator(self) -> None:
        rows = [
            {"judge_valid": True, "correct": True, "reader_valid": True},
            {"judge_valid": True, "correct": False, "reader_valid": True},
            {"judge_valid": False, "correct": None, "reader_valid": False},
        ]
        result = summarize_pc4_rows(rows)
        self.assertEqual(result["question_count"], 3)
        self.assertEqual(result["correct_count"], 1)
        self.assertEqual(result["invalid_count"], 1)
        self.assertAlmostEqual(result["accuracy"], 1 / 3)
        self.assertEqual(result["valid_only_accuracy"], 0.5)

    def test_private_gold_ranks_are_restored_without_changing_scores(self) -> None:
        rows = [
            {
                "method": "P(C=4)",
                "question_id": "q1",
                "correct": False,
                "retrieval_metrics": {"recall_at_10": 1.0},
            },
            {
                "method": "U0",
                "question_id": "q1",
                "correct": True,
                "retrieval_metrics": {"gold_ranks": [3]},
            },
        ]
        restored = restore_pc4_gold_ranks(rows, {"q1": [9]})
        self.assertEqual(restored[0]["retrieval_metrics"]["gold_ranks"], [9])
        self.assertEqual(restored[0]["correct"], False)
        self.assertEqual(restored[1]["retrieval_metrics"]["gold_ranks"], [3])
        self.assertEqual(rows[0]["retrieval_metrics"], {"recall_at_10": 1.0})


if __name__ == "__main__":
    unittest.main()
