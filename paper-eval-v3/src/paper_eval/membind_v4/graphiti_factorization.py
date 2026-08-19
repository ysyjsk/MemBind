"""Production Graphiti 0.29.3 NodeResolve factorization for MemBind v4.

The frozen v3.1 adapter remains unchanged.  This module reads its private
configuration and delegates only to the pinned Graphiti helper surface.  A
factorized context is private to one materialized request: candidate retrieval
and deterministic resolution happen before an LLM request is captured, the
provider response is replayed into that exact context, and the native suffix
is admitted at most once.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from paper_eval.artifacts import canonical_bytes
from paper_eval.membind_v31.graphiti_adapter import MemBindV31BindObservation
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.node_resolve_adapter import (
    ExactNodeResolveResult,
    PreparedSemanticCall,
)
from paper_eval.membind_v4.semantic_call import SemanticCall


_NODE_OPERATIONS = "graphiti_core.utils.maintenance.node_operations"
_HELPERS = (
    "_collect_candidate_nodes",
    "_build_candidate_indexes",
    "_resolve_with_similarity",
    "_merge_candidate_nodes",
    "_resolve_with_llm",
    "DedupResolutionState",
)
_OPERATOR_REVISION = "graphiti-0.29.3-node-resolve-v4-1"


class V4GraphitiFactorizationError(ValueError):
    """The pinned Graphiti split or an exact private context failed closed."""


def _fail(code: str) -> V4GraphitiFactorizationError:
    return V4GraphitiFactorizationError(code)


async def _await(value: object, code: str) -> object:
    if not inspect.isawaitable(value):
        raise _fail(code)
    try:
        return await value
    except V4GraphitiFactorizationError:
        raise
    except Exception as error:
        raise _fail(code) from error


def _require_callable(owner: object, name: str) -> Callable[..., object]:
    value = getattr(owner, name, None)
    if not callable(value):
        raise _fail("graphiti_node_helper_missing")
    return value


def _projection(value: object, code: str) -> dict[str, object]:
    if isinstance(value, Mapping):
        selected: object = dict(value)
    else:
        dump = getattr(value, "model_dump", None)
        if not callable(dump):
            raise _fail(code)
        try:
            selected = dump(mode="json")
        except TypeError:
            selected = dump()
    if not isinstance(selected, Mapping):
        raise _fail(code)
    # A canonical round trip rejects non-serializable provider/private state.
    import json

    try:
        decoded = json.loads(canonical_bytes(dict(selected)).decode("utf-8"))
    except Exception:
        raise _fail(code) from None
    if not isinstance(decoded, dict):
        raise _fail(code)
    return decoded


def _node_uuid(node: object) -> str:
    value = node.get("uuid") if isinstance(node, Mapping) else getattr(node, "uuid", None)
    if not isinstance(value, str) or not value:
        raise _fail("node_uuid_missing")
    return value


def _logical_ns_to_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _fail("logical_time_invalid")
    seconds, nanos = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc) + timedelta(
        microseconds=nanos // 1_000
    )


def _coalesce(nodes: object) -> list[object]:
    if isinstance(nodes, (str, bytes)) or not isinstance(nodes, Sequence):
        raise _fail("resolved_nodes_invalid")
    selected: list[object] = []
    projections: dict[str, bytes] = {}
    for node in nodes:
        uuid = _node_uuid(node)
        projection = canonical_bytes(_projection(node, "resolved_node_projection_invalid"))
        prior = projections.get(uuid)
        if prior is None:
            projections[uuid] = projection
            selected.append(node)
        elif prior != projection:
            raise _fail("conflicting_duplicate_uuid")
    return selected


def _entity_type_names(entity_types: object) -> tuple[str, ...]:
    if entity_types is None:
        return ("Entity",)
    if not isinstance(entity_types, Mapping):
        raise _fail("entity_types_invalid")
    return ("Entity", *tuple(str(key) for key in entity_types))


@dataclass(frozen=True, slots=True)
class CapturedGraphitiRequest:
    """The exact positional and keyword arguments sent to ``generate_response``."""

    args: tuple[object, ...]
    kwargs: dict[str, object]


class _RequestCaptured(RuntimeError):
    pass


class _CaptureLLMClient:
    def __init__(self) -> None:
        self.request: CapturedGraphitiRequest | None = None

    async def generate_response(self, *args: object, **kwargs: object) -> object:
        if self.request is not None:
            raise _fail("multiple_node_resolve_llm_calls")
        self.request = CapturedGraphitiRequest(tuple(args), dict(kwargs))
        raise _RequestCaptured


class _ReplayLLMClient:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls = 0

    async def generate_response(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        if self.calls != 1:
            raise _fail("multiple_node_resolve_llm_calls")
        return self._response


@dataclass(slots=True)
class _FactorizedRequest:
    captured_request: CapturedGraphitiRequest | None
    extracted_nodes: list[object]
    candidate_nodes_by_extracted: list[list[object]]
    llm_indexes: object | None
    resolution_state: object
    episode: object
    previous: list[object]
    entity_types: object
    node_module: object
    response: object | None = None
    interpreted: tuple[list[object], dict[str, str], list[object]] | None = None
    continuation_used: bool = False


class _Factorization:
    def __init__(
        self,
        native_adapter: object,
        *,
        semantic_encoder: Callable[[CapturedGraphitiRequest], Mapping[str, object]],
        identity_metadata: Mapping[str, object],
        node_operations_loader: Callable[[], object],
    ) -> None:
        if not callable(semantic_encoder) or not callable(node_operations_loader):
            raise _fail("factorization_callback_invalid")
        if not isinstance(identity_metadata, Mapping):
            raise _fail("identity_metadata_invalid")
        operator_identity = identity_metadata.get("operator_identity")
        if (
            not isinstance(operator_identity, Mapping)
            or operator_identity.get("graphiti_version") != "0.29.3"
        ):
            raise _fail("graphiti_version_not_pinned")
        required_native = (
            "prepare",
            "bind",
            "_assert_artifact",
            "_materialize_episode",
            "_route_group_for_bind",
            "_retrieve_latest",
            "_materialize_nodes",
            "_materialize_edges",
            "_edge_type_map",
            "_observe",
            "_validate_commit",
        )
        for name in required_native:
            _require_callable(native_adapter, name)
        try:
            module = node_operations_loader()
        except Exception:
            raise _fail("graphiti_node_module_import_failed") from None
        for name in _HELPERS:
            _require_callable(module, name)
        self._native = native_adapter
        self._encoder = semantic_encoder
        self._identity = dict(identity_metadata)
        self._node_module = module

    def callbacks(self) -> dict[str, Callable[..., object]]:
        return {
            "materialize_request": self.materialize_request,
            "execute_request": self.execute_request,
            "interpret_response": self.interpret_response,
            "continue_native_bind": self.continue_native_bind,
        }

    async def materialize_request(
        self,
        compile_input: object,
        artifact: PreparedArtifact,
        state_version: int,
    ) -> PreparedSemanticCall:
        native = self._native
        try:
            native._assert_artifact(compile_input, artifact)
            source = compile_input.source
            if (
                isinstance(state_version, bool)
                or not isinstance(state_version, int)
                or state_version < 0
            ):
                raise _fail("state_version_invalid")
            episode = native._materialize_episode(
                source, code="source_episode_materialization_failed"
            )
            native._route_group_for_bind(source.group_id)
            previous = list(await native._retrieve_latest(episode, source))
            clients = getattr(getattr(native, "_graphiti", None), "clients", None)
            if clients is None:
                raise _fail("graphiti_clients_missing")
            extracted_nodes = list(native._materialize_nodes(artifact))
            candidate_rows = await _await(
                self._node_module._collect_candidate_nodes(
                    clients, list(extracted_nodes), None
                ),
                "candidate_collection_failed",
            )
            if (
                isinstance(candidate_rows, (str, bytes))
                or not isinstance(candidate_rows, Sequence)
                or len(candidate_rows) != len(extracted_nodes)
            ):
                raise _fail("candidate_collection_shape_invalid")
            candidates = [list(row) for row in candidate_rows]
            State = self._node_module.DedupResolutionState
            state = State(
                resolved_nodes=[None] * len(extracted_nodes),
                uuid_map={},
                unresolved_indices=[],
            )
            for index, (node, row) in enumerate(
                zip(extracted_nodes, candidates, strict=True)
            ):
                if not row:
                    continue
                indexes = self._node_module._build_candidate_indexes(row)
                local = State(resolved_nodes=[None], uuid_map={}, unresolved_indices=[])
                self._node_module._resolve_with_similarity([node], indexes, local)
                if local.resolved_nodes[0] is not None:
                    state.resolved_nodes[index] = local.resolved_nodes[0]
                    state.uuid_map.update(local.uuid_map)
                    state.duplicate_pairs.extend(local.duplicate_pairs)
                else:
                    state.unresolved_indices.append(index)

            captured: CapturedGraphitiRequest | None = None
            llm_indexes: object | None = None
            if state.unresolved_indices:
                flattened = [
                    candidate
                    for index in state.unresolved_indices
                    for candidate in candidates[index]
                ]
                merged = self._node_module._merge_candidate_nodes(flattened, None)
                llm_indexes = self._node_module._build_candidate_indexes(merged)
                capture_client = _CaptureLLMClient()
                try:
                    await self._node_module._resolve_with_llm(
                        capture_client,
                        list(extracted_nodes),
                        llm_indexes,
                        state,
                        episode,
                        list(previous),
                        getattr(native, "_entity_types", None),
                    )
                except _RequestCaptured:
                    captured = capture_client.request
                if captured is None:
                    raise _fail("node_resolve_request_not_captured")

            request = _FactorizedRequest(
                captured_request=captured,
                extracted_nodes=extracted_nodes,
                candidate_nodes_by_extracted=candidates,
                llm_indexes=llm_indexes,
                resolution_state=state,
                episode=episode,
                previous=previous,
                entity_types=getattr(native, "_entity_types", None),
                node_module=self._node_module,
            )
            call = self._semantic_call(source, state_version, request)
            return PreparedSemanticCall(call=call, request=request)
        except V4GraphitiFactorizationError:
            raise
        except Exception as error:
            raise _fail("node_resolve_materialization_failed") from error

    def _semantic_call(
        self, source: object, state_version: int, request: _FactorizedRequest
    ) -> SemanticCall:
        all_candidates = self._node_module._merge_candidate_nodes(
            [item for row in request.candidate_nodes_by_extracted for item in row], None
        )
        candidate_id_by_uuid = {
            _node_uuid(candidate): index for index, candidate in enumerate(all_candidates)
        }
        bindings: list[dict[str, object]] = []
        for candidate_id, candidate in enumerate(all_candidates):
            uuid = _node_uuid(candidate)
            bindings.append(
                {
                    "candidate_id": candidate_id,
                    "uuid": uuid,
                    "projection": _projection(candidate, "candidate_projection_invalid"),
                    "extracted_node_indices": [
                        index
                        for index, row in enumerate(request.candidate_nodes_by_extracted)
                        if any(_node_uuid(item) == uuid for item in row)
                    ],
                }
            )
        values: dict[str, object] = {}
        mode = "NO_LLM" if request.captured_request is None else "LLM"
        if request.captured_request is not None:
            try:
                captured_copy = CapturedGraphitiRequest(
                    tuple(deepcopy(request.captured_request.args)),
                    deepcopy(request.captured_request.kwargs),
                )
                values = dict(self._encoder(captured_copy))
            except Exception:
                raise _fail("semantic_encoding_failed") from None
        identity = self._identity
        operator_identity = identity.get("operator_identity")
        model_identity = identity.get("model_identity")
        decoding_identity = identity.get("decoding_identity")
        configured_schema = identity.get("response_schema")
        revision = identity.get("operator_revision", _OPERATOR_REVISION)
        for value, code in (
            (operator_identity, "operator_identity_missing"),
            (model_identity, "model_identity_missing"),
            (decoding_identity, "decoding_identity_missing"),
            (configured_schema, "response_schema_missing"),
        ):
            if not isinstance(value, Mapping):
                raise _fail(code)
        if not isinstance(revision, str) or not revision:
            raise _fail("operator_revision_invalid")
        response_schema = dict(configured_schema)
        if request.captured_request is not None:
            response_model = request.captured_request.kwargs.get("response_model")
            schema = getattr(response_model, "model_json_schema", None)
            if not callable(schema) or dict(schema()) != response_schema:
                raise _fail("response_schema_mismatch")
        return SemanticCall.create(
            source_sequence=source.source_sequence,
            state_version=state_version,
            operator_identity=operator_identity,
            model_identity=model_identity,
            decoding_identity=decoding_identity,
            response_schema=response_schema,
            rendered_request_sha256=values.get("rendered_request_sha256"),
            token_sequence_sha256=values.get("token_sequence_sha256"),
            prompt_tokens=values.get("prompt_tokens"),
            extracted_nodes=[
                _projection(node, "extracted_node_projection_invalid")
                for node in request.extracted_nodes
            ],
            candidate_order=list(candidate_id_by_uuid.values()),
            candidate_bindings=bindings,
            previous_episodes=[
                _projection(item, "previous_episode_projection_invalid")
                for item in request.previous
            ],
            episode_context=_projection(
                request.episode, "episode_context_projection_invalid"
            ),
            entity_types=_entity_type_names(request.entity_types),
            execution_mode=mode,
            operator_revision=revision,
        )

    async def execute_request(self, request: object) -> object:
        if not isinstance(request, _FactorizedRequest):
            raise _fail("factorized_request_invalid")
        if request.captured_request is None:
            return None
        clients = getattr(getattr(self._native, "_graphiti", None), "clients", None)
        provider = getattr(clients, "llm_client", None)
        generate = getattr(provider, "generate_response", None)
        if not callable(generate):
            raise _fail("graphiti_llm_client_missing")
        captured = request.captured_request
        return await _await(
            generate(*deepcopy(captured.args), **deepcopy(captured.kwargs)),
            "node_resolve_provider_failed",
        )

    async def interpret_response(
        self, response: object, exact_call: PreparedSemanticCall
    ) -> tuple[list[object], dict[str, str], list[object]]:
        if not isinstance(exact_call, PreparedSemanticCall):
            raise _fail("prepared_semantic_call_invalid")
        request = exact_call.request
        if not isinstance(request, _FactorizedRequest):
            raise _fail("factorized_request_invalid")
        if request.interpreted is not None:
            raise _fail("node_resolve_response_already_interpreted")
        state = request.resolution_state
        if request.captured_request is not None:
            replay = _ReplayLLMClient(response)
            await _await(
                request.node_module._resolve_with_llm(
                    replay,
                    list(request.extracted_nodes),
                    request.llm_indexes,
                    state,
                    request.episode,
                    list(request.previous),
                    request.entity_types,
                ),
                "node_resolve_response_interpretation_failed",
            )
            if replay.calls != 1:
                raise _fail("node_resolve_response_not_consumed")
        elif response is not None:
            raise _fail("no_llm_response_forbidden")
        for index, node in enumerate(request.extracted_nodes):
            if state.resolved_nodes[index] is None:
                state.resolved_nodes[index] = node
                state.uuid_map[_node_uuid(node)] = _node_uuid(node)
        resolved = list(state.resolved_nodes)
        if any(node is None for node in resolved):
            raise _fail("resolved_nodes_incomplete")
        interpreted = (resolved, dict(state.uuid_map), list(state.duplicate_pairs))
        request.response = response
        request.interpreted = interpreted
        return interpreted

    async def continue_native_bind(
        self,
        compile_input: object,
        artifact: PreparedArtifact,
        result: ExactNodeResolveResult,
        *,
        logical_time_ns: int,
    ) -> MemBindV31BindObservation:
        if not isinstance(result, ExactNodeResolveResult):
            raise _fail("exact_node_resolve_result_invalid")
        request = result.exact_call.request
        if not isinstance(request, _FactorizedRequest):
            raise _fail("factorized_request_invalid")
        if result.interpreted is not request.interpreted or request.interpreted is None:
            raise _fail("exact_interpreted_result_mismatch")
        if request.continuation_used:
            raise _fail("native_continuation_already_used")
        request.continuation_used = True
        native = self._native
        try:
            native._assert_artifact(compile_input, artifact)
            source = compile_input.source
            nodes_value, uuid_map, _duplicates = request.interpreted
            if not isinstance(uuid_map, Mapping):
                raise _fail("resolve_nodes_uuid_map_invalid")
            nodes = _coalesce(nodes_value)
            edge_map = native._edge_type_map(
                getattr(native, "_configured_edge_type_map", None),
                getattr(native, "_edge_types", None),
            )
            if getattr(native, "_compile_edges", False):
                extracted_edges = list(native._materialize_edges(artifact))
            else:
                native._observe("extract_edges")
                extracted_edges = await _await(
                    native._binding.extract_edges(
                        native._graphiti.clients,
                        request.episode,
                        list(request.extracted_nodes),
                        list(request.previous),
                        edge_map,
                        source.group_id,
                        getattr(native, "_edge_types", None),
                        getattr(native, "_custom_extraction_instructions", None),
                    ),
                    "extract_edges_failed",
                )
            if isinstance(extracted_edges, (str, bytes)) or not isinstance(
                extracted_edges, Sequence
            ):
                raise _fail("extracted_edges_invalid")
            native._observe("resolve_edge_pointers")
            pointer_edges = native._binding.resolve_edge_pointers(
                list(extracted_edges), dict(uuid_map)
            )
            if isinstance(pointer_edges, (str, bytes)) or not isinstance(
                pointer_edges, Sequence
            ):
                raise _fail("resolved_edge_pointers_invalid")
            native._observe("resolve_extracted_edges")
            edge_output = await _await(
                native._binding.resolve_extracted_edges(
                    native._graphiti.clients,
                    list(pointer_edges),
                    request.episode,
                    list(nodes),
                    dict(getattr(native, "_edge_types", None) or {}),
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
            native._observe("extract_attributes_from_nodes")
            hydrated = await _await(
                native._binding.extract_attributes_from_nodes(
                    native._graphiti.clients,
                    list(nodes),
                    request.episode,
                    list(request.previous),
                    getattr(native, "_entity_types", None),
                    edges=list(new_edges),
                ),
                "extract_attributes_failed",
            )
            if isinstance(hydrated, (str, bytes)) or not isinstance(hydrated, Sequence):
                raise _fail("hydrated_nodes_invalid")
            node_map = artifact.pure_intermediates.get("node_episode_index_map")
            if not isinstance(node_map, Mapping):
                raise _fail("node_episode_index_map_invalid")
            native._observe("process_episode_data")
            committed = await _await(
                native._binding.process_episode_data(
                    native._graphiti,
                    request.episode,
                    list(hydrated),
                    list(resolved_edges) + list(invalidated_edges),
                    _logical_ns_to_datetime(logical_time_ns),
                    source.group_id,
                    None,
                    None,
                    dict(node_map),
                ),
                "process_episode_data_failed",
            )
            if getattr(native, "_require_native_commit_shape", True):
                native._validate_commit(committed)
            return MemBindV31BindObservation(
                source_sequence=source.source_sequence,
                resolved_node_count=len(nodes),
                resolved_edge_count=len(resolved_edges),
                invalidated_edge_count=len(invalidated_edges),
                commit_result_type=type(committed).__qualname__,
            )
        except V4GraphitiFactorizationError:
            raise
        except Exception as error:
            raise _fail("native_continuation_failed") from error


def _default_node_operations_loader() -> object:
    return importlib.import_module(_NODE_OPERATIONS)


class V4GraphitiFactorizedAdapter:
    """V4-only wrapper that adds the opt-in callback surface to v3.1.

    ``prepare`` and ``bind`` remain direct delegates, so wrapping an adapter
    does not change its native behavior.  The live v4 bridge discovers
    ``v4_node_resolve_callbacks`` and uses that surface instead of calling the
    monolithic delegate.
    """

    def __init__(
        self,
        native_adapter: object,
        *,
        semantic_encoder: Callable[[CapturedGraphitiRequest], Mapping[str, object]],
        identity_metadata: Mapping[str, object],
        node_operations_loader: Callable[[], object] = _default_node_operations_loader,
    ) -> None:
        self._native_adapter = native_adapter
        self._factorization = _Factorization(
            native_adapter,
            semantic_encoder=semantic_encoder,
            identity_metadata=identity_metadata,
            node_operations_loader=node_operations_loader,
        )

    @property
    def native_adapter(self) -> object:
        return self._native_adapter

    async def prepare(self, compile_input: object) -> object:
        return await _await(
            self._native_adapter.prepare(compile_input), "native_prepare_failed"
        )

    async def bind(
        self,
        compile_input: object,
        artifact: PreparedArtifact,
        *,
        logical_time_ns: int,
    ) -> object:
        return await _await(
            self._native_adapter.bind(
                compile_input, artifact, logical_time_ns=logical_time_ns
            ),
            "native_bind_failed",
        )

    def v4_node_resolve_callbacks(self) -> dict[str, Callable[..., object]]:
        return self._factorization.callbacks()


def v4_node_resolve_callbacks(
    native_adapter: object,
    *,
    semantic_encoder: Callable[[CapturedGraphitiRequest], Mapping[str, object]],
    identity_metadata: Mapping[str, object],
    node_operations_loader: Callable[[], object] = _default_node_operations_loader,
) -> dict[str, Callable[..., object]]:
    """Return callbacks accepted by ``V4LiveNodeResolveBridge``.

    ``semantic_encoder`` owns provider-specific rendering/tokenization identity.
    It receives the exact captured Graphiti call and must return
    ``rendered_request_sha256``, ``token_sequence_sha256`` and ``prompt_tokens``.
    No encoder call is made for ``NO_LLM``.
    """

    return _Factorization(
        native_adapter,
        semantic_encoder=semantic_encoder,
        identity_metadata=identity_metadata,
        node_operations_loader=node_operations_loader,
    ).callbacks()


__all__ = [
    "CapturedGraphitiRequest",
    "V4GraphitiFactorizedAdapter",
    "V4GraphitiFactorizationError",
    "v4_node_resolve_callbacks",
]
