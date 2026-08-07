import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from semantic_compile import EvidenceFence, SemanticCompiler  # noqa: E402


class NoFutureEvidenceTests(TestCase):
    def test_all_prompts_exclude_future_sessions_and_dates(self):
        fence = EvidenceFence()
        episodes = [
            Episode("q", "q", "s0", 0, "h0", "2026-01-01", "session-0"),
            Episode("q", "q", "s1", 1, "h1", "2026-01-02", "session-1"),
            Episode("q", "q", "s2", 2, "h2", "2026-01-03", "session-2"),
        ]
        for ep in episodes:
            fence.append(ep)
        compiler = SemanticCompiler(system_prompt="sys", schema={})

        for ep in episodes:
            prompt = compiler.build_user_prompt(fence, ep)
            for future in episodes[ep.source_sequence + 1 :]:
                self.assertNotIn(future.body, prompt)
                self.assertNotIn(future.reference_time, prompt)

