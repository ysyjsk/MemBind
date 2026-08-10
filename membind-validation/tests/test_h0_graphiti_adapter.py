"""Offline lifecycle contracts for the H0-B/C Graphiti adapter.

The fakes in this module perform no network, database, embedding, or LLM I/O.
They protect the resource and readiness ordering that must hold before the r2
artifact set can authorize a live full-history attempt.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock

import httpx
from graphiti_core.cross_encoder import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver
from graphiti_core.embedder import EmbedderClient
from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client import LLMClient
from graphiti_core.tracer import Tracer


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from h0_graphiti_adapter import (  # noqa: E402
    H0ForbiddenCrossEncoder,
    H0GraphitiHistoryFactory,
    close_h0_graphiti_history,
    evaluate_h0_retrieval,
)
from h0_embedding import H0EmbeddingAdapter  # noqa: E402
from h0_phase_runner import H0SemanticEvidenceCollector  # noqa: E402
from h0_runtime import H0AttemptLedger, H0ManifestError  # noqa: E402


class _CloseResource:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        self.events.append(f"close:{self.name}")
        if self.fail:
            raise RuntimeError(f"private {self.name} close detail")


class _Embedding(_CloseResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("embedding", events)
        self.create_count = 0
        self.readiness_count = 0
        self.config = SimpleNamespace(embedding_dim=1024)

    async def readiness(self):
        self.readiness_count += 1
        raise AssertionError("stage-level embedding readiness must not repeat per history")

    async def create(self, _input):
        self.create_count += 1
        raise AssertionError("factory readiness must not embed")

    def safe_evidence(self):
        return []


class _Driver(_CloseResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("driver", events)

        async def initialize() -> None:
            self.events.append("neo4j-index-ready")

        self._init_task = asyncio.create_task(initialize())

    async def health_check(self) -> None:
        raise AssertionError("stage-level Neo4j readiness must not repeat per history")


def _definition() -> SimpleNamespace:
    return SimpleNamespace(
        identity={
            "base_url": "http://construction.invalid/v1/",
            "served_model_id": "qwen3-32b-fp8",
        },
        candidate=SimpleNamespace(candidate_id="Q1"),
        semantic_guardrail={"schema_version": "membind.h0.semantic-guardrail.v1"},
        embedding_namespace={
            "served_model_id": "qwen3-embedding-0.6b",
            "dimension": 1024,
            "normalization": "l2",
        },
    )


def _credentials() -> dict[str, dict[str, object]]:
    return {
        "construction": {
            "base_url": "http://construction.invalid/v1/",
            "api_key": "CONSTRUCTION-SECRET",
        },
        "embedding": {
            "base_url": "http://10.87.5.247:8001/v1/",
            "model": "qwen3-embedding-0.6b",
            "api_key": "EMBEDDING-SECRET",
        },
        "neo4j": {
            "uri": "bolt://localhost:7687",
            "user": "neo4j",
            "password": "NEO4J-SECRET",
            "database": "neo4j",
        },
    }


class H0ForbiddenCrossEncoderTests(IsolatedAsyncioTestCase):
    """Unexpected reranking must fail before any provider can be contacted."""

    async def test_rank_is_fail_closed_and_persists_only_hash_count_evidence(self):
        cross = H0ForbiddenCrossEncoder()

        with self.assertRaisesRegex(H0ManifestError, "cross.encoder"):
            await cross.rank("private query", ["private passage"])

        evidence = cross.safe_evidence()
        self.assertEqual(evidence["rank_call_count"], 1)
        self.assertEqual(evidence["events"][0]["passage_count"], 1)
        encoded = str(evidence)
        self.assertNotIn("private query", encoded)
        self.assertNotIn("private passage", encoded)


class H0GraphitiNominalClientContractTests(IsolatedAsyncioTestCase):
    """Exercise the exact Pydantic client contract used by real Graphiti."""

    async def test_h0_adapters_are_accepted_by_real_graphiti_client_container(self):
        def forbid_network(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("nominal client contract test must not use network")

        embedder = H0EmbeddingAdapter(
            binding={
                "base_url": "http://embedding.invalid/v1/",
                "served_model_id": "qwen3-embedding-0.6b",
                "vllm_version": "0.26.0",
                "dimension": 1024,
                "normalization": "l2",
            },
            credentials={
                "base_url": "http://embedding.invalid/v1/",
                "model": "qwen3-embedding-0.6b",
                "api_key": "TEST-ONLY-EMBEDDING-KEY",
            },
            transport=httpx.MockTransport(forbid_network),
        )
        cross_encoder = H0ForbiddenCrossEncoder()
        try:
            clients = GraphitiClients(
                driver=Mock(spec=GraphDriver),
                llm_client=Mock(spec=LLMClient),
                embedder=embedder,
                cross_encoder=cross_encoder,
                tracer=Mock(spec=Tracer),
            )

            self.assertIs(clients.embedder, embedder)
            self.assertIs(clients.cross_encoder, cross_encoder)
            self.assertIsInstance(embedder, EmbedderClient)
            self.assertIsInstance(cross_encoder, CrossEncoderClient)
        finally:
            await embedder.close()


class H0GraphitiHistoryFactoryTests(IsolatedAsyncioTestCase):
    """Each history owns a fresh, explicitly constructed resource graph."""

    def _factory(
        self,
        events: list[str],
        *,
        graph_error: bool = False,
        embedding_error: bool = False,
    ):
        ledger = H0AttemptLedger(stage_attempt_id="h0-full-history-test")
        collector = H0SemanticEvidenceCollector()
        created: list[dict[str, object]] = []

        def completion_factory(**kwargs):
            events.append("construction-client-created")
            llm = SimpleNamespace(
                h0_ledger=kwargs["ledger"],
                h0_token_counter=_CloseResource("token-counter", events),
                client=_CloseResource("construction-http", events),
            )
            return llm

        def embedding_factory(**_kwargs):
            events.append("embedding-client-created")
            if embedding_error:
                raise RuntimeError("private embedding construction detail")
            return _Embedding(events)

        def driver_factory(**_kwargs):
            events.append("neo4j-driver-created")
            return _Driver(events)

        def graphiti_factory(**kwargs):
            events.append("graphiti-created")
            if graph_error:
                raise RuntimeError("private graph construction detail")
            created.append(kwargs)
            return SimpleNamespace(**kwargs)

        def recheck():
            events.append("authorization-rechecked")

        factory = H0GraphitiHistoryFactory(
            definition=_definition(),
            credentials=_credentials(),
            ledger=ledger,
            semantic_collector=collector,
            authorization_rechecker=recheck,
            completion_client_factory=completion_factory,
            embedding_factory=embedding_factory,
            driver_factory=driver_factory,
            graphiti_factory=graphiti_factory,
        )
        return factory, ledger, collector, created

    async def test_construction_order_explicit_clients_and_zero_repeated_readiness(self):
        events: list[str] = []
        factory, ledger, _collector, created = self._factory(events)

        graph = await factory()

        self.assertEqual(
            events,
            [
                "construction-client-created",
                "embedding-client-created",
                "neo4j-driver-created",
                "neo4j-index-ready",
                "authorization-rechecked",
                "graphiti-created",
            ],
        )
        self.assertIs(graph.llm_client.h0_ledger, ledger)
        self.assertIs(created[0]["llm_client"], graph.llm_client)
        self.assertIs(created[0]["embedder"], graph.embedder)
        self.assertIs(created[0]["cross_encoder"], graph.cross_encoder)
        self.assertIs(created[0]["graph_driver"], graph.graph_driver)
        self.assertEqual(created[0]["max_coroutines"], 8)
        self.assertEqual(graph.embedder.create_count, 0)
        self.assertEqual(graph.embedder.readiness_count, 0)
        self.assertEqual(len(ledger.trials), 0)
        self.assertEqual(graph.cross_encoder.safe_evidence()["rank_call_count"], 0)
        evidence = factory.safe_runtime_evidence()
        self.assertEqual(evidence["fresh_graph_count"], 1)
        self.assertEqual(evidence["embedding_workload_request_count"], 0)
        self.assertEqual(evidence["cross_encoder_rank_call_count"], 0)
        self.assertNotIn("SECRET", str(evidence))
        await close_h0_graphiti_history(graph)

    async def test_two_histories_receive_distinct_resources_and_close_exactly_once(self):
        events: list[str] = []
        factory, _ledger, _collector, _created = self._factory(events)

        first = await factory()
        second = await factory()

        for attribute in ("llm_client", "embedder", "cross_encoder", "graph_driver"):
            self.assertIsNot(getattr(first, attribute), getattr(second, attribute))
        first_resources = first._membind_h0_resources
        await close_h0_graphiti_history(first)
        await close_h0_graphiti_history(first)
        await close_h0_graphiti_history(second)
        self.assertEqual(first_resources.close_count, 1)
        self.assertEqual(first_resources.driver.close_count, 1)
        self.assertEqual(first_resources.embedder.close_count, 1)
        self.assertEqual(first_resources.token_counter.close_count, 1)
        self.assertEqual(first_resources.construction_client.close_count, 1)
        self.assertEqual(factory.safe_runtime_evidence()["closed_graph_count"], 2)

    async def test_partial_graph_construction_closes_every_allocated_resource(self):
        events: list[str] = []
        factory, _ledger, _collector, _created = self._factory(
            events, graph_error=True
        )

        with self.assertRaises(H0ManifestError) as raised:
            await factory()

        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(events.count("close:driver"), 1)
        self.assertEqual(events.count("close:embedding"), 1)
        self.assertEqual(events.count("close:token-counter"), 1)
        self.assertEqual(events.count("close:construction-http"), 1)

    async def test_embedding_constructor_failure_closes_already_allocated_llm_resources(self):
        events: list[str] = []
        factory, _ledger, _collector, _created = self._factory(
            events, embedding_error=True
        )

        with self.assertRaises(H0ManifestError) as raised:
            await factory()

        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(events.count("close:token-counter"), 1)
        self.assertEqual(events.count("close:construction-http"), 1)


class H0RetrievalAdapterTests(IsolatedAsyncioTestCase):
    """The frozen retrieval adapter uses basic RRF search with no reranking."""

    async def test_uses_search_not_search_while_cross_encoder_stays_zero(self):
        calls: list[str] = []
        cross = H0ForbiddenCrossEncoder()

        class Driver:
            async def execute_query(self, _query, **_kwargs):
                return SimpleNamespace(records=[])

        class Graph:
            driver = Driver()
            cross_encoder = cross

            async def search(self, _query, **kwargs):
                calls.append(f"search:{kwargs['num_results']}")
                return []

            async def search_(self, *_args, **_kwargs):
                raise AssertionError("advanced cross-encoder search is forbidden")

        result = await evaluate_h0_retrieval(
            Graph(),
            {
                "question_id": "07741c45",
                "question": "private question",
                "answer_session_ids": ["session-0"],
            },
            [SimpleNamespace(group_id="07741c45", name="episode", session_id="session-0")],
        )

        self.assertEqual(calls, ["search:10"])
        self.assertEqual(result["top_k"], 10)
        self.assertEqual(cross.safe_evidence()["rank_call_count"], 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
