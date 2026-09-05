"""Generic three-arm runner hook.

Live construction is deliberately injected by callers so the same dataset,
Graphiti instance, and evaluator can be shared across A/B/C.  This module
only defines the order and machine-readable cell envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True, slots=True)
class ExperimentCell:
    history_id: str
    replicate_id: int
    arm: str
    attempt_id: str
    namespace: str


ARMS = ("SERIAL_NATIVE", "ASYNC_NATIVE", "MEMBIND")


async def run_cell(cell: ExperimentCell, episodes: Sequence[Any], runners: dict[str, Callable[[Sequence[Any]], Any]]) -> Any:
    if cell.arm not in ARMS:
        raise ValueError(f"unknown arm: {cell.arm}")
    if cell.arm not in runners:
        raise ValueError(f"runner missing for {cell.arm}")
    result = runners[cell.arm](episodes)
    if hasattr(result, "__await__"):
        return await result
    return result

