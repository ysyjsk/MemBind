import asyncio
import hashlib
import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deterministic_search import (  # noqa: E402
    install_edge_query_stabilizer,
    install_node_query_stabilizer,
)
from instrumentation import episode_scope  # noqa: E402
from search_forensics import (  # noqa: E402
    _neo4j_cosine,
    install_search_forensics,
    search_forensic_payload,
)


NODE_QUERY = """
MATCH (n:Entity)
WHERE n.group_id IN $group_ids
WITH n, vector.similarity.cosine(n.name_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN n.uuid AS uuid,
       n.name AS name,
       n.group_id AS group_id,
       n.created_at AS created_at,
       n.summary AS summary,
       labels(n) AS labels,
       properties(n) AS attributes
ORDER BY score DESC
LIMIT $limit
"""


EDGE_COSINE_QUERY = """
MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
WHERE e.group_id IN $group_ids
WITH DISTINCT e, n, m,
     vector.similarity.cosine(e.fact_embedding, $search_vector) AS score
WHERE score > $min_score
RETURN e.uuid AS uuid,
       e.name AS name,
       e.fact AS fact,
       e.valid_at AS valid_at,
       e.invalid_at AS invalid_at,
       n.name AS source_name,
       m.name AS target_name
ORDER BY score DESC
LIMIT $limit
"""


EDGE_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryRelationships(
    "edge_name_and_fact", $query, {limit: $limit}
)
YIELD relationship AS rel, score
MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity)
WHERE e.group_id IN $group_ids
WITH e, score, n, m
RETURN e.uuid AS uuid,
       e.name AS name,
       e.fact AS fact,
       e.valid_at AS valid_at,
       e.invalid_at AS invalid_at,
       n.name AS source_name,
       m.name AS target_name
ORDER BY score DESC
LIMIT $limit
"""


def _vector_hash(vector):
    payload = b"".join(struct.pack("!d", float(value)) for value in vector)
    return hashlib.sha256(payload).hexdigest()


class SearchForensicsTests(TestCase):
    def test_neo4j_cosine_matches_vector_similarity_cosine_semantics(self):
        self.assertAlmostEqual(_neo4j_cosine([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(_neo4j_cosine([1.0, 0.0], [0.0, 1.0]), 0.5)
        self.assertAlmostEqual(_neo4j_cosine([1.0, 0.0], [-1.0, 0.0]), 0.0)

    def test_node_cosine_capture_matches_query_actually_sent_to_backend(self):
        backend_calls = []
        candidate_records = [
            {
                "name": "SDG",
                "summary": "Sustainable development goal",
                "labels": ["Entity"],
            },
            {
                "name": "ESG",
                "summary": "Environmental, social, and governance",
                "labels": ["Entity"],
            },
        ]

        async def execute_query(query, *args, **kwargs):
            backend_calls.append((query, args, kwargs))
            return candidate_records, None, None

        driver = SimpleNamespace(execute_query=execute_query)
        install_edge_query_stabilizer(driver)
        install_node_query_stabilizer(driver)
        self.assertTrue(install_search_forensics(driver))
        self.assertFalse(install_search_forensics(driver))

        vector = [1.0, 0.0]

        async def exercise():
            with episode_scope("run-a", 8):
                return await driver.execute_query(
                    NODE_QUERY,
                    "positional",
                    search_vector=vector,
                    group_ids=["group-a"],
                    limit=15,
                    min_score=0.6,
                    routing_="r",
                )

        result = asyncio.run(exercise())
        payload = search_forensic_payload(driver)

        self.assertEqual(result[0], candidate_records)
        self.assertEqual(backend_calls[0][1], ("positional",))
        self.assertEqual(backend_calls[0][2]["routing_"], "r")
        self.assertIn(
            "ORDER BY score DESC, toLower(coalesce(n.name, '')) ASC",
            backend_calls[0][0],
        )
        self.assertEqual(len(payload["query_events"]), 1)
        event = payload["query_events"][0]
        self.assertEqual(event["episode_key"], ["run-a", 8])
        self.assertEqual(event["normalized_query"], backend_calls[0][0])
        self.assertEqual(event["parameters"]["search_vector_sha256"], _vector_hash(vector))
        self.assertEqual(event["parameters"]["search_vector_dimension"], 2)
        self.assertNotIn("search_vector", event["parameters"])
        self.assertEqual(
            [candidate["name"] for candidate in event["backend_candidates"]],
            ["SDG", "ESG"],
        )

    def test_target_source_snapshot_is_sanitized_and_used_for_score_diagnostics(self):
        snapshot_records = [
            {
                "name": "SDG",
                "summary": "Sustainable development goal",
                "labels": ["Entity"],
                "name_embedding": [1.0, 0.0],
            },
            {
                "name": "ESG",
                "summary": "Environmental, social, and governance",
                "labels": ["Entity"],
                "name_embedding": [0.8, 0.6],
            },
        ]
        candidate_records = [snapshot_records[0], snapshot_records[1]]
        backend_calls = []

        async def execute_query(query, **kwargs):
            backend_calls.append((query, kwargs))
            if "n.name_embedding AS name_embedding" in query:
                return snapshot_records, None, None
            return candidate_records, None, None

        driver = SimpleNamespace(execute_query=execute_query)
        install_edge_query_stabilizer(driver)
        install_node_query_stabilizer(driver)
        install_search_forensics(driver, snapshot_source_sequences={8})

        async def exercise():
            with episode_scope("run-b", 8):
                await driver.execute_query(
                    NODE_QUERY,
                    search_vector=[1.0, 0.0],
                    group_ids=["group-b"],
                    limit=1,
                    min_score=0.6,
                )
                await driver.execute_query(
                    NODE_QUERY,
                    search_vector=[0.8, 0.6],
                    group_ids=["group-b"],
                    limit=1,
                    min_score=0.6,
                )

        asyncio.run(exercise())
        payload = search_forensic_payload(driver)

        self.assertEqual(len(payload["source_states"]), 1)
        state = payload["source_states"][0]
        self.assertEqual(state["phase"], "before_node_resolution")
        self.assertEqual(state["source_sequence"], 8)
        self.assertEqual(state["group_id"], "group-b")
        self.assertEqual([entity["name"] for entity in state["entities"]], ["ESG", "SDG"])
        self.assertEqual(state["entities"][1]["embedding_sha256"], _vector_hash([1.0, 0.0]))
        self.assertNotIn("name_embedding", json.dumps(state))
        self.assertEqual(len(state["logical_graph_hash"]), 64)
        self.assertEqual(len(payload["query_events"]), 2)
        self.assertEqual(payload["query_events"][0]["python_ranked"][0]["name"], "SDG")
        self.assertEqual(payload["query_events"][0]["python_ranked"][0]["score"], 1.0)
        self.assertEqual(sum("n.name_embedding AS name_embedding" in call[0] for call in backend_calls), 1)

    def test_non_target_queries_are_forwarded_without_forensic_events(self):
        calls = []

        async def execute_query(query, **kwargs):
            calls.append((query, kwargs))
            return [], None, None

        driver = SimpleNamespace(execute_query=execute_query)
        install_search_forensics(driver)

        result = asyncio.run(driver.execute_query("MATCH (n) RETURN n", routing_="r"))

        self.assertEqual(result, ([], None, None))
        self.assertEqual(calls, [("MATCH (n) RETURN n", {"routing_": "r"})])
        self.assertEqual(
            search_forensic_payload(driver),
            {"query_events": [], "source_states": []},
        )

    def test_edge_cosine_and_fulltext_capture_include_rrf_source_membership(self):
        edge_state = [
            {
                "uuid": "uuid-a",
                "name": "ADVISES",
                "fact": "Racita advises Jacob to keep praying.",
                "valid_at": None,
                "invalid_at": None,
                "source_name": "Racita",
                "target_name": "Jacob",
                "fact_embedding": [1.0, 0.0],
            },
            {
                "uuid": "uuid-b",
                "name": "USES",
                "fact": "The fund considers SDGs.",
                "valid_at": None,
                "invalid_at": None,
                "source_name": "fund",
                "target_name": "SDG",
                "fact_embedding": [0.8, 0.6],
            },
        ]
        cosine_records = [dict(edge_state[0])]
        cosine_records[0].pop("fact_embedding")
        fulltext_records = [dict(edge_state[1])]
        fulltext_records[0].pop("fact_embedding")
        backend_calls = []

        async def execute_query(cypher_query, *args, **kwargs):
            backend_calls.append((cypher_query, args, kwargs))
            if "e.fact_embedding AS fact_embedding" in cypher_query:
                return edge_state, None, None
            if "vector.similarity.cosine(e.fact_embedding" in cypher_query:
                return cosine_records, None, None
            return fulltext_records, None, None

        driver = SimpleNamespace(execute_query=execute_query)
        install_search_forensics(driver, snapshot_source_sequences={7})

        async def exercise():
            with episode_scope("run-edge", 7):
                await driver.execute_query(
                    EDGE_COSINE_QUERY,
                    search_vector=[1.0, 0.0],
                    group_ids=["group-edge"],
                    limit=10,
                    min_score=0.6,
                )
                await driver.execute_query(
                    EDGE_FULLTEXT_QUERY,
                    query="Racita advises Jacob",
                    group_ids=["group-edge"],
                    limit=10,
                )

        asyncio.run(exercise())
        payload = search_forensic_payload(driver)

        self.assertEqual(len(payload["query_events"]), 2)
        self.assertEqual(
            [event["kind"] for event in payload["query_events"]],
            ["edge_cosine_search", "edge_fulltext_search"],
        )
        cosine_event, fulltext_event = payload["query_events"]
        self.assertEqual(
            cosine_event["rrf_source_membership"][0]["fact"],
            "Racita advises Jacob to keep praying.",
        )
        self.assertEqual(
            fulltext_event["rrf_source_membership"][0]["fact"],
            "The fund considers SDGs.",
        )
        self.assertEqual(
            cosine_event["parameters"]["search_vector_dimension"], 2
        )
        self.assertEqual(
            fulltext_event["parameters"]["query_length"], len("Racita advises Jacob")
        )
        self.assertNotIn("\"fact_embedding\":", json.dumps(payload))
        self.assertNotIn("\"embedding\":", json.dumps(payload))
        self.assertEqual(len(payload["source_states"]), 1)
        self.assertEqual(payload["source_states"][0]["phase"], "before_edge_resolution")
        self.assertEqual(payload["source_states"][0]["edge_count"], 2)
        self.assertEqual(len(backend_calls), 3)


if __name__ == "__main__":
    import unittest

    unittest.main()
