"""Fail-closed common lifecycle for prospective SFWB v1.3 blocks.

The class is a small event/state contract.  It does not start services or
perform validation; callers report those events and receive one monotonic
construction interval that excludes preparation and post-build work.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field


class LifecycleError(ValueError):
    """A block lifecycle event arrived out of order or with invalid timing."""


_ORDER = (
    "FRESH_NAMESPACE",
    "BACKEND_PREPARED",
    "SERVICE_READY",
    "WARMUP_COMPLETE",
    "BACKEND_IDLE",
    "FORMAL_START",
    "CONSTRUCTION_COMPLETE",
    "DURABLE_COMPLETE",
    "VALIDATION_COMPLETE",
)


@dataclass(slots=True)
class BlockLifecycle:
    """Stateful common timing boundary shared by all execution policies."""

    monotonic_ns: Callable[[], int] = time.monotonic_ns
    state: str = "CREATED"
    events: list[tuple[str, int]] = field(default_factory=list)
    timer_start_ns: int | None = None
    timer_stop_ns: int | None = None

    def _advance(self, event: str) -> int:
        expected_index = len(self.events)
        if expected_index >= len(_ORDER) or _ORDER[expected_index] != event:
            expected = _ORDER[expected_index] if expected_index < len(_ORDER) else "TERMINAL"
            raise LifecycleError(f"LIFECYCLE_ORDER_INVALID:{expected}:{event}")
        stamp = self.monotonic_ns()
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            raise LifecycleError("LIFECYCLE_CLOCK_INVALID")
        if self.events and stamp < self.events[-1][1]:
            raise LifecycleError("LIFECYCLE_CLOCK_NOT_MONOTONIC")
        self.events.append((event, stamp))
        self.state = event
        if event == "FORMAL_START":
            self.timer_start_ns = stamp
        if event == "DURABLE_COMPLETE":
            self.timer_stop_ns = stamp
        return stamp

    def fresh_namespace(self) -> int:
        return self._advance("FRESH_NAMESPACE")

    def backend_prepared(self) -> int:
        return self._advance("BACKEND_PREPARED")

    def service_ready(self) -> int:
        return self._advance("SERVICE_READY")

    def warmup_complete(self) -> int:
        return self._advance("WARMUP_COMPLETE")

    def backend_idle(self) -> int:
        return self._advance("BACKEND_IDLE")

    def formal_start(self) -> int:
        return self._advance("FORMAL_START")

    def construction_complete(self) -> int:
        return self._advance("CONSTRUCTION_COMPLETE")

    def durable_complete(self) -> int:
        return self._advance("DURABLE_COMPLETE")

    def validation_complete(self) -> int:
        if self.state != "DURABLE_COMPLETE":
            raise LifecycleError("DURABLE_COMPLETION_REQUIRED")
        return self._advance("VALIDATION_COMPLETE")

    @property
    def build_makespan_ns(self) -> int:
        if self.timer_start_ns is None or self.timer_stop_ns is None:
            raise LifecycleError("BUILD_TIMER_NOT_COMPLETE")
        if self.timer_stop_ns < self.timer_start_ns:
            raise LifecycleError("BUILD_TIMER_NEGATIVE")
        return self.timer_stop_ns - self.timer_start_ns

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": "sfwb.v1.3.block-lifecycle.v1",
            "state": self.state,
            "events": [{"event": name, "monotonic_ns": stamp} for name, stamp in self.events],
            "timer_start_ns": self.timer_start_ns,
            "timer_stop_ns": self.timer_stop_ns,
            "build_makespan_ns": self.build_makespan_ns if self.timer_stop_ns is not None else None,
        }


__all__ = ["BlockLifecycle", "LifecycleError"]
