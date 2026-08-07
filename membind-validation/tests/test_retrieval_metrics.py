import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from retrieval_eval import rank_biased_overlap, retrieval_metrics  # noqa: E402


class RetrievalMetricTests(TestCase):
    def test_identical_finite_rankings_have_full_rbo(self):
        self.assertAlmostEqual(rank_biased_overlap(["a", "b", "c"], ["a", "b", "c"]), 1.0)

    def test_empty_and_disjoint_rankings_have_expected_bounds(self):
        self.assertEqual(rank_biased_overlap([], []), 1.0)
        self.assertEqual(rank_biased_overlap(["a"], ["b"]), 0.0)

    def test_retrieval_metrics_reports_reference_guardrails(self):
        metrics = retrieval_metrics(["gold", "other"], ["gold"], ["gold", "other"])

        self.assertEqual(metrics["evidence_recall_at_10"], 1.0)
        self.assertEqual(metrics["episode_set_overlap_with_m0"], 1.0)
        self.assertAlmostEqual(metrics["rank_biased_overlap_with_m0"], 1.0)


if __name__ == "__main__":
    import unittest

    unittest.main()
