"""Shared runner timer contract for V5 blocks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TimerContractError(ValueError):
    pass


@dataclass(slots=True)
class BuildTimer:
    timer_start_ns: int | None = None
    timer_stop_ns: int | None = None
    final_publication_ns: int | None = None

    def start(self, timestamp_ns: int) -> None:
        if self.timer_start_ns is not None:
            raise TimerContractError("timer already started")
        self.timer_start_ns = int(timestamp_ns)

    def durable_complete(self, timestamp_ns: int, *, final_publication_ns: int) -> None:
        if self.timer_start_ns is None:
            raise TimerContractError("timer start required")
        if int(timestamp_ns) < self.timer_start_ns:
            raise TimerContractError("timer stop precedes start")
        if int(final_publication_ns) > int(timestamp_ns):
            raise TimerContractError("final publication occurs after timer stop")
        self.timer_stop_ns = int(timestamp_ns)
        self.final_publication_ns = int(final_publication_ns)

    @property
    def build_makespan_ns(self) -> int:
        if self.timer_start_ns is None or self.timer_stop_ns is None:
            raise TimerContractError("timer incomplete")
        return self.timer_stop_ns - self.timer_start_ns

    def validate_spans(self, spans: list[dict[str, Any]], *, semantic_phases: set[str] = frozenset({"PREPARE", "NATIVE"})) -> None:
        if self.timer_start_ns is None or self.timer_stop_ns is None:
            raise TimerContractError("timer incomplete")
        for span in spans:
            if span.get("phase") in semantic_phases and not (
                self.timer_start_ns <= int(span["start_ns"]) <= int(span["end_ns"]) <= self.timer_stop_ns
            ):
                raise TimerContractError("semantic span outside build timer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timer_start_ns": self.timer_start_ns,
            "timer_stop_ns": self.timer_stop_ns,
            "final_publication_ns": self.final_publication_ns,
            "build_makespan_ns": self.build_makespan_ns if self.timer_stop_ns is not None else None,
        }

