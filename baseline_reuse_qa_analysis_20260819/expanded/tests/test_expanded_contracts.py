from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from expanded_analysis import (  # noqa: E402
    CLAIM_SCOPE,
    ExpandedAnalysisError,
    build_gold_blind_projection,
    load_expanded_inventory,
    validate_gold_provenance,
)


SOURCE = (
    ROOT.parents[1]
    / "paper-eval-v3/artifacts/paper_eval/development_inputs/"
    "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
)


class ExpandedContractTests(unittest.TestCase):
    def test_inventory_is_four_paired_histories_with_four_additional_questions(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        self.assertEqual(inventory["claim_scope"], CLAIM_SCOPE)
        self.assertEqual(inventory["question_count"], 16)
        self.assertEqual(inventory["questions_per_history"], {history: 4 for history in inventory["history_order"]})
        self.assertEqual(set(inventory["history_order"]), {"07741c45", "b6019101", "6071bd76", "a2f3aa27"})

    def test_public_projection_excludes_gold_labels(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        private = inventory["questions"][0]
        public = build_gold_blind_projection(private)
        self.assertNotIn("reference_answer", public)
        self.assertNotIn("gold_session_ids", public)
        self.assertEqual(public["question_id"], private["question_id"])

    def test_inventory_rejects_foreign_gold_session(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        row = dict(inventory["questions"][0])
        row["gold_session_ids"] = ["foreign-session"]
        with self.assertRaisesRegex(ExpandedAnalysisError, "GOLD_SESSION_NOT_IN_HISTORY"):
            build_gold_blind_projection(row, allowed_session_ids=set(inventory["history_sessions"][row["history_id"]]))

    def test_inventory_requires_exact_gold_provenance_and_timeline(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        for row in inventory["questions"]:
            self.assertTrue(row["gold_evidence_quotes"])
            self.assertTrue(row["gold_session_ids"])
            self.assertTrue(row["question_date"])

    def test_inventory_rejects_quote_not_present_in_bound_session(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        row = dict(inventory["questions"][0])
        row["gold_evidence_quotes"] = ["quote absent from source"]
        with self.assertRaisesRegex(ExpandedAnalysisError, "GOLD_QUOTE_NOT_IN_SESSION"):
            validate_gold_provenance(row, json.loads(SOURCE.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
