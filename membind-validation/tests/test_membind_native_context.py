import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from graphiti_membind import native_previous_source_episodes  # noqa: E402
from semantic_compile import EvidenceFence  # noqa: E402


def episode(sequence: int) -> Episode:
    return Episode(
        question_id="q",
        group_id="q",
        session_id=f"s{sequence}",
        source_sequence=sequence,
        source_hash=f"h{sequence}",
        reference_time=f"2026-01-{sequence + 1:02d}",
        body=f"episode-{sequence}",
    )


class MemBindNativeContextTests(TestCase):
    def test_compile_context_matches_native_recent_episode_limit_and_order(self):
        fence = EvidenceFence()
        episodes = [episode(i) for i in range(6)]
        for item in episodes:
            fence.append(item)

        previous = native_previous_source_episodes(fence, episodes[5], limit=3)

        self.assertEqual([item.source_sequence for item in previous], [2, 3, 4])

    def test_compile_context_never_contains_current_or_future_episode(self):
        fence = EvidenceFence()
        episodes = [episode(i) for i in range(5)]
        for item in episodes:
            fence.append(item)

        previous = native_previous_source_episodes(fence, episodes[2], limit=3)

        self.assertEqual([item.source_sequence for item in previous], [0, 1])
        self.assertNotIn(episodes[2], previous)
        self.assertNotIn(episodes[3], previous)

    def test_limit_must_be_positive(self):
        fence = EvidenceFence()
        item = episode(0)
        fence.append(item)

        with self.assertRaisesRegex(ValueError, "positive"):
            native_previous_source_episodes(fence, item, limit=0)

    def test_context_filters_source_prefix_entries_later_than_current_reference_time(self):
        fence = EvidenceFence()
        earlier = Episode("q", "q", "s0", 0, "h0", "2026-01-01T10:00:00Z", "earlier")
        later_in_source_but_future_in_time = Episode(
            "q", "q", "s1", 1, "h1", "2026-01-01T12:00:00Z", "future-by-time"
        )
        current = Episode("q", "q", "s2", 2, "h2", "2026-01-01T11:00:00Z", "current")
        for item in (earlier, later_in_source_but_future_in_time, current):
            fence.append(item)

        previous = native_previous_source_episodes(fence, current, limit=3)

        self.assertEqual(previous, [earlier])

    def test_context_selects_latest_by_time_but_presents_selected_window_chronologically(self):
        fence = EvidenceFence()
        episodes = [
            Episode("q", "q", "s0", 0, "h0", "2026-01-01T10:00:00Z", "t10"),
            Episode("q", "q", "s1", 1, "h1", "2026-01-01T13:00:00Z", "t13"),
            Episode("q", "q", "s2", 2, "h2", "2026-01-01T11:00:00Z", "t11"),
            Episode("q", "q", "s3", 3, "h3", "2026-01-01T12:00:00Z", "t12"),
            Episode("q", "q", "s4", 4, "h4", "2026-01-01T14:00:00Z", "current"),
        ]
        for item in episodes:
            fence.append(item)

        previous = native_previous_source_episodes(fence, episodes[4], limit=3)

        self.assertEqual([item.source_sequence for item in previous], [2, 3, 1])


if __name__ == "__main__":
    import unittest

    unittest.main()
