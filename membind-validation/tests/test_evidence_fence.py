import asyncio
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from semantic_compile import EvidenceFence, SemanticCompiler, UUIDLeakError  # noqa: E402


class EvidenceFenceTests(TestCase):
    def _episode(self, seq: int, body: str) -> Episode:
        return Episode(
            question_id="q",
            group_id="q",
            session_id=str(seq),
            source_sequence=seq,
            source_hash=f"h{seq}",
            reference_time=f"2026-01-0{seq + 1}",
            body=body,
        )

    def test_compile_prompt_contains_past_and_current_not_future(self):
        fence = EvidenceFence()
        episodes = [self._episode(0, "past"), self._episode(1, "current"), self._episode(2, "future")]
        for ep in episodes:
            fence.append(ep)
        compiler = SemanticCompiler(system_prompt="sys", schema={"type": "object"})
        prompt = compiler.build_user_prompt(fence, episodes[1])
        self.assertIn("past", prompt)
        self.assertIn("current", prompt)
        self.assertNotIn("future", prompt)

    def test_compiler_does_not_accept_graph_state(self):
        compiler = SemanticCompiler(system_prompt="sys", schema={"type": "object"})
        self.assertNotIn("graph", compiler.__dict__)

    def test_compiled_artifact_rejects_physical_uuid(self):
        fence = EvidenceFence()
        ep = self._episode(0, "body")
        fence.append(ep)

        async def fake_llm(_system, _user, _schema):
            return {
                "candidate_entities": [{"name": "Alice", "uuid": "123e4567-e89b-12d3-a456-426614174000"}],
                "candidate_relations": [],
            }

        compiler = SemanticCompiler(system_prompt="sys", schema={"type": "object"}, llm=fake_llm)
        with self.assertRaises(UUIDLeakError):
            asyncio.run(compiler.compile_episode(fence, ep))

