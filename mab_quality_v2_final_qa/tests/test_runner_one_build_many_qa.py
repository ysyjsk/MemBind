from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from mab_quality_v2_final_qa.artifacts import ArtifactStore
from mab_quality_v2_final_qa.contracts import MABContext
from mab_quality_v2_final_qa.dataset_adapter import MABDatasetAdapter
from mab_quality_v2_final_qa.runner import MABQualityRunner

from .test_contracts_adapter import _official_like_record


class FakeGraph:
    def __init__(self) -> None:
        self.write_calls = 0

    async def add_episode(self, **_kwargs):
        self.write_calls += 1
        return {"uuid": f"episode-{self.write_calls}"}


def _context(count: int = 5) -> MABContext:
    base = MABDatasetAdapter.from_records([_official_like_record()]).contexts[0]
    qas = tuple(
        replace(item, qa_pair_id=f"pair{i}", question_id=f"q{i}")
        for i, item in enumerate((base.qa_items * count)[:count])
    )
    return MABContext.create(base.context_id, base.sessions, qas)


def _runner(tmp_path, *, reader=None, violating=False, method_id="U0"):
    graph = FakeGraph()
    context = _context()
    method = SimpleNamespace(method_id=method_id, implementation_sha256="a" * 64)
    counts = {"construct": 0, "retrieve": 0, "reader": 0, "judge": 0}
    observed_public: list[object] = []

    async def construct(*, public_context, namespace, writer):
        counts["construct"] += 1
        observed_public.append(public_context)
        for session in public_context["sessions"]:
            await writer.add_episode(payload=session, namespace=namespace)
        return {
            "namespace_sealed": True,
            "episode_uuid_to_session_id": {
                f"episode-{i + 1}": session["session_id"]
                for i, session in enumerate(public_context["sessions"])
            },
            "construction_manifest_sha256": "b" * 64,
        }

    async def retrieve(**kwargs):
        counts["retrieve"] += 1
        if violating:
            await kwargs["graph"].add_episode(payload={})
        episodes = [{"episode_uuid": f"episode-{i + 1}"} for i in range(3)]
        return {"facts": [], "episodes": episodes}

    async def pack(**_kwargs):
        return {"context_json": json.dumps([{"raw_evidence": "memory"}])}

    async def answer(**_kwargs):
        counts["reader"] += 1
        if reader is not None:
            return await reader(**_kwargs)
        return "Suzhou"

    async def judge(**kwargs):
        counts["judge"] += 1
        return {"valid": True, "correct": kwargs["answer"] == "Suzhou"}

    runner = MABQualityRunner(
        store=ArtifactStore(tmp_path / "run"),
        method=method,
        run_id="offline-test",
        dataset_manifest_sha256="d" * 64,
        graph=graph,
        construct=construct,
        retrieve=retrieve,
        reader=answer,
        judge=judge,
        context_pack=pack,
        metrics=lambda ranked, gold: {
            "recall_at_1": 1.0,
            "recall_at_3": 1.0,
            "recall_at_5": 1.0,
            "recall_at_10": 1.0,
            "mrr": 1.0,
            "ndcg_at_10": 1.0,
            "gold_ranks": [1],
        },
    )
    return runner, context, graph, counts, observed_public


def test_one_context_constructs_once_and_answers_many(tmp_path) -> None:
    runner, context, graph, counts, observed = _runner(tmp_path)
    rows = asyncio.run(runner.run_context(context))
    assert len(rows) == 5
    assert counts == {"construct": 1, "retrieve": 5, "reader": 5, "judge": 5}
    assert graph.write_calls == 3
    assert all("reference_answers" not in json.dumps(payload) for payload in observed)
    assert all(row["status"] == "COMPLETE" for row in rows)

    again = asyncio.run(runner.run_context(context))
    assert len(again) == 5
    assert counts == {"construct": 1, "retrieve": 5, "reader": 5, "judge": 5}


def test_read_only_qa_write_is_a_hard_failure(tmp_path) -> None:
    runner, context, graph, counts, _ = _runner(tmp_path, violating=True)
    rows = asyncio.run(runner.run_context(context))
    assert counts["construct"] == 1
    assert all(row["failure_class"] == "QA_PHASE_WRITE_VIOLATION" for row in rows)
    assert graph.write_calls == 3
