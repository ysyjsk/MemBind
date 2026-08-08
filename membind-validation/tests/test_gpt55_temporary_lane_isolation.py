"""Guard the temporary GPT lane's filesystem and mainline boundaries."""

from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
TEMP = ROOT / "gpt55_temporary"
TEMP_MARKERS = ("GPT55", "gpt-5.5", "openai_chat", "LabForge", "labforge")


class GPT55TemporaryLaneIsolationTests(TestCase):
    """Keep one-off GPT artifacts out of shared mainline code and tests."""

    def test_temporary_lane_has_a_private_layout(self):
        expected = (
            TEMP / "README.md",
            TEMP / "WORKPLAN.md",
            TEMP / "scripts" / "labforge_gateway_probe.py",
            TEMP / "scripts" / "gpt55_temporary_graphiti_probe.py",
            TEMP / "scripts" / "local_embedding_adapter.py",
            TEMP / "tests" / "test_labforge_gateway_probe.py",
            TEMP / "tests" / "test_gpt55_temporary_graphiti_probe.py",
            TEMP / "tests" / "test_local_embedding_adapter.py",
            TEMP / "tests" / "test_workplan.py",
        )
        for path in expected:
            self.assertTrue(path.is_file(), f"missing isolated temporary file: {path}")

        shared_forbidden = (
            ROOT / "GPT55_TEMPORARY_WORKPLAN.md",
            ROOT / "scripts" / "labforge_gateway_probe.py",
            ROOT / "scripts" / "gpt55_temporary_graphiti_probe.py",
            ROOT / "tests" / "test_labforge_gateway_probe.py",
            ROOT / "tests" / "test_gpt55_temporary_graphiti_probe.py",
            ROOT / "tests" / "test_gpt55_temporary_workplan.py",
        )
        for path in shared_forbidden:
            self.assertFalse(path.exists(), f"temporary file leaked into shared tree: {path}")

    def test_mainline_state_and_source_have_no_temporary_lane_marker(self):
        state = (ROOT / "CURRENT_STATE.json").read_text(encoding="utf-8")
        self.assertNotIn("gpt55_temporary", state)
        forbidden_markers = (
            "GPT55",
            "gpt55",
            "gpt-5.5",
            "LabForge",
            "labforge",
            "openai_chat",
            "OpenAIChatClient",
        )
        for source in (ROOT / "src").rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                self.assertNotIn(marker, text, f"{marker!r} leaked into {source}")

    def test_shared_tests_do_not_hold_temporary_adapter_contracts(self):
        forbidden_markers = ("GPT55", "gpt55", "gpt-5.5", "LabForge", "labforge", "openai_chat")
        for test_file in (ROOT / "tests").glob("test_*.py"):
            if test_file.name == Path(__file__).name:
                continue
            text = test_file.read_text(encoding="utf-8")
            for marker in forbidden_markers:
                self.assertNotIn(marker, text, f"{marker!r} leaked into {test_file}")

    def test_temporary_markers_stay_out_of_mainline_source_and_tests(self):
        offenders: list[str] = []
        for base in (ROOT / "src", ROOT / "tests"):
            for path in base.rglob("*.py"):
                if path == Path(__file__).resolve():
                    continue
                text = path.read_text(encoding="utf-8")
                hits = [marker for marker in TEMP_MARKERS if marker in text]
                if hits:
                    offenders.append(f"{path.relative_to(ROOT)}: {', '.join(hits)}")
        self.assertEqual([], offenders)
