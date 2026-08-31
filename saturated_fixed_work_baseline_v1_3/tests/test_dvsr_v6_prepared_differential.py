"""Scientific contract for the Frozen-V6/DVSR Prepared differential."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_v6_differential import (
    DIFFERENTIAL_SCHEMA,
    build_prepared_path_evidence,
    compare_prepared_paths,
)
from saturated_fixed_work_baseline_v1_3.membind_v7.dvsr_v6_prepared_adapter import (
    PreparedExtractionBindings,
    StatefulSuffixBindings,
    install_prepared_randomness_binding,
    prepare_frozen_v6_artifact_async,
    resolve_prepared_no_reuse_async,
)


def _evidence(*, path: str) -> dict[str, object]:
    return build_prepared_path_evidence(
        path=path,
        source_workload={
            "history_id_digest": "1" * 64,
            "source_sequence": 3,
            "source_digest": "2" * 64,
            "workload_config_digest": "3" * 64,
        },
        previous_context={
            "policy": "current_evidence_only_certified_extraction_v1",
            "projection_digest": "4" * 64,
            "selection_events_digest": "5" * 64,
        },
        extraction={
            "canonical_request_sequence_digest": "6" * 64,
            "transcript_identity_digest": "7" * 64,
            "semantic_output_digest": "8" * 64,
            "logical_call_sequence_digest": "9" * 64,
            "physical_call_count": 2,
        },
        routing={
            "route_contract_digest": "a" * 64,
            "region_sequence_digest": "b" * 64,
        },
        execution_binding={
            "uuid_time_randomness_digest": "c" * 64,
        },
        stateful={
            "canonical_request_sequence_digest": "d" * 64,
            "logical_call_sequence_digest": "e" * 64,
            "db_read_inventory_digest": "f" * 64,
        },
        continuation_k_digest="0" * 64,
        canonical_graph_projection_digest="a1" * 32,
        publication_order_digest="b2" * 32,
        no_prepublication_write=True,
        runtime_metadata={
            "observer_enabled": path == "DVSR_PREPARED_NOREUSE",
            "runtime_instance_id": f"runtime-{path}",
        },
    )


def test_identical_semantic_paths_are_exact() -> None:
    frozen = _evidence(path="FROZEN_V6")
    dvsr = _evidence(path="DVSR_PREPARED_NOREUSE")

    result = compare_prepared_paths(frozen, dvsr)

    assert result["schema_version"] == DIFFERENTIAL_SCHEMA
    assert result["status"] == "EXPLAINED_NON_SEMANTIC_DIFFERENCE"
    assert result["semantic_mismatches"] == []
    assert {row["field"] for row in result["explained_non_semantic_differences"]} == {
        "runtime_metadata.observer_enabled",
        "runtime_metadata.runtime_instance_id",
    }


@pytest.mark.parametrize(
    "field_path,replacement",
    [
        ("previous_context.policy", "native-history-window"),
        ("previous_context.projection_digest", "f" * 64),
        ("extraction.canonical_request_sequence_digest", "e" * 64),
        ("extraction.transcript_identity_digest", "d" * 64),
        ("extraction.semantic_output_digest", "c" * 64),
        ("extraction.physical_call_count", 3),
        ("stateful.db_read_inventory_digest", "b" * 64),
        ("continuation_k_digest", "9" * 64),
        ("canonical_graph_projection_digest", "8" * 64),
        ("publication_order_digest", "7" * 64),
    ],
)
def test_semantic_difference_blocks_g1(field_path: str, replacement: object) -> None:
    frozen = _evidence(path="FROZEN_V6")
    dvsr = deepcopy(_evidence(path="DVSR_PREPARED_NOREUSE"))
    parent, leaf = field_path.split(".", 1) if "." in field_path else (None, field_path)
    if parent is None:
        dvsr[leaf] = replacement
    else:
        dvsr[parent][leaf] = replacement  # type: ignore[index]

    result = compare_prepared_paths(frozen, dvsr)

    assert result["status"] == "SEMANTIC_MISMATCH"
    assert field_path in {row["field"] for row in result["semantic_mismatches"]}
    assert result["g1_eligible"] is False


def test_missing_required_field_is_not_explainable() -> None:
    frozen = _evidence(path="FROZEN_V6")
    dvsr = deepcopy(_evidence(path="DVSR_PREPARED_NOREUSE"))
    del dvsr["stateful"]["db_read_inventory_digest"]  # type: ignore[index]

    result = compare_prepared_paths(frozen, dvsr)

    assert result["status"] == "SEMANTIC_MISMATCH"
    assert "stateful.db_read_inventory_digest" in result["missing_required_fields"]
    assert result["g1_eligible"] is False


def test_equal_paths_without_runtime_difference_are_exact() -> None:
    frozen = _evidence(path="FROZEN_V6")
    dvsr = deepcopy(frozen)
    dvsr["path"] = "DVSR_PREPARED_NOREUSE"

    result = compare_prepared_paths(frozen, dvsr)

    assert result["status"] == "EXACT"
    assert result["g1_eligible"] is True


def test_raw_payload_and_unknown_runtime_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="runtime metadata field"):
        build_prepared_path_evidence(
            **{
                key: value
                for key, value in _evidence(path="FROZEN_V6").items()
                if key not in {"schema_version", "path", "evidence_digest", "runtime_metadata"}
            },
            path="FROZEN_V6",
            runtime_metadata={"raw_prompt": "private content"},
        )


def test_no_prepublication_write_is_a_hard_semantic_mismatch() -> None:
    frozen = _evidence(path="FROZEN_V6")
    dvsr = deepcopy(_evidence(path="DVSR_PREPARED_NOREUSE"))
    dvsr["no_prepublication_write"] = False

    result = compare_prepared_paths(frozen, dvsr)

    assert result["status"] == "SEMANTIC_MISMATCH"
    assert "no_prepublication_write" in {
        row["field"] for row in result["semantic_mismatches"]
    }


@pytest.mark.asyncio
async def test_adapter_materializes_only_frozen_v6_node_and_edge_extraction() -> None:
    calls: list[str] = []

    async def extract_nodes(_clients, episode, previous, _kwargs):
        calls.append("extract_nodes")
        assert episode["uuid"] == "episode-fixed"
        assert previous == ({"uuid": "previous-fixed"},)
        return [{"uuid": "node-fixed", "name": "Alice"}], {"node-fixed": [0]}

    async def extract_edges(_clients, episode, nodes, previous, _kwargs):
        calls.append("extract_edges")
        assert episode["uuid"] == "episode-fixed"
        assert nodes[0]["uuid"] == "node-fixed"
        assert previous == ({"uuid": "previous-fixed"},)
        return [{"uuid": "edge-fixed", "fact": "knows"}]

    artifact = await prepare_frozen_v6_artifact_async(
        clients=object(),
        source_sequence=4,
        source_workload_digest="1" * 64,
        episode={"uuid": "episode-fixed"},
        previous_episodes=({"uuid": "previous-fixed"},),
        episode_kwargs={"group_id": "group"},
        provider_transcript_digest="2" * 64,
        request_sequence_digest="3" * 64,
        previous_context_policy="current_evidence_only_certified_extraction_v1",
        previous_context_digest="4" * 64,
        physical_extraction_call_count=9,
        bindings=PreparedExtractionBindings(extract_nodes, extract_edges),
    )

    assert calls == ["extract_nodes", "extract_edges"]
    assert artifact.logical_extraction_call_count == 2
    assert artifact.physical_extraction_call_count == 9
    assert artifact.extracted_nodes[0]["uuid"] == "node-fixed"
    assert artifact.extracted_edges[0]["uuid"] == "edge-fixed"
    assert len(artifact.semantic_output_digest) == 64


@pytest.mark.asyncio
async def test_adapter_clones_one_prepared_artifact_and_never_reextracts() -> None:
    calls: list[str] = []
    prepared = await prepare_frozen_v6_artifact_async(
        clients=object(),
        source_sequence=0,
        source_workload_digest="1" * 64,
        episode={"uuid": "episode", "created_at": "fixed"},
        previous_episodes=(),
        episode_kwargs={"group_id": "group"},
        provider_transcript_digest="2" * 64,
        request_sequence_digest="3" * 64,
        previous_context_policy="current_evidence_only_certified_extraction_v1",
        previous_context_digest="4" * 64,
        bindings=PreparedExtractionBindings(
            lambda *_args: ([{"uuid": "n1", "name": "Alice"}], {"n1": [0]}),
            lambda *_args: [{"uuid": "e1", "source_node_uuid": "n1", "target_node_uuid": "n1"}],
        ),
    )

    async def resolve_nodes(_clients, nodes, _episode, _previous, _kwargs):
        calls.append("resolve_nodes")
        nodes[0]["name"] = "mutated-clone"
        return nodes, {"n1": "resolved-n1"}, []

    async def resolve_edges(_clients, edges, _episode, nodes, uuid_map, _kwargs):
        calls.append("resolve_edges")
        assert edges[0]["uuid"] == "e1"
        assert nodes[0]["name"] == "mutated-clone"
        assert uuid_map == {"n1": "resolved-n1"}
        return edges, [], edges

    async def hydrate(_clients, nodes, _episode, _previous, _new_edges, _kwargs):
        calls.append("hydrate")
        return nodes

    result = await resolve_prepared_no_reuse_async(
        clients=object(),
        artifact=prepared,
        episode_kwargs={"group_id": "group"},
        publication_frontier=0,
        backend_epoch="backend",
        read_epoch="state-0",
        bindings=StatefulSuffixBindings(
            resolve_nodes=resolve_nodes,
            resolve_edges=resolve_edges,
            hydrate_nodes=hydrate,
            build_continuation=lambda **kwargs: kwargs,
        ),
    )

    assert calls == ["resolve_nodes", "resolve_edges", "hydrate"]
    assert prepared.extracted_nodes[0]["name"] == "Alice"
    assert result.database_writes == 0
    assert result.prepared_artifact_digest == prepared.artifact_digest
    assert result.continuation_k["nodes"][0]["name"] == "mutated-clone"


@pytest.mark.asyncio
async def test_randomness_binding_executes_replay_then_restores_prepared_ids_and_time() -> None:
    prepared = await prepare_frozen_v6_artifact_async(
        clients=object(),
        source_sequence=0,
        source_workload_digest="1" * 64,
        episode={"uuid": "episode-fixed", "created_at": "time-fixed", "name": "episode"},
        previous_episodes=(),
        episode_kwargs={"group_id": "group"},
        provider_transcript_digest="2" * 64,
        request_sequence_digest="3" * 64,
        previous_context_policy="current_evidence_only_certified_extraction_v1",
        previous_context_digest="4" * 64,
        bindings=PreparedExtractionBindings(
            lambda *_args: (
                [{"uuid": "node-fixed", "created_at": "time-fixed", "name": "Alice"}],
                {"node-fixed": [0]},
            ),
            lambda *_args: [
                {
                    "uuid": "edge-fixed",
                    "created_at": "time-fixed",
                    "source_node_uuid": "node-fixed",
                    "target_node_uuid": "node-fixed",
                    "fact": "knows",
                }
            ],
        ),
    )
    calls: list[str] = []

    async def extract_nodes(*_args, **_kwargs):
        calls.append("nodes-replay")
        return (
            [{"uuid": "node-random", "created_at": "time-random", "name": "Alice"}],
            {"node-random": [0]},
        )

    async def extract_edges(*_args, **_kwargs):
        calls.append("edges-replay")
        return [
            {
                "uuid": "edge-random",
                "created_at": "time-random",
                "source_node_uuid": "node-fixed",
                "target_node_uuid": "node-fixed",
                "fact": "knows",
            }
        ]

    class Episode:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    module = SimpleNamespace(
        extract_nodes=extract_nodes,
        extract_edges=extract_edges,
        EpisodicNode=Episode,
        utc_now=lambda: "time-random",
    )
    restore = install_prepared_randomness_binding(module, prepared)
    try:
        nodes, index_map = await module.extract_nodes()
        edges = await module.extract_edges()
        episode = module.EpisodicNode(name="episode", created_at="time-random")

        assert calls == ["nodes-replay", "edges-replay"]
        assert nodes == list(prepared.extracted_nodes)
        assert edges == list(prepared.extracted_edges)
        assert index_map == prepared.node_episode_index_map
        assert episode.uuid == "episode-fixed"
        assert episode.created_at == "time-fixed"
        assert module.utc_now() == "time-fixed"
    finally:
        restore()

    assert module.extract_nodes is extract_nodes
    assert module.extract_edges is extract_edges
    assert module.EpisodicNode is Episode


@pytest.mark.asyncio
async def test_randomness_binding_fails_closed_on_semantic_replay_change() -> None:
    prepared = await prepare_frozen_v6_artifact_async(
        clients=object(),
        source_sequence=0,
        source_workload_digest="1" * 64,
        episode={"uuid": "episode-fixed", "created_at": "time-fixed"},
        previous_episodes=(),
        episode_kwargs={"group_id": "group"},
        provider_transcript_digest="2" * 64,
        request_sequence_digest="3" * 64,
        previous_context_policy="current_evidence_only_certified_extraction_v1",
        previous_context_digest="4" * 64,
        bindings=PreparedExtractionBindings(
            lambda *_args: ([{"uuid": "node-fixed", "name": "Alice"}], {"node-fixed": [0]}),
            lambda *_args: [],
        ),
    )
    module = SimpleNamespace(
        extract_nodes=lambda *_args: ([{"uuid": "random", "name": "Bob"}], {"random": [0]}),
        extract_edges=lambda *_args: [],
        EpisodicNode=lambda **kwargs: kwargs,
        utc_now=lambda: "random",
    )
    restore = install_prepared_randomness_binding(module, prepared)
    try:
        with pytest.raises(ValueError, match="node semantic output changed"):
            await module.extract_nodes()
    finally:
        restore()


@pytest.mark.asyncio
async def test_no_reuse_suffix_accepts_authoritative_previous_window() -> None:
    prepared = await prepare_frozen_v6_artifact_async(
        clients=object(),
        source_sequence=0,
        source_workload_digest="1" * 64,
        episode={"uuid": "episode"},
        previous_episodes=({"uuid": "prepared-previous"},),
        episode_kwargs={"group_id": "group"},
        provider_transcript_digest="2" * 64,
        request_sequence_digest="3" * 64,
        previous_context_policy="current_evidence_only_certified_extraction_v1",
        previous_context_digest="4" * 64,
        bindings=PreparedExtractionBindings(
            lambda *_args: ([], {}),
            lambda *_args: [],
        ),
    )
    seen: list[object] = []

    async def resolve_nodes(_clients, nodes, _episode, previous, _kwargs):
        seen.extend(previous)
        return nodes, {}, []

    await resolve_prepared_no_reuse_async(
        clients=object(),
        artifact=prepared,
        episode_kwargs={"group_id": "group"},
        publication_frontier=0,
        backend_epoch="backend",
        read_epoch="state-0",
        authoritative_previous_episodes=({"uuid": "authoritative-previous"},),
        bindings=StatefulSuffixBindings(
            resolve_nodes=resolve_nodes,
            resolve_edges=lambda *_args: ([], [], []),
            hydrate_nodes=lambda *_args: [],
            build_continuation=lambda **kwargs: kwargs,
        ),
    )

    assert seen == [{"uuid": "authoritative-previous"}]
