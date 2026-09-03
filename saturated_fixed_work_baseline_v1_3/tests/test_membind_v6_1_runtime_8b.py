from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime import (
    LocalRuntimeConfigurationError,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b import (
    PROFILE_ID_8B,
    assert_8b_namespace_identity,
    build_8b_strict_native_runtime,
    build_8b_u0_runtime,
    close_8b_u0_runtime,
    install_empty_edge_shortcut,
    native_patch_inventory,
    runtime_8b_manifest,
)
from saturated_fixed_work_baseline_v1_3.membind_v6_1.graphiti_compat import (
    resolve_extracted_nodes_with_candidate_provenance,
)


ENDPOINTS = [
    {
        "id": "native-replica",
        "base_url": "http://127.0.0.1:18200/v1",
        "served_model": "qwen3-8b-awq",
        "physical_gpu": 0,
    },
    {
        "id": "prepare-replica",
        "base_url": "http://127.0.0.1:18201/v1",
        "served_model": "qwen3-8b-awq",
        "physical_gpu": 1,
    },
]


def canonical_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def install_environment(monkeypatch: pytest.MonkeyPatch, profile_root: Path) -> None:
    values = {
        "MEMBIND_PROFILE_ID": PROFILE_ID_8B,
        "MEMBIND_PROFILE_ROOT": str(profile_root),
        "NATIVE_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
        "NATIVE_LLM_MODEL": "qwen3-8b-awq",
        "PREPARE_LLM_BASE_URL": "http://127.0.0.1:18201/v1",
        "PREPARE_LLM_MODEL": "qwen3-8b-awq",
        "MEMBIND_NATIVE_LLM_GPU": "0",
        "MEMBIND_PREPARE_LLM_GPU": "1",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
        "MEMBIND_EMBED_GPU": "1",
        "GRAPHITI_MAX_COROUTINES": "8",
        "CONSTRUCTION_MIN_CONTEXT_TOKENS": "65536",
        "CONSTRUCTION_SDK_MAX_RETRIES": "0",
        "CONSTRUCTION_HTTP_TIMEOUT_SECONDS": "3600",
        "CONSTRUCTION_TOP_P": "1.0",
        "CONSTRUCTION_SEED": "20260806",
        "CONSTRUCTION_MAX_TOKENS": "32768",
        "CONSTRUCTION_OVERFLOW_MAX_TOKENS": "32768",
        "CONSTRUCTION_MODEL_REVISION": "fixture-revision",
        "CONSTRUCTION_LLM_API_KEY": "fixture",
        "CONSTRUCTION_CONTEXT_SAFETY_TOKENS": "32",
        "MEMBIND_LLM_MODEL_DIR": "/data/predator/ly/Mem/models/Qwen3-8B-AWQ",
        "EMBEDDING_API_KEY": "fixture",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "fixture",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def write_platform(profile_root: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "profile_id": PROFILE_ID_8B,
        "platform_status": "LIVE_VALIDATED_RESOURCE_MATCHED",
        "platform_formal_eligible": True,
        "llm_endpoints": [
            {
                **endpoint,
                "structured_outputs_config": {
                    "backend": "xgrammar",
                    "disable_any_whitespace": True,
                },
                "json_separators": [", ", ": "],
            }
            for endpoint in ENDPOINTS
        ],
        "observed_llm_capacity": {
            "native-replica": {"observed_kv_tokens": 100_000},
            "prepare-replica": {"observed_kv_tokens": 80_000},
        },
    }
    payload["payload_sha256"] = canonical_hash(payload)
    profile_root.mkdir(parents=True)
    manifest_path = profile_root / "platform.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    file_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    (profile_root / "latest.json").write_text(
        json.dumps(
            {
                "profile_id": PROFILE_ID_8B,
                "manifest_path": str(manifest_path),
                "payload_sha256": payload["payload_sha256"],
                "file_sha256": file_hash,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return payload


def route_contract() -> dict[str, object]:
    return {
        "schema_version": "membind.routing-policy.v1",
        "profile_id": PROFILE_ID_8B,
        "endpoint_set": ENDPOINTS,
        "router": {"policy": "semantic_phase_affinity"},
    }


def test_8b_manifest_authenticates_platform_and_endpoint_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "profile"
    install_environment(monkeypatch, profile_root)
    platform = write_platform(profile_root)
    result = runtime_8b_manifest(route_contract())
    assert result["profile_id"] == PROFILE_ID_8B
    assert result["platform_manifest"]["payload_sha256"] == platform["payload_sha256"]
    assert len(result["construction"]["endpoint_set"]) == 2
    assert result["construction"]["routing_policy"] == "semantic_phase_affinity"
    assert result["construction"]["structured_outputs_config"] == {
        "backend": "xgrammar",
        "disable_any_whitespace": True,
    }
    assert result["construction"]["json_whitespace_authority"] == (
        "authenticated_platform_manifest_process_contract_v1"
    )
    assert result["construction"]["json_separators"] == [", ", ": "]
    assert result["construction"]["edge_execution_policy"] == (
        "global_cap_preserving_cross_partition_pipeline_v1"
    )
    assert result["construction"]["edge_partition_workers"] == 2
    assert result["construction"]["edge_physical_page_lanes"] == 2
    assert result["construction"]["node_partition_execution_policy"] == (
        "deterministic_shared_cap_partition_pipeline_v1"
    )
    assert result["construction"]["node_partition_workers"] == 2
    assert result["construction"]["node_physical_partition_lanes"] == 2
    assert result["construction"]["edge_page_admission_policy"] == (
        "durable_frontier_source_priority_v1"
    )
    assert result["construction"]["entity_summary_policy"] == (
        "graphiti_native_batched_summary_v1"
    )
    candidate = runtime_8b_manifest(
        route_contract(), enable_grounded_summary_materialization=True
    )
    assert candidate["construction"]["entity_summary_policy"] == (
        "provenance_grounded_incremental_materialized_summary_v3"
    )


def test_8b_manifest_rejects_route_endpoint_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "profile"
    install_environment(monkeypatch, profile_root)
    write_platform(profile_root)
    contract = route_contract()
    contract["endpoint_set"] = [dict(ENDPOINTS[0]), {**ENDPOINTS[1], "base_url": "http://127.0.0.1:19999/v1"}]
    with pytest.raises(LocalRuntimeConfigurationError, match="differs from the platform"):
        runtime_8b_manifest(contract)


def test_8b_shared_manifest_exposes_one_adapter_identity_for_all_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_root = tmp_path / "profile"
    install_environment(monkeypatch, profile_root)
    write_platform(profile_root)
    manifest = runtime_8b_manifest(
        route_contract(),
        shared_bounded_structured_output=True,
        enable_endpoint_schema_grounding=True,
        enable_work_conserving_edge_admission=True,
    )
    identity = manifest["construction"]["shared_structured_output"]
    assert identity["arm_identity"] is None
    assert identity["page_capacity"] == 1
    assert identity["max_pages"] == 64
    assert identity["retry_policy"] == "single_attempt_finite_task_fail_closed_v1"
    assert identity["terminal_confirmation_is_context_retry"] is False
    assert identity["terminal_only_success_allowed"] is False
    assert identity["edge_task_protocol"] == "finite_pair_task_v1"
    assert identity["max_pages_semantics"] == "finite_task_count"
    assert identity["total_page_cap"] == 1
    assert identity["saturation_policy"] == "declared_pair_task_completion_or_fail_closed_v1"
    assert manifest["construction"]["edge_endpoint_schema_policy"] == (
        "entity_block_literal_endpoint_grounding_v1"
    )


def test_v61_build_close_then_strict_native_has_no_patch_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    import graphiti_core.graphiti as graphiti_module
    from graphiti_core.utils import bulk_utils
    from graphiti_core.utils.maintenance import node_operations

    profile_root = tmp_path / "profile"
    install_environment(monkeypatch, profile_root)
    write_platform(profile_root)
    before = {
        "node_operations": node_operations.resolve_extracted_nodes,
        "graphiti": graphiti_module.resolve_extracted_nodes,
        "bulk": bulk_utils.resolve_extracted_nodes,
    }

    # Importing/reloading the V6.1 module does not install its runtime patches.
    module = importlib.import_module(
        "saturated_fixed_work_baseline_v1_3.membind_v6_1.runtime_8b"
    )
    importlib.reload(module)
    assert node_operations.resolve_extracted_nodes is before["node_operations"]
    assert graphiti_module.resolve_extracted_nodes is before["graphiti"]
    assert bulk_utils.resolve_extracted_nodes is before["bulk"]

    v61 = build_8b_u0_runtime(routing_contract=route_contract())
    assert node_operations.resolve_extracted_nodes is not before["node_operations"]
    asyncio.run(close_8b_u0_runtime(v61))
    assert node_operations.resolve_extracted_nodes is before["node_operations"]
    assert graphiti_module.resolve_extracted_nodes is before["graphiti"]
    assert bulk_utils.resolve_extracted_nodes is before["bulk"]

    native = build_8b_strict_native_runtime(routing_contract=route_contract())
    try:
        assert type(native.llm_client) is OpenAIGenericClient
        inventory = native_patch_inventory(native)
        assert inventory["status"] == "PASS"
        assert inventory["strict_native"] is True
        assert inventory["prohibited_algorithm_patches"] == []
        for name in (
            "_membind_context_budget_restore",
            "_membind_grounded_summary_restore",
            "_membind_route_prompt_restore",
            "_membind_semantic_shortcut_restore",
            "_membind_candidate_provenance_restore",
        ):
            assert getattr(native, name, None) is None
        assert node_operations.resolve_extracted_nodes is before["node_operations"]
        assert graphiti_module.resolve_extracted_nodes is before["graphiti"]
        assert bulk_utils.resolve_extracted_nodes is before["bulk"]
    finally:
        asyncio.run(close_8b_u0_runtime(native))


def test_8b_namespace_cannot_mix_frozen_profiles() -> None:
    assert_8b_namespace_identity(f"{PROFILE_ID_8B}-smoke-c0")
    # Hexadecimal attempt ids can contain a foreign marker by coincidence;
    # those ids must not make an isolated namespace fail nondeterministically.
    assert_8b_namespace_identity(f"{PROFILE_ID_8B}-c0-native-14b3a7f2")
    assert_8b_namespace_identity(f"{PROFILE_ID_8B}-c0-native-a132b7f2")
    with pytest.raises(LocalRuntimeConfigurationError):
        assert_8b_namespace_identity("local-qwen3-14b-awq-v1-smoke")
    with pytest.raises(LocalRuntimeConfigurationError):
        assert_8b_namespace_identity(f"{PROFILE_ID_8B}-from-32b")
    with pytest.raises(LocalRuntimeConfigurationError):
        assert_8b_namespace_identity(f"{PROFILE_ID_8B}-from-fp8")


def test_empty_edge_shortcut_is_only_used_for_fewer_than_two_distinct_entities() -> None:
    import asyncio
    from types import SimpleNamespace

    calls: list[dict[str, object]] = []

    class Client:
        async def generate_response(self, messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            return {"edges": [{"fact": "fixture"}]}

    client = Client()
    restore = install_empty_edge_shortcut(client)
    try:
        one = asyncio.run(
            client.generate_response(
                [
                    SimpleNamespace(
                        content=(
                            "<ENTITIES>\n[{\"name\": \"TV Time\", "
                            "\"entity_types\": [\"Entity\"]}]\n</ENTITIES>"
                        )
                    )
                ],
                prompt_name="extract_edges.edge",
            )
        )
        two = asyncio.run(
            client.generate_response(
                [
                    {
                        "content": (
                            "<ENTITIES>\n[{\"name\": \"TV Time\"}, "
                            "{\"name\": \"Netflix\"}]\n</ENTITIES>"
                        )
                    }
                ],
                prompt_name="extract_edges.edge",
            )
        )
    finally:
        restore()
    assert one == {"edges": []}
    assert two == {"edges": [{"fact": "fixture"}]}
    assert len(calls) == 1
    assert client._membind_semantic_shortcuts[0]["distinct_entity_count"] == 1


def test_empty_edge_shortcut_parses_nested_entity_type_arrays() -> None:
    import asyncio

    calls = 0

    class Client:
        async def generate_response(self, messages, **kwargs):
            nonlocal calls
            calls += 1
            return {"edges": [{"fact": "should not be reached"}]}

    client = Client()
    restore = install_empty_edge_shortcut(client)
    try:
        result = asyncio.run(
            client.generate_response(
                [
                    {
                        "content": (
                            "<ENTITIES>\n"
                            "[{\"name\": \"TV Time\", \"entity_types\": [\"Entity\"]}]\n"
                            "</ENTITIES>"
                        )
                    }
                ],
                prompt_name="extract_edges.edge",
            )
        )
    finally:
        restore()
    assert result == {"edges": []}
    assert calls == 0


def test_node_resolution_rejects_candidate_retrieved_for_another_entity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from graphiti_core.nodes import EntityNode
    from graphiti_core.utils.maintenance import node_operations

    def node(name: str, uuid: str) -> EntityNode:
        return EntityNode(
            uuid=uuid,
            name=name,
            group_id="fixture",
            labels=["Entity"],
            summary="",
            created_at=datetime.now(UTC),
        )

    journal = node("book journal", "new-journal")
    reading_log = node("reading log", "new-reading-log")
    existing_journal = node("digital book journal", "old-journal")
    existing_reading_log = node("daily reading log", "old-reading-log")

    async def candidates(*_args, **_kwargs):
        return [[existing_journal], [existing_reading_log]]

    def never_resolve(_nodes, _indexes, state):
        state.unresolved_indices.append(0)

    monkeypatch.setattr(node_operations, "_collect_candidate_nodes", candidates)
    monkeypatch.setattr(node_operations, "_resolve_with_similarity", never_resolve)

    class LLM:
        async def generate_response(self, _prompt, **_kwargs):
            # Candidate 1 belongs only to the second extracted entity.
            return {
                "entity_resolutions": [
                    {"id": 0, "name": "book journal", "duplicate_candidate_id": 1},
                    {"id": 1, "name": "reading log", "duplicate_candidate_id": 1},
                ]
            }

    evidence: list[dict[str, object]] = []
    resolved, uuid_map, duplicates = asyncio.run(
        resolve_extracted_nodes_with_candidate_provenance(
            SimpleNamespace(llm_client=LLM()),
            [journal, reading_log],
            evidence_sink=evidence.append,
        )
    )

    assert [value.uuid for value in resolved] == ["new-journal", "old-reading-log"]
    assert uuid_map == {
        "new-journal": "new-journal",
        "new-reading-log": "old-reading-log",
    }
    assert [(left.uuid, right.uuid) for left, right in duplicates] == [
        ("new-reading-log", "old-reading-log")
    ]
    assert evidence == [
        {
            "event": "NODE_RESOLUTION_REJECTED",
            "reason": "candidate_provenance_mismatch",
            "entity_id": 0,
            "candidate_id": 1,
            "allowed_candidate_count": 1,
        }
    ]


def test_node_resolution_filters_name_incompatible_candidate_before_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from graphiti_core.nodes import EntityNode
    from graphiti_core.utils.maintenance import node_operations

    def node(name: str, uuid: str) -> EntityNode:
        return EntityNode(
            uuid=uuid,
            name=name,
            group_id="fixture",
            labels=["Entity"],
            summary="",
            created_at=datetime.now(UTC),
        )

    evernote = node("Evernote", "new-evernote")
    airbnb = node("Airbnb", "old-airbnb")

    async def candidates(*_args, **_kwargs):
        return [[airbnb]]

    def never_resolve(_nodes, _indexes, state):
        state.unresolved_indices.append(0)

    monkeypatch.setattr(node_operations, "_collect_candidate_nodes", candidates)
    monkeypatch.setattr(node_operations, "_resolve_with_similarity", never_resolve)

    class LLM:
        calls = 0

        async def generate_response(self, _prompt, **_kwargs):
            self.calls += 1
            return {
                "entity_resolutions": [
                    {"id": 0, "name": "Evernote", "duplicate_candidate_id": 0}
                ]
            }

    evidence: list[dict[str, object]] = []
    llm = LLM()
    resolved, uuid_map, duplicates = asyncio.run(
        resolve_extracted_nodes_with_candidate_provenance(
            SimpleNamespace(llm_client=llm),
            [evernote],
            evidence_sink=evidence.append,
        )
    )

    assert [value.uuid for value in resolved] == ["new-evernote"]
    assert uuid_map == {"new-evernote": "new-evernote"}
    assert duplicates == []
    assert llm.calls == 0
    assert len(evidence) == 1
    assert evidence[0]["reason"] == "candidate_name_incompatible"
    assert evidence[0]["entity_id"] == 0
    assert evidence[0]["retrieved_candidate_count"] == 1
    assert evidence[0]["admitted_candidate_count"] == 0
    assert evidence[0]["filtered_candidate_count"] == 1
    assert evidence[0]["resolution_path"] == "new_entity_without_llm"


def test_node_resolution_accepts_strong_token_preserving_name_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from graphiti_core.nodes import EntityNode
    from graphiti_core.utils.maintenance import node_operations

    def node(name: str, uuid: str) -> EntityNode:
        return EntityNode(
            uuid=uuid,
            name=name,
            group_id="fixture",
            labels=["Entity"],
            summary="",
            created_at=datetime.now(UTC),
        )

    journal = node("book journal", "new-journal")
    digital_journal = node("digital book journal", "old-journal")

    async def candidates(*_args, **_kwargs):
        return [[digital_journal]]

    def never_resolve(_nodes, _indexes, state):
        state.unresolved_indices.append(0)

    monkeypatch.setattr(node_operations, "_collect_candidate_nodes", candidates)
    monkeypatch.setattr(node_operations, "_resolve_with_similarity", never_resolve)

    class LLM:
        async def generate_response(self, _prompt, **_kwargs):
            return {
                "entity_resolutions": [
                    {"id": 0, "name": "book journal", "duplicate_candidate_id": 0}
                ]
            }

    evidence: list[dict[str, object]] = []
    resolved, uuid_map, duplicates = asyncio.run(
        resolve_extracted_nodes_with_candidate_provenance(
            SimpleNamespace(llm_client=LLM()),
            [journal],
            evidence_sink=evidence.append,
        )
    )

    assert [value.uuid for value in resolved] == ["old-journal"]
    assert uuid_map == {"new-journal": "old-journal"}
    assert [(left.uuid, right.uuid) for left, right in duplicates] == [
        ("new-journal", "old-journal")
    ]
    assert evidence == []
