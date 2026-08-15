"""Offline tests for the lazy production collector used by S4 preflight."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s4_preflight import HISTORICAL_S1_NAMESPACE
from paper_eval.s4_preflight_production import (
    execute_production_preflight,
    load_expected_s1_state,
    load_s4_preflight_env,
)


def _state() -> dict:
    return {
        "node_count": 294,
        "relationship_count": 438,
        "episode_names": [f"07741c45::episode::{index:04d}" for index in range(49)],
    }


def _checkpoint(path: Path) -> dict:
    value = {
        "schema_version": "membind.paper-eval-v3.s1-checkpoint.v1",
        "run_id": "s1-20260814-001",
        "history_id": "07741c45",
        "namespace": HISTORICAL_S1_NAMESPACE,
        "status": "completed",
        "completed_source_sequences": list(range(49)),
        "namespace_state": _state(),
    }
    value["payload_sha256"] = payload_sha256(value)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def test_env_loader_selects_required_fields_without_mutating_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "NEO4J_URI=bolt://localhost:7687",
                "NEO4J_USER=neo4j",
                "NEO4J_PASSWORD=private-password",
                "CONSTRUCTION_LLM_API_KEY=construction-secret",
                "EMBEDDING_API_KEY=embedding-secret",
                "UNRELATED=ignored",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    selected = load_s4_preflight_env(path)

    assert selected == {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "private-password",
        "CONSTRUCTION_LLM_API_KEY": "construction-secret",
        "EMBEDDING_API_KEY": "embedding-secret",
    }
    assert "NEO4J_PASSWORD" not in __import__("os").environ


def test_checkpoint_loader_requires_exact_completed_s1_identity(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    _checkpoint(path)
    assert load_expected_s1_state(path) == _state()

    for field, value in (
        ("namespace", "wrong"),
        ("status", "running"),
        ("completed_source_sequences", list(range(48))),
    ):
        altered = _checkpoint(path)
        altered[field] = value
        altered["payload_sha256"] = payload_sha256(
            {key: item for key, item in altered.items() if key != "payload_sha256"}
        )
        path.write_text(json.dumps(altered), encoding="utf-8")
        with pytest.raises(ValueError):
            load_expected_s1_state(path)

    altered = _checkpoint(path)
    altered["namespace_state"]["node_count"] += 1
    path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_expected_s1_state(path)


@pytest.mark.asyncio
async def test_executor_closes_probe_and_never_exposes_credentials(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    _checkpoint(checkpoint)
    calls: list[tuple] = []

    async def get_json(base_url: str, path: str) -> dict:
        calls.append(("http", base_url, path))
        if path == "/version":
            return {"version": "0.26.0"}
        if ":8000/" in base_url:
            return {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 65536}]}
        return {"data": [{"id": "qwen3-embedding-0.6b"}]}

    class Probe:
        async def connectivity(self) -> bool:
            calls.append(("connectivity",))
            return True

        async def namespace_state(self, namespace: str) -> dict:
            calls.append(("namespace", namespace))
            if namespace == HISTORICAL_S1_NAMESPACE:
                return _state()
            return {"node_count": 0, "relationship_count": 0, "episode_names": []}

        async def close(self) -> None:
            calls.append(("close",))

    result = await execute_production_preflight(
        env={
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "do-not-persist",
            "CONSTRUCTION_LLM_API_KEY": "do-not-persist",
            "EMBEDDING_API_KEY": "do-not-persist",
        },
        s1_checkpoint_path=checkpoint,
        get_json=get_json,
        neo4j_probe=Probe(),
    )

    assert result["verdict"] == "PASS"
    assert calls[-1] == ("close",)
    assert "do-not-persist" not in json.dumps(result)


@pytest.mark.asyncio
async def test_executor_closes_probe_after_collection_failure(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoint.json"
    _checkpoint(checkpoint)
    closed: list[bool] = []

    async def fail(*_args):
        raise ConnectionError("private endpoint detail")

    class Probe:
        async def connectivity(self) -> bool:
            return True

        async def namespace_state(self, _namespace: str) -> dict:
            return {}

        async def close(self) -> None:
            closed.append(True)

    with pytest.raises(ConnectionError):
        await execute_production_preflight(
            env={
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "secret",
                "CONSTRUCTION_LLM_API_KEY": "secret",
                "EMBEDDING_API_KEY": "secret",
            },
            s1_checkpoint_path=checkpoint,
            get_json=fail,
            neo4j_probe=Probe(),
        )
    assert closed == [True]

