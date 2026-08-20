"""Deterministic, provider-free replay of one captured Graphiti NodeResolve."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass

from paper_eval.artifacts import payload_sha256
from paper_eval.membind_v4.graphiti_factorization import CapturedGraphitiRequest

from .capture import (
    CapturedBindReplay,
    graphiti_request_document,
    interpreted_effect_document,
)


class VDCDeterministicReplayError(ValueError):
    """Captured inputs could not reproduce the exact request and effect."""


def _fail(code: str) -> VDCDeterministicReplayError:
    return VDCDeterministicReplayError(code)


def _uuid(value: object) -> str:
    selected = value.get("uuid") if isinstance(value, Mapping) else getattr(value, "uuid", None)
    if not isinstance(selected, str) or not selected:
        raise _fail("node_uuid_missing")
    return selected


class _CapturedResponseClient:
    def __init__(self, capture: CapturedBindReplay) -> None:
        self._capture = capture
        self.calls = 0
        self.request_sha256: str | None = None

    async def generate_response(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        if self.calls != 1:
            raise _fail("multiple_replay_llm_calls")
        request = graphiti_request_document(
            CapturedGraphitiRequest(args=tuple(args), kwargs=dict(kwargs))
        )
        self.request_sha256 = str(request["request_sha256"])
        expected = self._capture.request
        if not isinstance(expected, dict) or request != expected:
            raise _fail("request_identity_mismatch")
        return deepcopy(self._capture.llm_response)


@dataclass(frozen=True, slots=True)
class VDCReplayResult:
    source_sequence: int
    state_version: int
    request_identity_match: bool
    effect_identity_match: bool
    request_sha256: str | None
    effect_sha256: str
    replay_sha256: str
    external_database_read_count: int = 0
    external_provider_call_count: int = 0


async def replay_captured_node_resolve(
    capture: CapturedBindReplay,
    *,
    node_factory: Callable[[dict[str, object]], object],
    episode_factory: Callable[[dict[str, object]], object],
    node_operations: object,
    entity_types: Mapping[str, object] | None = None,
) -> VDCReplayResult:
    """Replay private captured rows through the pinned deterministic/LLM suffix."""

    if not isinstance(capture, CapturedBindReplay):
        raise _fail("capture_invalid")
    capture.verify()
    if not callable(node_factory) or not callable(episode_factory):
        raise _fail("replay_factory_invalid")
    required = (
        "DedupResolutionState",
        "_build_candidate_indexes",
        "_merge_candidate_nodes",
        "_resolve_with_similarity",
        "_resolve_with_llm",
    )
    if any(not callable(getattr(node_operations, name, None)) for name in required):
        raise _fail("node_operations_invalid")
    try:
        extracted_nodes = [node_factory(deepcopy(value)) for value in capture.extracted_nodes]
        candidate_rows = [
            [node_factory(deepcopy(value)) for value in row]
            for row in capture.candidate_nodes_by_extracted
        ]
        episode = episode_factory(deepcopy(capture.episode))
        previous = [
            episode_factory(deepcopy(value)) for value in capture.previous_episodes
        ]
    except Exception:
        raise _fail("captured_runtime_materialization_failed") from None
    State = node_operations.DedupResolutionState
    state = State(
        resolved_nodes=[None] * len(extracted_nodes),
        uuid_map={},
        unresolved_indices=[],
    )
    for index, (node, row) in enumerate(
        zip(extracted_nodes, candidate_rows, strict=True)
    ):
        if not row:
            continue
        indexes = node_operations._build_candidate_indexes(row)
        local = State(resolved_nodes=[None], uuid_map={}, unresolved_indices=[])
        node_operations._resolve_with_similarity([node], indexes, local)
        if local.resolved_nodes[0] is not None:
            state.resolved_nodes[index] = local.resolved_nodes[0]
            state.uuid_map.update(local.uuid_map)
            state.duplicate_pairs.extend(local.duplicate_pairs)
        else:
            state.unresolved_indices.append(index)

    replay_client: _CapturedResponseClient | None = None
    if state.unresolved_indices:
        if capture.execution_mode != "LLM":
            raise _fail("captured_execution_mode_mismatch")
        candidates = node_operations._merge_candidate_nodes(
            [
                candidate
                for index in state.unresolved_indices
                for candidate in candidate_rows[index]
            ],
            None,
        )
        replay_client = _CapturedResponseClient(capture)
        pending = node_operations._resolve_with_llm(
            replay_client,
            list(extracted_nodes),
            node_operations._build_candidate_indexes(candidates),
            state,
            episode,
            list(previous),
            None if entity_types is None else dict(entity_types),
        )
        if not inspect.isawaitable(pending):
            raise _fail("replay_node_resolve_not_awaitable")
        await pending
        if replay_client.calls != 1:
            raise _fail("captured_response_not_consumed")
    elif capture.execution_mode != "NO_LLM":
        raise _fail("captured_execution_mode_mismatch")

    for index, node in enumerate(extracted_nodes):
        if state.resolved_nodes[index] is None:
            state.resolved_nodes[index] = node
            state.uuid_map[_uuid(node)] = _uuid(node)
    resolved = list(state.resolved_nodes)
    if any(node is None for node in resolved):
        raise _fail("replay_resolved_nodes_incomplete")
    effect = interpreted_effect_document(
        (resolved, dict(state.uuid_map), list(state.duplicate_pairs))
    )
    if effect != capture.effect:
        raise _fail("effect_identity_mismatch")
    request_sha256 = (
        None if replay_client is None else replay_client.request_sha256
    )
    expected_request_sha256 = (
        None if capture.request is None else capture.request.get("request_sha256")
    )
    request_match = request_sha256 == expected_request_sha256
    if not request_match:
        raise _fail("request_identity_mismatch")
    body = {
        "source_sequence": capture.prepared_artifact.source_sequence,
        "state_version": capture.state_version,
        "request_sha256": request_sha256,
        "effect_sha256": effect["effect_sha256"],
        "capture_sha256": capture.capture_sha256,
        "external_database_read_count": 0,
        "external_provider_call_count": 0,
    }
    return VDCReplayResult(
        source_sequence=capture.prepared_artifact.source_sequence,
        state_version=capture.state_version,
        request_identity_match=True,
        effect_identity_match=True,
        request_sha256=request_sha256,
        effect_sha256=str(effect["effect_sha256"]),
        replay_sha256=payload_sha256(body),
    )


__all__ = [
    "VDCDeterministicReplayError",
    "VDCReplayResult",
    "replay_captured_node_resolve",
]

