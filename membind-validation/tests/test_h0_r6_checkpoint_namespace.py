"""RED contract for a fresh R6/replacement-004 checkpoint namespace."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from h0_runtime import H0CheckpointStore  # noqa: E402
from test_h0_r6_recovery import _expected_admission  # noqa: E402


ATTEMPT_001 = "h0-q1-b-20260809-attempt-001"
ATTEMPT_002 = "h0-q1-b-20260809-replacement-001"
ATTEMPT_003 = "h0-q1-b-20260810-replacement-002"
ATTEMPT_004 = "h0-q1-b-20260810-replacement-003"
ATTEMPT_005 = "h0-q1-b-20260810-replacement-004"


class H0R6CheckpointNamespaceTests(TestCase):
    def test_replacement_004_starts_empty_and_does_not_merge_003_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "artifacts/h0_runs/h0/checkpoints"
            source_root = ROOT / "artifacts/h0_runs/h0/checkpoints"
            for attempt in (ATTEMPT_001, ATTEMPT_002, ATTEMPT_003, ATTEMPT_004):
                shutil.copytree(source_root / attempt, target / attempt)
            before = {
                attempt: (target / attempt / "index.json").read_bytes()
                for attempt in (ATTEMPT_001, ATTEMPT_002, ATTEMPT_003, ATTEMPT_004)
            }
            terminal = json.loads((target / ATTEMPT_004 / "index.json").read_text())
            store = H0CheckpointStore(
                root=root / "artifacts/h0_runs",
                stage_attempt_id=ATTEMPT_005,
                candidate_id="Q1",
                phase="H0-B",
                repair_admission=terminal["repair_admission"],
                infrastructure_rerun_admission=terminal["infrastructure_rerun_admission"],
                post_workload_repair_admission=terminal["post_workload_repair_admission"],
                r6_recovery_admission=_expected_admission(),
            )
            self.assertEqual(store.index["prior_matching_attempt_count"], 4)
            self.assertEqual(store.index["infrastructure_interrupted_attempt_count"], 1)
            self.assertEqual(
                store.index["historically_misclassified_infrastructure_attempt_count"],
                1,
            )
            self.assertEqual(store.index["segments"], [])
            self.assertTrue(store.index["r6_recovery_replacement"])
            reopened = H0CheckpointStore.open_existing(
                root / "artifacts/h0_runs", ATTEMPT_005
            )
            self.assertEqual(reopened.index["prior_matching_attempt_count"], 4)
            for attempt, payload in before.items():
                self.assertEqual((target / attempt / "index.json").read_bytes(), payload)


if __name__ == "__main__":
    import unittest

    unittest.main()
