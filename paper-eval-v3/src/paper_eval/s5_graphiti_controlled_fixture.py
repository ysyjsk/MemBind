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
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from graphiti_core.tracer import NoOpTracer
from pydantic import BaseModel

from .s5_graphiti_mstar_semantics import (
    GraphitiEpisodeInput,
    GraphitiBindObservation,
    S5GraphitiMStarSemanticError,
    S5GraphitiMStarSemanticRuntime,
)
from .s5_graphiti_semantic_binding import S5GraphitiSemanticBinding, load_graphiti_semantic_binding
from .artifacts import canonical_bytes, payload_sha256


class ControlledGraphitiFixtureError(RuntimeError):
    """Sanitized controlled-fixture failure."""


class _ProviderLedger:
    """Allowlist and observation ledger for controlled nondeterminism."""

    _ALLOWED = {
        "provider_scope",
        "logical_time",
        "initial_state",
        "llm",
        "embedding",
        "candidate_query",
    }

    def __init__(self) -> None:
        self.consumed: list[str] = []
        self.unexpected: list[str] = []

    def consume(self, provider: str) -> None:
        if provider not in self._ALLOWED:
            self.unexpected.append(provider)
            return
        self.consumed.append(provider)

    def reset(self) -> None:
        self.consumed.clear()
        self.unexpected.clear()


@dataclass(frozen=True)
class ControlledGraphitiProviders:
    """Explicit values allowed to cross the controlled Graphiti scope."""

    llm_responses: dict[str, Any]
    embedding_vector: tuple[float, ...] = (1.0, 0.0)
    logical_time_ns: int = 1_767_225_600_000_000_000
    initial_state: tuple[Any, ...] = ()
    candidate_nodes: tuple[Any, ...] = ()
    candidate_node_sets: tuple[tuple[Any, ...], ...] = ()
    invalidation_edges: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.llm_responses, dict):
            raise ControlledGraphitiFixtureError("LLM_RESPONSES_INVALID")
        if (
            isinstance(self.logical_time_ns, bool)
            or not isinstance(self.logical_time_ns, int)
            or self.logical_time_ns < 0
        ):
            raise ControlledGraphitiFixtureError("LOGICAL_TIME_INVALID")


def _default_llm_responses(
    edge_fact: str | None,
    invalidation_candidate: bool,
    duplicate_entity: bool = False,
    conflicting_candidate_projections: bool = False,
) -> dict[str, Any]:
    entities = [{"name": "Alice", "entity_type_id": 0, "episode_indices": [0]}]
    summaries = [{"name": "Alice", "summary": "Alice summary"}]
    if duplicate_entity:
        entities.append({"name": "Alice", "entity_type_id": 0, "episode_indices": [0]})
        summaries.append({"name": "Alice", "summary": "Alice summary"})
    if conflicting_candidate_projections:
        entities.append({"name": "Alicia", "entity_type_id": 0, "episode_indices": [0]})
        summaries.append({"name": "Alicia", "summary": "Alicia summary"})
    if edge_fact is not None:
        entities.append({"name": "Acme", "entity_type_id": 0, "episode_indices": [0]})
        summaries.append({"name": "Acme", "summary": "Acme summary"})
    edges: list[dict[str, Any]] = []
    if edge_fact is not None:
        edges = [
            {
                "source_entity_name": "Alice",
                "target_entity_name": "Acme",
                "relation_type": "WorksAt",
                "fact": edge_fact,
                "valid_at": "2026-01-01T00:00:00Z",
                "episode_indices": [0],
            }
        ]
    return {
        "ExtractedEntities": {"extracted_entities": entities},
        "ExtractedEdges": {"edges": edges},
        "SummarizedEntities": {"summaries": summaries},
        "NodeResolutions": {
            "entity_resolutions": [
                {"id": 0, "name": "Alice", "duplicate_candidate_id": -1}
            ]
        },
        "EdgeDuplicate": {
            "duplicate_facts": [],
            "contradicted_facts": [0] if invalidation_candidate else [],
        },
    }


class _WorksAt(BaseModel):
    """A minimal edge type used only to verify Native default routing."""

    pass


class _ControlledLLM(LLMClient):
    def __init__(
        self,
        fixture: "ControlledGraphitiFixture",
    ) -> None:
        super().__init__(LLMConfig(model="controlled", small_model="controlled"))
        self.calls: list[str] = []
        self.request_evidence: list[dict[str, str]] = []
        self.fixture = fixture

    async def _generate_response(
        self,
        messages: list[Any],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 16384,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        del max_tokens, model_size
        self.fixture.provider_ledger.consume("llm")
        name = response_model.__name__ if response_model is not None else ""
        self.calls.append(name)
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("LLM_RESPONSE_SCOPE_MISSING")
        parties = self.fixture.prepare_rendezvous_parties
        if parties and self.fixture.prepare_rendezvous_arrivals < parties:
            self.fixture.prepare_rendezvous_arrivals += 1
            if self.fixture.prepare_rendezvous_arrivals == parties:
                self.fixture.prepare_rendezvous_event.set()
            await self.fixture.prepare_rendezvous_event.wait()
        response = providers.llm_responses.get(name)
        if response is None:
            raise ControlledGraphitiFixtureError(f"LLM_RESPONSE_MISSING:{name}")
        self.request_evidence.append(
            {
                "request_identity": payload_sha256(
                    {"ordinal": len(self.request_evidence), "response_model": name}
                ),
                "prompt_sha256": payload_sha256(
                    [message.model_dump(mode="json") for message in messages]
                ),
                "model_schema_sha256": payload_sha256(
                    None
                    if response_model is None
                    else response_model.model_json_schema()
                ),
                "response_sha256": payload_sha256(response),
            }
        )
        return deepcopy(response)


class _ControlledEmbedder(EmbedderClient):
    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fixture = fixture

    async def create(self, input_data: Any) -> list[float]:
        self.fixture.provider_ledger.consume("embedding")
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("EMBEDDING_SCOPE_MISSING")
        if isinstance(input_data, str):
            values = (input_data,)
        else:
            values = tuple(str(item) for item in input_data)
        self.calls.append(values)
        return list(providers.embedding_vector)

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        self.fixture.provider_ledger.consume("embedding")
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("EMBEDDING_SCOPE_MISSING")
        values = tuple(input_data_list)
        self.calls.append(values)
        return [list(providers.embedding_vector) for _ in input_data_list]


class _ControlledCrossEncoder(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 1.0) for passage in passages]


class _SearchInterface:
    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.fixture = fixture

    async def node_similarity_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        self.fixture.provider_ledger.consume("candidate_query")
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("CANDIDATE_SCOPE_MISSING")
        if providers.candidate_node_sets:
            index = self.fixture.candidate_query_index
            self.fixture.candidate_query_index += 1
            if index >= len(providers.candidate_node_sets):
                raise ControlledGraphitiFixtureError("CANDIDATE_SET_EXHAUSTED")
            return list(providers.candidate_node_sets[index])
        return list(providers.candidate_nodes)

    async def edge_similarity_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        self.fixture.provider_ledger.consume("candidate_query")
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("CANDIDATE_SCOPE_MISSING")
        search_filter = _args[4] if len(_args) > 4 else None
        if providers.invalidation_edges and getattr(search_filter, "edge_uuids", None) is None:
            return list(providers.invalidation_edges)
        return []

    async def edge_fulltext_search(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        self.fixture.provider_ledger.consume("candidate_query")
        providers = self.fixture.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("CANDIDATE_SCOPE_MISSING")
        search_filter = _args[2] if len(_args) > 2 else None
        if providers.invalidation_edges and getattr(search_filter, "edge_uuids", None) is None:
            return list(providers.invalidation_edges)
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
        result: Any = None
        for attempt in range(1, 3 if self.fixture.retry_transaction_once else 2):
            self.fixture.transaction_attempts += 1
            tx = _Transaction(self.fixture)
            try:
                result = await func(tx, *args, **kwargs)
                if self.fixture.fail_transaction:
                    raise RuntimeError("controlled transaction failure")
                if (
                    self.fixture.mutate_retry_payload
                    and self.fixture.retry_transaction_once
                    and attempt == 2
                    and self.fixture.durable_records["nodes"]
                ):
                    first_row = next(iter(self.fixture.durable_records["nodes"].values()))
                    if isinstance(first_row, dict):
                        first_row["name"] = "retry-mutated"
                self.fixture.retry_commit_projections.append(
                    self.fixture.durable_projection()
                )
                if self.fixture.retry_transaction_once and attempt == 1:
                    self.fixture.events.append({"event": "transaction_retry"})
                    raise RuntimeError("controlled transient transaction failure")
            except Exception:
                if self.fixture.retry_transaction_once and attempt == 1:
                    continue
                self.fixture.events.append({"event": "commit_failed"})
                raise
            break
        self.fixture.events.append({"event": "commit_completed"})
        return result


class _GraphOperations:
    def __init__(self, fixture: "ControlledGraphitiFixture") -> None:
        self.fixture = fixture

    async def episodic_node_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture._record_durable("episodes", rows)
        self.fixture.events.append({"event": "episodic_node_save_bulk", "count": len(rows)})

    async def node_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture._record_durable("nodes", rows)
        self.fixture.events.append(
            {
                "event": "node_save_bulk",
                "count": len(rows),
                "node_uuids": [row.get("uuid") for row in rows if isinstance(row, dict)],
            }
        )

    async def episodic_edge_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture._record_durable("episodic_edges", rows)
        self.fixture.events.append({"event": "episodic_edge_save_bulk", "count": len(rows)})

    async def edge_save_bulk(self, _executor: Any, _driver: Any, _tx: Any, rows: Any) -> None:
        self.fixture._record_durable("edges", rows)
        self.fixture.events.append({"event": "edge_save_bulk", "count": len(rows)})

    async def edge_get_between_nodes(
        self, _cls: Any, _driver: Any, _source: str, _target: str
    ) -> list[Any]:
        return []


class _GraphDriver(GraphDriver):
    provider = GraphProvider.NEO4J
    fulltext_syntax = ""
    default_group_id = "default-db"

    def __init__(self, fixture: "ControlledGraphitiFixture", database: str) -> None:
        self.fixture = fixture
        self._database = database
        self.search_interface = _SearchInterface(fixture)
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
    retry_idempotence_proven: bool = False


@dataclass
class ControlledGraphitiFixture:
    """One fully isolated Graphiti execution fixture."""

    group_id: str = "controlled-db"
    configured_database: str = "controlled-db"
    edge_types: tuple[str, ...] = ()
    edge_fact: str | None = None
    canonical_candidate: bool = False
    duplicate_entity: bool = False
    conflicting_candidate_projections: bool = False
    invalidation_candidate: bool = False
    fail_transaction: bool = False
    malformed_commit_result: bool = False
    retry_transaction_once: bool = False
    idempotent_retry: bool = False
    mutate_retry_payload: bool = False
    missing_llm_response: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    transaction_attempts: int = 0

    def __post_init__(self) -> None:
        self.provider_ledger = _ProviderLedger()
        self.active_providers: ControlledGraphitiProviders | None = None
        self.durable_records: dict[str, dict[str, Any]] = {
            "episodes": {},
            "nodes": {},
            "edges": {},
            "episodic_edges": {},
        }
        self.retry_commit_projections: list[dict[str, tuple[str, ...]]] = []
        self.llm = _ControlledLLM(self)
        self.embedder = _ControlledEmbedder(self)
        self.prepare_rendezvous_parties = 0
        self.prepare_rendezvous_arrivals = 0
        self.prepare_rendezvous_event = asyncio.Event()
        self.candidate_query_index = 0
        self.candidate_nodes: list[EntityNode] = []
        self.candidate_node_sets: list[list[EntityNode]] = []
        if self.canonical_candidate:
            self.candidate_nodes.append(
                EntityNode(
                    uuid="canonical-alice",
                    name="Alice",
                    group_id=self.group_id,
                    summary="Canonical Alice",
                )
            )
        if self.conflicting_candidate_projections:
            self.candidate_node_sets = [
                [
                    EntityNode(
                        uuid="canonical-conflict",
                        name="Alice",
                        group_id=self.group_id,
                        summary="Projection one",
                    )
                ],
                [
                    EntityNode(
                        uuid="canonical-conflict",
                        name="Alicia",
                        group_id=self.group_id,
                        summary="Projection two",
                    )
                ],
            ]
        self.invalidation_edges: list[EntityEdge] = []
        if self.invalidation_candidate:
            self.invalidation_edges.append(
                EntityEdge(
                    uuid="old-edge",
                    group_id=self.group_id,
                    source_node_uuid="old-source",
                    target_node_uuid="old-target",
                    created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    name="WorksAt",
                    fact="Alice previously worked at Beta.",
                    episodes=[],
                    valid_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                )
            )
        self.providers = self._make_providers()
        self.driver = _GraphDriver(self, self.configured_database)
        self._initial_candidate_nodes = deepcopy(self.candidate_nodes)
        self._initial_candidate_node_sets = deepcopy(self.candidate_node_sets)
        self._initial_invalidation_edges = deepcopy(self.invalidation_edges)
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
            latest_state_retriever=self._retrieve_latest_state,
            controlled_provider_scope=self._provider_scope,
            call_observer=self.call_order.append,
            require_native_commit_shape=True,
        )

    async def _retrieve_latest_state(self, _source: GraphitiEpisodeInput) -> list[Any]:
        self.provider_ledger.consume("initial_state")
        providers = self.active_providers
        if providers is None:
            raise ControlledGraphitiFixtureError("INITIAL_STATE_SCOPE_MISSING")
        return list(providers.initial_state)

    def _make_providers(self) -> ControlledGraphitiProviders:
        responses = _default_llm_responses(
            self.edge_fact,
            self.invalidation_candidate,
            self.duplicate_entity,
            self.conflicting_candidate_projections,
        )
        if self.missing_llm_response is not None:
            responses.pop(self.missing_llm_response, None)
        return ControlledGraphitiProviders(
            llm_responses=responses,
            candidate_nodes=tuple(self.candidate_nodes),
            candidate_node_sets=tuple(
                tuple(nodes) for nodes in self.candidate_node_sets
            ),
            invalidation_edges=tuple(self.invalidation_edges),
        )

    def _record_durable(self, kind: str, rows: Any) -> None:
        if kind not in self.durable_records:
            raise ControlledGraphitiFixtureError("DURABLE_KIND_INVALID")
        for index, row in enumerate(rows):
            key = row.get("uuid") if isinstance(row, dict) else getattr(row, "uuid", None)
            if not isinstance(key, str) or not key:
                key = f"row-{index}"
            self.durable_records[kind][key] = deepcopy(row)

    def durable_projection(self) -> dict[str, tuple[str, ...]]:
        def normalize(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, dict):
                return {str(key): normalize(child) for key, child in value.items()}
            if isinstance(value, (list, tuple)):
                return [normalize(child) for child in value]
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            return repr(value)

        return {
            kind: tuple(
                sorted(payload_sha256(normalize(row)) for row in rows.values())
            )
            for kind, rows in self.durable_records.items()
        }

    def canonical_logical_state(self) -> dict[str, list[dict[str, Any]]]:
        """Project durable rows without runtime UUID or wall-clock identity."""

        def value(row: Any, field_name: str, default: Any = None) -> Any:
            if isinstance(row, dict):
                return row.get(field_name, default)
            return getattr(row, field_name, default)

        def timestamp(row: Any, field_name: str) -> str | None:
            item = value(row, field_name)
            if item is None:
                return None
            if not isinstance(item, datetime):
                raise ControlledGraphitiFixtureError("CANONICAL_TIME_INVALID")
            return item.isoformat()

        node_keys: dict[str, str] = {}
        nodes: list[dict[str, Any]] = []
        for row in self.durable_records["nodes"].values():
            uuid = value(row, "uuid")
            name = value(row, "name")
            if not isinstance(uuid, str) or not isinstance(name, str) or not name:
                raise ControlledGraphitiFixtureError("CANONICAL_NODE_INVALID")
            logical_key = (
                f"canonical:{uuid}"
                if uuid.startswith("canonical-")
                else f"name:{name.casefold()}"
            )
            node_keys[uuid] = logical_key
            labels = value(row, "labels", ())
            if isinstance(labels, (str, bytes)):
                raise ControlledGraphitiFixtureError("CANONICAL_NODE_LABELS_INVALID")
            nodes.append(
                {
                    "logical_key": logical_key,
                    "name": name,
                    "summary": str(value(row, "summary", "")),
                    "labels": sorted(str(label) for label in labels),
                }
            )

        relationships: list[dict[str, Any]] = []
        for row in self.durable_records["edges"].values():
            source_uuid = value(row, "source_node_uuid")
            target_uuid = value(row, "target_node_uuid")
            fact = value(row, "fact")
            name = value(row, "name")
            if not all(
                isinstance(item, str) and item
                for item in (source_uuid, target_uuid, fact, name)
            ):
                raise ControlledGraphitiFixtureError("CANONICAL_EDGE_INVALID")
            episodes = value(row, "episodes", ())
            if isinstance(episodes, (str, bytes)):
                raise ControlledGraphitiFixtureError("CANONICAL_EDGE_EPISODES_INVALID")
            relationships.append(
                {
                    "source": node_keys.get(source_uuid, f"external:{source_uuid}"),
                    "target": node_keys.get(target_uuid, f"external:{target_uuid}"),
                    "name": name,
                    "fact": fact,
                    "episodes": sorted(str(item) for item in episodes),
                    "valid_at": timestamp(row, "valid_at"),
                    "invalid_at": timestamp(row, "invalid_at"),
                    "reference_time": timestamp(row, "reference_time"),
                }
            )

        nodes.sort(key=canonical_bytes)
        relationships.sort(key=canonical_bytes)
        return {"nodes": nodes, "relationships": relationships}

    @property
    def provider_consumption(self) -> tuple[str, ...]:
        return tuple(self.provider_ledger.consumed)

    @property
    def unexpected_provider_consumption(self) -> tuple[str, ...]:
        return tuple(self.provider_ledger.unexpected)

    def reset_case(self) -> None:
        """Restore all mutable fixture state before another independent case."""

        self.events.clear()
        self.transaction_attempts = 0
        self.call_order.clear()
        self.provider_ledger.reset()
        self.candidate_query_index = 0
        self.prepare_rendezvous_arrivals = 0
        self.prepare_rendezvous_event = asyncio.Event()
        self.llm.calls.clear()
        self.llm.request_evidence.clear()
        self.embedder.calls.clear()
        for rows in self.durable_records.values():
            rows.clear()
        self.retry_commit_projections.clear()
        self.runtime.resolved_node_coalescing_observations.clear()
        self.candidate_nodes = deepcopy(self._initial_candidate_nodes)
        self.candidate_node_sets = deepcopy(self._initial_candidate_node_sets)
        self.invalidation_edges = deepcopy(self._initial_invalidation_edges)
        self.providers = self._make_providers()

    @contextmanager
    def _provider_scope(self, _providers: object):
        if not isinstance(_providers, ControlledGraphitiProviders):
            raise ControlledGraphitiFixtureError("PROVIDER_SCOPE_IDENTITY_MISMATCH")
        previous = self.active_providers
        self.active_providers = _providers
        self.provider_ledger.consume("provider_scope")
        self.events.append({"event": "provider_scope_enter"})
        try:
            yield
        finally:
            self.active_providers = previous
            self.events.append({"event": "provider_scope_exit"})

    def _source(self, source_sequence: int = 0) -> GraphitiEpisodeInput:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        episode = EpisodicNode(
            uuid=f"controlled-episode-{source_sequence}",
            name=f"controlled-{source_sequence}",
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

    async def run_sources(self, source_count: int) -> tuple[tuple[GraphitiBindObservation, ...], tuple[int, ...]]:
        """Run multiple real Graphiti sources and publish only in source order."""

        if isinstance(source_count, bool) or not isinstance(source_count, int) or source_count < 1:
            raise ControlledGraphitiFixtureError("SOURCE_COUNT_INVALID")
        self.reset_case()
        logical_time_ns = self.providers.logical_time_ns
        self.provider_ledger.consume("logical_time")
        prepared = await asyncio.gather(
            *[
                self.runtime.prepare(self._source(index), logical_time_ns, self.providers)
                for index in range(source_count)
            ]
        )
        observations: list[GraphitiBindObservation] = []
        publication_order: list[int] = []
        for index, bundle in enumerate(prepared):
            observation = await self.runtime.bind(
                bundle,
                logical_time_ns,
                index,
                tuple(range(index)),
                self.providers,
            )
            observations.append(observation)
            publication_order.append(index)
            self.events.append({"event": "publication", "source_sequence": index})
        return tuple(observations), tuple(publication_order)

    async def run_episode(self) -> ControlledRunResult:
        self.call_order.clear()
        source = self._source(0)
        logical_time_ns = self.providers.logical_time_ns
        self.provider_ledger.consume("logical_time")
        providers = self.providers
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
        retry_idempotence_proven = False
        if self.transaction_attempts > 1:
            retry_idempotence_proven = (
                self.idempotent_retry
                and len(self.retry_commit_projections) >= 2
                and self.retry_commit_projections[0] == self.retry_commit_projections[1]
            )
            if not retry_idempotence_proven:
                self.events.append({"event": "retry_idempotence_unproven"})
                raise ControlledGraphitiFixtureError("RETRY_IDEMPOTENCE_UNPROVEN")
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
            retry_idempotence_proven=retry_idempotence_proven,
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
        "retry_transaction_once",
        "idempotent_retry",
        "mutate_retry_payload",
        "missing_llm_response",
        "canonical_candidate",
        "duplicate_entity",
        "conflicting_candidate_projections",
        "invalidation_candidate",
    }
    if unknown:
        raise ControlledGraphitiFixtureError("UNKNOWN_FIXTURE_OPTION")
    return ControlledGraphitiFixture(**kwargs)


__all__ = [
    "ControlledGraphitiProviders",
    "ControlledGraphitiFixture",
    "ControlledGraphitiFixtureError",
    "ControlledRunResult",
    "build_controlled_graphiti_fixture",
]
