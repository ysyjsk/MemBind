import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from integration_gate import graphiti_integration_smoke  # noqa: E402


class GraphOps:
    async def clear_data(self, driver):
        driver.events.append("clear")
        driver.node_count = 0


class Driver:
    def __init__(self):
        self.events = []
        self.node_count = 4
        self.graph_ops = GraphOps()

    async def execute_query(self, query):
        self.events.append("count")
        return SimpleNamespace(records=[{"node_count": self.node_count}])


class Graphiti:
    def __init__(self, search_results=None):
        self.driver = Driver()
        self.search_results = (
            [SimpleNamespace(fact="Alice works at Adidas")]
            if search_results is None
            else search_results
        )

    async def build_indices_and_constraints(self):
        self.driver.events.append("indexes")

    async def add_episode(self, **kwargs):
        self.driver.events.append("add")
        self.driver.node_count = 3
        return SimpleNamespace(
            episode=SimpleNamespace(uuid="episode"),
            nodes=[SimpleNamespace()],
            edges=[SimpleNamespace()],
        )

    async def search(self, query, group_ids, num_results):
        self.driver.events.append("search")
        return self.search_results


class IntegrationGateTests(IsolatedAsyncioTestCase):
    async def test_smoke_adds_searches_and_leaves_database_empty(self):
        graphiti = Graphiti()

        result = await graphiti_integration_smoke(graphiti)

        self.assertTrue(result["ok"])
        self.assertEqual(result["search_count"], 1)
        self.assertEqual(result["node_count_after_clear"], 0)
        self.assertEqual(
            graphiti.driver.events,
            ["clear", "indexes", "count", "add", "search", "clear", "count", "count"],
        )

    async def test_smoke_fails_when_search_cannot_retrieve_warmup_fact(self):
        graphiti = Graphiti(search_results=[])

        with self.assertRaisesRegex(RuntimeError, "search returned no results"):
            await graphiti_integration_smoke(graphiti)

        self.assertEqual(graphiti.driver.node_count, 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
