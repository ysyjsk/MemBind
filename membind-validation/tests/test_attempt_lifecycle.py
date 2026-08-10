import hashlib
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest import TestCase
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import replay_driver  # noqa: E402


class CalibrationAttemptTests(TestCase):
    def test_pre_run_input_failure_is_persisted_as_a_failed_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            active_path = artifacts / "calibration" / "arrival_interval.json"

            with patch.object(replay_driver, "ARTIFACTS", artifacts):
                with self.assertRaises(FileNotFoundError):
                    replay_driver.cmd_calibrate(
                        Namespace(
                            data="missing.json",
                            arrival_interval_ms=0,
                            attempt="calibration09",
                            authorization_checker=lambda *_args, **_kwargs: None,
                        )
                    )

            status = _read_json(
                artifacts / "calibration" / "attempts" / "calibration09.json"
            )
            self.assertEqual(status["status"], "failed")
            self.assertIn("FileNotFoundError", status["error"])
            self.assertEqual(status["run_ids"], [])
            self.assertFalse(active_path.exists())

    def test_success_freezes_only_after_all_four_runs_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            qids = [f"c{i}" for i in range(4)]
            _write_json(
                artifacts / "dataset" / "frozen_split.json",
                {"calibration_question_ids": qids},
            )
            records = {qid: {"question_id": qid} for qid in qids}

            async def successful_run(method, instance, run_id, *args, **kwargs):
                trace = artifacts / "traces" / f"{run_id}.jsonl"
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text(
                    json.dumps(
                        {
                            "source_sequence": 0,
                            "add_episode_start": 1_000_000,
                            "add_episode_end": 101_000_000,
                            "error": None,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {"run_id": run_id, "status": "success"}

            with (
                patch.object(replay_driver, "ARTIFACTS", artifacts),
                patch.object(replay_driver, "load_json_records", return_value=[]),
                patch.object(replay_driver, "records_by_question_id", return_value=records),
                patch.object(
                    replay_driver,
                    "run_one",
                    new=AsyncMock(side_effect=successful_run),
                ) as run_one,
            ):
                replay_driver.cmd_calibrate(
                    Namespace(
                        data="data.json",
                        arrival_interval_ms=0,
                        attempt="calibration07",
                        authorization_checker=lambda *_args, **_kwargs: None,
                    )
                )

            expected_run_ids = [f"calibration07_calibration_M0_{qid}" for qid in qids]
            active = _read_json(artifacts / "calibration" / "arrival_interval.json")
            status = _read_json(
                artifacts / "calibration" / "attempts" / "calibration07.json"
            )
            self.assertEqual(run_one.await_count, 4)
            self.assertEqual(active["attempt"], "calibration07")
            self.assertEqual(active["run_ids"], expected_run_ids)
            self.assertEqual(status["status"], "success")
            self.assertEqual(status["run_ids"], expected_run_ids)
            self.assertTrue(Path(active["source_artifact"]).exists())

    def test_failure_is_retained_and_does_not_replace_active_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            qids = [f"c{i}" for i in range(4)]
            _write_json(
                artifacts / "dataset" / "frozen_split.json",
                {"calibration_question_ids": qids},
            )
            active_path = artifacts / "calibration" / "arrival_interval.json"
            original_active = {"attempt": "calibration06", "DELTA_MS": 900}
            _write_json(active_path, original_active)
            records = {qid: {"question_id": qid} for qid in qids}
            calls = 0

            async def failing_run(method, instance, run_id, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("calibration model failure")
                trace = artifacts / "traces" / f"{run_id}.jsonl"
                trace.parent.mkdir(parents=True, exist_ok=True)
                trace.write_text(
                    json.dumps(
                        {
                            "source_sequence": 0,
                            "add_episode_start": 1,
                            "add_episode_end": 101,
                            "error": None,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {"run_id": run_id, "status": "success"}

            with (
                patch.object(replay_driver, "ARTIFACTS", artifacts),
                patch.object(replay_driver, "load_json_records", return_value=[]),
                patch.object(replay_driver, "records_by_question_id", return_value=records),
                patch.object(
                    replay_driver,
                    "run_one",
                    new=AsyncMock(side_effect=failing_run),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "calibration model failure"):
                    replay_driver.cmd_calibrate(
                        Namespace(
                            data="data.json",
                            arrival_interval_ms=0,
                            attempt="calibration07",
                            authorization_checker=lambda *_args, **_kwargs: None,
                        )
                    )

            status_path = (
                artifacts / "calibration" / "attempts" / "calibration07.json"
            )
            status = _read_json(status_path)
            self.assertEqual(status["status"], "failed")
            self.assertIn("calibration model failure", status["error"])
            self.assertEqual(_read_json(active_path), original_active)
            self.assertFalse(
                (
                    artifacts
                    / "calibration"
                    / "attempts"
                    / "calibration07.native_episode_latency.parquet"
                ).exists()
            )

            with (
                patch.object(replay_driver, "ARTIFACTS", artifacts),
                patch.object(replay_driver, "run_one", new=AsyncMock()) as run_one,
            ):
                with self.assertRaisesRegex(FileExistsError, "calibration07"):
                    replay_driver.cmd_calibrate(
                        Namespace(
                            data="data.json",
                            arrival_interval_ms=0,
                            attempt="calibration07",
                            authorization_checker=lambda *_args, **_kwargs: None,
                        )
                    )
            run_one.assert_not_awaited()


class FormalPlanAttemptTests(TestCase):
    def test_execution_gate_rebuilds_from_attempt_frozen_in_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            data_path = Path(tmp) / "data.json"
            data_path.write_text("[]", encoding="utf-8")
            source_sha = hashlib.sha256(b"[]").hexdigest()
            split = {
                "source_sha256": source_sha,
                "evaluation_question_ids": [f"q{i}" for i in range(8)],
            }
            _write_json(artifacts / "dataset" / "frozen_split.json", split)
            source_sha_path = artifacts / "dataset" / "source_sha256.txt"
            source_sha_path.write_text(source_sha + "\n", encoding="utf-8")
            _write_json(
                artifacts / "environment" / "manifest.json",
                {
                    "model_probe": {
                        "ok": True,
                        "models_ok": True,
                        "runtime_contract_ok": True,
                        "structured_checks": 20,
                        "structured_success": 20,
                    },
                    "embedding_probe": {"ok": True},
                },
            )
            _write_json(
                artifacts / "environment" / "integration_gate_status.json",
                {"ok": True},
            )
            _write_json(
                artifacts / "smoke" / "smoke-pass.json",
                {
                    "ok": True,
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
            _write_json(
                artifacts / "calibration" / "arrival_interval.json",
                {"attempt": "calibration07", "DELTA_MS": 100},
            )
            plan = replay_driver.build_run_plan(split, attempt="formal07")

            self.assertIsNone(
                replay_driver.validate_formal_execution_gates(
                    artifacts, data_path, plan
                )
            )

    def test_plan_writes_immutable_snapshot_and_updates_active_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            _write_json(
                artifacts / "dataset" / "frozen_split.json",
                {"evaluation_question_ids": [f"q{i}" for i in range(8)]},
            )

            with patch.object(replay_driver, "ARTIFACTS", artifacts):
                replay_driver.cmd_plan(Namespace(attempt="formal07"))

            snapshot = artifacts / "plans" / "formal07.jsonl"
            active = artifacts / "final" / "run_plan.jsonl"
            snapshot_text = snapshot.read_text(encoding="utf-8")
            self.assertEqual(active.read_text(encoding="utf-8"), snapshot_text)
            specs = [json.loads(line) for line in snapshot_text.splitlines()]
            self.assertEqual(len(specs), 64)
            self.assertEqual({item["attempt"] for item in specs}, {"formal07"})

            with patch.object(replay_driver, "ARTIFACTS", artifacts):
                with self.assertRaisesRegex(FileExistsError, "formal07"):
                    replay_driver.cmd_plan(Namespace(attempt="formal07"))
            self.assertEqual(snapshot.read_text(encoding="utf-8"), snapshot_text)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import unittest

    unittest.main()
