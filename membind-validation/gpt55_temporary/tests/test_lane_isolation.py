"""Contract for isolating the temporary GPT-5.5 diagnostic lane."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
LANE = ROOT / "gpt55_temporary"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class GPT55TemporaryLaneIsolationTests(TestCase):
    """Protect the temporary lane from leaking into shared or mainline state."""

    def test_temporary_assets_live_under_gpt55_temporary(self):
        expected = [
            LANE / "scripts" / "gpt55_temporary_graphiti_probe.py",
            LANE / "scripts" / "labforge_gateway_probe.py",
            LANE / "scripts" / "local_embedding_adapter.py",
            LANE / "tests" / "test_gpt55_temporary_graphiti_probe.py",
            LANE / "tests" / "test_local_embedding_adapter.py",
            LANE / "tests" / "test_workplan.py",
            LANE / "tests" / "test_labforge_gateway_probe.py",
            LANE / "README.md",
            LANE / "WORKPLAN.md",
            LANE / "tests" / "test_workplan.py",
        ]
        missing = [str(path.relative_to(ROOT)) for path in expected if not path.is_file()]
        self.assertEqual(
            [],
            missing,
            "temporary GPT assets must be migrated below gpt55_temporary/: "
            + ", ".join(missing),
        )

    def test_shared_scripts_and_tests_have_no_temporary_lane_residue(self):
        residue: list[str] = []
        for shared_root in (ROOT / "scripts", ROOT / "tests"):
            for path in shared_root.iterdir():
                if not path.is_file():
                    continue
                lowered = path.name.casefold()
                # This one root-level guard is intentionally allowed to check
                # the boundary; all implementation tests belong in the lane.
                if path.name == "test_gpt55_temporary_lane_isolation.py":
                    continue
                if "gpt55" in lowered or "labforge" in lowered:
                    residue.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            [],
            sorted(residue),
            "shared scripts/tests must not retain GPT-5.5 temporary files",
        )

    def test_mainline_state_is_not_rehomed_or_rewritten(self):
        current_state = ROOT / "CURRENT_STATE.json"
        self.assertTrue(current_state.is_file(), "mainline CURRENT_STATE.json must remain")
        self.assertFalse(
            (ROOT / "src" / "CURRENT_STATE.json").exists(),
            "temporary lane must not create src/CURRENT_STATE.json",
        )
        self.assertFalse(
            (LANE / "CURRENT_STATE.json").exists(),
            "temporary lane must not own or rewrite CURRENT_STATE.json",
        )

        before = _sha256(current_state)
        payload = json.loads(current_state.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertEqual(before, _sha256(current_state))

    def test_mainline_source_has_no_temporary_lane_marker(self):
        for source in (ROOT / "src").rglob("*.py"):
            self.assertNotIn("gpt55_temporary", source.read_text(encoding="utf-8"))
