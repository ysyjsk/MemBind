"""Offline contracts for the pinned Graphiti 0.29.3 NodeResolve split."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import canonical_bytes
from paper_eval.membind_v31.graphiti_adapter import MemBindV31BindObservation
from paper_eval.membind_v31.prepared_artifact import PreparedArtifact
from paper_eval.membind_v4.graphiti_factorization import (
    V4GraphitiFactorizedAdapter,
    V4GraphitiFactorizationError,
    v4_node_resolve_callbacks,
)
from paper_eval.membind_v4.live_adapter import (
    V4LiveNodeResolveBridge,
    build_v31_graphiti_v4_bridge,
    graphiti_node_resolve_capability,
)
from paper_eval.membind_v4.node_resolve_adapter import ExactNodeResolveResult
from paper_eval.membind_v4.semantic_call import SemanticCallDecision


def _sha(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass
class _Node:
    uuid: str
    name: str
    labels: list[str] = field(default_factory=lambda: ["Entity"])
    summary: str = ""
    attributes: dict[str, object] = field(default_factory=dict)
    group_id: str = "v4-factorization-test"

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        assert mode in {"python", "json"}
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": list(self.labels),
            "summary": self.summary,
            "attributes": dict(self.attributes),
            "group_id": self.group_id,
        }


@dataclass
class _Episode:
    uuid: str
    content: str
    group_id: str = "v4-factorization-test"
    source: str = "message"
    valid_at: datetime = datetime(2026, 8, 19, tzinfo=timezone.utc)

    def model_dump(self, *, mode: str = "python") -> dict[str, object]:
        assert mode in {"python", "json"}
        return {
            "uuid": self.uuid,
            "content": self.content,
            "group_id": self.group_id,
            "source": self.source,
            "valid_at": self.valid_at.isoformat(),
        }


class _ResponseModel:
    @classmethod
    def model_json_schema(cls) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"entity_resolutions": {"type": "array"}},
            "required": ["entity_resolutions"],
        }


@dataclass
class _State:
    resolved_nodes: list[object | None]
    uuid_map: dict[str, str]
    unresolved_indices: list[int]
    duplicate_pairs: list[tuple[object, object]] = field(default_factory=list)


class _FakeNodeOperations:
    DedupResolutionState = _State

    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    async def _collect_candidate_nodes(self, clients, extracted_nodes, existing_nodes_override):
        assert existing_nodes_override is None
        self.trace.append("candidate_read")
        return [list(clients.candidates) for _node in extracted_nodes]

    @staticmethod
    def _merge_candidate_nodes(candidate_nodes, existing_nodes_override):
        merged = list(candidate_nodes) + list(existing_nodes_override or ())
        return list({node.uuid: node for node in merged}.values())

    @staticmethod
    def _build_candidate_indexes(candidates):
        return SimpleNamespace(existing_nodes=list(candidates))

    @staticmethod
    def _resolve_with_similarity(extracted_nodes, indexes, state):
        # This fixture deliberately exercises the LLM branch whenever a
        # semantic candidate exists.
        assert len(extracted_nodes) == len(state.resolved_nodes) == 1
        assert indexes.existing_nodes

    async def _resolve_with_llm(
        self,
        llm_client,
        extracted_nodes,
        indexes,
        state,
        episode,
        previous_episodes,
        entity_types,
    ):
        request = [
            {"role": "system", "content": "dedupe_nodes.nodes"},
            {
                "role": "user",
                "content": {
                    "extracted": [node.name for node in extracted_nodes],
                    "candidates": [node.uuid for node in indexes.existing_nodes],
                    "episode": episode.content,
                    "previous": [item.content for item in previous_episodes],
                    "entity_types": list((entity_types or {}).keys()),
                },
            },
        ]
        response = await llm_client.generate_response(
            request,
            response_model=_ResponseModel,
            prompt_name="dedupe_nodes.nodes",
        )
        for resolution in response["entity_resolutions"]:
            relative_id = resolution["id"]
            original_index = state.unresolved_indices[relative_id]
            extracted = extracted_nodes[original_index]
            candidate_id = resolution["duplicate_candidate_id"]
            resolved = extracted if candidate_id < 0 else indexes.existing_nodes[candidate_id]
            state.resolved_nodes[original_index] = resolved
            state.uuid_map[extracted.uuid] = resolved.uuid
            if extracted.uuid != resolved.uuid:
                state.duplicate_pairs.append((extracted, resolved))

    async def resolve_extracted_nodes(
        self, clients, extracted_nodes, episode, previous_episodes, entity_types
    ):
        candidates = await self._collect_candidate_nodes(clients, extracted_nodes, None)
        state = self.DedupResolutionState(
            resolved_nodes=[None] * len(extracted_nodes),
            uuid_map={},
            unresolved_indices=[],
        )
        for index, (node, row) in enumerate(zip(extracted_nodes, candidates, strict=True)):
            if not row:
                continue
            local = self.DedupResolutionState(
                resolved_nodes=[None], uuid_map={}, unresolved_indices=[]
            )
            self._resolve_with_similarity([node], self._build_candidate_indexes(row), local)
            if local.resolved_nodes[0] is not None:
                state.resolved_nodes[index] = local.resolved_nodes[0]
                state.uuid_map.update(local.uuid_map)
                state.duplicate_pairs.extend(local.duplicate_pairs)
            else:
                state.unresolved_indices.append(index)
        if state.unresolved_indices:
            merged = self._merge_candidate_nodes(
                [item for index in state.unresolved_indices for item in candidates[index]],
                None,
            )
            await self._resolve_with_llm(
                clients.llm_client,
                extracted_nodes,
                self._build_candidate_indexes(merged),
                state,
                episode,
                previous_episodes,
                entity_types,
            )
        for index, node in enumerate(extracted_nodes):
            if state.resolved_nodes[index] is None:
                state.resolved_nodes[index] = node
                state.uuid_map[node.uuid] = node.uuid
        return (list(state.resolved_nodes), state.uuid_map, state.duplicate_pairs)


class _Provider:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def generate_response(self, *args, **kwargs):
        self.trace.append("provider_execute")
        self.calls.append((args, kwargs))
        return {"entity_resolutions": [{"id": 0, "duplicate_candidate_id": 0}]}


class _Binding:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def resolve_edge_pointers(self, edges, uuid_map):
        self.trace.append("resolve_edge_pointers")
        return [
            {
                **edge,
                "source_node_uuid": uuid_map[edge["source_node_uuid"]],
                "target_node_uuid": uuid_map[edge["target_node_uuid"]],
            }
            for edge in edges
        ]

    async def resolve_extracted_edges(self, _clients, edges, _episode, nodes, *_args):
        self.trace.append("resolve_edges")
        return ([{"uuid": "edge-resolved", "input": edges[0]}], [], [{"uuid": "edge-new"}])

    async def extract_attributes_from_nodes(self, _clients, nodes, _episode, _previous, _types, *, edges):
        self.trace.append("attributes")
        assert edges == [{"uuid": "edge-new"}]
        return list(nodes)

    async def process_episode_data(self, _graphiti, episode, nodes, edges, *_args):
        self.trace.append("process_write")
        assert nodes and edges
        return ([], episode)


class _NativeAdapter:
    def __init__(self, node_module: _FakeNodeOperations) -> None:
        self.trace = node_module.trace
        self.provider = _Provider(self.trace)
        self._graphiti = SimpleNamespace(
            clients=SimpleNamespace(llm_client=self.provider, candidates=[])
        )
        self._binding = _Binding(self.trace)
        self._compile_edges = True
        self._entity_types = {"Person": object}
        self._edge_types = None
        self._configured_edge_type_map = None
        self._custom_extraction_instructions = None
        self._require_native_commit_shape = True
        self._continued = 0

    @property
    def state_cut_certification_sha256(self) -> str:
        return "9" * 64

    async def prepare(self, compile_input):
        return compile_input

    async def bind(self, compile_input, artifact, *, logical_time_ns):
        raise AssertionError("v3.1 monolithic bind must remain unused")

    def _assert_artifact(self, compile_input, artifact):
        self.trace.append("assert_artifact")
        assert artifact.source_sequence == compile_input.source.source_sequence

    def _materialize_episode(self, source, *, code):
        assert code == "source_episode_materialization_failed"
        return _Episode(uuid=source.episode_uuid, content="current episode")

    def _route_group_for_bind(self, group_id):
        self.trace.append("route_group")
        assert group_id == "v4-factorization-test"

    async def _retrieve_latest(self, _episode, _source):
        self.trace.append("retrieve_latest")
        return [_Episode(uuid="previous", content="previous episode")]

    def _materialize_nodes(self, artifact):
        return [_Node(**dict(node)) for node in artifact.raw_nodes]

    def _materialize_edges(self, artifact):
        return [dict(edge) for edge in artifact.raw_edges]

    @staticmethod
    def _edge_type_map(_configured, _edge_types):
        return {("Entity", "Entity"): []}

    def _observe(self, operation):
        self.trace.append(f"observe:{operation}")

    @staticmethod
    def _validate_commit(value):
        assert isinstance(value, tuple) and len(value) == 2


def _identity() -> dict[str, object]:
    return {
        "operator_identity": {
            "graphiti_version": "0.29.3",
            "helper": "graphiti_core.utils.maintenance.node_operations",
        },
        "model_identity": {"model": "fixture-model", "revision": "pinned"},
        "decoding_identity": {"temperature": 0, "seed": 7},
        "response_schema": _ResponseModel.model_json_schema(),
        "operator_revision": "graphiti-0.29.3-node-resolve-v4-1",
    }


def _encoder(captured) -> dict[str, object]:
    response_model = captured.kwargs["response_model"]
    projection = {
        "args": captured.args,
        "kwargs": {
            **captured.kwargs,
            "response_model": response_model.model_json_schema(),
        },
    }
    rendered = canonical_bytes(projection)
    return {
        "rendered_request_sha256": hashlib.sha256(rendered).hexdigest(),
        "token_sequence_sha256": hashlib.sha256(b"fixture-tokens:" + rendered).hexdigest(),
        "prompt_tokens": len(rendered),
    }


def _prepared() -> PreparedArtifact:
    return PreparedArtifact.create(
        source_sequence=1,
        source_sha256="1" * 64,
        evidence_sha256="2" * 64,
        certification_sha256="9" * 64,
        raw_nodes=({"uuid": "raw-a", "name": "Alice"},),
        raw_edges=(
            {
                "uuid": "edge-a",
                "source_node_uuid": "raw-a",
                "target_node_uuid": "raw-a",
            },
        ),
        pure_intermediates={"node_episode_index_map": {"raw-a": [0]}},
    )


def _compile_input():
    source = SimpleNamespace(
        source_sequence=1,
        source_sha256="1" * 64,
        episode_uuid="source-1",
        group_id="v4-factorization-test",
    )
    return SimpleNamespace(source=source)


def _callbacks(trace: list[str]):
    module = _FakeNodeOperations(trace)
    native = _NativeAdapter(module)
    callbacks = v4_node_resolve_callbacks(
        native,
        semantic_encoder=_encoder,
        identity_metadata=_identity(),
        node_operations_loader=lambda: module,
    )
    return native, callbacks


@pytest.mark.asyncio
async def test_materialize_is_read_only_and_execute_replays_exact_raw_request() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    native._graphiti.clients.candidates = [_Node(uuid="candidate-a", name="Alice")]

    exact = await callbacks["materialize_request"](_compile_input(), _prepared(), 4)

    assert trace == ["assert_artifact", "route_group", "retrieve_latest", "candidate_read"]
    assert native.provider.calls == []
    assert exact.call.execution_mode == "LLM"
    assert exact.call.source_sequence == 1
    assert exact.call.state_version == 4
    assert exact.call.candidate_order == (0,)
    assert exact.call.candidate_bindings[0]["uuid"] == "candidate-a"
    assert exact.call.candidate_bindings[0]["extracted_node_indices"] == [0]
    assert exact.call.extracted_nodes[0]["uuid"] == "raw-a"
    assert exact.call.previous_episodes[0]["uuid"] == "previous"
    assert exact.call.episode_context["uuid"] == "source-1"
    assert exact.call.response_schema == _ResponseModel.model_json_schema()

    captured = exact.request.captured_request
    response = await callbacks["execute_request"](exact.request)

    assert response["entity_resolutions"][0]["duplicate_candidate_id"] == 0
    assert native.provider.calls == [(captured.args, captured.kwargs)]
    assert "process_write" not in trace


@pytest.mark.asyncio
async def test_interpret_and_native_suffix_match_v31_order_and_commit_once() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    native._graphiti.clients.candidates = [_Node(uuid="candidate-a", name="Alice")]
    exact = await callbacks["materialize_request"](_compile_input(), _prepared(), 1)
    response = await callbacks["execute_request"](exact.request)
    interpreted = await callbacks["interpret_response"](response, exact)
    decision = SemanticCallDecision(
        decision="REUSE",
        reason="SEMANTIC_CALL_FINGERPRINT_MATCH",
        speculative_fingerprint=exact.call.fingerprint,
        exact_fingerprint=exact.call.fingerprint,
    )
    result = ExactNodeResolveResult(
        response=response,
        exact_call=exact,
        interpreted=interpreted,
        decision=decision,
        exact_execution_performed=False,
    )

    observation = await callbacks["continue_native_bind"](
        _compile_input(), _prepared(), result, logical_time_ns=2_000
    )

    assert observation == MemBindV31BindObservation(
        source_sequence=1,
        resolved_node_count=1,
        resolved_edge_count=1,
        invalidated_edge_count=0,
        commit_result_type="tuple",
    )
    assert trace[-8:] == [
        "observe:resolve_edge_pointers",
        "resolve_edge_pointers",
        "observe:resolve_extracted_edges",
        "resolve_edges",
        "observe:extract_attributes_from_nodes",
        "attributes",
        "observe:process_episode_data",
        "process_write",
    ]
    with pytest.raises(V4GraphitiFactorizationError, match="native_continuation_already_used"):
        await callbacks["continue_native_bind"](
            _compile_input(), _prepared(), result, logical_time_ns=2_000
        )
    assert trace.count("process_write") == 1


@pytest.mark.asyncio
async def test_factorized_node_resolve_matches_fake_native_0293_result() -> None:
    native_trace: list[str] = []
    native_module = _FakeNodeOperations(native_trace)
    native_adapter = _NativeAdapter(native_module)
    native_adapter._graphiti.clients.candidates = [
        _Node(uuid="candidate-a", name="Alice")
    ]
    native_nodes = native_adapter._materialize_nodes(_prepared())
    native_episode = native_adapter._materialize_episode(
        _compile_input().source, code="source_episode_materialization_failed"
    )
    native_previous = await native_adapter._retrieve_latest(
        native_episode, _compile_input().source
    )
    native_result = await native_module.resolve_extracted_nodes(
        native_adapter._graphiti.clients,
        native_nodes,
        native_episode,
        native_previous,
        native_adapter._entity_types,
    )

    factorized_trace: list[str] = []
    factorized_adapter, callbacks = _callbacks(factorized_trace)
    factorized_adapter._graphiti.clients.candidates = [
        _Node(uuid="candidate-a", name="Alice")
    ]
    prepared_call = await callbacks["materialize_request"](
        _compile_input(), _prepared(), 1
    )
    response = await callbacks["execute_request"](prepared_call.request)
    factorized_result = await callbacks["interpret_response"](response, prepared_call)

    assert [_projection.uuid for _projection in factorized_result[0]] == [
        _projection.uuid for _projection in native_result[0]
    ]
    assert factorized_result[1] == native_result[1]
    assert [(_node.uuid, _candidate.uuid) for _node, _candidate in factorized_result[2]] == [
        (_node.uuid, _candidate.uuid) for _node, _candidate in native_result[2]
    ]


@pytest.mark.asyncio
async def test_live_bridge_hit_uses_stale_provider_result_but_exact_cached_state() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    bridge = V4LiveNodeResolveBridge(**callbacks)
    native._graphiti.clients.candidates = [_Node(uuid="candidate-a", name="Alice")]

    await bridge.launch_speculation(_compile_input(), _prepared(), state_version=0)
    observation = await bridge.bind(
        _compile_input(), _prepared(), state_version=1, logical_time_ns=2_000
    )

    assert observation.resolved_node_count == 1
    assert bridge.telemetry()["semantic_hit_count"] == 1
    assert trace.count("provider_execute") == 1
    assert trace.count("process_write") == 1


@pytest.mark.asyncio
async def test_live_bridge_miss_executes_exact_and_commits_exact_candidate() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    bridge = V4LiveNodeResolveBridge(**callbacks)
    native._graphiti.clients.candidates = [_Node(uuid="candidate-stale", name="Alice")]
    await bridge.launch_speculation(_compile_input(), _prepared(), state_version=0)

    native._graphiti.clients.candidates = [_Node(uuid="candidate-exact", name="Alice")]
    observation = await bridge.bind(
        _compile_input(), _prepared(), state_version=1, logical_time_ns=2_000
    )

    assert observation.resolved_node_count == 1
    assert bridge.telemetry()["semantic_miss_count"] == 1
    assert trace.count("provider_execute") == 2
    assert trace.count("process_write") == 1
    process_index = trace.index("process_write")
    assert all(item != "process_write" for item in trace[:process_index])


@pytest.mark.asyncio
async def test_no_llm_path_has_no_fake_prompt_identity_or_provider_call() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    native._graphiti.clients.candidates = []

    exact = await callbacks["materialize_request"](_compile_input(), _prepared(), 3)
    assert exact.call.execution_mode == "NO_LLM"
    assert exact.call.rendered_request_sha256 is None
    assert exact.call.token_sequence_sha256 is None
    assert exact.call.prompt_tokens is None

    response = await callbacks["execute_request"](exact.request)
    interpreted = await callbacks["interpret_response"](response, exact)
    assert interpreted[0][0].uuid == "raw-a"
    assert interpreted[1] == {"raw-a": "raw-a"}
    assert native.provider.calls == []


@pytest.mark.asyncio
async def test_provider_failure_is_read_only_and_never_enters_native_suffix() -> None:
    trace: list[str] = []
    native, callbacks = _callbacks(trace)
    native._graphiti.clients.candidates = [_Node(uuid="candidate-a", name="Alice")]

    async def fail_provider(*_args, **_kwargs):
        trace.append("provider_execute")
        raise RuntimeError("offline provider")

    native.provider.generate_response = fail_provider
    bridge = V4LiveNodeResolveBridge(**callbacks)
    await bridge.launch_speculation(_compile_input(), _prepared(), state_version=0)
    with pytest.raises(Exception):
        await bridge.bind(_compile_input(), _prepared(), state_version=1, logical_time_ns=2_000)
    assert "process_write" not in trace
    assert bridge.active_speculation_count == 0


def test_factorization_fails_closed_on_unpinned_helper_surface() -> None:
    trace: list[str] = []
    native = _NativeAdapter(_FakeNodeOperations(trace))
    with pytest.raises(V4GraphitiFactorizationError, match="graphiti_node_helper_missing"):
        v4_node_resolve_callbacks(
            native,
            semantic_encoder=_encoder,
            identity_metadata=_identity(),
            node_operations_loader=lambda: SimpleNamespace(),
        )


def test_v4_wrapper_is_discoverable_without_mutating_v31_adapter() -> None:
    trace: list[str] = []
    module = _FakeNodeOperations(trace)
    native = _NativeAdapter(module)
    wrapper = V4GraphitiFactorizedAdapter(
        native,
        semantic_encoder=_encoder,
        identity_metadata=_identity(),
        node_operations_loader=lambda: module,
    )

    assert not hasattr(native, "v4_node_resolve_callbacks")
    assert graphiti_node_resolve_capability(native)["factorized"] is False
    assert graphiti_node_resolve_capability(wrapper)["factorized"] is True
    assert isinstance(build_v31_graphiti_v4_bridge(wrapper), V4LiveNodeResolveBridge)
