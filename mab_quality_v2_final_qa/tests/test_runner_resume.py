from __future__ import annotations

import asyncio
import json

import pytest

from mab_quality_v2_final_qa.runner import ResumeIdentityMismatch

from .test_runner_one_build_many_qa import _runner


def test_invalid_qa_is_retried_without_reconstruction(tmp_path) -> None:
    failed_once = {"done": False}

    async def failing_reader(**kwargs):
        if not failed_once["done"] and kwargs["public_qa"]["question_id"] == "q1":
            failed_once["done"] = True
            return ""
        return "Suzhou"

    runner, context, _graph, counts, _ = _runner(tmp_path, reader=failing_reader)
    first = asyncio.run(runner.run_context(context))
    assert first[1]["status"] == "INVALID"
    assert first[1]["failure_class"] == "READER_FAILED"
    assert counts["construct"] == 1

    second_runner, _, _, second_counts, _ = _runner(tmp_path, reader=failing_reader)
    second = asyncio.run(second_runner.run_context(context))
    assert second[0]["attempt"] == 1
    assert second[1]["attempt"] == 2
    assert second[1]["status"] == "COMPLETE"
    assert second_counts["construct"] == 0


def test_corrupt_completed_row_is_not_silently_reused(tmp_path) -> None:
    runner, context, _graph, _counts, _ = _runner(tmp_path)
    asyncio.run(runner.run_context(context))
    path = runner.store.path(runner._qa_relative(context))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["correct"] = False
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    resumed, resumed_context, *_ = _runner(tmp_path)
    with pytest.raises(ResumeIdentityMismatch, match="QA row payload hash mismatch"):
        asyncio.run(resumed.run_context(resumed_context))


def test_resume_reobserves_sealed_namespace(tmp_path) -> None:
    runner, context, _graph, _counts, _ = _runner(tmp_path)
    asyncio.run(runner.run_context(context))

    resumed, resumed_context, *_ = _runner(tmp_path)
    resumed.namespace_validator = lambda _receipt: False
    with pytest.raises(ResumeIdentityMismatch, match="sealed namespace validation failed"):
        asyncio.run(resumed.run_context(resumed_context))
