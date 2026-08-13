"""Fail-closed dispatch for benchmark-native evaluators."""

from __future__ import annotations

from typing import Any, Protocol

from evaluation.schemas import EvaluationItem


class Evaluator(Protocol):
    async def evaluate(self, item: EvaluationItem) -> Any: ...


class EvaluatorRegistryError(RuntimeError):
    """Base error for invalid registry operations."""


class UnknownEvaluatorError(EvaluatorRegistryError):
    """Raised instead of falling back to a generic LLM judge."""


class EvaluatorRegistry:
    """Dispatch by exact benchmark name and hold no scorer configuration."""

    def __init__(self) -> None:
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, benchmark: str, evaluator: Evaluator) -> None:
        if not isinstance(benchmark, str) or not benchmark:
            raise EvaluatorRegistryError("benchmark name must be non-empty")
        if benchmark in self._evaluators:
            raise EvaluatorRegistryError(f"evaluator already registered: {benchmark}")
        if not callable(getattr(evaluator, "evaluate", None)):
            raise EvaluatorRegistryError("evaluator must expose async evaluate")
        self._evaluators[benchmark] = evaluator

    def get(self, benchmark: str) -> Evaluator:
        try:
            return self._evaluators[benchmark]
        except (KeyError, TypeError):
            raise UnknownEvaluatorError(f"unknown evaluator: {benchmark}") from None

    async def evaluate(self, benchmark: str, item: EvaluationItem) -> Any:
        return await self.get(benchmark).evaluate(item)

