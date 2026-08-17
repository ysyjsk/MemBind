"""Offline tests for S6 workload, identity, preparation, and instrumentation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256, sha256_file
from paper_eval.s6_calibration_contract import (
    DEVELOPMENT_HISTORIES_PAYLOAD_SHA256,
    build_s6_matrix,
    finalize_s6_matrix_freeze,
)
from paper_eval.s6_live_authority import verify_s6_live_authority
from paper_eval.s6_production import (
    S6BlockPreparationPaths,
    S6ProductionError,
    S6ProductionPaths,
    build_s6_execution_identity,
    collect_s6_read_only_observations,
    instrument_s6_runtime,
    load_s6_sources,
    prepare_s6_block_authority,
    snapshot_s6_work_volume,
)


GIT = "a" * 40


def _matrix() -> dict[str, object]:
    return build_s6_matrix(
        input_bindings={
            "s6_development_histories_payload_sha256": (
                DEVELOPMENT_HISTORIES_PAYLOAD_SHA256
            ),
            "parent_protocol_sha256": "1" * 64,
            "s5_pstar_result_file_sha256": "2" * 64,
            "s5_pstar_result_payload_sha256": "3" * 64,
            "s5_mstar_result_file_sha256": "4" * 64,
            "s5_mstar_result_payload_sha256": "5" * 64,
        }
    )


def test_real_frozen_workloads_are_exact_49_49_46_44() -> None:
    matrix = _matrix()
    expected = {
        "07741c45": 49,
        "b6019101": 49,
        "6071bd76": 46,
        "a2f3aa27": 44,
    }
    for cell_index in (0, 8, 16, 24):
        cell = matrix["cells"][cell_index]
        sources = load_s6_sources(cell=cell, paths=S6ProductionPaths())
        assert len(sources) == expected[str(cell["history_id"])]
        assert [source.source_sequence for source in sources] == list(
            range(len(sources))
        )


def test_execution_identity_is_history_independent_but_method_c_sensitive() -> None:
    matrix = _matrix()
    closure = {"production_runtime": "1" * 64, "method_runner": "2" * 64}
    dependencies = {"graphiti": "3" * 64, "dataset": "4" * 64}

    p_c1_h0 = build_s6_execution_identity(
        cell=matrix["cells"][0],
        source_sha256=closure,
        dependency_sha256=dependencies,
    )
    p_c1_h1 = build_s6_execution_identity(
        cell=matrix["cells"][8],
        source_sha256=closure,
        dependency_sha256=dependencies,
    )
    p_c2 = build_s6_execution_identity(
        cell=matrix["cells"][2],
        source_sha256=closure,
        dependency_sha256=dependencies,
    )
    m_c1 = build_s6_execution_identity(
        cell=matrix["cells"][1],
        source_sha256=closure,
        dependency_sha256=dependencies,
        production_core_identity_sha256="5" * 64,
    )

    assert p_c1_h0 == p_c1_h1
    assert len({p_c1_h0, p_c2, m_c1}) == 3


@pytest.mark.asyncio
async def test_pass_preflight_exclusively_materializes_one_authority(tmp_path: Path) -> None:
    matrix_path = tmp_path / "S6_MATRIX_FREEZE.json"
    freeze = finalize_s6_matrix_freeze(
        output_path=matrix_path,
        matrix=_matrix(),
        git_commit=GIT,
    )["artifact"]
    cell = freeze["payload"]["cells"][0]
    sources = load_s6_sources(cell=cell, paths=S6ProductionPaths())
    paths = S6BlockPreparationPaths(
        matrix_freeze=matrix_path,
        preflight=tmp_path / "preflights" / f"{cell['run_id']}.json",
        authority=tmp_path / "authorities" / f"{cell['run_id']}.json",
    )

    async def observations(_cell: dict[str, object]) -> dict[str, object]:
        return {
            "construction": {
                "status": "PASS",
                "served_model_id": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
            },
            "embedding": {
                "status": "PASS",
                "served_model_id": "qwen3-embedding-0.6b",
            },
            "neo4j_connectivity": True,
            "namespace": _cell["namespace"],
            "namespace_state": {"node_count": 0, "relationship_count": 0},
        }

    result = await prepare_s6_block_authority(
        paths=paths,
        cell_index=0,
        sources=sources,
        git_commit=GIT,
        observation_collector=observations,
    )

    assert result["status"] == "AUTHORIZED_SINGLE_USE"
    assert paths.preflight.is_file()
    assert paths.authority.is_file()
    authority = verify_s6_live_authority(
        __import__("json").loads(paths.authority.read_text(encoding="utf-8"))
    )
    assert authority["payload"]["cell"] == cell
    assert authority["payload"]["matrix"]["file_sha256"] == sha256_file(matrix_path)


@pytest.mark.asyncio
async def test_failed_read_only_preflight_is_persisted_without_authority(
    tmp_path: Path,
) -> None:
    matrix_path = tmp_path / "S6_MATRIX_FREEZE.json"
    freeze = finalize_s6_matrix_freeze(
        output_path=matrix_path,
        matrix=_matrix(),
        git_commit=GIT,
    )["artifact"]
    cell = freeze["payload"]["cells"][0]
    sources = load_s6_sources(cell=cell, paths=S6ProductionPaths())
    paths = S6BlockPreparationPaths(
        matrix_freeze=matrix_path,
        preflight=tmp_path / "preflight.json",
        authority=tmp_path / "authority.json",
    )

    async def failed(_cell: dict[str, object]) -> dict[str, object]:
        return {
            "construction": {
                "status": "FAIL",
                "served_model_id": "qwen3-32b-fp8",
                "vllm_version": "0.26.0",
                "max_model_len": 65536,
            },
            "embedding": {
                "status": "PASS",
                "served_model_id": "qwen3-embedding-0.6b",
            },
            "neo4j_connectivity": True,
            "namespace": _cell["namespace"],
            "namespace_state": {"node_count": 0, "relationship_count": 0},
        }

    with pytest.raises(S6ProductionError, match="preflight_not_pass"):
        await prepare_s6_block_authority(
            paths=paths,
            cell_index=0,
            sources=sources,
            git_commit=GIT,
            observation_collector=failed,
        )

    assert paths.preflight.is_file()
    assert not paths.authority.exists()


@pytest.mark.asyncio
async def test_transparent_runtime_instrumentation_produces_work_volume_shape() -> None:
    class Embedder:
        async def create(self, _value: object) -> list[float]:
            return [1.0]

        async def create_batch(self, values: list[str]) -> list[list[float]]:
            return [[1.0] for _ in values]

    class Driver:
        async def execute_query(self, _query: str, *args, **kwargs) -> list[object]:
            return []

    llm = SimpleNamespace(
        call_events=[
            {
                "token_usage": {"prompt_tokens": 100, "completion_tokens": 20}
            },
            {
                "token_usage": {"prompt_tokens": 50, "completion_tokens": 10}
            },
        ]
    )
    embedder = Embedder()
    graphiti = SimpleNamespace(llm_client=llm, embedder=embedder, driver=Driver())
    runtime = SimpleNamespace(
        graphiti=graphiti,
        llm_client=llm,
        embedder=embedder,
    )
    instrument_s6_runtime(runtime, episode_key=lambda: ("run", 0))

    await embedder.create("one")
    await embedder.create_batch(["two", "three"])
    await graphiti.driver.execute_query("MATCH (n) RETURN n")
    await graphiti.driver.execute_query("CREATE (n)")

    assert snapshot_s6_work_volume(runtime) == {
        "llm_call_count": 2,
        "llm_prompt_tokens": 150,
        "llm_completion_tokens": 30,
        "embedding_call_count": 2,
        "embedding_input_count": 3,
        "db_query_count": 1,
        "db_transaction_count": None,
        "db_write_count": 1,
    }


@pytest.mark.asyncio
async def test_read_only_collector_uses_models_version_and_namespace_counts() -> None:
    cell = _matrix()["cells"][0]
    requested: list[str] = []

    async def get_json(
        url: str, _headers: dict[str, str]
    ) -> dict[str, object]:
        requested.append(url)
        if url.endswith("/version"):
            return {"version": "0.26.0"}
        if ":8001/" in url:
            return {"data": [{"id": "qwen3-embedding-0.6b"}]}
        return {"data": [{"id": "qwen3-32b-fp8", "max_model_len": 65536}]}

    async def counts(namespace: str) -> tuple[int, int]:
        assert namespace == cell["namespace"]
        return 0, 0

    observed = await collect_s6_read_only_observations(
        cell,
        http_get_json=get_json,
        neo4j_counter=counts,
    )

    assert observed["construction"] == {
        "status": "PASS",
        "served_model_id": "qwen3-32b-fp8",
        "vllm_version": "0.26.0",
        "max_model_len": 65536,
    }
    assert observed["namespace_state"] == {
        "node_count": 0,
        "relationship_count": 0,
    }
    assert len(requested) == 3
