"""Contract for isolating the temporary GPT diagnostic lane from mainline."""

from __future__ import annotations

import ast
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

    def test_mainline_source_neither_imports_nor_executes_temporary_lane(self):
        execution_calls = {
            "eval",
            "exec",
            "import_module",
            "run_module",
            "run_path",
            "Popen",
            "run",
            "check_call",
            "check_output",
        }
        violations: list[str] = []
        for source in (ROOT / "src").rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                    if any("gpt55_temporary" in name for name in names):
                        violations.append(f"{source.relative_to(ROOT)}:{node.lineno}:import")
                elif isinstance(node, ast.ImportFrom):
                    if "gpt55_temporary" in str(node.module or ""):
                        violations.append(f"{source.relative_to(ROOT)}:{node.lineno}:from-import")
                elif isinstance(node, ast.Call):
                    call_name = ""
                    if isinstance(node.func, ast.Name):
                        call_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        call_name = node.func.attr
                    if call_name not in execution_calls:
                        continue
                    literal_arguments = [
                        argument.value
                        for argument in node.args
                        if isinstance(argument, ast.Constant)
                        and isinstance(argument.value, str)
                    ]
                    if any("gpt55_temporary" in value for value in literal_arguments):
                        violations.append(f"{source.relative_to(ROOT)}:{node.lineno}:execute")

        self.assertEqual(
            [],
            violations,
            "mainline code may document the exclusion fence but must not import or "
            "execute temporary-lane modules",
        )
