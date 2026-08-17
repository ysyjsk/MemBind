"""TDD contracts for resumable QA decomposition model calls."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from paper_eval.qa_decomposition import select_variant_sessions
from paper_eval.qa_decomposition_live import (
    QADecompositionUnit,
    build_unit_binding,
    execute_qa_decomposition_unit,
)
from paper_eval.s2_session_reader import SessionReaderResult


def _record() -> dict:
    return {
        "question_id": "07741c45",
        "question": "Where is it?",
        "question_date": "2023-06-01",
        "question_type": "knowledge-update",
        "answer": "closet",
        "answer_session_ids": ["s1", "s2"],
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2023-01-01", "2023-02-01"],
        "haystack_sessions": [
            [{"role": "user", "content": "old"}],
            [{"role": "user", "content": "closet"}],
        ],
    }


@dataclass
class _Reader:
    calls: int = 0
    config_sha256: str = "4" * 64

    async def answer(self, sessions, *, question_date, question):
        self.calls += 1
        return SessionReaderResult(
            answer="It is in the closet.",
            prompt_for_test="private reader prompt",
            prompt_tokens=21,
            completion_tokens=7,
            model="qwen3-32b-fp8",
            config_sha256=self.config_sha256,
        )


@dataclass
class _Judge:
    calls: int = 0
    config_sha256: str = "5" * 64
    fail: bool = False

    async def evaluate(self, *, hypothesis, inputs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("disconnected")
        return {
            "status": "SUCCESS",
            "label": True,
            "model": "qwen3-32b-fp8",
            "prompt_sha256": "6" * 64,
            "config_sha256": "7" * 64,
            "output_sha256": "8" * 64,
            "output_character_count": 3,
            "output_byte_count": 3,
            "parse_status": "YES",
            "retry_count": 0,
            "error_class": None,
            "raw_output": "yes",
        }


def _unit() -> QADecompositionUnit:
    record = _record()
    selection = select_variant_sessions(
        record=record,
        variant="gold_only",
        retrieved_session_ids=("s2", "s1"),
        top_k=2,
    )
    return QADecompositionUnit(
        overlay_run_id="qd-dev-20260817-001",
        source_run_id="nb-20260816-001",
        history_id="07741c45",
        namespace="nc-e1e2-0123456789abcdef",
        construction_result_sha256="2" * 64,
        record=record,
        selection=selection,
    )


def test_binding_hashes_private_identifiers() -> None:
    binding = build_unit_binding(
        unit=_unit(),
        reader_config_sha256="4" * 64,
        judge_config_sha256="5" * 64,
    )

    assert binding["namespace_sha256"] != _unit().namespace
    assert "s1" not in repr(binding)
    assert "s2" not in repr(binding)


@pytest.mark.asyncio
async def test_reader_checkpoint_survives_judge_failure_and_resume(tmp_path) -> None:
    reader = _Reader()
    judge = _Judge(fail=True)
    unit = _unit()

    with pytest.raises(RuntimeError, match="disconnected"):
        await execute_qa_decomposition_unit(
            unit=unit,
            reader=reader,
            judge=judge,
            unit_root=tmp_path,
        )
    assert reader.calls == 1
    assert judge.calls == 1
    assert (tmp_path / "runtime/private/reader_stage.json").is_file()
    assert not (tmp_path / "runtime/private/judge_stage.json").exists()

    judge.fail = False
    first = await execute_qa_decomposition_unit(
        unit=unit,
        reader=reader,
        judge=judge,
        unit_root=tmp_path,
    )
    second = await execute_qa_decomposition_unit(
        unit=unit,
        reader=reader,
        judge=judge,
        unit_root=tmp_path,
    )

    assert reader.calls == 1
    assert judge.calls == 2
    assert first == second
    assert first["qa_accuracy"] == 1.0
    assert (tmp_path / "private_bundle.json").is_file()
    assert (tmp_path / "public.json").is_file()


@pytest.mark.asyncio
async def test_tampered_reader_checkpoint_fails_before_new_calls(tmp_path) -> None:
    reader = _Reader()
    judge = _Judge()
    unit = _unit()
    await execute_qa_decomposition_unit(
        unit=unit,
        reader=reader,
        judge=judge,
        unit_root=tmp_path,
    )
    stage = tmp_path / "runtime/private/reader_stage.json"
    stage.write_text(stage.read_text().replace("closet", "garage"))

    with pytest.raises(ValueError, match="hash"):
        await execute_qa_decomposition_unit(
            unit=unit,
            reader=reader,
            judge=judge,
            unit_root=tmp_path,
        )
    assert reader.calls == 1
    assert judge.calls == 1
