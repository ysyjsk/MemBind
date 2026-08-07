import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import freeze_split, render_episode_body, build_episodes  # noqa: E402


class SplitFreezeTests(TestCase):
    def test_freeze_split_uses_protocol_filter_and_sha256_sort(self):
        records = []
        for qid in ["q9", "q1", "q2", "q3", "q4", "q5", "q6", "q7", "q8", "q_abs_abs", "other"]:
            records.append(
                {
                    "question_id": qid,
                    "question_type": "knowledge-update" if qid != "other" else "temporal-reasoning",
                    "haystack_sessions": [[{"role": "user", "content": qid}]],
                    "haystack_dates": ["2026-01-01T00:00:00Z"],
                }
            )
        with tempfile.TemporaryDirectory() as tmp:
            data_path = Path(tmp) / "longmemeval_s_cleaned.json"
            data_path.write_text(json.dumps(records), encoding="utf-8")
            split = freeze_split(data_path, Path(tmp) / "artifacts")

            eligible = [r for r in records if r["question_type"] == "knowledge-update" and not r["question_id"].endswith("_abs")]
            eligible.sort(key=lambda x: hashlib.sha256(x["question_id"].encode()).hexdigest())
            expected = [r["question_id"] for r in eligible]
            self.assertEqual(split.calibration_question_ids, expected[:4])
            self.assertEqual(split.evaluation_question_ids, expected[4:12])
            frozen = json.loads((Path(tmp) / "artifacts" / "frozen_split.json").read_text(encoding="utf-8"))
            self.assertEqual(frozen["source_sha256"], split.source_sha256)
            self.assertEqual(frozen["calibration_question_ids"], expected[:4])

    def test_episode_body_preserves_roles(self):
        body = render_episode_body(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
                {"role": "user", "content": "new fact"},
            ]
        )
        self.assertEqual(body, "[USER] hello\n[ASSISTANT] hi\n[USER] new fact")

    def test_build_episodes_keeps_all_sessions_in_order(self):
        instance = {
            "question_id": "qid",
            "haystack_sessions": [
                [{"role": "user", "content": "first"}],
                [{"role": "assistant", "content": "second"}],
            ],
            "haystack_dates": ["2026-01-01", "2026-01-02"],
        }
        episodes = build_episodes(instance)
        self.assertEqual([e.source_sequence for e in episodes], [0, 1])
        self.assertIn("[USER] first", episodes[0].body)
        self.assertIn("[ASSISTANT] second", episodes[1].body)

