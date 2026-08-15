"""Controlled offline execution of the pinned Graphiti semantic path.

The fixture is deliberately small and is not an alternative Graphiti runtime.
It constructs real Graphiti 0.29.3 objects and invokes the installed semantic
functions plus ``Graphiti._process_episode_data``.  Only the external
nondeterminism boundaries (LLM, embedding, candidate search, clock, and the
transaction I/O) are controlled, so this module can qualify call order and
commit behavior without a model service or Neo4j.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver, GraphDriverSession, GraphProvider
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.nodes import EpisodeType, EpisodicNode
from graphiti_core.tracer import NoOpTracer
from pydantic import BaseModel

from .s5_graphiti_mstar_semantics import (
    GraphitiEpisodeInput,
    GraphitiBindObservation,
    S5GraphitiMStarSemanticError,
    S5GraphitiMStarSemanticRuntime,
)
from .s5_graphiti_semantic_binding import S5GraphitiSemanticBinding, load_graphiti_semantic_binding


class ControlledGraphitiFixtureError(RuntimeError):
    """Sanitized controlled-fixture failure."""


class _WorksAt(BaseModel):
    """A minimal edge type used only to verify Native default routing."""

    pass


class _ControlledLLM(LLMClient):
    def __init__(self, edge_fact: str | None = None) -> None:
        super().__init__(LLMConfig(model="controlled", small_model="controlled"))
        self.calls: list[str] = []
        self.edge_fact = edge_fact

    async def _generate_response(
        self,
        messages: list[Any],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        del messages, max_tokens, model_size
        name = response_model.__name__ if response_model is not None else ""
        self.calls.append(name)
        if name == "ExtractedEntities":
            entities = [
                {"name": "Alice", "entity_type_id": 0, "episode_indices": [0]}
            ]
            if self.edge_fact is not None:
                entities.append(
                    {"name": "Acme", "entity_type_id": 0, "episode_indices": [0]}
                )
            return {
                "extracted_entities": entities
            }
        if name == "ExtractedEdges":
            if self.edge_fact is not None:
                return {
                    "edges": [
                        {
                            "source_entity_name": "Alice",
                            "target_entity_name": "Acme",
                            "relation_type": "WorksAt",
                            "fact": self.edge_fact,
                            "valid_at": "2026-01-01T00:00:00Z",
                            "episode_indices": [0],
                        }
                    ]
                }
            return {"edges": []}
        if name == "SummarizedEntities":
            summaries = [{"name": "Alice", "summary": "Alice summary"}]
            if self.edge_fact is not None:
                summaries.append({"name": "Acme", "summary": "Acme summary"})
            return {"summaries": summaries}
        if name == "NodeResolutions":
            return {"entity_resolutions": [{"id": 0, "name": "Alice", "duplicate_candidate_id": -1}]}
        raise ControlledGraphitiFixtureError(f"UNEXPECTED_LLM_RESPONSE_MODEL:{name}")


class _ControlledEmbedder(EmbedderClient):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def create(self, input_data: Any) -> list[float]:
        if isinstance(input_data, str):
            values = (input_data,)
        else:
            values = tuple(str(item) for item in input_data)
        self.calls.append(values)
        return [1.0, 0.0]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        values = tuple(input_data_list)
        self.calls.append(values)
        return [[1.0, 0.0] for _ in input_data_list]


class _ControlledCrossEncoder(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 1.0) for passage in passages]


class _SearchInterface:
    async def node_similarity_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def edge_similarity_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def edge_fulltext_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def node_fulltext_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class _Transaction:
    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.fixture = fixture

    async def run(self, query: str, **kwargs: Any) -> None:
        self.fixture.events.append({"event": "tx_run", "query_sha256": str(hash(query)), "keys": sorted(kwargs)})


class _Session(GraphDriverSession):
    provider = GraphProvider.NEO4J

    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.fixture = fixture

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def run(self, query: str, **kwargs: Any) -> None:
        return await _Transaction(self.fixture).run(query, **kwargs)

    async def close(self) -> None:
        self.fixture.events.append({"event": "session_close"})

    async def execute_write(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        self.fixture.transaction_attempts += 1
        tx = _Transaction(self.fixture)
        try:
            result = await func(tx, *args, **kwargs)
            if self.fixture.fail_transaction:
                raise RuntimeError("controlled transaction failure")
        except Exception:
            self.fixture.events.append({"event": "commit_failed"})
            raise
        self.fixture.events.append({"event": "commit_completed"})
        return result


class _GraphOperations:
    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.fixture = fixture

    async def episodic_node_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture.events.append({"event": "episodic_node_save_bulk", "count": len(rows)})

    async def node_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture.events.append({"event": "node_save_bulk", "count": len(rows)})

    async def episodic_edge_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture.events.append({"event": "episodic_edge_save_bulk", "count": len(rows)})

    async def edge_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture.events.append({"event": "edge_save_bulk", "count": len(rows)})

    async def get_between_nodes(self, _driver: Any, _source: str, _target: str) -> list[Any]:
        return []


class _GraphDriver(GraphDriver):
    provider = GraphProvider.NEO4J
    fulltext_syntax = ""
    default_group_id = "default-db"

    def __init__(self, fixture: "ControlledGraphitiFixture", database: str) -> None:
        self.fixture = fixture
        self._database = database
        self.search_interface = _SearchInterface()
        self.graph_operations_interface = _GraphOperations(fixture)
        self.clone_calls: list[str] = []

    async def execute_query(self, cypher_query_: str, **kwargs: Any) -> tuple[list[Any], Any, Any]:
        del cypher_query_, kwargs
        return [], None, None

    def session(self, database: str | None = None) -> GraphDriverSession:
        del database
        return _Session(self.fixture)

    def close(self) -> None:
        return None

    async def delete_all_indexes(self) -> None:
        return None

    async def build_indices_and_constraints(self, delete_existing: bool = False) -> None:
        del delete_existing
        return None

    def clone(self, database: str) -> "_GraphDriver":
        self.clone_calls.append(database)
        cloned = _GraphDriver(self.fixture, database)
        cloned.clone_calls = self.clone_calls
        return cloned


@dataclass(frozen=True)
class ControlledRunResult:
    observation: GraphitiBindObservation
    call_order: tuple[str, ...]
    commit_completed: bool
    publication_allowed: bool
    transaction_attempts: int
    edge_type_map: dict[tuple[str, str], list[str]]
    routed_database: str


@dataclass
class ControlledGraphitiFixture:
    """One fully isolated Graphiti execution fixture."""

    group_id: str = "controlled-db"
    configured_database: str = "controlled-db"
    edge_types: tuple[str, ...] = ()
    edge_fact: str | None = None
    fail_transaction: bool = False
    malformed_commit_result: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    transaction_attempts: int = 0

    def __post_init__(self) -> None:
        self.llm = _ControlledLLM(self.edge_fact)
        self.embedder = _ControlledEmbedder()
        self.driver = _GraphDriver(self, self.configured_database)
        self.graphiti = Graphiti.__new__(Graphiti)
        self.graphiti.driver = self.driver
        self.graphiti.llm_client = self.llm
        self.graphiti.embedder = self.embedder
        self.graphiti.store_raw_episode_content = True
        self.graphiti.max_coroutines = None
        self.graphiti.tracer = NoOpTracer()
        self.graphiti.clients = GraphitiClients(
            driver=self.driver,
            llm_client=self.llm,
            embedder=self.embedder,
            cross_encoder=_ControlledCrossEncoder(),
            tracer=NoOpTracer(),
        )
        self.binding = load_graphiti_semantic_binding()
        if self.malformed_commit_result:
            original_binding = self.binding

            async def malformed_process(*_args: Any, **_kwargs: Any) -> object:
                return {"malformed": True}

            self.binding = S5GraphitiSemanticBinding(
                extract_nodes=original_binding.extract_nodes,
                resolve_extracted_nodes=original_binding.resolve_extracted_nodes,
                extract_attributes_from_nodes=original_binding.extract_attributes_from_nodes,
                extract_edges=original_binding.extract_edges,
                resolve_extracted_edges=original_binding.resolve_extracted_edges,
                resolve_edge_pointers=original_binding.resolve_edge_pointers,
                process_episode_data=malformed_process,
                loader_verified=original_binding.loader_verified,
            )
        self.call_order: list[str] = []
        self.runtime = S5GraphitiMStarSemanticRuntime(
            graphiti=self.graphiti,
            binding=self.binding,
            latest_state_retriever=lambda _source: asyncio.sleep(0, result=[]),
            controlled_provider_scope=self._provider_scope,
            call_observer=self.call_order.append,
            require_native_commit_shape=True,
        )

    @contextmanager
    def _provider_scope(self, _providers: object):
        self.events.append({"event": "provider_scope_enter"})
        try:
            yield
        finally:
            self.events.append({"event": "provider_scope_exit"})

    def _source(self) -> GraphitiEpisodeInput:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        episode = EpisodicNode(
            uuid="controlled-episode",
            name="controlled",
            content="Alice works at Acme.",
            source=EpisodeType.text,
            source_description="controlled fixture",
            group_id=self.group_id,
            valid_at=now,
        )
        edge_models: dict[str, type[BaseModel]] = {
            "WorksAt": _WorksAt for _ in self.edge_types
        }
        return GraphitiEpisodeInput(
            episode_node=episode,
            previous_episodes=[],
            group_id=self.group_id,
            edge_types=edge_models or None,
            edge_type_map=None,
        )

    async def run_episode(self) -> ControlledRunResult:
        self.call_order.clear()
        source = self._source()
        logical_time_ns = 1_767_225_600_000_000_000
        providers = object()
        try:
            prepared = await self.runtime.prepare(source, logical_time_ns, providers)
            observation = await self.runtime.bind(
                prepared, logical_time_ns, 0, (), providers
            )
        except S5GraphitiMStarSemanticError as error:
            if not any(event.get("event") == "commit_failed" for event in self.events):
                self.events.append({"event": "commit_failed"})
            code = str(error)
            if code == "process_episode_data_failed":
                raise ControlledGraphitiFixtureError("COMMIT_FAILED") from None
            if code == "native_commit_result_shape_invalid":
                raise ControlledGraphitiFixtureError("COMMIT_RESULT_INVALID") from None
            raise ControlledGraphitiFixtureError(code) from None
        self.events.append({"event": "publication"})
        commit_completed = any(event.get("event") == "commit_completed" for event in self.events)
        return ControlledRunResult(
            observation=observation,
            call_order=tuple(self.call_order),
            commit_completed=commit_completed,
            publication_allowed=commit_completed,
            transaction_attempts=self.transaction_attempts,
            edge_type_map=deepcopy(self.runtime.last_edge_type_map),
            routed_database=getattr(self.graphiti.driver, "_database", ""),
        )


def build_controlled_graphiti_fixture(**kwargs: Any) -> ControlledGraphitiFixture:
    """Build a no-network fixture around real pinned Graphiti symbols."""

    unknown = set(kwargs) - {
        "group_id",
        "configured_database",
        "edge_types",
        "edge_fact",
        "fail_transaction",
        "malformed_commit_result",
    }
    if unknown:
        raise ControlledGraphitiFixtureError("UNKNOWN_FIXTURE_OPTION")
    return ControlledGraphitiFixture(**kwargs)


__all__ = [
    "ControlledGraphitiFixture",
    "ControlledGraphitiFixtureError",
    "ControlledRunResult",
    "build_controlled_graphiti_fixture",
]
