from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.dataset import EXPECTED_EPISODE_COUNTS
from saturated_fixed_work_baseline_v1_2.live import build_formal_plan
from saturated_fixed_work_baseline_v1_2.production_dependencies import (
    build_live_dependencies,
    build_neo4j_idle_probe,
)
from saturated_fixed_work_baseline_v1_2.production_qa import (
    build_production_qa_dependencies,
    build_formal_qa_judge_inputs,
)
from saturated_fixed_work_baseline_v1_2.qa_stage import execute_qa_stage
from saturated_fixed_work_baseline_v1_2.sampler import PeriodicSampler


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _formal(root: Path) -> dict[str, object]:
    selected = []
    for block in build_formal_plan("sfwb-v1-2-qa-stage-test"):
        attempt = root / "blocks" / block.block_id / "attempt-001"
        attempt.mkdir(parents=True)
        graph = {"entities": [block.history_id], "edges": [], "episodes": []}
        (attempt / "canonical_graph.json").write_text(
            json.dumps(graph, sort_keys=True) + "\n", encoding="utf-8"
        )
        selected.append(
            {
                "ordinal": block.ordinal,
                "block_id": block.block_id,
                "method": block.method.value,
                "history_id": block.history_id,
                "attempt_id": "attempt-001",
                "namespace": block.namespace,
                "canonical_graph_hash": _canonical_hash(graph),
            }
        )
    return {
        "verified": True,
        "payload_sha256": "f" * 64,
        "valid_construction_blocks": 8,
        "formal_construction_calls": 8,
        "selected_attempts": selected,
    }


@pytest.mark.asyncio
async def test_qa_stage_derives_all_namespaces_from_formal_seal_only(
    repository_root: Path, tmp_path: Path
) -> None:
    formal = _formal(tmp_path)
    observed: dict[str, Any] = {}

    async def qa_executor(**kwargs: Any) -> list[dict[str, object]]:
        observed.update(kwargs)
        rows = []
        for seal in kwargs["seals"]:
            for index in range(4):
                rows.append(
                    {
                        "method": seal.method,
                        "history_id": seal.history_id,
                        "qa_pair_id": f"{seal.history_id}-{index}",
                        "construction_calls": 0,
                        "graph_write_attempts": 0,
                        "graph_hash_before": seal.canonical_hash,
                        "graph_hash_after": seal.canonical_hash,
                    }
                )
        kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_path"].write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )
        return rows

    result = await execute_qa_stage(
        repository_root=repository_root,
        run_root=tmp_path,
        dependencies=object(),
        formal_verifier=lambda root: formal,
        qa_executor=qa_executor,
        inventory_loader=lambda root: {
            "questions": [
                {"question_id": f"q-{index}", "history_id": history}
                for history in EXPECTED_EPISODE_COUNTS
                for index in range(4)
            ]
        },
    )

    assert len(observed["seals"]) == 8
    assert observed["construction_calls"] == 8
    assert [seal.construction_call_ordinal for seal in observed["seals"]] == list(
        range(1, 9)
    )
    assert result["qa_rows"] == 32
    assert result["qa_graph_write_attempts"] == 0
    assert result["qa_extra_construction_calls"] == 0
    assert (tmp_path / "qa/read_only_evidence.json").is_file()


@pytest.mark.asyncio
async def test_neo4j_idle_probe_counts_only_other_active_transactions() -> None:
    queries: list[str] = []

    class Driver:
        async def execute_query(self, query: str, **kwargs: Any) -> Any:
            queries.append(query)
            assert kwargs["routing_"] == "r"
            return SimpleNamespace(records=[{"active_transactions": 0}])

    probe = build_neo4j_idle_probe(Driver())
    assert await probe() == {"idle": True, "active_transactions": 0}
    assert "SHOW TRANSACTIONS" in queries[0]
    assert "currentQuery" in queries[0]


def test_live_dependency_composition_installs_six_source_periodic_sampler(
    repository_root: Path, tmp_path: Path
) -> None:
    modules = {
        "native_characterization_tracing": SimpleNamespace(TraceRecorder=lambda: object()),
        "native_characterization_instrumentation": SimpleNamespace(
            install_native_characterization_instrumentation=lambda graph, recorder: object()
        ),
        "native_characterization_c2_measurement": SimpleNamespace(
            install_c2_measurement_adapter=lambda graph, recorder: object()
        ),
        "live_outputs": SimpleNamespace(
            export_canonical_graph=lambda graph, episodes, namespace: {}
        ),
    }
    probes = {
        "construction_vllm": lambda: {},
        "embedding_vllm": lambda: {},
        "runner_process": lambda: {},
        "neo4j_process": lambda: {},
        "runner_host": lambda: {},
        "provider_gpu": lambda: {},
    }
    dependencies = build_live_dependencies(
        repository_root=repository_root,
        service_idle=lambda: True,
        sampler_probes=probes,
        validation_loader=lambda root, name: modules[name],
        runtime_builder=lambda **kwargs: SimpleNamespace(graphiti=object()),
        episode_source="MESSAGE",
    )
    sampler = dependencies.sampler_factory(tmp_path / "telemetry.jsonl")
    assert isinstance(sampler, PeriodicSampler)


def test_formal_qa_judge_projection_binds_namespace_date_and_gold_sessions() -> None:
    inputs = build_formal_qa_judge_inputs(
        run_id="formal-qa",
        question_id="07741c45-ext-001",
        namespace="sfwb-v1-3-B0_NATIVE_SERIAL-07741c45-run-attempt-001",
        question_type="single-hop",
        question_date="2024/03/01 (Fri) 12:00",
        question="Where does Ravi work now?",
        reference_answer="OpenAI",
        gold_session_ids=("session-1", "session-2"),
    )
    assert inputs.namespace.startswith("sfwb-v1-3-")
    assert inputs.question_date == "2024/03/01 (Fri) 12:00"
    assert inputs.answer_session_ids == ("session-1", "session-2")


@pytest.mark.asyncio
async def test_production_qa_dependencies_use_frozen_source_and_read_only_runtime(
    repository_root: Path,
) -> None:
    calls: list[tuple[str, Any]] = []
    graphiti = SimpleNamespace()

    def read_only_builder(*, env: dict[str, str]) -> Any:
        calls.append(("runtime", dict(env)))
        return SimpleNamespace(graphiti=graphiti)

    async def exporter(graph: Any, episodes: list[Any], namespace: str) -> dict[str, object]:
        calls.append(("snapshot", (graph, len(episodes), namespace)))
        return {"entities": [], "edges": [], "episodes": []}

    dependencies = build_production_qa_dependencies(
        repository_root=repository_root,
        env_loader=lambda: {
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "secret",
            "CONSTRUCTION_LLM_API_KEY": "secret",
        },
        read_only_runtime_builder=read_only_builder,
        graph_exporter=exporter,
        component_builder=lambda **kwargs: (object(), object(), object()),
        question_runner_builder=lambda **kwargs: (lambda **call: {}),
    )
    seal = SimpleNamespace(history_id="07741c45", namespace="qa/ns")
    runtime = await dependencies.runtime_factory(seal)
    await dependencies.snapshot_graph(runtime, seal)
    assert calls[0][0] == "runtime"
    assert calls[1] == ("snapshot", (graphiti, 49, "qa/ns"))


@pytest.mark.asyncio
async def test_default_quality_qa_runtime_binds_embedding_capable_graph_quality_path(
    repository_root: Path,
) -> None:
    """Quality v1 Edge cosine must not be wired to the S2-R0 no-embedder runtime."""

    imported: list[str] = []
    runtime = SimpleNamespace(graphiti=SimpleNamespace())

    class RuntimeModule:
        @staticmethod
        def build_graph_quality_runtime(*, env: dict[str, str]) -> Any:
            assert env["EMBEDDING_MODEL"] == "qwen3-embedding-0.6b"
            return runtime

    def import_paper(_root: Path, module: str) -> Any:
        imported.append(module)
        if module == "paper_eval.graph_quality_live":
            return RuntimeModule
        raise AssertionError(f"unexpected paper module: {module}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "saturated_fixed_work_baseline_v1_2.production_qa.import_paper_eval_module",
        import_paper,
    )
    try:
        dependencies = build_production_qa_dependencies(
            repository_root=repository_root,
            env_loader=lambda: {
                "NEO4J_URI": "bolt://localhost:7687",
                "NEO4J_USER": "neo4j",
                "NEO4J_PASSWORD": "secret",
                "CONSTRUCTION_LLM_API_KEY": "secret",
                "EMBEDDING_BASE_URL": "http://10.87.5.247:8001/v1",
                "EMBEDDING_API_KEY": "embedding-secret",
                "EMBEDDING_MODEL": "qwen3-embedding-0.6b",
                "EMBEDDING_DIM": "1024",
            },
            component_builder=lambda **kwargs: (object(), object(), object()),
            question_runner_builder=lambda **kwargs: (lambda **call: {}),
        )
        await dependencies.runtime_factory(
            SimpleNamespace(history_id="07741c45", namespace="qa/ns")
        )
    finally:
        monkeypatch.undo()

    assert "paper_eval.graph_quality_live" in imported
    assert "paper_eval.s2_r0_live" not in imported
