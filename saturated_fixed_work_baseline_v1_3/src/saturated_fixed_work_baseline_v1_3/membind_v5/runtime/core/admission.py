"""Shared logical-provider admission derived from the frozen runtime scalar."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any


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
    def __init__(self, authority: CapacityAuthority) -> None:
        self.authority = authority
        self._condition = asyncio.Condition()
        self._outstanding = 0
        self._future_outstanding = 0
        self._waiters: list[tuple[int, int, AdmissionClass]] = []
        self._counter = 0

    @property
    def outstanding(self) -> int:
        return self._outstanding

    async def acquire(self, admission_class: AdmissionClass, *, source_sequence: int = 0) -> None:
        if not isinstance(admission_class, AdmissionClass):
            admission_class = AdmissionClass(admission_class)
        async with self._condition:
            ticket = self._counter
            self._counter += 1
            self._waiters.append((_PRIORITY[admission_class], ticket, admission_class))
            try:
                while True:
                    self._waiters.sort(key=lambda item: (item[0], item[1]))
                    is_head = self._waiters and self._waiters[0][1] == ticket
                    future_allowed = admission_class != AdmissionClass.FUTURE_PREPARE or self.authority.value == 1 or self._future_outstanding < self.authority.value - 1
                    if is_head and self._outstanding < self.authority.value and future_allowed:
                        self._waiters.pop(next(i for i, item in enumerate(self._waiters) if item[1] == ticket))
                        self._outstanding += 1
                        if admission_class == AdmissionClass.FUTURE_PREPARE:
                            self._future_outstanding += 1
                        return
                    await self._condition.wait()
            except BaseException:
                self._waiters = [item for item in self._waiters if item[1] != ticket]
                self._condition.notify_all()
                raise

    async def release(self, admission_class: AdmissionClass) -> None:
        async with self._condition:
            if self._outstanding <= 0:
                raise RuntimeError("admission release without permit")
            self._outstanding -= 1
            if admission_class == AdmissionClass.FUTURE_PREPARE:
                self._future_outstanding -= 1
            self._condition.notify_all()

    def evidence(self) -> dict[str, Any]:
        return {
            "capacity": self.authority.to_dict(),
            "outstanding": self._outstanding,
            "future_outstanding": self._future_outstanding,
            "priority": ["NATIVE_FRONTIER", "FRONTIER_PREPARE", "FUTURE_PREPARE"],
        }


def capacity_authority_from_runtime(runtime: Any) -> CapacityAuthority:
    return CapacityAuthority.from_protocol_runtime(runtime)
