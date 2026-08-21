"""Provider-free Graphiti 0.29.3 bind vertical slice.

The slice calls the production MemBind adapter and the installed semantic
binding.  Only nondeterministic provider boundaries are controlled.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

from paper_eval.membind_v1.evidence_fence import EvidenceFence, build_compile_input
from paper_eval.membind_v1.graphiti_factories import make_graphiti_node_factories
from paper_eval.membind_v1.source_log import SourceLog, SourceRecord
from paper_eval.membind_v31 import (
    AdmissionPolicy,
    CertificationRecord,
    DependencyClass,
    EffectClass,
    OperatorContract,
    StateCutCertification,
)
from paper_eval.membind_v31.graphiti_adapter import MemBindV31GraphitiAdapter
from paper_eval.membind_v31.request_runtime import (
    AdmittedLLMClientV31,
    RequestKind,
    llm_request_scope,
)
from paper_eval.membind_v31.prefix_affinity import PrefixMetadata

from paper_eval.s5_graphiti_controlled_fixture import build_controlled_graphiti_fixture

from .graphiti_0293_runtime import build_observe_only_binding
from .mutation_epoch import StateMutationEpoch
from .runtime_instrumentation import (
    InstrumentationMode,
    MEGRuntimeRecorder,
    OperatorEventType,
    SemanticOperatorClass,
    WriterDomainCertificate,
    current_runtime_request_metadata,
)


def _hash(index: int) -> str:
    return f"{index:064x}"


def _certification() -> StateCutCertification:
    records = []
    for offset, name in enumerate(("graphiti.extract_nodes", "graphiti.extract_edges")):
        records.append(
            CertificationRecord.create(
                operator_contract=OperatorContract.create(
                    operator_name=name,
                    dependency_class=DependencyClass.EVIDENCE_BOUND,
                    effect_class=EffectClass.PURE,
                ),
                memory_backend_identity_sha256=_hash(1),
                adapter_identity_sha256=_hash(2),
                operator_identity_sha256=_hash(10 + offset),
                code_revision_sha256=_hash(3),
                prompt_identity_sha256=_hash(20 + offset),
                schema_identity_sha256=_hash(30 + offset),
                config_identity_sha256=_hash(40 + offset),
                allowed_evidence_inputs=("current_source", "evidence_snapshot"),
                allowed_upstream_outputs=("graphiti.extract_nodes",) if offset else (),
                allowed_apis=("llm.generate_response",),
                forbidden_apis=("graph_driver.execute_query", "memory.search", "memory.write"),
                qualification_trace_sha256=_hash(50 + offset),
                persistent_state_read_count=0,
                persistent_state_write_count=0,
                undeclared_external_side_effect_count=0,
                future_evidence_access_count=0,
                undeclared_state_facing_call_count=0,
            )
        )
    return StateCutCertification.create(records)


def _prefix_encoder(*args: object, **kwargs: object) -> PrefixMetadata:
    prompt = str(kwargs.get("prompt_name", "controlled"))
    return PrefixMetadata.from_token_ids(
        [ord(char) for char in prompt],
        prefix_match_unit=4,
        tokenizer_identity_sha256="a" * 64,
        cache_identity_sha256="b" * 64,
        trace_hmac_key=b"c" * 32,
    )


class _VerticalWorksAt(BaseModel):
    confidence: str


@dataclass(frozen=True, slots=True)
class Graphiti0293BindVerticalSliceResult:
    prepared_artifact: object
    bind_observation: object
    recorder: MEGRuntimeRecorder
    mutation_epoch: StateMutationEpoch
    request_events: tuple[dict[str, object], ...]

    @property
    def request_lineage_complete(self) -> bool:
        spans = self.recorder.request_spans
        return bool(spans) and all(
            span.request_id and span.semantic_operator_id and span.prompt_name and span.semantic_subrequest_role
            for span in spans
        )


class Graphiti0293BindVerticalSlice:
    """Run one complete provider-free production-shaped bind."""

    def __init__(
        self,
        *,
        edge_fact: str | None = "Alice works at Acme.",
        edge_facts: tuple[str, ...] | None = None,
        canonical_candidate: bool = True,
        reverse_edge_completion: bool = False,
    ) -> None:
        selected_facts = edge_facts if edge_facts is not None else (() if edge_fact is None else (edge_fact,))
        self.fixture = build_controlled_graphiti_fixture(
            configured_database="neo4j",
            group_id="native-driver-group",
            edge_types=("WorksAt",) if selected_facts else (),
            edge_fact=selected_facts[0] if selected_facts else None,
            canonical_candidate=canonical_candidate,
            invalidation_candidate=bool(selected_facts),
            reverse_edge_duplicate_completion=(reverse_edge_completion and len(selected_facts) > 1),
            native_driver_shape=True,
        )
        if len(selected_facts) > 1:
            self.fixture.providers.llm_responses["ExtractedEdges"] = {
                "edges": [
                    {
                        "source_entity_name": "Alice",
                        "target_entity_name": "Acme",
                        "relation_type": "WorksAt",
                        "fact": fact,
                        "valid_at": None,
                        "episode_indices": [0],
                    }
                    for fact in selected_facts
                ]
            }
        self.fixture.providers.llm_responses["_VerticalWorksAt"] = {
            "confidence": "captured-high"
        }
        self.fixture.providers.llm_responses["EdgeTimestamps"] = {
            "valid_at": "2026-01-01T00:00:00Z",
            "invalid_at": None,
        }
        self.writer = WriterDomainCertificate.create(
            namespace=self.fixture.group_id,
            graph_backend="neo4j",
            authorized_writer_identity="controlled-membind-vertical-slice",
            write_path_coverage=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
            expected_write_paths=("bulk_utils.add_nodes_and_edges_bulk.execute_write",),
            external_writer_policy="DENY",
            commit_observer_coverage="ALL_MANAGED_COMMITS",
            fresh_namespace=True,
            no_background_mutation=True,
        )
        self.recorder = MEGRuntimeRecorder(
            mode=InstrumentationMode.OBSERVE_ONLY, writer_domain=self.writer
        )
        self.mutation_epoch = StateMutationEpoch(
            namespace=self.fixture.group_id,
            backend_id="neo4j",
            epoch="controlled-vertical-slice",
        )
        self.request_events: list[dict[str, object]] = []

    async def run(self) -> Graphiti0293BindVerticalSliceResult:
        admitted = AdmittedLLMClientV31(
            inner=self.fixture.llm,
            limit=4,
            policy=AdmissionPolicy.CACHE_AFFINE,
            request_id_prefix="vertical-slice-production",
            observer=self.request_events.append,
            causal_metadata_provider=current_runtime_request_metadata,
            prefix_encoder=_prefix_encoder,
        )
        self.fixture.graphiti.llm_client = admitted
        self.fixture.graphiti.clients.llm_client = admitted
        binding = build_observe_only_binding(
            self.fixture.binding,
            recorder=self.recorder,
            mutation_epoch=self.mutation_epoch,
            writer_domain=self.writer,
            stream_id="vertical-slice",
        )
        factories = make_graphiti_node_factories(
            episodic_node_type=__import__("graphiti_core.nodes", fromlist=["EpisodicNode"]).EpisodicNode,
            entity_node_type=__import__("graphiti_core.nodes", fromlist=["EntityNode"]).EntityNode,
            message_source=__import__("graphiti_core.nodes", fromlist=["EpisodeType"]).EpisodeType.text,
        )
        from graphiti_core.edges import EntityEdge

        episode = self.fixture._source(0).episode_node
        source = SourceRecord.create(
            source_sequence=0,
            episode_uuid=episode.uuid,
            group_id=self.fixture.group_id,
            reference_time_ns=int(episode.valid_at.timestamp() * 1_000_000_000),
            source_filter="message",
            episode_projection={"name": episode.name, "body": episode.content, "source_description": episode.source_description, "reference_time": episode.valid_at.isoformat()},
        )
        log = SourceLog.create([source])
        compile_input = build_compile_input(
            source,
            EvidenceFence.capture(log, target_source_sequence=0, last_n=10),
        )
        adapter = MemBindV31GraphitiAdapter(
            graphiti=self.fixture.graphiti,
            llm_client=admitted,
            semantic_binding=binding,
            episode_factory=factories.episode_factory,
            extracted_node_factory=factories.extracted_node_factory,
            extracted_edge_factory=lambda value: EntityEdge(**dict(value)),
            state_cut_certification=_certification(),
            latest_state_retriever=lambda _episode, _source: _empty_state(),
            edge_types=({"WorksAt": _VerticalWorksAt} if self.fixture.edge_types else None),
        )
        with self.fixture._provider_scope(self.fixture.providers):
            with llm_request_scope(kind=RequestKind.COMPILE, stream_id="vertical-slice", source_sequence=0):
                prepared = await adapter.prepare(compile_input)
            async with admitted.frontier_bind_region("vertical-slice", 0):
                with llm_request_scope(kind=RequestKind.FRONTIER, stream_id="vertical-slice", source_sequence=0):
                    bound = await adapter.bind(compile_input, prepared, logical_time_ns=episode.valid_at.timestamp_ns if hasattr(episode.valid_at, "timestamp_ns") else int(episode.valid_at.timestamp() * 1_000_000_000))
        return Graphiti0293BindVerticalSliceResult(
            prepared_artifact=prepared,
            bind_observation=bound,
            recorder=self.recorder,
            mutation_epoch=self.mutation_epoch,
            request_events=tuple(self.request_events),
        )


async def _empty_state() -> list[object]:
    return []


__all__ = ["Graphiti0293BindVerticalSlice", "Graphiti0293BindVerticalSliceResult"]
