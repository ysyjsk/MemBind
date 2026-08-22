"""Shared logical-provider admission derived from the frozen runtime scalar."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, Callable


class CapacityAuthorityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapacityAuthority:
    value: int
    source: str = "runtime.config.max_coroutines"
    runtime_value: int | None = None
    graphiti_value: int | None = None

    @classmethod
    def from_runtime(
        cls,
        runtime_max_coroutines: Any = None,
        graphiti_max_coroutines: Any = None,
        *,
        runtime_config: Any = None,
        graphiti: Any = None,
        claimed: int | None = None,
    ) -> "CapacityAuthority":
        if runtime_config is not None:
            runtime_max_coroutines = getattr(runtime_config, "max_coroutines", runtime_config.get("max_coroutines") if isinstance(runtime_config, dict) else None)
        if graphiti is not None:
            graphiti_max_coroutines = getattr(graphiti, "max_coroutines", None)
        if isinstance(runtime_max_coroutines, bool) or not isinstance(runtime_max_coroutines, int) or runtime_max_coroutines <= 0:
            raise CapacityAuthorityError("runtime authority missing or invalid")
        if isinstance(graphiti_max_coroutines, bool) or not isinstance(graphiti_max_coroutines, int) or graphiti_max_coroutines <= 0:
            raise CapacityAuthorityError("graphiti max_coroutines missing or invalid")
        if runtime_max_coroutines != graphiti_max_coroutines:
            raise CapacityAuthorityError("runtime/Graphiti equality mismatch")
        if claimed is not None and claimed != runtime_max_coroutines:
            raise CapacityAuthorityError("claimed capacity is not runtime authority")
        return cls(runtime_max_coroutines, runtime_value=runtime_max_coroutines, graphiti_value=graphiti_max_coroutines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "membind.v5.capacity-authority.v1",
            "source": self.source,
            "value": self.value,
            "runtime_value": self.runtime_value,
            "graphiti_value": self.graphiti_value,
            "submission_only": True,
            "non_claims": ["gpu_physical_capacity", "vllm_active_sequence_ceiling", "graphiti_global_semaphore"],
        }

    @classmethod
    def from_protocol_runtime(cls, runtime: Any) -> "CapacityAuthority":
        graphiti = getattr(runtime, "graphiti", runtime)
        config = getattr(runtime, "config", runtime)
        return cls.from_runtime(runtime_config=config, graphiti=graphiti)


class AdmissionClass(StrEnum):
    NATIVE_FRONTIER = "NATIVE_FRONTIER"
    FRONTIER_PREPARE = "FRONTIER_PREPARE"
    FUTURE_PREPARE = "FUTURE_PREPARE"


_PRIORITY = {
    AdmissionClass.NATIVE_FRONTIER: 0,
    AdmissionClass.FRONTIER_PREPARE: 1,
    AdmissionClass.FUTURE_PREPARE: 2,
}


class AdmissionArbiter:
    def __init__(
        self,
        authority: CapacityAuthority,
        *,
        name: str = "provider",
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.authority = authority
        if not isinstance(name, str) or not name:
            raise ValueError("admission arbiter name must be non-empty")
        self.name = name
        self.event_sink = event_sink
        self._condition = asyncio.Condition()
        self._outstanding = 0
        self._future_outstanding = 0
        self._waiters: list[tuple[int, int, int, AdmissionClass]] = []
        self._counter = 0
        self._active_classes: dict[AdmissionClass, int] = {item: 0 for item in AdmissionClass}
        self._events: list[dict[str, Any]] = []

    @property
    def outstanding(self) -> int:
        return self._outstanding

    def _emit(self, event: str, **fields: Any) -> None:
        row = {
            "event": event,
            "arbiter": self.name,
            "monotonic_ns": __import__("time").monotonic_ns(),
            **fields,
        }
        self._events.append(row)
        if self.event_sink is not None:
            self.event_sink(dict(row))

    async def acquire(
        self,
        admission_class: AdmissionClass,
        *,
        source_sequence: int = 0,
        class_resolver: Callable[[], AdmissionClass] | None = None,
    ) -> AdmissionClass:
        if not isinstance(admission_class, AdmissionClass):
            admission_class = AdmissionClass(admission_class)
        async with self._condition:
            ticket = self._counter
            self._counter += 1
            source_sequence = int(source_sequence)
            self._waiters.append((_PRIORITY[admission_class], source_sequence, ticket, admission_class))
            self._emit(
                "ADMISSION_ENQUEUE",
                ticket=ticket,
                source_sequence=source_sequence,
                admission_class=admission_class.value,
                outstanding=self._outstanding,
                future_outstanding=self._future_outstanding,
                reserved_future_credit=max(0, self.authority.value - 1 - self._future_outstanding),
            )
            try:
                while True:
                    if class_resolver is not None:
                        resolved = class_resolver()
                        if not isinstance(resolved, AdmissionClass):
                            resolved = AdmissionClass(resolved)
                        if resolved != admission_class:
                            admission_class = resolved
                            self._waiters = [
                                (priority, source, item_ticket, resolved if item_ticket == ticket else item_class)
                                for priority, source, item_ticket, item_class in self._waiters
                            ]
                            self._waiters = [
                                (_PRIORITY[item_class], source, item_ticket, item_class)
                                for _priority, source, item_ticket, item_class in self._waiters
                            ]
                            self._emit(
                                "ADMISSION_RECLASSIFY",
                                ticket=ticket,
                                source_sequence=source_sequence,
                                admission_class=admission_class.value,
                            )
                    self._waiters.sort(key=lambda item: (item[0], item[1], item[2]))
                    is_head = self._waiters and self._waiters[0][2] == ticket
                    # Always reserve one permit for the next frontier-critical
                    # operation.  In particular, C=1 has zero future credit;
                    # the queued source d+1 is promoted when d becomes durable.
                    future_allowed = (
                        admission_class != AdmissionClass.FUTURE_PREPARE
                        or self._future_outstanding < self.authority.value - 1
                    )
                    if is_head and self._outstanding < self.authority.value and future_allowed:
                        self._waiters.pop(next(i for i, item in enumerate(self._waiters) if item[2] == ticket))
                        self._outstanding += 1
                        if admission_class == AdmissionClass.FUTURE_PREPARE:
                            self._future_outstanding += 1
                        self._active_classes[admission_class] += 1
                        self._emit(
                            "ADMISSION_ADMIT",
                            ticket=ticket,
                            source_sequence=source_sequence,
                            admission_class=admission_class.value,
                            outstanding=self._outstanding,
                            future_outstanding=self._future_outstanding,
                            reserved_future_credit=max(0, self.authority.value - 1 - self._future_outstanding),
                        )
                        return admission_class
                    await self._condition.wait()
            except BaseException:
                self._waiters = [item for item in self._waiters if item[2] != ticket]
                self._emit(
                    "ADMISSION_CANCEL",
                    ticket=ticket,
                    source_sequence=source_sequence,
                    admission_class=admission_class.value,
                )
                self._condition.notify_all()
                raise

    async def release(self, admission_class: AdmissionClass) -> None:
        if not isinstance(admission_class, AdmissionClass):
            admission_class = AdmissionClass(admission_class)
        async with self._condition:
            if self._outstanding <= 0:
                raise RuntimeError("admission release without permit")
            if self._active_classes.get(admission_class, 0) <= 0:
                raise RuntimeError(f"admission release class mismatch: {admission_class.value}")
            self._outstanding -= 1
            if admission_class == AdmissionClass.FUTURE_PREPARE:
                self._future_outstanding -= 1
            self._active_classes[admission_class] -= 1
            self._emit(
                "ADMISSION_RELEASE",
                admission_class=admission_class.value,
                outstanding=self._outstanding,
                future_outstanding=self._future_outstanding,
                reserved_future_credit=max(0, self.authority.value - 1 - self._future_outstanding),
            )
            self._condition.notify_all()

    async def frontier_advanced(self, source_sequence: int) -> None:
        """Wake queued provider calls after ordered durable publication.

        A provider-free native replay may not release a provider permit.  The
        frontier advance is therefore an independent wake-up so a queued
        source ``d+1`` can be reclassified from FUTURE_PREPARE and use the
        reserved critical credit.
        """

        async with self._condition:
            self._emit(
                "FRONTIER_ADVANCE",
                source_sequence=int(source_sequence),
                outstanding=self._outstanding,
                future_outstanding=self._future_outstanding,
                reserved_future_credit=max(0, self.authority.value - 1 - self._future_outstanding),
            )
            self._condition.notify_all()

    def evidence(self) -> dict[str, Any]:
        return {
            "arbiter": self.name,
            "capacity": self.authority.to_dict(),
            "outstanding": self._outstanding,
            "future_outstanding": self._future_outstanding,
            "reserved_future_credit": max(0, self.authority.value - 1 - self._future_outstanding),
            "waiters": [
                {"ticket": ticket, "source_sequence": source, "admission_class": admission_class.value}
                for _priority, source, ticket, admission_class in self._waiters
            ],
            "events": list(self._events),
            "priority": ["NATIVE_FRONTIER", "FRONTIER_PREPARE", "FUTURE_PREPARE"],
        }


def capacity_authority_from_runtime(runtime: Any) -> CapacityAuthority:
    return CapacityAuthority.from_protocol_runtime(runtime)
