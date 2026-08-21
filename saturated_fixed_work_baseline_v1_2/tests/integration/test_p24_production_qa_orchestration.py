from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from saturated_fixed_work_baseline_v1_2.dataset import (
    EXPECTED_EPISODE_COUNTS,
    load_and_validate_qa_inventory,
)
from saturated_fixed_work_baseline_v1_2.production_qa import (
    ProductionQADependencies,
    ProductionQAError,
    execute_production_qa,
)
from saturated_fixed_work_baseline_v1_2.qa_lane import NamespaceSeal
from saturated_fixed_work_baseline_v1_2.schedules import Method


def _seals() -> tuple[NamespaceSeal, ...]:
    rows = []
    ordinal = 0
    for method in Method:
        for history in EXPECTED_EPISODE_COUNTS:
            ordinal += 1
            rows.append(
                NamespaceSeal(
                    method=method.value,
                    history_id=history,
                    namespace=f"formal/{method.value}/{history}",
                    canonical_hash=f"{ordinal:064x}",
                    construction_call_ordinal=ordinal,
                )
            )
    return tuple(rows)


@pytest.mark.asyncio
async def test_production_qa_runs_exactly_32_rows_without_gold_leak_or_construction(
    repository_root: Path, tmp_path: Path
) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    graph = {seal.namespace: {"entities": [seal.history_id], "edges": []} for seal in _seals()}
    closed: list[str] = []
    public_seen: list[dict[str, Any]] = []

    def runtime_factory(seal: NamespaceSeal) -> Any:
        async def close() -> None:
            closed.append(seal.namespace)

        return SimpleNamespace(graphiti=SimpleNamespace(close=close), seal=seal)

    async def snapshot(runtime: Any, seal: NamespaceSeal) -> dict[str, Any]:
        del runtime
        return copy.deepcopy(graph[seal.namespace])

    async def question_runner(**kwargs: Any) -> dict[str, Any]:
        public = kwargs["public_question"]
        private = kwargs["private_evaluation"]
        public_seen.append(copy.deepcopy(public))
        assert "reference_answer" not in public
        assert "gold_session_ids" not in public
        assert set(private) == {
            "reference_answer",
            "gold_session_ids",
            "gold_evidence_quotes",
        }
        return {
            "recall_at_1": 0.5,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 0.75,
            "ndcg_at_10": 0.8,
            "correct": True,
            "invalid": False,
            "invalid_reason": None,
            "failure_layer": None,
            "construction_calls": 0,
            "graph_write_attempts": 0,
        }

    rows = await execute_production_qa(
        seals=_seals(),
        questions=inventory["questions"],
        expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
        construction_calls=8,
        output_path=tmp_path / "qa/qa_rows.jsonl",
        dependencies=ProductionQADependencies(
            runtime_factory=runtime_factory,
            snapshot_graph=snapshot,
            question_runner=question_runner,
        ),
    )
    assert len(rows) == 32
    assert len(public_seen) == 32
    assert len(closed) == 8
    assert sum(row["construction_calls"] for row in rows) == 0
    assert sum(row["graph_write_attempts"] for row in rows) == 0
    assert all(row["graph_hash_before"] == row["graph_hash_after"] for row in rows)
    durable = [
        json.loads(line)
        for line in (tmp_path / "qa/qa_rows.jsonl").read_text().splitlines()
    ]
    assert durable == rows
    for row in durable:
        observed = row.pop("payload_sha256")
        payload = json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
        assert observed == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_production_qa_accepts_one_history_after_its_two_methods(
    repository_root: Path, tmp_path: Path
) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    history = next(iter(EXPECTED_EPISODE_COUNTS))
    seals = tuple(seal for seal in _seals() if seal.history_id == history)
    questions = tuple(
        row for row in inventory["questions"] if row["history_id"] == history
    )
    graph = {seal.namespace: {"history": history} for seal in seals}
    closed: list[str] = []

    def runtime_factory(seal: NamespaceSeal) -> Any:
        async def close() -> None:
            closed.append(seal.namespace)

        return SimpleNamespace(graphiti=SimpleNamespace(close=close), seal=seal)

    async def snapshot(runtime: Any, seal: NamespaceSeal) -> dict[str, Any]:
        del runtime
        return copy.deepcopy(graph[seal.namespace])

    async def question_runner(**kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "recall_at_1": 1.0,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 1.0,
            "ndcg_at_10": 1.0,
            "correct": True,
            "invalid": False,
            "invalid_reason": None,
            "failure_layer": None,
            "construction_calls": 0,
            "graph_write_attempts": 0,
        }

    rows = await execute_production_qa(
        seals=seals,
        questions=questions,
        expected_histories=(history,),
        construction_calls=2,
        output_path=tmp_path / "qa" / history / "qa_rows.jsonl",
        dependencies=ProductionQADependencies(
            runtime_factory=runtime_factory,
            snapshot_graph=snapshot,
            question_runner=question_runner,
        ),
    )

    assert len(rows) == 8
    assert len(closed) == 2
    assert {row["history_id"] for row in rows} == {history}
    assert {row["method"] for row in rows} == {method.value for method in Method}
    assert all(row["graph_write_attempts"] == 0 for row in rows)


@pytest.mark.asyncio
async def test_production_qa_fails_closed_on_namespace_mutation(
    repository_root: Path, tmp_path: Path
) -> None:
    inventory = load_and_validate_qa_inventory(repository_root)
    calls = 0

    async def snapshot(runtime: Any, seal: NamespaceSeal) -> dict[str, Any]:
        del runtime, seal
        return {"mutation_counter": calls}

    async def mutate(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        del kwargs
        calls += 1
        return {
            "recall_at_1": 0.0,
            "recall_at_3": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
            "ndcg_at_10": 0.0,
            "correct": False,
            "invalid": False,
            "construction_calls": 0,
            "graph_write_attempts": 1,
        }

    with pytest.raises(ProductionQAError, match="QA_GRAPH_WRITE_OR_MUTATION"):
        await execute_production_qa(
            seals=_seals(),
            questions=inventory["questions"],
            expected_histories=tuple(EXPECTED_EPISODE_COUNTS),
            construction_calls=8,
            output_path=tmp_path / "qa/qa_rows.jsonl",
            dependencies=ProductionQADependencies(
                runtime_factory=lambda seal: SimpleNamespace(graphiti=SimpleNamespace()),
                snapshot_graph=snapshot,
                question_runner=mutate,
            ),
        )


def test_production_qa_source_binds_required_local_components(
    repository_root: Path,
) -> None:
    source = (
        repository_root
        / "saturated_fixed_work_baseline_v1_2/src/saturated_fixed_work_baseline_v1_2/production_qa.py"
    ).read_text(encoding="utf-8")
    for required in (
        "retrieve_quality_v1",
        "build_context_pack",
        "QualityEvaluationV1Reader",
        "GraphQualityTransport",
        "build_qualified_qwen_judge",
        '"qwen3-32b-fp8"',
        '"http://10.87.5.247:8000/v1"',
    ):
        assert required in source
    assert "run_expanded.main" not in source
