import sys
from collections import Counter
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay_driver import build_run_plan, validate_formal_plan  # noqa: E402


class RunPlanTests(TestCase):
    def test_formal_plan_has_correct_lane_counts_and_is_deterministic(self):
        split = {"evaluation_question_ids": [f"q{i}" for i in range(8)]}
        plan1 = build_run_plan(split)
        plan2 = build_run_plan(split)
        self.assertEqual(plan1, plan2)
        self.assertEqual(len(plan1), 64)
        lane_counts = Counter(item["lane"] for item in plan1)
        self.assertEqual(lane_counts["correctness"], 16)
        self.assertEqual(lane_counts["performance"], 48)
        perf = [item for item in plan1 if item["lane"] == "performance"]
        self.assertEqual(
            Counter((item["method"], item["repeat"]) for item in perf),
            Counter(
                {
                    ("M0", 0): 8,
                    ("M0", 1): 8,
                    ("M1", 0): 8,
                    ("M1", 1): 8,
                    ("M2", 0): 8,
                    ("M2", 1): 8,
                }
            ),
        )
        self.assertEqual({item["attempt"] for item in plan1}, {"formal01"})
        self.assertTrue(all(item["run_id"].startswith("formal01_run_") for item in plan1))
        self.assertEqual(len({item["run_id"] for item in plan1}), 64)

    def test_each_read_only_replay_runs_after_its_capture_dependency(self):
        split = {"evaluation_question_ids": [f"q{i}" for i in range(8)]}
        plan = build_run_plan(split)
        positions = {item["run_id"]: index for index, item in enumerate(plan)}
        captures = {
            item["question_id"]: item
            for item in plan
            if item["lane"] == "correctness" and item["mode"] == "capture"
        }

        for replay in [item for item in plan if item["mode"] == "replay"]:
            capture = captures[replay["question_id"]]
            self.assertEqual(replay["depends_on"], capture["run_id"])
            self.assertLess(positions[capture["run_id"]], positions[replay["run_id"]])

        self.assertEqual(sum("depends_on" in item for item in plan), 8)

    def test_attempt_changes_every_run_id_without_changing_lane_distribution(self):
        split = {"evaluation_question_ids": [f"q{i}" for i in range(8)]}

        first = build_run_plan(split, attempt="formal07")
        second = build_run_plan(split, attempt="formal08")

        self.assertEqual({item["attempt"] for item in first}, {"formal07"})
        self.assertEqual({item["attempt"] for item in second}, {"formal08"})
        self.assertTrue(
            all(left["run_id"] != right["run_id"] for left, right in zip(first, second))
        )

    def test_builder_and_validator_require_exactly_eight_question_ids(self):
        with self.assertRaisesRegex(ValueError, "8 evaluation question ids"):
            build_run_plan({"evaluation_question_ids": [f"q{i}" for i in range(7)]})

        plan = build_run_plan({"evaluation_question_ids": [f"q{i}" for i in range(8)]})
        plan[0]["question_id"] = "q-extra"
        with self.assertRaisesRegex(RuntimeError, "8 evaluation question ids"):
            validate_formal_plan(plan)

    def test_validator_rejects_wrong_method_repeat_distribution(self):
        plan = build_run_plan({"evaluation_question_ids": [f"q{i}" for i in range(8)]})
        performance = next(
            item
            for item in plan
            if item["lane"] == "performance"
            and item["method"] == "M0"
            and item["repeat"] == 0
        )
        performance["method"] = "M1"

        with self.assertRaisesRegex(RuntimeError, "method/repeat"):
            validate_formal_plan(plan)
