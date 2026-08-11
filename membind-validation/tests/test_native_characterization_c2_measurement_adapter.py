"""TDD contracts for the C2-only hard-telemetry measurement adapter."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from native_characterization_c2_measurement import (  # noqa: E402
    C2MeasurementError,
    collect_graph_prefix_size,
    install_c2_measurement_adapter,
)
from native_characterization_tracing import TraceRecorder  # noqa: E402


class _Embedder:
    def __init__(self, calls: list[tuple[object, ...]]) -> None:
        self.calls = calls

    async def create_batch(self, values):
        self.calls.append(("embed", tuple(values)))
        return [[0.1, 0.2] for _ in values]


def _fixture():
    calls: list[tuple[object, ...]] = []
    embedder = _Embedder(calls)
    clients = SimpleNamespace(embedder=embedder, driver=object(), llm_client=object())
    node_module = SimpleNamespace()
    edge_module = SimpleNamespace()
    phase_module = SimpleNamespace()

    async def node_similarity_search(
        driver, vector, search_filter, group_ids, limit, min_score
    ):
        calls.append(
            (
                "node-search",
                driver,
                tuple(vector),
                search_filter,
                tuple(group_ids),
                limit,
                min_score,
            )
        )
        return [SimpleNamespace(uuid="node-a"), SimpleNamespace(uuid="node-b")]

    async def semantic_candidate_search(bound_clients, extracted_nodes):
        vectors = await bound_clients.embedder.create_batch(
            [node.name for node in extracted_nodes]
        )
        return [
            await node_module.node_similarity_search(
                bound_clients.driver,
                vector,
                object(),
                [node.group_id],
                15,
                0.6,
            )
            for node, vector in zip(extracted_nodes, vectors, strict=True)
        ]

    async def create_entity_edge_embeddings(bound_embedder, edges):
        calls.append(("edge-embedding-function", len(edges)))
        if edges:
            await bound_embedder.create_batch([edge.fact for edge in edges])

    async def search(bound_clients, query, **kwargs):
        calls.append(("edge-search", query, tuple(sorted(kwargs))))
        return SimpleNamespace(
            edges=[
                SimpleNamespace(uuid="edge-a", fact="candidate a"),
                SimpleNamespace(uuid="edge-b", fact="candidate b"),
            ]
        )

    def resolve_edge_contradictions(resolved_edge, candidates):
        calls.append(("edge-invalidation", len(candidates)))
        return list(candidates[:1])

    async def resolve_extracted_edge(
        llm_client,
        extracted_edge,
        related_edges,
        existing_edges,
        episode,
        edge_types,
    ):
        invalidated = edge_module.resolve_edge_contradictions(
            extracted_edge, existing_edges
        )
        return extracted_edge, invalidated, []

    async def resolve_extracted_edges(
        bound_clients,
        extracted_edges,
        episode,
        entities,
        edge_types,
        edge_type_map,
    ):
        await edge_module.create_entity_edge_embeddings(
            bound_clients.embedder, extracted_edges
        )
        related = [
            await edge_module.search(bound_clients, edge.fact, search_filter="dedupe")
            for edge in extracted_edges
        ]
        invalidation = [
            await edge_module.search(
                bound_clients, edge.fact, search_filter="invalidation"
            )
            for edge in extracted_edges
        ]
        results = [
            await edge_module.resolve_extracted_edge(
                bound_clients.llm_client,
                edge,
                related_result.edges,
                invalidation_result.edges,
                episode,
                {},
            )
            for edge, related_result, invalidation_result in zip(
                extracted_edges, related, invalidation, strict=True
            )
        ]
        resolved = [result[0] for result in results]
        invalidated = [item for result in results for item in result[1]]
        await asyncio.gather(
            edge_module.create_entity_edge_embeddings(bound_clients.embedder, resolved),
            edge_module.create_entity_edge_embeddings(
                bound_clients.embedder, invalidated
            ),
        )
        return resolved, invalidated, resolved

    node_module.node_similarity_search = node_similarity_search
    node_module._semantic_candidate_search = semantic_candidate_search
    edge_module.create_entity_edge_embeddings = create_entity_edge_embeddings
    edge_module.search = search
    edge_module.resolve_edge_contradictions = resolve_edge_contradictions
    edge_module.resolve_extracted_edge = resolve_extracted_edge
    edge_module.resolve_extracted_edges = resolve_extracted_edges
    phase_module.resolve_extracted_edges = resolve_extracted_edges
    graphiti = SimpleNamespace(embedder=embedder)
    return graphiti, clients, phase_module, node_module, edge_module, calls


class NativeCharacterizationC2MeasurementAdapterTests(TestCase):
    def test_adapter_records_candidate_embedding_search_counts_and_invalidation(self):
        (
            graphiti,
            clients,
            phase_module,
            node_module,
            edge_module,
            calls,
        ) = _fixture()
        recorder = TraceRecorder()
        original_node_search = node_module.node_similarity_search
        original_edge_resolver = phase_module.resolve_extracted_edges
        handle = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        nodes = [SimpleNamespace(name="Alice", group_id="group-a")]
        edges = [SimpleNamespace(fact="Alice knows Bob")]

        async def exercise():
            with recorder.episode_scope("run", "history:0", 0):
                node_candidates = await node_module._semantic_candidate_search(
                    clients, nodes
                )
                edge_result = await phase_module.resolve_extracted_edges(
                    clients, edges, object(), [], {}, {}
                )
                return node_candidates, edge_result

        node_candidates, edge_result = asyncio.run(exercise())
        handle.restore()

        self.assertEqual(len(node_candidates[0]), 2)
        self.assertEqual(len(edge_result[1]), 1)
        self.assertIn(("edge-invalidation", 2), calls)
        phases = [record.phase for record in recorder.records]
        self.assertIn("candidate-embedding", phases)
        self.assertIn("candidate-search", phases)
        self.assertIn("invalidation-update", phases)
        self.assertEqual(phases.count("candidate-embedding"), 2)
        candidate_counts = [
            record.metadata["candidate_count"]
            for record in recorder.records
            if record.phase == "candidate-search"
        ]
        self.assertEqual(candidate_counts, [2, 2, 2])
        invalidation = [
            record
            for record in recorder.records
            if record.phase == "invalidation-update"
        ]
        self.assertEqual(invalidation[0].metadata["invalidation_candidate_count"], 2)
        self.assertEqual(invalidation[0].metadata["invalidated_count"], 1)
        self.assertIs(node_module.node_similarity_search, original_node_search)
        self.assertIs(phase_module.resolve_extracted_edges, original_edge_resolver)

    def test_installation_is_idempotent_and_rejects_another_recorder(self):
        graphiti, _clients, phase_module, node_module, edge_module, _calls = _fixture()
        recorder = TraceRecorder()
        first = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        second = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        self.assertIs(first, second)
        with self.assertRaisesRegex(RuntimeError, "another recorder"):
            install_c2_measurement_adapter(
                graphiti,
                TraceRecorder(),
                phase_module=phase_module,
                node_module=node_module,
                edge_module=edge_module,
            )
        first.restore()

        replacement = install_c2_measurement_adapter(
            graphiti,
            TraceRecorder(),
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )
        replacement.restore()

    def test_candidate_context_resets_after_same_exception_object(self):
        graphiti, clients, phase_module, node_module, edge_module, _calls = _fixture()
        failure = RuntimeError("PRIVATE_FAILURE_MESSAGE")

        async def failing_search(bound_clients, _nodes):
            await bound_clients.embedder.create_batch(["PRIVATE_CANDIDATE_TEXT"])
            raise failure

        node_module._semantic_candidate_search = failing_search
        recorder = TraceRecorder()
        handle = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )

        async def exercise():
            with recorder.episode_scope("run", "history:0", 0):
                try:
                    await node_module._semantic_candidate_search(clients, [object()])
                except RuntimeError as observed:
                    self.assertIs(observed, failure)
                await graphiti.embedder.create_batch(["PRIVATE_OUTSIDE_TEXT"])

        asyncio.run(exercise())
        handle.restore()

        candidates = [
            record
            for record in recorder.records
            if record.phase == "candidate-embedding"
        ]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].status, "ok")
        searches = [
            record for record in recorder.records if record.phase == "candidate-search"
        ]
        self.assertEqual(len(searches), 1)
        self.assertEqual(searches[0].status, "error")
        persisted = str([record.to_dict() for record in recorder.records])
        self.assertNotIn("PRIVATE", persisted)

    def test_empty_candidate_paths_emit_explicit_zero_work_spans(self):
        graphiti, clients, phase_module, node_module, edge_module, _calls = _fixture()
        recorder = TraceRecorder()
        handle = install_c2_measurement_adapter(
            graphiti,
            recorder,
            phase_module=phase_module,
            node_module=node_module,
            edge_module=edge_module,
        )

        async def exercise():
            with recorder.episode_scope("run", "history:0", 0):
                await node_module._semantic_candidate_search(clients, [])
                await phase_module.resolve_extracted_edges(
                    clients, [], object(), [], {}, {}
                )

        asyncio.run(exercise())
        handle.restore()

        by_phase = {
            phase: [record for record in recorder.records if record.phase == phase]
            for phase in (
                "candidate-embedding",
                "candidate-search",
                "invalidation-update",
            )
        }
        self.assertTrue(all(by_phase.values()))
        self.assertTrue(
            all(
                record.metadata.get("candidate_count", 0) == 0
                for record in by_phase["candidate-search"]
            )
        )

    def test_graph_prefix_size_is_group_scoped_and_strictly_validated(self):
        calls: list[tuple[str, dict[str, object]]] = []

        class Driver:
            async def execute_query(self, query, **kwargs):
                calls.append((query, kwargs))
                return SimpleNamespace(
                    records=[{"node_count": 7, "relationship_count": 9}]
                )

        namespace = "nc-e1e2-0000000000000000"
        observed = asyncio.run(collect_graph_prefix_size(Driver(), namespace))
        self.assertEqual(
            observed,
            {
                "graph_prefix_node_count": 7,
                "graph_prefix_relationship_count": 9,
            },
        )
        self.assertEqual(
            [kwargs for _query, kwargs in calls],
            [
                {"params": {"group_id": namespace}},
            ],
        )

        class InvalidDriver:
            async def execute_query(self, _query, **_kwargs):
                return SimpleNamespace(
                    records=[{"node_count": -1, "relationship_count": 0}]
                )

        with self.assertRaisesRegex(C2MeasurementError, "graph_prefix"):
            asyncio.run(
                collect_graph_prefix_size(InvalidDriver(), namespace)
            )


if __name__ == "__main__":
    import unittest

    unittest.main()
