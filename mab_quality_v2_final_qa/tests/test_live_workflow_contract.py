from __future__ import annotations

from dataclasses import dataclass

import pytest

from mab_quality_v2_final_qa.live_workflow import _enable_abstention_route


@dataclass(frozen=True)
class _Item:
    question_id: str
    abstention: bool = False


class _Evaluator:
    def __init__(self) -> None:
        self.seen: list[_Item] = []

    async def evaluate(self, item: _Item) -> _Item:
        self.seen.append(item)
        return item


class _Judge:
    def __init__(self) -> None:
        self._evaluator = _Evaluator()


@pytest.mark.asyncio
async def test_abs_question_routes_to_official_abstention_flag() -> None:
    judge = _Judge()
    _enable_abstention_route(judge)
    item = _Item("question-123_abs")
    result = await judge._evaluator.evaluate(item)
    assert result.abstention is True
    assert judge._evaluator._delegate.seen[0].abstention is True


@pytest.mark.asyncio
async def test_non_abs_question_keeps_non_abstention_route() -> None:
    judge = _Judge()
    _enable_abstention_route(judge)
    item = _Item("question-123")
    result = await judge._evaluator.evaluate(item)
    assert result.abstention is False
