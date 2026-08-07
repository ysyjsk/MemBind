import asyncio
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from latest_state_bind import SourceOrderedCommitter  # noqa: E402
from semantic_compile import CompiledArtifact  # noqa: E402


class SourceOrderCommitTests(TestCase):
    def test_commit_waits_for_missing_prior_sequence(self):
        events = []

        async def bind_and_commit(artifact):
            events.append(("commit", artifact.source_sequence))

        committer = SourceOrderedCommitter(total_episodes=3, bind_and_commit=bind_and_commit)
        ep1 = Episode("q", "q", "s1", 1, "h1", "t1", "one")
        ep0 = Episode("q", "q", "s0", 0, "h0", "t0", "zero")
        asyncio.run(committer.submit(CompiledArtifact.from_episode(ep1, {}, "ph1", "rh1")))
        self.assertEqual(events, [])
        asyncio.run(committer.submit(CompiledArtifact.from_episode(ep0, {}, "ph0", "rh0")))
        self.assertEqual(events, [("commit", 0), ("commit", 1)])

