import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deterministic_search import (  # noqa: E402
    install_edge_search_stabilizer,
    stabilize_search_results,
)


def edge(fact: str, uuid: str) -> SimpleNamespace:
    return SimpleNamespace(
        fact=fact,
        name="RELATES_TO",
        valid_at=None,
        invalid_at=None,
        uuid=uuid,
    )


def results(edges, scores):
    return SimpleNamespace(edges=list(edges), edge_reranker_scores=list(scores))


class DeterministicSearchTests(TestCase):
    def test_stabilizer_canonicalizes_selected_candidates_and_keeps_scores_aligned(self):
        value = results(
            [edge("beta", "random-2"), edge("alpha", "random-1"), edge("gamma", "random-3")],
            [0.5, 0.5, 0.75],
        )

        stabilized = stabilize_search_results(value)

        self.assertEqual([item.fact for item in stabilized.edges], ["alpha", "beta", "gamma"])
        self.assertEqual(stabilized.edge_reranker_scores, [0.5, 0.5, 0.75])

    def test_stabilizer_is_independent_of_backend_order_and_random_uuids_for_tied_facts(self):
        first = results(
            [edge("resume", "m0-a"), edge("biometrics", "m0-b")],
            [1.0, 1.0],
        )
        second = results(
            [edge("biometrics", "m2-x"), edge("resume", "m2-y")],
            [1.0, 1.0],
        )

        stabilize_search_results(first)
        stabilize_search_results(second)

        self.assertEqual(
            [item.fact for item in first.edges],
            [item.fact for item in second.edges],
        )

    def test_stabilizer_is_independent_of_rrf_rank_assignment_drift_for_same_candidate_set(self):
        first = results(
            [edge("resume", "m0-a"), edge("profile", "m0-b"), edge("biometrics", "m0-c")],
            [1.3333333333333333, 1.1666666666666667, 0.75],
        )
        second = results(
            [edge("biometrics", "m2-x"), edge("profile", "m2-y"), edge("resume", "m2-z")],
            [1.3333333333333333, 1.1666666666666667, 0.75],
        )

        stabilize_search_results(first)
        stabilize_search_results(second)

        self.assertEqual(
            [item.fact for item in first.edges],
            ["biometrics", "profile", "resume"],
        )
        self.assertEqual(
            [item.fact for item in first.edges],
            [item.fact for item in second.edges],
        )

    def test_stabilizer_leaves_results_unchanged_when_scores_are_unavailable(self):
        value = results(
            [edge("beta", "b"), edge("alpha", "a")],
            [],
        )

        stabilize_search_results(value)

        self.assertEqual([item.fact for item in value.edges], ["beta", "alpha"])
        self.assertEqual(value.edge_reranker_scores, [])

    def test_installed_wrapper_is_idempotent_and_stabilizes_async_search(self):
        calls = []

        async def backend_search(*_args, **_kwargs):
            calls.append("search")
            return results(
                [edge("zeta", "z"), edge("alpha", "a")],
                [0.25, 0.25],
            )

        module = SimpleNamespace(search=backend_search)

        self.assertTrue(install_edge_search_stabilizer(module))
        self.assertFalse(install_edge_search_stabilizer(module))
        result = asyncio.run(module.search("ignored"))

        self.assertEqual(calls, ["search"])
        self.assertEqual([item.fact for item in result.edges], ["alpha", "zeta"])


if __name__ == "__main__":
    import unittest

    unittest.main()
