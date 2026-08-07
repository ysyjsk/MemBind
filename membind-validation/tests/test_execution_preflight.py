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


class ExecutionPreflightTests(TestCase):
    def test_execute_rejects_failed_formal_gate_before_any_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = Path(tmp) / "artifacts"
            artifacts.mkdir()
            data_path = Path(tmp) / "data.json"
            data_path.write_text("[]", encoding="utf-8")
            args = Namespace(
                data=str(data_path),
                max_runs=None,
                stop_on_failure=False,
                smoke_attempt=None,
            )
            with (
                patch.object(replay_driver, "ARTIFACTS", artifacts),
                patch.object(replay_driver, "_formal_plan", return_value=[]),
                patch.object(
                    replay_driver,
                    "validate_formal_execution_gates",
                    side_effect=RuntimeError("formal gate failed: blocked"),
                ),
                patch.object(replay_driver, "run_experiment", new_callable=AsyncMock) as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "blocked"):
                    replay_driver.cmd_execute(args)
            run.assert_not_awaited()

    def test_running_status_is_preserved_as_interrupted_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.json"
            path.write_text(
                json.dumps({"run_id": "run-1", "status": "running", "started_at": "t0"}),
                encoding="utf-8",
            )

            changed = replay_driver.mark_interrupted_status(path)
            status = json.loads(path.read_text(encoding="utf-8"))

            self.assertTrue(changed)
            self.assertEqual(status["status"], "failed")
            self.assertTrue(status["interrupted"])
            self.assertIn("interrupted", status["error"])
