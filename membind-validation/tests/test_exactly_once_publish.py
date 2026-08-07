import asyncio
import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from latest_state_bind import DuplicatePublishError, SourceOrderedCommitter  # noqa: E402
from semantic_compile import CompiledArtifact  # noqa: E402


class ExactlyOncePublishTests(TestCase):
    def test_each_sequence_publishes_once(self):
        published = []

        async def bind_and_commit(artifact):
            published.append(artifact.source_sequence)

        committer = SourceOrderedCommitter(total_episodes=2, bind_and_commit=bind_and_commit)
        ep0 = Episode("q", "q", "s0", 0, "h0", "t0", "zero")
        art0 = CompiledArtifact.from_episode(ep0, {}, "ph0", "rh0")
        asyncio.run(committer.submit(art0))
        with self.assertRaises(DuplicatePublishError):
            asyncio.run(committer.submit(art0))
        self.assertEqual(published, [0])

