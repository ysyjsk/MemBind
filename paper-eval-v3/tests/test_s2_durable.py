from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from paper_eval.artifacts import payload_sha256
from paper_eval.s2_durable import (
    S2DurabilityError,
    S2DurableRun,
    S2RunAlreadyStarted,
    run_s2_durable,
)
from paper_eval.s2_live import S2LiveInputs


@dataclass(frozen=True)
class _Episode:
    name: str
    session_id: str


class _Driver:
    async def execute_query(self, query: str, *, params: dict[str, str]):
        return SimpleNamespace(
            records=[
                {"uuid": "ep-1", "name": "q::episode::0000", "valid_at": "2024-01-01"},
                {"uuid": "ep-2", "name": "q::episode::0001", "valid_at": "2024-01-02"},
            ]
        )


class _Graph:
    def __init__(self, *, search_error: Exception | None = None) -> None:
        self.driver = _Driver()
        self.search_count = 0
        self.closed = False
        self.search_error = search_error

    async def search(self, query: str, *, group_ids: list[str], num_results: int):
        self.search_count += 1
        if self.search_error is not None:
            raise self.search_error
        return [
            SimpleNamespace(
                uuid="fact-1",
                fact="fact kept in memory only",
                reference_time="2024-01-01",
                episodes=["ep-1"],
            )
        ]

    async def close(self) -> None:
        self.closed = True


class _CloseErrorGraph(_Graph):
    async def close(self) -> None:
        self.closed = True
        raise RuntimeError("private close detail")


class _Reader:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.count = 0
        self.error = error

    async def answer(self, facts, *, question_date: str, question: str):
        self.count += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            answer="answer kept in memory only",
            to_artifact=lambda: {
                "status": "SUCCESS",
                "model": "reader-model",
                "config_sha256": "a" * 64,
                "prompt_sha256": "b" * 64,
                "prompt_character_count": 11,
                "prompt_byte_count": 11,
                "output_sha256": "c" * 64,
                "output_character_count": 11,
                "output_byte_count": 11,
                "prompt_tokens": 4,
                "completion_tokens": 2,
            },
        )


class _Judge:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.count = 0
        self.error = error

    async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs):
        self.count += 1
        if self.error is not None:
            raise self.error
        return {
            "status": "SUCCESS",
            "label": True,
            "model": "judge-model",
            "config_sha256": "d" * 64,
            "prompt_sha256": "e" * 64,
            "output_sha256": "f" * 64,
            "parse_status": "YES",
            "retry_count": 0,
        }


def _inputs() -> S2LiveInputs:
    return S2LiveInputs(
        run_id="s2-durable-test",
        history_id="07741c45",
        namespace="pev3-s1-namespace",
        question="raw question must never be written",
        question_date="2024-03-01",
        question_type="knowledge-update",
        reference_answer="raw answer must never be written",
        answer_session_ids=("session-1",),
    )


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_s2_durable_persists_one_chain_and_atomic_checkpoint(tmp_path: Path) -> None:
    run = S2DurableRun(tmp_path, _inputs())
    graph = _Graph()
    reader = _Reader()
    judge = _Judge()
    result = await run_s2_durable(
        run=run,
        graph=graph,
        episodes=[_Episode("q::episode::0000", "session-1"), _Episode("q::episode::0001", "session-2")],
        reader=reader,
        judge=judge,
        git_commit="deadbeef",
        qualification_evidence_sha256="e" * 64,
        adapter_identity_sha256="a" * 64,
    )

    assert result.payload["status"] == "PASS"
    assert (graph.search_count, reader.count, judge.count) == (1, 1, 1)
    checkpoint = json.loads((tmp_path / "s2-durable-test" / "checkpoint.json").read_text())
    assert checkpoint["status"] == "completed"
    assert checkpoint["completed_stages"] == ["retrieval", "reader", "judge"]
    assert checkpoint["chain_counts"] == {"retrieval": 1, "reader": 1, "judge": 1}
    assert checkpoint["qualification_evidence_sha256"] == "e" * 64
    assert checkpoint["adapter_identity_sha256"] == "a" * 64
    assert result.payload["adapter_identity_sha256"] == "a" * 64
    assert checkpoint["payload_sha256"] == payload_sha256(
        {key: value for key, value in checkpoint.items() if key != "payload_sha256"}
    )
    events = _events(tmp_path / "s2-durable-test" / "events.jsonl")
    assert [event["event_type"] for event in events] == [
        "start", "retrieval", "reader", "judge", "completed"
    ]
    assert all(event["payload_sha256"] == payload_sha256({k: v for k, v in event.items() if k != "payload_sha256"}) for event in events)
    persisted = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "s2-durable-test").iterdir()
        if path.is_file()
    )
    for raw_content in (
        _inputs().question,
        _inputs().reference_answer,
        "fact kept in memory only",
        "answer kept in memory only",
        "q::episode::0000",
    ):
        assert raw_content not in persisted


@pytest.mark.asyncio
async def test_s2_durable_service_failure_is_sanitized_and_fail_closed(tmp_path: Path) -> None:
    inputs = _inputs()
    run = S2DurableRun(tmp_path, inputs)
    graph = _Graph(search_error=ConnectionError("secret endpoint and raw question"))
    with pytest.raises(S2DurabilityError, match="retrieval"):
        await run_s2_durable(
            run=run,
            graph=graph,
            episodes=[_Episode("q::episode::0000", "session-1")],
            reader=_Reader(),
            judge=_Judge(),
            git_commit="deadbeef",
            qualification_evidence_sha256="e" * 64,
            adapter_identity_sha256="a" * 64,
        )

    run_dir = tmp_path / inputs.run_id
    failure = json.loads((run_dir / "failure.json").read_text())
    assert failure["payload"]["status"] == "FAIL"
    assert failure["payload"]["error_class"] == "ConnectionError"
    assert failure["payload"]["failure_stage"] == "retrieval"
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text())
    assert checkpoint["status"] == "incomplete"
    text = (run_dir / "events.jsonl").read_text() + (run_dir / "failure.json").read_text()
    assert "secret endpoint" not in text
    assert inputs.question not in text
    assert inputs.reference_answer not in text
    assert graph.closed is True


@pytest.mark.asyncio
async def test_s2_durable_reader_failure_never_calls_judge(tmp_path: Path) -> None:
    reader = _Reader(error=TimeoutError("raw reader response"))
    judge = _Judge()
    with pytest.raises(S2DurabilityError, match="reader"):
        await run_s2_durable(
            run=S2DurableRun(tmp_path, _inputs()),
            graph=_Graph(),
            episodes=[_Episode("q::episode::0000", "session-1")],
            reader=reader,
            judge=judge,
            git_commit="deadbeef",
            qualification_evidence_sha256="e" * 64,
            adapter_identity_sha256="a" * 64,
        )

    checkpoint = json.loads(
        (tmp_path / _inputs().run_id / "checkpoint.json").read_text()
    )
    assert checkpoint["completed_stages"] == ["retrieval"]
    assert checkpoint["failure_stage"] == "reader"
    assert (reader.count, judge.count) == (1, 0)


@pytest.mark.asyncio
async def test_s2_durable_judge_service_error_is_not_a_terminal_score(tmp_path: Path) -> None:
    class _ServiceErrorJudge(_Judge):
        async def evaluate(self, *, hypothesis: str, inputs: S2LiveInputs):
            self.count += 1
            return {
                "status": "SERVICE_ERROR",
                "label": False,
                "model": "judge-model",
                "config_sha256": "d" * 64,
                "prompt_sha256": "e" * 64,
                "output_sha256": "f" * 64,
                "parse_status": "NOT_RUN",
                "retry_count": 0,
                "error_class": "TimeoutError",
            }

    judge = _ServiceErrorJudge()
    with pytest.raises(S2DurabilityError, match="judge"):
        await run_s2_durable(
            run=S2DurableRun(tmp_path, _inputs()),
            graph=_Graph(),
            episodes=[_Episode("q::episode::0000", "session-1")],
            reader=_Reader(),
            judge=judge,
            git_commit="deadbeef",
            qualification_evidence_sha256="e" * 64,
            adapter_identity_sha256="a" * 64,
        )

    failure = json.loads(
        (tmp_path / _inputs().run_id / "failure.json").read_text()
    )
    assert failure["payload"]["failure_stage"] == "judge"
    assert failure["payload"]["status"] == "FAIL"
    assert judge.count == 1


@pytest.mark.asyncio
async def test_s2_durable_rejects_second_chain_for_same_run_id(tmp_path: Path) -> None:
    run = S2DurableRun(tmp_path, _inputs())
    run.bind_execution_evidence(
        qualification_evidence_sha256="e" * 64,
        adapter_identity_sha256="a" * 64,
    )
    await run.start()
    graph = _Graph()
    with pytest.raises(S2RunAlreadyStarted):
        await run_s2_durable(
            run=S2DurableRun(tmp_path, _inputs()),
            graph=graph,
            episodes=[_Episode("q::episode::0000", "session-1")],
            reader=_Reader(),
            judge=_Judge(),
            git_commit="deadbeef",
            qualification_evidence_sha256="e" * 64,
            adapter_identity_sha256="a" * 64,
        )
    assert graph.closed is True


@pytest.mark.asyncio
async def test_s2_start_failure_preserves_original_error_when_graph_close_fails(
    tmp_path: Path,
) -> None:
    inputs = _inputs()
    first = S2DurableRun(tmp_path, inputs)
    first.bind_execution_evidence(
        qualification_evidence_sha256="e" * 64,
        adapter_identity_sha256="a" * 64,
    )
    await first.start()

    with pytest.raises(S2RunAlreadyStarted):
        await run_s2_durable(
            run=S2DurableRun(tmp_path, inputs),
            graph=_CloseErrorGraph(),
            episodes=[_Episode("q::episode::0000", "session-1")],
            reader=_Reader(),
            judge=_Judge(),
            git_commit="deadbeef",
            qualification_evidence_sha256="e" * 64,
            adapter_identity_sha256="a" * 64,
        )


def test_s2_durable_rejects_raw_evidence_fields() -> None:
    run = S2DurableRun(Path("/tmp"), _inputs())
    with pytest.raises(ValueError, match="unsafe evidence"):
        run.sanitize_evidence({"answer": "raw", "status": "SUCCESS"})
