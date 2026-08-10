import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dataset import Episode  # noqa: E402
from live_outputs import evaluate_retrieval, export_canonical_graph  # noqa: E402


def episode(sequence: int, session_id: str) -> Episode:
    return Episode(
        question_id="q",
        group_id="q",
        session_id=session_id,
        source_sequence=sequence,
        source_hash=f"hash-{sequence}",
        reference_time=f"2026-01-{sequence + 1:02d}",
        body=f"body-{sequence}",
    )


class Result:
    def __init__(self, records):
        self.records = records


class FakeDriver:
    def __init__(self):
        self.queries = []

    async def execute_query(self, query, params=None):
        self.queries.append((query, params))
        if "MATCH (n:Entity)" in query and "RELATES_TO" not in query:
            return Result(
                [
                    {
                        "uuid": "entity-a",
                        "group_id": "q",
                        "name": "Alice",
                        "labels": ["Entity", "Person"],
                        "summary": " Alice likes tea ",
                        "attributes": {"uuid": "entity-a", "age": 30, "name_embedding": [1.0]},
                    },
                    {
                        "uuid": "entity-b",
                        "group_id": "q",
                        "name": "Adidas",
                        "labels": ["Entity"],
                        "summary": "",
                        "attributes": {"uuid": "entity-b"},
                    },
                ]
            )
        if "RELATES_TO" in query:
            return Result(
                [
                    {
                        "uuid": "edge-1",
                        "group_id": "q",
                        "source_name": "Alice",
                        "target_name": "Adidas",
                        "relation_type": "WORKS_AT",
                        "fact": "Alice works at Adidas",
                        "valid_at": None,
                        "invalid_at": None,
                        "expired_at": None,
                        "episode_uuids": ["ep-0"],
                        "attributes": {"uuid": "edge-1", "fact_embedding": [2.0]},
                    }
                ]
            )
        if "MATCH (ep:Episodic)" in query:
            return Result(
                [
                    {"uuid": "ep-0", "name": "q::episode::0000"},
                    {"uuid": "ep-1", "name": "q::episode::0001"},
                ]
            )
        raise AssertionError(query)


class FakeGraphiti:
    def __init__(self):
        self.driver = FakeDriver()
        self.search_calls = []

    async def search(self, query, group_ids, num_results):
        self.search_calls.append((query, group_ids, num_results))
        return [
            SimpleNamespace(
                uuid="edge-1",
                fact="Alice works at Adidas",
                episodes=["ep-1", "ep-0"],
            )
        ]


class LiveOutputTests(IsolatedAsyncioTestCase):
    async def test_export_queries_live_graph_and_maps_persisted_episodes(self):
        graphiti = FakeGraphiti()
        episodes = [episode(0, "session-a"), episode(1, "session-b")]

        exported = await export_canonical_graph(graphiti, episodes, "q")

        self.assertEqual({item["name"] for item in exported["entities"]}, {"alice", "adidas"})
        alice = next(item for item in exported["entities"] if item["name"] == "alice")
        self.assertEqual(alice["attributes"], {"age": 30})
        self.assertEqual(exported["edges"][0]["source_episode_sequence"], 0)
        self.assertEqual(
            exported["episodes"],
            [
                {"source_sequence": 0, "source_hash": "hash-0", "session_id": "session-a"},
                {"source_sequence": 1, "source_hash": "hash-1", "session_id": "session-b"},
            ],
        )
        self.assertEqual(len(exported["canonical_graph_hash"]), 64)

    async def test_retrieval_uses_default_top10_and_maps_edge_episode_uuids(self):
        graphiti = FakeGraphiti()
        episodes = [episode(0, "session-a"), episode(1, "session-b")]

        result = await evaluate_retrieval(
            graphiti,
            {"question": "Where does Alice work?", "answer_session_ids": ["session-b"]},
            episodes,
            reference_episode_ids=["session-b", "session-a"],
        )

        self.assertEqual(graphiti.search_calls, [("Where does Alice work?", ["q"], 10)])
        self.assertEqual(result["retrieved_episode_ids"], ["session-b", "session-a"])
        self.assertEqual(result["metrics"]["evidence_recall_at_10"], 1.0)
        self.assertEqual(result["metrics"]["episode_set_overlap_with_m0"], 1.0)

    async def test_retrieval_uses_first_ten_edges_and_first_ten_unique_session_ids(self):
        episodes = [episode(index, f"session-{index}") for index in range(12)]

        class ProtocolDriver(FakeDriver):
            async def execute_query(self, query, params=None):
                if "MATCH (ep:Episodic)" in query:
                    return Result(
                        [
                            {"uuid": f"ep-{index}", "name": item.name}
                            for index, item in enumerate(episodes)
                        ]
                    )
                return await super().execute_query(query, params=params)

        class ProtocolGraphiti(FakeGraphiti):
            def __init__(self):
                super().__init__()
                self.driver = ProtocolDriver()

            async def search(self, query, group_ids, num_results):
                self.search_calls.append((query, group_ids, num_results))
                return [
                    SimpleNamespace(
                        uuid="edge-0",
                        fact="first edge has more than ten source sessions",
                        episodes=[f"ep-{index}" for index in range(11)],
                    ),
                    *[
                        SimpleNamespace(
                            uuid=f"edge-{index}",
                            fact=f"duplicate edge {index}",
                            episodes=["ep-0"],
                        )
                        for index in range(1, 10)
                    ],
                    SimpleNamespace(
                        uuid="edge-10",
                        fact="the eleventh edge must be ignored",
                        episodes=["ep-11"],
                    ),
                ]

        graphiti = ProtocolGraphiti()
        result = await evaluate_retrieval(
            graphiti,
            {
                "question": "Which sessions support the answer?",
                "answer_session_ids": ["session-11"],
            },
            episodes,
        )

        self.assertEqual(
            result["retrieved_episode_ids"],
            [f"session-{index}" for index in range(10)],
        )
        self.assertEqual(len(result["results"]), 10)
        self.assertEqual(result["metrics"]["evidence_recall_at_10"], 0.0)


if __name__ == "__main__":
    import unittest

    unittest.main()
