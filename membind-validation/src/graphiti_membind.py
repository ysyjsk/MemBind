"""MemBind-GO runner for Graphiti v0.29.3.

This module keeps imports lazy so the unit-testable protocol logic works before
Graphiti and Neo4j are installed. The live path uses Graphiti's own extraction,
resolution, invalidation and commit functions at commit 021d3a5.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from dataset import Episode
from graphiti_native import parse_datetime
from instrumentation import apply_episode_metrics, episode_scope
from latest_state_bind import LatestStateBinder, SourceOrderedCommitter
from semantic_compile import CompiledArtifact, EvidenceFence, assert_unbound_payload
from tracing import EpisodeTrace, JsonlTraceWriter, now_ns


M2_MEMBIND_GO_C8 = "M2"


def native_previous_source_episodes(
    fence: EvidenceFence,
    episode: Episode,
    limit: int,
) -> list[Episode]:
    """Mirror Graphiti's recent-window selection and chronological presentation."""

    if limit <= 0:
        raise ValueError("previous episode limit must be positive")
    current_time = parse_datetime(episode.reference_time)
    prefix = [
        previous
        for previous in fence.prefix_before(episode.source_sequence)
        if parse_datetime(previous.reference_time) <= current_time
    ]
    prefix.sort(
        key=lambda previous: (
            parse_datetime(previous.reference_time),
            previous.source_sequence,
        ),
        reverse=True,
    )
    return list(reversed(prefix[:limit]))


@dataclass
class GraphitiCompileBundle:
    artifact: CompiledArtifact
    episode_node: Any
    extracted_nodes: list[Any]
    extracted_edges: list[Any]
    node_episode_index_map: dict[str, list[int]]
    entity_types: dict[str, Any] | None
    edge_types: dict[str, Any] | None
    edge_type_map: dict[tuple[str, str], list[str]]
    custom_extraction_instructions: str | None


class GraphitiMemBindRuntime:
    def __init__(
        self,
        graphiti: Any,
        entity_types: dict[str, Any] | None = None,
        excluded_entity_types: list[str] | None = None,
        edge_types: dict[str, Any] | None = None,
        edge_type_map: dict[tuple[str, str], list[str]] | None = None,
        custom_extraction_instructions: str | None = None,
    ) -> None:
        self.graphiti = graphiti
        self.entity_types = entity_types
        self.excluded_entity_types = excluded_entity_types
        self.edge_types = edge_types
        self.edge_type_map = edge_type_map or ({("Entity", "Entity"): list(edge_types.keys())} if edge_types else {("Entity", "Entity"): []})
        self.custom_extraction_instructions = custom_extraction_instructions
        self._bundles: dict[int, GraphitiCompileBundle] = {}

    def _episode_node(self, episode: Episode) -> Any:
        from graphiti_core.nodes import EpisodeType, EpisodicNode
        from graphiti_core.utils.datetime_utils import utc_now

        return EpisodicNode(
            name=episode.name,
            group_id=episode.group_id,
            labels=[],
            source=EpisodeType.message,
            content=episode.body,
            source_description="LongMemEval-S haystack session",
            created_at=utc_now(),
            valid_at=parse_datetime(episode.reference_time),
        )

    async def semantic_compile(self, fence: EvidenceFence, episode: Episode) -> CompiledArtifact:
        from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT
        from graphiti_core.utils.maintenance.edge_operations import extract_edges
        from graphiti_core.utils.maintenance.node_operations import extract_nodes

        previous_episodes = [
            self._episode_node(ep)
            for ep in native_previous_source_episodes(
                fence,
                episode,
                limit=RELEVANT_SCHEMA_LIMIT,
            )
        ]
        episode_node = self._episode_node(episode)
        extracted_nodes, node_episode_index_map = await extract_nodes(
            self.graphiti.clients,
            episode_node,
            previous_episodes,
            self.entity_types,
            self.excluded_entity_types,
            self.custom_extraction_instructions,
        )
        extracted_edges = await extract_edges(
            self.graphiti.clients,
            episode_node,
            extracted_nodes,
            previous_episodes,
            self.edge_type_map,
            episode.group_id,
            self.edge_types,
            self.custom_extraction_instructions,
        )
        payload = self._logical_payload(episode, extracted_nodes, extracted_edges)
        assert_unbound_payload(payload)
        prompt_hash = hashlib.sha256(json.dumps(payload["source_episode_mapping"], sort_keys=True).encode()).hexdigest()
        response_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
        artifact = CompiledArtifact.from_episode(episode, payload, prompt_hash, response_hash)
        self._bundles[episode.source_sequence] = GraphitiCompileBundle(
            artifact,
            episode_node,
            extracted_nodes,
            extracted_edges,
            node_episode_index_map,
            self.entity_types,
            self.edge_types,
            self.edge_type_map,
            self.custom_extraction_instructions,
        )
        return artifact

    def _logical_payload(self, episode: Episode, nodes: list[Any], edges: list[Any]) -> dict[str, Any]:
        uuid_to_name = {getattr(node, "uuid", ""): getattr(node, "name", "") for node in nodes}
        return {
            "candidate_entities": [
                {
                    "name": getattr(node, "name", ""),
                    "labels": list(getattr(node, "labels", []) or []),
                    "summary": getattr(node, "summary", "") or "",
                }
                for node in nodes
            ],
            "candidate_relations": [
                {
                    "source_entity_key": uuid_to_name.get(getattr(edge, "source_node_uuid", ""), ""),
                    "target_entity_key": uuid_to_name.get(getattr(edge, "target_node_uuid", ""), ""),
                    "relation_type": getattr(edge, "name", "") or getattr(edge, "relation_type", ""),
                    "fact": getattr(edge, "fact", ""),
                    "valid_at": str(getattr(edge, "valid_at", "")) if getattr(edge, "valid_at", None) else None,
                    "invalid_at": str(getattr(edge, "invalid_at", "")) if getattr(edge, "invalid_at", None) else None,
                }
                for edge in edges
            ],
            "candidate_temporal_fields": {},
            "source_episode_mapping": {
                "question_id": episode.question_id,
                "session_id": episode.session_id,
                "source_sequence": episode.source_sequence,
                "source_hash": episode.source_hash,
            },
        }

    async def bind_compiled_artifact(self, artifact: CompiledArtifact) -> Any:
        from graphiti_core.helpers import get_default_group_id, validate_group_id
        from graphiti_core.nodes import EpisodeType
        from graphiti_core.utils.datetime_utils import utc_now
        from graphiti_core.search.search_utils import RELEVANT_SCHEMA_LIMIT
        from graphiti_core.utils.bulk_utils import resolve_edge_pointers
        from graphiti_core.utils.maintenance.edge_operations import resolve_extracted_edges
        from graphiti_core.utils.maintenance.node_operations import extract_attributes_from_nodes, resolve_extracted_nodes

        bundle = self._bundles[artifact.source_sequence]
        group_id = artifact.group_id or get_default_group_id(self.graphiti.driver.provider)
        validate_group_id(group_id)
        previous_episodes = await self.graphiti.retrieve_episodes(
            parse_datetime(artifact.reference_time),
            last_n=RELEVANT_SCHEMA_LIMIT,
            group_ids=[group_id],
            source=EpisodeType.message,
        )
        nodes, uuid_map, _ = await resolve_extracted_nodes(
            self.graphiti.clients,
            bundle.extracted_nodes,
            bundle.episode_node,
            previous_episodes,
            bundle.entity_types,
        )
        edges = resolve_edge_pointers(bundle.extracted_edges, uuid_map)
        resolved_edges, invalidated_edges, new_edges = await resolve_extracted_edges(
            self.graphiti.clients,
            edges,
            bundle.episode_node,
            nodes,
            bundle.edge_types or {},
            bundle.edge_type_map,
        )
        hydrated_nodes = await extract_attributes_from_nodes(
            self.graphiti.clients,
            nodes,
            bundle.episode_node,
            previous_episodes,
            bundle.entity_types,
            edges=new_edges,
        )
        return await self.graphiti._process_episode_data(
            bundle.episode_node,
            hydrated_nodes,
            resolved_edges + invalidated_edges,
            utc_now(),
            group_id,
            None,
            None,
            bundle.node_episode_index_map,
        )


async def run_membind_go(
    graphiti: Any,
    episodes: list[Episode],
    run_id: str,
    repeat: int,
    arrival_interval_ms: int,
    trace_writer: JsonlTraceWriter,
    max_compile_concurrency: int = 8,
) -> None:
    fence = EvidenceFence()
    runtime = GraphitiMemBindRuntime(graphiti)
    traces: dict[int, EpisodeTrace] = {}

    async def bind_with_trace(artifact: CompiledArtifact) -> Any:
        trace = traces[artifact.source_sequence]
        trace.bind_start_time = now_ns()
        trace.commit_start_time = trace.bind_start_time
        with episode_scope(run_id, artifact.source_sequence):
            result = await LatestStateBinder(runtime).bind_and_commit(artifact)
        trace.bind_end_time = now_ns()
        trace.commit_end_time = trace.bind_end_time
        trace.publish_time = trace.commit_end_time
        apply_episode_metrics(graphiti, trace)
        trace_writer.write(trace)
        return result

    committer = SourceOrderedCommitter(len(episodes), bind_with_trace)
    compile_sem = asyncio.Semaphore(max_compile_concurrency)
    start = now_ns()

    async def compile_and_submit(ep: Episode) -> None:
        trace = traces[ep.source_sequence]
        try:
            async with compile_sem:
                trace.compile_start_time = now_ns()
                with episode_scope(run_id, ep.source_sequence):
                    artifact = await runtime.semantic_compile(fence, ep)
                trace.compile_end_time = now_ns()
            await committer.submit(artifact)
        except Exception as exc:
            trace.error = repr(exc)
            trace.publish_time = now_ns()
            apply_episode_metrics(graphiti, trace)
            trace_writer.write(trace)
            raise

    tasks: list[asyncio.Task[None]] = []
    for ep in episodes:
        target = start + int(ep.source_sequence * arrival_interval_ms * 1_000_000)
        delay = max(0, (target - now_ns()) / 1_000_000_000)
        if delay:
            await asyncio.sleep(delay)
        trace = EpisodeTrace(
            run_id,
            ep.question_id,
            M2_MEMBIND_GO_C8,
            repeat,
            ep.source_sequence,
            now_ns(),
        )
        trace.queue_enter_time = now_ns()
        traces[ep.source_sequence] = trace
        fence.append(ep)
        tasks.append(asyncio.create_task(compile_and_submit(ep)))

    await asyncio.gather(*tasks)
    if not committer.is_complete:
        raise RuntimeError("MemBind run ended before all episodes were published")
