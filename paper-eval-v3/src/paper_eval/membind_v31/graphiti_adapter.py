"""Pinned Graphiti v0.29.3 State-Cut production adapter for MemBind v3.1.

Compile receives only an immutable ``CompileInput`` and an LLM-only clients
capability.  Mutable Graphiti clients and latest-state retrieval first become
reachable in ``bind``.  Edge extraction enters Compile only when its own
operator certification is present in the frozen State-Cut identity.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from paper_eval.artifacts import canonical_bytes
from paper_eval.membind_v1.evidence_fence import CompileInput
from paper_eval.membind_v1.source_log import SourceRecord
from paper_eval.membind_v31.certification import StateCutCertification
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact, PreparedArtifactError
from paper_eval.s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    S5GraphitiSemanticBindingError,
)


_NODE_EXTRACT = "graphiti.extract_nodes"
_EDGE_EXTRACT = "graphiti.extract_edges"
_ALLOWED_COMPILE_OPERATORS = {_NODE_EXTRACT, _EDGE_EXTRACT}
_DEFAULT_PREVIOUS_EPISODE_LIMIT = 10


class MemBindV31GraphitiAdapterError(ValueError):
    """A capability, certification, artifact, or Graphiti shape failed."""

    def __init__(self, code: str, *, upstream_error_class: str | None = None) -> None:
        self.code = code
        self.upstream_error_class = upstream_error_class
        super().__init__(code)


def _fail(
    code: str,
    *,
    upstream_error_class: str | None = None,
) -> MemBindV31GraphitiAdapterError:
    return MemBindV31GraphitiAdapterError(
        code,
        upstream_error_class=upstream_error_class,
    )


class _ForbiddenCompileCapability(AttributeError):
    pass


def _qualified_error_class(error: BaseException) -> str:
    kind = type(error)
    return f"{kind.__module__}.{kind.__qualname__}"


def _logical_ns_to_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("logical_time_invalid")
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


def _canonical_projection(value: object, *, code: str) -> dict[str, object]:
    if isinstance(value, Mapping):
        selected: object = dict(value)
    else:
        model_dump = getattr(value, "model_dump", None)
        if not callable(model_dump):
            raise _fail(code)
        try:
            selected = model_dump(mode="json")
        except TypeError:
            try:
                selected = model_dump()
            except Exception:
                raise _fail(code) from None
        except Exception:
            raise _fail(code) from None
    if not isinstance(selected, Mapping):
        raise _fail(code)
    try:
        decoded = json.loads(canonical_bytes(dict(selected)).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return decoded


def _node_uuid(node: object) -> str:
    value = node.get("uuid") if isinstance(node, Mapping) else getattr(node, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("resolved_node_uuid_missing")
    return value


def _coalesce_compatible_nodes(nodes: Sequence[object]) -> tuple[object, ...]:
    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[object] = []
    projections: dict[str, bytes] = {}
    for node in nodes:
        uuid = _node_uuid(node)
        projection = canonical_bytes(
            _canonical_projection(node, code="resolved_node_projection_invalid")
        )
        prior = projections.get(uuid)
        if prior is None:
            projections[uuid] = projection
            selected.append(node)
        elif prior != projection:
            raise _fail("conflicting_duplicate_uuid")
    return tuple(selected)


@dataclass(frozen=True, slots=True)
class _LLMOnlyClients:
    llm_client: object

    def __getattr__(self, name: str) -> object:
        raise _ForbiddenCompileCapability(name)


@dataclass(frozen=True, slots=True)
class MemBindV31BindObservation:
    source_sequence: int
    resolved_node_count: int
    resolved_edge_count: int
    invalidated_edge_count: int
    commit_result_type: str


EpisodeFactory = Callable[[SourceRecord], object]
ProjectionFactory = Callable[[dict[str, object]], object]
LatestStateRetriever = Callable[[object, SourceRecord], Awaitable[Sequence[object]]]
CallObserver = Callable[[str], object]


class MemBindV31GraphitiAdapter:
    """Compile certified extraction, then bind the Native stateful suffix."""

    def __init__(
        self,
        *,
        graphiti: object,
        llm_client: object,
        semantic_binding: S5GraphitiSemanticBinding,
        episode_factory: EpisodeFactory,
        extracted_node_factory: ProjectionFactory,
        extracted_edge_factory: ProjectionFactory,
        state_cut_certification: StateCutCertification,
        previous_episode_limit: int = _DEFAULT_PREVIOUS_EPISODE_LIMIT,
        entity_types: Mapping[str, object] | None = None,
        excluded_entity_types: Sequence[str] | None = None,
        edge_types: Mapping[str, object] | None = None,
        edge_type_map: Mapping[tuple[str, str], Sequence[str]] | None = None,
        custom_extraction_instructions: str | None = None,
        latest_state_retriever: LatestStateRetriever | None = None,
        call_observer: CallObserver | None = None,
        require_native_commit_shape: bool = True,
    ) -> None:
        if graphiti is None or llm_client is None:
            raise _fail("graphiti_or_llm_missing")
        if not isinstance(semantic_binding, S5GraphitiSemanticBinding):
            raise _fail("semantic_binding_invalid")
        if not callable(episode_factory):
            raise _fail("episode_factory_invalid")
        if not callable(extracted_node_factory) or not callable(extracted_edge_factory):
            raise _fail("projection_factory_invalid")
        if not isinstance(state_cut_certification, StateCutCertification):
            raise _fail("state_cut_certification_invalid")
        try:
            certification = state_cut_certification.verify()
        except ValueError:
            raise _fail("state_cut_certification_invalid") from None
        operator_names = set(certification.operator_names)
        if _NODE_EXTRACT not in operator_names:
            raise _fail("node_extract_not_certified")
        if not operator_names <= _ALLOWED_COMPILE_OPERATORS:
            raise _fail("compile_operator_unsupported")
        if (
            isinstance(previous_episode_limit, bool)
            or not isinstance(previous_episode_limit, int)
            or previous_episode_limit <= 0
        ):
            raise _fail("previous_episode_limit_invalid")
        if latest_state_retriever is not None and not callable(latest_state_retriever):
            raise _fail("latest_state_retriever_invalid")
        if call_observer is not None and not callable(call_observer):
            raise _fail("call_observer_invalid")
        if not isinstance(require_native_commit_shape, bool):
            raise _fail("require_native_commit_shape_invalid")

        self._graphiti = graphiti
        self._binding = semantic_binding
        self._episode_factory = episode_factory
        self._node_factory = extracted_node_factory
        self._edge_factory = extracted_edge_factory
        self._compile_clients = _LLMOnlyClients(llm_client=llm_client)
        self._certification = certification
        self._compile_edges = _EDGE_EXTRACT in operator_names
        self._previous_episode_limit = previous_episode_limit
        self._entity_types = None if entity_types is None else dict(entity_types)
        self._excluded_entity_types = tuple(excluded_entity_types or ())
        self._edge_types = None if edge_types is None else dict(edge_types)
        self._configured_edge_type_map = (
            None
            if edge_type_map is None
            else {tuple(key): list(value) for key, value in edge_type_map.items()}
        )
        self._custom_extraction_instructions = custom_extraction_instructions
        self._latest_state_retriever = latest_state_retriever
        self._call_observer = call_observer
        self._require_native_commit_shape = require_native_commit_shape

    @property
    def state_cut_certification_sha256(self) -> str:
        return self._certification.certification_sha256

    @property
    def compiled_operator_names(self) -> tuple[str, ...]:
        return self._certification.operator_names

    def _observe(self, operation: str) -> None:
        if self._call_observer is None:
            return
        try:
            self._call_observer(operation)
        except Exception:
            raise _fail("call_observer_failed") from None

    @staticmethod
    async def _await(value: object, code: str) -> object:
        if not inspect.isawaitable(value):
            raise _fail(code)
        try:
            return await value
        except MemBindV31GraphitiAdapterError:
            raise
        except _ForbiddenCompileCapability:
            raise _fail("certified_compile_forbidden_capability") from None
        except Exception as error:
            raise _fail(code, upstream_error_class=_qualified_error_class(error)) from None

    def _materialize_episode(self, source: SourceRecord, *, code: str) -> object:
        try:
            return self._episode_factory(source)
        except MemBindV31GraphitiAdapterError:
            raise
        except Exception as error:
            raise _fail(code, upstream_error_class=_qualified_error_class(error)) from None

    @staticmethod
    def _edge_type_map(
        configured: Mapping[tuple[str, str], Sequence[str]] | None,
        edge_types: Mapping[str, object] | None,
    ) -> dict[tuple[str, str], list[str]]:
        if configured is not None:
            return {tuple(key): list(value) for key, value in configured.items()}
        return {
            ("Entity", "Entity"): [] if edge_types is None else list(edge_types.keys())
        }

    def _materialize_nodes(self, artifact: PreparedArtifact) -> list[object]:
        try:
            return [self._node_factory(dict(item)) for item in artifact.raw_nodes]
        except Exception as error:
            raise _fail(
                "extracted_node_materialization_failed",
                upstream_error_class=_qualified_error_class(error),
            ) from None

    def _materialize_edges(self, artifact: PreparedArtifact) -> list[object]:
        raw = artifact.raw_edges
        if raw is None:
            raise _fail("prepared_edges_missing")
        try:
            return [self._edge_factory(dict(item)) for item in raw]
        except Exception as error:
            raise _fail(
                "extracted_edge_materialization_failed",
                upstream_error_class=_qualified_error_class(error),
            ) from None

    def _assert_artifact(self, compile_input: CompileInput, artifact: PreparedArtifact) -> None:
        if not isinstance(artifact, PreparedArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            artifact.verify(
                expected_source_sha256=compile_input.source.source_sha256,
                expected_evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
                expected_certification_sha256=self.state_cut_certification_sha256,
            )
        except PreparedArtifactError as error:
            raise _fail(str(error)) from None
        if artifact.source_sequence != compile_input.source.source_sequence:
            raise _fail("artifact_source_sequence_mismatch")
        if (artifact.raw_edges is not None) != self._compile_edges:
            raise _fail("prepared_edge_certification_mismatch")

    def _route_group_for_bind(self, group_id: str) -> None:
        driver = getattr(self._graphiti, "driver", None)
        if driver is None:
            return
        database = getattr(driver, "_database", group_id)
        if database == group_id:
            return
        clone = getattr(driver, "clone", None)
        if not callable(clone):
            raise _fail("graphiti_driver_clone_missing")
        try:
            selected = clone(database=group_id)
            setattr(self._graphiti, "driver", selected)
            clients = getattr(self._graphiti, "clients", None)
            if clients is None:
                raise _fail("graphiti_clients_missing")
            setattr(clients, "driver", selected)
        except MemBindV31GraphitiAdapterError:
            raise
        except Exception as error:
            raise _fail(
                "graphiti_driver_routing_failed",
                upstream_error_class=_qualified_error_class(error),
            ) from None

    async def _retrieve_latest(self, episode: object, source: SourceRecord) -> list[object]:
        try:
            if self._latest_state_retriever is not None:
                pending = self._latest_state_retriever(episode, source)
            else:
                retrieve = getattr(self._graphiti, "retrieve_episodes", None)
                if not callable(retrieve):
                    raise _fail("latest_state_retrieval_missing")
                pending = retrieve(
                    getattr(episode, "valid_at"),
                    last_n=self._previous_episode_limit,
                    group_ids=[source.group_id],
                    source=getattr(episode, "source"),
                )
            rows = await self._await(pending, "latest_state_retrieval_failed")
        except MemBindV31GraphitiAdapterError:
            raise
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise _fail("latest_state_retrieval_shape_invalid")
        return list(rows)

    @staticmethod
    def _validate_commit(value: object) -> None:
        if not isinstance(value, tuple) or len(value) != 2:
            raise _fail("native_commit_result_shape_invalid")
        episodic_edges, episode = value
        if not isinstance(episodic_edges, list) or not hasattr(episode, "uuid"):
            raise _fail("native_commit_result_shape_invalid")

    async def prepare(self, compile_input: CompileInput) -> PreparedArtifact:
        if not isinstance(compile_input, CompileInput):
            raise _fail("compile_input_invalid")
        source = compile_input.source
        episode = self._materialize_episode(source, code="source_episode_materialization_failed")
        previous = [
            self._materialize_episode(item, code="evidence_episode_materialization_failed")
            for item in compile_input.evidence.evidence_records
        ]
        edge_map = self._edge_type_map(self._configured_edge_type_map, self._edge_types)
        try:
            self._observe("extract_nodes")
            output = await self._await(
                self._binding.extract_nodes(
                    self._compile_clients,
                    episode,
                    list(previous),
                    self._entity_types,
                    list(self._excluded_entity_types),
                    self._custom_extraction_instructions,
                ),
                "extract_nodes_failed",
            )
            if not isinstance(output, tuple) or len(output) != 2:
                raise _fail("extract_nodes_result_shape_invalid")
            extracted_nodes, node_episode_index_map = output
            if isinstance(extracted_nodes, (str, bytes)) or not isinstance(
                extracted_nodes, Sequence
            ):
                raise _fail("extracted_nodes_invalid")
            if not isinstance(node_episode_index_map, Mapping):
                raise _fail("node_episode_index_map_invalid")
            raw_nodes = [
                _canonical_projection(item, code="extracted_node_projection_invalid")
                for item in extracted_nodes
            ]
            raw_edges: list[dict[str, object]] | None = None
            if self._compile_edges:
                self._observe("extract_edges")
                edge_output = await self._await(
                    self._binding.extract_edges(
                        self._compile_clients,
                        episode,
                        list(extracted_nodes),
                        list(previous),
                        edge_map,
                        source.group_id,
                        self._edge_types,
                        self._custom_extraction_instructions,
                    ),
                    "extract_edges_failed",
                )
                if isinstance(edge_output, (str, bytes)) or not isinstance(
                    edge_output, Sequence
                ):
                    raise _fail("extracted_edges_invalid")
                raw_edges = [
                    _canonical_projection(item, code="extracted_edge_projection_invalid")
                    for item in edge_output
                ]
            return PreparedArtifact.create(
                source_sequence=source.source_sequence,
                source_sha256=source.source_sha256,
                evidence_sha256=compile_input.evidence.evidence_prefix_sha256,
                certification_sha256=self.state_cut_certification_sha256,
                raw_nodes=raw_nodes,
                raw_edges=raw_edges,
                pure_intermediates={
                    "node_episode_index_map": dict(node_episode_index_map),
                },
            )
        except _ForbiddenCompileCapability:
            raise _fail("certified_compile_forbidden_capability") from None
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        except PreparedArtifactError as error:
            raise _fail(str(error)) from None

    async def bind(
        self,
        compile_input: CompileInput,
        artifact: PreparedArtifact,
        *,
        logical_time_ns: int,
    ) -> MemBindV31BindObservation:
        self._assert_artifact(compile_input, artifact)
        source = compile_input.source
        episode = self._materialize_episode(source, code="source_episode_materialization_failed")
        logical_time = _logical_ns_to_datetime(logical_time_ns)
        self._route_group_for_bind(source.group_id)
        previous = await self._retrieve_latest(episode, source)
        clients = getattr(self._graphiti, "clients", None)
        if clients is None:
            raise _fail("graphiti_clients_missing")
        extracted_nodes = self._materialize_nodes(artifact)
        edge_map = self._edge_type_map(self._configured_edge_type_map, self._edge_types)
        try:
            self._observe("resolve_extracted_nodes")
            node_output = await self._await(
                self._binding.resolve_extracted_nodes(
                    clients,
                    list(extracted_nodes),
                    episode,
                    list(previous),
                    self._entity_types,
                ),
                "resolve_nodes_failed",
            )
            if not isinstance(node_output, tuple) or len(node_output) != 3:
                raise _fail("resolve_nodes_result_shape_invalid")
            resolved_nodes, uuid_map, _duplicates = node_output
            if not isinstance(uuid_map, Mapping):
                raise _fail("resolve_nodes_uuid_map_invalid")
            nodes = _coalesce_compatible_nodes(resolved_nodes)

            if self._compile_edges:
                extracted_edges = self._materialize_edges(artifact)
            else:
                self._observe("extract_edges")
                extracted_edges = await self._await(
                    self._binding.extract_edges(
                        clients,
                        episode,
                        list(extracted_nodes),
                        list(previous),
                        edge_map,
                        source.group_id,
                        self._edge_types,
                        self._custom_extraction_instructions,
                    ),
                    "extract_edges_failed",
                )
                if isinstance(extracted_edges, (str, bytes)) or not isinstance(
                    extracted_edges, Sequence
                ):
                    raise _fail("extracted_edges_invalid")

            self._observe("resolve_edge_pointers")
            try:
                pointer_edges = self._binding.resolve_edge_pointers(
                    list(extracted_edges), dict(uuid_map)
                )
            except Exception as error:
                raise _fail(
                    "resolve_edge_pointers_failed",
                    upstream_error_class=_qualified_error_class(error),
                ) from None
            if isinstance(pointer_edges, (str, bytes)) or not isinstance(
                pointer_edges, Sequence
            ):
                raise _fail("resolved_edge_pointers_invalid")

            self._observe("resolve_extracted_edges")
            edge_output = await self._await(
                self._binding.resolve_extracted_edges(
                    clients,
                    list(pointer_edges),
                    episode,
                    list(nodes),
                    dict(self._edge_types or {}),
                    edge_map,
                ),
                "resolve_edges_failed",
            )
            if not isinstance(edge_output, tuple) or len(edge_output) != 3:
                raise _fail("resolve_edges_result_shape_invalid")
            resolved_edges, invalidated_edges, new_edges = edge_output
            for value, code in (
                (resolved_edges, "resolved_edges_invalid"),
                (invalidated_edges, "invalidated_edges_invalid"),
                (new_edges, "new_edges_invalid"),
            ):
                if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                    raise _fail(code)

            self._observe("extract_attributes_from_nodes")
            hydrated = await self._await(
                self._binding.extract_attributes_from_nodes(
                    clients,
                    list(nodes),
                    episode,
                    list(previous),
                    self._entity_types,
                    edges=list(new_edges),
                ),
                "extract_attributes_failed",
            )
            if isinstance(hydrated, (str, bytes)) or not isinstance(hydrated, Sequence):
                raise _fail("hydrated_nodes_invalid")

            pure = artifact.pure_intermediates
            node_map = pure.get("node_episode_index_map")
            if not isinstance(node_map, Mapping):
                raise _fail("node_episode_index_map_invalid")
            self._observe("process_episode_data")
            committed = await self._await(
                self._binding.process_episode_data(
                    self._graphiti,
                    episode,
                    list(hydrated),
                    list(resolved_edges) + list(invalidated_edges),
                    logical_time,
                    source.group_id,
                    None,
                    None,
                    dict(node_map),
                ),
                "process_episode_data_failed",
            )
            if self._require_native_commit_shape:
                self._validate_commit(committed)
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None

        return MemBindV31BindObservation(
            source_sequence=source.source_sequence,
            resolved_node_count=len(nodes),
            resolved_edge_count=len(resolved_edges),
            invalidated_edge_count=len(invalidated_edges),
            commit_result_type=type(committed).__qualname__,
        )


__all__ = [
    "MemBindV31BindObservation",
    "MemBindV31GraphitiAdapter",
    "MemBindV31GraphitiAdapterError",
]
