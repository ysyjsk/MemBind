import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deterministic_search import (  # noqa: E402
    install_edge_query_stabilizer,
    install_edge_search_stabilizer,
    install_node_query_stabilizer,
    install_node_resolution_stabilizer,
    stabilize_edge_search_query,
    stabilize_node_candidates,
    stabilize_node_search_query,
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


def node(name: str, uuid: str, *, summary: str = "", labels=None, attributes=None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        uuid=uuid,
        summary=summary,
        labels=list(labels or ["Entity"]),
        attributes=dict(attributes or {}),
    )


def results(edges, scores):
    return SimpleNamespace(edges=list(edges), edge_reranker_scores=list(scores))


class DeterministicSearchTests(TestCase):
    def test_node_search_ties_are_broken_by_logical_content_before_limit(self):
        query = """
        MATCH (n:Entity)
        WITH n, vector.similarity.cosine(n.name_embedding, $search_vector) AS score
        WHERE score > $min_score
        RETURN n.name AS name
        ORDER BY score DESC
        LIMIT $limit
        """

        stabilized = stabilize_node_search_query(query)

        self.assertIn("toLower(coalesce(n.name, '')) ASC", stabilized)
        self.assertIn(
            "ORDER BY score DESC, toLower(coalesce(n.name, '')) ASC",
            stabilized,
        )
        self.assertIn("toLower(coalesce(n.summary, '')) ASC", stabilized)
        self.assertIn("coalesce(n.summary, '') ASC", stabilized)
        self.assertIn("labels(n) ASC", stabilized)
        self.assertNotIn("toString(labels(n))", stabilized)
        self.assertLess(
            stabilized.index("coalesce(n.name, '') ASC"),
            stabilized.index("LIMIT $limit"),
        )
        self.assertEqual(stabilize_node_search_query(stabilized), stabilized)

    def test_node_search_tie_break_matches_graphiti_fulltext_query_shape(self):
        query = """
        CALL db.index.fulltext.queryNodes(
            "node_name_and_summary", $query, {limit: $limit}
        )
        YIELD node AS n, score
        WITH n, score
        ORDER BY score DESC
        LIMIT $limit
        RETURN n.name AS name
        """

        stabilized = stabilize_node_search_query(query)

        self.assertIn(
            "ORDER BY score DESC, toLower(coalesce(n.name, '')) ASC",
            stabilized,
        )
        self.assertIn("{limit: $limit}", stabilized)

    def test_node_query_stabilizer_is_idempotent_and_preserves_call_contract(self):
        calls = []

        async def execute_query(query, *args, **kwargs):
            calls.append((query, args, kwargs))
            return ["result"]

        driver = SimpleNamespace(execute_query=execute_query)
        query = (
            "MATCH (n:Entity) RETURN n.name AS name "
            "ORDER BY score DESC LIMIT $limit"
        )

        self.assertTrue(install_node_query_stabilizer(driver))
        self.assertFalse(install_node_query_stabilizer(driver))
        result = asyncio.run(driver.execute_query(query, "arg", limit=15, routing_="r"))

        self.assertEqual(result, ["result"])
        self.assertEqual(calls[0][1], ("arg",))
        self.assertEqual(calls[0][2], {"limit": 15, "routing_": "r"})
        self.assertIn(
            "ORDER BY score DESC, toLower(coalesce(n.name, '')) ASC",
            calls[0][0],
        )

    def test_node_query_stabilizer_does_not_change_edge_score_queries(self):
        query = (
            "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) "
            "RETURN e.fact ORDER BY score DESC LIMIT $limit"
        )

        self.assertEqual(stabilize_node_search_query(query), query)

    def test_node_and_edge_query_stabilizers_compose_without_cross_rewrite(self):
        calls = []

        async def execute_query(query, **kwargs):
            calls.append((query, kwargs))
            return query

        driver = SimpleNamespace(execute_query=execute_query)
        install_edge_query_stabilizer(driver)
        install_node_query_stabilizer(driver)
        node_query = (
            "MATCH (n:Entity) RETURN n.name "
            "ORDER BY score DESC LIMIT $limit"
        )
        edge_query = (
            "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) RETURN e.fact "
            "ORDER BY score DESC LIMIT $limit"
        )

        asyncio.run(driver.execute_query(node_query, limit=15))
        asyncio.run(driver.execute_query(edge_query, limit=10))

        self.assertEqual(len(calls), 2)
        self.assertIn("coalesce(n.name, '') ASC", calls[0][0])
        self.assertNotIn("e.fact ASC", calls[0][0])
        self.assertIn("e.fact ASC", calls[1][0])
        self.assertNotIn("toLower(coalesce(n.name, ''))", calls[1][0])

    def test_edge_search_ties_are_broken_by_logical_content_before_limit(self):
        query = """
        MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
        WITH e, n, m, vector.similarity.cosine(e.fact_embedding, $vector) AS score
        RETURN e.fact AS fact
        ORDER BY score DESC
        LIMIT $limit
        """

        stabilized = stabilize_edge_search_query(query)

        self.assertIn("ORDER BY score DESC, e.fact ASC", stabilized)
        self.assertIn("e.name ASC", stabilized)
        self.assertIn("n.name ASC", stabilized)
        self.assertIn("m.name ASC", stabilized)
        self.assertLess(stabilized.index("e.fact ASC"), stabilized.index("LIMIT $limit"))
        self.assertEqual(stabilize_edge_search_query(stabilized), stabilized)

    def test_edge_search_tie_break_matches_graphiti_fulltext_query_shape(self):
        query = """
        CALL db.index.fulltext.queryRelationships(
            "edge_name_and_fact", $query, {limit: $limit}
        )
        YIELD relationship AS rel, score
        MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity)
        WITH e, score, n, m
        RETURN e.fact AS fact
        ORDER BY score DESC
        LIMIT $limit
        """

        stabilized = stabilize_edge_search_query(query)

        self.assertIn("ORDER BY score DESC, e.fact ASC", stabilized)
        self.assertIn("{limit: $limit}", stabilized)

    def test_edge_query_stabilizer_is_idempotent_and_preserves_call_contract(self):
        calls = []

        async def execute_query(query, *args, **kwargs):
            calls.append((query, args, kwargs))
            return ["result"]

        driver = SimpleNamespace(execute_query=execute_query)
        query = (
            "MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity) "
            "RETURN e.fact AS fact ORDER BY score DESC LIMIT $limit"
        )

        self.assertTrue(install_edge_query_stabilizer(driver))
        self.assertFalse(install_edge_query_stabilizer(driver))
        result = asyncio.run(driver.execute_query(query, "arg", limit=10, routing_="r"))

        self.assertEqual(result, ["result"])
        self.assertEqual(calls[0][1], ("arg",))
        self.assertEqual(calls[0][2], {"limit": 10, "routing_": "r"})
        self.assertIn("ORDER BY score DESC, e.fact ASC", calls[0][0])

    def test_edge_query_stabilizer_does_not_change_non_edge_score_queries(self):
        query = "MATCH (n:Entity) RETURN n ORDER BY score DESC LIMIT $limit"

        self.assertEqual(stabilize_edge_search_query(query), query)

    def test_node_candidates_are_ordered_by_logical_content_not_uuid(self):
        first = [
            node("USER", "m0-z", summary="same person"),
            node("italki", "m0-a", summary="language exchange"),
        ]
        second = [
            node("italki", "m2-z", summary="language exchange"),
            node("USER", "m2-a", summary="same person"),
        ]

        stabilize_node_candidates(first)
        stabilize_node_candidates(second)

        self.assertEqual([item.name for item in first], ["italki", "USER"])
        self.assertEqual(
            [(item.name, item.summary) for item in first],
            [(item.name, item.summary) for item in second],
        )

    def test_node_stabilizer_wrapper_preserves_arguments_and_is_idempotent(self):
        calls = []

        def backend_merge(candidates, override):
            calls.append((candidates, override))
            return list(candidates) + list(override or [])

        module = SimpleNamespace(_merge_candidate_nodes=backend_merge)

        self.assertTrue(install_node_resolution_stabilizer(module))
        self.assertFalse(install_node_resolution_stabilizer(module))
        override = [node("zeta", "override-z")]
        result = module._merge_candidate_nodes(
            [node("USER", "m0-z"), node("italki", "m0-a")], override
        )

        self.assertEqual(len(calls), 1)
        self.assertIs(calls[0][1], override)
        self.assertEqual([item.name for item in result], ["italki", "USER", "zeta"])

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
