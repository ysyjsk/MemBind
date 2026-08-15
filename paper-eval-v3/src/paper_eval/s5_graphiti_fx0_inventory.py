"""Frozen eleven-case inventory for production-path Graphiti FX0 parity.

Each source contains only Graphiti episode data.  All controlled behavior is
declared in the provider plan, while exact expected outcomes remain separate
literal oracles consumed only by the artifact comparator.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .fx0_mechanism_fixture import (
    ControlledNondeterminism,
    Fx0FixtureCase,
    Fx0FixtureSpec,
)


_LOGICAL_TIME_0 = "2026-01-01T00:00:00Z"
_LOGICAL_TIME_1 = "2026-01-01T00:00:01Z"
_CANDIDATE_CREATED_AT = "2025-12-31T00:00:00Z"
_PUBLISH_0 = ({"source_sequence": 0, "event": "publish"},)
_PUBLISH_01 = (
    {"source_sequence": 0, "event": "publish"},
    {"source_sequence": 1, "event": "publish"},
)


def _episode(
    index: int = 0,
    *,
    content: str = "Alice works at Acme.",
    edge_types: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "uuid": f"episode-{index}",
        "name": f"episode-{index}",
        "content": content,
        "source": "text",
        "source_description": "controlled FX0 episode",
        "group_id": "controlled-db",
        "valid_at": _LOGICAL_TIME_0 if index == 0 else _LOGICAL_TIME_1,
        "edge_types": list(edge_types),
    }


def _source(*episodes: dict[str, object]) -> dict[str, object]:
    return {"episodes": [deepcopy(row) for row in episodes]}


def _responses(
    names: tuple[str, ...] = ("Alice",),
    *,
    edge_fact: str | None = None,
    contradicted: bool = False,
) -> dict[str, Any]:
    entities = [
        {"name": name, "entity_type_id": 0, "episode_indices": [0]}
        for name in names
    ]
    summaries = [
        {"name": name, "summary": f"{name} summary"} for name in names
    ]
    edges: list[dict[str, object]] = []
    if edge_fact is not None:
        edges.append(
            {
                "source_entity_name": "Alice",
                "target_entity_name": "Acme",
                "relation_type": "WorksAt",
                "fact": edge_fact,
                "valid_at": _LOGICAL_TIME_0,
                "episode_indices": [0],
            }
        )
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
            "contradicted_facts": [0] if contradicted else [],
        },
    }


def _candidate(
    uuid: str,
    name: str,
    summary: str,
) -> dict[str, object]:
    return {
        "uuid": uuid,
        "name": name,
        "group_id": "controlled-db",
        "summary": summary,
        "labels": ["Entity"],
        "created_at": _CANDIDATE_CREATED_AT,
    }


def _candidate_set(*nodes: dict[str, object]) -> dict[str, object]:
    return {"nodes": [deepcopy(node) for node in nodes]}


def _old_edge() -> dict[str, object]:
    return {
        "uuid": "old-edge",
        "group_id": "controlled-db",
        "source_node_uuid": "old-source",
        "target_node_uuid": "old-target",
        "created_at": "2025-01-01T00:00:00Z",
        "name": "WorksAt",
        "fact": "Alice previously worked at Beta.",
        "episodes": [],
        "valid_at": "2025-01-01T00:00:00Z",
    }


def _providers(
    *,
    responses: dict[str, Any] | None = None,
    logical_times: tuple[str, ...] = (_LOGICAL_TIME_0,),
    initial_state: dict[str, Any] | None = None,
    candidate_sets: tuple[dict[str, object], ...] = (),
    transaction_failures: tuple[int, ...] = (),
    publication_actions: tuple[str, ...] = ("APPEND",),
    prepare_rendezvous: bool = False,
) -> ControlledNondeterminism:
    llm_responses = deepcopy(responses or _responses())
    if prepare_rendezvous:
        llm_responses["__prepare_rendezvous_parties__"] = 2
    return ControlledNondeterminism(
        llm_responses=llm_responses,
        embeddings={"vector": [1.0, 0.0]},
        logical_times=logical_times,
        initial_state=deepcopy(initial_state or {"nodes": []}),
        candidate_sets=deepcopy(candidate_sets),
        transaction_io_schedule={
            "fail_after_callback_attempts": list(transaction_failures)
        },
        publication_sink_schedule={
            "actions_by_source": list(publication_actions)
        },
    )


_NORMAL_STATE = {
    "nodes": [
        {
            "logical_key": "name:alice",
            "name": "Alice",
            "summary": "Alice summary",
            "labels": ["Entity"],
        }
    ],
    "relationships": [],
}
_ALIAS_STATE = {
    "nodes": [
        {
            "logical_key": "canonical:canonical-alice",
            "name": "Alice",
            "summary": "Canonical Alice",
            "labels": ["Entity"],
        }
    ],
    "relationships": [],
}
_COMPATIBLE_STATE = {
    "nodes": [
        {
            "logical_key": "canonical:canonical-compatible",
            "name": "Alice",
            "summary": "Shared projection",
            "labels": ["Entity"],
        }
    ],
    "relationships": [],
}
_TWO_SOURCE_STATE = {
    "nodes": [
        deepcopy(_NORMAL_STATE["nodes"][0]),
        deepcopy(_NORMAL_STATE["nodes"][0]),
    ],
    "relationships": [],
}
_RELATION_STATE = {
    "nodes": [
        {
            "logical_key": "name:acme",
            "name": "Acme",
            "summary": "Alice works at Acme.",
            "labels": ["Entity"],
        },
        {
            "logical_key": "name:alice",
            "name": "Alice",
            "summary": "Alice works at Acme.",
            "labels": ["Entity"],
        },
    ],
    "relationships": [
        {
            "source": "name:alice",
            "target": "name:acme",
            "name": "WorksAt",
            "fact": "Alice works at Acme.",
            "episodes": ["episode-0"],
            "valid_at": "2026-01-01T00:00:00+00:00",
            "invalid_at": None,
            "reference_time": "2026-01-01T00:00:00+00:00",
        }
    ],
}
_TEMPORAL_STATE = deepcopy(_RELATION_STATE)
_TEMPORAL_STATE["relationships"].append(
    {
        "source": "external:old-source",
        "target": "external:old-target",
        "name": "WorksAt",
        "fact": "Alice previously worked at Beta.",
        "episodes": [],
        "valid_at": "2025-01-01T00:00:00+00:00",
        "invalid_at": "2026-01-01T00:00:00+00:00",
        "reference_time": None,
    }
)


def _case(
    *,
    case_id: str,
    transition: str,
    source: dict[str, object],
    providers: ControlledNondeterminism,
    expected_state: dict[str, Any],
    expected_history: tuple[dict[str, object], ...],
    expected_status: str = "PASS",
    expected_error_code: str | None = None,
) -> Fx0FixtureCase:
    return Fx0FixtureCase(
        case_id=case_id,
        transition=transition,
        source_sequence=0,
        source=source,
        providers=providers,
        expected_status=expected_status,
        expected_error_code=expected_error_code,
        expected_canonical_logical_state=deepcopy(expected_state),
        expected_publication_history=deepcopy(expected_history),
    )


def build_s5_graphiti_fx0_inventory(
    *,
    run_id: str,
    parent_protocol_sha256: str,
    amendment_sha256: str,
    current_stage_pointer_sha256: str,
    production_core_identity_sha256: str,
) -> Fx0FixtureSpec:
    """Build the frozen oracle-separated production FX0 case specification."""

    shared = _candidate(
        "canonical-compatible", "Alice", "Shared projection"
    )
    two_source = _source(
        _episode(0),
        _episode(1, content="Alice joined Acme one second later."),
    )
    cases = (
        _case(
            case_id="graphiti-entity-alias",
            transition="ENTITY_ALIAS_CANONICAL_MERGE",
            source=_source(_episode()),
            providers=_providers(
                candidate_sets=(
                    _candidate_set(
                        _candidate(
                            "canonical-alice", "Alice", "Canonical Alice"
                        )
                    ),
                )
            ),
            expected_state=_ALIAS_STATE,
            expected_history=_PUBLISH_0,
        ),
        _case(
            case_id="graphiti-compatible-duplicate",
            transition="COMPATIBLE_DUPLICATE_UUID_COALESCING",
            source=_source(_episode(content="Alice and Alicia are aliases.")),
            providers=_providers(
                responses={
                    **_responses(("Alice", "Alicia")),
                    "NodeResolutions": {
                        "entity_resolutions": [
                            {
                                "id": 0,
                                "name": "Alicia",
                                "duplicate_candidate_id": 0,
                            }
                        ]
                    },
                },
                candidate_sets=(
                    _candidate_set(shared),
                    _candidate_set(shared),
                ),
            ),
            expected_state=_COMPATIBLE_STATE,
            expected_history=_PUBLISH_0,
        ),
        _case(
            case_id="graphiti-conflicting-duplicate",
            transition="CONFLICTING_DUPLICATE_UUID_FAIL_CLOSED",
            source=_source(_episode(content="Alice and Alicia conflict.")),
            providers=_providers(
                responses=_responses(("Alice", "Alicia")),
                candidate_sets=(
                    _candidate_set(
                        _candidate(
                            "canonical-conflict", "Alice", "Projection one"
                        )
                    ),
                    _candidate_set(
                        _candidate(
                            "canonical-conflict", "Alicia", "Projection two"
                        )
                    ),
                ),
            ),
            expected_status="FAIL_CLOSED",
            expected_error_code="CONFLICTING_DUPLICATE_UUID",
            expected_state={"nodes": [], "relationships": []},
            expected_history=(),
        ),
        _case(
            case_id="graphiti-relation-resolution",
            transition="RELATION_RESOLUTION",
            source=_source(_episode(edge_types=("WorksAt",))),
            providers=_providers(
                responses=_responses(
                    ("Alice", "Acme"), edge_fact="Alice works at Acme."
                )
            ),
            expected_state=_RELATION_STATE,
            expected_history=_PUBLISH_0,
        ),
        _case(
            case_id="graphiti-temporal-invalidation",
            transition="TEMPORAL_INVALIDATION_UPDATE",
            source=_source(_episode(edge_types=("WorksAt",))),
            providers=_providers(
                responses=_responses(
                    ("Alice", "Acme"),
                    edge_fact="Alice works at Acme.",
                    contradicted=True,
                ),
                initial_state={
                    "nodes": [],
                    "invalidation_edges": [_old_edge()],
                },
            ),
            expected_state=_TEMPORAL_STATE,
            expected_history=_PUBLISH_0,
        ),
        _case(
            case_id="graphiti-prepare-bind-state-change",
            transition="PREPARE_TO_BIND_STATE_CHANGE",
            source=two_source,
            providers=_providers(
                logical_times=(_LOGICAL_TIME_0, _LOGICAL_TIME_1),
                publication_actions=("APPEND", "APPEND"),
                prepare_rendezvous=True,
            ),
            expected_state=_TWO_SOURCE_STATE,
            expected_history=_PUBLISH_01,
        ),
        _case(
            case_id="graphiti-source-ordered-publication",
            transition="SOURCE_ORDERED_PUBLICATION",
            source=two_source,
            providers=_providers(
                logical_times=(_LOGICAL_TIME_0, _LOGICAL_TIME_1),
                publication_actions=("APPEND", "APPEND"),
                prepare_rendezvous=True,
            ),
            expected_state=_TWO_SOURCE_STATE,
            expected_history=_PUBLISH_01,
        ),
        _case(
            case_id="graphiti-retry-idempotence",
            transition="RETRY_IDEMPOTENCE",
            source=_source(_episode()),
            providers=_providers(transaction_failures=(1,)),
            expected_state=_NORMAL_STATE,
            expected_history=_PUBLISH_0,
        ),
        _case(
            case_id="graphiti-lost-publication",
            transition="LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            source=_source(_episode()),
            providers=_providers(publication_actions=("DROP",)),
            expected_status="FAIL_CLOSED",
            expected_error_code="LOST_PUBLICATION",
            expected_state=_NORMAL_STATE,
            expected_history=(),
        ),
        _case(
            case_id="graphiti-duplicate-publication",
            transition="LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            source=_source(_episode()),
            providers=_providers(publication_actions=("DUPLICATE",)),
            expected_status="FAIL_CLOSED",
            expected_error_code="DUPLICATE_PUBLICATION",
            expected_state=_NORMAL_STATE,
            expected_history=(_PUBLISH_0[0], deepcopy(_PUBLISH_0[0])),
        ),
        _case(
            case_id="graphiti-partial-publication",
            transition="LOST_DUPLICATE_PARTIAL_PUBLICATION_DETECTION",
            source=two_source,
            providers=_providers(
                logical_times=(_LOGICAL_TIME_0, _LOGICAL_TIME_1),
                publication_actions=("APPEND", "DROP"),
                prepare_rendezvous=True,
            ),
            expected_status="FAIL_CLOSED",
            expected_error_code="PARTIAL_PUBLICATION",
            expected_state=_TWO_SOURCE_STATE,
            expected_history=_PUBLISH_0,
        ),
    )
    return Fx0FixtureSpec(
        run_id=run_id,
        parent_protocol_sha256=parent_protocol_sha256,
        amendment_sha256=amendment_sha256,
        current_stage_pointer_sha256=current_stage_pointer_sha256,
        production_path_identity={
            "status": "FROZEN",
            "method": "M_STAR",
            "identity_sha256": production_core_identity_sha256,
        },
        cases=cases,
    )


__all__ = ["build_s5_graphiti_fx0_inventory"]
