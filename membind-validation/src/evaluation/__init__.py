"""Benchmark-native, offline-testable evaluation infrastructure.

This package is future confirmation infrastructure only. It does not retrieve
memory, construct graphs, or participate in the current C4/C5 scientific lane.
"""

from evaluation.registry import EvaluatorRegistry
from evaluation.schemas import (
    EvaluationItem,
    EvaluationResult,
    EvaluationStatus,
    JudgeQualificationRecord,
)

__all__ = [
    "EvaluationItem",
    "EvaluationResult",
    "EvaluationStatus",
    "EvaluatorRegistry",
    "JudgeQualificationRecord",
]
