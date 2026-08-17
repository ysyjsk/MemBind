"""Request-level global LLM admission with observed, not inferred, bounds."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class MemBindV1AdmissionError(ValueError):
    """A request-level global admission contract was violated."""


def _fail(code: str) -> MemBindV1AdmissionError:
    return MemBindV1AdmissionError(code)


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code)
    return value


@dataclass(frozen=True, slots=True)
class RuntimeBounds:
    """Explicit C/W/K controls; worker count alone is not resource evidence."""

    compile_concurrency: int
    prepared_lookahead: int
    llm_request_limit: int

    def __post_init__(self) -> None:
        _positive_int(self.compile_concurrency, "compile_concurrency_invalid")
        _positive_int(self.prepared_lookahead, "prepared_lookahead_invalid")
        _positive_int(self.llm_request_limit, "llm_request_limit_invalid")

    @classmethod
    def conservative_defaults(cls) -> "RuntimeBounds":
        """Return the first correctness-first C=1/W=1/K=2 configuration."""

        return cls(compile_concurrency=1, prepared_lookahead=1, llm_request_limit=2)


class RequestAdmission:
    """An async semaphore that records the true maximum in-flight requests."""

    def __init__(self, *, limit: int) -> None:
        self._limit = _positive_int(limit, "llm_request_limit_invalid")
        self._semaphore = asyncio.Semaphore(self._limit)
        self._lock = asyncio.Lock()
        self._reserved_request_ids: set[str] = set()
        self._active_request_ids: set[str] = set()
        self._observed_max_inflight = 0
        self._request_start_count = 0
        self._completed_request_count = 0

    @property
    def limit(self) -> int:
        return self._limit

    @asynccontextmanager
    async def request(self, request_id: str) -> AsyncIterator[None]:
        """Acquire exactly one global permit for one logical transport request."""

        if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
            raise _fail("request_id_invalid")
        async with self._lock:
            if request_id in self._reserved_request_ids:
                raise _fail("request_already_active")
            self._reserved_request_ids.add(request_id)
        acquired = False
        try:
            await self._semaphore.acquire()
            acquired = True
            async with self._lock:
                self._active_request_ids.add(request_id)
                self._request_start_count += 1
                self._observed_max_inflight = max(
                    self._observed_max_inflight, len(self._active_request_ids)
                )
            yield
        finally:
            async with self._lock:
                if request_id in self._active_request_ids:
                    self._active_request_ids.remove(request_id)
                    self._completed_request_count += 1
                self._reserved_request_ids.discard(request_id)
            if acquired:
                self._semaphore.release()

    def observation(self) -> dict[str, int]:
        """Return public counters suitable for append-only runtime evidence."""

        return {
            "active_request_count": len(self._active_request_ids),
            "completed_request_count": self._completed_request_count,
            "configured_request_limit": self._limit,
            "observed_max_inflight": self._observed_max_inflight,
            "request_start_count": self._request_start_count,
        }


class AdmittedLLMClient:
    """Apply one shared admission object to actual Graphiti LLM requests.

    ``Graphiti`` can make several nested calls during a single bind.  Wrapping
    its ``generate_response`` boundary gives all methods the same K limit at
    request granularity rather than treating a scheduler worker as a proxy for
    model-server occupancy.  Observer events are intentionally content-safe.
    """

    def __init__(
        self,
        *,
        inner: object,
        admission: RequestAdmission,
        request_id_prefix: str,
        observer: Callable[[dict[str, object]], object] | None = None,
    ) -> None:
        if inner is None or not callable(getattr(inner, "generate_response", None)):
            raise _fail("inner_llm_client_invalid")
        if not isinstance(admission, RequestAdmission):
            raise _fail("request_admission_invalid")
        if not isinstance(request_id_prefix, str) or _REQUEST_ID.fullmatch(
            f"{request_id_prefix}:0"
        ) is None:
            raise _fail("request_id_prefix_invalid")
        if observer is not None and not callable(observer):
            raise _fail("request_observer_invalid")
        self._inner = inner
        self._admission = admission
        self._request_id_prefix = request_id_prefix
        self._observer = observer
        self._counter = 0
        self._counter_lock = asyncio.Lock()

    def __getattr__(self, name: str) -> Any:
        """Preserve the non-request client surface expected by Graphiti."""

        return getattr(self._inner, name)

    async def _next_request_id(self) -> str:
        async with self._counter_lock:
            result = f"{self._request_id_prefix}:{self._counter:08d}"
            self._counter += 1
        return result

    def _emit(self, value: dict[str, object]) -> None:
        if self._observer is None:
            return
        try:
            self._observer(dict(value))
        except Exception:
            raise _fail("request_observer_failed") from None

    async def generate_response(self, *args: object, **kwargs: object) -> object:
        """Acquire K for exactly one provider request without retaining prompt text."""

        request_id = await self._next_request_id()
        prompt_name = kwargs.get("prompt_name")
        if prompt_name is not None and not isinstance(prompt_name, str):
            raise _fail("prompt_name_invalid")
        async with self._admission.request(request_id):
            self._emit(
                {
                    "event_type": "llm_request_start",
                    "request_id": request_id,
                    "prompt_name": prompt_name or "",
                }
            )
            error_class: str | None = None
            try:
                result = self._inner.generate_response(*args, **kwargs)
                if not hasattr(result, "__await__"):
                    raise TypeError("inner generate_response must be async")
                return await result
            except Exception as error:
                error_class = f"{type(error).__module__}.{type(error).__qualname__}"
                raise
            finally:
                self._emit(
                    {
                        "event_type": "llm_request_end",
                        "request_id": request_id,
                        "prompt_name": prompt_name or "",
                        "status": "error" if error_class is not None else "ok",
                        "error_class": error_class,
                    }
                )


__all__ = [
    "AdmittedLLMClient",
    "MemBindV1AdmissionError",
    "RequestAdmission",
    "RuntimeBounds",
]
