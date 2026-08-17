"""Node-only Evidence-Bounded Semantic Late Binding for pinned Graphiti.

``prepare`` is intentionally capability-restricted: it receives a
``CompileInput`` made of an immutable source log and ``EvidenceFence``, gives
``extract_nodes`` a proxy exposing only ``llm_client``, and materializes every
previous episode from the fence.  It never reads a Graphiti namespace.

``bind`` is the first point at which this adapter is allowed to retrieve the
latest committed episode state.  It then preserves Graphiti's Native operation
order for the node-only relocation boundary:

    resolve nodes -> coalesce -> extract edges -> resolve pointers
      -> resolve edges -> attributes -> _process_episode_data

The module deliberately has no ``graphiti_core`` import.  Production supplies
the two tiny factories which materialize immutable source projections as
``EpisodicNode``/``EntityNode`` objects; fakes can exercise every contract
offline.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from paper_eval.artifacts import canonical_bytes
from paper_eval.membind_v1.delta import MemBindV1DeltaError, PreparedNodeArtifact
from paper_eval.membind_v1.evidence_fence import CompileInput
from paper_eval.membind_v1.source_log import SourceRecord
from paper_eval.s5_graphiti_semantic_binding import (
    S5GraphitiSemanticBinding,
    S5GraphitiSemanticBindingError,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_PREVIOUS_EPISODE_LIMIT = 10


class MemBindV1GraphitiAdapterError(ValueError):
    """A capability, durable-artifact, or upstream Graphiti contract failed."""

    def __init__(self, code: str, *, upstream_error_class: str | None = None) -> None:
        self.code = code
        self.upstream_error_class = upstream_error_class
        super().__init__(code)


def _fail(
    code: str, *, upstream_error_class: str | None = None
) -> MemBindV1GraphitiAdapterError:
    return MemBindV1GraphitiAdapterError(code, upstream_error_class=upstream_error_class)


def _qualified_error_class(error: BaseException) -> str:
    error_type = type(error)
    return f"{error_type.__module__}.{error_type.__qualname__}"


def _sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


def _logical_ns_to_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("logical_time_invalid")
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


@dataclass(frozen=True, slots=True)
class NodeArtifactIdentity:
    """Immutable identities bound into every durable node-only artifact."""

    operation_identity_sha256: str
    model_identity_sha256: str
    prompt_identity_sha256: str
    schema_identity_sha256: str
    config_identity_sha256: str

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            _sha256(getattr(self, field), f"{field}_invalid")


@dataclass(frozen=True, slots=True)
class MemBindV1BindObservation:
    """Sanitized result of the Native bind and commit suffix."""

    source_sequence: int
    resolved_node_count: int
    resolved_edge_count: int
    invalidated_edge_count: int
    commit_result_type: str


@dataclass(frozen=True, slots=True)
class _LLMOnlyClients:
    """The complete client capability supplied to evidence-bound compilation."""

    llm_client: object


EpisodeFactory = Callable[[SourceRecord], object]
ExtractedNodeFactory = Callable[[Mapping[str, object]], object]
LatestStateRetriever = Callable[[object, SourceRecord], Awaitable[Sequence[object]]]
CallObserver = Callable[[str], object]


def _identity_node_factory(node: Mapping[str, object]) -> object:
    """Use mappings directly for offline tests; production injects EntityNode."""

    return dict(node)


def _node_mapping(node: object, *, code: str) -> dict[str, object]:
    """Project a Mapping or Pydantic-like Graphiti node into durable JSON data."""

    if isinstance(node, Mapping):
        result: object = dict(node)
    else:
        model_dump = getattr(node, "model_dump", None)
        if not callable(model_dump):
            raise _fail(code)
        try:
            result = model_dump(mode="json")
        except TypeError:
            try:
                result = model_dump()
            except Exception:
                raise _fail(code) from None
        except Exception:
            raise _fail(code) from None
    if not isinstance(result, Mapping):
        raise _fail(code)
    try:
        # Round-trip through canonical JSON so Pydantic UUID/datetime values
        # cannot sneak non-durable objects into PreparedNodeArtifact.
        decoded = json.loads(canonical_bytes(dict(result)).decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return decoded


def _node_uuid(node: object) -> str:
    if isinstance(node, Mapping):
        value = node.get("uuid")
    else:
        value = getattr(node, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("resolved_node_uuid_missing")
    return value


def _coalesce_compatible_nodes(nodes: Sequence[object]) -> tuple[object, ...]:
    """Accept duplicate UUIDs only when their canonical projections agree."""

    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[object] = []
    projections_by_uuid: dict[str, bytes] = {}
    for node in nodes:
        uuid = _node_uuid(node)
        try:
            projection = canonical_bytes(
                _node_mapping(node, code="resolved_node_projection_invalid")
            )
        except MemBindV1GraphitiAdapterError:
            raise
        prior = projections_by_uuid.get(uuid)
        if prior is None:
            projections_by_uuid[uuid] = projection
            selected.append(node)
        elif prior != projection:
            raise _fail("conflicting_duplicate_uuid")
    return tuple(selected)


class MemBindV1GraphitiAdapter:
    """Execute the node-only candidate without importing or wrapping Graphiti.

    The object stores Graphiti only for the bind suffix.  ``prepare`` does not
    dereference it, which is why a fake whose ``driver`` and ``clients``
    properties raise remains a valid compile-time test fixture.
    """

    def __init__(
        self,
        *,
        graphiti: object,
        llm_client: object,
        semantic_binding: S5GraphitiSemanticBinding,
        episode_factory: EpisodeFactory,
        artifact_identity: NodeArtifactIdentity,
        extracted_node_factory: ExtractedNodeFactory = _identity_node_factory,
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
        if graphiti is None:
            raise _fail("graphiti_missing")
        if llm_client is None:
            raise _fail("llm_client_missing")
        if not isinstance(semantic_binding, S5GraphitiSemanticBinding):
            raise _fail("semantic_binding_invalid")
        if not callable(episode_factory):
            raise _fail("episode_factory_invalid")
        if not isinstance(artifact_identity, NodeArtifactIdentity):
            raise _fail("artifact_identity_invalid")
        if not callable(extracted_node_factory):
            raise _fail("extracted_node_factory_invalid")
        if latest_state_retriever is not None and not callable(latest_state_retriever):
            raise _fail("latest_state_retriever_invalid")
        if call_observer is not None and not callable(call_observer):
            raise _fail("call_observer_invalid")
        if not isinstance(require_native_commit_shape, bool):
            raise _fail("require_native_commit_shape_invalid")
        if custom_extraction_instructions is not None and not isinstance(
            custom_extraction_instructions, str
        ):
            raise _fail("custom_extraction_instructions_invalid")

        self._graphiti = graphiti
        self._compile_clients = _LLMOnlyClients(llm_client=llm_client)
        self._binding = semantic_binding
        self._episode_factory = episode_factory
        self._artifact_identity = artifact_identity
        self._extracted_node_factory = extracted_node_factory
        self._previous_episode_limit = _positive_int(
            previous_episode_limit, "previous_episode_limit_invalid"
        )
        self._entity_types = None if entity_types is None else dict(entity_types)
        self._excluded_entity_types = tuple(excluded_entity_types or ())
        self._edge_types = None if edge_types is None else dict(edge_types)
        self._configured_edge_type_map = (
            None
            if edge_type_map is None
            else {key: tuple(value) for key, value in edge_type_map.items()}
        )
        self._custom_extraction_instructions = custom_extraction_instructions
        self._latest_state_retriever = latest_state_retriever
        self._call_observer = call_observer
        self._require_native_commit_shape = require_native_commit_shape

    def _observe(self, operation: str) -> None:
        if self._call_observer is None:
            return
        try:
            self._call_observer(operation)
        except Exception:
            raise _fail("call_observer_failed") from None

    async def _await(self, value: object, code: str) -> object:
        if not inspect.isawaitable(value):
            raise _fail(code, upstream_error_class="builtins.TypeError")
        try:
            return await value
        except MemBindV1GraphitiAdapterError:
            raise
        except Exception as error:
            raise _fail(code, upstream_error_class=_qualified_error_class(error)) from None

    def _materialize_episode(self, record: SourceRecord, *, code: str) -> object:
        try:
            result = self._episode_factory(record)
        except MemBindV1GraphitiAdapterError:
            raise
        except Exception:
            raise _fail(code) from None
        if result is None:
            raise _fail(code)
        return result

    def _materialize_extracted_nodes(self, artifact: PreparedNodeArtifact) -> list[object]:
        nodes: list[object] = []
        for projection in artifact.extracted_nodes:
            try:
                node = self._extracted_node_factory(projection)
            except Exception:
                raise _fail("extracted_node_materialization_failed") from None
            if node is None:
                raise _fail("extracted_node_materialization_failed")
            nodes.append(node)
        return nodes

    def _assert_artifact_matches(
        self, compile_input: CompileInput, artifact: PreparedNodeArtifact
    ) -> None:
        if not isinstance(compile_input, CompileInput):
            raise _fail("compile_input_invalid")
        if not isinstance(artifact, PreparedNodeArtifact):
            raise _fail("prepared_artifact_invalid")
        try:
            artifact.verify()
        except MemBindV1DeltaError as error:
            raise _fail(error.args[0] if error.args else "prepared_artifact_invalid") from None
        source = compile_input.source
        evidence = compile_input.evidence
        expected = {
            "source_sequence": source.source_sequence,
            "source_sha256": source.source_sha256,
            "evidence_prefix_sha256": evidence.evidence_prefix_sha256,
            "episode_projection_sha256": source.episode_projection_sha256,
            "operation_identity_sha256": self._artifact_identity.operation_identity_sha256,
            "model_identity_sha256": self._artifact_identity.model_identity_sha256,
            "prompt_identity_sha256": self._artifact_identity.prompt_identity_sha256,
            "schema_identity_sha256": self._artifact_identity.schema_identity_sha256,
            "config_identity_sha256": self._artifact_identity.config_identity_sha256,
        }
        for field, expected_value in expected.items():
            if getattr(artifact, field) != expected_value:
                raise _fail(f"artifact_{field}_mismatch")

    @staticmethod
    def _build_edge_type_map(
        supplied: Mapping[tuple[str, str], Sequence[str]] | None,
        edge_types: Mapping[str, object] | None,
    ) -> dict[tuple[str, str], list[str]]:
        if supplied is not None:
            return {key: list(value) for key, value in supplied.items()}
        if edge_types is not None:
            return {("Entity", "Entity"): list(edge_types.keys())}
        return {("Entity", "Entity"): []}

    def _route_group_for_bind(self, group_id: str) -> None:
        """Use Native database routing, but only after the compile boundary."""

        driver = getattr(self._graphiti, "driver", None)
        clients = getattr(self._graphiti, "clients", None)
        if driver is None or clients is None or getattr(driver, "_database", None) == group_id:
            return
        clone = getattr(driver, "clone", None)
        if not callable(clone):
            raise _fail("group_database_routing_unavailable")
        try:
            routed = clone(database=group_id)
            setattr(self._graphiti, "driver", routed)
            setattr(clients, "driver", routed)
        except Exception:
            raise _fail("group_database_routing_failed") from None

    async def _retrieve_latest(self, episode: object, source: SourceRecord) -> list[object]:
        if self._latest_state_retriever is not None:
            rows = await self._await(
                self._latest_state_retriever(episode, source), "latest_state_retrieval_failed"
            )
        else:
            retrieve = getattr(self._graphiti, "retrieve_episodes", None)
            if not callable(retrieve):
                raise _fail("retrieve_episodes_missing")
            reference_time = getattr(episode, "valid_at", None)
            episode_source = getattr(episode, "source", None)
            if reference_time is None or episode_source is None:
                raise _fail("episode_retrieval_projection_invalid")
            try:
                pending = retrieve(
                    reference_time,
                    last_n=self._previous_episode_limit,
                    group_ids=[source.group_id],
                    source=episode_source,
                )
            except Exception as error:
                raise _fail(
                    "latest_state_retrieval_failed",
                    upstream_error_class=_qualified_error_class(error),
                ) from None
            rows = await self._await(pending, "latest_state_retrieval_failed")
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise _fail("latest_state_retrieval_shape_invalid")
        return list(rows)

    @staticmethod
    def _validate_native_commit_result(value: object) -> None:
        if not isinstance(value, tuple) or len(value) != 2:
            raise _fail("native_commit_result_shape_invalid")
        episodic_edges, primary_episode = value
        if not isinstance(episodic_edges, list) or not hasattr(primary_episode, "uuid"):
            raise _fail("native_commit_result_shape_invalid")

    async def prepare(self, compile_input: CompileInput) -> PreparedNodeArtifact:
        """Compile only nodes from immutable source and evidence data.

        This method intentionally contains no ``self._graphiti`` dereference.
        Any future addition that reads a driver, ``clients``, retrieval method,
        or graph state here violates the fundamental EvidenceFence contract.
        """

        if not isinstance(compile_input, CompileInput):
            raise _fail("compile_input_invalid")
        source = compile_input.source
        evidence = compile_input.evidence
        episode = self._materialize_episode(
            source, code="source_episode_materialization_failed"
        )
        previous = [
            self._materialize_episode(record, code="evidence_episode_materialization_failed")
            for record in evidence.evidence_records
        ]
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
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        if not isinstance(output, tuple) or len(output) != 2:
            raise _fail("extract_nodes_result_shape_invalid")
        extracted_nodes, node_episode_index_map = output
        if isinstance(extracted_nodes, (str, bytes)) or not isinstance(extracted_nodes, Sequence):
            raise _fail("extracted_nodes_invalid")
        if not isinstance(node_episode_index_map, Mapping):
            raise _fail("node_episode_index_map_invalid")
        projections = [
            _node_mapping(node, code="extracted_node_projection_invalid")
            for node in extracted_nodes
        ]
        try:
            return PreparedNodeArtifact.create(
                source_sequence=source.source_sequence,
                source_sha256=source.source_sha256,
                evidence_prefix_sha256=evidence.evidence_prefix_sha256,
                episode_projection_sha256=source.episode_projection_sha256,
                operation_identity_sha256=self._artifact_identity.operation_identity_sha256,
                model_identity_sha256=self._artifact_identity.model_identity_sha256,
                prompt_identity_sha256=self._artifact_identity.prompt_identity_sha256,
                schema_identity_sha256=self._artifact_identity.schema_identity_sha256,
                config_identity_sha256=self._artifact_identity.config_identity_sha256,
                extracted_nodes=projections,
                node_episode_index_map=node_episode_index_map,
            )
        except MemBindV1DeltaError as error:
            raise _fail(error.args[0] if error.args else "prepared_artifact_invalid") from None

    async def bind(
        self,
        compile_input: CompileInput,
        artifact: PreparedNodeArtifact,
        *,
        logical_time_ns: int,
    ) -> MemBindV1BindObservation:
        """Run the Native state-dependent suffix against latest committed state."""

        self._assert_artifact_matches(compile_input, artifact)
        source = compile_input.source
        logical_time = _logical_ns_to_datetime(logical_time_ns)
        episode = self._materialize_episode(
            source, code="source_episode_materialization_failed"
        )
        self._route_group_for_bind(source.group_id)
        previous_episodes = await self._retrieve_latest(episode, source)
        clients = getattr(self._graphiti, "clients", None)
        if clients is None:
            raise _fail("graphiti_clients_missing")
        extracted_nodes = self._materialize_extracted_nodes(artifact)
        edge_type_map = self._build_edge_type_map(
            self._configured_edge_type_map, self._edge_types
        )
        try:
            self._observe("resolve_extracted_nodes")
            resolved_output = await self._await(
                self._binding.resolve_extracted_nodes(
                    clients,
                    list(extracted_nodes),
                    episode,
                    list(previous_episodes),
                    self._entity_types,
                ),
                "resolve_nodes_failed",
            )
            if not isinstance(resolved_output, tuple) or len(resolved_output) != 3:
                raise _fail("resolve_nodes_result_shape_invalid")
            resolved_nodes, uuid_map, _duplicates = resolved_output
            if isinstance(resolved_nodes, (str, bytes)) or not isinstance(resolved_nodes, Sequence):
                raise _fail("resolved_nodes_invalid")
            if not isinstance(uuid_map, Mapping):
                raise _fail("resolve_nodes_uuid_map_invalid")
            nodes = _coalesce_compatible_nodes(resolved_nodes)

            # This is deliberately after node resolution, while the edge prompt
            # still receives the original extracted nodes as Graphiti does.
            self._observe("extract_edges")
            extracted_edges = await self._await(
                self._binding.extract_edges(
                    clients,
                    episode,
                    list(extracted_nodes),
                    list(previous_episodes),
                    edge_type_map,
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
            try:
                self._observe("resolve_edge_pointers")
                edges = self._binding.resolve_edge_pointers(list(extracted_edges), dict(uuid_map))
            except MemBindV1GraphitiAdapterError:
                raise
            except Exception as error:
                raise _fail(
                    "resolve_edge_pointers_failed",
                    upstream_error_class=_qualified_error_class(error),
                ) from None
            if isinstance(edges, (str, bytes)) or not isinstance(edges, Sequence):
                raise _fail("resolved_edge_pointers_invalid")

            self._observe("resolve_extracted_edges")
            edge_output = await self._await(
                self._binding.resolve_extracted_edges(
                    clients,
                    list(edges),
                    episode,
                    list(nodes),
                    dict(self._edge_types or {}),
                    edge_type_map,
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
            hydrated_nodes = await self._await(
                self._binding.extract_attributes_from_nodes(
                    clients,
                    list(nodes),
                    episode,
                    list(previous_episodes),
                    self._entity_types,
                    edges=list(new_edges),
                ),
                "extract_attributes_failed",
            )
            if isinstance(hydrated_nodes, (str, bytes)) or not isinstance(hydrated_nodes, Sequence):
                raise _fail("hydrated_nodes_invalid")

            self._observe("process_episode_data")
            committed = await self._await(
                self._binding.process_episode_data(
                    self._graphiti,
                    episode,
                    list(hydrated_nodes),
                    list(resolved_edges) + list(invalidated_edges),
                    logical_time,
                    source.group_id,
                    None,
                    None,
                    artifact.node_episode_index_map,
                ),
                "process_episode_data_failed",
            )
            if self._require_native_commit_shape:
                self._validate_native_commit_result(committed)
        except S5GraphitiSemanticBindingError:
            raise _fail("semantic_binding_failed") from None
        return MemBindV1BindObservation(
            source_sequence=source.source_sequence,
            resolved_node_count=len(nodes),
            resolved_edge_count=len(resolved_edges),
            invalidated_edge_count=len(invalidated_edges),
            commit_result_type=type(committed).__qualname__,
        )


__all__ = [
    "MemBindV1BindObservation",
    "MemBindV1GraphitiAdapter",
    "MemBindV1GraphitiAdapterError",
    "NodeArtifactIdentity",
]
