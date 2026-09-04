from __future__ import annotations

import json

from mab_quality_v2_final_qa.runtime_gate import (
    RuntimeEndpoint,
    RuntimeTopology,
    classify_probe_error,
)


def test_project_topology_uses_frozen_remote_ports_not_local_placeholders() -> None:
    topology = RuntimeTopology.from_env(
        {
            "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
            "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
            "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
            "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
            "NEO4J_URI": "bolt://localhost:7687",
        }
    )

    assert topology.construction == RuntimeEndpoint(
        "construction", "http://10.87.5.247:8000/v1", "qwen3-32b-fp8"
    )
    assert topology.embedding == RuntimeEndpoint(
        "embedding", "http://10.87.5.247:8001/v1", "qwen3-embedding-0.6b"
    )
    assert topology.neo4j_uri == "bolt://localhost:7687"
    assert "8002" not in json.dumps(topology.public_identity())
    assert "8003" not in json.dumps(topology.public_identity())


def test_probe_failure_class_distinguishes_sandbox_from_connection_refused() -> None:
    assert (
        classify_probe_error(PermissionError(1, "Operation not permitted"))
        == "EXECUTION_SANDBOX_NETWORK_ISOLATION"
    )
    assert (
        classify_probe_error(ConnectionRefusedError(111, "Connection refused"))
        == "ENDPOINT_CONNECTION_REFUSED"
    )


def test_local_dual_replica_topology_is_model_neutral_but_route_strict() -> None:
    topology = RuntimeTopology.from_env(
        {
            "MAB_RUNTIME_PROVIDER": "LOCAL_DUAL_REPLICA",
            "CONSTRUCTION_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
            "CONSTRUCTION_LLM_MODEL": "qwen3-14b-awq",
            "QUALITY_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
            "QUALITY_LLM_MODEL": "qwen3-14b-awq",
            "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
            "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
            "EMBEDDING_DIM": "1024",
            "NEO4J_URI": "bolt://127.0.0.1:7687",
        }
    )

    assert topology.provider == "LOCAL_DUAL_REPLICA"
    assert topology.construction.model == "qwen3-14b-awq"
    assert topology.quality == topology.construction.__class__(
        "quality", "http://127.0.0.1:18200/v1", "qwen3-14b-awq"
    )


def test_local_dual_replica_topology_rejects_quality_model_drift() -> None:
    env = {
        "MAB_RUNTIME_PROVIDER": "LOCAL_DUAL_REPLICA",
        "CONSTRUCTION_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
        "CONSTRUCTION_LLM_MODEL": "qwen3-14b-awq",
        "QUALITY_LLM_BASE_URL": "http://127.0.0.1:18200/v1",
        "QUALITY_LLM_MODEL": "qwen3-8b-awq",
        "EMBEDDING_BASE_URL": "http://127.0.0.1:18202/v1",
        "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
        "EMBEDDING_DIM": "1024",
        "NEO4J_URI": "bolt://127.0.0.1:7687",
    }

    try:
        RuntimeTopology.from_env(env)
    except ValueError as exc:
        assert str(exc) == "LOCAL_DUAL_REPLICA_QUALITY_ENDPOINT_DRIFT"
    else:
        raise AssertionError("quality model drift was accepted")
