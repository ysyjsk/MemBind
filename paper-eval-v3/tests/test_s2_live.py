from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_live import (
    S2LiveInputs,
    S2LiveQualification,
    finalize_s2_qualification,
    run_s2_numeric_sanity,
)
from paper_eval.s2_reader import RetrievedFact


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str


class _Graph:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, list[str], int]] = []
        self.closed = False

    async def search(self, query: str, *, group_ids: list[str], num_results: int):
        self.search_calls.append((query, group_ids, num_results))
        return [
            SimpleNamespace(
                uuid="edge-1",
                fact="Ravi now works at OpenAI.",
                reference_time="2024/02/02 (Fri) 10:00",
                episodes=["ep-2"],
            ),
            SimpleNamespace(
                uuid="edge-2",
                fact="Ravi previously worked at Google.",
                reference_time="2024/01/01 (Mon) 09:00",
                episodes=["ep-1"],
            ),
        ]

    async def close(self) -> None:
        self.closed = True


class _Driver:
    async def execute_query(self, query: str, *, params: dict[str, str]):
        assert params == {"group_id": "pev3-s1-namespace"}
        return SimpleNamespace(
            records=[
                {"uuid": "ep-1", "name": "q::episode::0000"},
                {"uuid": "ep-2", "name": "q::episode::0001"},
            ]
        )


class _Reader:
    model = "reader-model"
    config_sha256 = "r" * 64

    def __init__(self) -> None:
        self.facts: list[RetrievedFact] = []

    async def answer(self, facts, *, question_date: str, question: str):
        self.facts = list(facts)
        assert question_date == "2024/03/01 (Fri) 12:00"
        assert question == "Where does Ravi work now?"
        return SimpleNamespace(
            answer="OpenAI",
            to_artifact=lambda: {
                "status": "SUCCESS",
                "model": "reader-model",
                "config_sha256": "r" * 64,
                "prompt_sha256": "p" * 64,
                "prompt_character_count": 100,
                "prompt_byte_count": 100,
                "output_sha256": "o" * 64,
                "output_character_count": 6,
                "output_byte_count": 6,
                "prompt_tokens": 50,
                "completion_tokens": 2,
            },
        )


class _Judge:
    async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs):
        assert hypothesis == "OpenAI"
        return {
            "status": "SUCCESS",
            "label": True,
            "prompt_sha256": "j" * 64,
            "output_sha256": "k" * 64,
            "model": "judge-model",
            "config_sha256": "c" * 64,
            "retry_count": 0,
            "parse_status": "YES",
        }


def _inputs() -> S2LiveInputs:
    return S2LiveInputs(
        run_id="s2-live-test",
        history_id="07741c45",
        namespace="pev3-s1-namespace",
        question="Where does Ravi work now?",
        question_date="2024/03/01 (Fri) 12:00",
        question_type="knowledge-update",
        reference_answer="OpenAI",
        answer_session_ids=("s2",),
    )


@pytest.mark.asyncio
async def test_s2_live_reuses_namespace_and_runs_one_retrieval_reader_judge_chain() -> None:
    graph = _Graph()
    graph.driver = _Driver()
    reader = _Reader()
    result = await run_s2_numeric_sanity(
        inputs=_inputs(),
        graph=graph,
        episodes=[_Episode("q::episode::0000", "s1"), _Episode("q::episode::0001", "s2")],
        reader=reader,
        judge=_Judge(),
    )

    assert graph.search_calls == [
        ("Where does Ravi work now?", ["pev3-s1-namespace"], 10)
    ]
    assert [item.rank for item in reader.facts] == [1, 2]
    assert [item.source_session_ids for item in reader.facts] == [("s2",), ("s1",)]
    assert result.edge_attributed_source_session_coverage_at_10 == 1.0
    assert result.qa_accuracy == 1.0
    assert result.edge_result_count == 2
    assert result.retrieved_source_session_ids == ("s2", "s1")
    assert result.reader_status == "SUCCESS"
    assert result.judge_status == "SUCCESS"
    assert graph.closed is True


@pytest.mark.asyncio
async def test_s2_live_closes_graph_and_stops_before_reader_when_no_facts() -> None:
    graph = _Graph()
    graph.driver = _Driver()

    async def no_results(*args, **kwargs):
        return []

    graph.search = no_results
    reader = _Reader()
    with pytest.raises(RuntimeError, match="retrieval returned no facts"):
        await run_s2_numeric_sanity(
            inputs=_inputs(),
            graph=graph,
            episodes=[_Episode("q::episode::0000", "s1")],
            reader=reader,
            judge=_Judge(),
        )
    assert reader.facts == []
    assert graph.closed is True


def test_s2_live_final_artifact_is_sealed_and_contains_no_raw_content(
    tmp_path: Path,
) -> None:
    result = S2LiveQualification(
        edge_attributed_source_session_coverage_at_10=1.0,
        qa_accuracy=1.0,
        edge_result_count=2,
        retrieved_source_session_ids=("s2", "s1"),
        reader_status="SUCCESS",
        reader_evidence={"prompt_sha256": "p" * 64, "output_sha256": "o" * 64},
        judge_status="SUCCESS",
        judge_evidence={"prompt_sha256": "j" * 64, "output_sha256": "k" * 64},
    )
    output = tmp_path / "U0_REFERENCE_SANITY.json"
    artifact = finalize_s2_qualification(
        output,
        result=result,
        inputs=_inputs(),
        git_commit="deadbeef",
        qualification_evidence_sha256="q" * 64,
        adapter_identity_sha256="a" * 64,
    )

    persisted = json.loads(output.read_text())
    assert artifact == persisted
    assert artifact["payload_sha256"] == payload_sha256(artifact["payload"])
    assert artifact["payload"]["status"] == "PASS"
    assert artifact["payload"]["adapter_identity_sha256"] == "a" * 64
    assert artifact["payload"]["retrieval_surface"] == "graphiti_basic_edge"
    assert artifact["payload"]["retrieval_unit"] == "EntityEdge"
    assert artifact["payload"]["top_k_unit"] == "edge"
    assert artifact["payload"]["edge_attributed_source_session_coverage_at_10"] == 1.0
    assert artifact["payload"]["official_longmemeval_session_recall_at_10"] is None
    assert "evidence_recall_at_10" not in artifact["payload"]
    assert artifact["payload"]["numeric_alignment"] == (
        "NATIVE_EDGE_SURFACE_NOT_LONGMEMEVAL_SESSION_RETRIEVAL"
    )
    text = output.read_text()
    assert "Where does" not in text
    assert "OpenAI" not in text
    assert "Ravi" not in text
