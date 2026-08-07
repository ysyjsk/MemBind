import sys
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from canonicalize_graph import canonical_graph_hash, canonicalize_edge, canonicalize_entity, compare_canonical_graphs  # noqa: E402


class GraphCanonicalizationTests(TestCase):
    def test_entity_removes_non_semantic_fields_and_normalizes_text(self):
        entity = canonicalize_entity(
            {
                "uuid": "abc",
                "database_id": 1,
                "embedding": [1, 2],
                "created_at": "now",
                "group_id": "G",
                "name": " Alice ",
                "labels": ["Person", "Entity"],
                "summary": "Alice\n  likes tea",
                "attributes": {"age": 30, "uuid": "nested"},
            }
        )
        self.assertEqual(entity["name"], "alice")
        self.assertNotIn("uuid", entity)
        self.assertNotIn("embedding", entity)
        self.assertEqual(entity["summary"], "Alice likes tea")
        self.assertEqual(entity["attributes"], {"age": 30})

    def test_edge_normalizes_and_hashes_stably(self):
        edge = canonicalize_edge(
            {
                "uuid": "edge",
                "source_entity_key": "Alice",
                "target_entity_key": "Bob",
                "relation_type": "KNOWS",
                "fact": "Alice\nknows   Bob",
                "valid_at": "2026-01-01T00:00:00Z",
                "source_episode_sequence": 2,
            }
        )
        self.assertEqual(edge["fact"], "Alice knows Bob")
        self.assertNotIn("uuid", edge)
        h1 = canonical_graph_hash({"entities": [], "edges": [edge], "episodes": []})
        h2 = canonical_graph_hash({"edges": [edge], "entities": [], "episodes": []})
        self.assertEqual(h1, h2)

    def test_compare_requires_exact_entity_edge_and_episode_mapping(self):
        base = {
            "entities": [{"name": "alice", "group_id": "g", "labels": [], "summary": "", "attributes": {}}],
            "edges": [],
            "episodes": [{"source_sequence": 0, "source_hash": "h"}],
        }
        same = {
            "entities": [{"name": "alice", "group_id": "g", "labels": [], "summary": "", "attributes": {}}],
            "edges": [],
            "episodes": [{"source_sequence": 0, "source_hash": "h"}],
        }
        diff = {
            "entities": [{"name": "bob", "group_id": "g", "labels": [], "summary": "", "attributes": {}}],
            "edges": [],
            "episodes": [{"source_sequence": 0, "source_hash": "h"}],
        }
        self.assertTrue(compare_canonical_graphs(base, same)["canonical_graph_parity"])
        self.assertFalse(compare_canonical_graphs(base, diff)["canonical_graph_parity"])

