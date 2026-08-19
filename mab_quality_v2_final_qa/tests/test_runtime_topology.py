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
