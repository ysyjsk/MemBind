"""Offline tests for lazy, read-only S5 production preflight collection."""

from __future__ import annotations

import asyncio

import pytest

from paper_eval.s5_live_preflight_production import (
    S5LivePreflightProductionError,
    execute_s5_live_preflight_production,
)
from tests.test_s5_live_preflight import (
    POINTER_FILE_SHA256,
    QUALIFICATION_FILE_SHA256,
    _identity,
    _pointer,
    _qualification,
)


SOURCE_SHA256S = tuple(f"{index + 1:064x}" for index in range(49))


class _Probe:
    def __init__(self) -> None:
        self.connectivity_calls = 0
        self.namespace_calls: list[str] = []
        self.close_calls = 0

    async def connectivity(self) -> bool:
        self.connectivity_calls += 1
        return True

    async def namespace_state(self, namespace: str) -> dict[str, int]:
        self.namespace_calls.append(namespace)
        return {"node_count": 0, "relationship_count": 0}

    async def close(self) -> None:
        self.close_calls += 1


def test_production_collection_uses_injected_read_only_dependencies_and_closes() -> None:
    http_calls: list[tuple[str, str]] = []
    probe = _Probe()

    async def get_json(base_url: str, path: str) -> dict[str, object]:
        http_calls.append((base_url, path))
        if path == "/version":
            return {"version": "0.26.0"}
        if "8001" in base_url:
            return {"data": [{"id": "qwen3-embedding-0.6b"}]}
        return {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 65536}]}

    result = asyncio.run(
        execute_s5_live_preflight_production(
            method="A0",
            run_id="s5-a0-production-test",
            namespace="pev3-s5-a0-production-test",
            episode_source_sha256s=SOURCE_SHA256S,
            production_identity_qualification=_qualification("A0"),
            production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
            current_stage_pointer=_pointer(),
            current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
            env={
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "ignored-test-secret",
                "CONSTRUCTION_LLM_API_KEY": "",
                "EMBEDDING_API_KEY": "",
            },
            get_json=get_json,
            neo4j_probe=probe,
        )
    )

    assert result["verdict"] == "PASS"
    assert probe.connectivity_calls == 1
    assert probe.namespace_calls == ["pev3-s5-a0-production-test"]
    assert probe.close_calls == 1
    assert http_calls == [
        ("http://10.87.5.247:8000/v1/", "/models"),
        ("http://10.87.5.247:8000", "/version"),
        ("http://10.87.5.247:8001/v1", "/models"),
    ]


def test_production_collection_rejects_partial_injection_without_network() -> None:
    with pytest.raises(
        S5LivePreflightProductionError,
        match="partial_dependency_injection",
    ):
        asyncio.run(
            execute_s5_live_preflight_production(
                method="A0",
                run_id="s5-a0-production-test",
                namespace="pev3-s5-a0-production-test",
                episode_source_sha256s=SOURCE_SHA256S,
                production_identity_qualification=_qualification("A0"),
                production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
                current_stage_pointer=_pointer(),
                current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
                env={
                    "NEO4J_URI": "bolt://localhost:7687",
                    "NEO4J_USER": "neo4j",
                    "NEO4J_PASSWORD": "ignored-test-secret",
                    "CONSTRUCTION_LLM_API_KEY": "",
                    "EMBEDDING_API_KEY": "",
                },
                get_json=lambda _base, _path: {},
                neo4j_probe=None,
            )
        )


def test_production_collection_closes_probe_when_collection_fails() -> None:
    probe = _Probe()

    async def bad_get_json(_base_url: str, _path: str) -> dict[str, object]:
        raise RuntimeError("controlled offline failure")

    with pytest.raises(RuntimeError, match="controlled offline failure"):
        asyncio.run(
            execute_s5_live_preflight_production(
                method="A0",
                run_id="s5-a0-production-test",
                namespace="pev3-s5-a0-production-test",
                episode_source_sha256s=SOURCE_SHA256S,
                production_identity_qualification=_qualification("A0"),
                production_identity_qualification_file_sha256=QUALIFICATION_FILE_SHA256,
                current_stage_pointer=_pointer(),
                current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
                env={
                    "NEO4J_URI": "bolt://localhost:7687",
                    "NEO4J_USER": "neo4j",
                    "NEO4J_PASSWORD": "ignored-test-secret",
                    "CONSTRUCTION_LLM_API_KEY": "",
                    "EMBEDDING_API_KEY": "",
                },
                get_json=bad_get_json,
                neo4j_probe=probe,
            )
        )
    assert probe.close_calls == 1


def test_raw_identity_is_rejected_before_any_read_only_probe() -> None:
    calls: list[tuple[str, str]] = []
    probe = _Probe()

    async def get_json(base_url: str, path: str) -> dict[str, object]:
        calls.append((base_url, path))
        return {}

    with pytest.raises(ValueError, match="production_identity_qualification_invalid"):
        asyncio.run(
            execute_s5_live_preflight_production(
                method="A0",
                run_id="s5-a0-production-raw-identity",
                namespace="pev3-s5-a0-production-raw-identity",
                episode_source_sha256s=SOURCE_SHA256S,
                production_identity_qualification=_identity("A0"),
                production_identity_qualification_file_sha256=(
                    QUALIFICATION_FILE_SHA256
                ),
                current_stage_pointer=_pointer(),
                current_stage_pointer_file_sha256=POINTER_FILE_SHA256,
                env={
                    "NEO4J_URI": "bolt://localhost:7687",
                    "NEO4J_USER": "neo4j",
                    "NEO4J_PASSWORD": "ignored-test-secret",
                    "CONSTRUCTION_LLM_API_KEY": "",
                    "EMBEDDING_API_KEY": "",
                },
                get_json=get_json,
                neo4j_probe=probe,
            )
        )
    assert calls == []
    assert probe.connectivity_calls == 0
    assert probe.namespace_calls == []
    assert probe.close_calls == 1
