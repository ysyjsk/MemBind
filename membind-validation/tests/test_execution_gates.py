import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from replay_driver import (  # noqa: E402
    build_run_plan,
    validate_formal_execution_gates,
    validate_formal_plan,
)


class ExecutionGateTests(TestCase):
    def test_plan_validator_rejects_missing_or_future_capture_dependency(self):
        plan = build_run_plan({"evaluation_question_ids": [f"q{i}" for i in range(8)]})
        replay = next(item for item in plan if item["mode"] == "replay")
        replay.pop("depends_on")
        with self.assertRaisesRegex(RuntimeError, "depends_on"):
            validate_formal_plan(plan)

    def test_formal_gate_rejects_incomplete_contract_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            data_path = Path(tmp) / "data.json"
            data_path.write_text("[]", encoding="utf-8")
            _write_json(
                artifacts / "dataset" / "frozen_split.json",
                {
                    "source_sha256": hashlib.sha256(b"[]").hexdigest(),
                    "evaluation_question_ids": [f"q{i}" for i in range(8)],
                },
            )
            _write_json(
                artifacts / "environment" / "manifest.json",
                {
                    "model_probe": {
                        "models_ok": True,
                        "runtime_contract_ok": False,
                        "structured_checks": 3,
                        "structured_success": 3,
                    },
                    "embedding_probe": {"ok": True},
                },
            )
            _write_json(artifacts / "environment" / "integration_gate_status.json", {"ok": True})
            _write_json(artifacts / "smoke" / "smoke03.json", {"ok": True})
            (artifacts / "calibration").mkdir(parents=True)
            _write_json(artifacts / "calibration" / "arrival_interval.json", {"DELTA_MS": 100})
            (artifacts / "final").mkdir(parents=True)
            plan = build_run_plan({"evaluation_question_ids": [f"q{i}" for i in range(8)]})
            (artifacts / "final" / "run_plan.jsonl").write_text(
                "\n".join(json.dumps(item) for item in plan) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(RuntimeError, "20/20"):
                validate_formal_execution_gates(artifacts, data_path, plan)

    def test_formal_gate_accepts_all_frozen_success_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            data_path = Path(tmp) / "data.json"
            data_path.write_text("[]", encoding="utf-8")
            split = {"source_sha256": hashlib.sha256(b"[]").hexdigest(), "evaluation_question_ids": [f"q{i}" for i in range(8)]}
            _write_json(artifacts / "dataset" / "frozen_split.json", split)
            (artifacts / "dataset" / "source_sha256.txt").write_text(
                split["source_sha256"] + "\n", encoding="utf-8"
            )
            _write_json(
                artifacts / "environment" / "manifest.json",
                {"model_probe": {"models_ok": True, "runtime_contract_ok": True, "structured_checks": 20, "structured_success": 20}, "embedding_probe": {"ok": True}},
            )
            _write_json(artifacts / "environment" / "integration_gate_status.json", {"ok": True})
            _write_json(
                artifacts / "smoke" / "smoke03.json",
                {
                    "ok": True,
                    "question_id": "q0",
                    "m2_vs_m0": {"canonical_graph_parity": True},
                    "unexpected_prompt": False,
                    "source_order": {
                        "M2": {
                            "exactly_once": True,
                            "source_order_violation": False,
                        }
                    },
                },
            )
            _write_json(artifacts / "calibration" / "arrival_interval.json", {"DELTA_MS": 100})
            plan = build_run_plan(split)
            with self.assertRaisesRegex(RuntimeError, "run plan"):
                # A plan is valid, but the source split is deliberately incomplete.
                validate_formal_execution_gates(artifacts, data_path, plan[:-1])
            self.assertIsNone(validate_formal_execution_gates(artifacts, data_path, plan))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


if __name__ == "__main__":
    import unittest

    unittest.main()
