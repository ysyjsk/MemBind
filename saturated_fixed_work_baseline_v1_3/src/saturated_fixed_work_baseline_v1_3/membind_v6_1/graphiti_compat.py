"""Audited compatibility fixes shared by every local 8B experiment arm."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any


logger = logging.getLogger(__name__)


def _normalized_name_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _name_fingerprint(value: str) -> str:
    return hashlib.sha256(" ".join(value.split()).casefold().encode("utf-8")).hexdigest()


def _node_names_compatible(extracted_name: str, candidate_name: str) -> bool:
    """Conservatively admit exact names, aliases, and strong name extensions."""

    extracted = _normalized_name_tokens(extracted_name)
    candidate = _normalized_name_tokens(candidate_name)
    if not extracted or not candidate:
        return False
    extracted_compact = "".join(extracted)
    candidate_compact = "".join(candidate)
    if extracted_compact == candidate_compact:
        return True

    def is_ordered_subset(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
        cursor = iter(longer)
        return all(any(token == value for value in cursor) for token in shorter)

    shorter, longer = sorted((extracted, candidate), key=lambda value: (len(value), len("".join(value))))
    shorter_chars = len("".join(shorter))
    longer_chars = len("".join(longer))
    if (
        is_ordered_subset(shorter, longer)
        and shorter_chars / longer_chars >= 0.4
    ):
        return True

    extracted_set = set(extracted)
    candidate_set = set(candidate)
    token_overlap = len(extracted_set & candidate_set) / len(extracted_set | candidate_set)
    character_similarity = SequenceMatcher(None, extracted_compact, candidate_compact).ratio()
    if token_overlap >= 0.75 and character_similarity >= 0.8:
        return True
    if min(len(extracted_compact), len(candidate_compact)) >= 4 and character_similarity >= 0.9:
        return True

    def is_acronym(short_name: tuple[str, ...], full_name: tuple[str, ...]) -> bool:
        compact = "".join(short_name)
        return (
            len(short_name) == 1
            and 2 <= len(compact) <= 8
            and len(full_name) >= 2
            and compact == "".join(token[0] for token in full_name)
        )

    return is_acronym(extracted, candidate) or is_acronym(candidate, extracted)


def _emit_name_rejection(
    evidence_sink: Callable[[dict[str, Any]], None] | None,
    *,
    entity_id: int,
    candidate_id: int | None,
    extracted_name: str,
    candidate_name: str,
    resolution_path: str,
) -> None:
    if evidence_sink is None:
        return
    evidence_sink(
        {
            "event": "NODE_RESOLUTION_REJECTED",
            "reason": "candidate_name_incompatible",
            "entity_id": entity_id,
            "candidate_id": candidate_id,
            "resolution_path": resolution_path,
            "extracted_name_sha256": _name_fingerprint(extracted_name),
            "candidate_name_sha256": _name_fingerprint(candidate_name),
        }
    )


def _emit_candidate_filter(
    evidence_sink: Callable[[dict[str, Any]], None] | None,
    *,
    entity_id: int,
    extracted_name: str,
    retrieved_candidate_count: int,
    admitted_candidate_count: int,
) -> None:
    if evidence_sink is None or retrieved_candidate_count == admitted_candidate_count:
        return
    evidence_sink(
        {
            "event": "NODE_RESOLUTION_CANDIDATE_FILTER",
            "reason": "candidate_name_incompatible",
            "entity_id": entity_id,
            "retrieved_candidate_count": retrieved_candidate_count,
            "admitted_candidate_count": admitted_candidate_count,
            "filtered_candidate_count": (
                retrieved_candidate_count - admitted_candidate_count
            ),
            "resolution_path": (
                "new_entity_without_llm"
                if admitted_candidate_count == 0
                else "llm_candidate_subset"
            ),
            "extracted_name_sha256": _name_fingerprint(extracted_name),
        }
    )


async def resolve_extracted_nodes_with_candidate_provenance(
    clients: Any,
    extracted_nodes: list[Any],
    episode: Any | None = None,
    previous_episodes: list[Any] | None = None,
    entity_types: dict[str, type[Any]] | None = None,
    existing_nodes_override: list[Any] | None = None,
    *,
    evidence_sink: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[Any], dict[str, str], list[tuple[Any, Any]]]:
    """Resolve a batch without allowing candidates to leak across entities.

    Graphiti 0.29.3 retrieves candidates per extracted entity, but its batch LLM
    path flattens those candidates into one global pool. The returned
    ``duplicate_candidate_id`` is only range-checked against that global pool,
    so an entity may resolve to a candidate retrieved for another entity. This
    adapter preserves one batched LLM request while validating every selected
    candidate against the originating entity's retrieval provenance.
    """

    from graphiti_core.prompts import prompt_library
    from graphiti_core.prompts.dedupe_nodes import NodeResolutions
    from graphiti_core.utils.maintenance import node_operations as operations
    from graphiti_core.utils.maintenance.dedup_helpers import DedupResolutionState

    candidate_nodes_by_extracted = await operations._collect_candidate_nodes(
        clients,
        extracted_nodes,
        existing_nodes_override,
    )
    state = DedupResolutionState(
        resolved_nodes=[None] * len(extracted_nodes),
        uuid_map={},
        unresolved_indices=[],
    )

    admitted_candidates_by_extracted: dict[int, list[Any]] = {}
    for index, (node, candidates) in enumerate(
        zip(extracted_nodes, candidate_nodes_by_extracted, strict=True)
    ):
        if not candidates:
            continue
        local_state = DedupResolutionState(
            resolved_nodes=[None],
            uuid_map={},
            unresolved_indices=[],
        )
        operations._resolve_with_similarity(
            [node],
            operations._build_candidate_indexes(candidates),
            local_state,
        )
        if local_state.resolved_nodes[0] is not None:
            candidate = local_state.resolved_nodes[0]
            if not _node_names_compatible(node.name, candidate.name):
                _emit_name_rejection(
                    evidence_sink,
                    entity_id=index,
                    candidate_id=None,
                    extracted_name=node.name,
                    candidate_name=candidate.name,
                    resolution_path="deterministic_similarity",
                )
            else:
                operations._commit_resolution(
                    state,
                    candidate,
                    local_state.uuid_map,
                    local_state.duplicate_pairs,
                    index,
                )
                continue

        # The post-LLM guard below can never commit a name-incompatible
        # candidate. Push that exact predicate before prompt construction so
        # impossible resolutions do not inflate every later episode's dedupe
        # request. This changes neither the set of candidates that may be
        # committed nor the deterministic exact/similarity fast path above.
        admitted_candidates = [
            candidate
            for candidate in candidates
            if _node_names_compatible(node.name, candidate.name)
        ]
        _emit_candidate_filter(
            evidence_sink,
            entity_id=index,
            extracted_name=node.name,
            retrieved_candidate_count=len(candidates),
            admitted_candidate_count=len(admitted_candidates),
        )
        if not admitted_candidates:
            state.resolved_nodes[index] = node
            state.uuid_map[node.uuid] = node.uuid
            continue
        admitted_candidates_by_extracted[index] = admitted_candidates
        state.unresolved_indices.append(index)

    if state.unresolved_indices:
        candidate_union = operations._merge_candidate_nodes(
            [
                candidate
                for index in state.unresolved_indices
                for candidate in admitted_candidates_by_extracted[index]
            ],
            None,
        )
        candidate_id_by_uuid = {
            candidate.uuid: candidate_id
            for candidate_id, candidate in enumerate(candidate_union)
        }
        candidates_by_id = {
            candidate_id: candidate
            for candidate_id, candidate in enumerate(candidate_union)
        }
        allowed_ids_by_relative_index = {
            relative_index: {
                candidate_id_by_uuid[candidate.uuid]
                for candidate in admitted_candidates_by_extracted[original_index]
                if candidate.uuid in candidate_id_by_uuid
            }
            for relative_index, original_index in enumerate(state.unresolved_indices)
        }
        entity_types_dict = entity_types if entity_types is not None else {}
        extracted_context = []
        for relative_index, original_index in enumerate(state.unresolved_indices):
            node = extracted_nodes[original_index]
            extracted_context.append(
                {
                    "id": relative_index,
                    "name": node.name,
                    "entity_type": node.labels,
                    "entity_type_description": operations._get_entity_type_description(
                        node.labels,
                        entity_types_dict,
                    ),
                    "allowed_candidate_ids": sorted(
                        allowed_ids_by_relative_index[relative_index]
                    ),
                }
            )
        existing_context = [
            {
                **candidate.attributes,
                "candidate_id": candidate_id,
                "name": candidate.name,
                "entity_types": candidate.labels,
                "summary": candidate.summary[:120] if candidate.summary else "",
            }
            for candidate_id, candidate in enumerate(candidate_union)
        ]
        context = {
            "extracted_nodes": extracted_context,
            "existing_nodes": existing_context,
            "episode_content": episode.content if episode is not None else "",
            "previous_episodes": (
                [
                    {
                        "content": prior.content,
                        "timestamp": prior.valid_at.isoformat() if prior.valid_at else None,
                    }
                    for prior in previous_episodes
                ]
                if previous_episodes is not None
                else []
            ),
        }
        prompt = prompt_library.dedupe_nodes.nodes(context)
        prompt[-1].content += (
            "\n\n<CANDIDATE_PROVENANCE_CONSTRAINT>\n"
            "For each entity, duplicate_candidate_id MUST be -1 or one of that "
            "entity's allowed_candidate_ids. Candidates assigned to another "
            "entity are out of scope.\n"
            "</CANDIDATE_PROVENANCE_CONSTRAINT>\n"
        )
        response = await clients.llm_client.generate_response(
            prompt,
            response_model=NodeResolutions,
            prompt_name="dedupe_nodes.nodes",
        )
        resolutions = NodeResolutions(**response).entity_resolutions
        processed: set[int] = set()
        for resolution in resolutions:
            relative_index = int(resolution.id)
            if relative_index < 0 or relative_index >= len(state.unresolved_indices):
                if evidence_sink is not None:
                    evidence_sink(
                        {
                            "event": "NODE_RESOLUTION_REJECTED",
                            "reason": "entity_id_out_of_range",
                            "entity_id": relative_index,
                        }
                    )
                continue
            if relative_index in processed:
                if evidence_sink is not None:
                    evidence_sink(
                        {
                            "event": "NODE_RESOLUTION_REJECTED",
                            "reason": "duplicate_entity_id",
                            "entity_id": relative_index,
                        }
                    )
                continue
            processed.add(relative_index)
            original_index = state.unresolved_indices[relative_index]
            extracted_node = extracted_nodes[original_index]
            candidate_id = int(resolution.duplicate_candidate_id)
            allowed_ids = allowed_ids_by_relative_index[relative_index]
            if candidate_id < 0:
                resolved_node = extracted_node
            elif candidate_id not in allowed_ids or candidate_id not in candidates_by_id:
                resolved_node = extracted_node
                if evidence_sink is not None:
                    evidence_sink(
                        {
                            "event": "NODE_RESOLUTION_REJECTED",
                            "reason": "candidate_provenance_mismatch",
                            "entity_id": relative_index,
                            "candidate_id": candidate_id,
                            "allowed_candidate_count": len(allowed_ids),
                        }
                    )
                logger.warning(
                    "Rejected node resolution candidate %d for entity %d: candidate "
                    "was not retrieved for that entity",
                    candidate_id,
                    relative_index,
                )
            else:
                candidate = candidates_by_id[candidate_id]
                if not _node_names_compatible(extracted_node.name, candidate.name):
                    resolved_node = extracted_node
                    _emit_name_rejection(
                        evidence_sink,
                        entity_id=relative_index,
                        candidate_id=candidate_id,
                        extracted_name=extracted_node.name,
                        candidate_name=candidate.name,
                        resolution_path="llm_resolution",
                    )
                    logger.warning(
                        "Rejected node resolution candidate %d for entity %d: names "
                        "were incompatible",
                        candidate_id,
                        relative_index,
                    )
                else:
                    resolved_node = operations._promote_resolved_node(
                        extracted_node,
                        candidate,
                    )
            state.resolved_nodes[original_index] = resolved_node
            state.uuid_map[extracted_node.uuid] = resolved_node.uuid
            if resolved_node.uuid != extracted_node.uuid:
                state.duplicate_pairs.append((extracted_node, resolved_node))

    for index, node in enumerate(extracted_nodes):
        if state.resolved_nodes[index] is None:
            state.resolved_nodes[index] = node
            state.uuid_map[node.uuid] = node.uuid

    return (
        [node for node in state.resolved_nodes if node is not None],
        state.uuid_map,
        state.duplicate_pairs,
    )


def install_candidate_provenance_guard() -> tuple[Callable[[], None], list[dict[str, Any]]]:
    """Install the common Graphiti resolver guard and return its audit stream."""

    import graphiti_core.graphiti as graphiti_module
    from graphiti_core.utils import bulk_utils
    from graphiti_core.utils.maintenance import node_operations

    evidence: list[dict[str, Any]] = []
    original_operations = node_operations.resolve_extracted_nodes
    original_graphiti = graphiti_module.resolve_extracted_nodes
    original_bulk = bulk_utils.resolve_extracted_nodes

    async def guarded(*args: Any, **kwargs: Any) -> Any:
        return await resolve_extracted_nodes_with_candidate_provenance(
            *args,
            **kwargs,
            evidence_sink=evidence.append,
        )

    node_operations.resolve_extracted_nodes = guarded
    graphiti_module.resolve_extracted_nodes = guarded
    bulk_utils.resolve_extracted_nodes = guarded
    restored = False

    def restore() -> None:
        nonlocal restored
        if restored:
            return
        restored = True
        node_operations.resolve_extracted_nodes = original_operations
        graphiti_module.resolve_extracted_nodes = original_graphiti
        bulk_utils.resolve_extracted_nodes = original_bulk

    return restore, evidence


__all__ = [
    "install_candidate_provenance_guard",
    "resolve_extracted_nodes_with_candidate_provenance",
]
