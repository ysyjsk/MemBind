"""Fresh-history Graphiti resources for Protocol v1.3 H0-B and H0-C.

This module is deliberately separate from the legacy experiment runner.  It
accepts only source-bound runtime inputs and caller-supplied credentials, does
metadata-only readiness, injects every Graphiti client explicitly, and owns the
complete per-history cleanup lifecycle.
"""

from __future__ import annotations

from h0_bootstrap import disable_implicit_dotenv

disable_implicit_dotenv()

import hashlib
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from graphiti_core.cross_encoder import CrossEncoderClient

from h0_embedding import H0EmbeddingAdapter
from h0_runtime import (
    H0AttemptLedger,
    H0InfrastructureError,
    H0ManifestError,
    H0QwenVLLMClient,
    H0WireObserver,
    VLLMChatTokenCounter,
    build_h0_openai_client,
)
from live_outputs import evaluate_retrieval as _evaluate_retrieval


H0_EMBEDDING_BASE_URL = "http://10.87.5.247:8001/v1/"
H0_EMBEDDING_VLLM_VERSION = "0.26.0"
H0_NEO4J_URI = "bolt://localhost:7687"
H0_NEO4J_DATABASE = "neo4j"
H0_GRAPHITI_MAX_COROUTINES = 8


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class H0ForbiddenCrossEncoder(CrossEncoderClient):
    """Fail before network I/O if a full-history path unexpectedly reranks."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    @property
    def rank_call_count(self) -> int:
        return len(self._events)

    async def rank(
        self, query: str, passages: list[str]
    ) -> list[tuple[str, float]]:
        self._events.append(
            {
                "query_sha256": _sha256_text(str(query)),
                "query_utf8_byte_count": len(str(query).encode("utf-8")),
                "passage_count": len(passages),
                "passage_bundle_sha256": hashlib.sha256(
                    b"\0".join(str(value).encode("utf-8") for value in passages)
                ).hexdigest(),
            }
        )
        raise H0ManifestError("H0 cross-encoder invocation is forbidden")

    def safe_evidence(self) -> dict[str, Any]:
        return {
            "rank_call_count": self.rank_call_count,
            "events": deepcopy(self._events),
            "network_client_constructed": False,
        }


@dataclass
class H0GraphitiResources:
    """Every closeable object owned by one full-history Graphiti instance."""

    driver: Any
    embedder: Any
    token_counter: Any
    construction_client: Any
    cross_encoder: H0ForbiddenCrossEncoder
    graph: Any | None = None
    closed: bool = False
    close_count: int = 0
    cleanup_failure_count: int = 0

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_count += 1
        errors: list[BaseException] = []
        seen: set[int] = set()
        for resource in (
            self.driver,
            self.embedder,
            self.token_counter,
            self.construction_client,
        ):
            if resource is None or id(resource) in seen:
                continue
            seen.add(id(resource))
            close = getattr(resource, "close", None)
            if not callable(close):
                errors.append(TypeError("H0 resource has no close method"))
                continue
            try:
                await _maybe_await(close())
            except BaseException as exc:
                errors.append(exc)
        self.cleanup_failure_count += len(errors)
        if errors:
            raise H0ManifestError("H0 Graphiti resource cleanup failed") from errors[0]

    def safe_evidence(self) -> dict[str, Any]:
        embedding_events = (
            self.embedder.safe_evidence()
            if callable(getattr(self.embedder, "safe_evidence", None))
            else []
        )
        token_events = deepcopy(getattr(self.token_counter, "events", []))
        observer = getattr(self.construction_client, "_membind_h0_observer", None)
        wire_events = deepcopy(getattr(observer, "events", []))
        return {
            "closed": self.closed,
            "close_count": self.close_count,
            "cleanup_failure_count": self.cleanup_failure_count,
            "embedding_events": deepcopy(embedding_events),
            "tokenize_events": token_events,
            "wire_events": wire_events,
            "cross_encoder": self.cross_encoder.safe_evidence(),
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }


class H0GraphitiHistoryFactory:
    """Construct a distinct, readiness-checked resource set for every history."""

    def __init__(
        self,
        *,
        definition: Any,
        credentials: Mapping[str, Any],
        ledger: H0AttemptLedger,
        semantic_collector: Any,
        authorization_rechecker: Callable[[], Any],
        completion_client_factory: Callable[..., Any] | None = None,
        embedding_factory: Callable[..., Any] | None = None,
        driver_factory: Callable[..., Any] | None = None,
        graphiti_factory: Callable[..., Any] | None = None,
        readiness_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.definition = definition
        self.credentials = self._validated_credentials(credentials)
        self.ledger = ledger
        self.semantic_collector = semantic_collector
        self.authorization_rechecker = authorization_rechecker
        self.completion_client_factory = (
            completion_client_factory or self._build_completion_client
        )
        self.embedding_factory = embedding_factory or self._build_embedding
        self.driver_factory = driver_factory or self._build_driver
        self.graphiti_factory = graphiti_factory or self._build_graphiti
        self.readiness_sink = readiness_sink
        self.resources: list[H0GraphitiResources] = []
        self._resource_identities: dict[str, set[int]] = {
            "driver": set(),
            "embedder": set(),
            "token_counter": set(),
            "construction_client": set(),
        }

    def _validated_credentials(
        self, value: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(value, Mapping):
            raise H0ManifestError("H0 full-history credentials are invalid")
        sections: dict[str, dict[str, Any]] = {}
        for name in ("construction", "embedding", "neo4j"):
            section = value.get(name)
            if not isinstance(section, Mapping):
                raise H0ManifestError(f"H0 {name} credentials are invalid")
            sections[name] = dict(section)
        identity = getattr(self.definition, "identity", None)
        namespace = getattr(self.definition, "embedding_namespace", None)
        construction = sections["construction"]
        embedding = sections["embedding"]
        neo4j = sections["neo4j"]
        exact = (
            isinstance(identity, Mapping)
            and construction.get("base_url") == identity.get("base_url")
            and isinstance(construction.get("api_key"), str)
            and bool(construction.get("api_key"))
            and isinstance(namespace, Mapping)
            and embedding.get("base_url") == H0_EMBEDDING_BASE_URL
            and embedding.get("model") == namespace.get("served_model_id")
            and isinstance(embedding.get("api_key"), str)
            and bool(embedding.get("api_key"))
            and neo4j.get("uri") == H0_NEO4J_URI
            and neo4j.get("database") == H0_NEO4J_DATABASE
            and isinstance(neo4j.get("user"), str)
            and isinstance(neo4j.get("password"), str)
            and bool(neo4j.get("password"))
        )
        if not exact:
            raise H0ManifestError("H0 full-history credentials differ from bindings")
        return sections

    def _build_completion_client(self, **_kwargs: Any) -> Any:
        from graphiti_core.llm_client.config import LLMConfig

        identity = self.definition.identity
        candidate = self.definition.candidate
        credentials = self.credentials["construction"]
        observer = H0WireObserver()
        openai_client = build_h0_openai_client(
            api_key=credentials["api_key"],
            base_url=credentials["base_url"],
            observer=observer,
        )
        counter = VLLMChatTokenCounter(
            base_url=credentials["base_url"],
            model=identity["served_model_id"],
            api_key=credentials["api_key"],
        )
        try:
            return H0QwenVLLMClient(
                config=LLMConfig(
                    api_key="credential-held-by-injected-client",
                    model=identity["served_model_id"],
                    small_model=identity["served_model_id"],
                    base_url=credentials["base_url"],
                    temperature=candidate.temperature,
                    max_tokens=candidate.requested_max_tokens,
                ),
                candidate=candidate,
                token_counter=counter,
                semantic_guardrail=self.definition.semantic_guardrail,
                semantic_evidence_sink=self.semantic_collector,
                ledger=self.ledger,
                repeated_trial_index=0,
                client=openai_client,
            )
        except BaseException:
            # The caller's partial-construction cleanup cannot see these objects
            # until the LLM exists, so close both here before surfacing failure.
            async def close_local() -> None:
                await counter.close()
                await openai_client.close()

            try:
                import asyncio

                asyncio.get_running_loop().create_task(close_local())
            finally:
                raise

    def _build_embedding(self, **_kwargs: Any) -> H0EmbeddingAdapter:
        namespace = self.definition.embedding_namespace
        return H0EmbeddingAdapter(
            binding={
                "base_url": H0_EMBEDDING_BASE_URL,
                "served_model_id": namespace["served_model_id"],
                "vllm_version": H0_EMBEDDING_VLLM_VERSION,
                "dimension": namespace["dimension"],
                "normalization": namespace["normalization"],
            },
            credentials=self.credentials["embedding"],
        )

    def _build_driver(self, **_kwargs: Any) -> Any:
        from graphiti_core.driver.neo4j_driver import Neo4jDriver

        values = self.credentials["neo4j"]
        return Neo4jDriver(
            values["uri"],
            values["user"],
            values["password"],
            database=values["database"],
        )

    @staticmethod
    def _build_graphiti(**kwargs: Any) -> Any:
        from deterministic_search import (
            install_edge_query_stabilizer,
            install_edge_search_stabilizer,
            install_node_query_stabilizer,
            install_node_resolution_stabilizer,
        )
        from graphiti_core import Graphiti

        install_edge_search_stabilizer()
        install_node_resolution_stabilizer()
        graph = Graphiti(**kwargs)
        install_edge_query_stabilizer(graph.driver)
        install_node_query_stabilizer(graph.driver)
        return graph

    async def __call__(self) -> Any:
        trial_count = len(self.ledger.trials)
        attempt_count = len(self.ledger.attempts)
        llm: Any | None = None
        embedder: Any | None = None
        driver: Any | None = None
        resources: H0GraphitiResources | None = None
        cross_encoder = H0ForbiddenCrossEncoder()
        try:
            llm = self.completion_client_factory(
                definition=self.definition,
                credentials=self.credentials["construction"],
                ledger=self.ledger,
                semantic_collector=self.semantic_collector,
            )
            if getattr(llm, "h0_ledger", None) is not self.ledger:
                raise H0ManifestError("H0 Graphiti LLM does not share the stage ledger")
            embedder = self.embedding_factory(
                definition=self.definition,
                credentials=self.credentials["embedding"],
            )
            driver = self.driver_factory(credentials=self.credentials["neo4j"])
            resources = H0GraphitiResources(
                driver=driver,
                embedder=embedder,
                token_counter=getattr(llm, "h0_token_counter", None),
                construction_client=getattr(llm, "client", None),
                cross_encoder=cross_encoder,
            )

            init_task = getattr(driver, "_init_task", None)
            if init_task is not None:
                await _maybe_await(init_task)
            else:
                build_indices = getattr(driver, "build_indices_and_constraints", None)
                if not callable(build_indices):
                    raise H0ManifestError("H0 Neo4j driver has no index readiness path")
                await _maybe_await(build_indices())
            if len(self.ledger.trials) != trial_count or len(self.ledger.attempts) != attempt_count:
                raise H0ManifestError("H0 readiness issued a construction-model call")
            await _maybe_await(self.authorization_rechecker())
            graph = self.graphiti_factory(
                graph_driver=driver,
                llm_client=llm,
                embedder=embedder,
                cross_encoder=cross_encoder,
                max_coroutines=H0_GRAPHITI_MAX_COROUTINES,
            )
            if graph is None:
                raise H0ManifestError("H0 Graphiti factory returned no graph")
            current_resources = {
                "driver": driver,
                "embedder": embedder,
                "token_counter": resources.token_counter,
                "construction_client": resources.construction_client,
            }
            if any(
                id(value) in self._resource_identities[name]
                for name, value in current_resources.items()
            ):
                raise H0ManifestError("H0 full-history resource was reused")
            for name, value in current_resources.items():
                self._resource_identities[name].add(id(value))
            resources.graph = graph
            graph._membind_h0_resources = resources
            self.resources.append(resources)
            if self.readiness_sink is not None:
                await _maybe_await(
                    self.readiness_sink(
                        {
                            "schema_version": "membind.h0.graph-construction.v1",
                            "stage_readiness_repeated": False,
                            "embedding_request_count": 0,
                            "neo4j_indices_ready": True,
                            "neo4j_health_probe_count": 0,
                            "construction_generation_request_count": 0,
                            "cross_encoder_rank_call_count": 0,
                            "authorization_rechecked": True,
                            "warmup_performed": False,
                            "secrets_persisted": False,
                        }
                    )
                )
            return graph
        except H0InfrastructureError:
            if resources is not None:
                try:
                    await resources.close()
                except BaseException:
                    pass
            raise
        except BaseException as exc:
            if resources is not None:
                try:
                    await resources.close()
                except BaseException:
                    pass
            else:
                token_counter = getattr(llm, "h0_token_counter", None)
                construction_client = getattr(llm, "client", None)
                seen: set[int] = set()
                for resource in (
                    driver,
                    embedder,
                    token_counter,
                    construction_client,
                ):
                    if resource is None or id(resource) in seen:
                        continue
                    seen.add(id(resource))
                    close = getattr(resource, "close", None)
                    if not callable(close):
                        continue
                    try:
                        await _maybe_await(close())
                    except BaseException:
                        pass
            if isinstance(exc, H0ManifestError):
                raise
            raise H0ManifestError("H0 Graphiti history construction failed") from exc

    def safe_runtime_evidence(self) -> dict[str, Any]:
        histories = [resource.safe_evidence() for resource in self.resources]
        embedding_workload_requests = sum(
            int(event.get("request_count") or 0)
            for history in histories
            for event in history["embedding_events"]
            if event.get("event") != "embedding_metadata_readiness"
        )
        return {
            "fresh_graph_count": len(self.resources),
            "closed_graph_count": sum(resource.closed for resource in self.resources),
            "embedding_workload_request_count": embedding_workload_requests,
            "cross_encoder_rank_call_count": sum(
                resource.cross_encoder.rank_call_count for resource in self.resources
            ),
            "histories": histories,
            "secrets_persisted": False,
            "raw_prompts_persisted": False,
            "raw_responses_persisted": False,
        }


async def close_h0_graphiti_history(graph: Any) -> None:
    """Close the exact resource bundle attached by the H0 history factory."""

    resources = getattr(graph, "_membind_h0_resources", None)
    if not isinstance(resources, H0GraphitiResources):
        raise H0ManifestError("H0 graph has no owned resource bundle")
    await resources.close()


async def evaluate_h0_retrieval(
    graph: Any, instance: Mapping[str, Any], episodes: list[Any]
) -> dict[str, Any]:
    """Use Graphiti's basic RRF search and prove reranking stayed unused."""

    cross_encoder = getattr(graph, "cross_encoder", None)
    if cross_encoder is None:
        cross_encoder = getattr(getattr(graph, "clients", None), "cross_encoder", None)
    if not isinstance(cross_encoder, H0ForbiddenCrossEncoder):
        raise H0ManifestError("H0 graph has no fail-closed cross encoder")
    if cross_encoder.rank_call_count != 0:
        raise H0ManifestError("H0 cross-encoder was invoked before retrieval")
    result = await _evaluate_retrieval(graph, dict(instance), episodes, top_k=10)
    if cross_encoder.rank_call_count != 0:
        raise H0ManifestError("H0 RRF retrieval unexpectedly invoked cross-encoder")
    return result
