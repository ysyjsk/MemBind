from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import (
    atomic_write_json,
    finalize_envelope,
    payload_sha256,
    sha256_file,
)
from paper_eval.s2_retrieval_probe import (
    ProbeCounters,
    corpus_identity_sha256,
)
from paper_eval.s2_r0_authorization import (
    REQUIRED_BINDINGS,
    finalize_s2r0_authorization,
    finalize_s2r0_offline_qualification,
)
from paper_eval.s2_r0_controller import (
    S2R0ControllerDependencies,
    execute_s2r0_once,
    load_neo4j_env_file,
)
from paper_eval.s2_r0_live import S2R0Runtime


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str
    body: str


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        edge_config=None,
        node_config=None,
        episode_config=SimpleNamespace(
            search_methods=[SimpleNamespace(value="bm25")],
            reranker=SimpleNamespace(value="reciprocal_rank_fusion"),
            sim_min_score=0.6,
            mmr_lambda=0.5,
            bfs_max_depth=3,
        ),
        community_config=None,
        limit=10,
        reranker_min_score=0.0,
    )


def _config_identity() -> dict[str, object]:
    return {
        "edge_config": None,
        "node_config": None,
        "episode_config": {
            "search_methods": ["bm25"],
            "reranker": "reciprocal_rank_fusion",
            "sim_min_score": 0.6,
            "mmr_lambda": 0.5,
            "bfs_max_depth": 3,
        },
        "community_config": None,
        "limit": 10,
        "reranker_min_score": 0.0,
        "candidate_limit": 20,
        "search_filter": "EMPTY",
        "center_node_uuid": None,
        "bfs_origin_node_uuids": None,
        "query_vector": None,
    }


def _episodes() -> list[_Episode]:
    return [
        _Episode(f"q::episode::{index:04d}", f"s{index}", f"body {index}")
        for index in range(49)
    ]


def _authorization(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Path]]:
    bindings: dict[str, Path] = {}
    for name in REQUIRED_BINDINGS:
        path = tmp_path / f"{name}.txt"
        if name in {"focused_green", "full_green"}:
            path.write_text(
                '<testsuites><testsuite tests="3" errors="0" failures="0"/></testsuites>\n',
                encoding="utf-8",
            )
        else:
            path.write_text(f"{name}\n", encoding="utf-8")
        bindings[name] = path
    episodes = _episodes()
    qualification = tmp_path / "qualification.json"
    finalize_s2r0_offline_qualification(
        qualification,
        binding_paths=bindings,
        expected_parent_protocol_sha256=sha256_file(bindings["parent_protocol"]),
        retrieval_config_identity=_config_identity(),
        dataset_sha256=sha256_file(bindings["dataset"]),
        frozen_split_sha256=sha256_file(bindings["frozen_split"]),
        frozen_corpus_identity_sha256=corpus_identity_sha256(episodes),
        ordered_session_ids_sha256=payload_sha256(
            [episode.session_id for episode in episodes]
        ),
        gold_session_ids_sha256=payload_sha256(["s2", "s31"]),
        episode_names_sha256=payload_sha256([episode.name for episode in episodes]),
        episode_content_hash_sequence_sha256=payload_sha256(
            [
                __import__("hashlib").sha256(episode.body.encode()).hexdigest()
                for episode in episodes
            ]
        ),
        gold_session_count=2,
        git_commit="deadbeef",
        run_id="s2r0-offline-test",
    )
    authorization = tmp_path / "authorization.json"
    result = tmp_path / "S2_R0_EPISODE_PROBE.json"
    consumption = tmp_path / "consumption.json"
    finalize_s2r0_authorization(
        authorization,
        qualification_path=qualification,
        binding_paths=bindings,
        expected_output_path=result,
        consumption_path=consumption,
        git_commit="deadbeef",
        run_id="s2r0-20260814-001",
    )
    return authorization, consumption, result, bindings


class _Graph:
    def __init__(self) -> None:
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1


def _dependencies(
    *, consumption: Path, calls: list[str], fail_probe: bool = False
) -> tuple[S2R0ControllerDependencies, _Graph]:
    episodes = _episodes()
    graph = _Graph()
    counters = ProbeCounters()

    def load_history() -> dict[str, object]:
        assert consumption.exists()
        calls.append("load_history")
        return {
            "question_id": "07741c45",
            "question": "private question",
            "haystack_session_ids": [episode.session_id for episode in episodes],
            "answer_session_ids": ["s2", "s31"],
        }

    def build_episodes(instance: object) -> list[_Episode]:
        calls.append("build_episodes")
        return episodes

    def build_config() -> SimpleNamespace:
        calls.append("build_config")
        return _config()

    def load_env() -> dict[str, str]:
        calls.append("load_env")
        return {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "private",
        }

    def build_runtime(env: object) -> S2R0Runtime:
        assert consumption.exists()
        calls.append("build_runtime")
        return S2R0Runtime(graphiti=graph, counters=counters)

    async def run_probe(**kwargs: object) -> object:
        calls.append("run_probe")
        if fail_probe:
            raise ConnectionError("secret endpoint and private query")
        return SimpleNamespace(marker="probe-result")

    def finalize_probe(output_path: Path, **kwargs: object) -> dict[str, object]:
        calls.append("finalize_probe")
        assert kwargs["consumption_sha256"] == sha256_file(consumption)
        artifact = finalize_envelope(
            payload={"status": "READ_ONLY_RETRIEVAL_SURFACE_DIAGNOSTIC"},
            protocol_version="paper-eval-v3",
            git_commit="deadbeef",
            run_id="s2r0-20260814-001",
        )
        atomic_write_json(output_path, artifact)
        return artifact

    return (
        S2R0ControllerDependencies(
            load_history=load_history,
            build_episodes=build_episodes,
            build_search_config=build_config,
            load_env=load_env,
            build_runtime=build_runtime,
            run_probe=run_probe,
            finalize_probe=finalize_probe,
        ),
        graph,
    )


def test_controller_consumes_before_runtime_and_seals_result(tmp_path: Path) -> None:
    authorization, consumption, result, bindings = _authorization(tmp_path)
    calls: list[str] = []
    dependencies, graph = _dependencies(consumption=consumption, calls=calls)
    failure = tmp_path / "failure.json"
    outcome = execute_s2r0_once(
        authorization_path=authorization,
        consumption_path=consumption,
        failure_path=failure,
        binding_paths=bindings,
        dependencies=dependencies,
        git_commit="deadbeef",
        expected_run_id="s2r0-20260814-001",
    )

    assert outcome.status == "COMPLETED"
    assert outcome.artifact_path == result
    assert result.is_file()
    assert not failure.exists()
    assert graph.closed == 1
    assert calls == [
        "load_history",
        "build_episodes",
        "build_config",
        "load_env",
        "build_runtime",
        "run_probe",
        "finalize_probe",
    ]


def test_controller_seals_sanitized_failure_after_consumption(tmp_path: Path) -> None:
    authorization, consumption, result, bindings = _authorization(tmp_path)
    calls: list[str] = []
    dependencies, graph = _dependencies(
        consumption=consumption, calls=calls, fail_probe=True
    )
    failure = tmp_path / "failure.json"
    outcome = execute_s2r0_once(
        authorization_path=authorization,
        consumption_path=consumption,
        failure_path=failure,
        binding_paths=bindings,
        dependencies=dependencies,
        git_commit="deadbeef",
        expected_run_id="s2r0-20260814-001",
    )

    assert outcome.status == "FAILED_STOPPED"
    assert outcome.artifact_path == failure
    assert consumption.is_file()
    assert failure.is_file()
    assert not result.exists()
    assert graph.closed == 1
    serialized = json.dumps(json.loads(failure.read_text()), sort_keys=True)
    assert "secret endpoint" not in serialized
    assert "private query" not in serialized


def test_controller_rejects_second_execution_before_dependencies(tmp_path: Path) -> None:
    authorization, consumption, _result, bindings = _authorization(tmp_path)
    calls: list[str] = []
    dependencies, _graph = _dependencies(consumption=consumption, calls=calls)
    execute_s2r0_once(
        authorization_path=authorization,
        consumption_path=consumption,
        failure_path=tmp_path / "failure.json",
        binding_paths=bindings,
        dependencies=dependencies,
        git_commit="deadbeef",
        expected_run_id="s2r0-20260814-001",
    )
    with pytest.raises(ValueError, match="already consumed"):
        execute_s2r0_once(
            authorization_path=authorization,
            consumption_path=consumption,
            failure_path=tmp_path / "other-failure.json",
            binding_paths=bindings,
            dependencies=dependencies,
            git_commit="deadbeef",
            expected_run_id="s2r0-20260814-001",
        )


def test_neo4j_env_loader_projects_only_required_fields_without_process_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            (
                "NEO4J_URI=bolt://localhost:7687",
                'NEO4J_USER="neo4j"',
                "export NEO4J_PASSWORD='private-value'",
                "CONSTRUCTION_LLM_API_KEY=must-not-be-loaded",
                "EMBEDDING_BASE_URL=http://must-not-be-loaded",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    for key in (
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "CONSTRUCTION_LLM_API_KEY",
        "EMBEDDING_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    loaded = load_neo4j_env_file(env_path)

    assert loaded == {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USER": "neo4j",
        "NEO4J_PASSWORD": "private-value",
    }
    assert "CONSTRUCTION_LLM_API_KEY" not in __import__("os").environ
    assert "EMBEDDING_BASE_URL" not in __import__("os").environ
