from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s2_retrieval_probe import ProbeCounters
from paper_eval.s2_r0_live import (
    S2R0RuntimeComponents,
    build_read_only_graphiti,
    finalize_s2r0_failure,
)


class _Driver:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self.identity = (uri, user, password)
        self._init_task = None

    async def close(self) -> None:
        return None


class _Graphiti:
    def __init__(self, **kwargs: object) -> None:
        self.driver = kwargs["graph_driver"]
        self.llm_client = kwargs["llm_client"]
        self.embedder = kwargs["embedder"]
        self.cross_encoder = kwargs["cross_encoder"]


class _ForbiddenLLM:
    def __init__(self, counters: ProbeCounters) -> None:
        self.counters = counters


class _ForbiddenEmbedder(_ForbiddenLLM):
    pass


class _ForbiddenCrossEncoder(_ForbiddenLLM):
    pass


def _components() -> S2R0RuntimeComponents:
    return S2R0RuntimeComponents(
        driver_type=_Driver,
        graphiti_type=_Graphiti,
        llm_factory=_ForbiddenLLM,
        embedder_factory=_ForbiddenEmbedder,
        cross_encoder_factory=_ForbiddenCrossEncoder,
    )


def test_read_only_runtime_is_built_without_auto_schema_init_or_model_config() -> None:
    runtime = build_read_only_graphiti(
        env={
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "private",
        },
        components=_components(),
    )
    assert runtime.graphiti.driver._init_task is None
    assert runtime.graphiti.driver.identity == (
        "bolt://localhost:7687",
        "neo4j",
        "private",
    )
    assert runtime.counters == ProbeCounters()
    assert runtime.telemetry_enabled is False


@pytest.mark.asyncio
async def test_read_only_runtime_factory_rejects_active_event_loop() -> None:
    with pytest.raises(RuntimeError, match="outside an active event loop"):
        build_read_only_graphiti(
            env={
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "private",
            },
            components=_components(),
        )


def test_read_only_runtime_rejects_nonlocal_or_missing_neo4j_identity() -> None:
    for env in (
        {
            "NEO4J_URI": "bolt://remote:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "private",
        },
        {"NEO4J_URI": "bolt://localhost:7687", "NEO4J_USER": "neo4j"},
    ):
        with pytest.raises(ValueError, match="Neo4j"):
            build_read_only_graphiti(env=env, components=_components())


def test_failure_artifact_is_sanitized_and_immutable(tmp_path: Path) -> None:
    output = tmp_path / "S2_R0_FAILURE.json"
    counters = ProbeCounters(neo4j_read_requests=1, graphiti_search_calls=1)
    artifact = finalize_s2r0_failure(
        output,
        run_id="s2r0-20260814-001",
        history_id="07741c45",
        namespace="pev3-s1-20260814-001",
        error=RuntimeError("secret endpoint, query, and credential"),
        counters=counters,
        authorization_sha256="a" * 64,
        consumption_sha256="b" * 64,
        git_commit="deadbeef",
    )
    payload = artifact["payload"]
    assert payload["status"] == "FAILED_STOPPED"
    assert payload["error_class"] == "RuntimeError"
    assert payload["result_mergeable"] is False
    assert payload["s3_authorized"] is False
    assert payload["neo4j_read_requests"] == 1
    assert payload["consumption_sha256"] == "b" * 64
    serialized = json.dumps(artifact, sort_keys=True)
    assert "secret endpoint" not in serialized
    assert "credential" not in serialized

    with pytest.raises(ValueError, match="already exists"):
        finalize_s2r0_failure(
            output,
            run_id="s2r0-20260814-001",
            history_id="07741c45",
            namespace="pev3-s1-20260814-001",
            error=RuntimeError("again"),
            counters=counters,
            authorization_sha256="a" * 64,
            consumption_sha256="b" * 64,
            git_commit="deadbeef",
        )
