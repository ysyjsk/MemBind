"""A compact dependency-aware MemBind scheduler.

The scheduler owns timing and ordering only.  It may prepare future work, but
the caller owns both the Native preparation function and the authoritative
publication function.  A failed or stale preparation always falls back to
Native for that item; it is never retried with a different algorithm.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from .contracts import PreparedWork, PreparedWorkStore, RequestIdentity, ValidationResult, validate_prepared_work


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    sequence: int
    path: str
    validation: ValidationResult


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    records: tuple[ExecutionRecord, ...]

    @property
    def reused(self) -> int:
        return sum(row.path == "REUSE" for row in self.records)

    @property
    def fallback(self) -> int:
        return sum(row.path == "FALLBACK" for row in self.records)


class MemBindScheduler:
    def __init__(self, *, lookahead: int = 2, store: PreparedWorkStore | None = None) -> None:
        if isinstance(lookahead, bool) or not isinstance(lookahead, int) or lookahead < 0:
            raise ValueError("lookahead must be a non-negative integer")
        self.lookahead = lookahead
        self.store = store or PreparedWorkStore()

    async def run(
        self,
        items: Sequence[Any],
        *,
        identity_for: Callable[[Any], RequestIdentity],
        prepare: Callable[[Any], Awaitable[PreparedWork] | PreparedWork],
        reuse_publish: Callable[[Any, PreparedWork], Awaitable[Any] | Any],
        native_publish: Callable[[Any], Awaitable[Any] | Any],
    ) -> SchedulerResult:
        values = tuple(items)
        tasks: dict[int, asyncio.Task[Any]] = {}
        records: list[ExecutionRecord] = []

        async def prepare_one(sequence: int) -> PreparedWork:
            work = await _await(prepare(values[sequence]))
            if not isinstance(work, PreparedWork):
                raise TypeError("prepare must return PreparedWork")
            return work

        def submit(sequence: int) -> None:
            if sequence < 0 or sequence >= len(values) or sequence in tasks:
                return
            tasks[sequence] = asyncio.create_task(prepare_one(sequence))

        try:
            for sequence in range(len(values)):
                submit(sequence)
                for future in range(sequence + 1, min(len(values), sequence + self.lookahead + 1)):
                    submit(future)
                validation: ValidationResult
                try:
                    work = await tasks[sequence]
                except BaseException as exc:
                    validation = ValidationResult(False, f"PREPARE_FAILURE:{type(exc).__name__}")
                    work = None
                expected = identity_for(values[sequence])
                if work is not None:
                    validation = validate_prepared_work(work, expected)
                if validation.valid:
                    self.store.put(work)
                    stored = self.store.pop(expected.logical_id)
                    assert stored is not None
                    await _await(reuse_publish(values[sequence], stored))
                    path = "REUSE"
                else:
                    await _await(native_publish(values[sequence]))
                    path = "FALLBACK"
                records.append(ExecutionRecord(sequence, path, validation))
                tasks.pop(sequence, None)
        except BaseException:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            raise
        return SchedulerResult(tuple(records))

