from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.s2_completion_chain import BoundedRetrievalOutcome
from paper_eval.s2_completion_production import (
    CompletionProductionFactories,
    build_production_live_executor,
    load_completion_env_file,
)


def test_env_loader_reads_only_required_fields_without_mutating_environ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "NEO4J_URI=bolt://localhost:7687",
                "NEO4J_USER=neo4j",
                "NEO4J_PASSWORD='private-password'",
                "CONSTRUCTION_LLM_BASE_URL=http://10.87.5.247:8000/v1/",
                "CONSTRUCTION_LLM_API_KEY=not-required",
                "CONSTRUCTION_LLM_MODEL=qwen3-32b-fp8",
                "UNRELATED_SECRET=must-not-load",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)

    loaded = load_completion_env_file(env_path, environ={})

    assert set(loaded) == {
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "CONSTRUCTION_LLM_BASE_URL",
        "CONSTRUCTION_LLM_API_KEY",
        "CONSTRUCTION_LLM_MODEL",
    }
    assert loaded["NEO4J_PASSWORD"] == "private-password"
    assert "UNRELATED_SECRET" not in loaded


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("NEO4J_URI", "bolt://other:7687"),
        ("CONSTRUCTION_LLM_BASE_URL", "http://other/v1/"),
        ("CONSTRUCTION_LLM_MODEL", "other-model"),
    ],
)
def test_env_loader_rejects_runtime_identity_drift(
    tmp_path: Path, field: str, value: str
) -> None:
    values = {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "password",
        "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
        "CONSTRUCTION_LLM_API_KEY": "not-required",
        "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
    }
    values[field] = value
    path = tmp_path / ".env"
    path.write_text(
        "".join(f"{key}={item}\n" for key, item in values.items()),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runtime identity|missing"):
        load_completion_env_file(path, environ={})


def test_env_loader_uses_nonsecret_placeholder_when_vllm_requires_no_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "NEO4J_URI=bolt://localhost:7687\n"
        "NEO4J_USER=neo4j\n"
        "NEO4J_PASSWORD=password\n"
        "CONSTRUCTION_LLM_BASE_URL=http://10.87.5.247:8000/v1/\n"
        "CONSTRUCTION_LLM_API_KEY=\n"
        "CONSTRUCTION_LLM_MODEL=qwen3-32b-fp8\n",
        encoding="utf-8",
    )

    loaded = load_completion_env_file(path, environ={})

    assert loaded["CONSTRUCTION_LLM_API_KEY"] == "not-required"


@dataclass
class _Closeable:
    closed: bool = False

    async def aclose(self) -> None:
        self.closed = True


def test_production_builder_wires_gold_blind_retrieval_and_closes_all_components(
    tmp_path: Path,
) -> None:
    record = {
        "question_id": "07741c45",
        "question_type": "knowledge-update",
        "question": "question",
        "question_date": "date",
        "answer": "answer",
        "answer_session_ids": ["s0", "s1"],
        "haystack_session_ids": [f"s{index}" for index in range(10)],
    }
    episodes = [SimpleNamespace(session_id=f"s{index}") for index in range(10)]
    runtime = SimpleNamespace(
        graphiti=SimpleNamespace(close=lambda: None),
        counters=object(),
    )
    transport = _Closeable()
    judge = _Closeable()
    reader = object()
    calls: dict[str, object] = {}

    async def retrieval(**kwargs):
        calls["retrieval_keys"] = set(kwargs)
        return BoundedRetrievalOutcome(
            retrieved_session_ids=tuple(f"s{index}" for index in range(10)),
            graphiti_search_calls=1,
            neo4j_read_requests=2,
            construction_llm_requests=0,
            embedding_requests=0,
            cross_encoder_requests=0,
            database_mutation_attempts=0,
            database_mutations=0,
            cleanup_calls=0,
            retry_count=0,
        )

    async def chain(**kwargs):
        calls["chain_inputs"] = kwargs["inputs"]
        calls["reader"] = kwargs["reader"]
        calls["judge"] = kwargs["judge"]
        await kwargs["retrieve"](question="question", namespace="pev3-s1-20260814-001")
        return "synthetic-result"

    factories = CompletionProductionFactories(
        load_history=lambda _dataset, _split: record,
        build_episodes=lambda _record: episodes,
        build_runtime=lambda _env: runtime,
        build_search_config=lambda: "config",
        build_transport=lambda **_kwargs: transport,
        build_reader=lambda **_kwargs: reader,
        build_judge=lambda **_kwargs: judge,
        run_retrieval=retrieval,
        execute_chain=chain,
        corpus_identity=lambda _episodes: "c" * 64,
    )
    env = {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "password",
        "CONSTRUCTION_LLM_BASE_URL": "http://10.87.5.247:8000/v1/",
        "CONSTRUCTION_LLM_API_KEY": "not-required",
        "CONSTRUCTION_LLM_MODEL": "qwen3-32b-fp8",
    }

    executor = build_production_live_executor(
        env=env,
        dataset_path=tmp_path / "dataset.json",
        split_path=tmp_path / "split.json",
        factories=factories,
        expected_session_count=10,
    )
    result = asyncio.run(executor.execute(lambda _stage, _evidence: None))
    asyncio.run(executor.close())

    assert result == "synthetic-result"
    assert calls["retrieval_keys"] == {
        "graph",
        "query",
        "namespace",
        "episodes",
        "expected_frozen_session_ids",
        "expected_corpus_identity_sha256",
        "search_config",
        "counters",
    }
    assert "answer_session_ids" not in calls["retrieval_keys"]
    assert calls["reader"] is reader
    assert calls["judge"] is judge
    assert transport.closed is True
    assert judge.closed is True
