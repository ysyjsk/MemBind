from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from expanded_analysis import load_expanded_inventory  # noqa: E402
from run_expanded import (  # noqa: E402
    EXACT_EMBEDDING_MODEL,
    EXACT_JUDGE_MODEL,
    EXACT_READER_MODEL,
    EMBEDDING_DIMENSION,
    _build_frozen_episodes,
    build_judge_public_config,
    build_public_question,
)


SOURCE = (
    ROOT.parents[1]
    / "paper-eval-v3/artifacts/paper_eval/development_inputs/"
    "LONGMEMEVAL_S_DEVELOPMENT_EXPOSED_4.json"
)


class ExpandedRunnerContractTests(unittest.TestCase):
    def test_exact_model_contract_is_explicit(self) -> None:
        self.assertEqual(EXACT_READER_MODEL, "Qwen/Qwen3-32B")
        self.assertEqual(EXACT_JUDGE_MODEL, "Qwen/Qwen3-32B")
        self.assertEqual(EXACT_EMBEDDING_MODEL, "Qwen/Qwen3-Embedding-0.6B")
        self.assertEqual(EMBEDDING_DIMENSION, 1024)
        config = build_judge_public_config("https://api.siliconflow.cn/v1")
        self.assertEqual(config["model"], EXACT_JUDGE_MODEL)
        self.assertEqual(config["temperature"], 0)
        self.assertEqual(config["max_tokens"], 10)
        self.assertEqual(config["n"], 1)
        self.assertFalse(config["enable_thinking"])
        self.assertEqual(config["max_attempts"], 1)

    def test_public_question_projection_is_gold_blind(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        public = build_public_question(inventory["questions"][0])
        self.assertNotIn("reference_answer", public)
        self.assertNotIn("gold_session_ids", public)
        self.assertNotIn("gold_evidence_quotes", public)

    def test_runner_contract_rejects_labels_in_public_projection(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        row = dict(inventory["questions"][0])
        public = build_public_question(row)
        self.assertEqual(set(public), {
            "question_id", "qa_pair_id", "history_id", "question_type",
            "question_date", "question",
        })

    def test_extension_question_id_does_not_change_frozen_episode_identity(self) -> None:
        inventory = load_expanded_inventory(ROOT / "expanded_qa_inventory.json", SOURCE)
        row = inventory["questions"][0]
        source = __import__("json").loads(SOURCE.read_text(encoding="utf-8"))
        source_record = next(
            record for record in source["records"] if record["question_id"] == row["history_id"]
        )
        episodes = _build_frozen_episodes(source_record, row)
        self.assertEqual(episodes[0].name, "07741c45::episode::0000")
        self.assertNotIn("ext-001", episodes[0].name)


if __name__ == "__main__":
    unittest.main()
